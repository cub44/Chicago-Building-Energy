# Chicago building energy

[Explore the project](https://connorblandford.com/projects/chicago-building-energy/). This is the dataset behind an exploratory map; the accompanying article is forthcoming. It covers the properties Chicago's Energy Benchmarking Ordinance requires to report: what each reported for **data year 2022**, which City building footprints it occupies, and how that reported energy is distributed across hexagons and community areas. Data release: **2026-09-23**; what changed in it, and in the releases of 2026-09-22 and 2026-09-21 before it, is at the end.

No software is required to read it: open the CSVs in a spreadsheet or your preferred analysis tool. The pipeline that produced them is published here too, under `scripts/` and `tests/`; see [The code](#the-code).

## Download

| File | Rows | One row represents |
|---|---:|---|
| [energy.csv](data/processed/energy.csv) | 3,613 | One property the ordinance covered in 2022: what it reported, its status, and its total site energy |
| [buildings.csv](data/processed/buildings.csv) | 3,749 | One property, with the City footprints attached to it, how confidently they were matched, and where it is placed |
| [density_hex.csv](data/processed/density_hex.csv) | 1,319 | One 400 m hexagon holding at least one benchmarked property |
| [density_ca.csv](data/processed/density_ca.csv) | 77 | One community area |
| [footprint_overrides.csv](data/processed/footprint_overrides.csv) | 16 | One property whose location the tiers could not settle, resolved on review, with the evidence |
| [match_review.csv](data/processed/match_review.csv) | 406 | One footprint match from the precision check, with its verdict and the reason |
| [classes.json](data/processed/classes.json) | — | The color-class boundaries the map uses |
| [dictionary.md](data/processed/dictionary.md) | — | Every column, with its unit, source column and vintage |
| [facts.json](data/processed/facts.json) | — | Every figure the project page states, with the display string it prints, a definition, its source file and rounding; the map's own counts are checked against it |

See the [data dictionary](data/processed/dictionary.md) for all fields. Join `energy.csv` to `buildings.csv` on `id`. `buildings.csv` is longer because it carries every property seen in any admitted year from 2014 to 2022, not only those covered in 2022, so a left join from `energy.csv` loses nothing. `footprint_ids` holds the City's own `bldg_id`s from `syp8-uezg`, separated by `;`, so any outline can be checked against its source. To group by community area, use `community_area_num`, not `community_area`: the latter is the City's free text, with 132 distinct values in `energy.csv` (blank included) for 77 areas, and grouping on it splits areas without warning. `community_area_num` is where the property is placed, by geometry, and grouping `energy.csv` on it reproduces `density_ca.csv` exactly. The two density files aggregate the same properties on two geographies and each sums to the same citywide total: never add them together, and never add either to `energy.csv`. [SHA-256 checksums](checksums.sha256) identify the download versions; verify with `shasum -a 256 -c checksums.sha256`.

## Sources and method

Inputs are the City of Chicago datasets `xq83-jr8c` (Chicago Energy Benchmarking, every data year; rows last updated 2025-02-05), `g5i5-yz37` (Covered Buildings; rows updated 2025-03-14), `igwz-8jzy` (community areas) and `syp8-uezg` (building footprints, a 2015 snapshot), all pulled on 2026-09-18. The build reads those snapshots rather than the live portal, so an upstream revision cannot change a published figure after the fact. The snapshots themselves are held privately (the footprint export alone is 1 GB); the code that reads them is in this repository.

**Total site energy is the sum of the fuel use each property reported**: electricity, natural gas, district steam, district chilled water and other fuels, rounded to the kBtu. It is formed only where the City published a site EUI, and it is not EUI × gross floor area. For 989 of the 2,562 reporting properties with an EUI, that product runs 3% or more above the fuel total, by a median factor of about 1.2, because the floor area the City published for them is inflated. Three things show it is the floor area and not the fuel columns. First, the City's greenhouse-gas columns fall short of GHG intensity × floor area by the same factor (median 0.835 against 0.835, r = 0.996) and agree exactly for every other property; a missing fuel would leave them agreeing. Second, the City's 2023 release lists the same properties at a median 0.836 × their 2022 floor area. Third, 2023's fuel columns and EUI × floor area agree for 2,577 of 2,583 records. `eui_x_gfa_kbtu`, `ratio_to_eui_x_gfa` and `gfa_consistent` are carried so the check can be repeated. Site EUI is the City's own figure and agrees with the fuels; only the floor-area column is off. The same defect is in the 2020 and 2021 releases.

**Locations come from matching each reported address to a City building footprint**, not from the coordinates in the release. The matcher runs fixed tiers — address range and street first, then the City's own coordinate as a check or a fallback — and records which tier attached each property in `match_method` and how confidently in `match_confidence`. A City coordinate is used only if it falls inside the community area its own row names, with a 100 m buffer, or, for 13 properties, after a review of that property's evidence one at a time (`coord_source` = `override`). A footprint built more than three years after the property is taken to be a different building, unless it holds the property's address and lies within 30 m of the City coordinate. A size check then rejects a footprint that would put the reported floor area on more than 120 floors, and lowers the confidence of one that would need more than 60, or more than twice the stories the City's layer gives it. A coordinate is trusted to pick a footprint only within 30 m, and an address range that is merely near the property's number is never a match: the precision checks below found the looser versions of those rules right 1 time in 17 and 4 times in 30. A property with several buildings is attached to every footprint its address or coordinate ties to it; for 292 of the 356 reporting campuses that is a single footprint, so the outline may show one building of several, and the property's figure is never divided among its footprints. `located_by` says where each property is placed: its footprint, or its City coordinate when it has no footprint or only a low-confidence one, since an unconfirmed outline can be the wrong building. Nothing is geocoded through a third-party service. `footprint_overrides.csv` lists the 16 properties resolved that way — 14 of them placed, 2 deliberately left unplaced — and the evidence for each. Those reviews were desk checks by AI reviewers (Claude) working to a written rubric, on the same day and to the same standard as the precision checks below; no person has re-checked them, and no imagery was used.

**Density** sums total site energy over properties that reported and have a location, and divides by the area of the cell: constant 400 m hexagons (0.0535 sq mi each), and the 77 community areas assigned by geometry rather than by the name on the row. The area is the whole polygon (`area_sqmi`), including parks, rail yards, rivers and, for lakefront hexagons, water; it is not land area. Every one of the 2,562 reporting properties with a total has a location, so none is left out of either table. Color classes are seven quantiles of the 2022 values, computed once at build time and published in `classes.json`; the map never computes its own.

## Match precision

A match tier says how a footprint was found, not whether it is the right building. To measure that, a seeded random sample of up to 100 matches per tier (406 in all: 100 from each tier except T2, whose 6 matches were all checked) was checked one match at a time against evidence the tier did not use: the footprint's own address range, building name, stories and plan area; the reported floor area against that; the neighboring footprints and their addresses; and the City coordinate. The checks were made by AI reviewers (Claude), one per tier, working to a written rubric, with a sample of their verdicts re-checked; no person has yet re-checked them, and no imagery or site visit was used. Every verdict and its reason is in `match_review.csv`, so any of them can be verified. Precision is correct over correct plus wrong, where one building of a campus counts as correct; `unsure` is left out of it, and the floor counts it as wrong.

| Tier | How the footprint was found | Matches, 2022 | Checked | Right | Wrong | Unsure | Precision (95% CI) |
|---|---|---:|---:|---:|---:|---:|---|
| T1 | The number is in the footprint's address range on the same street | 2,577 | 100 | 96 | 3 | 1 | 97% (91–99%) |
| T1_multi | Several footprints hold the number; all that belong together attached | 146 | 100 | 74 | 15 | 11 | 83% (74–90%) |
| T2 | Same number and street under another direction, confirmed by the coordinate | 6 | 6 | 6 | 0 | 0 | 100% (61–100%) |
| coord_pip | The City coordinate falls inside the footprint | 125 | 100 | 76 | 12 | 12 | 86% (78–92%) |
| coord_nearest | The footprint nearest the City coordinate, within 30 m | 439 | 100 | 53 | 36 | 11 | 60% (49–69%) |

"Right" includes one building of a campus. By `match_confidence`, weighting each tier's checked matches by how many matches it holds at that level:

| Confidence | Matches, 2022 | Estimated precision |
|---|---:|---:|
| high | 2,551 | 99% |
| medium | 626 | 74% |
| low | 116 | 20% |

**About 94% of the outlines the map draws are the right building**, and 91% of all the footprints the tiers attach. A high-confidence match is right about 99 times in 100. A medium one is right about three times in four: the footprint nearest a City coordinate is the weak tier, often a smaller neighbor or the building across the street. Treat a medium-confidence `coord_nearest` outline as the right block rather than the right building. A low-confidence match is right about one time in five, which is why those properties are placed at their City coordinate and not on the outline; the footprint is still listed in `buildings.csv`. That holds for 115 of the 116 low-confidence matches in 2022: the one exception has no trusted coordinate to fall back to, so its outline is drawn for want of anything else, and `located_by` says so. The 16 overridden properties (`footprint_overrides.csv`) are outside the sample.

## The code

`scripts/` is the pipeline that produced every file above, `tests/` is what it has to satisfy, and
`requirements.txt` pins the versions it ran under. Run the tests with `python -m pytest tests`: they
check the published files against the rules, and skip the ones that need a raw snapshot, which is
not published.

| Path | What it does |
|---|---|
| `scripts/fetch_sources.py` | Snapshots the four City datasets. The only stage that touches the network. |
| `scripts/normalize.py`, `scripts/normalize_address.py` | Reads a snapshot into one row per (`id`, `data_year`), and parses each address into number, range, direction, street and type. |
| `scripts/match_footprints.py` | The matcher: the tiers in the order they run, the year guard, the size check, and the overrides applied last. |
| `scripts/build.py` | Everything in `data/processed/`: the total-energy definition, the density tables, the color classes and the dictionary. |
| `scripts/export_site.py` | The files the map reads. |
| `scripts/review_sample.py` | Draws the precision sample and writes one evidence card per sampled match. |
| `scripts/schema.py` | Every fixed decision in one place: display year, status mapping, match target, distance and size thresholds. |
| `scripts/publish.py` | Verifies a release against its own checksums and copies it here. |

What is not in this repository: the raw snapshots, and the working notes and reconciliation report
that go with them. So the code says exactly how these figures were derived and can be rerun, but it
cannot rebuild these bytes on its own — `fetch_sources.py` pulls the portal as it stands today, and
the City revises rows retroactively. Everything published here came from the 2026-09-18 snapshot.

## Before reporting

- **This is a map of where Chicago's large buildings are.** The ordinance covers buildings of 50,000 square feet and more: "less than 1% of Chicago's buildings, which account for approximately 20% of total energy used by all buildings" — the City's own figure, from its description of `xq83-jr8c` on the data portal as it stood at the 2026-09-18 snapshot, written of the 2014–2017 phase-in. This project has not measured that share independently. Single-family homes, small apartment buildings and small commercial buildings are not in it, so these files say nothing about household energy burden, which is where the equity question lives. An empty hexagon means no covered building, not low energy use.
- **Reported, not measured.** Owners report through ENERGY STAR Portfolio Manager, and a third party verifies each property once every three years. The City meters none of it.
- **Do not total energy as EUI × floor area.** For 989 of the 2,562 reporting properties, the City's 2022 floor area is inflated (`gfa_consistent` is false), so that product overstates the city's total by about 12% (64.14 against 57.34 billion kBtu) and any per-square-foot figure recomputed from the floor area is too low. Use `site_energy_kbtu`, and the City's `site_eui_kbtu_sq_ft` for intensity.
- **Not reporting is not low use.** 732 covered properties did not report for 2022 and 236 were exempt. They stay in `energy.csv` with their `status`; the map hatches the non-reporters and outlines the exempt on request rather than dropping them. The release lists at least one building twice: `id` 159288 as not reporting and `id` 256960 as reporting, with an identical floor area (258,445 sq ft) and the same footprint (364553). The year built agrees too, but not in this file: `energy.csv` leaves it blank on 159288's 2022 row, and it is the City's 2021 row for that id that gives 1964.
- **83 properties reported but have no EUI** in the City's release, so no total is formed for them. Eight of them list some fuel use, six of those a single fuel, which is why it is not used. Those cells are blank, not zero.
- **Very high EUIs are kept as published.** Two reporting properties exceed 1,000 kBtu per square foot in 2022: a data center, which is plausible, and a worship facility (`id` 163223), which is almost certainly a reporting error. Neither is corrected or dropped. Check any figure of that size before citing it.
- **Data year 2022 only, and 2023 is next.** The map was built on 2022, and 2023 has not yet been reviewed for it. The 2023 release's own coordinates put only about 9% of rows inside the community area the same row names, but these files never place a building by a release's coordinates. Located the way this project locates, by property `id` and address, 86.5% of 2023's reporting properties match at high or medium confidence, against 87.1% for 2022, and 2023 is the release whose floor areas are consistent. Data years 2024 and 2025 had not been published at the snapshot date.
- **The building outlines are from 2015.** The City's footprint layer has not been updated since. 181 properties that reported for 2022 were built after it and have no outline: they are placed at a City coordinate that passed the community-area test or was accepted on review. A matched outline is whatever stood at that address in 2015.
- **Check `match_confidence` before relying on an outline.** 2,304 of the 2,645 properties that reported for 2022 (87.1%) are matched at high or medium confidence. That is below the 90% the project set before matching. The first release said "above the project's 85% release threshold"; the threshold had been lowered from 90% to 85% the day that result came in, and the result, 86.4%, counted 224 properties the matcher had moved to a neighboring building (77.9% without them). 51 matches of that pattern remain (85.2% without them); in 48 the footprint turned away is a neighbor built a median 24 years after the property, which is the check doing its job. 76 are low and are placed at their City coordinate instead of the unconfirmed outline, and 265 have no footprint. See the precision above for how often each tier is right.
- **Campuses report one figure.** 356 reporting properties cover more than one building. Their figures are property totals, drawn on every attached outline — for most, one — and never divided by footprint area.
- **Many density cells rest on one building.** 591 of the 964 hexagons with a value hold a single reporting property, and 14 community areas hold fewer than five. Read `n_submitted` beside `kbtu_per_sqmi`.
- **Names are as the City publishes them.** `property_name` is carried unedited. On the map, residential properties are labeled by address and type rather than by an owner or LLC name, and so is any property with no type whose name carries an owner, LLC or association token.
- **Portal datasets get revised retroactively.** These figures come from a dated snapshot and will differ from a query run against the live portal today.

## What changed on 2026-09-23

One file is added and one section written; **no figure changed**, and `energy.csv`, `buildings.csv`,
`density_hex.csv`, `density_ca.csv`, `classes.json`, `footprint_overrides.csv` and `match_review.csv`
are byte for byte the files published on 2026-09-22.

- **`facts.json` joins the release.** Every figure the project page states — the counts of covered,
  reporting, non-reporting and exempt properties, what is drawn as an outline or a marker, the total
  reported site energy, the match rate and the precision of the check behind it, the density counts —
  with the display string the page prints, a one-sentence definition, its source file, its sources and
  the rounding applied. The map's own counts are checked against it when the map files are exported,
  so the figures the map quotes and the figures the page states cannot disagree. Percents are on a
  0–100 scale; the density hexagon's width is stated in feet (1312-ft); no entry carries a metric unit.
- **`dictionary.md` describes it.** The dictionary also states this release date.
- **The code is republished** with the module that writes `facts.json` and the test that checks it.

## What changed on 2026-09-22

Documentation and disclosure, after an audit of this repository, the working repository and the
project page. **No figure changed.** `energy.csv`, `density_hex.csv`, `density_ca.csv`,
`classes.json`, `footprint_overrides.csv` and `match_review.csv` are byte for byte the files
published on 2026-09-21. `buildings.csv` differs in 13 `match_note` values, by the wording below and
nothing else; `dictionary.md` is rewritten in the places listed.

- **The sixteen overrides were described as resolved "by hand", here and on the project page.** They
  were desk reviews by AI reviewers (Claude) working to a written rubric, on the same day and to the
  same standard as the match-precision checks, which were already disclosed that way. No person has
  re-checked either, and no imagery was used. Every place that said "by hand" now says what was
  actually done. Fourteen of the sixteen are placed; two are deliberately left unplaced.
- **`scripts/` and `tests/` are published**, with `requirements.txt`. The MIT license and the claim
  of reproducibility had nothing to stand on while the code was private, and the dictionary cited
  files the reader could not see. See [The code](#the-code) for what is here and what is not.
- **The license files are now verbatim.** `LICENSE` holds CC BY 4.0 and `LICENSE-CODE` holds MIT,
  each on its own, so a tool can identify them; the old single file was a composite that none could.
- **The dictionary documents every column.** `footprint_overrides.csv` and `match_review.csv` have
  column tables rather than a sentence each, the empty `data_year` and `community_area_num` cells
  are filled, and the two references to files that are not published are gone.
- **"Every match in the two smallest tiers was checked" was wrong.** Only T2, with 6 matches, was
  checked in full; the next smallest, coord_pip, has 125 and 100 were checked.
- **The low-confidence rule has one exception,** now stated: 115 of the 116 low-confidence matches
  are placed at their City coordinate, and the one with no coordinate keeps its outline.
- **The duplicate-listing example cited a year built that is not in `energy.csv`.** It is on the
  City's 2021 row for that id, not the 2022 row; the shared footprint is named instead.
- **The coverage caveat is attributed.** "Under 1% of buildings, about a fifth of building energy" is
  the City's own figure, from its description of `xq83-jr8c`, not a measurement made here.
- **The map's own caveat now says who checked the matches.** It is a standalone page and was leaning
  on a disclosure that only appears here. Its legend also printed a literal `{year}`.
- **On the project page:** Rush's 2023 coordinate lands in North Center, not Lakeview; the two large
  CSVs are 0.8 MB, not 0.7.

## What changed on 2026-09-21

The first release (2026-09-18) had these errors, all corrected in that release:

- **Total site energy was EUI × floor area**, which the inflated floor areas push about 12% high (64.14 billion kBtu citywide). It is now the fuel total (57.34 billion), and every density figure and color class is rebuilt on it. That README also told readers the opposite of the truth: that the fuel columns were the incomplete side and should not be totaled.
- **UChicago Medicine (1.43 billion kBtu) was not placed**, and so was left out of both density tables without notice; Hyde Park's total is 85% higher with it. It and 12 other unplaced properties are now resolved by override (`footprint_overrides.csv`), and every reporting property is now placed.
- **McCormick Place was placed in Armour Square**, on a small building on S Archer, because every one of its rows names Armour Square and its older coordinates sit at 301 W Cermak. That made Armour Square the sixth most energy-dense community area; it is now 17th. Mount Sinai was drawn in West Town and Hilliard Homes in Lincoln Park, matched to the same address on the North Side. A mirrored match is now refused when the coordinate disagrees, and a low-confidence match is placed at its coordinate.
- **The year check moved 224 properties to a neighboring building**, among them 350 E Cermak, the largest energy user. It now only notes the disagreement where the address and the coordinate agree.
- **174 matches implied more than 60 floors**, one of them 335,000 sq ft on a 39.5 sq ft footprint. The size check now rejects or downgrades them, and also downgrades a match whose floor area needs more than twice the stories the City's layer gives the footprint.
- **The match rate was reported against a threshold lowered on the day of the result**, and no precision had been measured. Both are above. The first precision check, run on the matcher before the last refinements, found the nearest footprint to a coordinate right 56 times in 94 (1 in 17 beyond 30 m) and the nearest address range on the street right 2 times in 10. The coordinate reach was cut to 30 m and the story test added; the nearest-range tier, checked again with a coordinate to confirm it, was right 2 times in 20 and was dropped. The published precision comes from the second check, on the final matcher.
- **Owner, LLC and condominium-association names** appeared on the outlines of properties with no stated type, mostly non-reporters. They are now withheld on the same rule as residential names.
- **The home card and map panel did not add up** (3,613 covered against 2,645 + 732): the 236 exempt properties are now shown.
- **New columns:** `community_area_num` and `community_area_name` (the clean area), `located_by`, `implied_floors`, `size_check`, `site_energy_basis`, `eui_x_gfa_kbtu`, `ratio_to_eui_x_gfa`, `gfa_consistent`. **Renamed:** `land_sqmi` is `area_sqmi`, because it was never land area. **Removed:** `fuel_gap_pct` and `fuel_mix_shown`, which described the fuel columns as the faulty side.
- **Why 2023 is not shown** was stated as a failed coordinate check. That check does not decide whether a year can be mapped here; the accurate reason is above.

## Reuse and corrections

The data and the prose — `data/`, `checksums.sha256`, this README and the dictionary — are under
[CC BY 4.0](LICENSE). The code — `scripts/`, `tests/`, `requirements.txt` — is under
[MIT](LICENSE-CODE). Each file holds that license verbatim and nothing else, so it can be read by a
tool as well as by a person. The figures in `data/` are derived from public records the City of
Chicago publishes on its Data Portal (`xq83-jr8c`, `g5i5-yz37`, `igwz-8jzy`, `syp8-uezg`); the two
licenses above cover this project's selection, derivation and documentation, and do not relicense
the City's underlying records, whose own terms of use govern their reuse.

Suggested attribution: "Chicago building energy, Connor Ulrich Blandford, data release 2026-09-23," with a link to this repository. Cite the release date, the data year and the file you used. Report corrections through [Issues](https://github.com/cub44/Chicago-Building-Energy/issues), including the filename, the property `id` and the disputed value. A footprint matched to the wrong building is a correction worth sending.
