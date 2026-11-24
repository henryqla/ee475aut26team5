#!/usr/bin/env bash
# check_map_functions.sh - does events_in_area (contract §7) return what the contract says,
# including the since_ms / until_ms time filter and tile edges? Uploads four FAKE_DEVICE test
# drives in the middle of the Pacific (so no real data is nearby), calls the function as the web map would
# (not signed in), then deletes the drives and the test user with the secret key. Also checks
# that the old road_quality_in_area is gone. Prints PASS/FAIL only; never prints a key.
# road_quality_snapped is checked by check_snapped_map.sh (it needs the road matcher).
#
# Run on the laptop, with Supabase started and all migrations applied (supabase migration up):
#     cd ~/ee475aut26team5/Backend && bash checks/check_map_functions.sh

set -u
cd "$(dirname "$0")/.."   # run from Backend/

API=http://127.0.0.1:54321
GREEN=$'\e[32m'; RED=$'\e[31m'; BOLD=$'\e[1m'; RESET=$'\e[0m'
failures=0
pass() { echo "  ${GREEN}PASS${RESET}  $1"; }
fail() { echo "  ${RED}FAIL${RESET}  $1"; failures=$((failures + 1)); }

status_env=$(supabase status -o env 2>/dev/null) || {
    echo "${RED}Supabase isn't running.${RESET} Run: supabase start"; exit 1; }
env_value() { printf '%s\n' "$status_env" | grep -E "^$1=" | head -1 | cut -d= -f2- | tr -d '"' | sed 's/[[:space:]]*$//'; }
PUB=$(env_value PUBLISHABLE_KEY); [[ -z $PUB ]] && PUB=$(env_value ANON_KEY)
SECRET=$(env_value SECRET_KEY)

