#!/usr/bin/env bash
# check_map_functions.sh - do road_quality_in_area and events_in_area (contract §7) return
# what the contract says? Uploads four FAKE_DEVICE test drives in the middle of the Pacific (so no
# real data is nearby), calls both functions as the web map would (not signed in), then deletes
# the drives and the test user with the secret key. Prints PASS/FAIL only; never prints a key.
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
allowed() { [[ $CODE == 2* ]] && pass "$1" || fail "$1 (got $CODE: ${BODY:0:300})"; }

# expect DESCRIPTION PYTHON_EXPRESSION: checks the rows in BODY. In the expression, `rows` is the
# list of result rows, `dir(h, d)` is true when heading h is within 20° of d.
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
read -r PHONE USER_ID <<<"$(sign_in)"
[[ -z $PHONE ]] && { echo "${RED}Anonymous sign-in failed.${RESET} Check enable_anonymous_sign_ins = true and restart."; exit 1; }

# The test road: due north from (10.0, -150.0), 6 segments of 1 s at 10 m/s, 10 m apart.
# A, B, D drive north (heading 0) with roughness 0.1, 0.2, 0.9 (median 0.2, average 0.4);
# C drives the same road south (heading 180).
A=$(uuid); B=$(uuid); C=$(uuid); D=$(uuid)
M=0.0000899   # 10 m in degrees of latitude
BOX='"south":9.999,"west":-150.001,"north":10.001,"east":-149.999'

trip() {      # $1 = id
    echo "{\"id\":\"$1\",\"vehicle_id\":\"camry\",\"start_time\":$NOW,\"source\":\"FAKE_DEVICE\"}"
}
segments() {  # $1 = trip id, $2 = heading, $3 = rms_z
    local rows=() i
    for i in 0 1 2 3 4 5; do
        rows+=("{\"trip_id\":\"$1\",\"t_start\":$((NOW + i * 1000)),\"t_end\":$((NOW + i * 1000 + 1000)),\"lat\":$(awk "BEGIN{printf \"%.7f\", 10 + $i * $M}"),\"lon\":-150.0,\"speed_mps\":10,\"gps_accuracy_m\":4,\"heading_deg\":$2,\"rms_z\":$3,\"peak_z\":0.5,\"roll_rms\":1,\"pitch_rms\":1,\"source\":\"FAKE_DEVICE\"}")
    done
    local IFS=,; echo "[${rows[*]}]"
}
event() {     # $1 = trip id, $2 = meters north of the start, $3 = heading, $4 = class, $5 = user_label, $6 = peak_z
    echo "{\"id\":\"$(uuid)\",\"trip_id\":\"$1\",\"vehicle_id\":\"camry\",\"t\":$((NOW + 2500)),\"lat\":$(awk "BEGIN{printf \"%.7f\", 10 + $2 / 111195}"),\"lon\":-150.0,\"speed_mps\":10,\"gps_accuracy_m\":4,\"heading_deg\":$3,\"event_class\":\"$4\",\"user_label\":$5,\"peak_z\":$6,\"roll_peak\":40,\"pitch_peak\":10,\"conf\":0.7,\"source\":\"FAKE_DEVICE\"}"
}

echo "${BOLD}1. Upload test drives${RESET}"
ok=1
for t in "$A 0 0.1" "$B 0 0.2" "$D 0 0.9" "$C 180 0.5"; do
    set -- $t
    req POST /rest/v1/trips "$PHONE" "$(trip "$1")";             [[ $CODE == 2* ]] || ok=0
    req POST /rest/v1/segments "$PHONE" "$(segments "$1" "$2" "$3")"; [[ $CODE == 2* ]] || ok=0
