# Chicago building energy

[Explore the project](https://connorblandford.com/projects/chicago-building-energy/). This is the dataset behind an exploratory map; the accompanying article is forthcoming. It covers the properties Chicago's Energy Benchmarking Ordinance requires to report: what each reported for **data year 2022**, which City building footprints it occupies, and how that reported energy is distributed across hexagons and community areas. City release: **5 February 2025**; source snapshot: **18 September 2026**.

No software is required. Open the CSVs in a spreadsheet or your preferred analysis tool.

## Download

| File | Rows | One row represents |
|---|---:|---|
| [energy.csv](data/processed/energy.csv) | 3,613 | One property the ordinance covered in 2022: what it reported, its status, and its total site energy |
| [buildings.csv](data/processed/buildings.csv) | 3,749 | One property, with the City footprints attached to it and how confidently they were matched |
| [density_hex.csv](data/processed/density_hex.csv) | 1,313 | One 400 m hexagon holding at least one benchmarked property |
| [density_ca.csv](data/processed/density_ca.csv) | 77 | One community area |
| [classes.json](data/processed/classes.json) | — | The colour-class boundaries the map uses |
| [dictionary.md](data/processed/dictionary.md) | — | Every column, with its unit, source column and vintage |

See the [data dictionary](data/processed/dictionary.md) for all fields. Join `energy.csv` to `buildings.csv` on `id`. `buildings.csv` is longer because it carries every property seen in any admitted year from 2014 to 2022, not only those covered in 2022, so a left join from `energy.csv` loses nothing. `footprint_ids` holds the City's own `bldg_id`s from `syp8-uezg`, separated by `;`, so any outline can be checked against its source. The two density files aggregate the same properties on two geographies and each sums to the same citywide total: never add them together, and never add either to `energy.csv`. [SHA-256 checksums](checksums.sha256) identify the download versions; verify with `shasum -a 256 -c checksums.sha256`.

## Sources and method

Inputs are the City of Chicago datasets `xq83-jr8c` (Chicago Energy Benchmarking, every data year; rows last updated 5 February 2025), `g5i5-yz37` (Covered Buildings; rows updated 14 March 2025), `igwz-8jzy` (community areas) and `syp8-uezg` (building footprints, a 2015 snapshot), all pulled on 18 September 2026. Raw snapshots and their hashes are preserved privately, and the build reads those snapshots, so the published figures stay reproducible after an upstream revision.

**Total site energy is site EUI × gross floor area**, both as reported, rounded to the kBtu. It is not the sum of the release's five fuel columns: in 2022 those fall short of that product for 989 of the 2,562 properties with an EUI. Defining the total this way keeps the map's two measures consistent — total is exactly energy per square foot times square feet — and the fuel mix is carried beside it but shown only where it agrees within 3%.

**Locations come from matching each reported address to a City building footprint**, not from the coordinates in the release. The matcher runs fixed tiers — address range and street first, then the City's own coordinate as a check or a fallback — and records which tier attached each property in `match_method` and how confidently in `match_confidence`. A City coordinate is used only if it falls inside the community area its own row names, with a 100 m buffer. A property with several buildings is attached to every footprint that belongs to it and its figure is never divided among them. Nothing is geocoded through a third-party service.

**Density** sums total site energy over properties that reported and have a location, and divides by land area: constant 400 m hexagons (0.0535 sq mi each), and the 77 community areas assigned by geometry rather than by the name on the row. Colour classes are seven quantiles of the 2022 values, computed once at build time and published in `classes.json`; the map never computes its own.

## Before reporting

- **This is a map of where Chicago's large buildings are.** The ordinance covers buildings of 50,000 square feet and more: under 1% of the city's buildings and about a fifth of its building energy. Single-family homes, small apartment buildings and small commercial buildings are not in it, so these files say nothing about household energy burden, which is where the equity question lives. An empty hexagon means no covered building, not low energy use.
- **Reported, not measured.** Owners report through ENERGY STAR Portfolio Manager, and a third party verifies each property once every three years. The City meters none of it.
- **Not reporting is not low use.** 732 covered properties did not report for 2022 and 236 were exempt. They stay in `energy.csv` with their `status`; the map hatches them rather than dropping them.
- **83 properties reported but have no EUI** in the City's release, so no total can be formed for them. Those cells are blank, not zero.
- **Very high EUIs are kept as published.** Two reporting properties exceed 1,000 kBtu per square foot in 2022: a data centre, which is plausible, and a worship facility (`id` 163223), which is almost certainly a reporting error. Neither is corrected or dropped. Check any figure of that size before citing it.
- **Data year 2022 only.** In the 2023 release, only about 9% of rows place the building inside the community area the same row names, so 2023 is excluded from every file here rather than drawn in the wrong place. Data years 2024 and 2025 had not been published at the snapshot date.
- **The building outlines are from 2015.** The City's footprint layer has not been updated since. 181 properties that reported for 2022 were built after it and have no outline: they are placed at a City coordinate that passed the community-area test, or not at all. A matched outline is whatever stood at that address in 2015.
- **Check `match_confidence` before relying on an outline.** 2,285 of the 2,645 properties that reported for 2022 (86.4%) are matched at high or medium confidence, above the project's 85% release threshold. 153 are low — an unconfirmed match — and 207 have no footprint.
- **Campuses report one figure.** 356 reporting properties cover more than one building. Their figures are property totals, drawn on every attached outline; do not divide them by footprint area.
- **The fuel breakdown is incomplete.** For 989 of 2,562 properties with an EUI, the five fuel columns fall short of total site energy; `fuel_gap_pct` says by how much, and `fuel_mix_shown` marks the 1,573 where the mix agrees within 3%. Do not total the city's energy by fuel from these columns.
- **Names are as the City publishes them.** `property_name` is carried unedited. On the map, residential properties are labelled by address and type rather than by an owner or LLC name.
- **Portal datasets get revised retroactively.** These figures come from a dated snapshot and will differ from a query run against the live portal today.

## Reuse and corrections

Data and prose are CC BY 4.0; code is MIT. See [LICENSE](LICENSE). The underlying City of Chicago records remain subject to the City's terms of use.

Suggested attribution: "Chicago building energy, Connor Ulrich Blandford, source snapshot 18 September 2026," with a link to this repository. Cite the snapshot date, the data year and the file you used. Report corrections through [Issues](https://github.com/cub44/chicago-building-energy/issues), including the filename, the property `id` and the disputed value. A footprint matched to the wrong building is a correction worth sending.
