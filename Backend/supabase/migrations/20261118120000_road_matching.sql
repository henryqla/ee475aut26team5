-- road_matching
-- Snaps drives to OpenStreetMap roads, so the map can draw road quality on the road itself and
-- combine every drive over the same stretch (see Backend/ROAD_MATCHING_SETUP.txt).
--
-- How it fits together:
--   roads.ways            OSM roads for Washington, loaded by Backend/roads/import_roads.sh.
--                         Geometry in UTM zone 10N (EPSG:32610), so lengths are in metres.
--   road matcher worker   Backend/matcher/road_matcher.py on the laptop. Sends each finished trip
--                         to a local Valhalla, which says which OSM way each segment was on.
--   roads.segment_matches One row per matched segment: way, distance along it, direction, and
--                         the stretch of the way the segment covered.
--   roads.drive_pieces    One row per drive per 15 m piece of a way per direction: what the map
--                         function combines. Rebuilt from segment_matches at any time.
--   roads.trip_matches    Which trips were matched, and with how many segments, so trips that
--                         get more segments later are matched again.
--
-- The roads schema is not in config.toml [api] schemas, so none of this is reachable through the
-- API directly. The map functions (next migration) read it as the calling user, so anon and
-- authenticated get select on the tables they need, and nothing else.
-- The worker logs in as its own role, road_matcher, which can only call three functions here.
-- Its password is set on the laptop by Backend/matcher/install_matcher.sh, never in a migration.
--
-- Pieces are keyed by OSM way id (stable across Valhalla rebuilds), direction (+1 = the way's
-- drawing direction, -1 = against it), and k = which 15 m piece counted from the way's start.

create schema if not exists roads;
revoke all on schema roads from public;

-- ---------------------------------------------------------------- tables
create table roads.ways (
    way_id   bigint primary key,                        -- OSM way id (Valhalla's edge.way_id)
    highway  text not null,                             -- OSM highway tag: residential, primary, ...
    name     text,
    oneway   boolean not null,                          -- one-way road: drawn without a side offset
    geom     extensions.geometry(LineString, 32610) not null,
    len_m    double precision not null check (len_m > 0)
);
create index ways_geom_idx on roads.ways using gist (geom);

create table roads.trip_matches (
    trip_id        uuid primary key references public.trips (id) on delete cascade,
    status         text not null check (status in ('MATCHED', 'NO_MATCH')),
    segment_count  integer not null,                    -- located segments when matched
    matched_points integer not null,                    -- of those, how many were put on a road
    matched_at     timestamptz not null default now()
);

create table roads.segment_matches (
    trip_id  uuid not null,
    t_start  bigint not null,
    way_id   bigint not null,                           -- no foreign key: roads.ways is reloaded
    lat      double precision not null,                 -- the point Valhalla put on the road
    lon      double precision not null,
    off_m    real not null,                             -- how far the GPS point was moved
    dist_m   double precision not null,                 -- distance along the way from its start
    dir      smallint check (dir in (-1, 1)),           -- null: direction unknown (not drawn)
    lo_m     double precision,                          -- stretch of the way this segment covered;
    hi_m     double precision,                          -- null when it isn't drawn as a road
    primary key (trip_id, t_start),
    foreign key (trip_id, t_start) references public.segments (trip_id, t_start) on delete cascade
);

create table roads.drive_pieces (
    way_id   bigint not null,
    dir      smallint not null check (dir in (-1, 1)),
    k        integer not null check (k >= 0),           -- piece covers [k * 15, (k + 1) * 15) m
    trip_id  uuid not null references public.trips (id) on delete cascade,
    rough    real not null,                             -- g, length-weighted mean rms_z (raw;
                                                        -- scale_factor is applied when read)
    len_m    real not null,                             -- metres of the piece this drive covered
    t        bigint not null,                           -- epoch ms, when the drive was there
    primary key (way_id, dir, k, trip_id)
);
create index drive_pieces_trip_idx on roads.drive_pieces (trip_id);

-- ---------------------------------------------------------------- privileges
-- Start from nothing, then grant read on what the map functions read.
revoke all on roads.ways, roads.trip_matches, roads.segment_matches, roads.drive_pieces
    from public, anon, authenticated;
grant usage on schema roads to anon, authenticated;
grant select on roads.ways, roads.segment_matches, roads.drive_pieces to anon, authenticated;

alter table roads.ways            enable row level security;
alter table roads.trip_matches    enable row level security;
alter table roads.segment_matches enable row level security;
alter table roads.drive_pieces    enable row level security;

create policy "everyone can read ways" on roads.ways
    for select to anon, authenticated using (true);
create policy "everyone can read segment matches" on roads.segment_matches
    for select to anon, authenticated using (true);
create policy "everyone can read drive pieces" on roads.drive_pieces
    for select to anon, authenticated using (true);

-- The worker's role. No login until install_matcher.sh gives it a password.
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'road_matcher') then
        create role road_matcher nologin;
    end if;
