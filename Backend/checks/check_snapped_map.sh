#!/usr/bin/env bash
# check_snapped_map.sh - end-to-end check of road matching and road_quality_snapped (contract §7).
# Picks a quiet two-way road in eastern Washington with no stored drives near it, uploads five
# FAKE_DEVICE test drives as the app would (four on that road, one in the middle of the Pacific),
# checks they show as CELL rows before matching, ends the trips so the road-matcher service
# matches them, then checks the ROAD rows. Roughness is speed-adjusted to 25 mph (§8), and the
# box split into four tiles must return every row exactly once. Deletes everything with the
# secret key at the end.
# Prints PASS/FAIL only; never prints a key.
#
# Run on the laptop with Supabase, Valhalla and the road-matcher service running:
#     cd ~/ee475aut26team5/Backend && bash checks/check_snapped_map.sh

set -u
cd "$(dirname "$0")/.."   # run from Backend/

API=http://127.0.0.1:54321
GREEN=$'\e[32m'; RED=$'\e[31m'; BOLD=$'\e[1m'; RESET=$'\e[0m'
failures=0
pass() { echo "  ${GREEN}PASS${RESET}  $1"; }
fail() { echo "  ${RED}FAIL${RESET}  $1"; failures=$((failures + 1)); }
die()  { echo "${RED}$1${RESET}"; exit 1; }

status_env=$(supabase status -o env 2>/dev/null) || die "Supabase isn't running. Run: supabase start"
env_value() { printf '%s\n' "$status_env" | grep -E "^$1=" | head -1 | cut -d= -f2- | tr -d '"' | sed 's/[[:space:]]*$//'; }
PUB=$(env_value PUBLISHABLE_KEY); [[ -z $PUB ]] && PUB=$(env_value ANON_KEY)
SECRET=$(env_value SECRET_KEY)
DB_URL=$(env_value DB_URL)
q() { psql "$DB_URL" -qAtX -c "$1" 2>/dev/null; }

systemctl is-active --quiet road-matcher || die "The road-matcher service isn't running (bash checks/check_matcher.sh)."
[[ $(q "select count(*) > 0 from roads.ways") == t ]] || die "No roads loaded (bash roads/import_roads.sh)."

