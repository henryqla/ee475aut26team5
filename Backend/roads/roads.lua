-- roads.lua - osm2pgsql flex style for Backend/roads/import_roads.sh.
-- Keeps only drivable OSM roads (the ones Valhalla's car profile can match to), with their way
-- id, name, one-way and lane tags, in UTM zone 10N (EPSG:32610) so lengths are in metres.
-- Writes to osm_import.ways; roads.load_ways() then copies it into roads.ways.
-- Works with osm2pgsql 1.6 (Ubuntu 22.04) and newer.

local ways = osm2pgsql.define_way_table('ways', {
    { column = 'highway',  type = 'text' },
    { column = 'name',     type = 'text' },
    { column = 'oneway',   type = 'text' },
    { column = 'junction', type = 'text' },
    { column = 'lanes',           type = 'text' },   -- lane tags, read by roads.lanes_by_direction
    { column = 'lanes_forward',   type = 'text' },
    { column = 'lanes_backward',  type = 'text' },
    { column = 'lanes_both_ways', type = 'text' },
    { column = 'geom',    type = 'linestring', projection = 32610 },
}, { schema = 'osm_import' })

local drivable = {
    motorway = true, motorway_link = true, trunk = true, trunk_link = true,
    primary = true, primary_link = true, secondary = true, secondary_link = true,
    tertiary = true, tertiary_link = true, unclassified = true, residential = true,
    living_street = true, service = true, road = true, track = true,
}

function osm2pgsql.process_way(object)
    local highway = object.tags.highway
    if not highway or not drivable[highway] then return end
    if object.tags.area == 'yes' then return end

    local geom
    if object.as_linestring then
        geom = object:as_linestring()       -- osm2pgsql 1.7 and newer
    else
        geom = { create = 'line' }          -- osm2pgsql 1.6
    end

    ways:insert({
        highway  = highway,
        name     = object.tags.name,
        oneway   = object.tags.oneway,
        junction = object.tags.junction,
        lanes           = object.tags.lanes,
        lanes_forward   = object.tags['lanes:forward'],
        lanes_backward  = object.tags['lanes:backward'],
        lanes_both_ways = object.tags['lanes:both_ways'],
        geom     = geom,
    })
end