end
$$;
grant usage on schema roads to road_matcher;

-- ---------------------------------------------------------------- rebuild one trip
-- From a trip's segment_matches: direction and covered stretch per segment, then its
-- drive_pieces. Returns how many pieces it stored. Owner only (called by save_trip_match and
-- rebuild_all_trips).
--
-- Direction: from how distance along the way changes between the segment's neighbours on the
-- same way (at least 2 m), else over its whole run on that way, else from the heading compared
-- with the way's direction there. A run is consecutive segments on one way with no gap over 2 s.
-- Stretch: like road_quality_in_area, from halfway to the previous segment to halfway to the
-- next, measured along the way; at the ends of a run, speed x duration / 2 (at most 30 m).
-- Stretches longer than 100 m are bad data and aren't drawn.
create function roads.rebuild_trip(p_trip uuid)
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
    n integer;
begin
    update roads.segment_matches set dir = null, lo_m = null, hi_m = null where trip_id = p_trip;

    with m as (
        select sm.t_start, sm.way_id, sm.dist_m, w.len_m, w.geom, s.heading_deg,
               coalesce(s.speed_mps, 0)::double precision * (s.t_end - s.t_start) / 2000.0 as reach_m,
               (s.t_start + s.t_end) / 2 as t_mid
        from roads.segment_matches sm
        join public.segments s on s.trip_id = sm.trip_id and s.t_start = sm.t_start
        join roads.ways w      on w.way_id = sm.way_id
        where sm.trip_id = p_trip
    ),
    breaks as (
        select m.*,
               case when lag(m.way_id) over o is distinct from m.way_id
                      or m.t_mid - lag(m.t_mid) over o > 2000 then 1 else 0 end as new_run
        from m
        window o as (order by m.t_start)
    ),
    runs as (
        select b.*, sum(b.new_run) over (order by b.t_start) as run from breaks b
    ),
    nb as (
        select r.*,
               first_value(r.dist_m) over whole as d_first,
               last_value(r.dist_m)  over whole as d_last,
               lag(r.dist_m)  over o as pd,
               lag(r.t_mid)   over o as pt,
               lead(r.dist_m) over o as nd,
               lead(r.t_mid)  over o as nt
        from runs r
        window o     as (partition by r.run order by r.t_start),
               whole as (partition by r.run order by r.t_start
                         rows between unbounded preceding and unbounded following)
    ),
    dirs as (
        select nb.*,
               case when abs(coalesce(nb.nd, nb.dist_m) - coalesce(nb.pd, nb.dist_m)) >= 2
                        then sign(coalesce(nb.nd, nb.dist_m) - coalesce(nb.pd, nb.dist_m))
                    when abs(nb.d_last - nb.d_first) >= 2
                        then sign(nb.d_last - nb.d_first)
                    when nb.heading_deg is not null and a.az is not null
                        then case when abs(((nb.heading_deg - a.az + 540)::numeric % 360) - 180) <= 90
                                  then 1 else -1 end
               end::smallint as dir
        from nb
        cross join lateral (
            select degrees(extensions.st_azimuth(
                       extensions.st_lineinterpolatepoint(nb.geom, greatest(nb.dist_m - 2, 0) / nb.len_m),
                       extensions.st_lineinterpolatepoint(nb.geom, least(nb.dist_m + 2, nb.len_m) / nb.len_m)
                   )) as az
        ) a
    ),
    ends as (
        select d.t_start, d.dir, d.len_m,
               case when d.pd is not null and d.t_mid - d.pt <= 2000
                         and abs(d.dist_m - d.pd) <= 0.05 * (d.t_mid - d.pt)
                    then (d.pd + d.dist_m) / 2
                    else d.dist_m - d.dir * least(d.reach_m, 30) end as back_m,
               case when d.nd is not null and d.nt - d.t_mid <= 2000
                         and abs(d.nd - d.dist_m) <= 0.05 * (d.nt - d.t_mid)
                    then (d.dist_m + d.nd) / 2
                    else d.dist_m + d.dir * least(d.reach_m, 30) end as front_m
        from dirs d
        where d.dir is not null
    ),
    ivl as (
        select e.t_start, e.dir,
               greatest(least(e.back_m, e.front_m), 0)       as lo_m,
               least(greatest(e.back_m, e.front_m), e.len_m) as hi_m
        from ends e
    )
    update roads.segment_matches sm
    set dir = i.dir, lo_m = i.lo_m, hi_m = i.hi_m
    from ivl i
    where sm.trip_id = p_trip
      and sm.t_start = i.t_start
      and i.hi_m > i.lo_m
      and i.hi_m - i.lo_m <= 100;

    delete from roads.drive_pieces where trip_id = p_trip;

    insert into roads.drive_pieces (way_id, dir, k, trip_id, rough, len_m, t)
    select x.way_id, x.dir, x.k, p_trip,
           (sum(x.rms_z * x.ov) / sum(x.ov))::real,
           sum(x.ov)::real,
           (sum(x.t_mid * x.ov) / sum(x.ov))::bigint
    from (
        select sm.way_id, sm.dir, k.k, s.rms_z, ((s.t_start + s.t_end) / 2)::double precision as t_mid,
               least(sm.hi_m, (k.k + 1) * 15.0) - greatest(sm.lo_m, k.k * 15.0) as ov
        from roads.segment_matches sm
        join public.segments s on s.trip_id = sm.trip_id and s.t_start = sm.t_start
        cross join lateral generate_series(floor(sm.lo_m / 15)::int,
                                           greatest(ceil(sm.hi_m / 15)::int - 1, floor(sm.lo_m / 15)::int)) as k(k)
        where sm.trip_id = p_trip
          and sm.lo_m is not null
    ) x
    where x.ov > 0
    group by x.way_id, x.dir, x.k;

    get diagnostics n = row_count;
    return n;
