# Methods: Chicago building energy

How the files in this repository were made, stage by stage: what each stage reads and writes,
the rules the matcher applies in order, what the build derives, what the map reads, and what the
tests check. `scripts/` implements each stage and `tests/` checks it. The raw snapshots the
stages read are not published, so the code can be read and rerun against a fresh pull of the
City's data, but it cannot rebuild these exact bytes: the City revises rows after the fact.

## Toolchain

Python 3.13, with the versions pinned in `requirements.txt` (`pandas`, `numpy`, `geopandas`,
`shapely`, `pyproj`, `pyogrio`, `pyarrow`, `pytest`). The hexagon grid is computed by the build
itself, so neither `h3` nor the Python `topojson` package is a dependency. The map's TopoJSON is
built with mapshaper 0.7.61, which needs Node 20.11 or later. No stage after the fetch touches
the network.

Every stage is deterministic: the same raw snapshot in, byte-identical files out. A rebuild
followed by `git diff --exit-code data/processed` shows no change.

## Years

`scripts/schema.py` holds `DISPLAY_YEARS = [2022]` and `EXCLUDED_YEARS`. Nothing from an excluded
year reaches `data/processed/` or `site/data/`, and nothing outside `DISPLAY_YEARS` reaches the
map. 2023 is excluded because it has not yet been reviewed for release. It was first excluded
because its row coordinates fail the community-area test for 91% of rows. That is true, and those
coordinates are never used, but it is not a reason the year cannot be mapped: properties are
located by id and address first, with a coordinate only as a check or a fallback, and the location
test in stage 2 places 2023 about as well as 2022. The reason string in `schema.py` says what actually keeps it out.

## Stage 0: fetch (`scripts/fetch_sources.py`)

Pulls `xq83-jr8c` (Chicago Energy Benchmarking, every data year), `g5i5-yz37` (Covered Buildings)
and `igwz-8jzy` (community areas); `--footprints` adds the CSV export of `syp8-uezg` (Building
Footprints), about 450 MB. A snapshot is dated and never overwritten. Its manifest records each
file's SHA-256 and byte count, each dataset's `rowsUpdatedAt`, and its column list. The column
list is the schema contract: stage 1 fails if a required column is missing, or if a new one
appears without being acknowledged in `schema.py`.

Every file here comes from the snapshot of 2026-09-18. The footprint layer in it is a 2015
snapshot: rows last updated 2015-08-15 (newest `edit_date` 2015-08-06).

## Stage 1: normalize (`scripts/normalize.py`)

Writes one row per (`id`, `data_year`) for every year in the snapshot, plus the covered-buildings
list, the 77 community areas and the ACTIVE footprints, all in EPSG:3435.

- `reporting_status` becomes `status`, one of `submitted`, `not_submitted`, `exempt` and
  `not_covered`, through an explicit mapping. An unmapped label fails the build: a new year may add
  one.
- `fuel_sum_kbtu` is the sum of the five fuel columns, nulls as 0 inside the sum, formed only when
  at least one is non-null. `eui_x_gfa_kbtu` is `site_eui_kbtu_sq_ft × gross_floor_area_buildings_sq_ft`,
  exact in decimal, rounded half-up (null if either is null).
- `site_energy_kbtu` (total site energy) is formed only where the City published a site EUI: the
  fuel sum rounded to the kBtu, or `eui_x_gfa_kbtu` if the fuel columns are all empty or sum to
  zero. `site_energy_basis` records which. A record with no EUI has no total even if some fuel is
  listed (8 in 2022, six of them a single fuel).
- `ratio_to_eui_x_gfa` is the total over `eui_x_gfa_kbtu` where the total is a fuel sum, and
  `gfa_consistent` is `|ratio − 1| < 0.03`. In 2022, EUI × GFA overstates the fuel total by 3% or
  more for 989 of 2,562 records because the published floor area is inflated; the README sets out
  the evidence.
- The floor-area check is made per year, with the GHG corroboration that decides which side is
  wrong: total GHG / (GHG intensity × GFA) against the fuel ratio, its correlation, and the same
  ratio over the consistent records.
