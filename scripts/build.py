#!/usr/bin/env python3
"""Stage 3 - build. data/interim/ in; data/processed/ and data/reconciliation.md out.

data/reconciliation.md is the build's working report of what did not resolve. It is kept
with the raw snapshot and is not published; data/processed/ is the release.

    python scripts/build.py                  # normalize -> match -> processed tables
    python scripts/build.py --processed-only # reuse data/interim/ as it stands

Reads only what the earlier stages wrote from data/raw/. Never fetches. Every file written
here is a function of the raw snapshot and data/overrides/ alone - no clock, no locale, no
unordered iteration - so a rebuild from the same snapshot is byte-identical, which a rebuild
followed by `git diff --exit-code data/processed` verifies.

Nothing from a year in schema.EXCLUDED_YEARS is written to data/processed/. The
working report names an excluded year only to show the test it failed.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import schema  # noqa: E402
from normalize import display_name  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
INTERIM = ROOT / "data" / "interim"
PROCESSED = ROOT / "data" / "processed"
RECONCILIATION = ROOT / "data" / "reconciliation.md"   # the working report; not published
OVERRIDES = ROOT / "data" / "overrides" / "footprint_overrides.csv"
REVIEW = ROOT / "data" / "review" / "match_review.csv"  # published unchanged as data/processed/match_review.csv
REVIEW_COLUMNS = ["id", "match_method", "match_confidence", "footprint_ids", "verdict", "reason", "reviewed_on"]
VERDICTS = ["correct", "partial", "wrong", "unsure"]

BUILDINGS_COLUMNS = [
    "id", "address_raw", "address_norm", "community_area", "primary_property_type_latest",
    "year_built", "of_buildings_latest", "trusted_lat", "trusted_lon", "coord_source",
    "footprint_ids", "n_footprints", "footprint_year_built", "footprint_area_sqft",
    "implied_floors", "size_check", "match_method", "match_confidence", "match_note",
    "located_by", "community_area_num", "community_area_name",
]
ENERGY_COLUMNS = [
    "id", "data_year", "property_name", "address", "zip_code", "community_area",
    "community_area_num", "primary_property_type", "reporting_status", "status",
    "exempt_from_chicago_energy_rating", *schema.NUMERIC_COLUMNS,
    "site_energy_kbtu", "site_energy_basis", "fuel_sum_kbtu", "eui_x_gfa_kbtu",
    "ratio_to_eui_x_gfa", "gfa_consistent",
]
DENSITY_MEASURES = ["n_properties", "n_submitted", "n_not_submitted", "site_energy_kbtu",
                    "ghg_tco2e", "area_sqmi", "kbtu_per_sqmi"]
HEX_COLUMNS = ["hex_id", "data_year", *DENSITY_MEASURES]
CA_COLUMNS = ["community_area_num", "name", "display_name", "data_year", *DENSITY_MEASURES, "share_not_submitted"]

HEX_W_FT = schema.HEX_SIZE_M * schema.FT_PER_M            # flat-to-flat width = center spacing
HEX_DY_FT = HEX_W_FT * math.sqrt(3) / 2                   # row spacing, pointy-top hexagons
HEX_SQMI = (math.sqrt(3) / 2) * HEX_W_FT ** 2 / schema.SQFT_PER_SQMI


# --- deterministic writers ----------------------------------------------------------------------------
def fmt(v) -> str:
    """None/NaN -> empty. Booleans lower-case. Floats lose trailing zeros. Lists ';'-joined."""
    if isinstance(v, (list, tuple, np.ndarray)):
        return ";".join(str(int(x)) for x in v)
    if v is None or v is pd.NA or (isinstance(v, float) and math.isnan(v)):
        return ""
    if isinstance(v, (bool, np.bool_)):
        return "true" if v else "false"
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    if isinstance(v, (float, np.floating)):
        return str(int(v)) if float(v).is_integer() else f"{float(v):.7f}".rstrip("0").rstrip(".")
    return str(v)


def write_csv(path: Path, columns: list[str], frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(columns)
        for row in frame[columns].itertuples(index=False):
            w.writerow([fmt(v) for v in row])


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def write_checksums(folder: Path) -> int:
    lines = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}"
             for p in sorted(folder.glob("*")) if p.is_file() and p.name != "checksums.sha256"]
    (folder / "checksums.sha256").write_text("\n".join(lines) + "\n")
    return len(lines)


# --- where each property is, for density and for markers --------------------------------------------
def located_by(r) -> str:
    """Where a property is placed, for density, markers and community_area_num.
    A footprint, unless the match is low confidence and a trusted coordinate exists: an
    unconfirmed footprint can be a North/South mirror miles away, and the coordinate has
    passed the community-area test (or been accepted on review). Then the trusted coordinate;
    then nothing."""
    has_coord = not pd.isna(r.trusted_x_ft)
    if len(r.footprint_ids) and not (r.match_confidence == "low" and has_coord):
        return "footprint"
    return "trusted_coordinate" if has_coord else "none"


def property_points(buildings: pd.DataFrame, footprints: gpd.GeoDataFrame) -> pd.DataFrame:
    """One point per id: the centroid of the largest attached footprint, or the trusted
    coordinate, or nothing, as located_by() decides."""
    fp = footprints.drop_duplicates("bldg_id", keep=False).set_index("bldg_id")
    rows = []
    for r in buildings.itertuples(index=False):
        how = located_by(r)
        if how == "footprint":
            sub = fp.loc[[int(x) for x in r.footprint_ids]].sort_values(["area_sqft"], ascending=False, kind="stable")
            c = sub.geometry.iloc[0].centroid
            rows.append((r.id, c.x, c.y, how))
        elif how == "trusted_coordinate":
            rows.append((r.id, float(r.trusted_x_ft), float(r.trusted_y_ft), how))
        else:
            rows.append((r.id, None, None, how))
    return pd.DataFrame(rows, columns=["id", "x_ft", "y_ft", "located_by"])


def hex_of(x: float, y: float) -> tuple[int, int]:
    """(col, row) of the pointy-top hexagon whose center is nearest. The grid is anchored at
    the EPSG:3435 origin, so a hex_id means the same place in every build."""
    r0 = int(math.floor(y / HEX_DY_FT))
    best = None
    for row in (r0 - 1, r0, r0 + 1, r0 + 2):
        off = 0.5 if row % 2 else 0.0
        c0 = int(math.floor(x / HEX_W_FT - off))
        for col in (c0 - 1, c0, c0 + 1, c0 + 2):
            d = (x - (col + off) * HEX_W_FT) ** 2 + (y - row * HEX_DY_FT) ** 2
            if best is None or (d, row, col) < best:
                best = (d, row, col)
    return best[2], best[1]


def hex_id(col: int, row: int) -> str:
    return f"r{row}c{col}"


def locate(points: pd.DataFrame, cas: gpd.GeoDataFrame) -> pd.DataFrame:
    """Add hex_id and community_area_num by geometry, never by the row's stated name."""
    have = points[points["x_ft"].notna()].copy()
    have["hex_id"] = [hex_id(*hex_of(x, y)) for x, y in zip(have["x_ft"], have["y_ft"])]
    g = gpd.GeoDataFrame(have, geometry=gpd.points_from_xy(have["x_ft"], have["y_ft"]), crs=schema.CRS_PLANE)
    polys = list(zip(cas["area_numbe"], cas.geometry))
    nums, snapped = [], []
    for p in g.geometry:
        inside = [n for n, poly in polys if poly.contains(p)]
        if inside:
            nums.append(int(inside[0]))
            snapped.append(False)
        else:   # on the lake edge or a boundary sliver: the nearest area, and counted
            nums.append(int(min(polys, key=lambda t: (t[1].distance(p), t[0]))[0]))
            snapped.append(True)
    have["community_area_num"] = nums
    have["ca_by_nearest"] = snapped
    return points.merge(have[["id", "hex_id", "community_area_num", "ca_by_nearest"]], on="id", how="left")


