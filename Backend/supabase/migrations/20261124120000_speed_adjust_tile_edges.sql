-- speed_adjust_tile_edges
-- 1. Speed adjustment (contract §8, from the Nov 22 2026 speed test). Each segment's rms_z is
--    scaled to what it would read at 25 mph before anything is averaged:
--        road     = sqrt(max(rms_z^2 - 0.015^2, 0))        remove the idle noise floor
--        adjusted = road * (11.176 / max(speed_mps, 4.4704)) ^ 0.56
--    11.176 m/s = 25 mph (reference); below 10 mph (4.4704 m/s) counts as 10 mph; no speed_mps
--    (no GPS fix): noise floor removed, no speed scaling. segments.rms_z stays raw.
--    - roads.rough_25mph(rms_z, speed_mps): the formula, in one place.
--    - roads.rebuild_trip: drive_pieces.rough is now the length-weighted mean of the adjusted
--      value (still before scale_factor, which is applied when read). Every matched trip is
--      rebuilt at the end of this migration, from the raw segments, so nothing is lost.
--    - roads.fallback_cells (CELL rows): adjusted value x scale_factor, as for ROAD rows.
-- 2. Tile edges. The app loads the map as tiles that share edges. Before, a row near an edge
--    came back from both tiles: a ROAD line crossing it was sent twice (the app kept one by
--    piece_id); a CELL cut by it came back from each side under the same piece_id but with
--    only that side's drives (the app kept whichever loaded first); events merged differently
--    on each side, so one pothole showed as two pins with the hits split between them. Now the
--    box is half-open (south <= lat < north, west <= lon < east) and each row comes back only
--    from the tile holding its point:
--      ROAD   the middle of its line (the lat / lon it returns)
--      CELL   the centre of its grid cell (fixed to the globe, so the same for every tile);
--             the cell is computed from all its pieces, also those over the edge
--      event  the merged position; events up to ~200 m past the edge are read so a group
--             crossing it merges the same way from either side
-- Signatures and result columns are unchanged, so create or replace keeps the grants.

-- ---------------------------------------------------------------- speed adjustment helper
create function roads.rough_25mph(rms_z real, speed_mps real)
returns double precision
language sql
immutable
parallel safe
set search_path = ''
as $$
    select sqrt(greatest(rms_z::double precision * rms_z - 0.015 * 0.015, 0))
           * case when speed_mps is null then 1
                  else power(11.176 / greatest(speed_mps::double precision, 4.4704), 0.56) end;
$$;

-- fallback_cells runs as the caller (anon / authenticated), so they need execute; roads isn't
-- exposed through the API, so it still can't be called directly.
revoke all on function roads.rough_25mph(real, real) from public, anon, authenticated;
grant execute on function roads.rough_25mph(real, real) to anon, authenticated;

