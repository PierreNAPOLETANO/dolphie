import ipaddress
import os
import re
import socket
import sys
from datetime import datetime
from importlib import metadata

import pymysql
import requests
from dolphie.Database import Database
from dolphie.Functions import format_bytes, format_number, format_sys_table_memory
from dolphie.ManualException import ManualException
from dolphie.Queries import Queries
from dolphie.Widgets.command_screen import CommandScreen
from dolphie.Widgets.popup import CommandPopup
from dolphie.Widgets.topbar import TopBar
from packaging.version import parse as parse_version
from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.style import Style
from rich.syntax import Syntax
from rich.table import Table
from sqlparse import format as sqlformat
from textual.app import App
from textual.widgets import DataTable

try:
    __package_name__ = metadata.metadata(__package__ or __name__)["Name"]
    __version__ = metadata.version(__package__ or __name__)
except Exception:
    __package_name__ = "N/A"
    __version__ = "N/A"


class Dolphie:
    def __init__(self, app: App):
        self.app = app
        self.console = Console()
        self.processlist_datatable = DataTable(show_cursor=False)

        # Config options
        self.user: str = None
        self.password: str = None
        self.host: str = None
        self.port: int = 3306
        self.socket: str = None
        self.ssl: dict = {}
        self.config_file: str = None
        self.host_cache_file: str = None
        self.debug: bool = False
        self.refresh_interval: int = 1
        self.use_processlist: bool = False
        self.show_idle_queries: bool = False
        self.show_trxs_only: bool = False
        self.show_additional_query_columns: bool = False
        self.show_last_executed_query: bool = False
        self.sort_by_time_descending: bool = True
        self.heartbeat_table: str = None
        self.user_filter: str = None
        self.db_filter: str = None
        self.host_filter: str = None
        self.time_filter: str = 0
        self.query_filter: str = None

        # Loop variables
        self.dolphie_start_time: datetime = datetime.now()
        self.previous_main_loop_time: datetime = datetime.now()
        self.loop_duration_seconds: int = 0
        self.processlist_threads: dict = {}
        self.pause_refresh: bool = False
        self.first_loop: bool = True
        self.saved_status: bool = None
        self.previous_binlog_position: int = 0
        self.previous_replica_sbm: int = 0
        self.host_cache: dict = {}
        self.host_cache_from_file: dict = {}
        self.variables: dict = {}
        self.statuses: dict = {}
        self.primary_status: dict = {}
        self.replica_status: dict = {}
        self.innodb_status: dict = {}
        self.replica_connections: dict = {}

        # Set on database connection
        self.connection_id: int = None
        self.use_performance_schema: bool = False
        self.performance_schema_enabled: bool = False
        self.innodb_locks_sql: bool = False
        self.host_is_rds: bool = False
        self.server_uuid: str = None
        self.mysql_version: str = None
        self.host_distro: str = None

        self.footer_timer = None

        self.app_version = __version__
        self.header = TopBar(app_version=self.app_version)

    def check_for_update(self):
        # Query PyPI API to get the latest version
        try:
            url = f"https://pypi.org/pypi/{__package_name__}/json"
            response = requests.get(url)

            if response.status_code == 200:
                data = response.json()

                # Extract the latest version from the response
                latest_version = data["info"]["version"]

                # Compare the current version with the latest version
                if parse_version(latest_version) > parse_version(__version__):
                    self.console.print(
                        (
                            "[bright_green]New version available!\n\n[grey93]Current version:"
                            f" [#91abec]{__version__}\n[grey93]Latest version:"
                            f" [#91abec]{latest_version}\n\n[grey93]Please update to the latest version at your"
                            " convenience\n[grey66]You can find more details at:"
                            " [underline]https://github.com/charles-001/dolphie[/underline]\n\n[#91abec]Press any key"
                            " to continue"
                        ),
                        highlight=False,
                    )

                    key = self.kb.key_press_blocking()
                    if key:
                        pass
            else:
                self.console.print(
                    f"[indian_red]Failed to retrieve package information from PyPI![/indian_red] URL: {url} - Code:"
                    f" {response.status_code}"
                )
        except Exception:
            return

    def update_footer(self, output, hide=False, temporary=True):
        footer = self.app.query_one("#footer")

        footer.display = True
        footer.update(f"[gray93]{output}")

        if hide:
            footer.display = False
        elif temporary:
            # Remove existing time if it exists
            if self.footer_timer and self.footer_timer._active:
                self.footer_timer.stop()
            self.footer_timer = self.app.set_timer(7, lambda: setattr(footer, "display", False))

    def db_connect(self):
        self.db = Database(self.host, self.user, self.password, self.socket, self.port, self.ssl)

        query = "SELECT CONNECTION_ID() AS connection_id"
        self.connection_id = self.db.fetchone(query, "connection_id")

        query = "SELECT @@performance_schema"
        performance_schema = self.db.fetchone(query, "@@performance_schema")
        if performance_schema == 1:
            self.performance_schema_enabled = True

            if not self.use_processlist:
                self.use_performance_schema = True

        query = "SELECT @@version_comment"
        version_comment = self.db.fetchone(query, "@@version_comment").lower()

        query = "SELECT @@basedir"
        basedir = self.db.fetchone(query, "@@basedir")

        aurora_version = None
        query = "SHOW GLOBAL VARIABLES LIKE 'aurora_version'"
        aurora_version_data = self.db.fetchone(query, "Value")
        if aurora_version_data:
            aurora_version = aurora_version_data["Value"]

        query = "SELECT @@version"
        version = self.db.fetchone(query, "@@version").lower()
        version_split = version.split(".")

        self.mysql_version = "%s.%s.%s" % (
            version_split[0],
            version_split[1],
            version_split[2].split("-")[0],
        )
        major_version = int(version_split[0])

        # Get proper host version and fork
        if "percona xtradb cluster" in version_comment:
            self.host_distro = "Percona XtraDB Cluster"
        elif "percona server" in version_comment:
            self.host_distro = "Percona Server"
        elif "mariadb cluster" in version_comment:
            self.host_distro = "MariaDB Cluster"
        elif "mariadb" in version_comment or "mariadb" in version:
            self.host_distro = "MariaDB"
        elif aurora_version:
            self.host_distro = "Amazon Aurora"
            self.host_is_rds = True
        elif "rdsdb" in basedir:
            self.host_distro = "Amazon RDS"
            self.host_is_rds = True
        else:
            self.host_distro = "MySQL"

        # Determine if InnoDB locks panel is available for a version and which query to use
        self.innodb_locks_sql = None
        server_uuid_query = "SELECT @@server_uuid"
        if "MariaDB" in self.host_distro and major_version >= 10:
            server_uuid_query = "SELECT @@server_id AS @@server_uuid"
            self.innodb_locks_sql = Queries["locks_query-5"]
        elif major_version == 5:
            self.innodb_locks_sql = Queries["locks_query-5"]
        elif major_version == 8 and self.use_performance_schema:
            self.innodb_locks_sql = Queries["locks_query-8"]

        self.server_uuid = self.db.fetchone(server_uuid_query, "@@server_uuid")

    def fetch_data(self, command):
        command_data = {}

        if command == "status" or command == "variables":
            self.db.execute(Queries[command])
            data = self.db.fetchall()

            for row in data:
                variable = row["Variable_name"]
                value = row["Value"]

                try:
                    converted_value = row["Value"]

                    if converted_value.isnumeric():
                        converted_value = int(converted_value)
                except (UnicodeDecodeError, AttributeError):
                    converted_value = value

                command_data[variable] = converted_value

        elif command == "innodb_status":
            data = self.db.fetchone(Queries[command], "Status")
            command_data["status"] = data

        else:
            self.db.execute(Queries[command])
            data = self.db.fetchall()

            for row in data:
                for column, value in row.items():
                    try:
                        converted_value = value

                        if converted_value.isnumeric():
                            converted_value = int(converted_value)
                    except (UnicodeDecodeError, AttributeError):
                        converted_value = value

                    command_data[column] = converted_value

        return command_data

    def command_input_to_variable(self, return_data):
        variable = return_data[0]
        value = return_data[1]
        if value:
            setattr(self, variable, value)

    def capture_key(self, key):
        screen_data = None

        if key == "1":
            if self.use_performance_schema:
                self.use_performance_schema = False
                self.update_footer("Switched to using [b #91abec]Processlist")
            else:
                if self.performance_schema_enabled:
                    self.use_performance_schema = True
                    self.update_footer("Switched to using [b #91abec]Performance Schema")
                else:
                    self.update_footer("[indian_red]You can't switch to Performance Schema because it isn't enabled")

        elif key == "2":
            screen_data = self.innodb_status["status"]

        elif key == "a":
            if self.show_additional_query_columns:
                self.show_additional_query_columns = False
            else:
                self.show_additional_query_columns = True

        elif key == "C":
            self.user_filter = ""
            self.db_filter = ""
            self.host_filter = ""
            self.time_filter = ""
            self.query_filter = ""

            self.update_footer("Cleared all filters")

        elif key == "d":
            tables = {}
            all_tables = []

            db_count = self.db.execute(Queries["databases"])
            databases = self.db.fetchall()

            # Determine how many tables to provide data
            max_num_tables = 1 if db_count <= 20 else 3

            # Calculate how many databases per table
            row_per_count = db_count // max_num_tables

            # Create dictionary of tables
            for table_counter in range(1, max_num_tables + 1):
                tables[table_counter] = Table(box=box.ROUNDED, show_header=False, style="#b0bad7")
                tables[table_counter].add_column("")

            # Loop over databases
            db_counter = 1
            table_counter = 1

            # Sort the databases by name
            for database in databases:
                tables[table_counter].add_row(database["SCHEMA_NAME"], style="grey93")
                db_counter += 1

                if db_counter > row_per_count and table_counter < max_num_tables:
                    table_counter += 1
                    db_counter = 1

            # Collect table data into an array
            all_tables = [table_data for table_data in tables.values() if table_data]

            table_grid = Table.grid()
            table_grid.add_row(*all_tables)

            screen_data = Group(
                Align.center("[b]Databases[/b]"),
                Align.center(table_grid),
                Align.center("Total: [b #91abec]%s[/b #91abec]" % db_count),
            )

        elif key == "D":
            dashboard = self.app.query_one("#dashboard_panel")
            if dashboard.display:
                dashboard.display = False
            else:
                dashboard.display = True

        elif key == "E":
            if not self.mysql_version.startswith("8"):
                self.update_footer("[indian_red]This command requires MySQL 8")
            else:
                event_levels = {
                    "a": "all",
                    "s": "system",
                    "w": "warning",
                    "e": "error",
                }
                available_levels = ", ".join(
                    f"[b #91abec]{key}[/b #91abec]=[grey70]{value}[/grey70]" for key, value in event_levels.items()
                )
                while True:
                    event_levels_input = self.console.input(
                        (
                            "[#91abec]What level(s) of events do you want to display? Use a comma to"
                            f" separate[/#91abec] \\[{available_levels}]: "
                        ),
                    )

                    # Split the input by commas and remove leading/trailing whitespaces
                    selected_levels = [level.strip() for level in event_levels_input.split(",")]

                    # Check if all selected levels are valid
                    if all(level in event_levels.keys() for level in selected_levels):
                        break
                    else:
                        self.update_footer("[indian_red]Invalid level")

                table = Table(
                    show_header=False,
                    caption="Press q to return",
                    box=box.SIMPLE,
                )
                table.add_column("Time", style="dim")
                table.add_column("Level")
                table.add_column("Event")

                query = Queries["error_log"]
                where_clauses = {
                    "s": 'prio = "System"',
                    "w": 'prio = "Warning"',
                    "e": 'prio = "Error"',
                }

                if "a" not in selected_levels:
                    where_conditions = [where_clauses[level] for level in selected_levels if level in where_clauses]
                    where_clause = " OR ".join(where_conditions)
                    query = query.replace("$placeholder", f"AND ({where_clause})")
                else:
                    query = query.replace("$placeholder", "")

                self.db.execute(query)
                data = self.db.fetchall()
                for row in data:
                    level_color = "grey93"
                    if row["level"] == "Error":
                        level_color = "white on red"
                    elif row["level"] == "Warning":
                        level_color = "bright_yellow"

                    level = f"[{level_color}]{row['level']}[/{level_color}]"
                    table.add_row(row["timestamp"].strftime("%Y-%m-%d %H:%M:%S"), level, row["message"])

                self.rich_live.stop()

                os.system("clear")
                with self.console.pager(styles=True):
                    self.console.print(Align.right(f"{self.header_title} [dim]press q to return"), highlight=False)
                    self.console.print(table)

                self.rich_live.start()

        elif key == "e":

            def command_get_input(thread_id):
                if thread_id:
                    if thread_id in self.processlist_threads:
                        row_style = Style(color="grey93")
                        table = Table(box=box.ROUNDED, show_header=False, style="#b0bad7")
                        table.add_column("")
                        table.add_column("")

                        table.add_row("[#c5c7d2]Thread ID", str(thread_id), style=row_style)
                        table.add_row(
                            "[#c5c7d2]User",
                            self.processlist_threads[thread_id]["user"],
                            style=row_style,
                        )
                        table.add_row(
                            "[#c5c7d2]Host",
                            self.processlist_threads[thread_id]["host"],
                            style=row_style,
                        )
                        table.add_row(
                            "[#c5c7d2]Database",
                            self.processlist_threads[thread_id]["db"],
                            style=row_style,
                        )
                        table.add_row(
                            "[#c5c7d2]Command",
                            self.processlist_threads[thread_id]["command"],
                            style=row_style,
                        )
                        table.add_row(
                            "[#c5c7d2]State",
                            self.processlist_threads[thread_id]["state"],
                            style=row_style,
                        )
                        table.add_row(
                            "[#c5c7d2]Time",
                            self.processlist_threads[thread_id]["hhmmss_time"],
                            style=row_style,
                        )
                        table.add_row(
                            "[#c5c7d2]Rows Locked",
                            self.processlist_threads[thread_id]["trx_rows_locked"],
                            style=row_style,
                        )
                        table.add_row(
                            "[#c5c7d2]Rows Modified",
                            self.processlist_threads[thread_id]["trx_rows_modified"],
                            style=row_style,
                        )
                        if (
                            "innodb_thread_concurrency" in self.variables
                            and self.variables["innodb_thread_concurrency"]
                        ):
                            table.add_row(
                                "[#c5c7d2]Tickets",
                                self.processlist_threads[thread_id]["trx_concurrency_tickets"],
                                style=row_style,
                            )
                        table.add_row("", "")
                        table.add_row(
                            "[#c5c7d2]TRX State",
                            self.processlist_threads[thread_id]["trx_state"],
                            style=row_style,
                        )
                        table.add_row(
                            "[#c5c7d2]TRX Operation",
                            self.processlist_threads[thread_id]["trx_operation_state"],
                            style=row_style,
                        )

                        query = sqlformat(self.processlist_threads[thread_id]["query"], reindent_aligned=True)
                        query_db = self.processlist_threads[thread_id]["db"]

                        if query and query_db:
                            explain_failure = None
                            explain_data = None

                            formatted_query = Syntax(
                                query,
                                "sql",
                                line_numbers=False,
                                word_wrap=True,
                                theme="monokai",
                                background_color="#000718",
                            )

                            try:
                                self.db.cursor.execute("USE %s" % query_db)
                                self.db.cursor.execute("EXPLAIN %s" % query)

                                explain_data = self.db.fetchall()
                            except pymysql.Error as e:
                                explain_failure = (
                                    "[b indian_red]EXPLAIN ERROR:[/b indian_red] [indian_red]%s" % e.args[1]
                                )

                            if explain_data:
                                explain_table = Table(box=box.ROUNDED, style="#b0bad7")

                                columns = []
                                for row in explain_data:
                                    values = []
                                    for column, value in row.items():
                                        # Exclude possbile_keys field since it takes up too much space
                                        if column == "possible_keys":
                                            continue

                                        # Don't duplicate columns
                                        if column not in columns:
                                            explain_table.add_column(column)
                                            columns.append(column)

                                        if column == "key" and value is None:
                                            value = "[b white on red]NO INDEX[/b white on red]"

                                        if column == "rows":
                                            value = format_number(value)

                                        values.append(str(value))

                                    explain_table.add_row(*values, style="grey93")

                                screen_data = Group(
                                    Align.center(table),
                                    "",
                                    Align.center(formatted_query),
                                    "",
                                    Align.center(explain_table),
                                )
                            else:
                                screen_data = Group(
                                    Align.center(table),
                                    "",
                                    Align.center(formatted_query),
                                    "",
                                    Align.center(explain_failure),
                                )
                        else:
                            screen_data = Align.center(table)

                        self.app.push_screen(CommandScreen(self.app_version, self.host, screen_data))
                    else:
                        self.update_footer("Thread ID [b #91abec]%s[/b #91abec] does not exist" % thread_id)

            self.app.push_screen(
                CommandPopup(message="Specify a Thread ID to explain its query"),
                command_get_input,
            )

        elif key == "H":

            def command_get_input(host):
                found = False
                # Since our filtering is done by the processlist query, the value needs to be what's in host cache
                for ip, addr in self.host_cache.items():
                    if host == addr:
                        self.host_filter = ip

                        found = True

                if host and not found:
                    self.update_footer("Host [b #91abec]%s[/b #91abec] was not found processlist" % host)

            self.app.push_screen(CommandPopup(message="Specify a host to filter by"), command_get_input)

        elif key == "I":
            innodb_io = self.app.query_one("#innodb_io_panel")
            if innodb_io.display:
                innodb_io.display = False
            else:
                innodb_io.update(Align.center("[b #91abec]Loading[/b #91abec]…"))
                innodb_io.display = True

        elif key == "i":
            if self.show_idle_queries:
                self.show_idle_queries = False
                self.sort_by_time_descending = True
            else:
                self.show_idle_queries = True
                self.sort_by_time_descending = False

        elif key == "k":

            def command_get_input(thread_id):
                if thread_id:
                    if thread_id in self.processlist_threads:
                        try:
                            if self.host_is_rds:
                                self.db.cursor.execute("CALL mysql.rds_kill(%s)" % thread_id)
                            else:
                                self.db.cursor.execute("KILL %s" % thread_id)

                            self.update_footer("Killed thread [b #91abec]%s[/b #91abec]" % thread_id)
                        except Exception as e:
                            self.update_footer("[b][indian_red]Error[/b]: %s" % e.args[1])
                    else:
                        self.update_footer("Thread ID [b #91abec]%s[/b #91abec] does not exist" % thread_id)

            self.app.push_screen(CommandPopup(message="Specify a Thread ID to kill"), command_get_input)

        elif key == "K":
            include_sleep = self.console.input("[#91abec]Include queries in sleep state? (y/n)[/#91abec]: ")

            if include_sleep != "y" and include_sleep != "n":
                self.update_footer("[indian_red]Invalid option")
            else:
                kill_type = self.console.input("[#91abec]Kill by username/hostname/time range (u/h/t)[/#91abec]: ")
                threads_killed = 0

                commands_without_sleep = ["Query", "Execute"]
                commands_with_sleep = ["Query", "Execute", "Sleep"]

                if kill_type == "u":
                    user = self.console.input("[#91abec]User[/#91abec]: ")

                    for thread_id, thread in self.processlist_threads.items():
                        try:
                            if thread["user"] == user:
                                if include_sleep == "y":
                                    if thread["command"] in commands_with_sleep:
                                        if self.host_is_rds:
                                            self.db.cursor.execute("CALL mysql.rds_kill(%s)" % thread_id)
                                        else:
                                            self.db.cursor.execute("KILL %s" % thread_id)

                                        threads_killed += 1
                                else:
                                    if thread["command"] in commands_without_sleep:
                                        if self.host_is_rds:
                                            self.db.cursor.execute("CALL mysql.rds_kill(%s)" % thread_id)
                                        else:
                                            self.db.cursor.execute("KILL %s" % thread_id)

                                        threads_killed += 1
                        except pymysql.OperationalError:
                            continue

                elif kill_type == "h":
                    host = self.console.input("[#91abec]Host/IP[/#91abec]: ")

                    for thread_id, thread in self.processlist_threads.items():
                        try:
                            if thread["host"] == host:
                                if include_sleep == "y":
                                    if thread["command"] in commands_with_sleep:
                                        if self.host_is_rds:
                                            self.db.cursor.execute("CALL mysql.rds_kill(%s)" % thread_id)
                                        else:
                                            self.db.cursor.execute("KILL %s" % thread_id)

                                        threads_killed += 1
                                else:
                                    if thread["command"] in commands_without_sleep:
                                        if self.host_is_rds:
                                            self.db.cursor.execute("CALL mysql.rds_kill(%s)" % thread_id)
                                        else:
                                            self.db.cursor.execute("KILL %s" % thread_id)

                                        threads_killed += 1
                        except pymysql.OperationalError:
                            continue

                elif kill_type == "t":
                    time = self.console.input("[#91abec]Time range (ex. 10-20)[/#91abec]: ")

                    if re.search(r"(\d+-\d+)", time):
                        time_range = time.split("-")
                        lower_limit = int(time_range[0])
                        upper_limit = int(time_range[1])

                        if lower_limit > upper_limit:
                            self.update_footer("[indian_red]Invalid time range! Lower limit can't be higher than upper")
                        else:
                            for thread_id, thread in self.processlist_threads.items():
                                try:
                                    if thread["time"] >= lower_limit and thread["time"] <= upper_limit:
                                        if include_sleep == "y":
                                            if thread["command"] in commands_with_sleep:
                                                if self.host_is_rds:
                                                    self.db.cursor.execute("CALL mysql.rds_kill(%s)" % thread_id)
                                                else:
                                                    self.db.cursor.execute("KILL %s" % thread_id)

                                                threads_killed += 1
                                        else:
                                            if thread["command"] in commands_without_sleep:
                                                if self.host_is_rds:
                                                    self.db.cursor.execute("CALL mysql.rds_kill(%s)" % thread_id)
                                                else:
                                                    self.db.cursor.execute("KILL %s" % thread_id)

                                                threads_killed += 1
                                except pymysql.OperationalError:
                                    continue
                    else:
                        self.update_footer("[indian_red]Invalid time range")
                else:
                    self.update_footer("[indian_red]Invalid option")

                if threads_killed:
                    self.update_footer("[grey93]Killed [#91abec]%s [grey93]threads" % threads_killed)
                else:
                    self.update_footer("[#91abec]No threads were killed")

        elif key == "L":
            if self.innodb_locks_sql:
                innodb_locks = self.app.query_one("#innodb_locks_panel")
                if innodb_locks.display:
                    innodb_locks.display = False
                else:
                    innodb_locks.update(Align.center("[b #91abec]Loading[/b #91abec]…"))
                    innodb_locks.display = True
            else:
                self.update_footer("[indian_red]InnoDB Locks panel isn't supported for this host's version")

        elif key == "l":
            deadlock = ""
            output = re.search(
                r"------------------------\nLATEST\sDETECTED\sDEADLOCK\n------------------------"
                "\n(.*?)------------\nTRANSACTIONS",
                self.innodb_status["status"],
                flags=re.S,
            )
            if output:
                deadlock = output.group(1)

                deadlock = deadlock.replace("***", "[yellow]*****[/yellow]")
                screen_data = deadlock
                self.console.print(deadlock, highlight=False)
            else:
                screen_data = Align.center("No deadlock detected")

        elif key == "m":
            table_grid = Table.grid()

            table1 = Table(
                box=box.ROUNDED,
                style="#b0bad7",
            )

            header_style = Style(bold=True)
            table1.add_column("User", header_style=header_style)
            table1.add_column("Current", header_style=header_style)
            table1.add_column("Total", header_style=header_style)

            self.db.execute(Queries["memory_by_user"])
            data = self.db.fetchall()
            for row in data:
                table1.add_row(
                    row["user"],
                    format_sys_table_memory(row["current_allocated"]),
                    format_sys_table_memory(row["total_allocated"]),
                )

            table2 = Table(
                box=box.ROUNDED,
                style="#b0bad7",
            )
            table2.add_column("Code Area", header_style=header_style)
            table2.add_column("Current", header_style=header_style)

            self.db.execute(Queries["memory_by_code_area"])
            data = self.db.fetchall()
            for row in data:
                table2.add_row(row["code_area"], format_sys_table_memory(row["current_allocated"]))

            table3 = Table(
                box=box.ROUNDED,
                style="#b0bad7",
            )
            table3.add_column("Host", header_style=header_style)
            table3.add_column("Current", header_style=header_style)
            table3.add_column("Total", header_style=header_style)

            self.db.execute(Queries["memory_by_host"])
            data = self.db.fetchall()
            for row in data:
                table3.add_row(
                    self.get_hostname(row["host"]),
                    format_sys_table_memory(row["current_allocated"]),
                    format_sys_table_memory(row["total_allocated"]),
                )

            table_grid.add_row("", Align.center("[b]Memory Allocation[/b]"), "")
            table_grid.add_row(table1, table3, table2)

            screen_data = Align.center(table_grid)

        elif key == "P":
            processlist = self.app.query_one("#processlist_panel")
            if processlist.display:
                processlist.display = False
            else:
                processlist.display = True

        elif key == "p":
            if not self.pause_refresh:
                self.pause_refresh = True
                self.update_footer(
                    f"Refresh is paused! Press [b #91abec]{key}[/b #91abec] again to resume",
                    temporary=False,
                )
            else:
                self.pause_refresh = False
                self.update_footer("", hide=True)

        elif key == "Q":
            self.app.push_screen(
                CommandPopup(message="Specify a query text to filter by", variable="query_filter"),
                self.command_input_to_variable,
            )

        elif key == "q":
            sys.exit()

        elif key == "r":

            def command_get_input(refresh_interval):
                if refresh_interval.isnumeric():
                    self.refresh_interval = int(refresh_interval)
                else:
                    self.update_footer("[indian_red]Input must be an integer")

            self.app.push_screen(
                CommandPopup(message="Specify refresh interval (in seconds)"),
                command_get_input,
            )

        elif key == "R":
            if self.use_performance_schema:
                find_replicas_query = Queries["ps_find_replicas"]
            else:
                find_replicas_query = Queries["pl_find_replicas"]

            self.db.cursor.execute(find_replicas_query)
            data = self.db.fetchall()
            if not data and not self.replica_status:
                self.update_footer(
                    "[b]Cannot use this panel![/b] This host is not a replica and has no replicas connected"
                )
                return

            replica = self.app.query_one("#replica_panel")
            if replica.display:
                replica.display = False

                for connection in self.replica_connections.values():
                    connection["connection"].close()

                self.replica_connections = {}
            else:
                replica.update(Align.center("[b #91abec]Loading[/b #91abec]…"))
                replica.display = True

        elif key == "s":
            if self.sort_by_time_descending:
                self.sort_by_time_descending = False
            else:
                self.sort_by_time_descending = True

        elif key == "S":
            if self.show_last_executed_query:
                self.show_last_executed_query = False
            else:
                self.show_last_executed_query = True

        elif key == "t":
            if self.show_trxs_only:
                self.show_trxs_only = False
            else:
                self.show_trxs_only = True

        elif key == "T":

            def command_get_input(time):
                if time.isnumeric():
                    self.time_filter = int(time)
                else:
                    self.update_footer("[indian_red]Time must be an integer")

            self.app.push_screen(
                CommandPopup(message="Specify minimum time to display for queries"),
                command_get_input,
            )

        elif key == "u":
            user_stat_data = self.create_user_stats_table()
            if user_stat_data:
                screen_data = Align.center(user_stat_data)
            else:
                self.update_footer(
                    "[indian_red]This command requires Userstat variable or Performance Schema to be enabled"
                )

        elif key == "U":
            self.app.push_screen(
                CommandPopup(message="Specify a user to filter by", variable="user_filter"),
                self.command_input_to_variable,
            )

        elif key == "Y":
            self.app.push_screen(
                CommandPopup(message="Specify a database to filter by", variable="db_filter"),
                self.command_input_to_variable,
            )
        elif key == "v":

            def command_get_input(input_variable):
                table_grid = Table.grid()
                table_counter = 1
                variable_counter = 1
                row_counter = 1
                variable_num = 1
                all_tables = []
                tables = {}
                display_variables = {}

                for variable, value in self.variables.items():
                    if input_variable:
                        if input_variable in variable:
                            display_variables[variable] = self.variables[variable]

                max_num_tables = 1 if len(display_variables) <= 50 else 2

                # Create the number of tables we want
                while table_counter <= max_num_tables:
                    tables[table_counter] = Table(box=box.ROUNDED, show_header=False, style="#b0bad7")
                    tables[table_counter].add_column("")
                    tables[table_counter].add_column("")

                    table_counter += 1

                # Calculate how many variables per table
                row_per_count = len(display_variables) // max_num_tables

                # Loop variables
                for variable, value in display_variables.items():
                    tables[variable_num].add_row("[#c5c7d2]%s" % variable, str(value), style="grey93")

                    if variable_counter == row_per_count and row_counter != max_num_tables:
                        row_counter += 1
                        variable_counter = 0
                        variable_num += 1

                    variable_counter += 1

                # Put all the variable data from dict into an array
                all_tables = [table_data for table_data in tables.values() if table_data]

                # Add the data into a single tuple for add_row
                if display_variables:
                    table_grid.add_row(*all_tables)
                    screen_data = Align.center(table_grid)

                    self.app.push_screen(CommandScreen(self.app_version, self.host, screen_data))
                else:
                    if input_variable:
                        self.update_footer("No variable(s) found that match [b #91abec]%s[/b #91abec]" % input_variable)

            self.app.push_screen(
                CommandPopup(message="Specify a variable to wildcard search\n([dim]leave blank for all[/dim])"),
                command_get_input,
            )

        elif key == "z":
            if self.host_cache:
                table = Table(box=box.ROUNDED, style="#b0bad7")
                table.add_column("Host/IP")
                table.add_column("Hostname (if resolved)")

                for ip, addr in self.host_cache.items():
                    if ip:
                        table.add_row(ip, addr)

                screen_data = Group(
                    Align.center("[b]Host Cache[/b]"),
                    Align.center(table),
                    Align.center("Total: [b #91abec]%s" % len(self.host_cache)),
                )
            else:
                screen_data = Align.center("\nThere are currently no hosts resolved")

        elif key == "question_mark":
            row_style = Style(color="grey93")

            filters = {
                "C": "Clear all filters",
                "H": "Filter by host/IP",
                "Q": "Filter by query text",
                "T": "Filter by minimum query time",
                "Y": "Filter by database",
                "U": "Filter by user",
            }
            table_filters = Table(box=box.ROUNDED, style="#b0bad7", title="Filters", title_style="bold")
            table_filters.add_column("Key", justify="center", style="b #91abec")
            table_filters.add_column("Description")
            for key, description in sorted(filters.items()):
                table_filters.add_row("[#91abec]%s" % key, description, style=row_style)

            panels = {
                "D": "Show/hide dashboard",
                "I": "Show/hide InnoDB information",
                "L": "Show/hide InnoDB query locks",
                "P": "Show/hide processlist",
                "R": "Show/hide replication + replicas",
            }
            table_panels = Table(box=box.ROUNDED, style="#b0bad7", title="Panels", title_style="bold")
            table_panels.add_column("Key", justify="center", style="b #91abec")
            table_panels.add_column("Description")
            for key, description in sorted(panels.items()):
                table_panels.add_row("[#91abec]%s" % key, description, style=row_style)

            keys = {
                "1": "Switch between using Processlist/Performance Schema for listing queries",
                "2": "Display output from SHOW ENGINE INNODB STATUS",
                "a": "Show/hide additional processlist columns",
                "d": "Display all databases",
                "e": "Explain query of a thread and display thread information",
                "E": "Display error log from Performance Schema",
                "i": "Show/hide idle queries",
                "k": "Kill a query by thread ID",
                "K": "Kill a query by either user/host/time range",
                "l": "Show latest deadlock detected",
                "m": "Display memory usage - limits to only 30 rows",
                "p": "Pause Dolphie",
                "q": "Quit Dolphie",
                "r": "Set the refresh interval",
                "t": "Show/hide running transactions only",
                "s": "Sort query list by time in descending/ascending order",
                "S": "Show/hide last executed query for sleeping thread (Performance Schema only)",
                "u": "List users (results vary depending on if userstat variable is enabled)",
                "v": "Variable wildcard search via SHOW VARIABLES",
                "z": "Show all entries in the host cache",
            }

            table_keys = Table(box=box.ROUNDED, style="#b0bad7", title="Features", title_style="bold")
            table_keys.add_column("Key", justify="center", style="b #91abec")
            table_keys.add_column("Description")

            for key, description in sorted(keys.items()):
                table_keys.add_row("[#91abec]%s" % key, description, style=row_style)

            datapoints = {
                "Read Only": "If the host is in read-only mode",
                "Use PS": "If Dolphie is using Performance Schema for listing queries",
                "Read Hit": "The percentage of how many reads are from InnoDB buffer pool compared to from disk",
                "Lag": (
                    "Retrieves metric from: Slave -> SHOW SLAVE STATUS, HB -> Heartbeat table, PS -> Performance Schema"
                ),
                "Chkpt Age": (
                    "This depicts how close InnoDB is before it starts to furiously flush dirty data to disk "
                    "(Higher is better)"
                ),
                "AHI Hit": (
                    "The percentage of how many lookups there are from Adapative Hash Index compared to it not"
                    " being used"
                ),
                "Pending AIO": "W means writes, R means reads. The values should normally be 0",
                "Diff": "This is the size difference of the binary log between each refresh interval",
                "Cache Hit": "The percentage of how many binary log lookups are from cache instead of from disk",
                "History List": "History list length (number of un-purged row changes in InnoDB's undo logs)",
                "Unpurged TRX": (
                    "How many transactions are between the newest and the last purged in InnoDB's undo logs"
                ),
                "QPS": "Queries per second",
                "Latency": "How much time it takes to receive data from the host for Dolphie each refresh interval",
                "Threads": "Con = Connected, Run = Running, Cac = Cached from SHOW GLOBAL STATUS",
                "Speed": "How many seconds were taken off of replication lag from the last refresh interval",
                "Query A/Q": (
                    "How many queries are active/queued in InnoDB. Based on innodb_thread_concurrency variable"
                ),
                "Tickets": "Relates to innodb_concurrency_tickets variable",
            }

            table_info = Table(box=box.ROUNDED, style="#b0bad7")
            table_info.add_column("Datapoint", style="#91abec")
            table_info.add_column("Description")
            for datapoint, description in sorted(datapoints.items()):
                table_info.add_row("[#91abec]%s" % datapoint, description, style=row_style)

            table_grid = Table.grid()
            table_grid.add_row(table_panels, table_filters)

            screen_data = Group(
                Align.center(table_keys),
                "",
                Align.center(table_grid),
                "",
                Align.center(table_info),
            )

        if screen_data:
            self.app.push_screen(CommandScreen(self.app_version, self.host, screen_data))

    def create_user_stats_table(self):
        table = Table(header_style="bold white", box=box.ROUNDED, style="#b0bad7")

        columns = {}
        user_stats = {}
        userstat_enabled = 0

        if self.db.execute("SELECT @@userstat", ignore_error=True) == 1:
            userstat_enabled = self.db.cursor.fetchone()["@@userstat"]

        if userstat_enabled:
            self.db.execute(Queries["userstat_user_statisitics"])

            columns.update(
                {
                    "User": {"field": "user", "format_number": False},
                    "Active": {"field": "concurrent_connections", "format_number": True},
                    "Total": {"field": "total_connections", "format_number": True},
                    "Binlog Data": {"field": "binlog_bytes_written", "format_number": False},
                    "Rows Read": {"field": "table_rows_read", "format_number": True},
                    "Rows Sent": {"field": "rows_fetched", "format_number": True},
                    "Rows Updated": {"field": "rows_updated", "format_number": True},
                    "Selects": {"field": "select_commands", "format_number": True},
                    "Updates": {"field": "update_commands", "format_number": True},
                    "Other": {"field": "other_commands", "format_number": True},
                    "Commit": {"field": "commit_transactions", "format_number": True},
                    "Rollback": {"field": "rollback_transactions", "format_number": True},
                    "Access Denied": {"field": "access_denied", "format_number": True},
                    "Conn Denied": {"field": "denied_connections", "format_number": True},
                }
            )

        elif self.performance_schema_enabled:
            self.db.execute(Queries["ps_user_statisitics"])

            columns.update(
                {
                    "User": {"field": "user", "format_number": False},
                    "Active": {"field": "current_connections", "format_number": True},
                    "Total": {"field": "total_connections", "format_number": True},
                    "Rows Read": {"field": "rows_read", "format_number": True},
                    "Rows Sent": {"field": "rows_sent", "format_number": True},
                    "Rows Updated": {"field": "rows_affected", "format_number": True},
                    "Tmp Tables": {"field": "created_tmp_tables", "format_number": True},
                    "Tmp Disk Tables": {"field": "created_tmp_disk_tables", "format_number": True},
                }
            )
        else:
            return False

        users = self.db.fetchall()
        for user in users:
            username = user["user"]
            if userstat_enabled:
                user_stats.setdefault(username, {}).update(
                    user=username,
                    total_connections=user["total_connections"],
                    concurrent_connections=user.get("concurrent_connections"),
                    denied_connections=user.get("denied_connections"),
                    binlog_bytes_written=user.get("binlog_bytes_written"),
                    rows_fetched=user.get("rows_fetched"),
                    rows_updated=user.get("rows_updated"),
                    table_rows_read=user.get("table_rows_read"),
                    select_commands=user.get("select_commands"),
                    update_commands=user.get("update_commands"),
                    other_commands=user.get("other_commands"),
                    commit_transactions=user.get("commit_transactions"),
                    rollback_transactions=user.get("rollback_transactions"),
                    access_denied=user.get("access_denied"),
                    current_connections=user.get("current_connections"),
                    rows_affected=user.get("sum_rows_affected"),
                    rows_sent=user.get("sum_rows_sent"),
                    rows_read=user.get("sum_rows_examined"),
                    created_tmp_disk_tables=user.get("sum_created_tmp_disk_tables"),
                    created_tmp_tables=user.get("sum_created_tmp_tables"),
                )
            else:
                if username not in user_stats:
                    user_stats[username] = {
                        "user": username,
                        "total_connections": user["total_connections"],
                        "current_connections": user["current_connections"],
                        "rows_affected": user["sum_rows_affected"],
                        "rows_sent": user["sum_rows_sent"],
                        "rows_read": user["sum_rows_examined"],
                        "created_tmp_disk_tables": user["sum_created_tmp_disk_tables"],
                        "created_tmp_tables": user["sum_created_tmp_tables"],
                    }
                else:
                    # I would use SUM() in the query instead of this, but pymysql doesn't play well with it since I
                    # use use_unicode = 0 in the connection
                    user_stats[username]["rows_affected"] += user["sum_rows_affected"]
                    user_stats[username]["rows_sent"] += user["sum_rows_sent"]
                    user_stats[username]["rows_read"] += user["sum_rows_examined"]
                    user_stats[username]["created_tmp_disk_tables"] += user["sum_created_tmp_disk_tables"]
                    user_stats[username]["created_tmp_tables"] += user["sum_created_tmp_tables"]

        for column, data in columns.items():
            table.add_column(column, no_wrap=True)

        for user_data in user_stats.values():
            row_values = []
            for column, data in columns.items():
                value = user_data.get(data["field"])
                if column == "Binlog Data":
                    row_values.append(format_bytes(value) if value else "")
                elif data["format_number"]:
                    row_values.append(format_number(value) if value else "")
                else:
                    row_values.append(value or "")

            table.add_row(*row_values, style="grey93")

        return table if user_stats else False

    def load_host_cache_file(self):
        if os.path.exists(self.host_cache_file):
            with open(self.host_cache_file) as file:
                for line in file:
                    line = line.strip()
                    error_message = f"Host cache entry '{line}' is not properly formatted! Format: ip=hostname"

                    if "=" not in line:
                        raise ManualException(error_message)

                    host, hostname = line.split("=", maxsplit=1)
                    host = host.strip()
                    hostname = hostname.strip()

                    if not host or not hostname:
                        raise ManualException(error_message)

                    self.host_cache_from_file[host] = hostname

    def get_hostname(self, host):
        if host in self.host_cache:
            return self.host_cache[host]

        if self.host_cache_from_file and host in self.host_cache_from_file:
            self.host_cache[host] = self.host_cache_from_file[host]
            return self.host_cache_from_file[host]

        try:
            ipaddress.IPv4Network(host)
            hostname = socket.gethostbyaddr(host)[0]
            self.host_cache[host] = hostname
        except (ValueError, socket.error):
            self.host_cache[host] = host
            hostname = host

        return hostname