def density(year_rows: pd.DataFrame, located: pd.DataFrame, cas: gpd.GeoDataFrame, year: int):
    """-> (hex frame, ca frame, included property ids). A property is INCLUDED in the energy
    sums when it is submitted, has a site energy, and has a point. It is COUNTED in
    n_properties whenever it has a point, whatever its status."""
    d = year_rows.drop(columns=["community_area_num"], errors="ignore").merge(located, on="id", how="left")
    d = d[d["hex_id"].notna()].copy()
    d["included"] = (d["status"] == "submitted") & d["site_energy_kbtu"].notna()

    def measures(g: pd.DataFrame, area_sqmi: float) -> dict:
        inc = g[g["included"]]
        energy = int(inc["site_energy_kbtu"].sum()) if len(inc) else None
        ghg = inc["total_ghg_emissions_metric_tons_co2e"].dropna()
        return {"n_properties": len(g), "n_submitted": int((g["status"] == "submitted").sum()),
                "n_not_submitted": int((g["status"] == "not_submitted").sum()),
                "site_energy_kbtu": energy,
                "ghg_tco2e": round(float(ghg.sum()), 1) if len(ghg) else None,
                "area_sqmi": round(area_sqmi, 6),
                "kbtu_per_sqmi": None if energy is None else round(energy / area_sqmi, 1)}

    hexes = [{"hex_id": h, "data_year": year, **measures(g, HEX_SQMI)}
             for h, g in sorted(d.groupby("hex_id"), key=lambda t: t[0])]
    by_ca = dict(list(d.groupby("community_area_num")))
    areas = []
    for n, name, poly in zip(cas["area_numbe"], cas["community"], cas.geometry):
        g = by_ca.get(n, d.iloc[0:0])
        m = measures(g, poly.area / schema.SQFT_PER_SQMI)
        required = m["n_submitted"] + m["n_not_submitted"]
        m["share_not_submitted"] = round(m["n_not_submitted"] / required, 4) if required else None
        areas.append({"community_area_num": int(n), "name": name, "display_name": display_name(name),
                      "data_year": year, **m})
    return pd.DataFrame(hexes, columns=HEX_COLUMNS), pd.DataFrame(areas, columns=CA_COLUMNS), d


# --- match precision ------------------------------------------------------------------------------------
def wilson(k: int, n: int, z: float = 1.96, digits: int | None = 4) -> tuple[float, float] | tuple[None, None]:
    """Wilson 95% interval for k of n, rounded to `digits` places (None: unrounded)."""
    if n == 0:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    lo, hi = (c - h) / d, (c + h) / d
    return (lo, hi) if digits is None else (round(lo, digits), round(hi, digits))


def match_precision(buildings: pd.DataFrame, energy_display: pd.DataFrame) -> dict | None:
    """Precision per match_method from the review sheet (REVIEW, published as match_review.csv),
    a desk check of a seeded sample (scripts/review_sample.py). precision = (correct + partial) /
    (correct + partial + wrong); `unsure` is left out of it and reported, and `floor` counts it as
    wrong. A reviewed row whose method or footprints no longer match the build is stale and not
    counted. The published figures are rounded to 4 places; `unrounded` keeps the weighted ones
    exact, so a report that formats them rounds once."""
    if not REVIEW.exists():
        return None
    rv = pd.read_csv(REVIEW, dtype=str, keep_default_na=False)
    if list(rv.columns) != REVIEW_COLUMNS:
        raise SystemExit(f"{REVIEW.name}: header must be {','.join(REVIEW_COLUMNS)}")
    bad = sorted(set(rv["verdict"]) - set(VERDICTS))
    if bad:
        raise SystemExit(f"{REVIEW.name}: unknown verdicts {bad}")
    cur = buildings.set_index("id")
    ids = rv["id"].astype("int64")
    now = [(cur.loc[i, "match_method"], fmt(list(cur.loc[i, "footprint_ids"]))) if i in cur.index else (None, None)
           for i in ids]
    rv["stale"] = [(m, f) != (a, b) for (m, f), a, b in zip(now, rv["match_method"], rv["footprint_ids"])]
    shown = set(energy_display["id"])
    tier = buildings[buildings["id"].isin(shown) & (buildings["n_footprints"] > 0) & (buildings["match_method"] != "override")]
    pop = tier["match_method"].value_counts()
    out = {"reviewed": int(len(rv)), "stale": int(rv["stale"].sum()), "by_method": {}}
    live = rv[~rv["stale"]]
    num = num_exact = den = 0.0
    for m in [x for x in schema.MATCH_METHODS if x in set(live["match_method"])]:
        g = live[live["match_method"] == m]
        c = {v: int((g["verdict"] == v).sum()) for v in VERDICTS}
        ok, decided = c["correct"] + c["partial"], c["correct"] + c["partial"] + c["wrong"]
        lo, hi = wilson(ok, decided)
        prec = round(ok / decided, 4) if decided else None
        out["by_method"][m] = {"population": int(pop.get(m, 0)), "reviewed": int(len(g)), **c,
                               "precision": prec, "ci95": [lo, hi],
                               "floor": round(ok / len(g), 4) if len(g) else None}
        if prec is not None:
            num += pop.get(m, 0) * prec
            num_exact += pop.get(m, 0) * ok / decided
            den += pop.get(m, 0)
    out["weighted_precision"] = round(num / den, 4) if den else None
    out["population"] = int(den)

    # By confidence, and over the outlines the map draws: the sample is stratified by tier, so
    # each (tier, confidence) cell's precision is weighted by how many matches that cell holds.
    cur = buildings.assign(id=buildings["id"].astype(str)).set_index("id")
    live = live.assign(conf_now=live["id"].map(cur["match_confidence"]), located_by=live["id"].map(cur["located_by"]))

    def weighted(pop_mask, sample_mask) -> tuple[dict, float | None]:
        n_pop = w_sum = covered = 0.0
        for (m, c), cell in tier[pop_mask].groupby(["match_method", "match_confidence"]):
            g = live[sample_mask & (live["match_method"] == m) & (live["conf_now"] == c)]
            ok, bad = int(g["verdict"].isin(["correct", "partial"]).sum()), int((g["verdict"] == "wrong").sum())
            n_pop += len(cell)
            if ok + bad:
                w_sum += len(cell) * ok / (ok + bad)
                covered += len(cell)
        exact = w_sum / covered if covered else None
        return ({"matches": int(n_pop), "estimated_precision": None if exact is None else round(exact, 4),
                 "share_with_a_checked_cell": round(covered / n_pop, 4) if n_pop else None}, exact)
    by_conf = {c: weighted(tier["match_confidence"] == c, live["conf_now"] == c) for c in ("high", "medium", "low")}
    drawn, drawn_exact = weighted(tier["located_by"] == "footprint", live["located_by"] == "footprint")
    out["by_confidence"] = {c: d for c, (d, _) in by_conf.items()}
    out["drawn_outlines"] = drawn
    out["unrounded"] = {"weighted_precision": num_exact / den if den else None, "drawn_outlines": drawn_exact,
                        "by_confidence": {c: e for c, (_, e) in by_conf.items()}}
    return out


# --- class breaks -----------------------------------------------------------------------------------------
def sig3(x: float) -> float:
    if x == 0 or not math.isfinite(x):
        return 0.0
    r = round(x, -int(math.floor(math.log10(abs(x)))) + 2)
    return int(r) if float(r).is_integer() else r


def quantile_breaks(values) -> dict:
    """Six interior breaks for seven classes: quantiles k/7 (linear interpolation), rounded
    to three significant figures, ascending and distinct. Class k of 7 holds
    breaks[k-2] <= value < breaks[k-1]; class 1 is everything below breaks[0]."""
    v = np.sort(np.asarray([float(x) for x in values if x is not None and not pd.isna(x)]))
    qs = [sig3(float(np.quantile(v, k / schema.N_CLASSES))) for k in range(1, schema.N_CLASSES)]
    breaks = []
    for q in qs:
        if not breaks or q > breaks[-1]:
            breaks.append(q)
    lo, hi = float(v[0]), float(v[-1])
    return {"n": int(len(v)), "min": int(lo) if lo.is_integer() else lo,
            "max": int(hi) if hi.is_integer() else hi, "breaks": breaks}


