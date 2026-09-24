#!/usr/bin/env python3
"""facts.json: every figure the project page states, computed in stage 3 from the release.

Written by build.build_processed() beside the tables it describes, so a rebuild covers it
(`make check`, and the determinism test), checksums.sha256 lists it, and it is published with
the rest. The map manifest's `counts` (export_site.py) are checked against it at export, so the
figures the map's own caveats quote and the figures the website annotates cannot disagree.

Each fact carries the exact `value`, the `display` string the page prints (thousands
separators and unit included), a `label` for a stat tile, a one-sentence `definition`, the
released `source_file` it is computed from, its `sources`, the `rounding` applied wherever
`display` is not the plain rendering of `value`, and a `unit`. Percents are on a 0-100 scale;
the hexagon width is in feet and in miles. No fact carries a metric unit, and no definition names
a metric parameter or an internal name: definitions feed the website's JSON-LD and dataset pages.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

import pandas as pd

import schema

SOCRATA = "https://data.cityofchicago.org/d/"
B, C, CA, FP = "benchmarking", "covered", "community_areas", "footprints"


def _dec(v) -> Decimal:
    return Decimal(str(v))


def count(v) -> str:
    return f"{int(v):,}"


def fixed(v, places: int) -> str:
    q = Decimal(1).scaleb(-places) if places else Decimal(1)
    return str(_dec(v).quantize(q, rounding=ROUND_HALF_UP))


def pct_of(numerator, denominator) -> float:
    """A percentage on the 0-100 scale, to four decimals."""
    return float(fixed(100 * _dec(numerator) / _dec(denominator), 4))


def fact(F: dict, key: str, value, display: str, label: str, definition: str, source_file: str,
         sources: list[str], rounding: str | None = None, unit: str | None = None) -> None:
    if key in F:
        raise SystemExit(f"facts.json: duplicate key {key!r}")
    f = {"value": value, "display": display, "label": label, "definition": definition,
         "source_file": source_file, "sources": sources, "rounding": rounding}
    if unit:
        f["unit"] = unit
    F[key] = f


def build(energy: pd.DataFrame, buildings: pd.DataFrame, hexes: pd.DataFrame, areas: pd.DataFrame,
          classes: dict, prec: dict | None, overrides: dict, raw_manifest: dict, sizes: dict) -> dict:
    y = schema.DISPLAY_YEARS[-1]
    e = energy[energy["data_year"] == y].merge(
        buildings[["id", "located_by", "match_confidence", "n_footprints"]], on="id", how="left")
    sub = e[e["status"] == "submitted"]
    with_eui = sub[sub["site_eui_kbtu_sq_ft"].notna()]
    F: dict = {}

    # ---- counts ---------------------------------------------------------------------------------
    fact(F, "data_year", y, str(y), "Data year", "The benchmarking data year every figure describes.", "energy.csv", [B])
    fact(F, "covered", len(e), count(len(e)), "Covered properties",
         f"Properties the ordinance covered for {y}: rows of energy.csv.", "energy.csv", [B], unit="properties")
    for key, status, label in (("reported", "submitted", "Reported"), ("not_reported", "not_submitted", "Did not report"),
                               ("exempt", "exempt", "Exempt"), ("not_covered", "not_covered", "Not covered")):
        n = int((e["status"] == status).sum())
        fact(F, key, n, count(n), label, f"Properties whose status is {status}.", "energy.csv", [B], unit="properties")
    for key, how, label, extra in (
            ("drawn_as_footprints", "footprint", "Drawn as footprints", "its largest attached City footprint"),
            ("drawn_as_markers", "trusted_coordinate", "Drawn as markers",
             "a City coordinate that passed the community-area test or was accepted on review, having no confirmed footprint"),
            ("not_mapped", "none", "Not mapped", "neither a footprint nor a coordinate that passed the test")):
        n = int((e["located_by"] == how).sum())
        fact(F, key, n, count(n), label, f"Covered properties whose located_by is {how}: placed by {extra}.",
             "buildings.csv", [B, FP], unit="properties")
    n = int(sub["site_eui_kbtu_sq_ft"].isna().sum())
    fact(F, "reported_no_eui", n, count(n), "Reported with no EUI published",
         "Submitted rows with an empty site_eui_kbtu_sq_ft; no total is formed for them.", "energy.csv", [B], unit="properties")
    fact(F, "reported_with_eui", len(with_eui), count(len(with_eui)), "Reported with a site EUI",
         "Submitted rows with a site EUI, the population every total and class break is computed over.", "energy.csv", [B],
         unit="properties")
    n = int((with_eui["gfa_consistent"] == False).sum())  # noqa: E712  (pandas nullable boolean)
    fact(F, "gfa_inflated", n, count(n), "Reported properties with inflated floor area",
         f"Reported rows with an EUI whose gfa_consistent is false: EUI times floor area runs {schema.GFA_TOLERANCE:.0%} or more "
         "above the fuel total.", "energy.csv", [B], unit="properties")
    n = int((sub["of_buildings"].fillna(0) > 1).sum())
    fact(F, "campuses_reporting", n, count(n), "Reporting properties with more than one building",
         "Submitted rows whose of_buildings exceeds 1.", "energy.csv", [B], unit="properties")
    n = int((sub["site_eui_kbtu_sq_ft"] > 1000).sum())
    fact(F, "eui_over_1000", n, count(n), "Reported properties above 1,000 kBtu per square foot",
         "Submitted rows with site_eui_kbtu_sq_ft above 1,000, kept as published.", "energy.csv", [B], unit="properties")

    # ---- energy ----------------------------------------------------------------------------------
    total = int(sub["site_energy_kbtu"].sum())
    fact(F, "total_site_energy", total, f"{fixed(_dec(total) / 1_000_000_000, 1)} billion kBtu", "Total reported site energy",
         "site_energy_kbtu summed over reported properties: each property's reported fuel use, added up.", "energy.csv", [B],
         rounding="nearest 0.1 billion", unit="kBtu")
    with_total = sub[sub["site_energy_kbtu"].notna() & sub["eui_x_gfa_kbtu"].notna()]
    over = float(fixed(100 * (_dec(int(with_total["eui_x_gfa_kbtu"].sum())) / _dec(int(with_total["site_energy_kbtu"].sum())) - 1), 4))
    fact(F, "eui_x_gfa_overstatement_pct", over, f"{fixed(over, 0)}%", "Overstatement by EUI times floor area",
         "Summed eui_x_gfa_kbtu over summed site_energy_kbtu, minus one, over reported properties with a total.",
         "energy.csv", [B], rounding="nearest percent", unit="percent")
    med = float(with_eui["site_eui_kbtu_sq_ft"].median())
    fact(F, "median_site_eui", med, f"{fixed(med, 1)} kBtu per sq ft", "Median site EUI",
         f"Median site_eui_kbtu_sq_ft over the {len(with_eui):,} reported properties with one.", "energy.csv", [B],
         rounding="nearest tenth", unit="kBtu per sq ft")

    # ---- matching --------------------------------------------------------------------------------
    hm = int(sub["match_confidence"].isin(["high", "medium"]).sum())
    fact(F, "matched_high_medium", hm, count(hm), "Reported properties matched at high or medium confidence",
         "Submitted properties whose match_confidence is high or medium.", "buildings.csv", [B, FP], unit="properties")
    hmp = pct_of(hm, len(sub))
    fact(F, "matched_high_medium_pct", hmp, f"{fixed(hmp, 1)}%", "Share matched at high or medium confidence",
         f"{hm:,} of {len(sub):,} reported properties.", "buildings.csv", [B, FP], rounding="nearest tenth of a percent",
         unit="percent")
    tgt = float(_dec(schema.MATCH_TARGET) * 100)
    fact(F, "match_target_pct", tgt, f"{fixed(tgt, 0)}%", "Match target set before matching",
         "The release bar the reconciliation states, met or not: the share of reported properties at high or medium confidence.",
         "buildings.csv", [B, FP], rounding="nearest percent", unit="percent")
    if prec:
        fact(F, "precision_reviewed", prec["reviewed"], count(prec["reviewed"]), "Matches checked for precision",
             "Rows of match_review.csv: the seeded sample of footprint matches checked one by one.", "match_review.csv",
             [B, FP], unit="matches")
        for key, v, label, what in (
                ("precision_drawn_pct", prec["drawn_outlines"]["estimated_precision"], "Drawn outlines on the right building",
                 "outlines the map draws"),
                ("precision_high_pct", prec["by_confidence"]["high"]["estimated_precision"],
                 "High-confidence matches on the right building", "high-confidence matches"),
                ("precision_medium_pct", prec["by_confidence"]["medium"]["estimated_precision"],
                 "Medium-confidence matches on the right building", "medium-confidence matches")):
            pv = float(_dec(v) * 100)
            fact(F, key, pv, f"{fixed(pv, 0)}%", label,
                 f"Population-weighted precision of the {what}, from the verdicts in match_review.csv (correct or one building of "
                 "a campus, over correct plus wrong).", "match_review.csv", [B, FP], rounding="nearest percent", unit="percent")
    n = int(((sub["n_footprints"] == 0) & (sub["year_built"] > schema.FOOTPRINT_VINTAGE_YEAR)).sum())
    fact(F, "built_after_footprints_no_outline", n, count(n), f"Reported properties built after {schema.FOOTPRINT_VINTAGE_YEAR} with no outline",
         f"Submitted properties with no footprint_ids and a year_built after {schema.FOOTPRINT_VINTAGE_YEAR}.", "buildings.csv",
         [B, FP], unit="properties")
    fact(F, "footprint_vintage_year", schema.FOOTPRINT_VINTAGE_YEAR, str(schema.FOOTPRINT_VINTAGE_YEAR), "Footprint layer vintage",
         "The newest year_built in the City's building footprint layer, a 2015 snapshot.", "buildings.csv", [FP])
    placed = sum(1 for o in overrides.values() if len(o["footprint_ids"]) or o.get("coordinate"))
    fact(F, "overrides", len(overrides), count(len(overrides)), "Properties resolved on review",
         "Rows of footprint_overrides.csv: matches the tiers could not settle, resolved one at a time against named evidence.",
         "footprint_overrides.csv", [B, FP], unit="properties")
    fact(F, "overrides_placed", placed, count(placed), "Resolved properties placed",
         "Override rows with footprint_ids or a coordinate.", "footprint_overrides.csv", [B, FP], unit="properties")
    fact(F, "overrides_unplaced", len(overrides) - placed, count(len(overrides) - placed), "Resolved properties left unplaced",
         "Override rows with neither footprints nor a coordinate: a veto.", "footprint_overrides.csv", [B, FP], unit="properties")
    tol = float(_dec(schema.GFA_TOLERANCE) * 100)
    fact(F, "floor_area_tolerance_pct", tol, f"{fixed(tol, 0)}%", "Floor-area consistency tolerance",
         "A record is floor-area consistent where EUI times floor area is within this of the fuel total.", "energy.csv", [B],
         rounding="nearest percent", unit="percent")

    # ---- density and the map -------------------------------------------------------------------
    fact(F, "n_classes", classes["n_classes"], str(classes["n_classes"]), "Color classes",
         "Quantile classes per metric, computed once at build time and published in classes.json.", "classes.json", [B],
         unit="classes")
    w = float(fixed(schema.HEX_SIZE_M * schema.FT_PER_M, 4))
    fact(F, "hex_width", w, f"{fixed(w, 0)}-ft", "Density hexagon width",
         "Width of each density hexagon, flat side to flat side, in feet.", "density_hex.csv", [B],
         rounding="nearest foot", unit="feet")
    mi = float(fixed(schema.HEX_SIZE_M * schema.FT_PER_M / 5280, 4))
    if abs(mi - 0.25) / 0.25 >= 0.01:
        raise SystemExit(f"facts.json: a {mi}-mile hexagon is not a quarter mile to within 1%; restate hex_width_mi")
    fact(F, "hex_width_mi", mi, "quarter-mile", "Density hexagon width",
         f"Width of each density hexagon, flat side to flat side: {fixed(mi, 4)} miles ({int(fixed(w, 0)):,} feet), "
         "a quarter mile to within 1%.", "density_hex.csv", [B], rounding="nearest quarter mile", unit="miles")
    valued = hexes[(hexes["data_year"] == y) & hexes["site_energy_kbtu"].notna()]
    fact(F, "hex_with_value", len(valued), count(len(valued)), "Hexagons with a value",
         "density_hex.csv rows with a site_energy_kbtu: hexagons holding a reporting property with a total.", "density_hex.csv",
         [B, FP], unit="hexagons")
    n = int((valued["n_submitted"] == 1).sum())
    fact(F, "hex_one_property", n, count(n), "Hexagons with a value that hold one reporting property",
         "Of the hexagons with a value, those whose n_submitted is 1.", "density_hex.csv", [B, FP], unit="hexagons")
    n = int(((areas["data_year"] == y) & (areas["n_submitted"] < 5)).sum())
    fact(F, "ca_under_five", n, count(n), "Community areas with fewer than five reporting properties",
         "density_ca.csv rows whose n_submitted is below 5.", "density_ca.csv", [B, CA], unit="areas")
    for name, key in (("energy.csv", "energy_csv_mb"), ("buildings.csv", "buildings_csv_mb")):
        mb = float(_dec(sizes[name]) / 1_000_000)
        fact(F, key, mb, f"{fixed(mb, 1)} MB", f"Size of {name}", "Its size in bytes divided by 1,000,000.", name, [B],
             rounding="nearest tenth", unit="MB")

    invariants = [
        {"sum": ["reported", "not_reported", "exempt", "not_covered"], "equals": "covered"},
        {"sum": ["drawn_as_footprints", "drawn_as_markers", "not_mapped"], "equals": "covered"},
        {"sum": ["reported_no_eui", "reported_with_eui"], "equals": "reported"},
        {"sum": ["overrides_placed", "overrides_unplaced"], "equals": "overrides"},
    ]
    return {"schema": 1, "project": "chicago-building-energy", "release": schema.RELEASE_DATE,
            "generated_by": "scripts/build.py", "sources": sources(raw_manifest), "facts": F, "invariants": invariants}


def sources(raw_manifest: dict) -> list[dict]:
    meta = raw_manifest["meta"]
    return [{"id": k, "name": f"{s['name']} (City of Chicago data portal, {s['dataset']})", "url": SOCRATA + s["dataset"],
             "pulled": meta["fetched_on"], "rows_updated": s["rowsUpdatedAt_iso"][:10]}
            for k, s in meta["sources"].items()]
