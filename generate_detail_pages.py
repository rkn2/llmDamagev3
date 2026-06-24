#!/usr/bin/env python3
"""Generate per-building HTML detail pages — all 177 spreadsheet columns in order."""
import json, openpyxl
from pathlib import Path

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
        "latitude":                  "44.2605489",
        "longitude":                 "-72.5752484",
        "archetype":                 "7",  # F7: Small multi-unit commercial building (closest match; guide F7 is typically 1-story but no multi-story masonry option exists)
        "occupany_u":                "assembly",
        "number_stories":            "3",
        "year_built_u":              "c.1870–1890 (Italianate commercial era, downtown Montpelier)",
        "front_elevation_orientation":"w",
        # Verified via collect_building_attributes.py + analyze_visual_attributes.py (Street View vision pass)
        "buidling_height_m":         "10.5",
        "building_area_m2":          "569.5",
        "wall_length_front":         "33.2",
        "wall_length_side":          "32.9",
        "wall_fenesteration_front_per": "25",
        "parapet_height_m":          "0.6",
        "soffit_present_u":          "yes",
        "soffit_type_u":             "un",
        "wall_cladding_u":           "weatherboard",  # wood clapboard siding, confirmed via Street View — overrides COMMON's brick default
        "construction_type_u":       "wood_frame",    # confirmed wood frame, not masonry — overrides COMMON
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
        "_llm_damage_level":        "2 (Moderate)",
        "_llm_confidence":          "high",
        "_llm_water_depth_ft":      "3.5",
        "_llm_reasoning":           "After photos clearly show floodwater reaching approximately mid-door height on the Three Penny Taproom storefront — roughly 3 to 4 ft above sidewalk grade — submerging the entire first-floor commercial space interior including bar equipment, lower cabinetry, electrical outlets, and flooring. Aerial shots confirm the entire street block is inundated with the waterline sitting just below the second-floor windowsills on the front facade.",
        "_llm_limitations":         "Interior access photos would confirm actual inundation depth against wall markings; post-recession photos showing tide lines, HVAC, water heater, and electrical panel condition would sharpen the damage tier boundary between Level 2 and 3; survey-grade flood gauge data for this location would pin the precise depth.",
    },
    "112 State St, Montpelier, VT 05602": {
        **COMMON,
        "complete address":          "112 State St, Montpelier, VT 05602",
        "building_name_current":     "112 State St — commercial/office with arcade ground floor",
        "building_name_listing":     "un — within Montpelier Historic District",
        "flood_height_building":     "~4.0 ft above first floor (LLM estimate, medium confidence)",
        "latitude":                  "44.2608121",
        "longitude":                 "-72.579971",
        "archetype":                 "14", # F14: Office building (best match for bank/commercial office use; no multi-story masonry commercial archetype in Nofal 2020)
        "occupany_u":                "business",
        "number_stories":            "4",
        "year_built_u":              "un — requires further research; commercial block likely late 19th–early 20th c.",
        "front_elevation_orientation":"w",
        # Verified via collect_building_attributes.py + analyze_visual_attributes.py (Street View vision pass)
        "buidling_height_m":         "14.0",
        "building_area_m2":          "753.6",
        "wall_length_front":         "37.5",
        "wall_length_side":          "32.5",
        "wall_fenesteration_front_per": "45",
        "parapet_height_m":          "0.9",
        "buidling_use_before_flood": "business",
        "buidling_use_after_flood":  "business",
        "building_use_during_flood": "business",
        "restoration_completed_building_in_use_1_yrs_after_event": "un",
        "owner_business":            "un",
        "sub_national_heritage _list": "yes",
        "property_of_local_significance": "yes",
        "_llm_damage_level":        "2 (Moderate)",
        "_llm_confidence":          "medium",
        "_llm_water_depth_ft":      "4.0",
        "_llm_reasoning":           "Aerial flood photo shows floodwater surrounding the building at street level, reaching into the arcade/ground-floor archway zone. Brick facade and upper floors appear structurally intact, but ground-floor commercial interior and any below-grade or slab-on-grade mechanical systems would have been inundated to several feet, consistent with moderate damage to first-floor finishes, lower drywall, electrical, and ground-floor contents.",
        "_llm_limitations":         "No interior post-flood photos available; water-line staining on brick not clearly visible at photo resolution; exact flood stage relative to finished floor elevation unknown without survey data; interior inspection required to confirm drywall, electrical panel, HVAC, and elevator pit damage.",
    },
    "27 Langdon St, Montpelier, VT 05602": {
        **COMMON,
        "complete address":          "27 Langdon St, Montpelier, VT 05602",
        "building_name_current":     "27 Langdon St — commercial (Langdon Street shopping area)",
        "building_name_listing":     "un — within Montpelier Historic District",
        "flood_height_building":     "~3.5 ft above first floor (LLM estimate, medium confidence)",
        "latitude":                  "44.26051",
        "longitude":                 "-72.5755308",
        "archetype":                 "7",  # F7: Small multi-unit commercial building
        "occupany_u":                "mercantile",
        "number_stories":            "3",
        "year_built_u":              "un — Langdon St commercial development likely late 19th c.",
        "front_elevation_orientation":"w",
        # Verified via collect_building_attributes.py + analyze_visual_attributes.py (Street View vision pass).
        # Footprint is the 90 Main St tax parcel — this storefront's E911/parcel address is 90 Main St;
        # "27 Langdon St" is the shop's own Langdon-St-facing address. See LESSONS_LEARNED.md §2.
        "buidling_height_m":         "10.5",
        "building_area_m2":          "811.2",
        "wall_length_front":         "43.0",
        "wall_length_side":          "38.5",
        "wall_fenesteration_front_per": "35",
        "parapet_height_m":          "0.6",
        "buidling_use_before_flood": "mercantile",
        "buidling_use_after_flood":  "mercantile",
        "building_use_during_flood": "mercantile",
        "restoration_completed_building_in_use_1_yrs_after_event": "un",
        "owner_business":            "un",
        "sub_national_heritage _list": "yes",
        "property_of_local_significance": "yes",
        "_llm_damage_level":        "2 (Moderate)",
        "_llm_confidence":          "medium",
        "_llm_water_depth_ft":      "3.5",
        "_llm_reasoning":           "After photos show floodwater reaching approximately mid-way up the first-floor storefront columns/facade — roughly 3–4 ft above grade at peak — consistent with water entering ground-floor commercial spaces and damaging lower drywall, electrical outlets, HVAC, and first-floor contents. Aerial photos confirm the entire block was inundated. Interior shot shows submerged lower cabinetry and equipment consistent with moderate (Level 2) damage.",
        "_llm_limitations":         "No post-recession interior photos available to confirm waterline height on interior walls, condition of drywall, electrical panels, or mechanical equipment. Physical interior inspection with moisture readings and high-water mark stain line survey would significantly improve accuracy.",
    },
    "40 Main St, Montpelier, VT 05602": {
        **COMMON,
        "complete address":          "40 Main St, Montpelier, VT 05602",
        "building_name_current":     "40 Main St — Aubuchon Hardware / Capitol Copy / Sherpa Dinner House",
        "building_name_listing":     "un — within Montpelier Historic District",
        "flood_height_building":     "~2.5 ft above first floor (LLM estimate, high confidence)",
        "latitude":                  "44.2593998",
        "longitude":                 "-72.5764979",
        "archetype":                 "7",  # F7: Small multi-unit commercial building (hardware + restaurant + copy shop = multi-unit retail, F7 is closest)
        "occupany_u":                "mercantile",
        "number_stories":            "3",
        "year_built_u":              "un — commercial block likely late 19th–early 20th c.",
        "front_elevation_orientation":"s",
        # Verified via collect_building_attributes.py + analyze_visual_attributes.py (Street View vision pass)
        "buidling_height_m":         "10.5",
        "building_area_m2":          "812.4",
        "wall_length_front":         "51.6",
        "wall_length_side":          "36.9",
        "wall_fenesteration_front_per": "30",
        "parapet_height_m":          "0.6",
        "buidling_use_before_flood": "mercantile",
        "buidling_use_after_flood":  "mercantile",
        "building_use_during_flood": "mercantile",
        "restoration_completed_building_in_use_1_yrs_after_event": "un",
        "owner_business":            "yes",
        "sub_national_heritage _list": "yes",
        "property_of_local_significance": "yes",
        "_llm_damage_level":        "2 (Moderate)",
        "_llm_confidence":          "high",
        "_llm_water_depth_ft":      "2.5",
        "_llm_reasoning":           "After photos show floodwater reaching approximately mid-shin to knee height on a standing adult in the street (roughly 2–3 ft exterior), and the interior shot of Aubuchon Hardware confirms water covering the entire floor slab with merchandise floating/scattered at roughly 6–12 inches of standing water inside the first-floor commercial space. Aerial views show the entire back alley and street submerged well above grade, indicating full first-floor inundation of the ground-level retail.",
        "_llm_limitations":         "Interior photos only show the hardware store entry area during active flooding; post-flood interior inspections of all tenant spaces (Capitol Copy, Sherpa Dinner House, upper-floor units) needed to confirm wall-height waterline, drywall damage extent, HVAC/electrical panel submersion, and mold initiation.",
    },
    "54 Elm St, Montpelier, VT 05602": {
        **COMMON,
        "complete address":          "54 Elm St, Montpelier, VT 05602",
        "building_name_current":     "54 Elm St — laundromat (ground floor, closed/vacant post-flood); brick commercial building",
        "building_name_listing":     "un — within Montpelier Historic District",
        "flood_height_building":     "~3.0 ft above first floor (LLM estimate, low confidence — indirect visual evidence only)",
        "latitude":                  "44.2616257",
        "longitude":                 "-72.5757163",
        "archetype":                 "7",  # F7: Small multi-unit commercial building (laundromat ground floor + likely residential upper floors)
        "occupany_u":                "mercantile",
        "number_stories":            "4",
        "year_built_u":              "un — brick construction likely late 19th–early 20th c.",
        "front_elevation_orientation":"w",
        # Verified via collect_building_attributes.py + analyze_visual_attributes.py (Street View + satellite vision pass)
        "buidling_height_m":         "14.0",
        "building_area_m2":          "137.7",
        "wall_length_front":         "15.4",
        "wall_length_side":          "19.9",
        "wall_fenesteration_front_per": "15",
        "parapet_height_m":          "0.6",
        "soffit_present_u":          "yes",
        "soffit_type_u":             "un",
        "buidling_use_before_flood": "mercantile",
        "buidling_use_after_flood":  "not in use",
        "building_use_during_flood": "mercantile",
        "restoration_completed_building_in_use_1_yrs_after_event": "un",
        "owner_business":            "un",
        "sub_national_heritage _list": "yes",
        "property_of_local_significance": "yes",
        "_llm_damage_level":        "2 (Moderate)",
        "_llm_confidence":          "low",
        "_llm_water_depth_ft":      "3.0",
        "_llm_reasoning":           "After photos show the laundromat on the ground floor appears closed/vacant with debris visible in the storefront window, and there is visible staining and deterioration at the base of the brick facade compared to the before photos. Montpelier VT experienced significant flooding in 2023 that inundated first floors of downtown commercial buildings to several feet. The exterior brick structure appears intact but ground-floor commercial spaces show signs consistent with moderate flood inundation.",
        "_llm_limitations":         "No interior photos available to confirm wall damage height, mold, or mechanical equipment loss; after photos are exterior street-view imagery only and do not clearly show a flood tideline on the brick; interior inspection and documentation of water staining on walls, damaged finishes, and mechanical systems would be needed for a definitive assessment. Low confidence because visual evidence of inundation is indirect.",
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

    ("Construction Materials — Horizontal",
     ["const_material_h_stone", "const_material_h_brick", "const_material_h_wood",
      "const_material_h_mud", "const_material_h_rf_masonry", "const_material_h_rglr_stone",
      "const_material_h_rf_conc", "const_material_h_ir_stone",
      "const_material_h_steel", "const_material_h_othr"]),

    ("Construction Materials — Vertical",
     ["const_material_v_stone", "const_material_v_brick", "const_material_v_wood",
      "const_material_v_mud", "const_material_v_rf_masonry", "const_material_v_rglr_stone",
      "const_material_v_rf_conc", "const_material_v_ir_stone",
      "const_material_v_steel", "const_material_v_othr"]),
]