def classes(energy: pd.DataFrame, hexes: pd.DataFrame, areas: pd.DataFrame) -> dict:
    out = {"n_classes": schema.N_CLASSES,
           "method": "quantiles k/7 of the display-year values, rounded to 3 significant figures; "
                     "class k holds breaks[k-2] <= value < breaks[k-1]",
           "metrics": {
               "site_eui": {"unit": "kBtu per sq ft per year", "column": "site_eui_kbtu_sq_ft",
                            "population": "submitted properties with a published site EUI", "years": {}},
               "site_energy_kbtu": {"unit": "kBtu per year", "column": "site_energy_kbtu",
                                    "population": "submitted properties with a published site EUI", "years": {}},
               "kbtu_per_sqmi": {"unit": "kBtu per year per square mile", "column": "kbtu_per_sqmi",
                                 "population": "hexagons / community areas with at least one included property",
                                 "years": {}}}}
    for y in schema.DISPLAY_YEARS:
        s = energy[(energy["data_year"] == y) & (energy["status"] == "submitted")]
        out["metrics"]["site_eui"]["years"][str(y)] = quantile_breaks(s["site_eui_kbtu_sq_ft"])
        out["metrics"]["site_energy_kbtu"]["years"][str(y)] = quantile_breaks(s["site_energy_kbtu"])
        out["metrics"]["kbtu_per_sqmi"]["years"][str(y)] = {
            "hex": quantile_breaks(hexes.loc[hexes["data_year"] == y, "kbtu_per_sqmi"]),
            "ca": quantile_breaks(areas.loc[areas["data_year"] == y, "kbtu_per_sqmi"])}
    return out


# --- reconciliation: the working report of what did not resolve (not published) --------------------------
def likely_cause(r) -> str:
    if r.match_method == "override":
        return "I. Resolved by override: no footprint exists or none can be verified (see section 15)"
    if not pd.isna(r.year_built) and int(r.year_built) > schema.FOOTPRINT_VINTAGE_YEAR:
        return "A. Built after the City's 2015 footprint layer (drawn as a point; no footprint exists to override to)"
    if r.size_check in ("fail", "doubtful"):
        return "B. The size check rejected or downgraded the footprint the tiers found (section 4)"
    if len(r.guard_rejected_ids):
        return "C. The year guard turned away a footprint that the address and the coordinate did not agree on within 30 m"
    if r.match_method in ("T2", "T1_multi"):
        return "E. Address-only match with no trusted coordinate to confirm it"
    if "is not in the footprint table" in r.match_note or "did not parse" in r.match_note:
        return "F. Street name not in the footprint table, or the address does not parse (typo, place name, new street)"
    if r.coord_source == "none":
        return "G. House number in no footprint range, and no coordinate passes the community-area test"
    return "H. House number in no footprint range, and no footprint within 30 m of the trusted coordinate"


def md_table(header: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(str(c).replace("|", "/") for c in row) + " |" for row in rows]
    return out


def n(v) -> str:
    return "" if v is None or pd.isna(v) else f"{int(v):,}"


def pct(v) -> str:
    return "" if v is None else f"{v:.1%}"