# req METHOD PATH TOKEN [BODY] -> sets CODE and BODY. TOKEN "" = not signed in.
req() {
    local headers=(-H "apikey: $PUB" -H "Content-Type: application/json")
    [[ -n $3 ]] && headers+=(-H "Authorization: Bearer $3")
    [[ -n ${4:-} ]] && headers+=(-d "$4")
    local out; out=$(curl -s -m 10 -w $'\n%{http_code}' -X "$1" "$API$2" "${headers[@]}")
    CODE=${out##*$'\n'}; BODY=${out%$'\n'*}
}

# expect DESCRIPTION PYTHON_EXPRESSION: checks the rows in BODY. In the expression, `rows` is the
# list of result rows and `dir(h, d)` is true when heading h is within 20° of d.
expect() {
    local result
    result=$(printf '%s' "$BODY" | python3 -c '
import json, sys
def dir(h, d): return h is not None and min(abs(h - d), 360 - abs(h - d)) <= 20
rows = json.load(sys.stdin)
print("yes" if eval(sys.argv[1]) else "no")' "$2" 2>/dev/null)
    [[ $result == yes ]] && pass "$1" || fail "$1 (got $CODE: ${BODY:0:300})"
}

# Anonymous sign-in, like the app does on first launch. Prints "<access_token> <user_id>".
sign_in() {
    curl -s -m 5 -X POST "$API/auth/v1/signup" -H "apikey: $PUB" \
         -H "Content-Type: application/json" -d '{}' | python3 -c '
import json, sys
r = json.load(sys.stdin)
print(r.get("access_token", ""), (r.get("user") or {}).get("id", ""))' 2>/dev/null
}

uuid() { cat /proc/sys/kernel/random/uuid; }
NOW=$(date +%s%3N)
OLD=$((NOW - 2 * 86400000))     # two days ago
CUT=$((NOW - 3600000))          # time-filter cutoff: an hour ago, between OLD and NOW
read -r PHONE USER_ID <<<"$(sign_in)"
[[ -z $PHONE ]] && { echo "${RED}Anonymous sign-in failed.${RESET} Check enable_anonymous_sign_ins = true and restart."; exit 1; }

# The test road: due north from (10.0, -150.0), 6 segments of 1 s at 10 m/s, 10 m apart.
# A, B (today) and D (two days ago) drive north (heading 0); C (today) drives it south.
A=$(uuid); B=$(uuid); C=$(uuid); D=$(uuid)
BOX='"south":9.999,"west":-150.001,"north":10.001,"east":-149.999'

trip() {      # $1 = id, $2 = start time
    echo "{\"id\":\"$1\",\"vehicle_id\":\"camry\",\"start_time\":$2,\"source\":\"FAKE_DEVICE\"}"
}
segments() {  # $1 = trip id, $2 = start time, $3 = heading
    local rows=() i
    for i in 0 1 2 3 4 5; do
        rows+=("{\"trip_id\":\"$1\",\"t_start\":$(($2 + i * 1000)),\"t_end\":$(($2 + i * 1000 + 1000)),\"lat\":$(awk "BEGIN{printf \"%.7f\", 10 + $i * 10 / 111195}"),\"lon\":-150.0,\"speed_mps\":10,\"gps_accuracy_m\":4,\"heading_deg\":$3,\"rms_z\":0.2,\"peak_z\":0.5,\"roll_rms\":1,\"pitch_rms\":1,\"source\":\"FAKE_DEVICE\"}")
    done
    local IFS=,; echo "[${rows[*]}]"
}
event() {     # $1 = trip id, $2 = start time, $3 = meters north of the start, $4 = heading, $5 = class, $6 = user_label, $7 = peak_z
    echo "{\"id\":\"$(uuid)\",\"trip_id\":\"$1\",\"vehicle_id\":\"camry\",\"t\":$(($2 + 2500)),\"lat\":$(awk "BEGIN{printf \"%.7f\", 10 + $3 / 111195}"),\"lon\":-150.0,\"speed_mps\":10,\"gps_accuracy_m\":4,\"heading_deg\":$4,\"event_class\":\"$5\",\"user_label\":$6,\"peak_z\":$7,\"roll_peak\":40,\"pitch_peak\":10,\"conf\":0.7,\"source\":\"FAKE_DEVICE\"}"
}

echo "${BOLD}1. Upload test drives${RESET}"
ok=1
for t in "$A $NOW 0" "$B $NOW 0" "$D $OLD 0" "$C $NOW 180"; do
    set -- $t
    req POST /rest/v1/trips "$PHONE" "$(trip "$1" "$2")";        [[ $CODE == 2* ]] || ok=0
    req POST /rest/v1/segments "$PHONE" "$(segments "$@")";      [[ $CODE == 2* ]] || ok=0
done
# A and B hit the same pothole 4 m apart (B labels it UNKNOWN = not sure, so it stays a POTHOLE);
# C hits it going the other way; D's POTHOLE 15 m further on is relabelled SPEED_BUMP.
req POST /rest/v1/events "$PHONE" "[$(event "$A" "$NOW" 25 0 POTHOLE null 1.5),$(event "$B" "$NOW" 29 3 POTHOLE '"UNKNOWN"' 2.5),$(event "$C" "$NOW" 25 180 POTHOLE null 1.8),$(event "$D" "$OLD" 40 0 POTHOLE '"SPEED_BUMP"' 1.2)]"
[[ $CODE == 2* ]] || ok=0
(( ok )) && pass "4 trips, 24 segments, 4 events uploaded" || fail "upload failed (last: $CODE: ${BODY:0:300})"

echo "${BOLD}2. events_in_area${RESET}"
req POST /rest/v1/rpc/events_in_area "" "{$BOX}"
expect "fake data is hidden by default" 'rows == []'
req POST /rest/v1/rpc/events_in_area "" "{$BOX,\"include_fake\":true}"
expect "the web map (not signed in) can call it: 3 spots (pothole north, pothole south, speed bump)" 'len(rows) == 3'
expect "north pothole: A and B merged (UNKNOWN label = no change), 2 hits of 3 passes, max_peak_z 2.5" \
    'any(r["event_class"] == "POTHOLE" and dir(r["heading_deg"], 0) and r["hits"] == 2 and r["passes"] == 3 and abs(r["max_peak_z"] - 2.5) < 0.01 for r in rows)'
expect "south pothole is separate: 1 hit of 1 pass" \
    'any(r["event_class"] == "POTHOLE" and dir(r["heading_deg"], 180) and r["hits"] == 1 and r["passes"] == 1 for r in rows)'
expect "SPEED_BUMP label overrides POTHOLE: 1 hit of 3 passes" \
    'any(r["event_class"] == "SPEED_BUMP" and r["hits"] == 1 and r["passes"] == 3 for r in rows)'
req POST /rest/v1/rpc/events_in_area "" "{$BOX,\"include_fake\":true,\"vehicle_id\":\"camry\"}"
expect "vehicle_id filter: camry still sees all 3" 'len(rows) == 3'
req POST /rest/v1/rpc/events_in_area "" "{$BOX,\"include_fake\":true,\"vehicle_id\":\"truck\"}"
expect "vehicle_id filter: truck sees none" 'rows == []'

echo "${BOLD}3. Tiles: each merged event from exactly one tile${RESET}"
# Cut the box 26 m north of the start: A's hit (25 m) is south of the edge, B's (29 m) north of
# it, their merged position (27 m) north. C's (25 m, southbound) south; D's (40 m) north.
EDGE=$(awk "BEGIN{printf \"%.7f\", 10 + 26 / 111195}")
req POST /rest/v1/rpc/events_in_area "" "{\"south\":9.999,\"west\":-150.001,\"north\":$EDGE,\"east\":-149.999,\"include_fake\":true}"
expect "south tile: only C's southbound pothole (A's hit is merged into the north tile's spot)" \
    'len(rows) == 1 and dir(rows[0]["heading_deg"], 180)'
req POST /rest/v1/rpc/events_in_area "" "{\"south\":$EDGE,\"west\":-150.001,\"north\":10.001,\"east\":-149.999,\"include_fake\":true}"
expect "north tile: the north pothole with A's hit from over the edge (2 hits of 3 passes), and the speed bump" \
    'len(rows) == 2 and any(r["event_class"] == "POTHOLE" and r["hits"] == 2 and r["passes"] == 3 for r in rows) and any(r["event_class"] == "SPEED_BUMP" for r in rows)'
req POST /rest/v1/rpc/events_in_area "" "{\"south\":9.999,\"west\":-150.001,\"north\":10.001,\"east\":-150.0,\"include_fake\":true}"
expect "the box is half-open: events at lon -150.0 are not in a tile with east = -150.0" 'rows == []'
req POST /rest/v1/rpc/events_in_area "" "{\"south\":9.999,\"west\":-150.0,\"north\":10.001,\"east\":-149.999,\"include_fake\":true}"
expect "... but are in the tile with west = -150.0 (all 3 spots)" 'len(rows) == 3'

echo "${BOLD}4. Time filter (since_ms / until_ms, by trip start_time)${RESET}"
req POST /rest/v1/rpc/events_in_area "" "{$BOX,\"include_fake\":true,\"since_ms\":$CUT}"
expect "since an hour ago: D's speed bump is gone (2 spots left)" \
    'len(rows) == 2 and not any(r["event_class"] == "SPEED_BUMP" for r in rows)'
expect "since an hour ago: north pothole is 2 hits of 2 passes (D's old drive no longer passes)" \
    'any(r["event_class"] == "POTHOLE" and dir(r["heading_deg"], 0) and r["hits"] == 2 and r["passes"] == 2 for r in rows)'
req POST /rest/v1/rpc/events_in_area "" "{$BOX,\"include_fake\":true,\"until_ms\":$CUT}"
expect "until an hour ago: only D's speed bump, 1 hit of 1 pass" \
    'len(rows) == 1 and rows[0]["event_class"] == "SPEED_BUMP" and rows[0]["hits"] == 1 and rows[0]["passes"] == 1'
req POST /rest/v1/rpc/events_in_area "" "{$BOX,\"include_fake\":true,\"since_ms\":$OLD,\"until_ms\":$((OLD + 1))}"
expect "since_ms is inclusive, until_ms exclusive (a 1 ms window on D's start finds it)" \
    'len(rows) == 1 and rows[0]["event_class"] == "SPEED_BUMP"'
req POST /rest/v1/rpc/events_in_area "" "{$BOX,\"include_fake\":true,\"since_ms\":$((NOW + 3600000))}"
expect "since a time after every drive: nothing" 'rows == []'
req POST /rest/v1/rpc/events_in_area "" "{$BOX,\"include_fake\":true,\"since_ms\":null,\"until_ms\":null}"
expect "since_ms / until_ms sent as null: same as not sent (3 spots)" 'len(rows) == 3'

echo "${BOLD}5. road_quality_in_area is gone${RESET}"
req POST /rest/v1/rpc/road_quality_in_area "" "{$BOX,\"include_fake\":true}"
[[ $CODE == 404 ]] && pass "calling it returns 404 (function not found)" || fail "expected 404, got $CODE: ${BODY:0:300}"

echo "${BOLD}6. Cleanup${RESET}"
code=$(curl -s -m 5 -o /dev/null -w '%{http_code}' -X DELETE "$API/rest/v1/trips?id=in.($A,$B,$C,$D)" -H "apikey: $SECRET")
[[ $code == 2* ]] && pass "test trips deleted with the secret key" || fail "couldn't delete test trips ($code); delete trips $A $B $C $D in Studio"
curl -s -m 5 -o /dev/null -X DELETE "$API/auth/v1/admin/users/$USER_ID" -H "apikey: $SECRET"

echo
if (( failures == 0 )); then echo "${GREEN}${BOLD}Map functions work as the contract says.${RESET}"
else echo "${RED}${BOLD}$failures check(s) failed.${RESET} Send this output (it contains no keys)."; fi