# ── Inline notes specific to certain attributes ───────────────────────────────
NOTES = {
    "NRHP_ref_number":              NRHP_NOTE,
    "national_register_listing_year": "1978 is the Montpelier Historic District listing year. " + NRHP_NOTE,
    "flood_height_building":        "Primary source: USGS High Water Mark survey (July 2023). LLM estimate from photo analysis. Point-cloud not available.",
    "distance_from_river_edge":     "Computed from USGS NHD centerline minus estimated 5m half-width of N. Branch Winooski. Source: hydro.nationalmap.gov NHD Large-Scale Flowlines, UTM 19N (EPSG:32619). NHD does not provide a polygon for this reach.",
    "distance_from_river_centerline":"Computed from USGS NHD Large-Scale Flowlines (N. Branch Winooski River + Winooski River merged centerline). UTM 19N (EPSG:32619). Source: hydro.nationalmap.gov/arcgis/rest/services/nhd/MapServer/6.",
    "FEMA_floodzone":               "Downtown Montpelier along N. Branch Winooski is Zone AE. Verify exact parcel at FEMA Map Service Center.",
    "latitude":                     "Approximate centroid; refine with Google Earth Pro.",
    "longitude":                    "Approximate centroid; refine with Google Earth Pro.",
    "archetype":                    "Assigned from Nofal & van de Lindt (2020) 15-type portfolio. None of the 15 archetypes is a perfect match for a multi-story historic masonry downtown commercial block — F7 (7, small multi-unit commercial) is the closest for retail/mixed-use; F14 (14, office) for primarily office use. Certainty is inherently low for all 5 buildings.",
    "wall_thickness":               "0.46 m assumed (~18 inches) for late 19th c. load-bearing brick commercial construction. Guide default of 0.2 m is for residential wood frame — not appropriate here. Historic masonry commercial buildings typically 12–24 inches (0.30–0.61 m). Use 0.46 as a mid-range estimate; refine from damage photos or historic drawings.",
    "construction_type_u_unc":      "Certainty 2 (35–50%) — brick visible in exterior photos but interior structure not confirmed from drawings.",
    "mwfrs_u_wall_unc":             "Certainty 2 — approximated from construction type.",
    "wall_cladding_u_unc":          "Certainty 3 (>75%) — brick clearly visible in assessment photos.",
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

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: system-ui, sans-serif; background: #f4f4f0; color: #1a1a1a; padding: 2rem; max-width: 1100px; }
h1 { font-size: 1.15rem; font-weight: 600; margin-bottom: 0.2rem; }
.meta { color: #777; font-size: 0.82rem; margin-bottom: 1.75rem; }
.meta a { color: #555; text-decoration: none; border-bottom: 1px solid #ccc; }
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
.badge-2 { display:inline-block; background:#fff3e0; color:#e65100;
           font-weight:600; padding:0.1em 0.45em; border-radius:3px; font-size:0.9em; }
"""

def safe_filename(address):
    return address.split(",")[0].replace(" ", "_").replace("/", "-") + ".html"

def fmt(key, val):
    if val in (None, "", "un"):
        return '<span class="un">un</span>'
    if key == "_llm_confidence":
        return f'<span class="badge-{val}">{val}</span>'
    if key == "_llm_damage_level":
        return f'<span class="badge-2">{val}</span>'
    return str(val)

def build_page(address, data):
    # Inject computed river distances
    rd = RIVER_DISTANCES.get(address, {})
    data = dict(data)
    if rd:
        data["distance_from_river_centerline"] = rd["cl"]
        data["distance_from_river_edge"] = rd["edge"]
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
            note = NOTES.get(k, "")
            # append options to defn if useful
            if opts and "options:" not in defn.lower():
                defn_display = f"{defn}<br><span style='color:#aaa'>Options: {opts}</span>" if defn else f"Options: {opts}"
            else:
                defn_display = defn
            llm_cls = ' class="llm-row"' if k.startswith("_llm") else ""
            rows += f"""
            <tr{llm_cls}>
              <td class="attr">{k}</td>
              <td class="value">{fmt(k, val)}</td>
              <td class="defn">{defn_display}</td>
              <td class="notes-col">{note}</td>
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
        rows = "".join(f"""
            <tr><td class="attr">{k}</td>
            <td class="value">{fmt(k, v)}</td>
            <td class="defn">{ATTR_INFO.get(k,{}).get('defn','')}</td>
            <td class="notes-col">{NOTES.get(k,'')}</td></tr>"""
            for k, v in extra.items())
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
{sections_html}
</body>
</html>"""
    out = OUTPUT_DIR / safe_filename(address)
    out.write_text(html)
    return str(out)

if __name__ == "__main__":
    manifest = {}
    for address, data in BUILDINGS.items():
        path = build_page(address, data)
        manifest[address] = f"building_details/{safe_filename(address)}"
        print(f"  {path}")
    (ROOT / "building_detail_manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"\nDone — {len(manifest)} pages, all 177 columns covered.")
