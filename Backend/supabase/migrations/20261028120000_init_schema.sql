-- init_schema
-- Tables, checks and access rules from docs/data-contract.md §7. Change the contract first,
-- then add a new migration; never edit this file after it has been applied on the server.
--
-- Access summary (contract §7 "Who can do what"):
--   app     = signed in anonymously -> role `authenticated`, own user id in auth.uid()
--   web map = anon key, not signed in -> role `anon`, read-only
--   Nobody can delete through the API. vehicles is changed only by migrations.

create extension if not exists postgis with schema extensions;

-- ---------------------------------------------------------------- vehicles
-- Rows are added by migrations (see the vehicles migration), not by the app.
create table public.vehicles (
    id           text primary key,                       -- short readable id: truck, camry, escape
    name         text not null,
    type         text not null check (type in ('truck', 'sedan', 'suv')),
    scale_factor double precision not null default 1.0 check (scale_factor > 0)
);

-- ---------------------------------------------------------------- trips
create table public.trips (
    id            uuid primary key,                      -- made by the app
    vehicle_id    text not null references public.vehicles (id),
    start_time    bigint not null,                       -- epoch ms, phone time
    end_time      bigint,                                -- null while the trip is in progress
    source        text not null check (source in ('REAL', 'FAKE_ASSET', 'FAKE_DEVICE')),
    app_version   text,
    created_by    uuid default auth.uid() references auth.users (id) on delete set null,
    received_at   timestamptz not null default now(),
    start_time_ts timestamptz generated always as (to_timestamp(start_time::double precision / 1000)) stored,
    end_time_ts   timestamptz generated always as (to_timestamp(end_time::double precision / 1000)) stored,
    check (end_time is null or end_time >= start_time),
    -- Targets for the composite foreign keys below, so a segment/event can't disagree with its
    -- trip about the vehicle or the source.
    unique (id, source),
    unique (id, vehicle_id, source)
);

-- ---------------------------------------------------------------- segments
-- ~1 second of road each. Re-sent segments hit the primary key and are skipped.
create table public.segments (
    trip_id        uuid not null,
    t_start        bigint not null,                      -- epoch ms
    t_end          bigint not null,
    lat            double precision check (lat between -90 and 90),
    lon            double precision check (lon between -180 and 180),
    speed_mps      real check (speed_mps >= 0),
    gps_accuracy_m real check (gps_accuracy_m >= 0),
    heading_deg    real check (heading_deg >= 0 and heading_deg < 360),
    rms_z          real not null,                        -- g, roughness
    peak_z         real not null,                        -- g
    roll_rms       real not null,                        -- deg/s
    pitch_rms      real not null,                        -- deg/s
    source         text not null,
    geog           extensions.geography(Point, 4326) generated always as
                       (extensions.st_setsrid(extensions.st_makepoint(lon, lat), 4326)::extensions.geography) stored,
    received_at    timestamptz not null default now(),
    t_start_ts     timestamptz generated always as (to_timestamp(t_start::double precision / 1000)) stored,
    t_end_ts       timestamptz generated always as (to_timestamp(t_end::double precision / 1000)) stored,
    primary key (trip_id, t_start),
    foreign key (trip_id, source) references public.trips (id, source) on delete cascade,
    check (t_end >= t_start),
    check ((lat is null) = (lon is null))
);

create index segments_geog_idx on public.segments using gist (geog);

-- ---------------------------------------------------------------- events
-- Potholes / speed bumps. BLE calls the class field `class`; here it is `event_class`.
create table public.events (
    id             uuid primary key,                     -- made by the app
    trip_id        uuid not null,
    vehicle_id     text not null references public.vehicles (id),
    t              bigint not null,                      -- epoch ms, time of the peak
    lat            double precision check (lat between -90 and 90),
    lon            double precision check (lon between -180 and 180),
    speed_mps      real check (speed_mps >= 0),
    gps_accuracy_m real check (gps_accuracy_m >= 0),
    heading_deg    real check (heading_deg >= 0 and heading_deg < 360),
    event_class    text not null check (event_class in ('POTHOLE', 'SPEED_BUMP', 'UNKNOWN')),
    peak_z         real not null,                        -- g
    roll_peak      real not null,                        -- deg/s
    pitch_peak     real not null,                        -- deg/s
    conf           real not null check (conf between 0 and 1),
    user_label     text check (user_label in ('POTHOLE', 'SPEED_BUMP', 'UNKNOWN')),
    source         text not null,
    geog           extensions.geography(Point, 4326) generated always as
                       (extensions.st_setsrid(extensions.st_makepoint(lon, lat), 4326)::extensions.geography) stored,
    received_at    timestamptz not null default now(),
    t_ts           timestamptz generated always as (to_timestamp(t::double precision / 1000)) stored,
    foreign key (trip_id, vehicle_id, source)
        references public.trips (id, vehicle_id, source) on delete cascade,
    check ((lat is null) = (lon is null))
);

create index events_geog_idx    on public.events using gist (geog);
create index events_trip_id_idx on public.events (trip_id);

-- ---------------------------------------------------------------- privileges
-- Supabase grants everything on new public tables to anon/authenticated by default, leaving RLS
-- as the only guard. Start from nothing and grant back only what the contract allows, so a
-- missing policy can never leave a table writable.
revoke all on public.vehicles, public.trips, public.segments, public.events
    from anon, authenticated;

grant select on public.vehicles, public.trips, public.segments, public.events
    to anon, authenticated;
grant insert on public.trips, public.segments, public.events to authenticated;
grant update (end_time)   on public.trips  to authenticated;
grant update (user_label) on public.events to authenticated;

-- ---------------------------------------------------------------- row level security
alter table public.vehicles enable row level security;
alter table public.trips    enable row level security;
alter table public.segments enable row level security;
alter table public.events   enable row level security;

-- Everyone can read map data.
create policy "everyone can read vehicles" on public.vehicles
    for select to anon, authenticated using (true);
create policy "everyone can read trips" on public.trips
    for select to anon, authenticated using (true);
create policy "everyone can read segments" on public.segments
    for select to anon, authenticated using (true);
create policy "everyone can read events" on public.events
    for select to anon, authenticated using (true);

-- The app adds trips as itself and may end only its own trips.
create policy "app adds its own trips" on public.trips
    for insert to authenticated
    with check (created_by = (select auth.uid()));
create policy "app ends its own trips" on public.trips
    for update to authenticated
    using (created_by = (select auth.uid()))
    with check (created_by = (select auth.uid()));

-- Segments and events can only be added to trips this phone created (so the trip is uploaded
-- first). Vehicle and source agreement with the trip is enforced by the foreign keys.
create policy "app adds segments to its own trips" on public.segments
    for insert to authenticated
    with check (exists (
        select 1 from public.trips t
        where t.id = segments.trip_id and t.created_by = (select auth.uid())
    ));
create policy "app adds events to its own trips" on public.events
    for insert to authenticated
    with check (exists (
        select 1 from public.trips t
        where t.id = events.trip_id and t.created_by = (select auth.uid())
    ));

-- The app may relabel only events on its own trips (column grant limits it to user_label).
create policy "app labels events on its own trips" on public.events
    for update to authenticated
    using (exists (
        select 1 from public.trips t
        where t.id = events.trip_id and t.created_by = (select auth.uid())
    ))
    with check (exists (
        select 1 from public.trips t
        where t.id = events.trip_id and t.created_by = (select auth.uid())
    ));
