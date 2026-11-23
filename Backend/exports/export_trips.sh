#!/usr/bin/env bash
# export_trips.sh - writes the trips, segments and events of trips that started in a date range
# (Pacific time) to CSV files, for offline analysis. Read-only; prints no keys.
#
# Run on the laptop with Supabase running:
#     cd ~/ee475aut26team5/Backend && bash exports/export_trips.sh 2026-11-22     (one day)
#     bash exports/export_trips.sh 2026-11-20 2026-11-23     (end date exclusive)
# Output: exports/out/<from>_<to>_{trips,segments,events}.csv  (gitignored: holds GPS traces)
#
# Times stay epoch ms (as stored), plus a readable Pacific copy. scale_factor is the vehicle's
# current value; rms_z is raw (not multiplied by it). All sources are included (see the
# source column); add REAL-only filtering in your analysis if needed.

set -eu
cd "$(dirname "$0")/.."   # run from Backend/

RED=$'\e[31m'; RESET=$'\e[0m'
die() { echo "${RED}$1${RESET}"; exit 1; }

from=${1:-}
[[ $from =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || die "Usage: bash exports/export_trips.sh YYYY-MM-DD [YYYY-MM-DD]"
to=${2:-$(date -d "$from + 1 day" +%F)}
[[ $to =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || die "End date must be YYYY-MM-DD"

status_env=$(supabase status -o env 2>/dev/null) || die "Supabase isn't running. Run: supabase start"
DB_URL=$(printf '%s\n' "$status_env" | grep -E '^DB_URL=' | head -1 | cut -d= -f2- | tr -d '"' | sed 's/[[:space:]]*$//')
[[ -n $DB_URL ]] || die "Couldn't read DB_URL from supabase status."

mkdir -p exports/out
base=exports/out/${from}_${to}

# Trips whose start_time falls in [from, to) Pacific.
trip_filter="t.start_time >= extract(epoch from timestamptz '$from 00:00 America/Los_Angeles') * 1000 and t.start_time < extract(epoch from timestamptz '$to 00:00 America/Los_Angeles') * 1000"
pt() { echo "to_char($1 at time zone 'America/Los_Angeles', 'YYYY-MM-DD HH24:MI:SS.MS')"; }

trips_q="select t.id as trip_id, t.vehicle_id, v.scale_factor, t.source, t.start_time, t.end_time, $(pt t.start_time_ts) as start_pacific, $(pt t.end_time_ts) as end_pacific, t.fw_version, t.app_version, (select count(*) from public.segments s where s.trip_id = t.id) as n_segments, (select count(*) from public.events e where e.trip_id = t.id) as n_events from public.trips t join public.vehicles v on v.id = t.vehicle_id where $trip_filter order by t.start_time"

segments_q="select s.trip_id, t.vehicle_id, v.scale_factor, s.source, t.fw_version, s.t_start, s.t_end, $(pt s.t_start_ts) as t_start_pacific, s.lat, s.lon, s.speed_mps, s.gps_accuracy_m, s.heading_deg, s.rms_z, s.peak_z, s.roll_rms, s.pitch_rms from public.segments s join public.trips t on t.id = s.trip_id join public.vehicles v on v.id = t.vehicle_id where $trip_filter order by t.start_time, s.trip_id, s.t_start"

events_q="select e.id as event_id, e.trip_id, e.vehicle_id, v.scale_factor, e.source, e.t, $(pt e.t_ts) as t_pacific, e.lat, e.lon, e.speed_mps, e.gps_accuracy_m, e.heading_deg, e.event_class, e.user_label, e.peak_z, e.roll_peak, e.pitch_peak, e.conf from public.events e join public.trips t on t.id = e.trip_id join public.vehicles v on v.id = t.vehicle_id where $trip_filter order by t.start_time, e.trip_id, e.t"

for part in trips segments events; do
    q_var=${part}_q
    psql "$DB_URL" -qX -v ON_ERROR_STOP=1 -c "\\copy (${!q_var}) to '${base}_$part.csv' with (format csv, header)" \
        || die "Export of $part failed."
done

# psql's \copy writes relative to the current dir, so the names above resolve from Backend/.
for part in trips segments events; do
    f=${base}_$part.csv
    if [[ -f $f ]]; then printf '  %-9s %6d rows  %s\n' "$part" $(( $(wc -l < "$f") - 1 )) "$PWD/$f"; fi
done
echo
echo "Trips started $from to $to (Pacific, end exclusive):"
psql "$DB_URL" -qX -c "select t.vehicle_id, t.source, $(pt t.start_time_ts) as start_pacific, round((t.end_time - t.start_time) / 60000.0, 1) as minutes, (select count(*) from public.segments s where s.trip_id = t.id) as segments from public.trips t where $trip_filter order by t.start_time"