def reconciliation(energy, buildings, located, summary, match, rejected, year_density, overrides, prec) -> str:
    L: list[str] = []
    snap, fp = summary["snapshot"], summary["footprints"]
    L += ["# Reconciliation - chicago-building-energy", "",
          f"Regenerated by `scripts/build.py` from the raw snapshot of {snap}. Do not edit: fix the "
          "input (an override row, the parser, `schema.py`) and rebuild.", "",
          f"Display years: {schema.DISPLAY_YEARS}. Excluded years: "
          + "; ".join(f"{y} ({why})" for y, why in schema.EXCLUDED_YEARS.items()) + ".", ""]
    b_all = buildings

    for y in schema.DISPLAY_YEARS:
        rows_y = energy[energy["data_year"] == y]
        b = b_all.merge(rows_y[["id", "status", "site_energy_kbtu", "primary_property_type"]], on="id")
        L += [f"## 1. Footprint match, data year {y}", ""]
        for label in ("all", "submitted"):
            t = match["by_year"][str(y)][label]
            met = "met" if t["high_or_medium_share"] >= schema.MATCH_TARGET else "**not met**"
            L += [f"**{label.capitalize()} properties: {t['properties']:,}.** High or medium: "
                  f"{t['high_or_medium']:,} ({t['high_or_medium_share']:.1%}). Of those, {t['displaced']:,} are "
                  f"displaced matches (section 3); without them {t['high_or_medium_share_without_displaced']:.1%}. "
                  + (f"Release target, set before matching: {schema.MATCH_TARGET:.0%} of submitted properties - {met}."
                     if label == "submitted" else ""),
                  f"Size check: " + ", ".join(f"{k} {v:,}" for k, v in t["size_check"].items()) + ".", ""]
            L += md_table(["method", *schema.MATCH_CONFIDENCES, "total"],
                          [[m, *[row.get(c, 0) for c in schema.MATCH_CONFIDENCES], sum(row.values())]
                           for m, row in t["by_method"].items()]
                          + [["**all**", *[t["by_confidence"][c] for c in schema.MATCH_CONFIDENCES], t["properties"]]])
            L += [""]
        lb = b["located_by"].value_counts()
        low_pt = int(((b["match_confidence"] == "low") & (b["located_by"] == "trusted_coordinate")).sum())
        L += [f"Trusted coordinate source, all {match['ids']:,} ids: "
              + ", ".join(f"{k} {v:,}" for k, v in match["coord_source"].items()) + ".", "",
              f"Located, data year {y}: by footprint {lb.get('footprint', 0):,}; by trusted coordinate "
              f"{lb.get('trusted_coordinate', 0):,} (of which {low_pt:,} have a low-confidence footprint that is "
              f"listed but not used); not located {lb.get('none', 0):,}.", ""]

        open_ = b[~b["match_confidence"].isin(["high", "medium"])].copy()
        open_["cause"] = [likely_cause(r) for r in open_.itertuples()]
        open_ = open_.sort_values(["site_energy_kbtu", "id"], ascending=[False, True], na_position="last")
        L += [f"## 2. Not at high or medium confidence, data year {y}: {len(open_):,} properties, by likely cause", "",
              "Within each group the largest reported site energy comes first, because that is the "
              "order in which an override changes the map most. `drawn` is what the map does today: "
              "`point` at the trusted coordinate, `footprint (low)` where there is no coordinate to "
              "prefer, or `listed` only.", ""]
        counts = open_.groupby("cause").agg(properties=("id", "size"),
                                            submitted=("status", lambda s: int((s == "submitted").sum())),
                                            kbtu=("site_energy_kbtu", "sum"))
        L += md_table(["likely cause", "properties", "submitted", "site energy, kBtu"],
                      [[c, f"{r.properties:,}", f"{r.submitted:,}", n(r.kbtu)] for c, r in counts.iterrows()])
        L += [""]
        drawn = {"footprint": "footprint (low)", "trusted_coordinate": "point", "none": "listed"}
        for cause, g in open_.groupby("cause", sort=True):
            L += [f"### {cause} ({len(g):,})", ""]
            L += md_table(["id", "status", "address", "property type", "site energy, kBtu", "match", "drawn", "note"],
                          [[r.id, r.status, r.address_raw, r.primary_property_type or "", n(r.site_energy_kbtu),
                            f"{r.match_method}/{r.match_confidence}", drawn[r.located_by], r.match_note]
                           for r in g.itertuples()])
            L += [""]

        guarded = b[(b["guard_rejected_ids"].map(len) > 0)].copy()
        advised = b[(b["guard_advisory_ids"].map(len) > 0)]
        disp = b[b["guard_rejected_min_dist_m"].notna() & (b["guard_rejected_min_dist_m"] <= schema.NEAREST_MEDIUM_M)
                 & b["match_method"].isin(["coord_pip", "coord_nearest"]) & b["match_confidence"].isin(["high", "medium"])]
        L += [f"## 3. The year guard, data year {y}", "",
              f"The year guard (match_footprints.py, rule 9) rejects a footprint whose `year_built` is more than "
              f"{schema.YEAR_GUARD_YEARS} years after the benchmarking `year_built`, except where the address and the "
              f"trusted coordinate agree on it (it holds the address and lies within {schema.NEAREST_MEDIUM_M:.0f} m of the "
              "coordinate): there it only notes the disagreement. It was advisory "
              f"for **{len(advised):,}** properties ({int((advised['status'] == 'submitted').sum()):,} submitted), which "
              f"keep the footprint their address and coordinate agree on. It still turned away at least one candidate "
              f"for **{len(guarded):,}** ({int((guarded['status'] == 'submitted').sum()):,} submitted). "
              f"**{len(disp):,}** of those are displaced: the rejected footprint is within {schema.NEAREST_MEDIUM_M:.0f} m "
              "of the trusted coordinate, and a coordinate tier then attached another footprint at high or medium - in a "
              "built-up block, possibly a neighbor. Section 1 reports the rate with and without them. The 2026-09-18 "
              "release, before the exception, had 224 displaced submitted properties.", "",
              f"Of the {len(disp):,} displaced, {int(disp['guard_rejected_tiers'].map(lambda t: 'address' in list(t)).sum()):,} "
              "had an address footprint turned away (the address and the coordinate did not agree within "
              f"{schema.NEAREST_MEDIUM_M:.0f} m); the rest had only a neighboring footprint near the coordinate turned away, "
              "built a median of "
              f"{pd.Series([min(int(v) for v in ys) - int(yb) for ys, yb in zip(disp['guard_rejected_years'], disp['year_built'])]).median():.0f} "
              "years after the property - the guard doing its job, and the pattern counted only because the audit defined it so.", ""]
        gl = guarded.sort_values(["site_energy_kbtu", "id"], ascending=[False, True], na_position="last")
        L += md_table(["id", "address", "property built", "rejected bldg_id (built)", "rejected, m from coordinate",
                       "matched instead", "displaced", "site energy, kBtu"],
                      [[r.id, r.address_raw, n(r.year_built),
                        ", ".join(f"{int(i)} ({int(yr)})" for i, yr in zip(r.guard_rejected_ids, r.guard_rejected_years)),
                        "" if pd.isna(r.guard_rejected_min_dist_m) else f"{r.guard_rejected_min_dist_m:.0f}",
                        f"{r.match_method}/{r.match_confidence} " + fmt(list(r.footprint_ids)),
                        "yes" if r.id in set(disp["id"]) else "", n(r.site_energy_kbtu)]
                       for r in gl.itertuples()])
        L += [""]

        sz = b[b["size_check"].isin(["fail", "doubtful"])].sort_values(["size_check", "implied_floors"], ascending=[False, False])
        L += [f"## 4. Size check, data year {y}: {int((sz['size_check'] == 'fail').sum()):,} rejected, "
              f"{int((sz['size_check'] == 'doubtful').sum()):,} downgraded", "",
              f"`implied_floors` is gross floor area over attached footprint area, a campus's floor area first scaled by "
              f"attached footprints over reported buildings. Over {schema.SIZE_FAIL_FLOORS} floors the footprint is "
              f"rejected; over {schema.SIZE_DOUBT_FLOORS}, or over {schema.SIZE_STORY_FACTOR} times the stories the "
              f"City's layer gives the attached footprints, the match drops one confidence step, unless the layer gives "
              f"{schema.SIZE_TOWER_STORIES} or more stories.", ""]
        L += md_table(["id", "address", "check", "implied floors", "result", "note"],
                      [[r.id, r.address_raw, r.size_check, f"{r.implied_floors:,.0f}",
                        f"{r.match_method}/{r.match_confidence}", r.match_note.split("size check: ")[-1]]
                       for r in sz.itertuples()])
        L += [""]

        d, stats = year_density[y]
        no_point = rows_y.drop(columns=["community_area_num"]).merge(located, on="id", how="left")
        no_point = no_point[no_point["hex_id"].isna()]
        sub_no_point = no_point[(no_point["status"] == "submitted") & no_point["site_energy_kbtu"].notna()]
        L += [f"## 5. Density, data year {y}", "",
              f"Included in the sums (submitted, a site energy, and a location): **{stats['included']:,}** "
              f"properties, **{stats['site_energy_kbtu']:,} kBtu**. Excluded for having no location: "
              f"{len(sub_no_point):,} submitted properties carrying {n(sub_no_point['site_energy_kbtu'].sum()) or 0} kBtu "
              f"({len(no_point):,} properties of any status). Placed in a community area by nearest polygon rather "
              f"than containment: {stats['ca_by_nearest']:,}. Hexagons with a value: {stats['hex_with_value']:,}, of "
              f"which {stats['hex_one_property']:,} hold a single reporting property and "
              f"{stats['hex_two_or_fewer']:,} two or fewer. Community areas with fewer than five reporting "
              f"properties: {stats['ca_under_five']:,}.", ""]
        if len(no_point):
            L += md_table(["id", "status", "address", "site energy, kBtu"],
                          [[r.id, r.status, r.address, n(r.site_energy_kbtu)] for r in
                           no_point.sort_values(["site_energy_kbtu", "id"], ascending=[False, True], na_position="last").itertuples()])
            L += [""]

        sub = rows_y[rows_y["status"] == "submitted"]
        no_eui = sub[sub["site_eui_kbtu_sq_ft"].isna()].sort_values("id")
        L += [f"## 6. Submitted with no EUI published, data year {y}: {len(no_eui):,}", "",
              "Drawn as \"reported; no EUI published\". No total is formed for them: without an EUI the record is one "
              f"Portfolio Manager did not complete. {int((no_eui['fuel_sum_kbtu'] > 0).sum()):,} list some fuel use "
              f"({n(no_eui.loc[no_eui['fuel_sum_kbtu'] > 0, 'fuel_sum_kbtu'].sum())} kBtu), mostly a single fuel.", ""]
        L += md_table(["id", "address", "property type", "gross floor area, sq ft", "fuel columns, kBtu"],
                      [[r.id, r.address, r.primary_property_type or "", n(r.gross_floor_area_buildings_sq_ft),
                        n(None if pd.isna(r.fuel_sum_kbtu) else round(r.fuel_sum_kbtu))]
                       for r in no_eui.itertuples()])
        L += [""]
        out = sub[sub["site_eui_kbtu_sq_ft"] > 1000].sort_values(["site_eui_kbtu_sq_ft", "id"], ascending=[False, True])
        L += [f"## 7. Site EUI above 1,000 kBtu per sq ft, data year {y}: {len(out):,}", "",
              "Kept and flagged, never dropped. A data center can be real at this level; a worship "
              "facility almost certainly is not.", ""]
        L += md_table(["id", "address", "property type", "site EUI", "gross floor area, sq ft"],
                      [[r.id, r.address, r.primary_property_type or "", fmt(r.site_eui_kbtu_sq_ft),
                        n(r.gross_floor_area_buildings_sq_ft)] for r in out.itertuples()])
        L += [""]
        fa = summary["years"][str(y)]["floor_area"]
        g = fa["ghg"]
        big = sub[sub["ratio_to_eui_x_gfa"] <= 0.70].sort_values(["ratio_to_eui_x_gfa", "id"])
        L += [f"## 8. The floor-area check, data year {y}", "",
              f"Total site energy is the fuel sum for {fa['total_is_fuel_sum']:,} of {fa['submitted_with_total']:,} "
              f"submitted records with a total ({fa['total_is_eui_x_gfa']:,} fall back to EUI x GFA): "
              f"**{fa['sum_total_kbtu']:,} kBtu**. EUI x published floor area would give {fa['sum_eui_x_gfa_kbtu']:,}, "
              f"{fa['sum_eui_x_gfa_kbtu'] / fa['sum_total_kbtu'] - 1:.1%} more. For {fa['overstated_3pct_or_more']:,} records "
              f"EUI x GFA runs 3% or more above the fuel total. The GHG columns say which side is wrong: over the "
              f"{g['overstated_with_ghg']:,} of those that carry GHG, total GHG / (GHG intensity x GFA) has median "
              f"{g['overstated_median_ghg_ratio']} against a fuel-total ratio of {g['overstated_median_fuel_ratio']}, "
              f"r = {g['overstated_r']}, {g['overstated_within_3_points']:,} within 3 points of each other; over the "
              f"{g['consistent_with_ghg']:,} consistent records it is {g['consistent_median_ghg_ratio']}. An incomplete "
              "fuel column would leave the GHG ratio at 1. It is the floor area.", "",
              f"Records with a ratio of 0.70 or less ({len(big):,}):", ""]
        L += md_table(["id", "address", "total (fuel sum), kBtu", "EUI x GFA, kBtu", "ratio"],
                      [[r.id, r.address, n(r.site_energy_kbtu), n(r.eui_x_gfa_kbtu), f"{r.ratio_to_eui_x_gfa:.3f}"]
                       for r in big.itertuples()])
        L += [""]

    L += ["## 9. The floor-area check, by year", "",
          "Submitted records whose total is a fuel sum; the ratio is fuel total / (EUI x GFA). An excluded year "
          "appears here to show what it would change.", ""]
    bins = ["within_3pct", "ratio_0.90_to_0.97", "ratio_0.70_to_0.90", "ratio_0.70_or_below", "ratio_1.03_or_above"]
    L += md_table(["data year", "records", *[b_.replace("_", " ") for b_ in bins], "GHG ratio, overstated", "r", ""],
                  [[y, f"{s['floor_area']['checkable']:,}", *[f"{s['floor_area'][k]:,}" for k in bins],
                    s["floor_area"]["ghg"]["overstated_median_ghg_ratio"] or "", s["floor_area"]["ghg"]["overstated_r"] or "",
                    "EXCLUDED" if s["excluded"] else ""]
                   for y, s in summary["years"].items() if s["floor_area"]["checkable"]])
    L += [""]

    L += ["## 10. The excluded-year location test", "",
          "METHODS.md \"Adding a new data year\" step 4, run the way this pipeline locates: an id already in "
          "`buildings.csv` keeps its location, any other id is matched on its own address with no coordinate. "
          "Address alone is the literal reading of step 4(a).", ""]
    ys = schema.DISPLAY_YEARS[0]
    ref = match["by_year"][str(ys)]["submitted"]
    L += md_table(["data year", "submitted", "located by id", "matched on own address", "high or medium, id then address",
                   "address alone"],
                  [[y, f"{t['submitted']:,}", f"{t['located_by_id']:,}", f"{t['matched_on_own_address']:,}",
                    f"{t['high_or_medium']:,} ({t['high_or_medium_share']:.1%})", pct(t["address_alone_share"])]
                   for y, t in match["excluded_year_location_test"].items()]
                  + [[f"{ys} (display)", f"{ref['properties']:,}", "", "", f"{ref['high_or_medium']:,} ({ref['high_or_medium_share']:.1%})", ""]])
    L += [""]

    L += ["## 11. The community-area test, by year", "",
          "A row passes when its own coordinate lies inside its own stated community area. It decides which "
          "years' coordinates may be trusted. It cannot catch a coordinate and a stated area that are wrong "
          "together: McCormick Place's 2014-2021 rows do exactly that (section 15).", ""]
    # Rates are formatted from the counts, not from the stored pass_rate, which is rounded to 4 places
    # (2016's 2,463 of 2,717 is 90.65%: stored 0.9065, it printed 90.6%).
    def share(c, k):
        return pct(c[k] / c["testable"] if c["testable"] else None)
    L += md_table(["data year", "rows", "with a coordinate", "no community area to test against", "testable",
                   "inside", "pass rate", "inside with 100 m buffer", ""],
                  [[y, f"{s['rows']:,}", f"{s['coordinates']['with_coordinate']:,}",
                    f"{s['coordinates']['no_community_area_to_test']:,}", f"{s['coordinates']['testable']:,}",
                    f"{s['coordinates']['inside_own_community_area']:,}", share(s["coordinates"], "inside_own_community_area"),
                    f"{s['coordinates']['inside_with_100m_buffer']:,} ({share(s['coordinates'], 'inside_with_100m_buffer')})",
                    "EXCLUDED" if s["excluded"] else ""]
                   for y, s in summary["years"].items()])
    c = summary["covered_list"]
    L += ["", f"Covered-buildings list: {c['inside_own_community_area']:,} of {c['testable']:,} inside "
          f"({share(c, 'inside_own_community_area')}); {c['inside_with_100m_buffer']:,} with the 100 m buffer "
          f"({share(c, 'inside_with_100m_buffer')}).", ""]
    L += ["## 12. Coordinate candidates that failed the test", "",
          "Candidates the trusted-coordinate rule looked at and refused, for ids in the admitted years.", ""]
    rj = rejected.groupby(["source", "reason"]).size().reset_index(name="n")
    L += md_table(["source", "reason", "candidates"], [[r.source, r.reason, f"{r.n:,}"] for r in rj.itertuples()])
    placeholder = rejected.groupby(["latitude", "longitude"]).size().sort_values(ascending=False)
    if len(placeholder) and placeholder.iloc[0] > 5:
        (la, lo), k = placeholder.index[0], int(placeholder.iloc[0])
        L += ["", f"{k:,} of the refused candidates are the single point ({la}, {lo}): a placeholder the "
              "sources use where they have no location. The test refuses every one of them."]
    L += [""]

    L += ["## 13. Reporting-status labels seen, by year", ""]
    labels = sorted({k for s in summary["years"].values() for k in s["status_labels"]})
    L += md_table(["data year", *labels], [[y, *[f"{s['status_labels'].get(k, 0):,}" if s["status_labels"].get(k) else ""
                                                for k in labels]] for y, s in summary["years"].items()])
    L += ["", "Mapping: " + "; ".join(f"`{k}` -> {v}" for k, v in schema.STATUS_MAP.items()) + ".", ""]

    L += ["## 14. Footprint table", "",
          f"{fp['rows_in_csv']:,} rows; status " + ", ".join(f"{k or '(blank)'} {v:,}" for k, v in fp["bldg_statu"].items())
          + f". {fp['active_kept']:,} ACTIVE kept; newest `year_built` {fp['max_year_built']}. "
          f"`shape_area` against area recomputed from the geometry: {fp['shape_area_vs_geometry']['differ_over_1pct']:,} "
          f"of {fp['shape_area_vs_geometry']['compared']:,} differ by more than 1%. "
          f"`bldg_id` shared by two footprints, and so matchable to neither: "
          f"{', '.join(str(x) for x in match['bldg_ids_shared_by_two_footprints']) or 'none'}.", ""]

    L += [f"## 15. Overrides applied: {len(overrides):,}", "",
          "`data/overrides/footprint_overrides.csv`. Each row was resolved one id at a time against the "
          "evidence its `note` and `source` name, by the same AI desk review (Claude, written rubric) that "
          "produced the match precision below; no person has re-checked them and no imagery was used. "
          "`coordinate` names a City-published coordinate accepted on that review; empty footprints and no "
          "coordinate is a veto.", ""]
    L += md_table(["id", "footprint_ids", "coordinate", "note", "source", "verified"],
                  [[i, fmt(o["footprint_ids"]), o["coordinate"] or "", o["note"], o["source"], o["verified_on"]]
                   for i, o in sorted(overrides.items())])
    L += [""]

    L += ["## 16. Match precision", ""]
    if prec is None:
        L += ["No review sheet (`match_review.csv`): no precision can be stated.", ""]
    else:
        # Every percent here is formatted from the unrounded value: the stored figures are rounded to 4
        # places, and formatting those rounds twice (74 of 89 is 83.146%: stored 0.8315, it printed 83.2%).
        u = prec["unrounded"]

        def tier_row(m, t):
            ok, decided = t["correct"] + t["partial"], t["correct"] + t["partial"] + t["wrong"]
            lo, hi = wilson(ok, decided, digits=None)
            return [m, f"{t['population']:,}", t["reviewed"], t["correct"], t["partial"], t["wrong"], t["unsure"],
                    pct(ok / decided if decided else None), "" if lo is None else f"{lo:.1%}-{hi:.1%}",
                    pct(ok / t["reviewed"] if t["reviewed"] else None)]
        L += [f"{prec['reviewed']:,} matches reviewed; {prec['stale']:,} stale (the build has since changed them; not "
              f"counted - redraw and recheck them). Weighted precision over the {prec['population']:,} tier matches: "
              f"{u['weighted_precision']:.1%}; over the {prec['drawn_outlines']['matches']:,} drawn as outlines: "
              f"{u['drawn_outlines']:.1%}. By confidence: "
              + "; ".join(f"{c} {u['by_confidence'][c]:.1%} of {t['matches']:,}" for c, t in prec["by_confidence"].items()
                          if u["by_confidence"][c] is not None) + ".", ""]
        L += md_table(["tier", "matches", "checked", "correct", "partial", "wrong", "unsure", "precision", "95% CI", "floor"],
                      [tier_row(m, t) for m, t in prec["by_method"].items()])
        L += [""]

    L += ["## 17. Where this build differs from the first baseline", "",
          "- The first synonym table mapped `LA SALLE` to `LASALLE`. The footprint table spells it `LA SALLE`, "
          "so no LaSalle Street address could match in the first baseline run, which predates the fix.", ""]
    return "\n".join(L)


