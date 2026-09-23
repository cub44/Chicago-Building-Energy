"""The schema contract and the fixed decisions of the build.

Nothing here is computed. `normalize.py` compares each raw source's column list (as the
portal's metadata reported it on the snapshot date, recorded in data/raw/<date>/MANIFEST.json)
against REQUIRED + ACKNOWLEDGED and fails on any difference in either direction: a missing
column breaks a stage downstream, and a new column is a release change someone should read
before it is ignored. Adding a data year should need edits to this file and nothing else
(PIPELINE "Adding a new data year").
"""

# --- Years ---------------------------------------------------------------------------------
# The map shows DISPLAY_YEARS and nothing else. Nothing from an EXCLUDED year reaches
# data/processed/ or site/data/, and an excluded year's coordinates are never a candidate for
# a trusted coordinate. The reason string is published in dictionary.md and reconciliation.md,
# so it has to be the real reason. 2023 was first excluded because its row coordinates fail
# the community-area test; but no year's row coordinates place a building here (ids and
# addresses do), and match_footprints.excluded_year_location_test shows 2023 locates about as
# well as 2022. What keeps it out is that it has not been reviewed for release.
DISPLAY_YEARS = [2022]
# The date of this data release, set by hand when a release is cut: the pipeline reads no clock.
# Pages state this one date (website DESIGN-SYSTEM, "One release date per data project"); the
# per-source dates live in dictionary.md.
RELEASE_DATE = "2026-09-23"
EXCLUDED_YEARS = {2023: "not yet reviewed for release. Its own row coordinates fail the "
                        "community-area test for 91% of rows and are never used, but the year "
                        "can be located by id and address (see the location test)"}

# A year's coordinates are a candidate source only if at least this share of its rows with a
# coordinate fall inside their own stated community area (README §3a, PIPELINE §2 rule 1).
MIN_CA_PASS_RATE = 0.80
# A single coordinate is trusted only inside its row's community-area polygon buffered by this.
CA_BUFFER_M = 100.0

# The City's footprint layer is a 2015 snapshot (README §3b): max(year_built) = 2015.
FOOTPRINT_VINTAGE_YEAR = 2015
# A footprint built more than this many years after the benchmarked building is another building,
# unless it holds the address and lies within NEAREST_MEDIUM_M of the trusted coordinate: then the
# two sources disagree about a construction year, and the guard only notes it (PIPELINE §2 rule 9).
YEAR_GUARD_YEARS = 3

# --- Sources -------------------------------------------------------------------------------
SOURCES = {
    "benchmarking": {"dataset": "xq83-jr8c", "file": "benchmarking_xq83-jr8c.json"},
    "covered": {"dataset": "g5i5-yz37", "file": "covered_g5i5-yz37.json"},
    "community_areas": {"dataset": "igwz-8jzy", "file": "community_areas_igwz-8jzy.geojson"},
    "footprints": {"dataset": "syp8-uezg", "file": "footprints_syp8-uezg.csv"},
}

REQUIRED = {
    "benchmarking": [
        "data_year", "id", "property_name", "reporting_status", "address", "zip_code",
        "chicago_energy_rating", "exempt_from_chicago_energy_rating", "community_area",
        "primary_property_type", "gross_floor_area_buildings_sq_ft", "year_built",
        "of_buildings", "water_use_kgal", "energy_star_score", "electricity_use_kbtu",
        "natural_gas_use_kbtu", "district_steam_use_kbtu", "district_chilled_water_use_kbtu",
        "all_other_fuel_use_kbtu", "site_eui_kbtu_sq_ft", "source_eui_kbtu_sq_ft",
        "weather_normalized_site_eui_kbtu_sq_ft", "weather_normalized_source_eui_kbtu_sq_ft",
        "total_ghg_emissions_metric_tons_co2e", "ghg_intensity_kg_co2e_sq_ft",
        "latitude", "longitude", "location", "row_id",
    ],
    "covered": [
        "building_id", "address", "zip", "cohort_sector", "cohort_size", "verification_year",
        "community_area_name", "community_area_number", "ward", "latitude", "longitude",
        "location",
    ],
    "community_areas": ["the_geom", "area_numbe", "community"],
    "footprints": [
        "the_geom", "bldg_id", "bldg_statu", "f_add1", "t_add1", "pre_dir1", "st_name1",
        "st_type1", "suf_dir1", "bldg_name1", "bldg_name2", "stories", "no_stories",
        "year_built", "bldg_sq_fo", "shape_area", "x_coord", "y_coord",
    ],
}

# Present in the source, read by nothing here. Listed so that a column appearing upstream is
# a visible decision rather than a silent one.
ACKNOWLEDGED = {
    "benchmarking": [
        ":@computed_region_43wa_7qmu", ":@computed_region_vrxf_vc4k",
        ":@computed_region_6mkv_f3dw", ":@computed_region_bdys_3d7i",
        ":@computed_region_awaf_s7ux",
    ],
    "covered": [
        ":@computed_region_vrxf_vc4k", ":@computed_region_6mkv_f3dw",
        ":@computed_region_rpca_8um6", ":@computed_region_bdys_3d7i",
        ":@computed_region_43wa_7qmu", ":@computed_region_awaf_s7ux",
    ],
    "community_areas": ["area_num_1", "shape_area", "shape_len"],
    "footprints": [
        "cdb_city_i", "unit_name", "non_standa", "comments", "orig_bldg_", "footprint_",
        "create_use", "bldg_creat", "bldg_activ", "bldg_end_d", "demolished", "edit_date",
        "edit_useri", "edit_sourc", "qc_date", "qc_userid", "qc_source", "z_coord",
        "harris_str", "no_of_unit", "bldg_condi", "condition_", "vacancy_st", "label_hous",
        "shape_len",
    ],
}

