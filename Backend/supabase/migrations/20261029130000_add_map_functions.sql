-- add_map_functions
-- First version of the map functions in docs/data-contract.md §7 "Map functions". The app and the
-- web map both call these (REST: POST /rest/v1/rpc/<name>), so they always show the same thing.
-- Roughness is raw rms_z x scale_factor for now; speed normalization comes after the calibration
-- drives, in a new migration (create or replace).
--
-- Both stay security invoker (the default), so RLS on the tables still applies. search_path is
-- empty, so every table and PostGIS function is schema-qualified.
-- Parameters are named so the app can pass them as JSON. `vehicle_id` is also a column name, so
-- in the body it is written with the function name in front (road_quality_in_area.vehicle_id).
-- The API returns at most max_rows (1000, config.toml) rows per request, so a big map view needs
-- several pages (range / offset). Results are sorted so pages don't overlap or skip rows.

-- ---------------------------------------------------------------- road_quality_in_area
-- Segments are grouped into ~15 m grid cells and 8 direction sectors (45° each, centred on N, NE,
-- E, ...), so the two directions of a road stay apart; 350° and 10° land in the same sector.
-- Each drive first gets one value per group (the average of its segments there, so a slow drive
-- with many segments counts once), then roughness is the median over drives, so one odd drive
-- doesn't skew a road. The grid is fixed to the globe, not to the query box, so panning the map
-- never regroups rows. Segments with no location or no heading are left out.
create function public.road_quality_in_area(
    south        double precision,
    west         double precision,
    north        double precision,
    east         double precision,
    include_fake boolean default false,
    vehicle_id   text    default null
)
returns table (
    lat         double precision,   -- average position of the group
    lon         double precision,
    heading_deg real,               -- average direction of travel, 0 to <360, clockwise from north
    roughness   real,               -- g, median over drives of rms_z x scale_factor
    drives      integer,            -- drives that contributed
    length_m    real                -- length of road the row stands for (the cell size)
)
language sql
stable
set search_path = ''
as $$
    with grid as (
        select 15.0::double precision          as cell_m,
               15.0::double precision / 111195 as cell_deg   -- 15 m in degrees of latitude
    ),
    seg as (
        select s.trip_id, s.lat, s.lon, s.heading_deg,
               s.rms_z * v.scale_factor                  as rough,
               floor(s.lat / g.cell_deg)                 as cell_y,
               floor(s.heading_deg / 45 + 0.5)::int % 8  as sector,
               g.cell_deg
        from public.segments s
        join public.trips t    on t.id = s.trip_id
        join public.vehicles v on v.id = t.vehicle_id
        cross join grid g
        where s.lat between south and north
          and s.lon between west and east
          and s.heading_deg is not null
          and (include_fake or s.source = 'REAL')
          and (road_quality_in_area.vehicle_id is null
               or t.vehicle_id = road_quality_in_area.vehicle_id)
    ),
    cell as (
        -- Cells are narrower in degrees of longitude away from the equator; use each row's cos.
        select seg.*,
               floor(seg.lon * cos(radians((seg.cell_y + 0.5) * seg.cell_deg)) / seg.cell_deg) as cell_x
        from seg
    ),
    per_drive as (
        select c.cell_y, c.cell_x, c.sector, c.trip_id,
               avg(c.rough)                     as rough,
               avg(c.lat)                       as lat,
               avg(c.lon)                       as lon,
               avg(sin(radians(c.heading_deg))) as hs,   -- headings averaged around the circle
               avg(cos(radians(c.heading_deg))) as hc
        from cell c
        group by c.cell_y, c.cell_x, c.sector, c.trip_id
    )
    select avg(d.lat),
           avg(d.lon),
           ((degrees(atan2(avg(d.hs), avg(d.hc))) + 360)::numeric % 360)::real,
           (percentile_cont(0.5) within group (order by d.rough))::real,
           count(*)::integer,
           (select g.cell_m from grid g)::real
    from per_drive d
    group by d.cell_y, d.cell_x, d.sector
    order by d.cell_y, d.cell_x, d.sector;
$$;