end
$$;

-- Rebuilds every matched trip, e.g. after changing rebuild_trip in a later migration.
-- Owner only: in Studio's SQL editor, `select roads.rebuild_all_trips();`
create function roads.rebuild_all_trips()
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
    t uuid;
    n integer := 0;
begin
    for t in select trip_id from roads.trip_matches loop
        perform roads.rebuild_trip(t);
        n := n + 1;
    end loop;
    return n;
end
$$;

-- ---------------------------------------------------------------- functions for the worker
-- Trips that need matching, oldest first:
--   - never matched, or got more located segments since they were matched, and
--   - ended (end_time set), or with no new segment for 30 minutes (end_time never arrived).
create function roads.trips_to_match(max_trips integer default 20)
returns table (trip_id uuid)
language sql
stable
security definer
set search_path = ''
as $$
    select t.id
    from public.trips t
    cross join lateral (
        select count(*)::integer as n, max(s.received_at) as last_rx
        from public.segments s
        where s.trip_id = t.id and s.lat is not null
    ) s
    left join roads.trip_matches m on m.trip_id = t.id
    where s.n >= 2
      and (m.trip_id is null or m.segment_count <> s.n)
      and (t.end_time is not null or s.last_rx < now() - interval '30 minutes')
    order by t.start_time
    limit greatest(least(coalesce(max_trips, 20), 200), 1);
$$;

-- A trip's located segments, in time order, for sending to Valhalla.
create function roads.trip_points(p_trip uuid)
returns table (
    t_start        bigint,
    t_end          bigint,
    lat            double precision,
    lon            double precision,
    speed_mps      real,
    gps_accuracy_m real,
    heading_deg    real
)
language sql
stable
security definer
set search_path = ''
as $$
    select s.t_start, s.t_end, s.lat, s.lon, s.speed_mps, s.gps_accuracy_m, s.heading_deg
    from public.segments s
    where s.trip_id = p_trip and s.lat is not null
    order by s.t_start;
$$;

-- Stores the worker's result for one trip, replacing any earlier one, and rebuilds its pieces.
-- p_matches: [{"t_start": ms, "way_id": osm id, "lat": .., "lon": .., "off_m": ..}, ...] for the
-- segments Valhalla put on a road. Rows for unknown ways or segments are skipped.
-- Returns how many drive pieces were stored.
create function roads.save_trip_match(
    p_trip          uuid,
    p_segment_count integer,
    p_status        text,
    p_matches       jsonb
)
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
    n integer;
