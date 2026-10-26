-- sample_data.sql
-- Throwaway schema + fake data for poking around in Supabase Studio.
-- NOT a migration. Safe to re-run: it drops and recreates everything below.
--
-- Run it in Studio > SQL Editor, or:
--   psql "postgresql://postgres:postgres@127.0.0.1:54322/postgres" -f sample_data.sql
--
-- Tables mirror android_app/.../data/Models.kt (snake_case). All rows are tagged
-- FAKE_ASSET / FAKE_DEVICE so they can be filtered out before the demo.
-- Locations are around the UW Seattle campus.

-- ---------------------------------------------------------------- reset
drop table if exists road_events cascade;
drop table if exists segments    cascade;
drop table if exists trips       cascade;
drop table if exists vehicles    cascade;
drop type  if exists data_source;
drop type  if exists event_class;

-- ---------------------------------------------------------------- types
create type data_source as enum ('FAKE_ASSET', 'FAKE_DEVICE', 'REAL');
create type event_class as enum ('POTHOLE', 'SPEED_BUMP', 'UNKNOWN');

-- ---------------------------------------------------------------- tables
create table vehicles (
    id           uuid primary key default gen_random_uuid(),
    name         text not null,
    type         text not null check (type in ('truck', 'sedan', 'suv')),
    scale_factor double precision not null default 1.0,   -- from reference-route calibration
    created_at   timestamptz not null default now()
);

create table trips (
    id         uuid primary key default gen_random_uuid(),
    vehicle_id uuid not null references vehicles (id) on delete cascade,
    start_time timestamptz not null default now(),
    end_time   timestamptz,                                -- null while the trip is in progress
    source     data_source not null default 'REAL'
);

-- ~1 second of road each. Location/speed from the phone, roughness from the ESP32.
create table segments (
    id        bigint generated always as identity primary key,
    trip_id   uuid not null references trips (id) on delete cascade,
    t         timestamptz not null,
    lat       double precision not null,
    lon       double precision not null,
    speed_mps double precision not null,
    roughness double precision not null
);

create table road_events (
    id          uuid primary key default gen_random_uuid(),
    trip_id     uuid not null references trips (id) on delete cascade,
    vehicle_id  uuid not null references vehicles (id) on delete cascade,
    t           timestamptz not null,
    lat         double precision not null,
    lon         double precision not null,
    speed_mps   double precision not null,
    event_class event_class not null default 'UNKNOWN',
    peak_g      double precision not null,
    roll        double precision not null,
    confidence  double precision not null check (confidence between 0 and 1),
    user_label  event_class,                               -- set from the Drive screen label buttons
    source      data_source not null default 'REAL'
);

create index segments_trip_t_idx     on segments (trip_id, t);
create index road_events_lat_lon_idx on road_events (lat, lon);  -- for eventsInArea(bbox)
create index road_events_trip_idx    on road_events (trip_id);

-- ---------------------------------------------------------------- row level security
-- DEV ONLY: lets the app's anon key read and write everything. Tighten before anything real.
alter table vehicles    enable row level security;
alter table trips       enable row level security;
alter table segments    enable row level security;
alter table road_events enable row level security;

create policy "dev all access" on vehicles    for all to anon, authenticated using (true) with check (true);
create policy "dev all access" on trips       for all to anon, authenticated using (true) with check (true);
create policy "dev all access" on segments    for all to anon, authenticated using (true) with check (true);
create policy "dev all access" on road_events for all to anon, authenticated using (true) with check (true);

-- ---------------------------------------------------------------- fake data
select setseed(0.475);   -- same "random" numbers every run

-- Fixed ids so the rows below can reference each other and are easy to spot.
insert into vehicles (id, name, type, scale_factor) values
    ('00000000-0000-0000-0000-0000000000a1', 'Team Civic',   'sedan', 1.00),
    ('00000000-0000-0000-0000-0000000000a2', 'Rental Tacoma','truck', 0.82),
    ('00000000-0000-0000-0000-0000000000a3', 'Mom''s RAV4',  'suv',   0.91);

insert into trips (id, vehicle_id, start_time, end_time, source) values
    -- northbound up 15th Ave NE
    ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000a1',
     '2026-10-20 15:00:00-07', '2026-10-20 15:02:20-07', 'FAKE_ASSET'),
    -- eastbound along NE 45th St
    ('00000000-0000-0000-0000-0000000000b2', '00000000-0000-0000-0000-0000000000a2',
     '2026-10-21 09:30:00-07', '2026-10-21 09:32:30-07', 'FAKE_ASSET'),
    -- short bench test with the real board, still "in progress"
    ('00000000-0000-0000-0000-0000000000b3', '00000000-0000-0000-0000-0000000000a3',
     '2026-10-24 18:10:00-07', null, 'FAKE_DEVICE');

