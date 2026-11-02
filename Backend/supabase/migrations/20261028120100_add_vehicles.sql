-- add_vehicles
-- Starting vehicle list from docs/data-contract.md §7. The final list is still a team decision
-- (§9 Team); change it with a new migration, not by editing this one after it is applied.
-- scale_factor stays 1.0 until the calibration drives.

insert into public.vehicles (id, name, type) values
    ('truck',  'Truck',  'truck'),
    ('camry',  'Camry',  'sedan'),
    ('escape', 'Escape', 'suv')
on conflict (id) do nothing;