- Each address is parsed into `addr_number`, `addr_number_hi`, `addr_pre_dir`, `addr_st_name`,
  `addr_st_type`, `addr_unit` and `addr_parse_ok`. The parser reads ranges written `315-331`,
  `4612 - 4730`, `1610 1620` and `801- 831`; lists such as `205/225`; spelled-out numbers (`One`,
  `Two`); a leading label (`North Tower:`); and a second address, which it carries without
  parsing. It never guesses: an address it cannot read stays unparsed.
- Footprints keep `bldg_id, f_add1, t_add1, pre_dir1, st_name1, st_type1, bldg_name1, bldg_name2,
  bldg_statu, year_built, stories, shape_area, x_coord, y_coord` and the geometry. Non-ACTIVE
  footprints are dropped, and `shape_area` is recomputed from the geometry and compared with the
  source value (reported, not failed).

## Stage 2: match (`scripts/match_footprints.py`)

One row per benchmarking `id` (the union of ids across all years, about 3,700):

```
id, address_raw, address_norm, community_area, primary_property_type_latest,
year_built (benchmarking), of_buildings_latest,
trusted_lat, trusted_lon, coord_source ∈ {row_2022, covered_list, row_<year>, override, none},
footprint_ids (list<int>), n_footprints, footprint_year_built (max), footprint_area_sqft (sum),
implied_floors, size_check ∈ {pass, doubtful, fail, ''},
match_method ∈ {T1, T1b, T1_multi, T2, coord_pip, coord_nearest, override, none},
match_confidence ∈ {high, medium, low, none}, match_note
```

The rules, in order. The first hit wins, and `match_note` records what was tried.

1. **Trusted coordinate.** Candidates in order: the display-year row (`row_2022`), the covered
   list, then other years newest first, excluding every year in `EXCLUDED_YEARS` and any year whose
   community-area pass rate is below 80%. The first candidate inside its own row's `community_area`
   polygon, buffered 100 m, is accepted.
2. **T1/T1b.** `addr_number` in `[f_add1, t_add1]`, `pre_dir1` equal to `addr_pre_dir`, and
   `st_name1` equal to `addr_st_name` after the synonym table. One footprint: `T1`, high. Several,
   and the street type narrows them to one: `T1b`, high.
3. **T1_multi.** Several remain. If `of_buildings > 1`, or all candidates are within 60 m of each
   other, all are attached: `T1_multi`, high. Otherwise the one nearest the trusted coordinate is
   attached (medium); with no coordinate, all are attached (low).
4. **T2.** The number is in range and the street name matches, but the direction is blank on one
   side or different: medium if it agrees with the trusted coordinate within 60 m. Farther than
   that it is not matched and the coordinate tiers run: every such case in 2022 was a North/South
   mirror miles away (Sinai on S California drawn on N California). With no coordinate, low.
5. **coord_pip.** The trusted coordinate falls inside an ACTIVE footprint: medium.
6. **coord_nearest.** The nearest ACTIVE footprint within 30 m of the trusted coordinate: medium.
   Nothing farther: the first precision check found 1 of 17 matches at 30–75 m right.
7. **T3** (retired 2026-09-21). The nearest address range on the same street within 24 house
   numbers. Two precision checks found it right 2 times in 10 with no coordinate, and 2 in 20 with
   one within 60 m, usually attaching the building across the street. A property that reaches
   this point is placed at its trusted coordinate, or not at all.
8. **Override.** `footprint_overrides.csv` (`id, footprint_ids, coordinate, note, source,
   verified_on`, published in `data/processed/`) is applied last and wins: a reviewed answer beats
   every tier. The 16 rows were resolved by the same AI desk review as the precision check below,
   on 2026-09-21, one id at a time against the evidence each `note` and `source` names; no person
   has re-checked them, and no imagery was used. `coordinate` may name a City-published coordinate
   (`row_<year>`, never an excluded year, or `covered_list`) that could not pass the community-area
   test and was accepted on that review; it becomes the trusted coordinate, with `coord_source =
   override`. Empty `footprint_ids` with a coordinate: drawn as a marker there. Empty
   `footprint_ids` and no coordinate: a veto, not drawn and not located.
