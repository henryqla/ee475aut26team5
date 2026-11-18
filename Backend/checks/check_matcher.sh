#!/usr/bin/env bash
# check_matcher.sh - is road matching set up, running, and locked down?
#   1. Valhalla answers on 127.0.0.1:8002, only there, and matches a known road
#   2. OSM roads are loaded
#   3. the road_matcher database role can do only what it needs
#   4. nothing new is reachable through the API except road_quality_snapped
#   5. the road-matcher service runs as you, locked down, and is connected
# Prints PASS/FAIL only; never prints a key or password.
#
# Run on the laptop after ROAD_MATCHING_SETUP.txt steps 1-6:
#     cd ~/ee475aut26team5/Backend && bash checks/check_matcher.sh

set -u
cd "$(dirname "$0")/.."   # run from Backend/

API=http://127.0.0.1:54321
VALHALLA=http://127.0.0.1:8002
GREEN=$'\e[32m'; RED=$'\e[31m'; BOLD=$'\e[1m'; RESET=$'\e[0m'
failures=0
pass() { echo "  ${GREEN}PASS${RESET}  $1"; }
fail() { echo "  ${RED}FAIL${RESET}  $1"; failures=$((failures + 1)); }
check() { if eval "$2"; then pass "$1"; else fail "$1${3:+ ($3)}"; fi; }

status_env=$(supabase status -o env 2>/dev/null) || {
    echo "${RED}Supabase isn't running.${RESET} Run: supabase start"; exit 1; }
env_value() { printf '%s\n' "$status_env" | grep -E "^$1=" | head -1 | cut -d= -f2- | tr -d '"' | sed 's/[[:space:]]*$//'; }
PUB=$(env_value PUBLISHABLE_KEY); [[ -z $PUB ]] && PUB=$(env_value ANON_KEY)
DB_URL=$(env_value DB_URL)
q() { psql "$DB_URL" -qAtX -c "$1" 2>/dev/null; }

echo "${BOLD}1. Valhalla${RESET}"
code=$(curl -s -m 5 -o /dev/null -w '%{http_code}' "$VALHALLA/status")
check "answers on 127.0.0.1:8002" '[[ $code == 200 ]]' "got $code; see: docker compose -f valhalla/docker-compose.yml logs --tail 50"
listen=$(ss -ltnH '( sport = :8002 )' 2>/dev/null | awk '{print $4}')
check "listens on localhost only" \
    '[[ -n $listen ]] && ! printf "%s\n" "$listen" | grep -qvE "^(127\.0\.0\.1|\[::1\]):8002$"' "listening on: ${listen:-nothing}"
lan_ip=$(hostname -I 2>/dev/null | awk '{print $1}')
if [[ -n $lan_ip ]]; then
    lan_code=$(curl -s -m 3 -o /dev/null -w '%{http_code}' "http://$lan_ip:8002/status")
    check "not reachable from the network ($lan_ip:8002)" '[[ $lan_code == 000 ]]' "got $lan_code"
fi

# A two-way residential road from roads.ways, sampled at 7 points along it.
road=$(q "select w.way_id || ' ' || (select json_agg(json_build_object(
                 'lat', extensions.st_y(p), 'lon', extensions.st_x(p)) order by f)
             from (select f, extensions.st_transform(extensions.st_lineinterpolatepoint(w.geom, f), 4326) as p
                   from generate_series(0.2, 0.8, 0.1) f) x)
          from roads.ways w
          where w.highway = 'residential' and not w.oneway and w.len_m between 300 and 1500
            and w.len_m < 1.05 * extensions.st_distance(extensions.st_startpoint(w.geom), extensions.st_endpoint(w.geom))
          order by w.way_id limit 1")
