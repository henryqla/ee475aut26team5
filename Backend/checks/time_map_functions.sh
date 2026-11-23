#!/usr/bin/env bash
# time_map_functions.sh - measures how long the map functions take, the way the app calls them.
# Not a PASS/FAIL check; it prints timings. Read-only; never prints a key.
#
# For a box around every stored segment (or the box you pass), and for several piece_m values:
#   API   - calls road_quality_snapped / events_in_area through the local API (127.0.0.1:54321,
#           so no Funnel or phone network time) as the app does: 1000 rows per request
#           (max_rows), next page until one comes back short. Reports pages, rows, MB and the
#           total time. Each page re-runs the whole function, since PostgREST applies the range
#           after the function has finished.
#   DB    - runs the function once inside Postgres (explain analyze), so API / DB shows how much
#           of the API time is paging overhead.
#
# Run on the laptop with Supabase running:
#     cd ~/ee475aut26team5/Backend && bash checks/time_map_functions.sh
#     bash checks/time_map_functions.sh 47.60 -122.36 47.70 -122.25   (south west north east)

set -u
cd "$(dirname "$0")/.."   # run from Backend/

API=http://127.0.0.1:54321
PAGE=1000          # max_rows in supabase/config.toml, the app's page size
MAX_PAGES=200
BOLD=$'\e[1m'; RED=$'\e[31m'; RESET=$'\e[0m'
die() { echo "${RED}$1${RESET}"; exit 1; }

status_env=$(supabase status -o env 2>/dev/null) || die "Supabase isn't running. Run: supabase start"
env_value() { printf '%s\n' "$status_env" | grep -E "^$1=" | head -1 | cut -d= -f2- | tr -d '"' | sed 's/[[:space:]]*$//'; }
PUB=$(env_value PUBLISHABLE_KEY); [[ -z $PUB ]] && PUB=$(env_value ANON_KEY)
DB_URL=$(env_value DB_URL)
q() { psql "$DB_URL" -qAtX -c "$1" 2>/dev/null; }

num='^-?[0-9]+(\.[0-9]+)?$'
if [[ $# -eq 4 ]]; then
    for v in "$@"; do [[ $v =~ $num ]] || die "Box values must be numbers: south west north east"; done
    S=$1; W=$2; N=$3; E=$4
elif [[ $# -eq 0 ]]; then
    box=$(q "select min(lat) - 0.001, min(lon) - 0.001, max(lat) + 0.001, max(lon) + 0.001 from public.segments where lat is not null")
    [[ -n $box && $box != '||'* ]] || die "No segments with a location stored."
    IFS='|' read -r S W N E <<< "$box"
else
    die "Usage: bash checks/time_map_functions.sh [south west north east]"
fi

echo "${BOLD}Box${RESET} south $S, west $W, north $N, east $E"
echo "Stored: $(q 'select count(*) from public.trips') trips, $(q 'select count(*) from public.segments') segments, $(q 'select count(*) from public.events') events, $(q 'select count(*) from roads.drive_pieces') drive pieces"
echo

tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT

# api_time FUNCTION JSON_PARAMS -> prints "pages rows MB seconds"
api_time() {
    local fn=$1 params=$2 offset=0 pages=0 rows=0 bytes=0 total=0 out code t size n
    while (( pages < MAX_PAGES )); do
        out=$(curl -s -o "$tmp" -w '%{http_code} %{time_total} %{size_download}' -m 300 \
              -X POST "$API/rest/v1/rpc/$fn?offset=$offset&limit=$PAGE" \
              -H "apikey: $PUB" -H "Content-Type: application/json" -d "$params")
        read -r code t size <<< "$out"
        [[ $code == 200 ]] || { echo "HTTP $code: $(head -c 200 "$tmp")" >&2; echo "- - - -"; return; }
        n=$(python3 -c 'import json, sys; print(len(json.load(open(sys.argv[1]))))' "$tmp")
        pages=$((pages + 1)); rows=$((rows + n)); bytes=$((bytes + size))
        total=$(python3 -c "print($total + $t)")
        (( n < PAGE )) && break
        offset=$((offset + PAGE))
    done
    python3 -c "print($pages, $rows, round($bytes / 1e6, 2), round($total, 2))"
}

# db_time SQL_CALL -> seconds for one run inside Postgres
db_time() {
    local ms
    ms=$(psql "$DB_URL" -qAtX -c "explain (analyze, timing off, summary) select count(*) from $1" 2>/dev/null \
         | grep -oE 'Execution Time: [0-9.]+' | grep -oE '[0-9.]+$')
    [[ -n $ms ]] && python3 -c "print(round($ms / 1000, 2))" || echo "-"
}

printf "${BOLD}%-22s %-12s %6s %7s %6s %8s %8s %8s${RESET}\n" function piece_m/fake pages rows MB "API s" "DB s" "API/DB"
row() {   # row LABEL1 LABEL2 FUNCTION JSON_PARAMS SQL_CALL
    local pages rows mb api db ratio
    db=$(db_time "$5")       # first run also warms the cache, so the API numbers aren't cold
    read -r pages rows mb api <<< "$(api_time "$3" "$4")"
    ratio=$(python3 -c "print(round($api / $db, 1) if '$db' not in ('-', '0.0') and '$api' != '-' else '-')" 2>/dev/null || echo -)
    printf "%-22s %-12s %6s %7s %6s %8s %8s %8s\n" "$1" "$2" "$pages" "$rows" "$mb" "$api" "$db" "$ratio"
}

box_json="\"south\": $S, \"west\": $W, \"north\": $N, \"east\": $E"
box_sql="$S, $W, $N, $E"
for fake in true false; do
    for piece in 15 60 240 960; do
        row road_quality_snapped "$piece/$fake" road_quality_snapped \
            "{$box_json, \"include_fake\": $fake, \"vehicle_id\": null, \"since_ms\": null, \"piece_m\": $piece}" \
            "public.road_quality_snapped($box_sql, include_fake => $fake, piece_m => $piece)"
    done
    row events_in_area "-/$fake" events_in_area \
        "{$box_json, \"include_fake\": $fake, \"vehicle_id\": null, \"since_ms\": null}" \
        "public.events_in_area($box_sql, include_fake => $fake)"
done

echo
echo "API s = what a phone would wait on a fast link (sum over pages). API/DB near 'pages' means"
echo "each page re-ran the whole function. Through Funnel and cellular, add network time per page."