# --- dictionary ------------------------------------------------------------------------------------------------
def dictionary(summary: dict, manifest: dict, spellings: int, small: dict, prec: dict | None) -> str:
    src = manifest["meta"]["sources"]
    rel = src["benchmarking"]["rowsUpdatedAt_iso"][:10]
    vint = f"City release of {rel}, snapshot {summary['snapshot']}"
    B, F = "Chicago Energy Benchmarking (xq83-jr8c)", "Building Footprints (syp8-uezg), a 2015 snapshot"
    L = ["# Data dictionary - chicago-building-energy", "",
         f"Script output (`scripts/build.py`); never hand-edited. Data release {schema.RELEASE_DATE}. "
         f"Vintage of every figure: {vint}. "
         f"Covered-buildings list (g5i5-yz37) rows updated {src['covered']['rowsUpdatedAt_iso'][:10]}; community areas "
         f"(igwz-8jzy) {src['community_areas']['rowsUpdatedAt_iso'][:10]}; building footprints (syp8-uezg) "
         f"{src['footprints']['rowsUpdatedAt_iso'][:10]}.", "",
         f"**Years.** Published: {schema.DISPLAY_YEARS}. Excluded: "
         + "; ".join(f"{y} - {why}" for y, why in schema.EXCLUDED_YEARS.items())
         + ". No row from an excluded year is in any file here. An empty cell means the City published "
           "nothing; no value is interpolated, estimated or geocoded.", ""]
    for y in schema.DISPLAY_YEARS:
        fa = summary["years"][str(y)]["floor_area"]
        g = fa["ghg"]
        L += [f"**Total site energy, {y}.** The sum of the five fuel columns, for each of the {fa['submitted_with_total']:,} "
              f"submitted records with a published site EUI; EUI x gross floor area is used only where the fuel columns are "
              f"empty ({fa['total_is_eui_x_gfa']:,} records). For {fa['overstated_3pct_or_more']:,} records EUI x floor area "
              f"runs 3% or more above the fuel total (median ratio {g['overstated_median_fuel_ratio']}), because the published "
              f"floor area is inflated: the GHG columns fall short of GHG intensity x floor area by the same factor "
              f"(median {g['overstated_median_ghg_ratio']}, r = {g['overstated_r']}) and agree exactly elsewhere "
              f"({g['consistent_median_ghg_ratio']}). `gfa_consistent` marks which records are affected; their site EUI "
              "is as the City computed it and is not affected.", ""]
    L += [f"**Community area.** `community_area` is the text on the City's row, as typed: {spellings} spellings for 77 "
          "areas across these files, and empty for some rows. Grouping on it splits areas silently. "
          "`community_area_num` is the area the property is located in, by geometry, the same assignment the "
          "density tables use; group on that.", "",
          f"**Small counts.** {small['hex_one_property']:,} of the {small['hex_with_value']:,} hexagons with a value hold a "
          f"single reporting property, and {small['ca_under_five']:,} community areas hold fewer than {small['threshold']}. "
          "There one building sets the figure; read `n_submitted` beside `kbtu_per_sqmi`.", ""]

    if prec:
        L += [f"**Match precision.** A seeded sample of up to 100 footprint matches per tier ({prec['reviewed']:,} in all) was "
              "checked one by one against evidence the tier did not use: the footprint's own address range, name, stories "
              "and size, its neighbors and their addresses, and the City coordinate. The checks were made by AI reviewers "
              "(Claude) working to a written rubric, with a sample re-checked; no person or imagery was involved. Every "
              "verdict and its reason is in `match_review.csv`. Precision is correct (including one building of a campus) "
              "over correct plus wrong; `unsure` is left out, and the floor counts it as wrong. Weighted by how many "
              f"matches each tier made, about {prec['weighted_precision']:.0%} of footprints attached by the tiers are the "
              f"right building, and about {prec['drawn_outlines']['estimated_precision']:.0%} of the outlines the map draws "
              "(low-confidence matches are placed at their coordinate instead).", ""]
        L += md_table(["tier", "matches", "checked", "correct", "partial", "wrong", "unsure", "precision (95% CI)", "floor"],
                      [[m, f"{t['population']:,}", t["reviewed"], t["correct"], t["partial"], t["wrong"], t["unsure"],
                        "" if t["precision"] is None else f"{t['precision']:.0%} ({t['ci95'][0]:.0%}-{t['ci95'][1]:.0%})",
                        "" if t["floor"] is None else f"{t['floor']:.0%}"]
                       for m, t in prec["by_method"].items()])
        L += ["", "By `match_confidence`, weighting each tier's checked matches by the matches it holds at that level:", ""]
        L += md_table(["confidence", "matches", "estimated precision"],
                      [[c, f"{t['matches']:,}", "" if t["estimated_precision"] is None else f"{t['estimated_precision']:.0%}"]
                       for c, t in prec["by_confidence"].items()])
        L += [""]

    def table(title, note, rows):
        out = [f"## {title}", "", note, ""] + md_table(["column", "unit / values", "source", "definition"], rows)
        return out + [""]

    L += table("buildings.csv", "One row per benchmarking `id` seen in any published-or-admitted year: where the "
               "property is, and which City footprints are attached to it.", [
        ["id", "integer", f"{B} `id`", "Property id; stable across years. Key."],
        ["address_raw", "text", f"{B} `address`", "As typed by the owner, from the display-year row, else the newest admitted year."],
        ["address_norm", "text", "derived", "Parsed form used for matching: number or range, direction, street, type. Empty when the address does not parse."],
        ["community_area", "text", f"{B} `community_area`", "As stated on the same row; may be empty or misspelled. Use `community_area_num`."],
        ["primary_property_type_latest", "text", f"{B} `primary_property_type`", "From the newest admitted year."],
        ["year_built", "year", f"{B} `year_built`", "As reported by the owner."],
        ["of_buildings_latest", "count", f"{B} `of_buildings`", "Buildings the property reports, newest admitted year."],
        ["trusted_lat, trusted_lon", "degrees, WGS84", f"{B} or covered list (g5i5-yz37) `latitude`/`longitude`",
         "A City-published coordinate, accepted only if inside its own row's stated community area (100 m buffer), or accepted on review (`coord_source` override; see `footprint_overrides.csv`). Empty when none."],
        ["coord_source", "row_<year> / covered_list / override / none", "derived",
         "Which source's coordinate is used. `override`: a City coordinate that could not pass the test and was accepted on review; `footprint_overrides.csv` names which. Never an excluded year."],
        ["footprint_ids", "`bldg_id`s joined by ';'", f"{F} `bldg_id`", "Footprints attached to the property. Empty when none."],
        ["n_footprints", "count", "derived", "Length of `footprint_ids`. For a campus it is often fewer than `of_buildings_latest`: only footprints tied to the address or the coordinate are attached."],
        ["footprint_year_built", "year", f"{F} `year_built`", "Latest non-zero construction year among attached footprints."],
        ["footprint_area_sqft", "sq ft", f"{F} geometry", "Summed plan area of attached footprints, computed in EPSG:3435."],
        ["implied_floors", "floors", "derived",
         "Gross floor area / `footprint_area_sqft`; for a campus with fewer footprints than buildings, the floor area is first scaled by footprints / buildings. For a rejected match, the rejected footprints' figure."],
        ["size_check", "pass / doubtful / fail / empty", "derived",
         f"fail: over {schema.SIZE_FAIL_FLOORS} implied floors, footprint rejected. doubtful: over {schema.SIZE_DOUBT_FLOORS}, or over {schema.SIZE_STORY_FACTOR} times the stories the City's layer gives the attached footprints, confidence lowered one step; not applied where the layer gives {schema.SIZE_TOWER_STORIES}+ stories. Empty: no footprint found or no floor area."],
        ["match_method", " / ".join(schema.MATCH_METHODS), "derived", "The tier that attached the footprints, in the order `scripts/match_footprints.py` tries them; the README's match-precision table defines each one."],
        ["match_confidence", " / ".join(schema.MATCH_CONFIDENCES), "derived",
         "high: address agrees, or set by an override. medium: coordinate-led or coordinate-confirmed. low: unconfirmed. none: no footprint. After the size check."],
        ["match_note", "text", "derived", "What was tried and refused on the way."],
        ["located_by", "footprint / trusted_coordinate / none", "derived",
         "Where the property is placed on the map and in the density tables: its largest footprint, or the trusted coordinate when it has no footprint or only a low-confidence one. A low-confidence match with no trusted coordinate keeps its footprint, there being nothing else to place it by: 1 of the 116 low matches in 2022."],
        ["community_area_num, community_area_name", "1-77, text", "igwz-8jzy `area_numbe`, `community`",
         "The community area the property is located in, by geometry. Empty when not located."]])
    L += table("energy.csv", f"One row per (`id`, `data_year`) for data years {schema.DISPLAY_YEARS}. Reported columns are "
               "as published. The release's own `latitude`/`longitude` are deliberately not carried: use "
               "`buildings.csv`.", [
        ["id, data_year", "integer", f"{B}", "Key."],
        ["property_name", "text", f"{B} `property_name`", "As the City publishes it."],
        ["address, zip_code, community_area, primary_property_type", "text", f"{B}", "As published. `community_area` is free text; see the note above."],
        ["community_area_num", "1-77", "derived", "As in `buildings.csv`: where the property is located, by geometry. Group on this."],
        ["reporting_status", "text", f"{B} `reporting_status`", "The City's label, which changes by year."],
        ["status", " / ".join(schema.STATUSES), "derived", "`reporting_status` under the explicit mapping in `scripts/schema.py`."],
        ["exempt_from_chicago_energy_rating", "text", f"{B}", "As published."],
        ["gross_floor_area_buildings_sq_ft", "sq ft", f"{B}", "As published. Inflated where `gfa_consistent` is false."],
        ["year_built, of_buildings", "year, count", f"{B}", "As published."],
        ["water_use_kgal", "thousand gallons", f"{B}", "As published."],
        ["energy_star_score", "1-100", f"{B}", "As published."],
        [", ".join(schema.FUEL_COLUMNS), "kBtu per year", f"{B}", "As published. Together they are total site energy."],
        ["site_eui_kbtu_sq_ft, source_eui_kbtu_sq_ft", "kBtu per sq ft per year", f"{B}", "As published."],
        ["weather_normalized_site_eui_kbtu_sq_ft, weather_normalized_source_eui_kbtu_sq_ft", "kBtu per sq ft per year", f"{B}", "As published."],
        ["total_ghg_emissions_metric_tons_co2e", "metric tons CO2e", f"{B}", "As published."],
        ["ghg_intensity_kg_co2e_sq_ft", "kg CO2e per sq ft", f"{B}", "As published."],
        ["chicago_energy_rating", "0-4 stars", f"{B}", "As published."],
        ["site_energy_kbtu", "kBtu per year", "derived",
         "Total site energy: the sum of the five fuel columns, rounded to the kBtu, where a site EUI is published; EUI x floor area only if the fuel columns are empty or zero. Empty without an EUI."],
        ["site_energy_basis", "fuel_sum / eui_x_gfa / empty", "derived", "Which definition gave `site_energy_kbtu`."],
        ["fuel_sum_kbtu", "kBtu per year", "derived", "Sum of the five fuel columns, empties as zero; empty when all five are. Also present for some records with no EUI, where it is not used."],
        ["eui_x_gfa_kbtu", "kBtu per year", "derived", "`site_eui_kbtu_sq_ft` x `gross_floor_area_buildings_sq_ft`, exact, rounded to the kBtu. A check on the floor area, not a total."],
        ["ratio_to_eui_x_gfa", "ratio", "derived", "`site_energy_kbtu` / `eui_x_gfa_kbtu` where the total is a fuel sum. 1 where the floor area agrees; 0.835 means the published floor area is about 1.2 times what the EUI and fuel figures imply."],
        ["gfa_consistent", "true / false / empty", "derived", f"True where `ratio_to_eui_x_gfa` is within {schema.GFA_TOLERANCE:.0%} of 1."]])
    dens = [
        ["data_year", "year", "derived", f"The display year the row aggregates; {schema.DISPLAY_YEARS} here."],
        ["n_properties", "count", "derived", "Benchmarked properties of any status located in the cell."],
        ["n_submitted, n_not_submitted", "count", "derived", "By `status`."],
        ["site_energy_kbtu", "kBtu per year", "derived", "Sum of `site_energy_kbtu` over submitted properties that have one. Empty when the cell has none - not zero."],
        ["ghg_tco2e", "metric tons CO2e", "derived", "Sum of `total_ghg_emissions_metric_tons_co2e` over the same properties."],
        ["area_sqmi", "square miles", "derived", "Area of the cell's polygon. Not land area: it includes parks, rail yards and rivers, and a lakefront hexagon includes water."],
        ["kbtu_per_sqmi", "kBtu per year per sq mi", "derived", "`site_energy_kbtu` / `area_sqmi`. Read with `n_submitted`: many cells hold one property."]]
    L += table("density_hex.csv", f"Hexagons {schema.HEX_SIZE_M:.0f} m flat to flat ({HEX_SQMI:.6f} sq mi, constant, not "
               "clipped to the shoreline), pointy-top, anchored at the EPSG:3435 origin. Only hexagons holding at "
               "least one property are listed. A property is placed as `located_by` says; with no location it is "
               "left out of both density tables, and `buildings.csv` marks it `located_by = none`.",
               [["hex_id", "r<row>c<col>", "derived", "Grid cell; stable across builds."]] + dens)
    L += table("density_ca.csv", "The 77 community areas (igwz-8jzy), assigned by geometry, never by the row's stated name.",
               [["community_area_num, name", "1-77, text", "igwz-8jzy `area_numbe`, `community`",
          "The community area and its name, as the City's boundary layer publishes them. Key with `data_year`."],
          ["display_name", "text", "derived", "`name` as the pothole release spells it: title case, with O'Hare and McKinley Park."]] + dens
               + [["share_not_submitted", "fraction", "derived", "`n_not_submitted` / (`n_submitted` + `n_not_submitted`). Exempt properties are outside the ratio."]])
    L += table("footprint_overrides.csv",
               "The answers the matcher applies last, after every tier. Each was resolved one id at a time against the "
               "evidence its `note` and `source` name, by the same AI desk review (Claude, working to a written rubric) "
               "that produced the match precision above; no person has re-checked them, and no imagery or site visit was "
               "used. No footprints and no coordinate means the property is deliberately not placed.", [
        ["id", "integer", f"{B} `id`", "The property the row resolves. Key."],
        ["footprint_ids", "`bldg_id`s joined by ';'", f"{F} `bldg_id`", "The footprints to attach. Empty for none."],
        ["coordinate", "row_<year> / covered_list / empty", "derived",
         "Which City-published coordinate to trust for this property, accepted on the review although it could not pass "
         "the community-area test. Empty for none. Never an excluded year."],
        ["note", "text", "derived", "The reasoning, naming the evidence it rests on."],
        ["source", "text", "derived", "The dataset columns and rows the note was read from."],
        ["verified_on", "date, YYYY-MM-DD", "derived", "When the row was reviewed."]])
    L += table("match_review.csv",
               "The precision check above, one row per sampled match, as reviewed. A row the build has since changed is "
               "stale and is not counted in the precision; the tables above say how many.", [
        ["id", "integer", f"{B} `id`", "The property whose match was checked. Key with `footprint_ids`."],
        ["match_method", " / ".join(schema.MATCH_METHODS), "derived", "The tier that attached the footprints, as reviewed."],
        ["match_confidence", " / ".join(schema.MATCH_CONFIDENCES), "derived", "Its confidence, as reviewed."],
        ["footprint_ids", "`bldg_id`s joined by ';'", f"{F} `bldg_id`", "The footprints the reviewer judged."],
        ["verdict", "correct / partial / wrong / unsure", "derived",
         "correct: the attached footprints are the property's building or buildings. partial: one building of a campus, "
         "location right. wrong: a different building. unsure: the evidence did not decide it."],
        ["reason", "text", "derived", "Why, naming the evidence on the card."],
        ["reviewed_on", "date, YYYY-MM-DD", "derived", "When the verdict was recorded."]])
    L += ["## facts.json", "",
          "Every figure the project page states, one entry per figure: `value` (the exact figure), `display` (the "
          "string the page prints, thousands separators and unit included), `label`, a one-sentence `definition`, "
          "`source_file`, `sources`, `rounding` (set wherever `display` is not the plain rendering of `value`) and "
          "`unit`. Computed in the same build as the tables above, from them; the map manifest's `counts` are checked "
          "against it at export. A percent is on a 0-100 scale; the hexagon width is in feet and in miles; no entry "
          "carries a metric unit. The top level carries the release date and the source pulls.", "",
          "## classes.json", "",
          f"{schema.N_CLASSES} quantile classes per metric and display year, computed once here; the map reads them and "
          "never computes its own. `site_eui` and `site_energy_kbtu` are classed over submitted properties with a "
          "published EUI; `kbtu_per_sqmi` separately for hexagons and community areas. `breaks` holds the six "
          "interior boundaries, rounded to three significant figures.", "",
          "## checksums.sha256", "", "SHA-256 of every other file in this folder, bare filenames, over the exact bytes.", ""]
    return "\n".join(L)


