-- road_quality_stretches
-- road_quality_in_area counted each segment only at its midpoint. Above ~15 m/s the midpoints are
-- farther apart than a cell, so some cells along a road got no row and the map showed gaps. Now
-- each segment counts along the stretch of road it covered, in every cell that stretch crosses.
-- Also new (contract §7):
--   - cell_m: cell size in metres (default 15, kept between 5 and 1000), so the app can ask for
--     bigger cells when zoomed out.
--   - length_m is now the length of road inside the cell (was always 15), so pieces drawn
--     centred on (lat, lon) with that length meet end to end.
--
-- A segment's stretch runs from halfway to the drive's previous segment to halfway to its next
-- one, so it follows curves and the stretches of one drive meet. Where a half has no usable
-- neighbour (start or end of a drive, a GPS gap over 2 s, or a jump faster than 50 m/s), that half
-- is speed x duration / 2 along the heading instead (the same reach events_in_area uses).
-- Each half is cut into pieces of at most cell_m / 4, and each piece counts in the cell its
-- middle falls in. The rest is as before: per drive an average per cell and direction, then the
-- median over drives.
--
-- A new parameter changes the function's signature, which create or replace can't do, and two
-- versions side by side would make API calls ambiguous, so the old one is dropped first. Calls
-- without cell_m still work (it has a default), so the app needs no change.

drop function public.road_quality_in_area(double precision, double precision, double precision, double precision, boolean, text);

create function public.road_quality_in_area(
    south        double precision,
    west         double precision,
    north        double precision,
    east         double precision,
    include_fake boolean          default false,
    vehicle_id   text             default null,
    cell_m       double precision default 15
)
returns table (
    lat         double precision,   -- average position of the road inside the group
    lon         double precision,
    heading_deg real,               -- average direction of travel, 0 to <360, clockwise from north
    roughness   real,               -- g, median over drives of rms_z x scale_factor
    drives      integer,            -- drives that contributed
    length_m    real                -- length of road inside the cell (longest over drives)
)
language sql
stable
set search_path = ''
as $$
    with grid as (
        select g.size_m,
               g.size_m / 111195 as cell_deg,   -- cell size in degrees of latitude
               g.size_m / 4      as step_m,     -- longest piece a stretch is cut into
               0.003::double precision as margin_deg  -- ~300 m: also read segments just outside the box
        from (select least(greatest(coalesce(road_quality_in_area.cell_m, 15), 5), 1000)::double precision
                     as size_m) g
    ),
    seg as (   -- segments in and around the box, with the previous and next one of the same drive
        select s.trip_id, s.lat, s.lon, s.heading_deg,
               s.rms_z * v.scale_factor as rough,
               coalesce(s.speed_mps, 0)::double precision
                   * (s.t_end - s.t_start)::double precision / 2000 as reach_m,
               (s.t_start + s.t_end) / 2       as t_mid,
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
          and (include_fake or s.source = 'REAL')
          and (road_quality_in_area.vehicle_id is null
               or t.vehicle_id = road_quality_in_area.vehicle_id)
        window w as (partition by s.trip_id order by s.t_start)
    ),
    rel as (   -- the neighbours in metres east (x) and north (y) of each midpoint
        -- Segments with no heading still serve as neighbours above, but aren't drawn.
        select seg.*,
               (seg.prev_lon - seg.lon) * 111195 * cos(radians(seg.lat)) as px,
               (seg.prev_lat - seg.lat) * 111195                          as py,
               (seg.next_lon - seg.lon) * 111195 * cos(radians(seg.lat)) as nx,
               (seg.next_lat - seg.lat) * 111195                          as ny
        from seg
        where seg.heading_deg is not null
    ),
    stretch as (   -- where the back and front halves end, in metres east (x) and north (y) of the midpoint
        select r.trip_id, r.lat, r.lon, r.heading_deg, r.rough,
               case when b.ok then r.px / 2 else -r.reach_m * sin(radians(r.heading_deg)) end as back_x,
               case when b.ok then r.py / 2 else -r.reach_m * cos(radians(r.heading_deg)) end as back_y,
               case when f.ok then r.nx / 2 else  r.reach_m * sin(radians(r.heading_deg)) end as front_x,
               case when f.ok then r.ny / 2 else  r.reach_m * cos(radians(r.heading_deg)) end as front_y
        from rel r
        -- A neighbour is usable if it's at most 2 s away and not a jump faster than 50 m/s.
        cross join lateral (select coalesce(r.t_mid - r.prev_t <= 2000
                   and sqrt(r.px * r.px + r.py * r.py) <= 0.05 * (r.t_mid - r.prev_t), false) as ok) b
        cross join lateral (select coalesce(r.next_t - r.t_mid <= 2000
                   and sqrt(r.nx * r.nx + r.ny * r.ny) <= 0.05 * (r.next_t - r.t_mid), false) as ok) f
    ),
    piece as (   -- each half cut into n equal pieces, one row per piece, placed at its middle
        select st.trip_id, st.heading_deg, st.rough,
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
        -- Cells are narrower in degrees of longitude away from the equator; use each row's cos.
        select cell.*,
               floor(cell.lon * cos(radians((cell.cell_y + 0.5) * cell.cell_deg)) / cell.cell_deg) as cell_x
        from cell
    ),
    per_drive as (
        select c.cell_y, c.cell_x, c.sector, c.trip_id,
               avg(c.rough)                     as rough,
               avg(c.lat)                       as lat,
               avg(c.lon)                       as lon,
               sum(c.len)                       as len,  -- metres of this drive inside the cell
               avg(sin(radians(c.heading_deg))) as hs,   -- headings averaged around the circle
               avg(cos(radians(c.heading_deg))) as hc
        from cell_xy c
        group by c.cell_y, c.cell_x, c.sector, c.trip_id
    )
    select avg(d.lat),
           avg(d.lon),
           ((degrees(atan2(avg(d.hs), avg(d.hc))) + 360)::numeric % 360)::real,
           (percentile_cont(0.5) within group (order by d.rough))::real,
           count(*)::integer,
           -- A road can't be longer inside a cell than its diagonal (a drive passing twice could).
           least(max(d.len), (select g.size_m * sqrt(2) from grid g))::real
    from per_drive d
    group by d.cell_y, d.cell_x, d.sector
    order by d.cell_y, d.cell_x, d.sector;
$$;

-- Dropping the function dropped its grants too; grant back the same as add_map_functions.
revoke all on function public.road_quality_in_area(double precision, double precision, double precision, double precision, boolean, text, double precision)
    from public, anon, authenticated;
grant execute on function public.road_quality_in_area(double precision, double precision, double precision, double precision, boolean, text, double precision)
    to anon, authenticated;

-- Have PostgREST pick up the new signature right away.
notify pgrst, 'reload schema';
