#!/usr/bin/env bash
# check_access.sh - do the access rules (RLS, contract §7 "Who can do what") work?
# Acts as the web map (not signed in), the app (signed in) and a second phone, and checks
# what each one can and can't do. Creates a FAKE_DEVICE test trip and two anonymous test
# users, then deletes them with the secret key. Prints PASS/FAIL only; never prints a key.
#
# Run on the laptop, with Supabase started and the migrations loaded:
#     cd ~/ee475aut26team5/Backend && bash checks/check_access.sh

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

# req METHOD PATH TOKEN [BODY] [PREFER] -> sets CODE and BODY. TOKEN "" = not signed in.
req() {
    local headers=(-H "apikey: $PUB" -H "Content-Type: application/json"
                   -H "Prefer: ${5:-return=representation}")
    [[ -n $3 ]] && headers+=(-H "Authorization: Bearer $3")
    [[ -n ${4:-} ]] && headers+=(-d "$4")
    local out; out=$(curl -s -m 5 -w $'\n%{http_code}' -X "$1" "$API$2" "${headers[@]}")
    CODE=${out##*$'\n'}; BODY=${out%$'\n'*}
}
allowed() { [[ $CODE == 2* ]] && pass "$1" || fail "$1 (got $CODE)"; }
refused() { [[ $CODE != 2* ]] && pass "$1 ($CODE)" || fail "$1 (was allowed: $CODE)"; }
# An update that RLS filters out "succeeds" with no rows changed, so check the returned rows.
changed()   { [[ $CODE == 2* && $BODY == *"$TRIP"* ]] && pass "$1" || fail "$1 (got $CODE)"; }
unchanged() { [[ $CODE != 2* || $BODY == "[]" ]] && pass "$1" || fail "$1 (a row was changed)"; }

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
TRIP=$(uuid); EVENT=$(uuid)
read -r PHONE_A USER_A <<<"$(sign_in)"
read -r PHONE_B USER_B <<<"$(sign_in)"
if [[ -z $PHONE_A || -z $PHONE_B ]]; then
    echo "${RED}Anonymous sign-in failed.${RESET} Check enable_anonymous_sign_ins = true and restart."; exit 1
fi

segment() {   # $1 = trip id
    echo "{\"trip_id\":\"$1\",\"t_start\":$NOW,\"t_end\":$((NOW + 1000)),\"rms_z\":0.1,\"peak_z\":0.2,\"roll_rms\":1,\"pitch_rms\":1,\"source\":\"FAKE_DEVICE\"}"
}
event() {     # $1 = vehicle, $2 = class
    echo "{\"id\":\"$EVENT\",\"trip_id\":\"$TRIP\",\"vehicle_id\":\"$1\",\"t\":$NOW,\"event_class\":\"$2\",\"peak_z\":1.9,\"roll_peak\":45,\"pitch_peak\":12,\"conf\":0.7,\"source\":\"FAKE_DEVICE\"}"
}

echo "${BOLD}1. Web map (not signed in): read only${RESET}"
req GET "/rest/v1/vehicles?select=id" "";                                  allowed "can read vehicles"
req POST /rest/v1/trips "" "{\"id\":\"$(uuid)\",\"vehicle_id\":\"camry\",\"start_time\":$NOW,\"source\":\"FAKE_DEVICE\"}"
refused "can't add a trip"

echo "${BOLD}2. App (signed in): adding data${RESET}"
req POST /rest/v1/trips "$PHONE_A" "{\"id\":\"$TRIP\",\"vehicle_id\":\"camry\",\"start_time\":$NOW,\"source\":\"FAKE_DEVICE\"}"
allowed "can add its own trip"
req POST /rest/v1/segments "$PHONE_A" "$(segment "$TRIP")";                allowed "can add a segment to its trip"
req POST /rest/v1/segments "$PHONE_A" "$(segment "$TRIP")" "return=minimal,resolution=ignore-duplicates"
allowed "re-sending the same segment is skipped without an error"
req POST /rest/v1/events "$PHONE_A" "$(event camry pothole)";              refused "lowercase event_class is rejected"
req POST /rest/v1/events "$PHONE_A" "$(event truck POTHOLE)";              refused "an event with a different vehicle than its trip is rejected"
req POST /rest/v1/events "$PHONE_A" "$(event camry POTHOLE)";              allowed "can add a valid event"
req POST /rest/v1/vehicles "$PHONE_A" '{"id":"test","name":"Test","type":"sedan"}'
refused "can't add a vehicle"

echo "${BOLD}3. App (signed in): changing data${RESET}"
req PATCH "/rest/v1/trips?id=eq.$TRIP" "$PHONE_A" "{\"end_time\":$((NOW + 5000))}"
changed "can set end_time on its own trip"
req PATCH "/rest/v1/trips?id=eq.$TRIP" "$PHONE_A" "{\"start_time\":1}"
refused "can't change any other trip column"
req PATCH "/rest/v1/events?id=eq.$EVENT" "$PHONE_A" '{"user_label":"SPEED_BUMP"}'
[[ $CODE == 2* && $BODY == *"$EVENT"* ]] && pass "can set user_label on its own event" \
    || fail "can set user_label on its own event (got $CODE)"
req DELETE "/rest/v1/trips?id=eq.$TRIP" "$PHONE_A"
[[ $CODE != 2* || $BODY == "[]" ]] && pass "can't delete its own trip" || fail "can't delete its own trip (it was deleted)"

echo "${BOLD}4. A different phone${RESET}"
req POST /rest/v1/segments "$PHONE_B" "$(segment "$TRIP" | sed "s/$NOW,/$((NOW + 1000)),/")"
refused "can't add segments to someone else's trip"
req PATCH "/rest/v1/trips?id=eq.$TRIP" "$PHONE_B" "{\"end_time\":$((NOW + 9000))}"
unchanged "can't end someone else's trip"
req GET "/rest/v1/trips?id=eq.$TRIP&select=id" "$PHONE_B"
[[ $BODY == *"$TRIP"* ]] && pass "can still read it (map data is shared)" || fail "can't read other trips (got $CODE)"

echo "${BOLD}5. Cleanup${RESET}"
code=$(curl -s -m 5 -o /dev/null -w '%{http_code}' -X DELETE "$API/rest/v1/trips?id=eq.$TRIP" -H "apikey: $SECRET")
[[ $code == 2* ]] && pass "test trip deleted with the secret key" || fail "couldn't delete test trip $TRIP ($code); delete it in Studio"
for user in "$USER_A" "$USER_B"; do
    curl -s -m 5 -o /dev/null -X DELETE "$API/auth/v1/admin/users/$user" -H "apikey: $SECRET"
done

echo
if (( failures == 0 )); then echo "${GREEN}${BOLD}All access rules work as the contract says.${RESET}"
else echo "${RED}${BOLD}$failures check(s) failed.${RESET} Send this output (it contains no keys)."; fi