begin
    if p_status not in ('MATCHED', 'NO_MATCH') then
        raise exception 'status must be MATCHED or NO_MATCH, not %', p_status;
    end if;

    delete from roads.segment_matches where trip_id = p_trip;

    insert into roads.segment_matches (trip_id, t_start, way_id, lat, lon, off_m, dist_m)
    select p_trip, x.t_start, x.way_id, x.lat, x.lon, greatest(coalesce(x.off_m, 0), 0),
           extensions.st_linelocatepoint(
               w.geom,
               extensions.st_transform(extensions.st_setsrid(extensions.st_makepoint(x.lon, x.lat), 4326), 32610)
           ) * w.len_m
    from jsonb_to_recordset(coalesce(p_matches, '[]'::jsonb))
             as x(t_start bigint, way_id bigint, lat double precision, lon double precision, off_m real)
    join roads.ways w      on w.way_id = x.way_id
    join public.segments s on s.trip_id = p_trip and s.t_start = x.t_start
    where x.lat between -90 and 90
      and x.lon between -180 and 180
    on conflict do nothing;

    n := roads.rebuild_trip(p_trip);

    insert into roads.trip_matches (trip_id, status, segment_count, matched_points, matched_at)
    values (p_trip, p_status, coalesce(p_segment_count, 0),
            (select count(*) from roads.segment_matches sm where sm.trip_id = p_trip), now())
    on conflict (trip_id) do update
        set status         = excluded.status,
            segment_count  = excluded.segment_count,
            matched_points = excluded.matched_points,
            matched_at     = excluded.matched_at;

    return n;
end
$$;

-- ---------------------------------------------------------------- loading roads
-- Called by Backend/roads/import_roads.sh after osm2pgsql has filled osm_import.ways. Replaces
-- roads.ways and clears all matches (they refer to the old roads), so the worker matches every
-- trip again; until then those trips show with the cell method. Owner only.
create function roads.load_ways()
returns bigint
language plpgsql
security definer
set search_path = ''
as $$
declare
    n bigint;
begin
    delete from roads.drive_pieces;
    delete from roads.segment_matches;
    delete from roads.trip_matches;
    truncate roads.ways;

    insert into roads.ways (way_id, highway, name, oneway, geom, len_m)
    select o.way_id, o.highway, o.name,
           coalesce(o.oneway in ('yes', '1', 'true', '-1'), false)
               or coalesce(o.junction in ('roundabout', 'circular'), false)
               or (o.highway in ('motorway', 'motorway_link') and coalesce(o.oneway, '') <> 'no'),
           o.geom,
           extensions.st_length(o.geom)
    from osm_import.ways o
    where o.geom is not null
      and extensions.geometrytype(o.geom) = 'LINESTRING'
      and extensions.st_length(o.geom) > 0
    on conflict (way_id) do nothing;
    get diagnostics n = row_count;

    perform pg_notify('road_matcher', 'catch_up');
    return n;
end
$$;

-- ---------------------------------------------------------------- wake the worker
-- When the app sets end_time, tell the worker to match the trip now instead of at its next
-- 30-minute catch-up.
create function roads.notify_trip_ended()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    perform pg_notify('road_matcher', new.id::text);
    return new;
end
$$;

create trigger trips_notify_road_matcher
    after update of end_time on public.trips
    for each row
    when (new.end_time is not null and old.end_time is distinct from new.end_time)
    execute function roads.notify_trip_ended();

-- ---------------------------------------------------------------- function privileges
revoke all on function roads.rebuild_trip(uuid)                              from public, anon, authenticated;
revoke all on function roads.rebuild_all_trips()                             from public, anon, authenticated;
revoke all on function roads.trips_to_match(integer)                         from public, anon, authenticated;
revoke all on function roads.trip_points(uuid)                               from public, anon, authenticated;
revoke all on function roads.save_trip_match(uuid, integer, text, jsonb)     from public, anon, authenticated;
revoke all on function roads.load_ways()                                     from public, anon, authenticated;
revoke all on function roads.notify_trip_ended()                             from public, anon, authenticated;

grant execute on function roads.trips_to_match(integer)                     to road_matcher;
grant execute on function roads.trip_points(uuid)                           to road_matcher;
grant execute on function roads.save_trip_match(uuid, integer, text, jsonb) to road_matcher;
