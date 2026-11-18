#!/usr/bin/env python3
"""Road matcher: puts each drive on OpenStreetMap roads (Backend/ROAD_MATCHING_SETUP.txt).

Runs on the laptop as the systemd service road-matcher. For each finished trip it sends the
trip's located segments to the local Valhalla (/trace_attributes), which says which OSM way
each segment was on, and stores that with roads.save_trip_match(). The database then works out
direction and 15 m pieces, which the map function road_quality_snapped draws.

When it runs:
  - right away when the app sets a trip's end_time (database trigger -> NOTIFY road_matcher)
  - every 30 minutes, a catch-up for trips that never got an end_time, got more segments after
    they were matched, or failed because Valhalla or the database was down.

It logs in as the road_matcher role, which can only call roads.trips_to_match,
roads.trip_points and roads.save_trip_match. It talks only to 127.0.0.1. It never prints the
database URL or password.

Settings (environment, from matcher.env, written by install_matcher.sh):
  MATCHER_DB_URL   postgresql://road_matcher:...@127.0.0.1:54322/postgres  (required)
  VALHALLA_URL     default http://127.0.0.1:8002
  SEARCH_RADIUS_M  how far from a GPS point Valhalla looks for a road (default 30)
  GPS_ACCURACY_M   typical GPS error Valhalla assumes (default 8)
  MAX_OFF_M        points moved further than this are left unmatched (default 40)

Usage:
  road_matcher.py                 run as the service
  road_matcher.py --once          match whatever is waiting, then exit
  road_matcher.py --dry-run TRIP  match one trip and print how far points moved; stores nothing
"""

import argparse
import json
import logging
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter

import psycopg
from psycopg.types.json import Jsonb

CATCH_UP_EVERY_S = 30 * 60
RECONNECT_WAIT_S = 30
SETTLE_S = 3                # after a notification, let the app's upload finish
CHUNK_POINTS = 1000         # Valhalla limits trace length; long drives go in parts
GAP_SPLIT_MS = 60_000       # a gap this long starts a new part
HTTP_TIMEOUT_S = 60

log = logging.getLogger("road_matcher")


class TransientError(Exception):
    """Valhalla unreachable or failing: try this trip again at the next catch-up."""


def setting(name, default=None, kind=str):
    value = os.environ.get(name, default)
    if value is None:
        sys.exit(f"{name} is not set (it comes from matcher.env)")
    return kind(value)


VALHALLA_URL = setting("VALHALLA_URL", "http://127.0.0.1:8002").rstrip("/")
SEARCH_RADIUS_M = setting("SEARCH_RADIUS_M", "30", float)
GPS_ACCURACY_M = setting("GPS_ACCURACY_M", "8", float)
MAX_OFF_M = setting("MAX_OFF_M", "40", float)


