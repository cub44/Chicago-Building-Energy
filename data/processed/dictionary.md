# Data dictionary - chicago-building-energy

Script output (`scripts/build.py`); never hand-edited. Data release 2026-09-24. Vintage of every figure: City release of 2025-02-05, snapshot 2026-09-18. Covered-buildings list (g5i5-yz37) rows updated 2025-03-14; community areas (igwz-8jzy) 2025-04-22; building footprints (syp8-uezg) 2015-08-15.

**Years.** Published: [2022]. Excluded: 2023 - not yet reviewed for release. Its own row coordinates fail the community-area test for 91% of rows and are never used, but the year can be located by id and address (see the location test). No row from an excluded year is in any file here. An empty cell means the City published nothing; no value is interpolated, estimated or geocoded.

**Total site energy, 2022.** The sum of the five fuel columns, for each of the 2,562 submitted records with a published site EUI; EUI x gross floor area is used only where the fuel columns are empty (0 records). For 989 records EUI x floor area runs 3% or more above the fuel total (median ratio 0.835), because the published floor area is inflated: the GHG columns fall short of GHG intensity x floor area by the same factor (median 0.8349, r = 0.9956) and agree exactly elsewhere (0.9998). `gfa_consistent` marks which records are affected; their site EUI is as the City computed it and is not affected.

**Community area.** `community_area` is the text on the City's row, as typed: 132 spellings for 77 areas across these files, and empty for some rows. Grouping on it splits areas silently. `community_area_num` is the area the property is located in, by geometry, the same assignment the density tables use; group on that.

**Small counts.** 591 of the 964 hexagons with a value hold a single reporting property, and 14 community areas hold fewer than 5. There one building sets the figure; read `n_submitted` beside `kbtu_per_sqmi`.

**Match precision.** A seeded sample of up to 100 footprint matches per tier (406 in all) was checked one by one against evidence the tier did not use: the footprint's own address range, name, stories and size, its neighbors and their addresses, and the City coordinate. The checks were made by AI reviewers (Claude) working to a written rubric, with a sample re-checked; no person or imagery was involved. Every verdict and its reason is in `match_review.csv`. Precision is correct (including one building of a campus) over correct plus wrong; `unsure` is left out, and the floor counts it as wrong. Weighted by how many matches each tier made, about 91% of footprints attached by the tiers are the right building, and about 94% of the outlines the map draws (low-confidence matches are placed at their coordinate instead).

| tier | matches | checked | correct | partial | wrong | unsure | precision (95% CI) | floor |
|---|---|---|---|---|---|---|---|---|
| T1 | 2,577 | 100 | 82 | 14 | 3 | 1 | 97% (91%-99%) | 96% |
| T1_multi | 146 | 100 | 61 | 13 | 15 | 11 | 83% (74%-90%) | 74% |
| T2 | 6 | 6 | 4 | 2 | 0 | 0 | 100% (61%-100%) | 100% |
| coord_pip | 125 | 100 | 69 | 7 | 12 | 12 | 86% (78%-92%) | 76% |
| coord_nearest | 439 | 100 | 45 | 8 | 36 | 11 | 60% (49%-69%) | 53% |

By `match_confidence`, weighting each tier's checked matches by the matches it holds at that level:

| confidence | matches | estimated precision |
|---|---|---|
| high | 2,551 | 99% |
| medium | 626 | 74% |
| low | 116 | 20% |

## buildings.csv

One row per benchmarking `id` seen in any published-or-admitted year: where the property is, and which City footprints are attached to it.

| column | unit / values | source | definition |
|---|---|---|---|
| id | integer | Chicago Energy Benchmarking (xq83-jr8c) `id` | Property id; stable across years. Key. |
| address_raw | text | Chicago Energy Benchmarking (xq83-jr8c) `address` | As typed by the owner, from the display-year row, else the newest admitted year. |
| address_norm | text | derived | Parsed form used for matching: number or range, direction, street, type. Empty when the address does not parse. |
| community_area | text | Chicago Energy Benchmarking (xq83-jr8c) `community_area` | As stated on the same row; may be empty or misspelled. Use `community_area_num`. |
| primary_property_type_latest | text | Chicago Energy Benchmarking (xq83-jr8c) `primary_property_type` | From the newest admitted year. |
| year_built | year | Chicago Energy Benchmarking (xq83-jr8c) `year_built` | As reported by the owner. |
| of_buildings_latest | count | Chicago Energy Benchmarking (xq83-jr8c) `of_buildings` | Buildings the property reports, newest admitted year. |
| trusted_lat, trusted_lon | degrees, WGS84 | Chicago Energy Benchmarking (xq83-jr8c) or covered list (g5i5-yz37) `latitude`/`longitude` | A City-published coordinate, accepted only if inside its own row's stated community area (100 m buffer), or accepted on review (`coord_source` override; see `footprint_overrides.csv`). Empty when none. |
| coord_source | row_<year> / covered_list / override / none | derived | Which source's coordinate is used. `override`: a City coordinate that could not pass the test and was accepted on review; `footprint_overrides.csv` names which. Never an excluded year. |
| footprint_ids | `bldg_id`s joined by ';' | Building Footprints (syp8-uezg), a 2015 snapshot `bldg_id` | Footprints attached to the property. Empty when none. |
| n_footprints | count | derived | Length of `footprint_ids`. For a campus it is often fewer than `of_buildings_latest`: only footprints tied to the address or the coordinate are attached. |
| footprint_year_built | year | Building Footprints (syp8-uezg), a 2015 snapshot `year_built` | Latest non-zero construction year among attached footprints. |
| footprint_area_sqft | sq ft | Building Footprints (syp8-uezg), a 2015 snapshot geometry | Summed plan area of attached footprints, computed in EPSG:3435. |
| implied_floors | floors | derived | Gross floor area / `footprint_area_sqft`; for a campus with fewer footprints than buildings, the floor area is first scaled by footprints / buildings. For a rejected match, the rejected footprints' figure. |
| size_check | pass / doubtful / fail / empty | derived | fail: over 120 implied floors, footprint rejected. doubtful: over 60, or over 2 times the stories the City's layer gives the attached footprints, confidence lowered one step; not applied where the layer gives 40+ stories. Empty: no footprint found or no floor area. |
| match_method | T1 / T1b / T1_multi / T2 / coord_pip / coord_nearest / override / none | derived | The tier that attached the footprints, in the order `scripts/match_footprints.py` tries them; the README's match-precision table defines each one. |
| match_confidence | high / medium / low / none | derived | high: address agrees, or set by an override. medium: coordinate-led or coordinate-confirmed. low: unconfirmed. none: no footprint. After the size check. |
| match_note | text | derived | What was tried and refused on the way. |
| located_by | footprint / trusted_coordinate / none | derived | Where the property is placed on the map and in the density tables: its largest footprint, or the trusted coordinate when it has no footprint or only a low-confidence one. A low-confidence match with no trusted coordinate keeps its footprint, there being nothing else to place it by: 1 of the 116 low matches in 2022. |
| community_area_num, community_area_name | 1-77, text | igwz-8jzy `area_numbe`, `community` | The community area the property is located in, by geometry. Empty when not located. |

## energy.csv

One row per (`id`, `data_year`) for data years [2022]. Reported columns are as published. The release's own `latitude`/`longitude` are deliberately not carried: use `buildings.csv`.

| column | unit / values | source | definition |
|---|---|---|---|
| id, data_year | integer | Chicago Energy Benchmarking (xq83-jr8c) | Key. |
| property_name | text | Chicago Energy Benchmarking (xq83-jr8c) `property_name` | As the City publishes it. |
| address, zip_code, community_area, primary_property_type | text | Chicago Energy Benchmarking (xq83-jr8c) | As published. `community_area` is free text; see the note above. |
| community_area_num | 1-77 | derived | As in `buildings.csv`: where the property is located, by geometry. Group on this. |
| reporting_status | text | Chicago Energy Benchmarking (xq83-jr8c) `reporting_status` | The City's label, which changes by year. |
| status | submitted / not_submitted / exempt / not_covered | derived | `reporting_status` under the explicit mapping in `scripts/schema.py`. |
| exempt_from_chicago_energy_rating | text | Chicago Energy Benchmarking (xq83-jr8c) | As published. |
| gross_floor_area_buildings_sq_ft | sq ft | Chicago Energy Benchmarking (xq83-jr8c) | As published. Inflated where `gfa_consistent` is false. |
| year_built, of_buildings | year, count | Chicago Energy Benchmarking (xq83-jr8c) | As published. |
| water_use_kgal | thousand gallons | Chicago Energy Benchmarking (xq83-jr8c) | As published. |
| energy_star_score | 1-100 | Chicago Energy Benchmarking (xq83-jr8c) | As published. |
| electricity_use_kbtu, natural_gas_use_kbtu, district_steam_use_kbtu, district_chilled_water_use_kbtu, all_other_fuel_use_kbtu | kBtu per year | Chicago Energy Benchmarking (xq83-jr8c) | As published. Together they are total site energy. |
| site_eui_kbtu_sq_ft, source_eui_kbtu_sq_ft | kBtu per sq ft per year | Chicago Energy Benchmarking (xq83-jr8c) | As published. |
| weather_normalized_site_eui_kbtu_sq_ft, weather_normalized_source_eui_kbtu_sq_ft | kBtu per sq ft per year | Chicago Energy Benchmarking (xq83-jr8c) | As published. |
| total_ghg_emissions_metric_tons_co2e | metric tons CO2e | Chicago Energy Benchmarking (xq83-jr8c) | As published. |
| ghg_intensity_kg_co2e_sq_ft | kg CO2e per sq ft | Chicago Energy Benchmarking (xq83-jr8c) | As published. |
| chicago_energy_rating | 0-4 stars | Chicago Energy Benchmarking (xq83-jr8c) | As published. |
| site_energy_kbtu | kBtu per year | derived | Total site energy: the sum of the five fuel columns, rounded to the kBtu, where a site EUI is published; EUI x floor area only if the fuel columns are empty or zero. Empty without an EUI. |
| site_energy_basis | fuel_sum / eui_x_gfa / empty | derived | Which definition gave `site_energy_kbtu`. |
| fuel_sum_kbtu | kBtu per year | derived | Sum of the five fuel columns, empties as zero; empty when all five are. Also present for some records with no EUI, where it is not used. |
| eui_x_gfa_kbtu | kBtu per year | derived | `site_eui_kbtu_sq_ft` x `gross_floor_area_buildings_sq_ft`, exact, rounded to the kBtu. A check on the floor area, not a total. |
| ratio_to_eui_x_gfa | ratio | derived | `site_energy_kbtu` / `eui_x_gfa_kbtu` where the total is a fuel sum. 1 where the floor area agrees; 0.835 means the published floor area is about 1.2 times what the EUI and fuel figures imply. |
| gfa_consistent | true / false / empty | derived | True where `ratio_to_eui_x_gfa` is within 3% of 1. |

## density_hex.csv

Hexagons 400 m flat to flat (0.053500 sq mi, constant, not clipped to the shoreline), pointy-top, anchored at the EPSG:3435 origin. Only hexagons holding at least one property are listed. A property is placed as `located_by` says; with no location it is left out of both density tables, and `buildings.csv` marks it `located_by = none`.

| column | unit / values | source | definition |
|---|---|---|---|
| hex_id | r<row>c<col> | derived | Grid cell; stable across builds. |
| data_year | year | derived | The display year the row aggregates; [2022] here. |
| n_properties | count | derived | Benchmarked properties of any status located in the cell. |
| n_submitted, n_not_submitted | count | derived | By `status`. |
| site_energy_kbtu | kBtu per year | derived | Sum of `site_energy_kbtu` over submitted properties that have one. Empty when the cell has none - not zero. |
| ghg_tco2e | metric tons CO2e | derived | Sum of `total_ghg_emissions_metric_tons_co2e` over the same properties. |
| area_sqmi | square miles | derived | Area of the cell's polygon. Not land area: it includes parks, rail yards and rivers, and a lakefront hexagon includes water. |
| kbtu_per_sqmi | kBtu per year per sq mi | derived | `site_energy_kbtu` / `area_sqmi`. Read with `n_submitted`: many cells hold one property. |

## density_ca.csv

The 77 community areas (igwz-8jzy), assigned by geometry, never by the row's stated name.

