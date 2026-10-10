#!/bin/sh
set -eu

until mariadb -h glpi-db -uglpi -p"$GLPI_DB_PASSWORD" glpi -Nse \
  "SELECT 1 FROM information_schema.tables WHERE table_schema='glpi' AND table_name='glpi_apiclients'" | grep -q 1; do
  sleep 2
done

mariadb -h glpi-db -uglpi -p"$GLPI_DB_PASSWORD" glpi <<'SQL'
UPDATE glpi_configs SET value='1'
WHERE context='core' AND name IN ('enable_api','enable_api_login_credentials');

INSERT INTO glpi_apiclients
  (entities_id,is_recursive,name,date_mod,date_creation,is_active,ipv4_range_start,ipv4_range_end,dolog_method,comment)
SELECT 0,1,'Nexora internal services',NOW(),NOW(),1,INET_ATON('172.30.250.10'),INET_ATON('172.30.250.11'),0,
       'Provisioned automatically for the private Docker network'
WHERE NOT EXISTS (SELECT 1 FROM glpi_apiclients WHERE name='Nexora internal services');
SQL
