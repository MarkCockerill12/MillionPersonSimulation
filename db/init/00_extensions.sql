-- Runs once, the first time the Postgres volume is created.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgrouting;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- Read-only role used by the in-app SQL console.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'analyst') THEN
    CREATE ROLE analyst LOGIN PASSWORD 'analyst';
  END IF;
END $$;
ALTER ROLE analyst SET statement_timeout = '15s';
ALTER ROLE analyst SET default_transaction_read_only = on;
