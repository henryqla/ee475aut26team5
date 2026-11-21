-- trip_time_filter
-- 1. Time filter for the map (contract §7): road_quality_snapped and events_in_area take two
--    optional parameters, so the app can show only the drives from a test day onward:
--      since_ms  only trips with start_time >= since_ms (epoch ms; null = no lower limit)
--      until_ms  only trips with start_time <  until_ms (epoch ms; null = no upper limit)
--    Whole trips are kept or left out by their start_time, so a drive crossing the cutoff is
--    never cut in half. Both null (the default) = every drive, as before. The age weighting
--    (14-day half-life) is unchanged: it decides how much the drives left in count.
-- 2. road_quality_in_area is dropped: the app now calls road_quality_snapped only.
--
-- New parameters change the function signatures, so the functions are dropped and created
-- again (create or replace would add a second version next to the old one, and PostgREST
-- couldn't pick between them). The bodies are as in road_quality_snapped / road_lanes /
-- add_map_functions, plus the two filters.

drop function public.road_quality_in_area(double precision, double precision, double precision, double precision, boolean, text, double precision);
drop function public.road_quality_snapped(double precision, double precision, double precision, double precision, boolean, text, double precision);
drop function roads.snapped_pieces(double precision, double precision, double precision, double precision, boolean, text, double precision);
drop function roads.fallback_cells(double precision, double precision, double precision, double precision, boolean, text, double precision);
drop function public.events_in_area(double precision, double precision, double precision, double precision, boolean, text);

-- ---------------------------------------------------------------- matched pieces
create function roads.snapped_pieces(
    south        double precision,
    west         double precision,
    north        double precision,
    east         double precision,
    include_fake boolean,
    vehicle_id   text,
    piece_m      double precision,
    since_ms     bigint,
    until_ms     bigint
)
returns table (
    piece_id    text,
    path        jsonb,
    lat         double precision,
    lon         double precision,
    heading_deg real,
    length_m    real,
    roughness   real,
    drives      integer,
    weight      real,
    last_t      bigint,
    oneway      boolean,
    lanes_dir   integer,
    road_name   text
)
language sql
stable
set search_path = ''
as $$
    with prm as (
        select greatest(least(round(coalesce(snapped_pieces.piece_m, 15) / 15), 200), 1)::int as n,
               extract(epoch from now())::double precision * 1000 as now_ms,
               14 * 86400000.0::double precision                  as half_life_ms,
               extensions.st_transform(
                   extensions.st_makeenvelope(west, south, east, north, 4326), 32610) as box
    ),
    w as (
        select w.way_id, w.geom, w.len_m, w.oneway, w.name, w.lanes_fwd, w.lanes_bwd
        from roads.ways w
        cross join prm
        where extensions.st_intersects(w.geom, prm.box)
    ),
    per_drive as (   -- one row per drive per joined piece
        select p.way_id, p.dir, p.k / prm.n as g, p.trip_id,
               sum(p.rough * p.len_m) / sum(p.len_m) * v.scale_factor as rough,
               min(p.k) as k_lo,
               max(p.k) as k_hi,
               max(p.t) as t
        from roads.drive_pieces p
        join w                 on w.way_id = p.way_id
        join public.trips t    on t.id = p.trip_id
        join public.vehicles v on v.id = t.vehicle_id
        cross join prm
        where (snapped_pieces.include_fake or t.source = 'REAL')
          and (snapped_pieces.vehicle_id is null or t.vehicle_id = snapped_pieces.vehicle_id)
          and (snapped_pieces.since_ms is null or t.start_time >= snapped_pieces.since_ms)
          and (snapped_pieces.until_ms is null or t.start_time <  snapped_pieces.until_ms)
        group by p.way_id, p.dir, p.k / prm.n, p.trip_id, v.scale_factor
    ),
    weighted as (
        select d.*,
               power(0.5, greatest(prm.now_ms - d.t, 0) / prm.half_life_ms) as wt
        from per_drive d
        cross join prm
    ),
    ranked as (   -- running weight in roughness order, for the weighted median
        select r.*,
               sum(r.wt) over (partition by r.way_id, r.dir, r.g
                               order by r.rough, r.trip_id rows unbounded preceding) as cum,
               sum(r.wt) over (partition by r.way_id, r.dir, r.g)                     as total
        from weighted r
    ),
    agg as (
        select r.way_id, r.dir, r.g,
               min(r.rough) filter (where r.cum >= r.total / 2) as roughness,
               count(*)::integer as drives,
               sum(r.wt)         as weight,
               max(r.t)          as last_t,
               min(r.k_lo)       as k_lo,
               max(r.k_hi)       as k_hi
        from ranked r
        group by r.way_id, r.dir, r.g
    ),
    shaped as (   -- the covered part of the way, in the direction of travel
        select a.*, w.oneway, w.name, prm.n, x.line_m,
               case when a.dir = 1 then w.lanes_fwd else w.lanes_bwd end as lanes_dir,
               extensions.st_transform(x.line_m, 4326) as line
        from agg a
        join w on w.way_id = a.way_id
        cross join prm
        cross join lateral (
            select extensions.st_simplify(
                       case when a.dir = 1 then l.sub else extensions.st_reverse(l.sub) end,
                       0.5 * prm.n) as line_m
            from (select extensions.st_linesubstring(
                             w.geom,
                             least(a.k_lo * 15.0 / w.len_m, 1),
                             least((a.k_hi + 1) * 15.0 / w.len_m, 1)) as sub) l
        ) x
        where a.k_lo * 15.0 < w.len_m
          and extensions.st_intersects(x.line_m, prm.box)
    )
    select 'R:' || s.way_id || ':' || s.dir || ':' || s.g || ':' || s.n,
           (select jsonb_agg(jsonb_build_array(extensions.st_y(pt.geom), extensions.st_x(pt.geom))
                             order by pt.path)
            from extensions.st_dumppoints(s.line) pt),
           extensions.st_y(mid.p),
           extensions.st_x(mid.p),
           degrees(extensions.st_azimuth(
               extensions.st_startpoint(s.line)::extensions.geography,
               extensions.st_endpoint(s.line)::extensions.geography))::real,
           extensions.st_length(s.line_m)::real,
           s.roughness::real,
           s.drives,
           s.weight::real,
           s.last_t,
           s.oneway,
           s.lanes_dir::integer,
           s.name
    from shaped s
    cross join lateral (select extensions.st_lineinterpolatepoint(s.line, 0.5) as p) mid
    where extensions.st_npoints(s.line) >= 2;
$$;

-- ---------------------------------------------------------------- fallback cells
-- The grid-cell method (once road_quality_in_area) for segments not on a matched road.
create function roads.fallback_cells(
    south        double precision,
    west         double precision,
    north        double precision,
    east         double precision,
    include_fake boolean,
    vehicle_id   text,
    cell_m       double precision,
    since_ms     bigint,
    until_ms     bigint
)
returns table (
    piece_id    text,
    lat         double precision,
    lon         double precision,
    heading_deg real,
    length_m    real,
    roughness   real,
    drives      integer,
    weight      real,
    last_t      bigint
)
language sql
stable
set search_path = ''
as $$
    with grid as (
        select g.size_m,
               g.size_m / 111195 as cell_deg,
               g.size_m / 4      as step_m,
               0.003::double precision as margin_deg,
               extract(epoch from now())::double precision * 1000 as now_ms,
               14 * 86400000.0::double precision                  as half_life_ms
        from (select least(greatest(coalesce(fallback_cells.cell_m, 15), 15), 1000)::double precision
                     as size_m) g
    ),
    seg as (
        select s.trip_id, s.lat, s.lon, s.heading_deg,
               s.rms_z * v.scale_factor as rough,
               coalesce(s.speed_mps, 0)::double precision
                   * (s.t_end - s.t_start)::double precision / 2000 as reach_m,
               (s.t_start + s.t_end) / 2       as t_mid,
               exists (select 1 from roads.segment_matches sm
                       where sm.trip_id = s.trip_id and sm.t_start = s.t_start
                         and sm.lo_m is not null) as snapped,
               lag(s.lat)  over w              as prev_lat,
               lag(s.lon)  over w              as prev_lon,
               lag((s.t_start + s.t_end) / 2)  over w as prev_t,
               lead(s.lat) over w              as next_lat,
               lead(s.lon) over w              as next_lon,
               lead((s.t_start + s.t_end) / 2) over w as next_t
        from public.segments s
        join public.trips t    on t.id = s.trip_id
        join public.vehicles v on v.id = t.vehicle_id
        cross join grid g
        where s.lat between south - g.margin_deg and north + g.margin_deg
          and s.lon between west - g.margin_deg and east + g.margin_deg
          and (fallback_cells.include_fake or s.source = 'REAL')
          and (fallback_cells.vehicle_id is null or t.vehicle_id = fallback_cells.vehicle_id)
          and (fallback_cells.since_ms is null or t.start_time >= fallback_cells.since_ms)
          and (fallback_cells.until_ms is null or t.start_time <  fallback_cells.until_ms)
        window w as (partition by s.trip_id order by s.t_start)
    ),
    rel as (
        select seg.*,
               (seg.prev_lon - seg.lon) * 111195 * cos(radians(seg.lat)) as px,
               (seg.prev_lat - seg.lat) * 111195                          as py,
               (seg.next_lon - seg.lon) * 111195 * cos(radians(seg.lat)) as nx,
               (seg.next_lat - seg.lat) * 111195                          as ny
        from seg
        where seg.heading_deg is not null
          and not seg.snapped
    ),
    stretch as (
        select r.trip_id, r.lat, r.lon, r.heading_deg, r.rough, r.t_mid,
               case when b.ok then r.px / 2 else -r.reach_m * sin(radians(r.heading_deg)) end as back_x,
               case when b.ok then r.py / 2 else -r.reach_m * cos(radians(r.heading_deg)) end as back_y,
               case when f.ok then r.nx / 2 else  r.reach_m * sin(radians(r.heading_deg)) end as front_x,
               case when f.ok then r.ny / 2 else  r.reach_m * cos(radians(r.heading_deg)) end as front_y
        from rel r
        cross join lateral (select coalesce(r.t_mid - r.prev_t <= 2000
                   and sqrt(r.px * r.px + r.py * r.py) <= 0.05 * (r.t_mid - r.prev_t), false) as ok) b
        cross join lateral (select coalesce(r.next_t - r.t_mid <= 2000
                   and sqrt(r.nx * r.nx + r.ny * r.ny) <= 0.05 * (r.next_t - r.t_mid), false) as ok) f
    ),
    piece as (
        select st.trip_id, st.heading_deg, st.rough, st.t_mid,
               d.len / c.n                                                         as len,
               st.lat + e.y * (k + 0.5) / c.n / 111195                             as lat,
               st.lon + e.x * (k + 0.5) / c.n / (111195 * cos(radians(st.lat)))    as lon
        from stretch st
        cross join grid g
        cross join lateral (values (st.back_x, st.back_y), (st.front_x, st.front_y)) as e(x, y)
        cross join lateral (select sqrt(e.x * e.x + e.y * e.y) as len) d
        cross join lateral (select greatest(ceil(d.len / g.step_m), 1)::int as n) c
        cross join lateral generate_series(0, c.n - 1) as k
    ),
    cell as (
        select p.*,
               floor(p.lat / g.cell_deg)                 as cell_y,
               floor(p.heading_deg / 45 + 0.5)::int % 8  as sector,
               g.cell_deg
        from piece p
        cross join grid g
        where p.lat between south and north
          and p.lon between west and east
    ),
    cell_xy as (
        select cell.*,
               floor(cell.lon * cos(radians((cell.cell_y + 0.5) * cell.cell_deg)) / cell.cell_deg) as cell_x
        from cell
    ),
    per_drive as (
        select c.cell_y, c.cell_x, c.sector, c.trip_id,
               avg(c.rough)                     as rough,
               avg(c.lat)                       as lat,
               avg(c.lon)                       as lon,
               sum(c.len)                       as len,
               avg(sin(radians(c.heading_deg))) as hs,
               avg(cos(radians(c.heading_deg))) as hc,
               max(c.t_mid)                     as t
        from cell_xy c
        group by c.cell_y, c.cell_x, c.sector, c.trip_id
    ),
    ranked as (
        select d.*, x.wt,
               sum(x.wt) over (partition by d.cell_y, d.cell_x, d.sector
                               order by d.rough, d.trip_id rows unbounded preceding) as cum,
               sum(x.wt) over (partition by d.cell_y, d.cell_x, d.sector)              as total
        from per_drive d
        cross join grid g
        cross join lateral (select power(0.5, greatest(g.now_ms - d.t, 0) / g.half_life_ms) as wt) x
    )
    select 'C:' || r.cell_y || ':' || r.cell_x || ':' || r.sector || ':' || (select g.size_m from grid g),
           avg(r.lat),
           avg(r.lon),
           ((degrees(atan2(avg(r.hs), avg(r.hc))) + 360)::numeric % 360)::real,
           least(max(r.len), (select g.size_m * sqrt(2) from grid g))::real,
           (min(r.rough) filter (where r.cum >= r.total / 2))::real,
           count(*)::integer,
           sum(r.wt)::real,
           max(r.t)::bigint
    from ranked r
    group by r.cell_y, r.cell_x, r.sector;
$$;

-- ---------------------------------------------------------------- road_quality_snapped
create function public.road_quality_snapped(
    south        double precision,
    west         double precision,
    north        double precision,
    east         double precision,
    include_fake boolean          default false,
    vehicle_id   text             default null,
    piece_m      double precision default 15,
    since_ms     bigint           default null,   -- only trips with start_time >= since_ms
    until_ms     bigint           default null    -- only trips with start_time <  until_ms
)
returns table (
    piece_id    text,               -- stable id; also the sort order (for paging past 1000 rows)
    kind        text,               -- ROAD: snapped to a road; CELL: not matched, cell method
    path        jsonb,              -- [[lat, lon], ...] in the direction of travel
    lat         double precision,   -- middle of the piece
    lon         double precision,
    heading_deg real,               -- direction of travel, start to end, 0 to <360
    length_m    real,               -- length of the line
    roughness   real,               -- g, age-weighted median over drives of rms_z x scale_factor
    drives      integer,            -- drives that contributed
    weight      real,               -- sum of the drives' age weights (1 = one drive from today)
    last_t      bigint,             -- epoch ms of the newest drive
    oneway      boolean,            -- ROAD only: one-way road (draw without a side offset)
    lanes_dir   integer,            -- ROAD only: lanes in this direction of travel (1 to 8)
    road_name   text                -- ROAD only: OSM name, may be null
)
language sql
stable
set search_path = ''
as $$
    select x.*
    from (
        select r.piece_id, 'ROAD'::text, r.path, r.lat, r.lon, r.heading_deg, r.length_m,
               r.roughness, r.drives, r.weight, r.last_t, r.oneway, r.lanes_dir, r.road_name
        from roads.snapped_pieces(south, west, north, east,
                                  coalesce(road_quality_snapped.include_fake, false),
                                  road_quality_snapped.vehicle_id,
                                  road_quality_snapped.piece_m,
                                  road_quality_snapped.since_ms,
                                  road_quality_snapped.until_ms) r
        union all
        select c.piece_id, 'CELL'::text,
               jsonb_build_array(
                   jsonb_build_array(c.lat - c.length_m / 2 * cos(radians(c.heading_deg)) / 111195,
                                     c.lon - c.length_m / 2 * sin(radians(c.heading_deg))
                                             / (111195 * cos(radians(c.lat)))),
                   jsonb_build_array(c.lat + c.length_m / 2 * cos(radians(c.heading_deg)) / 111195,
                                     c.lon + c.length_m / 2 * sin(radians(c.heading_deg))
                                             / (111195 * cos(radians(c.lat))))),
               c.lat, c.lon, c.heading_deg, c.length_m,
               c.roughness, c.drives, c.weight, c.last_t, null::boolean, null::integer, null::text
        from roads.fallback_cells(south, west, north, east,
                                  coalesce(road_quality_snapped.include_fake, false),
                                  road_quality_snapped.vehicle_id,
                                  road_quality_snapped.piece_m,
                                  road_quality_snapped.since_ms,
                                  road_quality_snapped.until_ms) c
    ) x (piece_id, kind, path, lat, lon, heading_deg, length_m, roughness, drives, weight,
         last_t, oneway, lanes_dir, road_name)
    order by x.piece_id;
$$;

-- ---------------------------------------------------------------- events_in_area
-- As in add_map_functions, plus the time filter on both the hits and the passes.
create function public.events_in_area(
    south        double precision,
    west         double precision,
    north        double precision,
    east         double precision,
    include_fake boolean default false,
    vehicle_id   text    default null,
    since_ms     bigint  default null,   -- only trips with start_time >= since_ms
    until_ms     bigint  default null    -- only trips with start_time <  until_ms
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
        join public.trips t on t.id = e.trip_id
        where e.lat between south and north
          and e.lon between west and east
          and (include_fake or e.source = 'REAL')
          and (events_in_area.vehicle_id is null
               or e.vehicle_id = events_in_area.vehicle_id)
          and (events_in_area.since_ms is null or t.start_time >= events_in_area.since_ms)
          and (events_in_area.until_ms is null or t.start_time <  events_in_area.until_ms)
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
          and (events_in_area.since_ms is null or t.start_time >= events_in_area.since_ms)
          and (events_in_area.until_ms is null or t.start_time <  events_in_area.until_ms)
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
-- As before: helpers are callable by the API roles (the public functions run as the caller)
-- but live in roads, which the API doesn't expose.
revoke all on function roads.snapped_pieces(double precision, double precision, double precision, double precision, boolean, text, double precision, bigint, bigint)
    from public, anon, authenticated;
revoke all on function roads.fallback_cells(double precision, double precision, double precision, double precision, boolean, text, double precision, bigint, bigint)
    from public, anon, authenticated;
revoke all on function public.road_quality_snapped(double precision, double precision, double precision, double precision, boolean, text, double precision, bigint, bigint)
    from public, anon, authenticated;
revoke all on function public.events_in_area(double precision, double precision, double precision, double precision, boolean, text, bigint, bigint)
    from public, anon, authenticated;

grant execute on function roads.snapped_pieces(double precision, double precision, double precision, double precision, boolean, text, double precision, bigint, bigint)
    to anon, authenticated;
grant execute on function roads.fallback_cells(double precision, double precision, double precision, double precision, boolean, text, double precision, bigint, bigint)
    to anon, authenticated;
grant execute on function public.road_quality_snapped(double precision, double precision, double precision, double precision, boolean, text, double precision, bigint, bigint)
    to anon, authenticated;
grant execute on function public.events_in_area(double precision, double precision, double precision, double precision, boolean, text, bigint, bigint)
    to anon, authenticated;

notify pgrst, 'reload schema';