# req METHOD PATH TOKEN [BODY] -> sets CODE and BODY. TOKEN "" = not signed in.
req() {
    local headers=(-H "apikey: $PUB" -H "Content-Type: application/json")
    [[ -n $3 ]] && headers+=(-H "Authorization: Bearer $3")
    [[ -n ${4:-} ]] && headers+=(-d "$4")
    local out; out=$(curl -s -m 20 -w $'\n%{http_code}' -X "$1" "$API$2" "${headers[@]}")
    CODE=${out##*$'\n'}; BODY=${out%$'\n'*}
}

# expect DESCRIPTION PYTHON_EXPRESSION: checks the rows in BODY. In the expression: `rows`,
# `road` / `cell` (rows of that kind), `fwd(rs)` / `back(rs)` (rows heading along / against the
# test road, within 30°), H (test road heading), NOW (epoch ms), `adj(rms_z, speed_mps)` (the
# §8 speed adjustment; speed None = no GPS fix).
expect() {
    local result
    result=$(printf '%s' "$BODY" | H=$H NOW=$NOW python3 -c '
import json, math, os, sys
H, NOW = float(os.environ["H"]), int(os.environ["NOW"])
def adj(r, v):
    road = math.sqrt(max(r * r - 0.015 ** 2, 0))
    return road if v is None else road * (11.176 / max(v, 4.4704)) ** 0.56
def near(h, d): return h is not None and min(abs(h - d) % 360, 360 - abs(h - d) % 360) <= 30
def fwd(rs):  return [r for r in rs if near(r["heading_deg"], H)]
def back(rs): return [r for r in rs if near(r["heading_deg"], (H + 180) % 360)]
rows = json.load(sys.stdin)
road = [r for r in rows if r["kind"] == "ROAD"]
cell = [r for r in rows if r["kind"] == "CELL"]
print("yes" if eval(sys.argv[1]) else "no")' "$2" 2>/dev/null)
    [[ $result == yes ]] && pass "$1" || fail "$1 (got $CODE: ${BODY:0:300})"
}

sign_in() {
    curl -s -m 5 -X POST "$API/auth/v1/signup" -H "apikey: $PUB" \
         -H "Content-Type: application/json" -d '{}' | python3 -c '
import json, sys
r = json.load(sys.stdin)
print(r.get("access_token", ""), (r.get("user") or {}).get("id", ""))' 2>/dev/null
}
uuid() { cat /proc/sys/kernel/random/uuid; }

# tiles DESCRIPTION EXTRA_JSON: calls road_quality_snapped for the whole box and for the box cut
# into four tiles at the middle of the test drive; every row must come back from exactly one tile.
ids() { printf '%s' "$BODY" | python3 -c 'import json, sys; print("\n".join(r["piece_id"] for r in json.load(sys.stdin)))' 2>/dev/null; }
tiles() {
    local full parts="" t nonempty=0
    req POST /rest/v1/rpc/road_quality_snapped "" "{$BOX,$2}"
    full=$(ids | sort)
    for t in "\"south\":$S,\"west\":$W,\"north\":$MLAT,\"east\":$MLON" \
             "\"south\":$S,\"west\":$MLON,\"north\":$MLAT,\"east\":$E" \
             "\"south\":$MLAT,\"west\":$W,\"north\":$N,\"east\":$MLON" \
             "\"south\":$MLAT,\"west\":$MLON,\"north\":$N,\"east\":$E"; do
        req POST /rest/v1/rpc/road_quality_snapped "" "{$t,$2}"
        [[ $CODE == 2* ]] || { fail "$1 (tile call got $CODE: ${BODY:0:300})"; return; }
        [[ $BODY != "[]" ]] && nonempty=$((nonempty + 1))
        parts+=$(ids)$'\n'
    done
    parts=$(printf '%s' "$parts" | sed '/^$/d' | sort)
    if [[ -n $full && $full == "$parts" && $nonempty -ge 2 ]]; then
        pass "$1 ($(wc -l <<<"$full") rows, from $nonempty tiles)"
    else
        fail "$1 (whole box $(wc -l <<<"$full") rows; tiles $(printf '%s' "$parts" | grep -c .) rows, $(printf '%s' "$parts" | sort | uniq -d | grep -c .) repeated; $nonempty non-empty tiles)"
    fi
}

echo "${BOLD}1. Pick a test road${RESET}"
# Straight, two-way, 400 m to 3 km, in eastern Washington, no stored segment within 3 km.
WAY=$(q "with c as (
            select w.way_id, w.geom from roads.ways w
            where extensions.st_intersects(w.geom, extensions.st_transform(
                      extensions.st_makeenvelope(-119.5, 46.5, -117.5, 47.5, 4326), 32610))
              and w.highway in ('residential', 'unclassified', 'tertiary') and not w.oneway
              and w.len_m between 400 and 3000
              and w.len_m < 1.02 * extensions.st_distance(extensions.st_startpoint(w.geom), extensions.st_endpoint(w.geom))
            order by w.way_id limit 300)
         select c.way_id from c
         where not exists (select 1 from public.segments s where extensions.st_dwithin(s.geog,
                   extensions.st_transform(extensions.st_lineinterpolatepoint(c.geom, 0.5), 4326)::extensions.geography, 3000))
         limit 1")
[[ -n $WAY ]] || die "Couldn't find a quiet test road in eastern Washington."
# 15 points 10 m apart, starting 100 m along the way: "lat lon heading" per line.
mapfile -t PTS < <(q "select round(extensions.st_y(a)::numeric, 7) || ' ' || round(extensions.st_x(a)::numeric, 7) || ' ' ||
                             (round(degrees(extensions.st_azimuth(a::extensions.geography, b::extensions.geography))::numeric, 1) % 360)
                      from roads.ways w,
                           generate_series(0, 14) i,
                           lateral (select extensions.st_transform(extensions.st_lineinterpolatepoint(w.geom, (100 + i * 10) / w.len_m), 4326) as a,
                                           extensions.st_transform(extensions.st_lineinterpolatepoint(w.geom, (101 + i * 10) / w.len_m), 4326) as b) p
                      where w.way_id = $WAY order by i")
(( ${#PTS[@]} == 15 )) || die "Couldn't sample points on way $WAY."
read -r LANES_FWD LANES_BWD <<<"$(q "select lanes_fwd || ' ' || lanes_bwd from roads.ways where way_id = $WAY")"
read -r LAT0 LON0 H <<<"${PTS[0]}"
read -r LAT1 LON1 _ <<<"${PTS[14]}"
pass "OSM way $WAY, heading ${H}°"
S=$(awk "BEGIN{a=$LAT0; b=$LAT1; print (a<b?a:b) - 0.0005}"); N=$(awk "BEGIN{a=$LAT0; b=$LAT1; print (a>b?a:b) + 0.0005}")
W=$(awk "BEGIN{a=$LON0; b=$LON1; print (a<b?a:b) - 0.0005}"); E=$(awk "BEGIN{a=$LON0; b=$LON1; print (a>b?a:b) + 0.0005}")
BOX="\"south\":$S,\"west\":$W,\"north\":$N,\"east\":$E"
read -r MLAT MLON _ <<<"${PTS[7]}"   # tile corner: the middle of the test drive
PAC_BOX='"south":9.999,"west":-150.001,"north":10.002,"east":-149.999'

NOW=$(date +%s%3N)
OLD=$((NOW - 28 * 86400000))   # 28 days = two half-lives: counts a quarter
read -r PHONE USER_ID <<<"$(sign_in)"
[[ -z $PHONE ]] && die "Anonymous sign-in failed."

# A, B: along the road 28 days ago at 10 m/s, rms_z 0.1, 0.2. D: along the road today, 0.9 at
# 2 m/s (below 10 mph, so counted as 10 mph). Weighted median = D (weights 0.25, 0.25, 1); the
# plain median would be B. C: against the road today, 0.5, no speed (no GPS fix: only the noise
# floor is removed). P: middle of the Pacific (no road), 0.3 at 10 m/s.
A=$(uuid); B=$(uuid); C=$(uuid); D=$(uuid); P=$(uuid)
trip() {      # $1 = id, $2 = start time
    echo "{\"id\":\"$1\",\"vehicle_id\":\"camry\",\"start_time\":$2,\"source\":\"FAKE_DEVICE\"}"
}
segments() {  # $1 = trip id, $2 = start time, $3 = rms_z, $4 = fwd | back | pacific, $5 = speed_mps or null
    local rows=() i lat lon h
    for i in $(seq 0 14); do
        case $4 in
            fwd)     read -r lat lon h <<<"${PTS[$i]}" ;;
            back)    read -r lat lon h <<<"${PTS[$((14 - i))]}"; h=$(awk "BEGIN{print ($h + 180) % 360}") ;;
            pacific) lat=$(awk "BEGIN{printf \"%.7f\", 10 + $i * 10 / 111195}"); lon=-150.0; h=0 ;;
        esac
        rows+=("{\"trip_id\":\"$1\",\"t_start\":$(($2 + i * 1000)),\"t_end\":$(($2 + i * 1000 + 1000)),\"lat\":$lat,\"lon\":$lon,\"speed_mps\":$5,\"gps_accuracy_m\":4,\"heading_deg\":$h,\"rms_z\":$3,\"peak_z\":0.5,\"roll_rms\":1,\"pitch_rms\":1,\"source\":\"FAKE_DEVICE\"}")
    done
    local IFS=,; echo "[${rows[*]}]"
}

echo "${BOLD}2. Speed adjustment formula (roads.rough_25mph)${RESET}"
adj_sql=$(q "select round(roads.rough_25mph(0.2, 11.176)::numeric, 4) || ' ' || round(roads.rough_25mph(0.2, null)::numeric, 4) || ' ' ||
                    round(roads.rough_25mph(0.2, 0)::numeric, 4) || ' ' || round(roads.rough_25mph(0.2, 22.352)::numeric, 4) || ' ' ||
                    round(roads.rough_25mph(0.01, 10)::numeric, 4)")
[[ $adj_sql == "0.1994 0.1994 0.3332 0.1353 0.0000" ]] \
    && pass "0.2 g at 25 mph / no speed / stopped (= 10 mph) / 50 mph, and below the noise floor: $adj_sql" \
    || fail "expected 0.1994 0.1994 0.3332 0.1353 0.0000, got '$adj_sql'"

echo "${BOLD}3. Upload test drives (not ended yet)${RESET}"
ok=1
for t in "$A $OLD 0.1 fwd 10" "$B $OLD 0.2 fwd 10" "$D $NOW 0.9 fwd 2" "$C $NOW 0.5 back null" "$P $NOW 0.3 pacific 10"; do
    set -- $t
    req POST /rest/v1/trips "$PHONE" "$(trip "$1" "$2")";            [[ $CODE == 2* ]] || ok=0
    req POST /rest/v1/segments "$PHONE" "$(segments "$@")";          [[ $CODE == 2* ]] || ok=0
done
(( ok )) && pass "5 trips, 75 segments uploaded" || fail "upload failed (last: $CODE: ${BODY:0:300})"

req POST /rest/v1/rpc/road_quality_snapped "" "{$BOX,\"include_fake\":true}"
expect "before matching: shown with the cell method (CELL rows only)" 'len(cell) > 0 and road == []'
# Each drive's first and last segment reaches speed x 1 s / 2 past its point: 5 m for A and B,
# only 1 m for D (2 m/s), so the cells at the two ends of the drive have only A and B in them.
expect "before matching: cells use the speed-adjusted, age-weighted median (D: 0.9 at 10 mph, about 1.50)" \
    '[r for r in fwd(cell) if r["drives"] == 3] and all(abs(r["roughness"] - adj(0.9, 2)) < 0.01 for r in fwd(cell) if r["drives"] == 3)'
expect "before matching: end cells only A and B reach (D is slower) have 2 drives, median A (about 0.105)" \
    'sum(r["drives"] != 3 for r in fwd(cell)) <= 4 and all(r["drives"] == 2 and abs(r["roughness"] - adj(0.1, 10)) < 0.01 for r in fwd(cell) if r["drives"] != 3)'
tiles "before matching: the box cut into 4 tiles returns every CELL row exactly once" '"include_fake":true'

echo "${BOLD}4. End the trips; the matcher should match them within a minute${RESET}"
ok=1
for t in "$A $OLD" "$B $OLD" "$D $NOW" "$C $NOW" "$P $NOW"; do
    set -- $t
    req PATCH "/rest/v1/trips?id=eq.$1" "$PHONE" "{\"end_time\":$(($2 + 15000))}"
    [[ $CODE == 2* ]] || { ok=0; patch_err="$CODE: ${BODY:0:300}"; }
done
IDS="'$A','$B','$C','$D','$P'"
(( ok )) && pass "app set end_time on all 5 trips" || fail "setting end_time failed ($patch_err)"
ended=$(q "select count(*) from public.trips where id in ($IDS) and end_time is not null")
[[ $ended == 5 ]] && pass "all 5 trips have end_time in the database" || fail "only ${ended:-?} of 5 trips have end_time"
for _ in $(seq 1 60); do
    [[ $(q "select count(*) from roads.trip_matches where trip_id in ($IDS)") == 5 ]] && break
    sleep 2
done
[[ $(q "select count(*) from roads.trip_matches where trip_id in ($IDS)") == 5 ]] \
    && pass "all 5 trips matched within 2 minutes of ending" \
    || { fail "not all trips were matched; matcher log for the last 3 minutes:"
         journalctl -u road-matcher --since "-3 min" --no-pager -o cat 2>/dev/null | tail -n 20 | sed 's/^/        /'; }
[[ $(q "select count(*) from roads.trip_matches where trip_id in ('$A','$B','$C','$D') and status = 'MATCHED'") == 4 ]] \
    && pass "the 4 drives on the road: MATCHED" || fail "the 4 road drives weren't all MATCHED"
[[ $(q "select status from roads.trip_matches where trip_id = '$P'") == NO_MATCH ]] \
    && pass "the Pacific drive: NO_MATCH" || fail "the Pacific drive should be NO_MATCH"
[[ $(q "select count(distinct way_id) = 1 and bool_and(way_id = $WAY) from roads.segment_matches where trip_id in ('$A','$B','$C','$D')") == t ]] \
    && pass "every matched segment is on way $WAY" \
    || fail "segments matched to other ways: $(q "select string_agg(distinct way_id::text, ' ') from roads.segment_matches where trip_id in ('$A','$B','$C','$D')")"

echo "${BOLD}5. road_quality_snapped${RESET}"
req POST /rest/v1/rpc/road_quality_snapped "" "{$BOX}"
expect "fake data is hidden by default" 'rows == []'
req POST /rest/v1/rpc/road_quality_snapped "" "{$BOX,\"include_fake\":true}"
expect "the web map (not signed in) can call it" 'len(rows) > 0'
expect "after matching: all ROAD rows, no CELL rows left" 'len(road) > 0 and cell == []'
expect "ROAD rows are on way $WAY, two-way, with a path of 2+ points" \
    'all(r["piece_id"].startswith("R:'"$WAY"':") and r["oneway"] is False and len(r["path"]) >= 2 for r in road)'
expect "along the road: 3 drives, speed-adjusted age-weighted median (D, about 1.50), weight about 1.5" \
    'fwd(road) and all(r["drives"] == 3 and abs(r["roughness"] - adj(0.9, 2)) < 0.01 and abs(r["weight"] - 1.5) < 0.05 for r in fwd(road))'
expect "against the road: kept apart, 1 drive, no speed so only the noise floor removed (about 0.50)" \
    'back(road) and all(r["drives"] == 1 and abs(r["roughness"] - adj(0.5, None)) < 0.01 for r in back(road))'
expect "every row is one direction or the other" 'len(fwd(road)) + len(back(road)) == len(road)'
expect "along the road: pieces cover the 150 m driven (140-170 m of line)" \
    '140 <= sum(r["length_m"] for r in fwd(road)) <= 170'
expect "lanes_dir is the way's lanes in each direction ($LANES_FWD along, $LANES_BWD against)" \
    'all(r["lanes_dir"] == '"${LANES_FWD:-0}"' for r in fwd(road)) and all(r["lanes_dir"] == '"${LANES_BWD:-0}"' for r in back(road))'
expect "last_t is the newest drive (today)" 'all(r["last_t"] >= NOW - 60000 for r in fwd(road))'
req POST /rest/v1/rpc/road_quality_snapped "" "{$BOX,\"include_fake\":true,\"piece_m\":60}"
expect "piece_m 60: fewer, longer pieces along the road (2-4, longest over 15 m)" \
    '2 <= len(fwd(road)) <= 4 and max(r["length_m"] for r in fwd(road)) > 15'
req POST /rest/v1/rpc/road_quality_snapped "" "{$BOX,\"include_fake\":true,\"vehicle_id\":\"truck\"}"
expect "vehicle_id filter: truck sees none of the camry rows" 'rows == []'
req POST /rest/v1/rpc/road_quality_snapped "" "{$PAC_BOX,\"include_fake\":true}"
expect "Pacific drive (no road): still shown, as CELL rows" 'len(cell) > 0 and road == [] and all(r["drives"] == 1 and r["lanes_dir"] is None for r in cell)'

echo "${BOLD}6. Tiles: every row from exactly one tile${RESET}"
tiles "ROAD rows, piece_m 15: the box cut into 4 tiles returns every row exactly once" '"include_fake":true'
tiles "ROAD rows, piece_m 60: the same" '"include_fake":true,"piece_m":60'

echo "${BOLD}7. Time filter (since_ms / until_ms, by trip start_time)${RESET}"
CUT=$((NOW - 86400000))   # a day ago: A, B (28 days old) before it; C, D, P after
req POST /rest/v1/rpc/road_quality_snapped "" "{$BOX,\"include_fake\":true,\"since_ms\":$CUT}"
expect "since a day ago, along the road: only D (1 drive, about 1.50)" \
    'fwd(road) and all(r["drives"] == 1 and abs(r["roughness"] - adj(0.9, 2)) < 0.01 for r in fwd(road))'
expect "since a day ago, against the road: C still there" 'len(back(road)) > 0'
req POST /rest/v1/rpc/road_quality_snapped "" "{$BOX,\"include_fake\":true,\"until_ms\":$CUT}"
expect "until a day ago: only A and B along the road (2 drives, median A: 0.1 at 10 m/s, about 0.105), nothing against it" \
    'fwd(road) and back(road) == [] and all(r["drives"] == 2 and abs(r["roughness"] - adj(0.1, 10)) < 0.01 for r in fwd(road))'
req POST /rest/v1/rpc/road_quality_snapped "" "{$BOX,\"include_fake\":true,\"since_ms\":$((NOW + 3600000))}"
expect "since a time after every drive: nothing" 'rows == []'
req POST /rest/v1/rpc/road_quality_snapped "" "{$PAC_BOX,\"include_fake\":true,\"since_ms\":$CUT}"
expect "CELL rows follow the filter too: Pacific drive shown since a day ago" 'len(cell) > 0'
req POST /rest/v1/rpc/road_quality_snapped "" "{$PAC_BOX,\"include_fake\":true,\"until_ms\":$CUT}"
expect "CELL rows follow the filter too: Pacific drive hidden until a day ago" 'rows == []'

echo "${BOLD}8. Cleanup${RESET}"
code=$(curl -s -m 5 -o /dev/null -w '%{http_code}' -X DELETE "$API/rest/v1/trips?id=in.($A,$B,$C,$D,$P)" -H "apikey: $SECRET")
[[ $code == 2* ]] && pass "test trips deleted with the secret key" || fail "couldn't delete test trips ($code); delete trips $A $B $C $D $P in Studio"
[[ $(q "select count(*) from roads.drive_pieces where trip_id in ($IDS)") == 0 ]] \
    && pass "their road matches went with them" || fail "road matches left behind"
curl -s -m 5 -o /dev/null -X DELETE "$API/auth/v1/admin/users/$USER_ID" -H "apikey: $SECRET"

echo
if (( failures == 0 )); then echo "${GREEN}${BOLD}Road matching and road_quality_snapped work as the contract says.${RESET}"
else echo "${RED}${BOLD}$failures check(s) failed.${RESET} Send this output (it contains no keys)."; fi