# --- reporting_status (README §3c) -----------------------------------------------------------
# The label changes by year. An unmapped label fails the build: a new year may add one, and
# which bucket it belongs in is a reading of the ordinance, not something to infer.
STATUS_MAP = {
    "Submitted": "submitted",
    "Submitted Data": "submitted",
    "Not Submitted": "not_submitted",
    "Exempt": "exempt",
    "Not Covered 2024": "not_covered",
}
STATUSES = ["submitted", "not_submitted", "exempt", "not_covered"]

# --- Benchmarking columns by kind --------------------------------------------------------------
FUEL_COLUMNS = [
    "electricity_use_kbtu", "natural_gas_use_kbtu", "district_steam_use_kbtu",
    "district_chilled_water_use_kbtu", "all_other_fuel_use_kbtu",
]
# Published numeric columns, carried into energy.csv exactly as the City reports them.
NUMERIC_COLUMNS = [
    "gross_floor_area_buildings_sq_ft", "year_built", "of_buildings", "water_use_kgal",
    "energy_star_score", *FUEL_COLUMNS, "site_eui_kbtu_sq_ft", "source_eui_kbtu_sq_ft",
    "weather_normalized_site_eui_kbtu_sq_ft", "weather_normalized_source_eui_kbtu_sq_ft",
    "total_ghg_emissions_metric_tons_co2e", "ghg_intensity_kg_co2e_sq_ft",
    "chicago_energy_rating",
]
# Total site energy is the sum of the five fuel columns wherever the City published an EUI.
# EUI x gross floor area overstates it for about 989 of 2,562 records in 2022 because the
# published floor area is inflated (README §1): the GHG columns agree with the fuel sum, and
# the 2023 release carries about 0.836 x the 2022 floor area for the same ids. A record is
# "floor-area consistent" where EUI x GFA is within this of the fuel total.
GFA_TOLERANCE = 0.03

# --- Footprints kept in data/interim/footprints.parquet (PIPELINE §1) ------------------------
FOOTPRINT_KEEP = [
    "bldg_id", "f_add1", "t_add1", "pre_dir1", "st_name1", "st_type1", "bldg_name1",
    "bldg_name2", "bldg_statu", "year_built", "stories", "shape_area", "x_coord", "y_coord",
]
FOOTPRINT_ACTIVE = "ACTIVE"

# --- Matching (PIPELINE §2) --------------------------------------------------------------------
MATCH_METHODS = ["T1", "T1b", "T1_multi", "T2", "coord_pip", "coord_nearest", "override", "none"]
# (T3, the nearest address range on the street, was retired 2026-09-21 after two precision checks.)
MATCH_CONFIDENCES = ["high", "medium", "low", "none"]
CLUSTER_M = 60.0          # T1_multi: candidates all within this of each other are one property
AGREE_M = 60.0            # T2: "agrees with the trusted coordinate"
NEAREST_MEDIUM_M = 30.0   # coord_nearest: medium at or under this, nothing beyond (a 30-75 m low band
                          # was retired 2026-09-21: 1 of 17 right). Also the reach within which the
                          # address and the coordinate agree (year guard, rule 9)
# Release threshold: share of submitted display-year properties matched at high or medium
# confidence. Set at 0.90 before matching. On 2026-09-18 it was lowered to 0.85 the same day
# the first result came in at 86.4% - a result that counted 224 properties moved to a
# neighboring footprint by the year guard (77.9% without them). Restored 2026-09-21. It is a
# bar the reconciliation report states, met or not; nothing in matching reads it.
MATCH_TARGET = 0.90

# Size check (PIPELINE §2 rule 11). implied_floors = gross floor area / attached footprint area,
# with a campus's floor area first scaled by attached footprints / reported buildings. The
# tallest building in Chicago has 108 floors, and the published floor area runs up to ~20% high.
SIZE_FAIL_FLOORS = 120     # above this the footprint is rejected: it cannot be the building
SIZE_DOUBT_FLOORS = 60     # above this the match is downgraded one step ...
SIZE_STORY_FACTOR = 2     # ... or when it exceeds this many times the stories the City's layer gives
SIZE_TOWER_STORIES = 40    # ... unless the City's layer gives an attached footprint this many stories

# --- Class breaks ---------------------------------------------------------------------------------
N_CLASSES = 7

# --- Geometry -----------------------------------------------------------------------------------
CRS_PLANE = "EPSG:3435"   # NAD83 / Illinois East (ftUS): the footprint layer's own x/y
CRS_GEO = "EPSG:4326"
FT_PER_M = 1 / 0.3048006096012192   # US survey foot
HEX_SIZE_M = 400.0        # flat-to-flat width of a density hexagon
SQFT_PER_SQMI = 5280.0 ** 2

# --- Language the release may not contain (MAP_SPEC §6, PIPELINE "Naming") --------------------
# Kept as fragments so that this file does not itself contain the words it forbids.
BANNED_FRAGMENTS = ["offen" + "der", "ho" + "g", "wor" + "st", "sha" + "me"]