9. **Year guard,** at every tier. A candidate whose `year_built` (when non-zero) is more than 3
   years later than the benchmarking `year_built` is rejected, and noted. It is only advisory where
   the address and the trusted coordinate agree on that footprint (it holds the number and lies
   within 30 m of the coordinate): the disagreement is noted and the footprint kept. A stricter
   first version, which spared only the coordinate's own footprint, left 84 properties displaced,
   and the AI reviewers found the address footprint a few feet off in several of them. Without
   the exception, the guard moved 224 submitted 2022 properties, including 350 E Cermak, to a
   neighbor.
10. **Ranges.** When `addr_number_hi` is set, a footprint whose range overlaps
    `[addr_number, addr_number_hi]` on the same side of the street counts as in range.
11. **Size check,** after the tiers and before the overrides. `implied_floors` is gross floor area
    over attached footprint area, the floor area first scaled by attached ÷ reported buildings when
    a campus has fewer footprints than buildings. Over 120 floors, the footprint is rejected (the
    city's tallest building has 108, and the published floor area runs up to about 20% high). Over
    60, or over twice the stories the City's layer gives the attached footprints, the confidence
    drops one step, unless the layer gives 40 or more stories. The story test was calibrated on the
    first precision check (for right matches, implied floors ÷ stories has a 95th percentile of
    1.5; for wrong ones, a median of 12) and validated on a tier held out of the calibration, where
    it caught 13 of 16 wrong matches and 2 of 75 right ones.

The build also records counts by method and confidence, per year; the rate with and without
*displaced* matches (the guard turned away a footprint within 30 m of the coordinate, and a
coordinate tier then attached another at high or medium); and the excluded-year location test.
For each excluded year, that test locates its submitted properties by id where the id is already
in `buildings`, and otherwise matches them on that year's own address with no coordinate.

The target for release is 90% (`schema.MATCH_TARGET`) of submitted display-year properties at
high or medium confidence. It was set at 90% before matching. On 2026-09-18 it was lowered to
85%, the same day the first result came in at 86.4%, a figure that counted 224 displaced matches
(77.9% without them); it was restored on 2026-09-21. The build states the rate against it, met or
not, and nothing in matching reads it. Before the parser read ranges written with a space and
before the coordinate tiers existed, a first baseline matched 55% of 2022 rows to a single
footprint by address range and left 22% unmatched.

## Stage 3: build (`scripts/build.py`) to `data/processed/`

```
buildings.csv        the table above, lists serialized as ';'-joined ints, plus located_by
                     and community_area_num / community_area_name (by geometry)
energy.csv           one row per (id, data_year) for DISPLAY_YEARS only: status, every reported
                     numeric column as published, community_area_num, site_energy_kbtu,
                     site_energy_basis, fuel_sum_kbtu, eui_x_gfa_kbtu, ratio_to_eui_x_gfa,
                     gfa_consistent; nulls stay empty
density_hex.csv      hex_id, data_year, n_properties, n_submitted, n_not_submitted,
                     site_energy_kbtu, ghg_tco2e, area_sqmi, kbtu_per_sqmi
density_ca.csv       community_area_num, name, display_name, data_year, the same measures,
                     share_not_submitted
footprint_overrides.csv  the overrides as maintained: the audit trail of the overridden matches
match_review.csv     the precision verdicts as maintained
classes.json         per (metric, year): 7 quantile breaks used for color; the metrics are
                     site_eui and site_energy_kbtu (buildings) and kbtu_per_sqmi (density)
dictionary.md        every column, unit, source column and vintage
facts.json           every figure the project page states: value, display string, label,
                     definition, source file, sources, rounding, unit (scripts/facts.py); the map
                     manifest's counts are checked against it at export
checksums.sha256     SHA-256 over the exact bytes (at this repository's root, with paths from it)
```

Where a property is placed (`located_by`): its largest attached footprint, unless the match is
low confidence and a trusted coordinate exists, in which case the coordinate; with no footprint,
the coordinate; otherwise nowhere.

Density is the sum of `site_energy_kbtu` over properties with `status = submitted`, a total and a
location, divided by the cell's polygon area (`area_sqmi`, which is not land area). It is computed
on two geographies: hexagons 400 m flat to flat, each property counted at the centroid of its
largest attached footprint or at its trusted coordinate as `located_by` says, and the 77
community areas, assigned by geometry. A property with no location is left out of the density
sums, and the README says how much energy that leaves out. The color classes are seven quantiles
of the display year's values, rounded to three significant figures and written to `classes.json`.

The build also writes a working report of what did not resolve, which stays with the raw
snapshot.

## Stage 4: export (`scripts/export_site.py`) to `site/`

```
footprints.topojson  one object 'footprints': features keyed by id (property level, a
                     MultiPolygon when several footprints are attached); properties: id only.
                     Pre-projected EPSG:3435 feet, quantized 1e5, simplified at 1 ft or less.
points.json          [{id, x, y}] for properties drawn as markers: located_by = trusted_coordinate
hex.topojson         object 'hex' with hex_id; community.topojson: object 'ca' with area_numbe
values_<year>.json   {metric: {id: value}} for site_eui and site_energy_kbtu, and
                     {geo: {metric: {geo_id: value}}} for density, per display year (2022 only);
                     plus per-property tooltip fields (under the name policy below), type, gross
                     floor area, status, match_confidence, n_footprints, how it is drawn, the fuel
                     shares wherever the total is the fuel sum, and gfa_ratio_permille where the
                     floor area is inconsistent
manifest.json        the years available, the default year, the metrics, the class breaks, the
                     snapshot date and each source's rowsUpdatedAt; the map reads years from here
                     and never hard-codes them. Also geometry.hex_width_mi and hex_width_display
                     (from facts.json) for the tab label
caveats.json         display copy, written rather than generated; the export checks that every
                     figure it quotes is the figure the build produced
```

The name policy: a commercial, office, institutional, hotel, retail, data-center, education or
healthcare property shows `property_name` as the City publishes it. A residential property shows
its address and type, and its name only when the name reads as a building's name rather than an
owner's, an LLC's or a person's. No ranking language is used about any property.

The export then copies d3 7.9.0 and topojson-client 3.1.0, unmodified and with their ISC
licenses, into `site/vendor/`, and writes `site/checksums.sha256` over `site/data/` and
`site/vendor/` (paths relative to `site/`). That manifest is the map's release: the public repo
and the website publish exactly its set, and the website refuses any map file it does not list.
The map page that reads these files is part of the website.

## Tests (`tests/`, pytest)

Every item below is a test that exists. Tests that need the raw snapshot, or the intermediate
files built from it, skip in a checkout without them; the rest check the published files.

- **Schema:** raw column lists match `schema.py`; a missing or unacknowledged column fails; an
  unknown `reporting_status` label fails.
- **Total:** `eui_x_gfa_kbtu` is EUI × GFA to the kBtu; `site_energy_kbtu` is the fuel sum
  wherever an EUI is published, EUI × GFA only where the fuels are empty, and empty without an EUI;
  `gfa_consistent` is the ratio within 3%. The 2022 floor-area counts (2,562 / 1,573 / 989), the GHG
  corroboration (r ≥ 0.99, medians within 0.01, the consistent records' median within 0.002 of 1)
  and the excluded year's consistent floor areas are pinned: a new snapshot that breaks them fails
  until the prose is re-read.
- **Address parser:** ranges, lists, spelled-out numbers, labels, units; typos are left alone.
- **Years:** no processed file has an excluded `data_year`; `classes.json` and the map manifest
  list the display years only.
- **Coordinates:** `coord_source` never names an excluded year; every `row_<year>` trusted
  coordinate passed its own test; an override may not name an excluded year.
- **Matching,** on planted footprints: the year guard rejects a later footprint the coordinate
  does not confirm and is advisory where address and coordinate agree; T2 is refused when the
  coordinate is elsewhere; the size check passes, downgrades and rejects at its thresholds and
  spares a tower the City's layer records. On the build: overrides win (and a veto places
  nothing); no T2 match disagrees with its coordinate; no tier match fails the size check;
  low-confidence matches with a coordinate are located by it; the excluded-year test ran.
- **Density:** hexagons, community areas and included properties sum to the same total;
  `community_area_num` in `energy.csv` reproduces `density_ca.csv` exactly; every located property
  has one; the density files say `area_sqmi`.
- **Naming:** no processed file, `caveats.json`, map manifest or map page contains the banned
  words; the name policy on 11 cases; no owner token on an untyped property's label.
- **Counts:** submitted + not submitted + exempt + not covered = covered, and footprints + markers
  + not drawn = covered, in the map manifest.
- **Facts:** every fact in `facts.json` has a numeric value, a display string, a label, a
  one-sentence definition and a released source file; the display equals the value under its
  rounding; the invariants hold; the map manifest's counts equal the facts.
- **Release:** `checksums.sha256` covers exactly the files in `data/processed/` and matches; the
  map manifest's `snapshot_date` is the raw snapshot's, and its file hashes match.
- **Determinism:** stage 3, rebuilt from the intermediate files into a scratch folder, is
  byte-identical to `data/processed/`.

Not yet a test: a fixture of named ids resolving to fixed footprints. The precision check below
stands in for it.

## Match precision (`scripts/review_sample.py`, `match_review.csv`)

The tiers say how a footprint was found, not whether it is right. To estimate that, a seeded
sample of up to 100 display-year matches per tier (overrides excluded) is drawn by
`scripts/review_sample.py`, which writes an evidence card per match: the attached footprint's
own range, name, stories, year and area; the property's floor area against it; the coordinate
and its distance; every footprint within 80 m with its address; and every footprint on the street
whose range holds the number under any direction. Each match gets a verdict, published in
`match_review.csv` with its reason: `correct`, `partial` (one building of a campus), `wrong` or
`unsure`.

`build.py` computes precision per tier as (correct + partial) / (correct + partial + wrong), with
a Wilson 95% interval, a floor that counts `unsure` as wrong, and a population-weighted figure. A
reviewed row the build has since changed is stale and not counted; after any matcher change the
sample is redrawn. The draw is the 100 ids per tier with the smallest SHA-256 of `"<seed>:<id>"`:
a random sample that shifts only at the margin when a rule moves a few properties between tiers,
so verdicts for unchanged matches carry over and only the new ones need checking.

The first check (2026-09-21, 417 matches, on the matcher before the 30 m, T3 and story
refinements) found T1 right 99 times in 99, T2 6 in 6, T1_multi 78 in 86, coord_pip 75 in 91,
coord_nearest 56 in 94 (1 in 17 beyond 30 m) and T3 2 in 10; the refinements followed from it.
The second check, on the refined matcher, found T3 right 2 times in 20 even with a coordinate, and
T3 was retired. The published figures are from the second check, on the final matcher.

Both rounds were desk checks by AI reviewer agents (Claude), one per tier, working to one written
rubric, with a stated reason for every verdict. No person has checked them yet, and no imagery or
site visit was used. The same is true of the 16 overrides (stage 2, rule 8): they were resolved on
the same day, by the same means.

## Adding a new data year

1. Fetch a new snapshot and diff its manifest's column lists against the previous one. If the City
   changes the schema (the program's move to its BEAM platform may), update `schema.py` and the
   status mapping. That is the only code change a new year should need.
2. Rebuild. New ids, buildings covered for the first time, are matched by the tiers, and the build
   reports what needs an override.
3. Check the new year's coordinate pass rate. If it is 80% or more, its coordinates become an
   accepted candidate source for new ids; existing ids keep their trusted coordinate.
4. Add the year to `DISPLAY_YEARS` only if (a) its properties locate at the 90% target the way this
   pipeline locates, by id for ids already matched and by the year's own address otherwise (the
   location test); its own coordinates are needed only for new ids, and only if its pass rate is
   80% or more; and (b) its floor-area check and EUI coverage look like 2022's or better. A year not
   yet added goes in `EXCLUDED_YEARS` with the true reason, as 2023 does: tested, it locates at
   86.5%, and its floor areas are the consistent ones.
5. Review what did not resolve, add overrides, rebuild and run the tests. `scripts/publish.py`
   then verifies the release against its own checksums before anything is copied out.
6. The map manifest picks up the new year from `DISPLAY_YEARS`; the map shows a year selector only
   when the manifest lists more than one year.
