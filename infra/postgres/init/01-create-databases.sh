#!/bin/sh
set -eu

psql --set ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  --set central_password="$CENTRAL_DB_PASSWORD" \
  --set zabbix_password="$ZABBIX_DB_PASSWORD" \
  --set keycloak_password="$KEYCLOAK_DB_PASSWORD" <<'SQL'
CREATE ROLE central_app LOGIN PASSWORD :'central_password';
CREATE ROLE zabbix_app LOGIN PASSWORD :'zabbix_password';
CREATE ROLE keycloak_app LOGIN PASSWORD :'keycloak_password';

CREATE DATABASE central_monitor OWNER central_app;
CREATE DATABASE zabbix OWNER zabbix_app;
CREATE DATABASE keycloak OWNER keycloak_app;

REVOKE ALL ON DATABASE central_monitor FROM PUBLIC;
REVOKE ALL ON DATABASE zabbix FROM PUBLIC;
REVOKE ALL ON DATABASE keycloak FROM PUBLIC;
SQL

