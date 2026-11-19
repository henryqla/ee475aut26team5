-- road_lanes
-- Lanes per direction for road_quality_snapped (contract §7), so the app can size each ROAD
-- line to the lanes it stands for: a one-lane-each-way street gets two narrow lines side by
-- side, a four-lane arterial two wide ones, and the two directions never overlap.
--
-- The value is still one number per direction: the matcher can't tell which lane a drive was
-- in, so every lane going that way shares it. lanes_dir only sets how wide that is drawn.
--
-- From the OSM tags lanes, lanes:forward, lanes:backward and lanes:both_ways (a centre turn
-- lane, counted in neither direction). Where they are missing, a default by road type.
-- roads.ways gets the counts when bash roads/import_roads.sh is run again; until then every
-- way has its default.

-- ---------------------------------------------------------------- the rule
-- Returns {forward, backward}: lanes in and against the way's drawing direction (the same
-- sense as dir = 1 / -1), each 1 to 8.
--   one-way:  lanes - both_ways, else lanes:forward / lanes:backward, else the default
--   two-way:  lanes:forward / lanes:backward where given; a missing side is
--             lanes - the other side - both_ways; with neither side given,
--             (lanes - both_ways) / 2 rounded down (lanes=3 is usually 1 + 1 + a turn lane);
--             else the default
--   default:  motorway, trunk, primary 2; everything else 1 (per direction)
create function roads.lanes_by_direction(
    highway    text,
    oneway     boolean,
    lanes      text,
    lanes_fwd  text,
    lanes_bwd  text,
    lanes_both text
)
returns smallint[]
language plpgsql
immutable
set search_path = ''
as $$
declare
    total int := case when trim(lanes)      ~ '^[0-9]{1,2}$' then nullif(trim(lanes)::int, 0) end;
    f     int := case when trim(lanes_fwd)  ~ '^[0-9]{1,2}$' then nullif(trim(lanes_fwd)::int, 0) end;
    b     int := case when trim(lanes_bwd)  ~ '^[0-9]{1,2}$' then nullif(trim(lanes_bwd)::int, 0) end;
    bw    int := case when trim(lanes_both) ~ '^[0-9]{1,2}$' then trim(lanes_both)::int else 0 end;
    dflt  int := case when highway in ('motorway', 'trunk', 'primary') then 2 else 1 end;
    rest  int := total - bw;
    n     int;
begin
    if rest is not null and rest < 1 then
        rest := null;
    end if;
    if oneway then
        n := coalesce(rest, f, b, dflt);
        return array[least(n, 8), least(n, 8)]::smallint[];
    end if;
    if f is null and b is not null and rest is not null and rest - b >= 1 then
        f := rest - b;
    end if;
    if b is null and f is not null and rest is not null and rest - f >= 1 then
        b := rest - f;
    end if;
    if f is null and b is null and rest is not null then
        f := greatest(rest / 2, 1);
        b := f;
    end if;
    return array[least(coalesce(f, dflt), 8), least(coalesce(b, dflt), 8)]::smallint[];
end
$$;

revoke all on function roads.lanes_by_direction(text, boolean, text, text, text, text)
    from public, anon, authenticated;

-- ---------------------------------------------------------------- roads.ways
alter table roads.ways
    add column lanes_fwd    smallint,   -- lanes in the way's drawing direction (dir = 1)
    add column lanes_bwd    smallint,   -- lanes against it (dir = -1)
    add column lanes_tagged boolean not null default false;   -- from OSM tags, not the default

update roads.ways w
set lanes_fwd = l.v[1], lanes_bwd = l.v[2]
from (select way_id, roads.lanes_by_direction(highway, oneway, null, null, null, null) as v
      from roads.ways) l
where l.way_id = w.way_id;

alter table roads.ways
    alter column lanes_fwd set not null,
    alter column lanes_bwd set not null,
    add constraint ways_lanes_check check (lanes_fwd between 1 and 8 and lanes_bwd between 1 and 8);

-- ---------------------------------------------------------------- load_ways
-- As in road_matching, plus the lane counts. Needs the lane columns that roads/roads.lua
-- writes to osm_import.ways (import_roads.sh runs both from the same commit).
create or replace function roads.load_ways()
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

    insert into roads.ways (way_id, highway, name, oneway, geom, len_m,
                            lanes_fwd, lanes_bwd, lanes_tagged)
    select o.way_id, o.highway, o.name, x.oneway, o.geom, extensions.st_length(o.geom),
           l.v[1], l.v[2],
           coalesce(o.lanes, o.lanes_forward, o.lanes_backward) is not null
    from osm_import.ways o
    cross join lateral (
        select coalesce(o.oneway in ('yes', '1', 'true', '-1'), false)
                   or coalesce(o.junction in ('roundabout', 'circular'), false)
                   or (o.highway in ('motorway', 'motorway_link') and coalesce(o.oneway, '') <> 'no')
               as oneway
    ) x
    cross join lateral (
        select roads.lanes_by_direction(o.highway, x.oneway, o.lanes, o.lanes_forward,
                                        o.lanes_backward, o.lanes_both_ways) as v
    ) l
    where o.geom is not null
      and extensions.geometrytype(o.geom) = 'LINESTRING'
      and extensions.st_length(o.geom) > 0
    on conflict (way_id) do nothing;
    get diagnostics n = row_count;

    perform pg_notify('road_matcher', 'catch_up');
    return n;
end
$$;

revoke all on function roads.load_ways() from public, anon, authenticated;

-- ---------------------------------------------------------------- map functions
-- A new result column means drop and create (create or replace can't change it).
-- roads.fallback_cells is unchanged.
drop function public.road_quality_snapped(double precision, double precision, double precision, double precision, boolean, text, double precision);
drop function roads.snapped_pieces(double precision, double precision, double precision, double precision, boolean, text, double precision);

-- As in road_quality_snapped, plus lanes_dir. A row never spans two ways (pieces are joined
-- within a way), so it has one lane count.
create function roads.snapped_pieces(
    south        double precision,
    west         double precision,
    north        double precision,
    east         double precision,
    include_fake boolean,
    vehicle_id   text,
    piece_m      double precision
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

create function public.road_quality_snapped(
    south        double precision,
    west         double precision,
    north        double precision,
    east         double precision,
    include_fake boolean          default false,
    vehicle_id   text             default null,
    piece_m      double precision default 15
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
                                  road_quality_snapped.piece_m) r
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
                                  road_quality_snapped.piece_m) c
    ) x (piece_id, kind, path, lat, lon, heading_deg, length_m, roughness, drives, weight,
         last_t, oneway, lanes_dir, road_name)
    order by x.piece_id;
$$;

-- ---------------------------------------------------------------- privileges
revoke all on function roads.snapped_pieces(double precision, double precision, double precision, double precision, boolean, text, double precision)
    from public, anon, authenticated;
revoke all on function public.road_quality_snapped(double precision, double precision, double precision, double precision, boolean, text, double precision)
    from public, anon, authenticated;

grant execute on function roads.snapped_pieces(double precision, double precision, double precision, double precision, boolean, text, double precision)
    to anon, authenticated;
grant execute on function public.road_quality_snapped(double precision, double precision, double precision, double precision, boolean, text, double precision)
    to anon, authenticated;

notify pgrst, 'reload schema';
