# Dolphie
<p align="center">
<img src="https://user-images.githubusercontent.com/13244625/187600748-19d2ad15-42e8-4f9c-ada5-a153cdcf4070.png" width="120"><br>
An intuitive feature-rich top tool for monitoring MySQL in real time
</p>


## Installation
Requires Python 3.8+

#### Using PyPi
```shell
pip install dolphie
```

#### Using Poetry
```shell
curl -sSL https://install.python-poetry.org | python3 -

poetry install
```

#### Using Docker
```
docker pull ghcr.io/charles-001/dolphie:latest
docker run -dit --name dolphie ghcr.io/charles-001/dolphie:latest
docker exec -it dolphie dolphie -h host.docker.internal -u root --ask-pass
```

## Usage
```
options:
  --help                show this help message and exit
  -u USER, --user USER  Username for MySQL
  -p PASSWORD, --password PASSWORD
                        Password for MySQL
  --ask-pass            Ask for password (hidden text)
  -h HOST, --host HOST  Hostname/IP address for MySQL
  -P PORT, --port PORT  Port for MySQL (Socket has precendence)
  -S SOCKET, --socket SOCKET
                        Socket file for MySQL
  -c CONFIG_FILE, --config-file CONFIG_FILE
                        Absolute config file path to use. This should use [client] section. See below for options support [default: ~/.my.cnf]
  -f HOST_CACHE_FILE, --host-cache-file HOST_CACHE_FILE
                        Resolve IPs to hostnames when your DNS is unable to. Each IP/hostname pair should be on its own line using format: ip=hostname [default: ~/dolphie_host_cache]
  -q QUICK_SWITCH_HOSTS_FILE, --quick-switch-hosts-file QUICK_SWITCH_HOSTS_FILE
                        Specify where the file is that stores the hosts you connect to for quick switching [default: ~/dolphie_quick_switch_hosts]
  -l LOGIN_PATH, --login-path LOGIN_PATH
                        Specify login path to use mysql_config_editor's file ~/.mylogin.cnf for encrypted login credentials. Supercedes config file [default: client]
  -r REFRESH_INTERVAL, --refresh_interval REFRESH_INTERVAL
                        How much time to wait in seconds between each refresh [default: 1]
  -H HEARTBEAT_TABLE, --heartbeat-table HEARTBEAT_TABLE
                        If your hosts use pt-heartbeat, specify table in format db.table to use the timestamp it has for replication lag instead of Seconds_Behind_Master from SHOW SLAVE STATUS
  --ssl-mode SSL_MODE   Desired security state of the connection to the host. Supports: REQUIRED/VERIFY_CA/VERIFY_IDENTITY [default: OFF]
  --ssl-ca SSL_CA       Path to the file that contains a PEM-formatted CA certificate
  --ssl-cert SSL_CERT   Path to the file that contains a PEM-formatted client certificate
  --ssl-key SSL_KEY     Path to the file that contains a PEM-formatted private key for the client certificate
  --hide-dashboard      Start without showing dashboard. This is good to use if you want to reclaim terminal space and not execute the additional queries for it
  --show-trxs-only      Start with only showing queries that are running a transaction
  --additional-columns  Start with additional columns in processlist panel
  --use-processlist     Start with using Processlist instead of Performance Schema for listing queries
  -V, --version         Display version and exit

Config file with [client] section supports these options:
    host
    user
    password
    port
    socket
    ssl_mode REQUIRED/VERIFY_CA/VERIFY_IDENTITY
    ssl_ca
    ssl_cert
    ssl_key

Login path file supports these options:
    host
    user
    password
    port
    socket

Environment variables support these options:
    DOLPHIE_USER
    DOLPHIE_PASSWORD
    DOLPHIE_HOST
    DOLPHIE_PORT
    DOLPHIE_SOCKET

```

## Supported MySQL versions
- MySQL/Percona Server 5.6/5.7/8.0
- MariaDB 10+ (maybe, let me know :smiley:)
- RDS/Aurora

## Grants required
#### Least privilege
1. PROCESS (if you don't use `performance_schema`)
2. SELECT to `performance_schema` (if used) + `pt-heartbeat table` (if used)
3. REPLICATION CLIENT
4. BACKUP_ADMIN (MySQL 8 only)

#### Recommended
1. PROCESS (if you don't use `performance_schema`)
2. Global SELECT access (good for explaining queries, listing all databases, etc)
4. REPLICATION CLIENT
5. SUPER (required if you want to kill queries)
6. BACKUP_ADMIN (MySQL 8 only)

## Features
- Dolphie uses panels to present groups of data. They can all be turned on/off to have a view of your database server that you prefer (see Help screenshot for panels available)
- Graphs for many metrics that can give you great insight into how your database is performing
- Quick switch host for connecting to different hosts instead of reloading the application. It keeps a history of the servers you connect to that provides autocompletion for hostnames
- Prefers Performance Schema over Processlist if it's turned on for listing queries. Can be switched to use Processlist by pressing key "1" (or using parameter) since P_S can truncate query length for explaining queries
- 3 options for finding replica lag in this order of precedence:
  - `pt-heartbeat table` (specified by `--heartbeat-table`)
  - `Performance Schema` (MySQL 8 only)
  - `SHOW SLAVE STATUS`
- Host cache file. This provides users a way to specify hostnames for IPs when their network's DNS can't resolve them. An example use case for this is when you connect to your work's VPN and DNS isn't available to resolve IPs. In my opinion, it's a lot easier to look at hostnames than IPs!
- Supports encrypted login credentials via `mysql_config_editor`
- Automatic conversion of large numbers & bytes to human-readable
- Notifies when new version is available
- Many commands at your fingertips with autocompletion for their input


## Things to note
Order of precedence for variables passed to Dolphie:
1. Command-line
2. Environment variables
3. ~/.mylogin.cnf (`mysql_config_editor`)
4. ~/.my.cnf

## Feedback
I welcome all questions, bug reports, and requests. If you enjoy Dolphie, please let me know! I'd love to hear from you :smiley:
