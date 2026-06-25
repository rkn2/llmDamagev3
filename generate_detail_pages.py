#!/usr/bin/env python3
"""Generate per-building HTML detail pages — all 177 spreadsheet columns in order."""
import json, openpyxl
from pathlib import Path

from damage_scale import DAMAGE_SCALE

ROOT_DIR = Path(__file__).parent
_DAMAGE_LABELS = {d.level: d.label for d in DAMAGE_SCALE}

# Live read of run_pipeline.py's output — the _llm_* fields below are computed from
# this at render time (see build_page()), not hand-copied, so re-running the
# assessment pipeline automatically flows through here instead of going stale.
ASSESSMENTS = {
    row["address"]: row
    for row in json.loads((ROOT_DIR / "address_assessments.json").read_text())
}

# Live reads of collect_building_attributes.py's and analyze_visual_attributes.py's
# output, for the same reason — these went stale once already (hardcoded placeholder
# lat/lon, wrong orientation) because the original version of this script was written
# before those two scripts existed and was never wired up to their output.
BUILDING_ATTRS = json.loads((ROOT_DIR / "building_attributes_auto.json").read_text())
VISUAL_ATTRS = json.loads((ROOT_DIR / "visual_attributes.json").read_text())

# Live read of critic.py's output, guarded since it's a new optional stage someone may
# not have run yet (unlike the three sources above, which the pipeline always produces).
_critic_path = ROOT_DIR / "critic_findings.json"
CRITIC_FINDINGS = json.loads(_critic_path.read_text()) if _critic_path.exists() else {}

# OSM-derived orientation and urban setting (compute_urban_attrs.py output).
# Guarded — pages render fine without it, but the LLM orientation values get used instead.
_ua_path = ROOT_DIR / "urban_attrs.json"
URBAN_ATTRS = json.loads(_ua_path.read_text()) if _ua_path.exists() else {}

# River distances computed from USGS NHD flowlines via
# hydro.nationalmap.gov/arcgis/rest/services/nhd/MapServer/6
# Projected to UTM 19N (EPSG:32619) for metric accuracy.
# distance_from_river_edge = centerline - 5m estimated half-width of N. Branch Winooski.
RIVER_DISTANCES = {
    "100 Main St, Montpelier, VT 05602":  {"cl": "34.5 m (113.2 ft)", "edge": "29.5 m (96.8 ft)"},
    "112 State St, Montpelier, VT 05602": {"cl": "66.7 m (218.8 ft)", "edge": "61.7 m (202.4 ft)"},
    "27 Langdon St, Montpelier, VT 05602":{"cl": "20.8 m (68.2 ft)",  "edge": "15.8 m (51.8 ft)"},
    "40 Main St, Montpelier, VT 05602":   {"cl": "57.7 m (189.3 ft)", "edge": "52.7 m (172.9 ft)"},
    "54 Elm St, Montpelier, VT 05602":    {"cl": "52.6 m (172.6 ft)", "edge": "47.6 m (156.2 ft)"},
}

ROOT = Path(__file__).parent
OUTPUT_DIR = ROOT / "building_details"
OUTPUT_DIR.mkdir(exist_ok=True)

# ── Pull column list and definitions straight from the xlsx ───────────────────
wb = openpyxl.load_workbook(ROOT / "Flood_DataInput.xlsx")
ws_attrs = wb["BuidlingAttributes"]
ALL_COLS = [c for c in next(ws_attrs.iter_rows(min_row=1, max_row=1, values_only=True)) if c is not None]

ws_info = wb["AttributeInfo"]
ATTR_INFO = {}   # col_name -> {defn, options}
for row in ws_info.iter_rows(min_row=2, values_only=True):
    if row[0]:
        ATTR_INFO[row[0]] = {
            "defn":    row[1] or "",
            "method":  row[2] or "",
            "options": row[4] or "",
        }

# ── Known values per building ─────────────────────────────────────────────────
# Sources:
#   - assessment JSON  (llmDamagev3 pipeline output)
#   - USGS HWM data    (table_JulyHWMs.csv)
#   - NRHP             (Montpelier Historic District, listed 1978)
#   - FEMA NFHL        (Zone AE for downtown Montpelier along N. Branch Winooski)
#   - Visual inspection from assessment before/after photos

USGS_NOTE = {
    "100 Main St, Montpelier, VT 05602":  "Nearest USGS HWM: MM-739 (Main St rotary), elev 526.33 ft, 1.7 ft above grade",
    "112 State St, Montpelier, VT 05602": "Nearest USGS HWM: MM-738 (State/Main intersection), elev 526.46 ft, 2.27 ft above grade",
    "27 Langdon St, Montpelier, VT 05602":"Nearest USGS HWM: MM-738 (State/Main intersection), elev 526.46 ft, 2.27 ft above grade",
    "40 Main St, Montpelier, VT 05602":   "Nearest USGS HWM: MM-739 (Main St rotary), elev 526.33 ft, 1.7 ft above grade",
    "54 Elm St, Montpelier, VT 05602":    "Nearest USGS HWM: MM-741 (Elm St Cemetery), elev 526.41 ft, 2.3 ft above grade",
}

NRHP_NOTE = "Part of Montpelier Historic District (NRHP 1978, amended 1989/2017). Individual resource # not confirmed — requires NPS database lookup."

# Common values shared by all 5 buildings
COMMON = {
    "town":                         "Montpelier",
    "FEMA_floodzone":               "Zone AE (1% annual chance / Special Flood Hazard Area)",
    "located_in_historic_district": "yes",
    "NRHP_ref_number":              "un — see notes",
    "national_register_listing_year": "1978 (district listing)",
    "building_urban_setting":       "row_middle",
    "building_position_on_street":  "right_on_street",
    "wall_thickness":               "0.46",   # ~18-inch brick load-bearing walls typical for late 19th c. commercial masonry; guide default 0.2 is for residential
    "construction_type_u":          "masonry_un",
    "construction_type_u_unc":      "2",
    "mwfrs_u_wall":                 "wall_diaphragm_masonry",
    "mwfrs_u_wall_unc":             "2",
    "mwfrs_u_roof":                 "un",
    "mwfrs_u_roof_unc":             "2",
    "mwfrs_u_moment_frame":         "no",
    "mwfrs_u_moment_frame_unc":     "2",
    "masonry_leaves":               "un",
    "masonry_leaves_unc":           "2",
    "structural_wall_system_u":     "solid_brick_wythe",
    "foundation_type_u":            "masonry_un",
    "foundation_type_u_unc":        "2",
    "wall_anchorage_type_u":        "un",
    "wall_anchorage_type_u_unc":    "2",
    "wall_substrate_u":             "un",
    "wall_substrate_u_unc":         "1",
    "wall_cladding_u":              "brick",
    "wall_cladding_u_unc":          "3",
    "soffit_type_u":                "not_applicable",
    "soffit_present_u":             "no",
    # Garage-door presence (per schema defn, not pedestrian entrance doors): "no" on the
    # street-facing side is confirmed by Street View (standard storefront glass entries,
    # no roll-up/sectional doors visible); side walls are party walls shared with adjoining
    # row buildings (building_urban_setting=row_middle/row_end) with no street/alley access,
    # so a garage door is structurally impossible there; no rear imagery exists, but garage
    # doors are not a feature of this building typology (19th-c. Main St commercial storefronts).
    "door_present_n":               "no",
    "door_present_n_lowerlevel":    "no",
    "door_present_s":               "no",
    "door_present_s_lowerlevel":    "no",
    "door_present_e":               "no",
    "door_present_e_lowerlevel":    "no",
    "door_present_w":               "no",
    "door_present_w_lowerlevel":    "no",
    "large_door_present_front":     "no",
    "large_door_present_back":      "no",
    "large_door_present_right":     "no",
    "large_door_present_left":      "no",
    # Fenestration by side: right/left are the party walls shared with adjoining row
    # buildings (no street/alley exposure), so 0% fenestration there, no protection
    # possible, type not_applicable. Back has no photographic coverage (Street View
    # only captured the street-facing front) -> left "un" rather than guessed. Front
    # values are per-building, from the Street View vision pass.
    "wall_fenesteration_right_per":            "0",
    "wall_fenesteration_right_lowerlevel_per":  "0",
    "wall_fenesteration_left_per":             "0",
    "wall_fenesteration_left_lowerlevel_per":   "0",
    "wall_fenesteration_back_per":             "un",
    "wall_fenesteration_back_lowerlevel_per":   "un",
    # Protection (boards/shutters/sandbags): checked the actual ref_photos/before and
    # ref_photos/after sets (July 2023 flood-period photos + Oct/Nov 2023 recovery photos)
    # for all 5 buildings, all available sides. No plywood, storm shutters, or sandbags
    # visible anywhere, on any side, before/during/after the flood — just ordinary glass
    # storefronts and (on 100 Main St, 112 State St) fixed decorative window shutters that
    # don't close over the glass. "no" below is from these photos, not inferred.
    "wall_fenesteration_protection_front":             "no",
    "wall_fenesteration_protection_front_lowerlevel":   "no",
    "wall_fenesteration_protection_back":               "no",
    "wall_fenesteration_protection_back_lowerlevel":    "no",
    "wall_fenesteration_protection_right":              "no",
    "wall_fenesteration_protection_right_lowerlevel":   "no",
    "wall_fenesteration_protection_left":               "no",
    "wall_fenesteration_protection_left_lowerlevel":    "no",
    "fenestration_protection_type_front":  "not_applicable",
    "fenestration_protection_type_back":   "not_applicable",
    "fenestration_protection_type_right":  "not_applicable",
    "fenestration_protection_type_left":   "not_applicable",
    "roof_shape_u":                 "flat",
    "roof_slope_u":                 "0",
    "roof_system_u":                "un",
    "roof_system_u_unc":            "2",
    "r2wall_attachment_u":          "un",
    "roof_substrate_type_u":        "un",
    "roof_substrate_type_u_unc":    "2",
    "roof_cover_u":                 "un",
    "roof_cover_u_unc":             "2",
    "overhang_length_u":            "0",
    "parapet_height_m":             "un",
    "raised_above_road_level":      "no",
    "raised_floor_elevation_ft":    "not_applicable",
    "retrofit_present_u":           "un",
    "retrofit_year_u":              "un",
    "retrofit_type_u":              "un",
    "retrofit_type_unc_u":          "un",
    "hazards_present_u":            "flood",
    "wind_damage_rating_u":         "0",
    "rainwater_ingress_damage_rating_u": "un",
    "rain_damage_details_u":        "Ground floor commercial space inundated; first-floor finishes, electrical, HVAC, and contents damaged by floodwater.",
    "wind_damage_details_u":        "No wind damage observed in before/after photos.",
    "roof_structure_damage_u":      "0",
    "roof_structure_damage_u_per":  "0",
    "roof_substrate_damage_u":      "0",
    "roof_substrate_damage_per":    "0",
    "foundation_failure_u":         "0",
    "foundation_failure_per_u":     "0",
    "foundation_damage_cause_u":    "not_applicable",
    "foundation_damage_u":          "0",
    "foundation_damage_u_per":      "0",
    "piles_damage_u":               "not_applicable",
    "soffit_damage_per_u":          "0",
    "fascia_damage_per_u":          "0",
    "risk_category_16":             "2",
    "building_low_rise":            "yes",
    "existed_during_flood":         "yes",
    "building_existed_during_flood":"yes",
    "building_in_use_during_flood": "no",
    "buidling_existed_5_yrs_before_flood": "yes",
    "buidling_existed_3_yrs_before_flood": "yes",
    "buidling_existed_1_yrs_before_flood": "yes",
    "damage_status":                "moderate",
    "status_u":                     "moderate",
    "degree_of_damage_u":           "2",
    "damage_indicator_u":           "2",
    "building_demolished_1_yrs_after_event": "no",
    "building_demolished_3_yrs_after_event": "no",
    "building_demolished_5_yrs_after_event": "no",
    "building_abandoned_1_yrs_after_event":  "no",
    "emergency_measures_only_performed_1_yrs_after_event": "un",
    "restoration_in_progress_1_yrs_after_event": "un",
    "single_unit":                  "no",
    "multiple_unit":                "yes",
    "world_heritage_property":      "no",
    "hague_convention":             "no",
    "iucn_protected_area":          "no",
    # wall/fenestration damage — flood only affects ground floor, no structural wall damage
    "wall_structure_damage_u":      "0",
    "wall_structure_damage_n":      "0",
    "wall_structure_damage_s":      "0",
    "wall_structure_damage_e":      "0",
    "wall_structure_damage_w":      "0",
    "wall_structure_damage_per_front": "0",
    "wall_structure_damage_per_back":  "0",
    "wall_structure_damage_per_left":  "0",
    "wall_structure_damage_per_right": "0",
    "wall_substrate_damage_u":      "un",
    # property classification
    "prop_agricultural ":           "no",
    "prop_cave":                    "no",
    "prop_culture _entertainment_facility": "no",
    "prop_forest":                  "no",
    "prop_industrial_facility":     "no",
    "prop_lake":                    "no",
    "prop_military":                "no",
    "prop_nature":                  "no",
    "prop_religious":               "no",
    "prop_rock formation":          "no",
    "prop_sports_facility":         "no",
    "prop_unilities_facility":      "no",
    "prop_archaeological":          "no",
    "prop_commemorative structure or landmark": "no",
    "prop_ecosystem":               "no",
    "prop_habitat":                 "no",
    "prop_infrastructure":          "no",
    "prop_law / government facility": "no",
    "prop_mine":                    "no",
    "prop_park / garden":           "no",
    "prop_residential facility":    "no",
    "prop_scenic area":             "no",
    "prop_transportation facility": "no",
    "prop_volcano":                 "no",
    "prop_battlefield":             "no",
    "prop_commercial / exchange facility": "yes",
    "prop_educational facility":    "no",
    "prop_health / welfare facility": "no",
    "prop_island(s)":               "no",
    "prop_marine zone":             "no",
    "prop_mountain":                "no",
    "prop_parking / storage facility": "no",
    "prop_river catchment system":  "no",
    "prop_sea":                     "no",
    "prop_underground facility":    "no",
    "prop_zoological park":         "no",
    # heritage value (based on being part of NRHP historic district)
    "prop_val_evidential":          "considerable",
    "prop_val_historical":          "considerable",
    "prop_val_aesthetic":           "considerable",
    "prop_val_communal":            "considerable",
    # ownership
    "owner_individual":             "un",
    "owner_government":             "no",
    "owner_ngo":                    "no",
    "owner_religious ":             "no",
    "owner_unknown":                "no",
    "owner_occupied":               "no",
    "tenant_occupied":              "yes",
    "demolishing_year":             "not_applicable",
    "buidling_use_plan_after_flood":"un",
}

BUILDINGS = {
    "100 Main St, Montpelier, VT 05602": {
        **COMMON,
        "complete address":          "100 Main St, Montpelier, VT 05602",
        "building_name_current":     "Three Penny Taproom (ground floor bar/restaurant)",
        "building_name_listing":     "un — within Montpelier Historic District",
        "flood_height_building":     "~3.5 ft above first floor (LLM estimate, high confidence)",
        # latitude/longitude/number_stories/front_elevation_orientation/buidling_height_m/
        # building_area_m2/wall_length_front+side/wall_fenesteration_front_per/
        # parapet_height_m/soffit_present_u/wall_fenestration_per_n,s,e,w are all injected
        # live in build_page() from building_attributes_auto.json + visual_attributes.json.
        "archetype":                 "7",  # F7: Small multi-unit commercial building (closest match; guide F7 is typically 1-story but no multi-story masonry option exists)
        "occupany_u":                "assembly",
        "year_built_u":              "c.1870–1890 (Italianate commercial era, downtown Montpelier)",
        "wall_fenesteration_front_lowerlevel_per": "30",  # storefront-level estimate from Street View (restaurant entry: glass door + display windows flanked by clapboard)
        # The following are judgment calls (enum classification), not pure value passthroughs,
        # so they stay manual rather than live-read - re-verify by hand if visual_attributes.json
        # is regenerated. Confirmed wood frame/clapboard via Street View, not masonry like COMMON assumes.
        "soffit_type_u":             "un",
        "wall_cladding_u":           "weatherboard",  # wood clapboard siding — overrides COMMON's brick default
        "construction_type_u":       "wood_frame",    # overrides COMMON's masonry_un default
        "structural_wall_system_u":  "wood_frame",
        "mwfrs_u_wall":              "wall_diaphragm_wood",
        "wall_thickness":            "0.15",  # ~6-in wood stud wall, typical for light wood-frame commercial construction; overrides COMMON's brick default
        "buidling_use_before_flood": "assembly",
        "buidling_use_after_flood":  "assembly",
        "building_use_during_flood": "assembly",
        "restoration_completed_building_in_use_1_yrs_after_event": "yes",
        "owner_business":            "yes",
        "sub_national_heritage _list": "yes",
        "property_of_local_significance": "yes",
        # LLM assessment
    },
    "112 State St, Montpelier, VT 05602": {
        **COMMON,
        "complete address":          "112 State St, Montpelier, VT 05602",
        # Critic HIGH: building is free-standing with arcade forecourt and landscaped setback —
        # not joined to adjacent buildings. COMMON's row_middle default is wrong here.
        "building_urban_setting":    "isolated",
        "building_position_on_street": "set_back_from_street",
        "building_name_current":     "112 State St — commercial/office with arcade ground floor",
        "building_name_listing":     "un — within Montpelier Historic District",
        "flood_height_building":     "~4.0 ft above first floor (LLM estimate, medium confidence)",
        "archetype":                 "14", # F14: Office building (best match for bank/commercial office use; no multi-story masonry commercial archetype in Nofal 2020)
        "occupany_u":                "business",
        "year_built_u":              "un — requires further research; commercial block likely late 19th–early 20th c.",
        "wall_fenesteration_front_lowerlevel_per": "55",  # Romanesque arched ground-floor glazing — heavily glazed arcade bays between brick piers
        # Before/after photos clearly show a steeply pitched metal mansard roof with dormers —
        # COMMON's "flat"/"0" defaults are wrong for this building. Slope is visually steep
        # (>30°) but not measurable from photos, so "un". The visible cornice band is at the
        # base of the mansard, not a true parapet above a flat deck, so parapet = "un".
        "roof_shape_u":              "mansard",
        "roof_slope_u":              "un",
        "parapet_height_m":          "un",
        "buidling_use_before_flood": "business",
        "buidling_use_after_flood":  "business",
        "building_use_during_flood": "business",
        "restoration_completed_building_in_use_1_yrs_after_event": "un",
        "owner_business":            "un",
        "sub_national_heritage _list": "yes",
        "property_of_local_significance": "yes",
    },
    "27 Langdon St, Montpelier, VT 05602": {
        **COMMON,
        "complete address":          "27 Langdon St, Montpelier, VT 05602",
        # Critic HIGH: corner/end position with exposed side walls on at least two sides.
        "building_urban_setting":    "row_end",
        "building_name_current":     "27 Langdon St — commercial (Langdon Street shopping area)",
        "building_name_listing":     "un — within Montpelier Historic District",
        "flood_height_building":     "~3.5 ft above first floor (LLM estimate, medium confidence)",
        "archetype":                 "7",  # F7: Small multi-unit commercial building
        "occupany_u":                "mercantile",
        "year_built_u":              "un — Langdon St commercial development likely late 19th c.",
        # Footprint is the 90 Main St tax parcel — this storefront's E911/parcel address is 90 Main St;
        # "27 Langdon St" is the shop's own Langdon-St-facing address. See LESSONS_LEARNED.md §2.
        "wall_fenesteration_front_lowerlevel_per": "60",  # Buch Spieler Records storefront — wide picture-window display glass on both sides of the entry, modest brick piers
        # Unlike the other 4 buildings, there is no back photo for 27 Langdon St at all
        # (neither before nor after) - override COMMON's photo-confirmed "no" back to "un"
        "wall_fenesteration_protection_back":              "un",
        "wall_fenesteration_protection_back_lowerlevel":   "un",
        "fenestration_protection_type_back":  "un",
        "parapet_height_m":          "0.6",
        "buidling_use_before_flood": "mercantile",
        "buidling_use_after_flood":  "mercantile",
        "building_use_during_flood": "mercantile",
        "restoration_completed_building_in_use_1_yrs_after_event": "un",
        "owner_business":            "un",
        "sub_national_heritage _list": "yes",
        "property_of_local_significance": "yes",
    },
    "40 Main St, Montpelier, VT 05602": {
        **COMMON,
        "complete address":          "40 Main St, Montpelier, VT 05602",
        "building_name_current":     "40 Main St — Aubuchon Hardware / Capitol Copy / Sherpa Dinner House",
        "building_name_listing":     "un — within Montpelier Historic District",
        "flood_height_building":     "~2.5 ft above first floor (LLM estimate, high confidence)",
        "archetype":                 "7",  # F7: Small multi-unit commercial building (hardware + restaurant + copy shop = multi-unit retail, F7 is closest)
        "occupany_u":                "mercantile",
        "year_built_u":              "un — commercial block likely late 19th–early 20th c.",
        "wall_fenesteration_front_lowerlevel_per": "35",  # Aubuchon Hardware / Capitol Copy / Sherpa Dinner House storefronts — standard Main St display windows under awnings
        "parapet_height_m":          "0.6",
        "buidling_use_before_flood": "mercantile",
        "buidling_use_after_flood":  "mercantile",
        "building_use_during_flood": "mercantile",
        "restoration_completed_building_in_use_1_yrs_after_event": "un",
        "owner_business":            "yes",
        "sub_national_heritage _list": "yes",
        "property_of_local_significance": "yes",
    },
    "54 Elm St, Montpelier, VT 05602": {
        **COMMON,
        "complete address":          "54 Elm St, Montpelier, VT 05602",
        # Critic MEDIUM: corner/end-of-row structure with exposed side wall (painted metal panels).
        "building_urban_setting":    "row_end",
        "building_name_current":     "54 Elm St — laundromat (ground floor, closed/vacant post-flood); brick commercial building",
        "building_name_listing":     "un — within Montpelier Historic District",
        "flood_height_building":     "~3.0 ft above first floor (LLM estimate, low confidence — indirect visual evidence only)",
        "archetype":                 "7",  # F7: Small multi-unit commercial building (laundromat ground floor + likely residential upper floors)
        "occupany_u":                "mercantile",
        "year_built_u":              "un — brick construction likely late 19th–early 20th c.",
        "wall_fenesteration_front_lowerlevel_per": "40",  # corrected from "un": the Street View frame was ambiguous, but ref_photos/after Oct 2023 photo clearly shows the storefront open ("EXPRESS LAUNDROMAT" / "WASH DRY FOLD" signage) with two display windows + two glazed entry doors - not boarded/vacant as the existing building_name_current field assumes
        "soffit_type_u":             "un",
        "buidling_use_before_flood": "mercantile",
        "buidling_use_after_flood":  "not in use",
        "building_use_during_flood": "mercantile",
        "restoration_completed_building_in_use_1_yrs_after_event": "un",
        "owner_business":            "un",
        "sub_national_heritage _list": "yes",
        "property_of_local_significance": "yes",
    },
}

# ── Section groupings (spreadsheet column order) ─────────────────────────────
SECTIONS = [
    ("Building Identity & Location",
     ["NRHP_ref_number", "national_register_listing_year", "located_in_historic_district",
      "building_name_listing", "building_name_current", "complete address",
      "town", "latitude", "longitude",
      "sub_national_heritage _list", "property_of_local_significance",
      "world_heritage_property", "hague_convention", "iucn_protected_area"]),

    ("Site & Flood Context",
     ["FEMA_floodzone", "flood_height_building",
      "distance_from_river_edge", "distance_from_river_centerline",
      "hazards_present_u"]),

    ("Building Characteristics",
     ["archetype", "occupany_u", "number_stories", "year_built_u",
      "building_area_m2", "building_urban_setting", "building_position_on_street",
      "buidling_height_m", "first_floor_elevation_m", "front_elevation_orientation",
      "wall_length_side", "wall_length_front", "wall_thickness",
      "risk_category_16", "building_low_rise"]),

    ("Roof",
     ["roof_shape_u", "roof_slope_u", "roof_system_u", "roof_system_u_unc",
      "r2wall_attachment_u", "roof_substrate_type_u", "roof_substrate_type_u_unc",
      "roof_cover_u", "roof_cover_u_unc", "overhang_length_u", "parapet_height_m"]),

    ("Wall & Structural Systems",
     ["construction_type_u", "construction_type_u_unc",
      "mwfrs_u_wall", "mwfrs_u_wall_unc",
      "mwfrs_u_roof", "mwfrs_u_roof_unc",
      "mwfrs_u_moment_frame", "mwfrs_u_moment_frame_unc",
      "masonry_leaves", "masonry_leaves_unc",
      "structural_wall_system_u",
      "foundation_type_u", "foundation_type_u_unc",
      "wall_anchorage_type_u", "wall_anchorage_type_u_unc",
      "wall_substrate_u", "wall_substrate_u_unc",
      "wall_cladding_u", "wall_cladding_u_unc",
      "wall_thickness"]),

    ("Soffit & Openings",
     ["soffit_type_u", "soffit_present_u",
      "raised_above_road_level", "raised_floor_elevation_ft",
      "large_door_present_front", "large_door_present_back",
      "large_door_present_right", "large_door_present_left",
      "door_present_n", "door_present_n_lowerlevel",
      "door_present_s", "door_present_s_lowerlevel",
      "door_present_e", "door_present_e_lowerlevel",
      "door_present_w", "door_present_w_lowerlevel"]),

    ("Fenestration — Percentage by Face",
     ["wall_fenesteration_front_per", "wall_fenesteration_front_lowerlevel_per",
      "wall_fenesteration_back_per", "wall_fenesteration_back_lowerlevel_per",
      "wall_fenesteration_right_per", "wall_fenesteration_right_lowerlevel_per",
      "wall_fenesteration_left_per", "wall_fenesteration_left_lowerlevel_per",
      "wall_fenestration_per_north", "wall_fenestration_per_south",
      "wall_fenestration_per_e", "wall_fenestration_per_w"]),

    ("Fenestration — Protection",
     ["wall_fenesteration_protection_front", "wall_fenesteration_protection_front_lowerlevel",
      "wall_fenesteration_protection_back", "wall_fenesteration_protection_back_lowerlevel",
      "wall_fenesteration_protection_right", "wall_fenesteration_protection_right_lowerlevel",
      "wall_fenesteration_protection_left", "wall_fenesteration_protection_left_lowerlevel",
      "fenestration_protection_type_front", "fenestration_protection_type_back",
      "fenestration_protection_type_right", "fenestration_protection_type_left"]),

    ("Retrofit",
     ["retrofit_present_u", "retrofit_year_u", "retrofit_type_u", "retrofit_type_unc_u"]),

    ("LLM Flood Damage Assessment (v3 pipeline)",
     ["_llm_damage_level", "_llm_confidence", "_llm_water_depth_ft",
      "_llm_reasoning", "_llm_limitations"]),

    ("Overall Damage Status",
     ["status_u", "damage_status", "degree_of_damage_u", "damage_indicator_u",
      "rainwater_ingress_damage_rating_u", "wind_damage_rating_u",
      "rain_damage_details_u", "wind_damage_details_u"]),

    ("Damage — Roof",
     ["roof_structure_damage_u", "roof_structure_damage_u_per",
      "roof_substrate_damage_u", "roof_substrate_damage_per"]),

    ("Damage — Foundation & Piles",
     ["foundation_failure_u", "foundation_failure_per_u",
      "foundation_damage_cause_u", "foundation_damage_u", "foundation_damage_u_per",
      "piles_damage_u"]),

    ("Damage — Wall Structure",
     ["wall_structure_damage_u",
      "wall_structure_damage_per_front", "wall_structure_damage_per_back",
      "wall_structure_damage_per_left", "wall_structure_damage_per_right",
      "wall_structure_damage_n", "wall_structure_damage_s",
      "wall_structure_damage_e", "wall_structure_damage_w"]),

    ("Damage — Wall Substrate",
     ["wall_substrate_damage_u",
      "wall_substrate_damage_per_front", "wall_substrate_damage_per_back",
      "wall_substrate_damage_per_right", "wall_substrate_damage_per_left",
      "wall_substrate_damage_n", "wall_substrate_damage_s",
      "wall_substrate_damage_e", "wall_substrate_damage_w"]),

    ("Damage — Wall Cladding",
     ["wall_cladding_damage_per_front", "wall_cladding_damage_per_back",
      "wall_cladding_damage_per_right", "wall_cladding_damage_per_left",
      "wall_cladding_damage_n", "wall_cladding_damage_s",
      "wall_cladding_damage_e", "wall_cladding_damage_w"]),

    ("Damage — Fenestration",
     ["damaged_fenesteration_per_front", "damaged_fenesteration_per_back",
      "damaged_fenesteration_per_right", "damaged_fenesteration_per_left",
      "wall_fenestration_damage_per_n", "wall_fenestration_damage_per_s",
      "wall_fenestration_damage_per_e", "wall_fenestration_damage_per_w",
      "soffit_damage_per_u", "fascia_damage_per_u"]),

    ("Building Use & Occupancy",
     ["buidling_use_before_flood", "buidling_use_after_flood",
      "buidling_use_plan_after_flood", "building_use_during_flood",
      "building_in_use_during_flood",
      "single_unit", "multiple_unit"]),

    ("Existence & Temporal Status",
     ["existed_during_flood", "building_existed_during_flood",
      "buidling_existed_5_yrs_before_flood", "buidling_existed_3_yrs_before_flood",
      "buidling_existed_1_yrs_before_flood",
      "demolishing_year",
      "building_demolished_1_yrs_after_event",
      "building_demolished_3_yrs_after_event",
      "building_demolished_5_yrs_after_event",
      "building_abandoned_1_yrs_after_event",
      "emergency_measures_only_performed_1_yrs_after_event",
      "restoration_in_progress_1_yrs_after_event",
      "restoration_completed_building_in_use_1_yrs_after_event"]),

    ("Heritage & Property Classification",
     ["sub_national_heritage _list", "property_of_local_significance",
      "prop_val_evidential", "prop_val_historical",
      "prop_val_aesthetic", "prop_val_communal",
      "prop_agricultural ", "prop_cave", "prop_culture _entertainment_facility",
      "prop_forest", "prop_industrial_facility", "prop_lake", "prop_military",
      "prop_nature", "prop_religious", "prop_rock formation", "prop_sports_facility",
      "prop_unilities_facility", "prop_archaeological",
      "prop_commemorative structure or landmark", "prop_ecosystem", "prop_habitat",
      "prop_infrastructure", "prop_law / government facility", "prop_mine",
      "prop_park / garden", "prop_residential facility", "prop_scenic area",
      "prop_transportation facility", "prop_volcano", "prop_battlefield",
      "prop_commercial / exchange facility", "prop_educational facility",
      "prop_health / welfare facility", "prop_island(s)", "prop_marine zone",
      "prop_mountain", "prop_parking / storage facility",
      "prop_river catchment system", "prop_sea", "prop_underground facility",
      "prop_zoological park"]),

    ("Ownership",
     ["owner_individual", "owner_business", "owner_government",
      "owner_ngo", "owner_religious ", "owner_unknown",
      "owner_occupied", "tenant_occupied"]),
]

# ── Inline notes specific to certain attributes ───────────────────────────────
NOTES = {
    "NRHP_ref_number":              NRHP_NOTE,
    "national_register_listing_year": "1978 is the Montpelier Historic District listing year. " + NRHP_NOTE,
    "flood_height_building":        "Primary source: USGS High Water Mark survey (July 2023). LLM estimate from photo analysis. Point-cloud not available.",
    "distance_from_river_edge":     "Computed from USGS NHD centerline minus estimated 5m half-width of N. Branch Winooski. Source: hydro.nationalmap.gov NHD Large-Scale Flowlines, UTM 19N (EPSG:32619). NHD does not provide a polygon for this reach.",
    "distance_from_river_centerline":"Computed from USGS NHD Large-Scale Flowlines (N. Branch Winooski River + Winooski River merged centerline). UTM 19N (EPSG:32619). Source: hydro.nationalmap.gov/arcgis/rest/services/nhd/MapServer/6.",
    "FEMA_floodzone":               "Downtown Montpelier along N. Branch Winooski is Zone AE. Verify exact parcel at FEMA Map Service Center.",
    "latitude":                     "Nominatim-geocoded (collect_building_attributes.py). Not an approximate centroid.",
    "longitude":                    "Nominatim-geocoded (collect_building_attributes.py). Not an approximate centroid.",
    "front_elevation_orientation":  "OSM-derived (compute_urban_attrs.py): direction from building centroid to nearest named road segment matching the address street. Replaces LLM Street View estimate.",
    "building_urban_setting":       "Photo-grounded correction (critic_findings.json) where flagged; OSM adjacency (compute_urban_attrs.py) otherwise. OSM building polygon coverage in Montpelier VT is incomplete, limiting algorithm reliability.",
    "buidling_height_m":            "Formula-derived from CV story count (H7 ensemble): 4.0 m ground floor + 3.5 m per upper floor. Replaces LLM visual estimate.",
    "building_low_rise":            "Derived from CV story count (H7): low-rise = ≤4 stories per schema definition.",
    "archetype":                    "Assigned from Nofal & van de Lindt (2020) 15-type portfolio. None of the 15 archetypes is a perfect match for a multi-story historic masonry downtown commercial block — F7 (7, small multi-unit commercial) is the closest for retail/mixed-use; F14 (14, office) for primarily office use. Certainty is inherently low for all 5 buildings.",
    "wall_thickness":               "0.46 m assumed (~18 inches) for late 19th c. load-bearing brick commercial construction. Guide default of 0.2 m is for residential wood frame — not appropriate here. Historic masonry commercial buildings typically 12–24 inches (0.30–0.61 m). Use 0.46 as a mid-range estimate; refine from damage photos or historic drawings.",
    "construction_type_u_unc":      "Certainty 2 (35–50%) — brick visible in exterior photos but interior structure not confirmed from drawings.",
    "mwfrs_u_wall_unc":             "Certainty 2 — approximated from construction type.",
    "wall_cladding_u_unc":          "Certainty 3 (>75%) — brick clearly visible in assessment photos.",
    # NOTE: the four notes above (wall_thickness, construction_type_u_unc, wall_cladding_u_unc,
    # soffit_present_u below) describe the brick/masonry/no-soffit majority case. 100 Main St is
    # wood frame, not brick, and both 100 Main St and 54 Elm St have a soffit -- see
    # NOTES_OVERRIDE below, checked first in build_page() before falling back to these.
    "prop_val_evidential":          "Assessed as 'considerable' based on NRHP contributing status in historic district.",
    "prop_val_historical":          "Assessed as 'considerable' based on NRHP historic district listing.",
    "prop_val_aesthetic":           "Assessed as 'considerable' — 19th–early 20th c. commercial architecture in intact historic streetscape.",
    "prop_val_communal":            "Assessed as 'considerable' — active commercial ground floors serving Montpelier community.",
    "_llm_damage_level":            "Output from llmDamagev3 pipeline (claude-sonnet-4-6). Clean rewrite — no ground truth in prompt; outputs in address_assessments.json.",
    "_llm_reasoning":               "Generated by claude-sonnet-4-6 from before/after photo analysis only. No HWM, historic context, or ground truth provided to model.",
    "rain_damage_details_u":        "Flood damage only (not rain/wind). Inundation of ground-floor commercial spaces per LLM assessment.",
    "wind_damage_rating_u":         "0 — no wind damage observed. Event was riverine flood only.",
    "wall_structure_damage_u":      "0 — no structural wall damage observed. Flood damage is finishes/contents only based on photos.",
    "roof_structure_damage_u":      "0 — no roof damage observed in post-flood photos.",
    "foundation_failure_u":         "0 — no foundation failure observed. Riverine flood, not scour event.",
    "soffit_present_u":             "No soffit — historic commercial masonry blocks in Montpelier typically have flat facades with no eaves/soffit.",
    "mwfrs_u_moment_frame":         "No moment frame expected for historic unreinforced masonry commercial construction.",
    "building_in_use_during_flood": "Buildings were closed during the July 2023 flood event.",
}

# Per-address exceptions to the NOTES above, checked first in build_page(). Needed because
# NOTES is one note per attribute for all 5 buildings, but a few buildings are real exceptions
# to the brick/masonry/no-soffit majority case (found during the 2026-06-24 audit).
NOTES_OVERRIDE = {
    "latitude": {
        "27 Langdon St, Montpelier, VT 05602": "Nominatim-geocoded; independently verified against VT state E911 address points (see LESSONS_LEARNED.md §2) — E911 lists this storefront under 90 Main St (its tax-parcel address), not 27 Langdon St.",
    },
    "longitude": {
        "27 Langdon St, Montpelier, VT 05602": "Same as latitude — Nominatim-geocoded, verified against VT state E911 address points; E911 parcel address is 90 Main St.",
    },
    "wall_thickness": {
        "100 Main St, Montpelier, VT 05602": "0.15 m assumed (~6 inches) for light wood-frame construction — this building is wood frame with clapboard siding, not masonry. See construction_type_u.",
    },
    "construction_type_u_unc": {
        "100 Main St, Montpelier, VT 05602": "Certainty 3 (>75%) — wood clapboard siding clearly visible in two independent Street View passes; not brick/masonry like the other 4 buildings.",
    },
    "wall_cladding_u_unc": {
        "100 Main St, Montpelier, VT 05602": "Certainty 3 (>75%) — wood clapboard siding clearly visible, confirmed via Street View.",
    },
    "soffit_present_u": {
        "100 Main St, Montpelier, VT 05602": "Soffit present per the Street View vision pass — an exception to the other 4 buildings' flat facades with no eaves.",
        "54 Elm St, Montpelier, VT 05602":   "Soffit present per the Street View vision pass — an exception to the other 4 buildings' flat facades with no eaves.",
    },
}

RESULTS_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: system-ui, sans-serif; background: #f4f4f0; color: #1a1a1a; padding: 2rem; }
h1 { font-size: 1.4rem; font-weight: 600; margin-bottom: 0.25rem; }
.subtitle { color: #666; font-size: 0.85rem; margin-bottom: 2rem; }
.card { background: white; border-radius: 8px; border: 1px solid #e0e0d8;
        padding: 1.25rem 1.5rem; margin-bottom: 1rem; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
.card-header { display: flex; align-items: baseline; gap: 1rem; margin-bottom: 0.75rem; }
.address { font-weight: 600; font-size: 1rem; }
.address a { color: inherit; text-decoration: none; border-bottom: 1px dashed #aaa; }
.address a:hover { border-bottom-color: #333; }
.badge { display: inline-block; font-size: 0.75rem; font-weight: 600;
         padding: 0.2em 0.6em; border-radius: 4px; white-space: nowrap; }
.level-0 { background: #e8f5e9; color: #2e7d32; }
.level-1 { background: #fff8e1; color: #f57f17; }
.level-2 { background: #fff3e0; color: #e65100; }
.level-3 { background: #fce4ec; color: #b71c1c; }
.level-4 { background: #311b92; color: #fff; }
.level-null { background: #eeeeee; color: #555; }
.conf-high   { background: #e3f2fd; color: #0d47a1; }
.conf-medium { background: #fafafa; color: #555; border: 1px solid #ddd; }
.conf-low    { background: #fce4ec; color: #880e4f; }
.meta { display: flex; gap: 0.5rem; flex-wrap: wrap; margin-bottom: 0.75rem; }
.meta-item { font-size: 0.78rem; color: #666; background: #f9f9f7; border: 1px solid #e8e8e0;
             border-radius: 4px; padding: 0.15em 0.5em; }
.label { font-size: 0.7rem; font-weight: 700; text-transform: uppercase;
         letter-spacing: 0.05em; color: #888; margin-bottom: 0.25rem; margin-top: 0.75rem; }
.reasoning { font-size: 0.88rem; line-height: 1.55; color: #333; }
.limitations { font-size: 0.82rem; line-height: 1.5; color: #777; font-style: italic; }
.damage-label { font-size: 0.8rem; font-weight: 500; color: #555; margin-left: 0.25rem; }
"""

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: system-ui, sans-serif; background: #f4f4f0; color: #1a1a1a; padding: 2rem; max-width: 1100px; }
h1 { font-size: 1.15rem; font-weight: 600; margin-bottom: 0.2rem; }
.meta { color: #777; font-size: 0.82rem; margin-bottom: 1.75rem; }
.meta a { color: #555; text-decoration: none; border-bottom: 1px solid #ccc; }
.legend { color: #999; font-size: 0.74rem; margin-bottom: 1.25rem; }
.section { margin-bottom: 1.25rem; }
.section-title { font-size: 0.68rem; font-weight: 700; text-transform: uppercase;
                 letter-spacing: 0.07em; color: #999; margin-bottom: 0.4rem;
                 padding: 0.3rem 0.5rem; background: #eeeee8; border-radius: 3px; }
table { width: 100%; border-collapse: collapse; background: white;
        border: 1px solid #e0e0d8; border-radius: 5px; overflow: hidden;
        font-size: 0.8rem; margin-bottom: 0; }
th, td { padding: 0.4rem 0.65rem; text-align: left; border-bottom: 1px solid #f2f2ed; vertical-align: top; }
thead th { font-size: 0.68rem; font-weight: 700; color: #888; background: #fafaf8;
           text-transform: uppercase; letter-spacing: 0.04em; }
td.attr { font-family: monospace; font-size: 0.75rem; color: #444; width: 24%; white-space: nowrap; }
td.value { width: 32%; color: #1a1a1a; }
td.defn { font-size: 0.73rem; color: #555; width: 22%; }
td.notes-col { font-size: 0.72rem; color: #888; font-style: italic; width: 22%; }
tr:last-child td { border-bottom: none; }
.un { color: #bbb; }
.llm-row { background: #fffdf0; }
.llm-row td.attr { color: #8a6a00; }
.badge-high   { color: #1565c0; font-weight: 600; }
.badge-medium { color: #6a6a00; font-weight: 600; }
.badge-low    { color: #b71c1c; font-weight: 600; }
.badge-level { display:inline-block; font-weight:600; padding:0.1em 0.45em;
               border-radius:3px; font-size:0.9em; }
.badge-0 { background:#e8f5e9; color:#2e7d32; }
.badge-1 { background:#f1f8e9; color:#558b2f; }
.badge-2 { background:#fff3e0; color:#e65100; }
.badge-3 { background:#fbe9e7; color:#bf360c; }
.badge-4 { background:#ffebee; color:#b71c1c; }
.flag-high   { background: #fdecea; }
.flag-high   td.attr { color: #b71c1c; }
.flag-medium { background: #fff8e1; }
.flag-medium td.attr { color: #e65100; }
.flag-low    { background: #f5f5f5; }
.flag-low    td.attr { color: #666; }
.critic-note { display: block; font-style: normal; font-weight: 600; margin-bottom: 0.25rem; }
.critic-note.sev-high   { color: #b71c1c; }
.critic-note.sev-medium { color: #e65100; }
.critic-note.sev-low    { color: #666; }
"""

def safe_filename(address):
    return address.split(",")[0].replace(" ", "_").replace("/", "-") + ".html"

def fmt(key, val):
    if val in (None, "", "un"):
        return '<span class="un">un</span>'
    if key == "_llm_confidence":
        return f'<span class="badge-{val}">{val}</span>'
    if key == "_llm_damage_level":
        level = str(val).split()[0]  # "2 (Moderate)" -> "2"
        return f'<span class="badge-level badge-{level}">{val}</span>'
    return str(val)

def resolve_building_data(address: str, data: dict) -> dict:
    """Merge the hardcoded BUILDINGS/COMMON entry for `address` with the three live
    JSON sources (ASSESSMENTS, BUILDING_ATTRS, VISUAL_ATTRS) plus computed fields
    (river distance, wall lengths, per-cardinal fenestration) -- the exact record
    build_page() renders, exposed standalone so critic.py can audit it without
    duplicating this merge logic.
    """
    # Inject computed river distances
    rd = RIVER_DISTANCES.get(address, {})
    data = dict(data)
    if rd:
        data["distance_from_river_centerline"] = rd["cl"]
        data["distance_from_river_edge"] = rd["edge"]

    # Inject the LLM damage assessment live from address_assessments.json (run_pipeline.py's
    # output) rather than a hand-copied snapshot, so re-running the pipeline stays in sync.
    a = ASSESSMENTS.get(address, {})
    level = a.get("damage_level")
    if level is not None and a.get("assessable", True):
        label = _DAMAGE_LABELS.get(level, "?")
        data["_llm_damage_level"] = f"{level} ({label})"
    data["_llm_confidence"]     = a.get("confidence", "un")
    data["_llm_water_depth_ft"] = a.get("estimated_water_depth_ft", "un")
    data["_llm_reasoning"]      = a.get("reasoning", "un")
    data["_llm_limitations"]    = a.get("limitations", "un")

    # Inject geo/footprint attributes live from collect_building_attributes.py's output.
    ba = BUILDING_ATTRS.get(address, {})
    if ba.get("latitude") is not None:
        data["latitude"]  = str(ba["latitude"])
        data["longitude"] = str(ba["longitude"])
    if ba.get("building_area_m2") is not None:
        data["building_area_m2"] = str(ba["building_area_m2"])

    # Inject vision-pass attributes live from analyze_visual_attributes.py's output.
    va = VISUAL_ATTRS.get(address, {})
    orientation = va.get("front_elevation_orientation")
    if orientation:
        orientation = orientation.lower()
        data["front_elevation_orientation"] = orientation
    if va.get("number_stories") is not None:
        data["number_stories"] = str(va["number_stories"])
    if va.get("buidling_height_m") is not None:
        data["buidling_height_m"] = str(va["buidling_height_m"])
    if va.get("parapet_height_m") is not None:
        data["parapet_height_m"] = str(va["parapet_height_m"])
    if va.get("soffit_present_u") is not None:
        data["soffit_present_u"] = "yes" if va["soffit_present_u"] else "no"
    front_per = va.get("wall_fenesteration_front_per")
    if front_per is not None:
        data["wall_fenesteration_front_per"] = str(front_per)

    # Override orientation from OSM-derived compute_urban_attrs.py output.
    # Must happen before the wall_length / cardinal-fenestration computation below so
    # those derived fields stay consistent with the corrected orientation.
    ua = URBAN_ATTRS.get(address, {})
    if ua.get("front_elevation_orientation"):
        orientation = ua["front_elevation_orientation"]
        data["front_elevation_orientation"] = orientation
    # building_urban_setting is handled via BUILDINGS dict overrides; urban_attrs.json
    # stores null for all buildings since OSM adjacency detection is unreliable here.

    # Derive building_low_rise and buidling_height_m from CV story count (H7 ensemble).
    # Formula: 4.0 m commercial ground floor + 3.5 m per upper floor.
    _n_str = data.get("number_stories")
    if _n_str is not None:
        try:
            _n = int(_n_str)
            data["building_low_rise"] = "yes" if _n <= 4 else "no"
            data["buidling_height_m"] = str(round(4.0 + (_n - 1) * 3.5, 1))
        except (ValueError, TypeError):
            pass

    # wall_length_front/side and the n/s/e/w fenestration columns are mechanical functions
    # of orientation + the a/b footprint extents — compute rather than hand-copy, so a
    # changed orientation or footprint always stays self-consistent with these.
    a_ns, b_ew = ba.get("approx_wall_length_a_m"), ba.get("approx_wall_length_b_m")
    if orientation and a_ns is not None and b_ew is not None:
        if orientation in ("n", "s"):
            front_len, side_len = b_ew, a_ns
        else:  # e, w
            front_len, side_len = a_ns, b_ew
        data["wall_length_front"] = str(front_len)
        data["wall_length_side"]  = str(side_len)
    if orientation:
        cardinals = ["n", "s", "e", "w"]
        opposite = {"n": "s", "s": "n", "e": "w", "w": "e"}
        for c in cardinals:
            if c == orientation:
                data[f"wall_fenestration_per_{c}"] = data.get("wall_fenesteration_front_per", "un")
            elif c == opposite[orientation]:
                data[f"wall_fenestration_per_{c}"] = data.get("wall_fenesteration_back_per", "un")
            else:
                data[f"wall_fenestration_per_{c}"] = "0"  # the two sides, party walls
    return data


def iter_populated_fields(address: str, data: dict):
    """Yield (key, value, defn, options, note) for every field with a real value
    (not None/""/"un"), in spreadsheet order -- same traversal build_page() uses to
    render rows, but skipping unset fields since there's nothing to critique there.
    """
    covered = set()
    for _sec_name, keys in SECTIONS:
        for k in keys:
            covered.add(k)
            val = data.get(k, "un")
            if val in (None, "", "un"):
                continue
            info = ATTR_INFO.get(k, {})
            note = NOTES_OVERRIDE.get(k, {}).get(address) or NOTES.get(k, "")
            yield (k, str(val), info.get("defn", ""), info.get("options", ""), note)
    for k, v in data.items():
        if k in covered or k.startswith("_") or v in (None, "", "un"):
            continue
        info = ATTR_INFO.get(k, {})
        yield (k, str(v), info.get("defn", ""), info.get("options", ""), NOTES.get(k, ""))


def build_page(address, data):
    data = resolve_building_data(address, data)
    covered = set()
    sections_html = ""

    for sec_name, keys in SECTIONS:
        rows = ""
        for k in keys:
            covered.add(k)
            val  = data.get(k, "un")
            info = ATTR_INFO.get(k, {})
            defn = info.get("defn", "")
            opts = info.get("options", "")
            note = NOTES_OVERRIDE.get(k, {}).get(address) or NOTES.get(k, "")
            # append options to defn if useful
            if opts and "options:" not in defn.lower():
                defn_display = f"{defn}<br><span style='color:#aaa'>Options: {opts}</span>" if defn else f"Options: {opts}"
            else:
                defn_display = defn
            finding = CRITIC_FINDINGS.get(address, {}).get(k)
            classes = []
            if k.startswith("_llm"):
                classes.append("llm-row")
            if finding:
                classes.append(f"flag-{finding['severity']}")
            cls_attr = f' class="{" ".join(classes)}"' if classes else ""
            note_html = note
            if finding:
                note_html = (f'<span class="critic-note sev-{finding["severity"]}">'
                              f'⚠ CRITIC ({finding["severity"]}): {finding["issue"]}</span>'
                              + note)
            rows += f"""
            <tr{cls_attr}>
              <td class="attr">{k}</td>
              <td class="value">{fmt(k, val)}</td>
              <td class="defn">{defn_display}</td>
              <td class="notes-col">{note_html}</td>
            </tr>"""
        if rows:
            sections_html += f"""
      <div class="section">
        <div class="section-title">{sec_name}</div>
        <table>
          <thead><tr>
            <th>Attribute</th><th>Value</th><th>Definition / Options</th><th>Notes on this entry</th>
          </tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </div>"""

    # Catch anything in BUILDINGS data not in any section
    extra = {k: v for k, v in data.items() if k not in covered and not k.startswith("_")}
    if extra:
        def _extra_row(k, v):
            finding = CRITIC_FINDINGS.get(address, {}).get(k)
            cls_attr = f' class="flag-{finding["severity"]}"' if finding else ""
            note_html = NOTES.get(k, "")
            if finding:
                note_html = (f'<span class="critic-note sev-{finding["severity"]}">'
                              f'⚠ CRITIC ({finding["severity"]}): {finding["issue"]}</span>'
                              + note_html)
            return (f'<tr{cls_attr}><td class="attr">{k}</td>'
                    f'<td class="value">{fmt(k, v)}</td>'
                    f'<td class="defn">{ATTR_INFO.get(k,{}).get("defn","")}</td>'
                    f'<td class="notes-col">{note_html}</td></tr>')
        rows = "".join(_extra_row(k, v) for k, v in extra.items())
        sections_html += f"""
      <div class="section">
        <div class="section-title">Additional Attributes</div>
        <table><thead><tr>
          <th>Attribute</th><th>Value</th><th>Definition</th><th>Notes</th>
        </tr></thead><tbody>{rows}</tbody></table>
      </div>"""

    usgs = USGS_NOTE.get(address, "")
    title = data.get("building_name_current", address)
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{address} — Building Attributes</title>
<style>{CSS}</style>
</head>
<body>
<p class="meta"><a href="../results.html">← Back to all assessments</a></p>
<h1>{address}</h1>
<p class="meta">{title} &nbsp;·&nbsp; Montpelier VT July 2023 flood &nbsp;·&nbsp; llmDamage v3 &nbsp;·&nbsp; {usgs}</p>
<p class="legend">Key (from the spreadsheet's AbbreviationsKey sheet): <b>un</b> = unknown, but might exist &nbsp;·&nbsp;
an <b>_unc</b> suffix is a certainty rating: <b>1</b> = 0–25%, <b>2</b> = 35–50%, <b>3</b> = &gt;75%</p>
{sections_html}
</body>
</html>"""
    out = OUTPUT_DIR / safe_filename(address)
    out.write_text(html)
    return str(out)

DAMAGE_LABELS_JS = {0: "No Damage", 1: "Minor", 2: "Moderate", 3: "Major", 4: "Destroyed"}

def build_results_page(manifest: dict) -> None:
    """Render results.html server-side from ASSESSMENTS, live - this used to be a
    hand-embedded copy of address_assessments.json's content with no generator at all,
    so it would silently go stale the moment any building was reassessed."""
    cards = ""
    for address in BUILDINGS:
        row = ASSESSMENTS.get(address, {})
        lvl = row.get("damage_level")
        conf = row.get("confidence") or "low"
        level_class = f"level-{lvl}" if lvl is not None else "level-null"
        depth = row.get("estimated_water_depth_ft")
        depth_str = f"~{depth} ft water depth" if depth is not None else "depth unknown"
        link = manifest.get(address)
        address_html = f'<a href="{link}" target="_blank">{address}</a>' if link else address
        cards += f"""
    <div class="card">
      <div class="card-header">
        <span class="address">{address_html}</span>
        <span class="badge {level_class}">Level {lvl if lvl is not None else '?'}</span>
        <span class="damage-label">{DAMAGE_LABELS_JS.get(lvl, 'Not Assessable')}</span>
        <span class="badge conf-{conf}">{conf} confidence</span>
      </div>
      <div class="meta">
        <span class="meta-item">{depth_str}</span>
        <span class="meta-item">{row.get('before_photo_count', '?')} before · {row.get('after_photo_count', '?')} after</span>
        <span class="meta-item">{'assessable' if row.get('assessable') else 'not assessable'}</span>
      </div>
      <div class="label">Reasoning</div>
      <div class="reasoning">{row.get('reasoning', 'un')}</div>
      <div class="label">Limitations</div>
      <div class="limitations">{row.get('limitations', 'un')}</div>
    </div>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>llmDamage v3 — Assessment Results</title>
<style>
{RESULTS_CSS}
</style>
</head>
<body>
<h1>llmDamage v3 — Flood Damage Assessment Results</h1>
<p class="subtitle">Montpelier, VT · model: claude-sonnet-4-6 · {len(BUILDINGS)} addresses assessed</p>
{cards}
</body>
</html>"""
    (ROOT / "results.html").write_text(html)

if __name__ == "__main__":
    manifest = {}
    for address, data in BUILDINGS.items():
        path = build_page(address, data)
        manifest[address] = f"building_details/{safe_filename(address)}"
        print(f"  {path}")
    (ROOT / "building_detail_manifest.json").write_text(json.dumps(manifest, indent=2))
    build_results_page(manifest)
    print(f"  {ROOT / 'results.html'}")
    print(f"\nDone — {len(manifest)} pages, all 177 columns covered.")
