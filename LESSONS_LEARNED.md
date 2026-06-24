# Lessons Learned — Building Attribute Collection

Notes from disambiguating 100 Main St / 27 Langdon St (see `DATA_METHODS.md` §10).
Read this before adding the next batch of buildings.

## 1. Identical auto-collected values across two addresses = red flag, not coincidence

`collect_building_attributes.py`'s `osm_footprint()` picks the OSM Overpass building
*way* whose centroid is nearest the geocoded point. When two addresses geocode within
~30 m of each other — common at corners and multi-storefront blocks in old downtowns —
Overpass can hand back the exact same way for both, silently duplicating
`building_area_m2` / `approx_wall_length_a_m` / `approx_wall_length_b_m`.

**Before trusting the output:** diff every building's auto-collected values against its
neighbors. Identical numbers for two different addresses means manual disambiguation is
needed, not that they happen to be the same size.

## 2. A storefront's "address" and its tax parcel's address can legitimately differ

100 Main St and 27 Langdon St turned out to be a single corner building with two
addresses: OSM, Nominatim, and the shop's own signage all call the Langdon-St-facing
storefront (Buch Spieler Records) "27 Langdon St" — but Vermont's E911/tax-parcel system
files that same storefront under the building's primary address, "90 Main St." Both are
"correct"; they're just two different addressing conventions (commercial/postal vs.
legal/parcel) pointing at the same structure.

**Don't assume a geocoded address resolves to its own parcel.** In a dense block, verify
with a point-in-polygon check against the authoritative parcel layer before treating a
geocode as ground truth.

## 3. A free, no-key Vermont GIS API beats OSM Overpass for this

`https://maps.vtrans.vermont.gov/arcgis/rest/services/ROW/Parcels/MapServer` is a public
ArcGIS REST service (no API key) with:
- Layer 1 — E911 address points (`HOUSE_NUMBER`, `SN`, `PRIMARYADDRESS`, `GPSX`/`GPSY`)
- Layer 8 — tax parcels (`E911ADDR`, `OWNER1`, `PropertyLocationStreet`, `SHAPE.STArea()`,
  full ring geometry via `returnGeometry=true&outSR=4326`)

Both support attribute queries (`where=E911ADDR LIKE '%X%'`) and spatial queries
(`geometry=...&geometryType=esriGeometryEnvelope|esriGeometryPoint&spatialRel=esriSpatialRelIntersects`).
This resolved the disambiguation in a few queries — far more reliable than Overpass's
nearest-centroid heuristic for anything address-specific. Worth adding as a fallback /
cross-check source in `collect_building_attributes.py` for future buildings, not just a
one-off manual query.

(Layer 3, "Building Footprints," looked promising but is sparse/landmark-oriented —
parks, courts, cemeteries — not a general building-footprint layer. Use Layer 8 parcels
instead for footprint-area proxies.)

## 4. Don't trust a search engine's synthesized API endpoint — hit it first

Asking a web search for "VCGI parcel ArcGIS REST service" returned a fluent answer citing
`MAP_VCGI_VTPARCELS_WM_NOCACHE_v2` — which 404'd. It was a plausible-sounding name the
summarizer inferred from sibling service names, not a service that exists. The real
endpoint (`maps.vtrans.vermont.gov/.../ROW/Parcels/MapServer`) was sitting in the raw
result links the whole time. Always curl `?f=json` on a MapServer root before writing
code against it.

## 5. Satellite screenshots are fine for rough roof-shape calls, not footprint measurement

Google Maps satellite tiles (captured via Playwright, no API key, same technique as the
Street View capture) are good enough for a medium-confidence roof-shape/parapet read
(used for 54 Elm St). They are *not* reliable for pixel-ruler area/dimension measurement
— oblique camera angle and tile resolution introduce real distortion. For footprint
disputes, pull authoritative parcel geometry (§3) instead of measuring pixels.
