#!/bin/sh
set -eu

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
SELECT 'CREATE DATABASE ${POSTGRES_TEST_DB:-maintenance_agent_test}'
WHERE NOT EXISTS (
  SELECT FROM pg_database WHERE datname = '${POSTGRES_TEST_DB:-maintenance_agent_test}'
)\gexec
SQL
