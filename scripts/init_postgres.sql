\set ON_ERROR_STOP on

-- Usage:
-- psql -U postgres -d postgres ^
--   -v app_db=ai_testcase_gen ^
--   -v app_user=ai_testcase_user ^
--   -v app_password=change_me_strong_password ^
--   -f scripts/init_postgres.sql

\if :{?app_db}
\else
\set app_db ai_testcase_gen
\endif

\if :{?app_user}
\else
\set app_user ai_testcase_user
\endif

\if :{?app_password}
\else
\set app_password change_me_strong_password
\endif

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user') THEN
        EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L', :'app_user', :'app_password');
    ELSE
        EXECUTE format('ALTER ROLE %I WITH LOGIN PASSWORD %L', :'app_user', :'app_password');
    END IF;
END
$$;

SELECT format('CREATE DATABASE %I OWNER %I', :'app_db', :'app_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'app_db')
\gexec

SELECT format('ALTER DATABASE %I OWNER TO %I', :'app_db', :'app_user')
\gexec

\connect :app_db

GRANT ALL PRIVILEGES ON SCHEMA public TO :app_user;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO :app_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO :app_user;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
GRANT ALL PRIVILEGES ON TABLES TO :app_user;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
GRANT ALL PRIVILEGES ON SEQUENCES TO :app_user;

SELECT current_database() AS initialized_database, current_user AS connected_user;
