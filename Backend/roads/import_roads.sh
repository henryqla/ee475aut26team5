#!/usr/bin/env bash
# import_roads.sh - load the OSM roads from the Washington extract into roads.ways.
# Uses the same .osm.pbf file Valhalla builds its tiles from, so way ids match.
# Replacing the roads clears all road matches; the matcher then matches every trip again.
# Never prints a key or password.
#
# Run on the laptop, with Supabase started and all migrations applied:
#     cd ~/ee475aut26team5/Backend && bash roads/import_roads.sh

set -euo pipefail
cd "$(dirname "$0")/.."   # run from Backend/

GREEN=$'\e[32m'; RED=$'\e[31m'; BOLD=$'\e[1m'; RESET=$'\e[0m'
die() { echo "${RED}$1${RESET}"; exit 1; }

PBF=valhalla/custom_files/washington-latest.osm.pbf
[[ -f $PBF ]] || die "No $PBF. Download it first (ROAD_MATCHING_SETUP.txt step 3)."
command -v osm2pgsql >/dev/null || die "osm2pgsql isn't installed: sudo apt install -y osm2pgsql"
command -v psql      >/dev/null || die "psql isn't installed: sudo apt install -y postgresql-client"

status_env=$(supabase status -o env 2>/dev/null) || die "Supabase isn't running. Run: supabase start"
DB_URL=$(printf '%s\n' "$status_env" | grep -E '^DB_URL=' | head -1 | cut -d= -f2- | tr -d '"' | sed 's/[[:space:]]*$//')
[[ -n $DB_URL ]] || die "Couldn't read DB_URL from supabase status."

psql "$DB_URL" -qAtc "select 1 from pg_proc where proname = 'load_ways' and pronamespace = 'roads'::regnamespace" \
    | grep -q 1 || die "roads.load_ways is missing. Run: supabase migration up"

echo "${BOLD}1. osm2pgsql: reading $PBF (takes a few minutes)${RESET}"
psql "$DB_URL" -q -v ON_ERROR_STOP=1 -c "drop schema if exists osm_import cascade; create schema osm_import;"
# osm2pgsql 1.9+ also writes a bookkeeping table (osm2pgsql_properties), by default into public,
# where the API would expose it. --schema puts it in osm_import too, which is dropped below.
schema_opt=()
osm2pgsql --help 2>&1 | grep -qE '(^|[ ,])--schema[= ]' && schema_opt=(--schema=osm_import)
# PostGIS lives in the extensions schema, so osm2pgsql needs it on its search path.
PGOPTIONS='-c search_path=osm_import,extensions' \
    osm2pgsql --output=flex --style=roads/roads.lua --create --cache=4000 "${schema_opt[@]}" \
              --database="$DB_URL" "$PBF" 2>&1 | grep -viE 'password|postgresql://' || true
psql "$DB_URL" -qAtc "select count(*) from osm_import.ways" | grep -qE '^[1-9]' \
    || die "osm2pgsql loaded no roads. Scroll up for its error."

echo "${BOLD}2. Replace roads.ways${RESET}"
n=$(psql "$DB_URL" -qAt -v ON_ERROR_STOP=1 -c "select roads.load_ways();")
psql "$DB_URL" -q -c "drop schema osm_import cascade;"
# Safety net: nothing osm2pgsql makes may stay in public (it would be reachable through the API).
psql "$DB_URL" -q -c "drop table if exists public.osm2pgsql_properties;" 2>/dev/null
left=$(psql "$DB_URL" -qAt -c "select string_agg(tablename, ' ') from pg_tables
                               where schemaname = 'public' and (tablename like 'osm2pgsql%' or tablename like 'planet_osm%')")
[[ -z $left ]] || die "osm2pgsql left tables in public: $left. Drop them in Studio before going on."
psql "$DB_URL" -q -c "analyze roads.ways;"

echo
echo "${GREEN}${BOLD}Loaded $n roads.${RESET} The matcher will now match every trip again (watch: journalctl -u road-matcher -f)."