# --- main ----------------------------------------------------------------------------------------------------------
def build_processed() -> dict:
    energy_all = pd.read_parquet(INTERIM / "energy_long.parquet")
    buildings = pd.read_parquet(INTERIM / "buildings.parquet")
    footprints = gpd.read_parquet(INTERIM / "footprints.parquet")
    cas = gpd.read_parquet(INTERIM / "community_areas.parquet")
    rejected = pd.read_parquet(INTERIM / "coord_rejections.parquet")
    summary = json.loads((INTERIM / "normalize_summary.json").read_text())
    match = json.loads((INTERIM / "match_summary.json").read_text())
    manifest = json.loads((ROOT / "data" / "raw" / summary["snapshot"] / "MANIFEST.json").read_text())

    # The excluded years stop here. Nothing below this line can see them.
    energy = energy_all[~energy_all["data_year"].isin(schema.EXCLUDED_YEARS)].copy()
    assert not set(buildings["coord_source"]) & {f"row_{y}" for y in schema.EXCLUDED_YEARS}

    import match_footprints
    overrides = match_footprints.load_overrides()

    located = locate(property_points(buildings, footprints), cas)
    located.to_parquet(INTERIM / "property_points.parquet", index=False)
    names = dict(zip(cas["area_numbe"].astype(int), cas["community"]))
    buildings = buildings.merge(located[["id", "located_by", "community_area_num"]], on="id", how="left")
    buildings["community_area_num"] = buildings["community_area_num"].astype("Int64")
    buildings["community_area_name"] = buildings["community_area_num"].map(lambda k: None if pd.isna(k) else names[int(k)])
    energy = energy.merge(located[["id", "community_area_num"]], on="id", how="left")
    energy["community_area_num"] = energy["community_area_num"].astype("Int64")

    PROCESSED.mkdir(parents=True, exist_ok=True)
    for old in PROCESSED.glob("*"):
        old.unlink()
    write_csv(PROCESSED / "buildings.csv", BUILDINGS_COLUMNS, buildings.sort_values("id"))
    display = energy[energy["data_year"].isin(schema.DISPLAY_YEARS)].sort_values(["data_year", "id"])
    write_csv(PROCESSED / "energy.csv", ENERGY_COLUMNS, display)

    hex_frames, ca_frames, year_density = [], [], {}
    for y in schema.DISPLAY_YEARS:
        h, a, d = density(display[display["data_year"] == y], located, cas, y)
        hex_frames.append(h)
        ca_frames.append(a)
        inc = d[d["included"]]
        valued = h[h["site_energy_kbtu"].notna()]
        year_density[y] = (d, {"included": int(len(inc)), "site_energy_kbtu": int(inc["site_energy_kbtu"].sum()),
                               "ca_by_nearest": int(d["ca_by_nearest"].sum()),
                               "hex_with_value": int(len(valued)),
                               "hex_one_property": int((valued["n_submitted"] == 1).sum()),
                               "hex_two_or_fewer": int((valued["n_submitted"] <= 2).sum()),
                               "ca_under_five": int((a["n_submitted"] < 5).sum()), "threshold": 5})
    hexes, areas = pd.concat(hex_frames), pd.concat(ca_frames)
    write_csv(PROCESSED / "density_hex.csv", HEX_COLUMNS, hexes)
    write_csv(PROCESSED / "density_ca.csv", CA_COLUMNS, areas)
    cls = classes(display, hexes, areas)
    write_json(PROCESSED / "classes.json", cls)
    spellings = len(set(display["community_area"].dropna()) | set(buildings["community_area"].dropna()))
    prec = match_precision(buildings, display)
    write_json(INTERIM / "precision.json", prec)
    # Every figure the project page states, from the tables just written (facts.py). Written
    # here so the determinism test, a rebuild checked with git diff, and checksums.sha256 all cover it.
    import facts
    sizes = {n: (PROCESSED / n).stat().st_size for n in ("energy.csv", "buildings.csv")}
    write_json(PROCESSED / "facts.json", facts.build(display, buildings, hexes, areas, cls, prec, overrides, manifest, sizes))
    (PROCESSED / "dictionary.md").write_text(
        dictionary(summary, manifest, spellings, year_density[schema.DISPLAY_YEARS[-1]][1], prec), encoding="utf-8")
    RECONCILIATION.write_text(
        reconciliation(energy, buildings, located, summary, match, rejected, year_density, overrides, prec), encoding="utf-8")
    # The audit trail travels with the release, byte for byte as maintained.
    (PROCESSED / "footprint_overrides.csv").write_bytes(OVERRIDES.read_bytes())
    if REVIEW.exists():
        (PROCESSED / "match_review.csv").write_bytes(REVIEW.read_bytes())
    n_files = write_checksums(PROCESSED)
    return {"files": n_files, "density": {y: s for y, (_, s) in year_density.items()}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--processed-only", action="store_true", help="reuse data/interim/ as it stands")
    args = ap.parse_args()
    if not args.processed_only:
        import match_footprints
        import normalize
        sys.argv = [sys.argv[0]]
        print("=== stage 1  normalize ===", flush=True)
        normalize.main()
        print("\n=== stage 2  match ===", flush=True)
        match_footprints.main()
    print("\n=== stage 3  build ===", flush=True)
    rep = build_processed()
    for y, s in rep["density"].items():
        print(f"density {y}: {s['included']:,} properties included, {s['site_energy_kbtu']:,} kBtu; "
              f"{s['hex_one_property']:,} of {s['hex_with_value']:,} valued hexagons hold one property")
    print(f"{rep['files']} files in data/processed/ + checksums.sha256; data/reconciliation.md regenerated")
    for p in sorted(PROCESSED.glob("*")):
        print(f"  {hashlib.sha256(p.read_bytes()).hexdigest()[:16]}  {p.stat().st_size:>10,}  {p.name}")


if __name__ == "__main__":
    main()