-- ---------------------------------------------------------------- events_in_area
-- Events are merged when they are within 10 m of each other, have the same class, and headings
-- within 45° (compared around the circle). An event with no heading merges by distance and class
-- only. Merging is transitive: A-B and B-C close means A, B, C are one spot.
-- Class: the user's label wins, except UNKNOWN, which means "not sure" and changes nothing
-- (the same as no label).
-- passes = drives that went past the spot in the same direction: one of their segments covers it
-- (its position is within half the distance driven in that segment, plus 10 m for GPS error).
-- Drives that hit the spot always count as passes too, so hits <= passes.
-- Events with no location are left out. Every event in the box is compared with every other, which
-- is fine for the hundreds of events a map view holds.
create function public.events_in_area(
    south        double precision,
    west         double precision,
    north        double precision,
    east         double precision,
    include_fake boolean default false,
    vehicle_id   text    default null
)
returns table (
    lat         double precision,   -- average position of the merged hits
    lon         double precision,
    event_class text,               -- POTHOLE, SPEED_BUMP or UNKNOWN, after user labels
    heading_deg real,               -- average heading of the hits; null if none had one
    hits        integer,            -- drives that hit it
    passes      integer,            -- drives that passed the spot in the same direction
    last_t      bigint,             -- epoch ms of the latest hit
    max_peak_z  real                -- g, largest peak_z among the hits
)
language sql
stable
set search_path = ''
as $$
    with recursive
    ev as (
        select row_number() over (order by e.id) as n,
               e.trip_id, e.lat, e.lon, e.heading_deg, e.t, e.peak_z, e.geog,
               coalesce(nullif(e.user_label, 'UNKNOWN'), e.event_class) as cls
        from public.events e
        where e.lat between south and north
          and e.lon between west and east
          and (include_fake or e.source = 'REAL')
          and (events_in_area.vehicle_id is null
               or e.vehicle_id = events_in_area.vehicle_id)
    ),
    link as (   -- pairs of events close enough to merge
        select a.n as a, b.n as b
        from ev a
        join ev b
          on b.n <> a.n
         and b.cls = a.cls
         and extensions.st_dwithin(a.geog, b.geog, 10)
         and (a.heading_deg is null or b.heading_deg is null
              or least(abs(a.heading_deg - b.heading_deg),
                       360 - abs(a.heading_deg - b.heading_deg)) <= 45)
    ),
    reach (n, root) as (   -- every event reachable from each event through links
        select ev.n, ev.n from ev
        union
        select r.n, l.b from reach r join link l on l.a = r.root
    ),
    grp as (   -- one spot per group of linked events, named by its lowest n
        select r.n, min(r.root) as g from reach r group by r.n
    ),
    hit as (
        select grp.g, ev.cls,
               avg(ev.lat) as lat,
               avg(ev.lon) as lon,
               degrees(atan2(avg(sin(radians(ev.heading_deg))),
                             avg(cos(radians(ev.heading_deg))))) as h,
               max(ev.t)       as last_t,
               max(ev.peak_z)  as max_peak_z,
               count(distinct ev.trip_id) as hits
        from ev join grp on grp.n = ev.n
        group by grp.g, ev.cls
    ),
    spot as (
        select hit.*,
               ((hit.h + 360)::numeric % 360)::real as heading,
               extensions.st_setsrid(extensions.st_makepoint(hit.lon, hit.lat), 4326)::extensions.geography as geog
        from hit
    ),
    passing as (   -- (spot, drive) pairs; union removes repeats
        select sp.g, s.trip_id
        from spot sp
        join public.segments s on extensions.st_dwithin(s.geog, sp.geog, 60)   -- uses the index
        join public.trips t    on t.id = s.trip_id
        where (include_fake or s.source = 'REAL')
          and (events_in_area.vehicle_id is null
               or t.vehicle_id = events_in_area.vehicle_id)
          and extensions.st_dwithin(s.geog, sp.geog,
                  10 + coalesce(s.speed_mps, 0)::double precision
                       * (s.t_end - s.t_start)::double precision / 2000)
          and (sp.heading is null or s.heading_deg is null
               or least(abs(sp.heading - s.heading_deg),
                        360 - abs(sp.heading - s.heading_deg)) <= 45)
        union
        select grp.g, ev.trip_id from ev join grp on grp.n = ev.n
    )
    select sp.lat,
           sp.lon,
           sp.cls,
           sp.heading,
           sp.hits::integer,
           (select count(*) from passing p where p.g = sp.g)::integer,
           sp.last_t,
           sp.max_peak_z
    from spot sp
    order by sp.g;
$$;

-- ---------------------------------------------------------------- privileges
-- Same idea as the tables: start from nothing, grant back only what the contract allows.
-- Both the app and the web map read the map.
revoke all on function public.road_quality_in_area(double precision, double precision, double precision, double precision, boolean, text)
    from public, anon, authenticated;
revoke all on function public.events_in_area(double precision, double precision, double precision, double precision, boolean, text)
    from public, anon, authenticated;
grant execute on function public.road_quality_in_area(double precision, double precision, double precision, double precision, boolean, text)
    to anon, authenticated;
grant execute on function public.events_in_area(double precision, double precision, double precision, double precision, boolean, text)
    to anon, authenticated;

-- Have PostgREST pick up the new functions right away.
notify pgrst, 'reload schema';
