-- add_trips_fw_version
-- Firmware version per trip, from the device's status message (docs/data-contract.md §4.3, §7,
-- CR-1). The app sets it when it first uploads the trip and never changes it, so no update grant
-- is needed. Null for trips uploaded before this column existed or without a status message.

alter table public.trips add column fw_version text;

-- Have PostgREST pick up the new column right away.
notify pgrst, 'reload schema';