-- Trip 1: 140 s north on 15th Ave NE. Rough patches around i = 37-38 and 91.
insert into segments (trip_id, t, lat, lon, speed_mps, roughness)
select '00000000-0000-0000-0000-0000000000b1',
       timestamptz '2026-10-20 15:00:00-07' + make_interval(secs => i),
       47.6550 + i * 0.000107,
       -122.3128 + (random() - 0.5) * 0.00002,
       11 + random() * 3,
       case when i in (37, 38, 91) then 2.0 + random()
            when i between 60 and 75 then 0.8 + random() * 0.4   -- worn stretch
            else 0.15 + random() * 0.35 end
from generate_series(0, 139) as i;

-- Trip 2: 150 s east on NE 45th St. Speed bump near i = 50, pothole near i = 112.
insert into segments (trip_id, t, lat, lon, speed_mps, roughness)
select '00000000-0000-0000-0000-0000000000b2',
       timestamptz '2026-10-21 09:30:00-07' + make_interval(secs => i),
       47.6612 + (random() - 0.5) * 0.00002,
       -122.3200 + i * 0.000167,
       9 + random() * 4,
       case when i in (50, 112) then 1.8 + random()
            else 0.2 + random() * 0.4 end
from generate_series(0, 149) as i;

-- Trip 3: 30 s crawling around a parking lot.
insert into segments (trip_id, t, lat, lon, speed_mps, roughness)
select '00000000-0000-0000-0000-0000000000b3',
       timestamptz '2026-10-24 18:10:00-07' + make_interval(secs => i),
       47.6535 + sin(i / 5.0) * 0.0002,
       -122.3055 + cos(i / 5.0) * 0.0003,
       2 + random(),
       0.1 + random() * 0.2
from generate_series(0, 29) as i;

insert into road_events
    (trip_id, vehicle_id, t, lat, lon, speed_mps, event_class, peak_g, roll, confidence, user_label, source)
values
    -- trip 1 (15th Ave NE)
    ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000a1',
     '2026-10-20 15:00:37-07', 47.65896, -122.31281, 12.4, 'POTHOLE',    2.71,  4.2, 0.91, 'POTHOLE',    'FAKE_ASSET'),
    ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000a1',
     '2026-10-20 15:00:38-07', 47.65907, -122.31279, 12.1, 'POTHOLE',    1.94, -3.1, 0.74, null,         'FAKE_ASSET'),
    ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000a1',
     '2026-10-20 15:01:31-07', 47.66474, -122.31282, 13.0, 'POTHOLE',    3.35,  6.8, 0.96, 'POTHOLE',    'FAKE_ASSET'),
    ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000a1',
     '2026-10-20 15:01:05-07', 47.66196, -122.31280, 11.6, 'UNKNOWN',    1.42,  0.9, 0.38, null,         'FAKE_ASSET'),
    -- trip 2 (NE 45th St)
    ('00000000-0000-0000-0000-0000000000b2', '00000000-0000-0000-0000-0000000000a2',
     '2026-10-21 09:30:50-07', 47.66121, -122.31165,  6.2, 'SPEED_BUMP', 1.65,  0.4, 0.88, 'SPEED_BUMP', 'FAKE_ASSET'),
    ('00000000-0000-0000-0000-0000000000b2', '00000000-0000-0000-0000-0000000000a2',
     '2026-10-21 09:31:52-07', 47.66119, -122.30130, 12.8, 'POTHOLE',    2.88, -5.5, 0.83, null,         'FAKE_ASSET'),
    ('00000000-0000-0000-0000-0000000000b2', '00000000-0000-0000-0000-0000000000a2',
     '2026-10-21 09:32:10-07', 47.66123, -122.29830, 11.9, 'SPEED_BUMP', 1.21,  0.2, 0.52, 'POTHOLE',    'FAKE_ASSET'),
    -- trip 3 (bench test)
    ('00000000-0000-0000-0000-0000000000b3', '00000000-0000-0000-0000-0000000000a3',
     '2026-10-24 18:10:14-07', 47.65357, -122.30578,  2.4, 'UNKNOWN',    1.08,  1.7, 0.21, null,         'FAKE_DEVICE');

-- ---------------------------------------------------------------- things to try
-- Row counts:
--   select 'vehicles' as t, count(*) from vehicles union all
--   select 'trips', count(*) from trips union all
--   select 'segments', count(*) from segments union all
--   select 'road_events', count(*) from road_events;
--
-- What the map screen will ask for (eventsInArea with a bounding box):
--   select * from road_events
--   where lat between 47.650 and 47.670 and lon between -122.320 and -122.295;
--
-- Roughest stretches of road:
--   select trip_id, t, lat, lon, round(roughness::numeric, 2) as roughness
--   from segments order by roughness desc limit 10;
--
-- Where the detector and the human disagreed:
--   select t, event_class, user_label, confidence from road_events
--   where user_label is not null and user_label <> event_class;
--
-- Same thing through the REST API the app will use:
--   curl "http://127.0.0.1:54321/rest/v1/road_events?event_class=eq.POTHOLE" \
--        -H "apikey: <anon-key>" -H "Authorization: Bearer <anon-key>"
--
-- Delete all fake data (keeps the tables):
--   delete from trips where source <> 'REAL';
--   delete from vehicles where id::text like '00000000-0000-0000-0000-0000000000a%';
