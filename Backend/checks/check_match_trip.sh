#!/usr/bin/env bash
# check_match_trip.sh - match one stored trip with Valhalla and print how far its points moved.
# Stores nothing. Use it to see how well matching works on real drives before trusting the map.
#
# Run on the laptop after ROAD_MATCHING_SETUP.txt step 6:
#     cd ~/ee475aut26team5/Backend && bash checks/check_match_trip.sh            # newest trip
#     cd ~/ee475aut26team5/Backend && bash checks/check_match_trip.sh list       # recent trips
#     cd ~/ee475aut26team5/Backend && bash checks/check_match_trip.sh <trip id>  # that trip

set -u
cd "$(dirname "$0")/.."   # run from Backend/

RED=$'\e[31m'; RESET=$'\e[0m'
die() { echo "${RED}$1${RESET}"; exit 1; }

[[ -f matcher/matcher.env && -x matcher/.venv/bin/python ]] || die "Install the matcher first: bash matcher/install_matcher.sh"
status_env=$(supabase status -o env 2>/dev/null) || die "Supabase isn't running. Run: supabase start"
DB_URL=$(printf '%s\n' "$status_env" | grep -E '^DB_URL=' | head -1 | cut -d= -f2- | tr -d '"' | sed 's/[[:space:]]*$//')
q() { psql "$DB_URL" -qAtX -c "$1" 2>/dev/null; }

recent="select t.id, t.source, t.vehicle_id, t.start_time_ts::timestamp(0), count(s.*) as located
        from public.trips t join public.segments s on s.trip_id = t.id and s.lat is not null
        group by t.id having count(s.*) >= 10 order by t.start_time desc"

case ${1:-} in
    list)
        psql "$DB_URL" -qX -c "$recent limit 15"
        exit 0 ;;
    "")
        TRIP=$(q "select id from ($recent limit 1) x")
        [[ -n $TRIP ]] || die "No trip with 10+ located segments yet." ;;
    *)
        TRIP=$1 ;;
esac

set -a; source matcher/matcher.env; set +a
matcher/.venv/bin/python matcher/road_matcher.py --dry-run "$TRIP"