-- ---------------------------------------------------------------- rebuild one trip
-- As in road_matching, except the per-piece mean uses roads.rough_25mph instead of rms_z.
create or replace function roads.rebuild_trip(p_trip uuid)
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
           (sum(x.rough * x.ov) / sum(x.ov))::real,
           sum(x.ov)::real,
           (sum(x.t_mid * x.ov) / sum(x.ov))::bigint
    from (
        select sm.way_id, sm.dir, k.k,
               roads.rough_25mph(s.rms_z, s.speed_mps) as rough,
               ((s.t_start + s.t_end) / 2)::double precision as t_mid,
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

-- ---------------------------------------------------------------- matched pieces
-- As in trip_time_filter, except a piece is kept when the middle of its line is in the
-- half-open box (was: when any part of the line touched the box).
create or replace function roads.snapped_pieces(
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
    )
    select 'R:' || s.way_id || ':' || s.dir || ':' || s.g || ':' || s.n,
           (select jsonb_agg(jsonb_build_array(extensions.st_y(pt.geom), extensions.st_x(pt.geom))
                             order by pt.path)
            from extensions.st_dumppoints(s.line) pt),
           mid.lat,
           mid.lon,
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
    cross join lateral (select extensions.st_lineinterpolatepoint(s.line, 0.5) as p) m
    cross join lateral (select extensions.st_y(m.p) as lat, extensions.st_x(m.p) as lon) mid
    where extensions.st_npoints(s.line) >= 2
      and mid.lat >= south and mid.lat < north      -- one tile per piece: the one holding
      and mid.lon >= west  and mid.lon < east;      -- the middle of its line
$$;

-- ---------------------------------------------------------------- fallback cells
-- As in trip_time_filter, except:
--   - roughness is roads.rough_25mph(...) x scale_factor (speed-adjusted, per segment);
--   - a cell is kept when its centre is in the half-open box. Pieces up to one cell past the
--     box are read too, so a kept cell has all its pieces, wherever the tile edge cuts it.
create or replace function roads.fallback_cells(
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
               -- one cell's width in degrees of longitude, at the box's edge nearest a pole
               g.size_m / 111195
                   / cos(radians(least(greatest(abs(south), abs(north)), 89))) as cell_deg_lon,
               g.size_m / 4      as step_m,
               0.003::double precision as margin_deg,
               extract(epoch from now())::double precision * 1000 as now_ms,
               14 * 86400000.0::double precision                  as half_life_ms
        from (select least(greatest(coalesce(fallback_cells.cell_m, 15), 15), 1000)::double precision
                     as size_m) g
    ),
    seg as (
        select s.trip_id, s.lat, s.lon, s.heading_deg,
               roads.rough_25mph(s.rms_z, s.speed_mps) * v.scale_factor as rough,
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
        where s.lat between south - g.margin_deg - g.cell_deg     and north + g.margin_deg + g.cell_deg
          and s.lon between west  - g.margin_deg - g.cell_deg_lon and east  + g.margin_deg + g.cell_deg_lon
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
    cell as (   -- pieces up to one cell past the box; cells are picked by their centre below
        select p.*,
               floor(p.lat / g.cell_deg)                 as cell_y,
               floor(p.heading_deg / 45 + 0.5)::int % 8  as sector,
               g.cell_deg
        from piece p
        cross join grid g
        where p.lat between south - g.cell_deg     and north + g.cell_deg
          and p.lon between west  - g.cell_deg_lon and east  + g.cell_deg_lon
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
    kept as (   -- one tile per cell: the one holding the cell's centre
        select d.*
        from per_drive d
        cross join grid g
        cross join lateral (select (d.cell_y + 0.5) * g.cell_deg as lat) cy
        cross join lateral (select (d.cell_x + 0.5) * g.cell_deg / cos(radians(cy.lat)) as lon) cx
        where cy.lat >= south and cy.lat < north
          and cx.lon >= west  and cx.lon < east
    ),
    ranked as (
        select d.*, x.wt,
               sum(x.wt) over (partition by d.cell_y, d.cell_x, d.sector
                               order by d.rough, d.trip_id rows unbounded preceding) as cum,
               sum(x.wt) over (partition by d.cell_y, d.cell_x, d.sector)              as total
        from kept d
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

-- ---------------------------------------------------------------- events_in_area
-- As in trip_time_filter, except events up to ~200 m past the box are read and merged, and a
-- merged event is kept when its merged position is in the half-open box. A group whose
-- linked hits stretch more than ~200 m past the edge could still merge differently per tile.
create or replace function public.events_in_area(
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
    pad as (   -- ~200 m, in degrees of latitude and of longitude
        select 0.0018::double precision as lat_deg,
               0.0018 / cos(radians(least(greatest(abs(south), abs(north)), 89))) as lon_deg
    ),
    ev as (
        select row_number() over (order by e.id) as n,
               e.trip_id, e.lat, e.lon, e.heading_deg, e.t, e.peak_z, e.geog,
               coalesce(nullif(e.user_label, 'UNKNOWN'), e.event_class) as cls
        from public.events e
        join public.trips t on t.id = e.trip_id
        cross join pad
        where e.lat between south - pad.lat_deg and north + pad.lat_deg
          and e.lon between west  - pad.lon_deg and east  + pad.lon_deg
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
    spot as (   -- one tile per merged event: the one holding its merged position
        select hit.*,
               ((hit.h + 360)::numeric % 360)::real as heading,
               extensions.st_setsrid(extensions.st_makepoint(hit.lon, hit.lat), 4326)::extensions.geography as geog
        from hit
        where hit.lat >= south and hit.lat < north
          and hit.lon >= west  and hit.lon < east
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

-- ---------------------------------------------------------------- recompute stored pieces
-- drive_pieces held raw means; rebuild every matched trip with the adjusted values.
select roads.rebuild_all_trips();

notify pgrst, 'reload schema';