def valhalla(path, body):
    """POST to Valhalla. Returns (json, None) on success or (None, error_text) on a 4xx (no match)."""
    request = urllib.request.Request(
        VALHALLA_URL + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
            return json.load(response), None
    except urllib.error.HTTPError as e:
        text = e.read().decode(errors="replace")[:300]
        if 400 <= e.code < 500:
            return None, text
        raise TransientError(f"Valhalla HTTP {e.code}: {text}") from e
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise TransientError(f"Valhalla unreachable: {e}") from e


def chunks(points):
    """Split a trip's points at long gaps and every CHUNK_POINTS points."""
    part = []
    for p in points:
        if part and (len(part) >= CHUNK_POINTS or p["t_start"] - part[-1]["t_start"] > GAP_SPLIT_MS):
            yield part
            part = []
        part.append(p)
    if part:
        yield part


def match_points(points):
    """Returns (matches, stats). matches: one dict per point Valhalla put on a road."""
    matches, offs, errors = [], [], Counter()
    for part in chunks(points):
        if len(part) < 2:
            continue
        body = {
            "shape": [
                {"lat": p["lat"], "lon": p["lon"], "time": (p["t_start"] + p["t_end"]) / 2000.0}
                for p in part
            ],
            "costing": "auto",
            "shape_match": "map_snap",
            "use_timestamps": True,
            "trace_options": {
                "search_radius": SEARCH_RADIUS_M,
                "gps_accuracy": GPS_ACCURACY_M,
                "breakage_distance": 2000,
            },
            "filters": {
                "attributes": [
                    "edge.way_id",
                    "matched.point",
                    "matched.type",
                    "matched.edge_index",
                    "matched.distance_from_trace_point",
                ],
                "action": "include",
            },
        }
        result, error = valhalla("/trace_attributes", body)
        if result is None:
            try:
                errors[json.loads(error).get("error", "error")] += 1
            except ValueError:
                errors["error"] += 1
            continue
        edges = result.get("edges") or []
        for p, m in zip(part, result.get("matched_points") or []):
            if m.get("type") not in ("matched", "interpolated"):
                continue
            i = m.get("edge_index")
            if not isinstance(i, int) or not 0 <= i < len(edges):
                continue
            way_id = edges[i].get("way_id")
            off = float(m.get("distance_from_trace_point") or 0.0)
            if way_id is None or off > MAX_OFF_M or "lat" not in m or "lon" not in m:
                continue
            matches.append({"t_start": p["t_start"], "way_id": int(way_id),
                            "lat": m["lat"], "lon": m["lon"], "off_m": round(off, 2)})
            offs.append(off)
    return matches, {"offs": offs, "errors": errors}


def fetch_points(conn, trip_id):
    rows = conn.execute("select * from roads.trip_points(%s)", (trip_id,)).fetchall()
    cols = ("t_start", "t_end", "lat", "lon", "speed_mps", "gps_accuracy_m", "heading_deg")
    return [dict(zip(cols, r)) for r in rows]


def match_trip(conn, trip_id):
    started = time.monotonic()
    points = fetch_points(conn, trip_id)
    if len(points) < 2:
        log.info("trip %s: fewer than 2 located segments, skipped", trip_id)
        return
    matches, stats = match_points(points)
    status = "MATCHED" if matches else "NO_MATCH"
    pieces = conn.execute(
        "select roads.save_trip_match(%s, %s, %s, %s)",
        (trip_id, len(points), status, Jsonb(matches)),
    ).fetchone()[0]
    log.info("trip %s: %s, %d of %d points on roads, %d pieces, %.1f s%s",
             trip_id, status, len(matches), len(points), pieces, time.monotonic() - started,
             f", Valhalla errors {dict(stats['errors'])}" if stats["errors"] else "")


def try_match(conn, trip_id):
    """match_trip, logging failures. Returns False if Valhalla is down (stop for now)."""
    try:
        match_trip(conn, trip_id)
    except TransientError as e:
        log.warning("trip %s: %s; will retry at the next catch-up", trip_id, e)
        return False
    except psycopg.Error as e:
        if conn.closed or conn.broken:
            raise                 # lost the connection: serve() reconnects
        log.error("trip %s: database error %s: %s", trip_id, type(e).__name__,
                  str(e).splitlines()[0][:200] if str(e) else "")
    return True


def catch_up(conn):
    """Match every waiting trip. A trip that fails is logged and left for the next catch-up."""
    done = set()
    while True:
        waiting = [r[0] for r in conn.execute("select trip_id from roads.trips_to_match(20)").fetchall()]
        waiting = [t for t in waiting if t not in done]
        if not waiting:
            return
        for trip_id in waiting:
            done.add(trip_id)
            if not try_match(conn, trip_id):
                return


def handle_notification(conn, payload):
    try:
        trip_id = uuid.UUID(payload)
    except ValueError:
        catch_up(conn)            # e.g. 'catch_up' after the roads were reloaded
        return
    time.sleep(SETTLE_S)
    try_match(conn, trip_id)


def connect():
    conn = psycopg.connect(setting("MATCHER_DB_URL"), autocommit=True,
                           application_name="road_matcher", connect_timeout=10)
    conn.execute("set statement_timeout = '5min'")
    return conn


def serve():
    while True:
        try:
            with connect() as conn:
                conn.execute("listen road_matcher")
                log.info("connected; listening for finished trips")
                catch_up(conn)
                next_catch_up = time.monotonic() + CATCH_UP_EVERY_S
                while True:
                    wait = max(1.0, next_catch_up - time.monotonic())
                    for note in conn.notifies(timeout=wait, stop_after=1):
                        handle_notification(conn, note.payload)
                    if time.monotonic() >= next_catch_up:
                        catch_up(conn)
                        next_catch_up = time.monotonic() + CATCH_UP_EVERY_S
        except psycopg.OperationalError as e:
            # Supabase not started yet, or restarted. The message has no password in it, but
            # keep only the first line to be safe.
            log.warning("database unavailable (%s); retrying in %d s",
                        str(e).splitlines()[0][:200] if str(e) else type(e).__name__, RECONNECT_WAIT_S)
            time.sleep(RECONNECT_WAIT_S)


def dry_run(trip_id):
    with connect() as conn:
        points = fetch_points(conn, trip_id)
    if len(points) < 2:
        sys.exit(f"Trip {trip_id} has fewer than 2 located segments.")
    started = time.monotonic()
    matches, stats = match_points(points)
    offs = sorted(stats["offs"])
    ways = Counter(m["way_id"] for m in matches)
    print(f"Trip {trip_id}: {len(points)} located segments, matched in {time.monotonic() - started:.1f} s")
    print(f"  on a road:      {len(matches)} ({100 * len(matches) / len(points):.0f}%)")
    if offs:
        p90 = offs[min(len(offs) - 1, int(0.9 * len(offs)))]
        print(f"  points moved:   median {statistics.median(offs):.1f} m, 90% within {p90:.1f} m, "
              f"max {offs[-1]:.1f} m")
        print(f"  OSM ways used:  {len(ways)}; most used: "
              + ", ".join(f"{w} ({n})" for w, n in ways.most_common(5)))
    if stats["errors"]:
        print(f"  Valhalla said no match for some parts: {dict(stats['errors'])}")
    print("  Nothing was stored.")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--once", action="store_true", help="match waiting trips, then exit")
    group.add_argument("--dry-run", metavar="TRIP_ID", type=uuid.UUID,
                       help="match one trip and print stats; store nothing")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        if args.dry_run:
            dry_run(args.dry_run)
        elif args.once:
            with connect() as conn:
                catch_up(conn)
        else:
            serve()
    except TransientError as e:
        sys.exit(str(e))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