| column | unit / values | source | definition |
|---|---|---|---|
| community_area_num, name | 1-77, text | igwz-8jzy `area_numbe`, `community` | The community area and its name, as the City's boundary layer publishes them. Key with `data_year`. |
| display_name | text | derived | `name` as the pothole release spells it: title case, with O'Hare and McKinley Park. |
| data_year | year | derived | The display year the row aggregates; [2022] here. |
| n_properties | count | derived | Benchmarked properties of any status located in the cell. |
| n_submitted, n_not_submitted | count | derived | By `status`. |
| site_energy_kbtu | kBtu per year | derived | Sum of `site_energy_kbtu` over submitted properties that have one. Empty when the cell has none - not zero. |
| ghg_tco2e | metric tons CO2e | derived | Sum of `total_ghg_emissions_metric_tons_co2e` over the same properties. |
| area_sqmi | square miles | derived | Area of the cell's polygon. Not land area: it includes parks, rail yards and rivers, and a lakefront hexagon includes water. |
| kbtu_per_sqmi | kBtu per year per sq mi | derived | `site_energy_kbtu` / `area_sqmi`. Read with `n_submitted`: many cells hold one property. |
| share_not_submitted | fraction | derived | `n_not_submitted` / (`n_submitted` + `n_not_submitted`). Exempt properties are outside the ratio. |

## footprint_overrides.csv

The answers the matcher applies last, after every tier. Each was resolved one id at a time against the evidence its `note` and `source` name, by the same AI desk review (Claude, working to a written rubric) that produced the match precision above; no person has re-checked them, and no imagery or site visit was used. No footprints and no coordinate means the property is deliberately not placed.

| column | unit / values | source | definition |
|---|---|---|---|
| id | integer | Chicago Energy Benchmarking (xq83-jr8c) `id` | The property the row resolves. Key. |
| footprint_ids | `bldg_id`s joined by ';' | Building Footprints (syp8-uezg), a 2015 snapshot `bldg_id` | The footprints to attach. Empty for none. |
| coordinate | row_<year> / covered_list / empty | derived | Which City-published coordinate to trust for this property, accepted on the review although it could not pass the community-area test. Empty for none. Never an excluded year. |
| note | text | derived | The reasoning, naming the evidence it rests on. |
| source | text | derived | The dataset columns and rows the note was read from. |
| verified_on | date, YYYY-MM-DD | derived | When the row was reviewed. |

## match_review.csv

The precision check above, one row per sampled match, as reviewed. A row the build has since changed is stale and is not counted in the precision; the tables above say how many.

| column | unit / values | source | definition |
|---|---|---|---|
| id | integer | Chicago Energy Benchmarking (xq83-jr8c) `id` | The property whose match was checked. Key with `footprint_ids`. |
| match_method | T1 / T1b / T1_multi / T2 / coord_pip / coord_nearest / override / none | derived | The tier that attached the footprints, as reviewed. |
| match_confidence | high / medium / low / none | derived | Its confidence, as reviewed. |
| footprint_ids | `bldg_id`s joined by ';' | Building Footprints (syp8-uezg), a 2015 snapshot `bldg_id` | The footprints the reviewer judged. |
| verdict | correct / partial / wrong / unsure | derived | correct: the attached footprints are the property's building or buildings. partial: one building of a campus, location right. wrong: a different building. unsure: the evidence did not decide it. |
| reason | text | derived | Why, naming the evidence on the card. |
| reviewed_on | date, YYYY-MM-DD | derived | When the verdict was recorded. |

## facts.json

Every figure the project page states, one entry per figure: `value` (the exact figure), `display` (the string the page prints, thousands separators and unit included), `label`, a one-sentence `definition`, `source_file`, `sources`, `rounding` (set wherever `display` is not the plain rendering of `value`) and `unit`. Computed in the same build as the tables above, from them; the map manifest's `counts` are checked against it at export. A percent is on a 0-100 scale; the hexagon width is in feet and in miles; no entry carries a metric unit. The top level carries the release date and the source pulls.

## classes.json

7 quantile classes per metric and display year, computed once here; the map reads them and never computes its own. `site_eui` and `site_energy_kbtu` are classed over submitted properties with a published EUI; `kbtu_per_sqmi` separately for hexagons and community areas. `breaks` holds the six interior boundaries, rounded to three significant figures.

## checksums.sha256

SHA-256 of every other file in this folder, bare filenames, over the exact bytes.
