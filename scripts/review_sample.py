#!/usr/bin/env python3
"""Draw the match-precision sample and write one evidence card per sampled match.

    python scripts/review_sample.py         # cards for the matches that still need a verdict
    python scripts/review_sample.py --all   # cards for every sampled match

Reads data/interim/ (run scripts/build.py first). Draws up to SAMPLE_SIZE display-year
properties per match_method among those with a footprint attached by the tiers (overrides are
reviewed answers from the AI desk review, recorded in footprint_overrides.csv, and are not
sampled): the SAMPLE_SIZE ids with the smallest SHA-256 of "<SEED>:<id>".
That is a uniform random sample, and a stable one: when a rule change moves a few properties
between tiers, only those few enter or leave the sample. Writes:

    data/interim/review_cards/<method>.md    the evidence for each sampled match that still
                                             needs a verdict (with --all, for every sampled
                                             match, so a verdict can be re-checked)
    data/interim/review_cards/match_review_draft.csv   the sample, with the verdict carried
                                             over from the review sheet (SHEET) wherever
                                             the same id has the same method and footprints;
                                             fill in the rest and copy it over the sheet

The evidence is everything a desk check can use that the tier did not already decide on: the
attached footprint's own address range, building name, stories, year and plan area; the
property's floor area against it; the City coordinate and how far it is; every nearby footprint
with its address; and every footprint on the property's street whose range holds its number,
under any direction. Verdicts go in the review sheet, SHEET, which the build publishes unchanged
as data/processed/match_review.csv:

    correct   the attached footprint is the property's building
    partial   it is one of the property's buildings, and others are not attached (a campus)
    wrong     it is another building
    unsure    the evidence does not decide it

build.py turns the sheet into the precision published in dictionary.md and the README.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely
from shapely.geometry import Point

sys.path.insert(0, str(Path(__file__).resolve().parent))
import schema  # noqa: E402
from match_footprints import name_key  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
INTERIM = ROOT / "data" / "interim"
CARDS = INTERIM / "review_cards"
SEED = 20260921
SAMPLE_SIZE = 100
SHEET_COLUMNS = ["id", "match_method", "match_confidence", "footprint_ids", "verdict", "reason", "reviewed_on"]
SHEET = ROOT / "data" / "review" / "match_review.csv"


def draw_key(i) -> str:
    return hashlib.sha256(f"{SEED}:{int(i)}".encode()).hexdigest()
M = schema.FT_PER_M


def sample(buildings: pd.DataFrame, year: int, energy: pd.DataFrame) -> pd.DataFrame:
    ids = set(energy.loc[energy["data_year"] == year, "id"])
    pool = buildings[buildings["id"].isin(ids) & (buildings["n_footprints"] > 0)
                     & (buildings["match_method"] != "override")].sort_values("id")
    pool = pool.assign(_key=pool["id"].map(draw_key))
    out = [g.sort_values("_key").head(SAMPLE_SIZE) for _, g in pool.groupby("match_method", sort=True)]
    return pd.concat(out).drop(columns="_key").sort_values(["match_method", "id"])


def fids(ids) -> str:
    return ";".join(str(int(x)) for x in ids)


def fp_line(r, pt=None) -> str:
    rng = f"{int(r.f_add1)}-{int(r.t_add1)}" if r.f_add1 else "no range"
    street = " ".join(x for x in [r.pre_dir1, r.st_name1, r.st_type1] if x) or "no street"
    name = " / ".join(x for x in [r.bldg_name1, r.bldg_name2] if x)
    d = "" if pt is None else f", {r.geometry.distance(pt) / M:.0f} m from coordinate" + (" (inside)" if r.geometry.contains(pt) else "")
    return (f"{int(r.bldg_id)}: {rng} {street}" + (f" [{name}]" if name else "")
            + f", {int(r.stories) if r.stories else '?'} stories, built {int(r.year_built) if r.year_built else '?'}, "
              f"{r.area_sqft:,.0f} sq ft{d}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--all", action="store_true",
                    help="write a card for every sampled match, not only those still needing a verdict")
    a = ap.parse_args()
    b = pd.read_parquet(INTERIM / "buildings.parquet")
    e = pd.read_parquet(INTERIM / "energy_long.parquet")
    fp = gpd.read_parquet(INTERIM / "footprints.parquet").drop_duplicates("bldg_id", keep=False)
    fp["key"] = fp["st_name1"].map(name_key)
    fpi = fp.set_index("bldg_id", drop=False)
    tree = shapely.STRtree(fp.geometry.values)
    y = schema.DISPLAY_YEARS[-1]
    rows = e[e["data_year"] == y].set_index("id")
    s = sample(b, y, e)
    done = {}
    if SHEET.exists():
        prev = pd.read_csv(SHEET, dtype=str, keep_default_na=False)
        done = {(r.id, r.match_method, r.footprint_ids): r for r in prev.itertuples() if r.verdict}
    s["_done"] = [(str(r.id), r.match_method, fids(r.footprint_ids)) in done for r in s.itertuples()]
    CARDS.mkdir(parents=True, exist_ok=True)
    for old in CARDS.glob("*.md"):
        old.unlink()
    for method, g in (s if a.all else s[~s["_done"]]).groupby("match_method", sort=True):
        L = [f"# {method}: {len(g)} sampled of {int(((b['match_method'] == method) & b['id'].isin(rows.index)).sum())}", ""]
        for r in g.itertuples():
            row = rows.loc[r.id]
            pt = None if pd.isna(r.trusted_x_ft) else Point(r.trusted_x_ft, r.trusted_y_ft)
            att = fpi.loc[[int(x) for x in r.footprint_ids]]
            L += [f"## id {r.id}", "",
                  f"- Property: {row['property_name'] or '(no name)'} | {row['address']} | "
                  f"{row['primary_property_type'] or 'type not stated'} | {row['status']}",
                  f"- Reported: {row['gross_floor_area_buildings_sq_ft']:,.0f} sq ft gross floor area"
                  if not pd.isna(row["gross_floor_area_buildings_sq_ft"]) else "- Reported: no floor area",
                  f"  | built {row['year_built'] if not pd.isna(row['year_built']) else '?'} | "
                  f"{row['of_buildings'] if not pd.isna(row['of_buildings']) else '?'} building(s) | "
                  f"stated community area {row['community_area'] or 'none'}",
                  f"- Parsed address: {r.address_norm or 'did not parse'}",
                  f"- City coordinate: {r.coord_source}" + ("" if pt is None else f" ({r.trusted_lat:.6f}, {r.trusted_lon:.6f})"),
                  f"- Match: {r.match_method}/{r.match_confidence}; implied floors "
                  f"{'' if pd.isna(r.implied_floors) else f'{r.implied_floors:,.1f}'}; note: {r.match_note or '-'}",
                  "- Attached:"]
            L += [f"  - {fp_line(f, pt)}" for f in att.itertuples()]
            geom = shapely.union_all(att.geometry.values)
            near = tree.query(geom, predicate="dwithin", distance=80 * M)
            near = fp.iloc[near]
            near = near[~near["bldg_id"].isin(att["bldg_id"])].copy()
            near["d"] = near.geometry.distance(geom)
            L += ["- Other footprints within 80 m of the attached one (nearest first):"]
            L += [f"  - {fp_line(f, pt)}, {f.d / M:.0f} m from the attached" for f in near.sort_values("d").head(8).itertuples()] or ["  - none"]
            if r.address_norm and not pd.isna(row["addr_number"]):
                n = int(row["addr_number"])
                hi = int(row["addr_number_hi"]) if not pd.isna(row["addr_number_hi"]) else n
                on = fp[(fp["key"] == name_key(row["addr_st_name"])) & (fp["f_add1"] <= hi) & (fp["t_add1"] >= n)
                        & ((fp["f_add1"] % 2) == (n % 2))].copy()
                on["d"] = on.geometry.distance(geom)
                L += [f"- Footprints on {row['addr_st_name']} whose range holds {n if hi == n else f'{n}-{hi}'}, any direction:"]
                L += [f"  - {fp_line(f, pt)}, {f.d / M:.0f} m from the attached" for f in on.sort_values("d").head(6).itertuples()] or ["  - none"]
            L += [""]
        (CARDS / f"{method}.md").write_text("\n".join(L), encoding="utf-8")
    with (CARDS / "match_review_draft.csv").open("w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(SHEET_COLUMNS)
        for r in s.itertuples():
            k = (str(r.id), r.match_method, fids(r.footprint_ids))
            old = done.get(k)
            w.writerow([r.id, r.match_method, r.match_confidence, k[2],
                        old.verdict if old else "", old.reason if old else "", old.reviewed_on if old else ""])
    t = s.groupby("match_method").agg(sampled=("id", "size"), carried_over=("_done", "sum"))
    print(t.to_string())
    carded = len(s) if a.all else int((~s["_done"]).sum())
    print(f"cards for {carded} matches ({int((~s['_done']).sum())} still to review), and the draft sheet, "
          f"in {CARDS.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