done
# A and B hit the same pothole 4 m apart (B labels it UNKNOWN = not sure, so it stays a POTHOLE);
# C hits it going the other way; D's POTHOLE 15 m further on is relabelled SPEED_BUMP.
req POST /rest/v1/events "$PHONE" "[$(event "$A" 25 0 POTHOLE null 1.5),$(event "$B" 29 3 POTHOLE '"UNKNOWN"' 2.5),$(event "$C" 25 180 POTHOLE null 1.8),$(event "$D" 40 0 POTHOLE '"SPEED_BUMP"' 1.2)]"
[[ $CODE == 2* ]] || ok=0
(( ok )) && pass "4 trips, 24 segments, 4 events uploaded" || fail "upload failed (last: $CODE: ${BODY:0:300})"

echo "${BOLD}2. road_quality_in_area${RESET}"
req POST /rest/v1/rpc/road_quality_in_area "" "{$BOX}"
expect "fake data is hidden by default" 'rows == []'
req POST /rest/v1/rpc/road_quality_in_area "" "{$BOX,\"include_fake\":true}"
expect "the web map (not signed in) can call it" 'len(rows) > 0'
expect "every row has length_m = 15" 'all(r["length_m"] == 15 for r in rows)'
expect "north rows: 3 drives, roughness is the median (0.2), not the average" \
    'any(dir(r["heading_deg"], 0) for r in rows) and all(r["drives"] == 3 and abs(r["roughness"] - 0.2) < 0.01 for r in rows if dir(r["heading_deg"], 0))'
expect "south rows are kept apart from north: 1 drive each" \
    'any(dir(r["heading_deg"], 180) for r in rows) and all(r["drives"] == 1 for r in rows if dir(r["heading_deg"], 180))'
req POST /rest/v1/rpc/road_quality_in_area "" "{$BOX,\"include_fake\":true,\"vehicle_id\":\"truck\"}"
expect "vehicle_id filter: truck sees none of the camry rows" 'rows == []'

echo "${BOLD}3. events_in_area${RESET}"
req POST /rest/v1/rpc/events_in_area "" "{$BOX}"
expect "fake data is hidden by default" 'rows == []'
req POST /rest/v1/rpc/events_in_area "" "{$BOX,\"include_fake\":true}"
expect "3 spots: pothole north, pothole south, speed bump" 'len(rows) == 3'
expect "north pothole: A and B merged (UNKNOWN label = no change), 2 hits of 3 passes, max_peak_z 2.5" \
    'any(r["event_class"] == "POTHOLE" and dir(r["heading_deg"], 0) and r["hits"] == 2 and r["passes"] == 3 and abs(r["max_peak_z"] - 2.5) < 0.01 for r in rows)'
expect "south pothole is separate: 1 hit of 1 pass" \
    'any(r["event_class"] == "POTHOLE" and dir(r["heading_deg"], 180) and r["hits"] == 1 and r["passes"] == 1 for r in rows)'
expect "SPEED_BUMP label overrides POTHOLE: 1 hit of 3 passes" \
    'any(r["event_class"] == "SPEED_BUMP" and r["hits"] == 1 and r["passes"] == 3 for r in rows)'
req POST /rest/v1/rpc/events_in_area "" "{$BOX,\"include_fake\":true,\"vehicle_id\":\"camry\"}"
expect "vehicle_id filter: camry still sees all 3" 'len(rows) == 3'

echo "${BOLD}4. Cleanup${RESET}"
code=$(curl -s -m 5 -o /dev/null -w '%{http_code}' -X DELETE "$API/rest/v1/trips?id=in.($A,$B,$C,$D)" -H "apikey: $SECRET")
[[ $code == 2* ]] && pass "test trips deleted with the secret key" || fail "couldn't delete test trips ($code); delete trips $A $B $C $D in Studio"
curl -s -m 5 -o /dev/null -X DELETE "$API/auth/v1/admin/users/$USER_ID" -H "apikey: $SECRET"

echo
if (( failures == 0 )); then echo "${GREEN}${BOLD}Map functions work as the contract says.${RESET}"
else echo "${RED}${BOLD}$failures check(s) failed.${RESET} Send this output (it contains no keys)."; fi