if [[ -n $road ]]; then
    way=${road%% *}; shape=${road#* }
    body="{\"shape\":$shape,\"costing\":\"auto\",\"shape_match\":\"map_snap\",\"filters\":{\"attributes\":[\"edge.way_id\"],\"action\":\"include\"}}"
    matched=$(curl -s -m 30 -X POST "$VALHALLA/trace_attributes" -H 'Content-Type: application/json' -d "$body" \
        | python3 -c 'import json, sys; print(" ".join(str(e.get("way_id")) for e in json.load(sys.stdin).get("edges", [])))' 2>/dev/null)
    check "matches points on OSM way $way back to that way (same OSM data in both)" \
        '[[ " $matched " == *" $way "* ]]' "got ways: ${matched:-none}"
else
    fail "couldn't pick a test road from roads.ways (load the roads first)"
fi

echo "${BOLD}2. OSM roads${RESET}"
n=$(q "select count(*) from roads.ways"); n=${n:-0}
check "roads.ways has Washington's roads ($n)" '(( n > 100000 ))' "run: bash roads/import_roads.sh"
check "no leftover import schema" '[[ -z $(q "select 1 from pg_namespace where nspname = '"'osm_import'"'") ]]'
osm_public=$(q "select string_agg(tablename, ' ') from pg_tables where schemaname = 'public'
                and (tablename like 'osm2pgsql%' or tablename like 'planet_osm%')")
check "no osm2pgsql tables in public (the API would expose them)" '[[ -z $osm_public ]]' "found: $osm_public"
open_public=$(q "select string_agg(c.relname, ' ') from pg_class c
                 where c.relnamespace = 'public'::regnamespace and c.relkind = 'r' and not c.relrowsecurity")
check "every table in public has RLS on" '[[ -z $open_public ]]' "without RLS: $open_public"

echo "${BOLD}3. road_matcher database role${RESET}"
role=$(q "select rolcanlogin, rolsuper, rolcreaterole, rolcreatedb, rolbypassrls, rolreplication
          from pg_roles where rolname = 'road_matcher'")
check "can log in, and is not superuser / can't create roles or databases / doesn't bypass RLS" \
    '[[ $role == "t|f|f|f|f|f" ]]' "got $role"
check "is a member of no other role" \
    '[[ $(q "select count(*) from pg_auth_members where member = '"'road_matcher'"'::regrole") == 0 ]]'
tables="public.vehicles public.trips public.segments public.events roads.ways roads.trip_matches roads.segment_matches roads.drive_pieces"
any=""
for t in $tables; do
    [[ $(q "select has_table_privilege('road_matcher', '$t', 'select, insert, update, delete, truncate')") == t ]] && any+=" $t"
done
check "has no direct access to any table" '[[ -z $any ]]' "has access to:$any"
fn() { q "select has_function_privilege('road_matcher', '$1', 'execute')"; }
check "can call trips_to_match, trip_points, save_trip_match" \
    '[[ $(fn "roads.trips_to_match(integer)")$(fn "roads.trip_points(uuid)")$(fn "roads.save_trip_match(uuid, integer, text, jsonb)") == ttt ]]'
check "can't call load_ways, rebuild_trip, rebuild_all_trips" \
    '[[ $(fn "roads.load_ways()")$(fn "roads.rebuild_trip(uuid)")$(fn "roads.rebuild_all_trips()") == fff ]]'
check "anon and authenticated can't call the worker functions" \
    '[[ $(q "select bool_or(has_function_privilege(r, f, '"'execute'"'))
             from unnest(array['"'anon'"', '"'authenticated'"']) r,
                  unnest(array['"'roads.trip_points(uuid)'"', '"'roads.save_trip_match(uuid, integer, text, jsonb)'"',
                               '"'roads.trips_to_match(integer)'"', '"'roads.load_ways()'"']) f") == f ]]'

echo "${BOLD}4. API${RESET}"
code=$(curl -s -m 5 -o /dev/null -w '%{http_code}' "$API/rest/v1/ways?limit=1" -H "apikey: $PUB" -H "Accept-Profile: roads")
check "the roads schema isn't reachable through the API" '[[ $code != 2* ]]' "got $code"
code=$(curl -s -m 5 -o /dev/null -w '%{http_code}' -X POST "$API/rest/v1/rpc/save_trip_match" -H "apikey: $PUB" \
    -H "Content-Type: application/json" -d '{}')
check "save_trip_match can't be called through the API" '[[ $code != 2* ]]' "got $code"
code=$(curl -s -m 10 -o /dev/null -w '%{http_code}' -X POST "$API/rest/v1/rpc/road_quality_snapped" -H "apikey: $PUB" \
    -H "Content-Type: application/json" -d '{"south":47.65,"west":-122.32,"north":47.66,"east":-122.30}')
check "road_quality_snapped can be called by the web map (not signed in)" '[[ $code == 200 ]]' "got $code"

echo "${BOLD}5. road-matcher service${RESET}"
env_file=matcher/matcher.env
check "matcher.env exists, readable by you only" \
    '[[ -f $env_file && $(stat -c %a "$env_file") == 600 && $(stat -c %U "$env_file") == $(id -un) ]]'
check "matcher.env is gitignored" 'git check-ignore -q "$env_file"'
check "service is running" 'systemctl is-active --quiet road-matcher' "see: journalctl -u road-matcher -n 50"
svc_user=$(systemctl show -p User --value road-matcher 2>/dev/null)
check "runs as an ordinary user ($svc_user), not root" '[[ -n $svc_user && $svc_user != root ]]'
check "runs locked down (no new privileges, read-only system, localhost-only network)" \
    '[[ $(systemctl show -p NoNewPrivileges --value road-matcher) == yes
        && $(systemctl show -p ProtectSystem --value road-matcher) == strict
        && $(systemctl show -p IPAddressDeny --value road-matcher) != "" ]]'
check "is connected to the database and listening" \
    '[[ $(q "select count(*) from pg_stat_activity where usename = '"'road_matcher'"'") -ge 1 ]]' \
    "see: journalctl -u road-matcher -n 50"

echo
if (( failures == 0 )); then echo "${GREEN}${BOLD}Road matching is set up and locked down.${RESET}"
else echo "${RED}${BOLD}$failures check(s) failed.${RESET} Send this output (it contains no keys)."; fi
