# Data dictionary - chicago-building-energy

Script output (`scripts/build.py`); never hand-edited. Vintage of every figure: City release of 2025-02-05, snapshot 2026-09-18. Covered-buildings list (g5i5-yz37) rows updated 2025-03-14; community areas (igwz-8jzy) 2025-04-22; building footprints (syp8-uezg) 2015-08-15.

**Years.** Published: [2022]. Excluded: 2023 - row coordinates fail the community-area test for 90% of rows. No row from an excluded year is in any file here. An empty cell means the City published nothing; no value is interpolated, estimated or geocoded.

**Fuel mix, 2022.** The five fuel columns sum to within 3% of total site energy for 61.4% of submitted records; for the rest they fall short and `fuel_mix_shown` is false.

## buildings.csv

One row per benchmarking `id` seen in any published-or-admitted year: where the property is, and which City footprints are attached to it.

| column | unit / values | source | definition |
|---|---|---|---|
| id | integer | Chicago Energy Benchmarking (xq83-jr8c) `id` | Property id; stable across years. Key. |
| address_raw | text | Chicago Energy Benchmarking (xq83-jr8c) `address` | As typed by the owner, from the display-year row, else the newest admitted year. |
| address_norm | text | derived | Parsed form used for matching: number or range, direction, street, type. Empty when the address does not parse. |
| community_area | text | Chicago Energy Benchmarking (xq83-jr8c) `community_area` | As stated on the same row; may be empty. |
| primary_property_type_latest | text | Chicago Energy Benchmarking (xq83-jr8c) `primary_property_type` | From the newest admitted year. |
| year_built | year | Chicago Energy Benchmarking (xq83-jr8c) `year_built` | As reported by the owner. |
| of_buildings_latest | count | Chicago Energy Benchmarking (xq83-jr8c) `of_buildings` | Buildings the property reports, newest admitted year. |
| trusted_lat, trusted_lon | degrees, WGS84 | Chicago Energy Benchmarking (xq83-jr8c) or covered list (g5i5-yz37) `latitude`/`longitude` | A City-published coordinate, accepted only if inside its own row's stated community area (100 m buffer). Empty when none passes. |
| coord_source | row_<year> / covered_list / none | derived | Which source's coordinate passed. Never an excluded year. |
| footprint_ids | `bldg_id`s joined by ';' | Building Footprints (syp8-uezg), a 2015 snapshot `bldg_id` | Footprints attached to the property. Empty when none. |
| n_footprints | count | derived | Length of `footprint_ids`. |
| footprint_year_built | year | Building Footprints (syp8-uezg), a 2015 snapshot `year_built` | Latest non-zero construction year among attached footprints. |
| footprint_area_sqft | sq ft | Building Footprints (syp8-uezg), a 2015 snapshot geometry | Summed plan area of attached footprints, computed in EPSG:3435. |
| match_method | T1 / T1b / T1_multi / T2 / coord_pip / coord_nearest / T3 / override / none | derived | The PIPELINE §2 tier that attached the footprints. |
| match_confidence | high / medium / low / none | derived | high: address agrees. medium: coordinate-led or coordinate-confirmed. low: unconfirmed. none: no footprint. |
| match_note | text | derived | What was tried and refused on the way. |

## energy.csv

One row per (`id`, `data_year`) for data years [2022]. Reported columns are as published. The release's own `latitude`/`longitude` are deliberately not carried: use `buildings.csv`.

| column | unit / values | source | definition |
|---|---|---|---|
| id, data_year | integer | Chicago Energy Benchmarking (xq83-jr8c) | Key. |
| property_name | text | Chicago Energy Benchmarking (xq83-jr8c) `property_name` | As the City publishes it. |
| address, zip_code, community_area, primary_property_type | text | Chicago Energy Benchmarking (xq83-jr8c) | As published. |
| reporting_status | text | Chicago Energy Benchmarking (xq83-jr8c) `reporting_status` | The City's label, which changes by year. |
| status | submitted / not_submitted / exempt / not_covered | derived | `reporting_status` under the explicit mapping in `scripts/schema.py`. |
| exempt_from_chicago_energy_rating | text | Chicago Energy Benchmarking (xq83-jr8c) | As published. |
| gross_floor_area_buildings_sq_ft | sq ft | Chicago Energy Benchmarking (xq83-jr8c) | As published. |
| year_built, of_buildings | year, count | Chicago Energy Benchmarking (xq83-jr8c) | As published. |
| water_use_kgal | thousand gallons | Chicago Energy Benchmarking (xq83-jr8c) | As published. |
| energy_star_score | 1-100 | Chicago Energy Benchmarking (xq83-jr8c) | As published. |
| electricity_use_kbtu, natural_gas_use_kbtu, district_steam_use_kbtu, district_chilled_water_use_kbtu, all_other_fuel_use_kbtu | kBtu per year | Chicago Energy Benchmarking (xq83-jr8c) | As published. Incomplete for many records; see `fuel_gap_pct`. |
| site_eui_kbtu_sq_ft, source_eui_kbtu_sq_ft | kBtu per sq ft per year | Chicago Energy Benchmarking (xq83-jr8c) | As published. |
| weather_normalized_site_eui_kbtu_sq_ft, weather_normalized_source_eui_kbtu_sq_ft | kBtu per sq ft per year | Chicago Energy Benchmarking (xq83-jr8c) | As published. |
| total_ghg_emissions_metric_tons_co2e | metric tons CO2e | Chicago Energy Benchmarking (xq83-jr8c) | As published. |
| ghg_intensity_kg_co2e_sq_ft | kg CO2e per sq ft | Chicago Energy Benchmarking (xq83-jr8c) | As published. |
| chicago_energy_rating | 0-4 stars | Chicago Energy Benchmarking (xq83-jr8c) | As published. |
| site_energy_kbtu | kBtu per year | derived | Total site energy = `site_eui_kbtu_sq_ft` x `gross_floor_area_buildings_sq_ft`, exact, rounded to the kBtu. Empty if either is empty. Never the fuel sum. |
| fuel_sum_kbtu | kBtu per year | derived | Sum of the five fuel columns, empties as zero; empty when all five are. |
| fuel_gap_pct | fraction | derived | (`fuel_sum_kbtu` - `site_energy_kbtu`) / `site_energy_kbtu`. -0.2 means the fuel columns account for 80% of the total. |
| fuel_mix_shown | true / false | derived | True only where /`fuel_gap_pct`/ < 0.03. |

## density_hex.csv

Hexagons 400 m flat to flat (0.053500 sq mi, constant, not clipped to the shoreline), pointy-top, anchored at the EPSG:3435 origin. Only hexagons holding at least one property are listed. A property sits at the centroid of its largest attached footprint, else at its trusted coordinate; with neither it is left out and counted in `reconciliation.md`.

| column | unit / values | source | definition |
|---|---|---|---|
| hex_id | r<row>c<col> | derived | Grid cell; stable across builds. |
| data_year | year |  |  |
| n_properties | count | derived | Benchmarked properties of any status located in the cell. |
| n_submitted, n_not_submitted | count | derived | By `status`. |
| site_energy_kbtu | kBtu per year | derived | Sum of `site_energy_kbtu` over submitted properties that have one. Empty when the cell has none - not zero. |
| ghg_tco2e | metric tons CO2e | derived | Sum of `total_ghg_emissions_metric_tons_co2e` over the same properties. |
| land_sqmi | square miles | derived | Area of the cell. |
| kbtu_per_sqmi | kBtu per year per sq mi | derived | `site_energy_kbtu` / `land_sqmi`. |

## density_ca.csv

The 77 community areas (igwz-8jzy), assigned by geometry, never by the row's stated name.

| column | unit / values | source | definition |
|---|---|---|---|
| community_area_num, name | 1-77, text | igwz-8jzy `area_numbe`, `community` |  |
| data_year | year |  |  |
| n_properties | count | derived | Benchmarked properties of any status located in the cell. |
| n_submitted, n_not_submitted | count | derived | By `status`. |
| site_energy_kbtu | kBtu per year | derived | Sum of `site_energy_kbtu` over submitted properties that have one. Empty when the cell has none - not zero. |
| ghg_tco2e | metric tons CO2e | derived | Sum of `total_ghg_emissions_metric_tons_co2e` over the same properties. |
| land_sqmi | square miles | derived | Area of the cell. |
| kbtu_per_sqmi | kBtu per year per sq mi | derived | `site_energy_kbtu` / `land_sqmi`. |
| share_not_submitted | fraction | derived | `n_not_submitted` / (`n_submitted` + `n_not_submitted`). Exempt properties are outside the ratio. |

## classes.json

7 quantile classes per metric and display year, computed once here; the map reads them and never computes its own. `site_eui` and `site_energy_kbtu` are classed over submitted properties with a published EUI; `kbtu_per_sqmi` separately for hexagons and community areas. `breaks` holds the six interior boundaries, rounded to three significant figures.

## checksums.sha256

SHA-256 of every other file in this folder, bare filenames, over the exact bytes.
