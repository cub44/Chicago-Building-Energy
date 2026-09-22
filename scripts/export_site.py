#!/usr/bin/env python3
"""Stage 4 - export. The map contract in site/data/ (PIPELINE §4, MAP_SPEC).

    python scripts/export_site.py

Reads data/processed/ (the release) and data/interim/ (geometry). Writes:

    footprints.topojson   object 'footprints': one feature per property located by its
                          footprint(s), a MultiPolygon when several are attached; properties: id
                          only. EPSG:3435 feet, Douglas-Peucker at 1 ft, quantized 1e5.
    points.json           [{id, x, y}] for properties located by their trusted coordinate: no
                          footprint, or only a low-confidence one (build.located_by)
    hex.topojson          object 'hex' (hex_id): the hexagons that hold at least one property
    community.topojson    object 'ca' (area_numbe): the 77 community areas
    values_<year>.json    metrics {metric: {id: value}}, density {geo: {metric: {geo_id: value}}},
                          and the tooltip fields, one file per display year
    manifest.json         years, default year, metrics, class breaks, snapshot date, the City's
                          rowsUpdatedAt. The map reads years and breaks from here and nowhere else.

`caveats.json` is authored copy, not pipeline output (same as potholes). This script never
writes it; it checks that the figures the copy quotes are the figures the build produced, so
a rebuild cannot quietly falsify the prose.

Geometry goes through mapshaper 0.7.61 (package.json pins it; `npm install` once), the same
tool and version as the potholes map.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely
from shapely.geometry import Polygon, mapping
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent))
import schema  # noqa: E402
from build import HEX_DY_FT, HEX_SQMI, HEX_W_FT  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
INTERIM = ROOT / "data" / "interim"
PROCESSED = ROOT / "data" / "processed"
SITE = ROOT / "site" / "data"
WORK = INTERIM / "site_geom"
MAPSHAPER = ROOT / "node_modules" / ".bin" / "mapshaper"
AUTHORED = {"caveats.json"}

# MAP_SPEC §6. For these types a name is shown only when it reads as a building's name.
RESIDENTIAL_TYPES = {"Multifamily Housing", "Senior Living Community", "Residence Hall/Dormitory"}
OWNER_TOKENS = re.compile(r"\b(LLC|L\.L\.C|L L C|LP|L\.P|LLP|LTD|TRUST|ASSOC|ASSN|ASS'N|ASSOCIATES|ASSOCIATION|HOA|"
                          r"MANAGEMENT|MGMT|INC|CORP|CORPORATION|CO|COMPANY|PARTNERS|PARTNERSHIP|HOLDINGS|"
                          r"PROPERTIES|REALTY|INVESTMENTS|VENTURES|OWNER|OWNERS)\b\.?", re.I)
BUILDING_WORDS = re.compile(r"\b(APARTMENTS?|APTS?|TOWERS?|PLACE|PLAZA|COURTS?|MANOR|HOMES?|HOUSE|HALL|LOFTS?|"
                            r"CONDOMINIUMS?|CONDOS?|RESIDENCES?|VILLAGE|TERRACE|SQUARE|COMMONS|CENT(?:ER|RE)|"
                            r"GARDENS?|ARMS|HOTEL|BUILDING|BLDG|LANDING|POINTE?|VIEW|PARK|ESTATES?|VILLAS?|"
                            r"COOPERATIVE|CO-OP|DORMITORY|COMMUNITY|LIVING|SENIOR|CAMPUS)\b", re.I)

DRAWN = ["footprint", "trusted_coordinate", "none"]   # build.located_by, as the map draws it

METRICS = {
    "buildings": [
        {"key": "site_eui", "label": "Per square foot", "default": True, "unit": "kBtu per sq ft",
         "definition": "Site EUI: energy used on site per square foot of gross floor area, as reported for {year}."},
        {"key": "site_energy_kbtu", "label": "Total", "default": False, "unit": "kBtu per year",
         "definition": "Total site energy: the sum of the fuel use reported for {year} (electricity, gas, "
                       "district steam and chilled water, other). Not EUI x floor area: the City's floor area is "
                       "inflated for nearly two in five {year} records."}],
    "density": [
        {"key": "kbtu_per_sqmi", "label": "Total per square mile", "default": True,
         "unit": "kBtu per year per sq mi",
         "definition": "Total site energy of submitted benchmarked properties per square mile. Cells with no "
                       "submitted property carry no value."}],
}


def shown_name(name, property_type):
    """MAP_SPEC §6. Non-residential: the City's property_name as published. Residential: only
    when it carries no owner, LLC or management token AND reads as a building (a building
    word, or a street number). Anything else - which is where personal names fall - is withheld,
    and the map shows the address and the type. No type (every non-reporter, and some
    exempt and reporting properties): the owner-token filter applies, since the property may be
    residential; the building-word test does not, because it would withhold schools and
    hospitals too."""
    if name is None or pd.isna(name) or not str(name).strip():
        return None
    name = str(name).strip()
    if property_type is None or pd.isna(property_type):
        return None if OWNER_TOKENS.search(name) else name
    if property_type not in RESIDENTIAL_TYPES:
        return name
    if OWNER_TOKENS.search(name):
        return None
    return name if (BUILDING_WORDS.search(name) or re.search(r"\d", name)) else None


# --- geometry ------------------------------------------------------------------------------------------
def write_geojson(path: Path, features: list[tuple[dict, object]]) -> None:
    """GeoJSON with coordinates to the hundredth of a foot, in the order given."""
    def rnd(o):
        if isinstance(o, (list, tuple)):
            return [rnd(x) for x in o]
        return round(o, 2)
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": props,
         "geometry": {"type": (g := mapping(geom))["type"], "coordinates": rnd(g["coordinates"])}}
        for props, geom in features]}
    path.write_text(json.dumps(fc, separators=(",", ":")))


def mapshaper(args: list[str]) -> None:
    if not MAPSHAPER.exists():
        raise SystemExit("mapshaper is not installed - run `npm install` in this project (package.json pins 0.7.61)")
    r = subprocess.run([str(MAPSHAPER)] + args, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"mapshaper failed: {' '.join(args)}\n{r.stderr}")


def hexagon(col: int, row: int) -> Polygon:
    cx = (col + (0.5 if row % 2 else 0.0)) * HEX_W_FT
    cy = row * HEX_DY_FT
    r = HEX_W_FT / math.sqrt(3)
    return Polygon([(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a)))
                    for a in (30, 90, 150, 210, 270, 330)])


def topo(name: str, features, simplify: list[str]) -> None:
    src = WORK / f"{name[0]}.geojson"
    write_geojson(src, features)
    mapshaper(["-i", str(src), *simplify, "-o", str(SITE / name[1]), "format=topojson",
               "quantization=100000"])


# --- values ----------------------------------------------------------------------------------------------
def num(v, nd=None):
    if v is None or pd.isna(v):
        return None
    v = float(v)
    if nd is not None:
        v = round(v, nd)
    return int(v) if v.is_integer() else v


def values_for(year: int, energy: pd.DataFrame, buildings: pd.DataFrame, hexes: pd.DataFrame,
               areas: pd.DataFrame) -> dict:
    e = energy[energy["data_year"] == year].merge(buildings, on="id", how="left").sort_values("id")
    sub = e[e["status"] == "submitted"]
    metrics = {
        "site_eui": {str(r.id): num(r.site_eui_kbtu_sq_ft) for r in sub.itertuples()
                     if not pd.isna(r.site_eui_kbtu_sq_ft)},
        "site_energy_kbtu": {str(r.id): num(r.site_energy_kbtu) for r in sub.itertuples()
                             if not pd.isna(r.site_energy_kbtu)}}
    types = sorted(t for t in e["primary_property_type"].dropna().unique())
    t_idx = {t: i for i, t in enumerate(types)}
    # Columnar and index-coded to stay near the 400 KB target: type/status/confidence/method/drawn
    # are indexes into the lists beside `rows`; fuel_mix_shown is 0/1 and fuel_shares_permille
    # five integers in the order of `fuels`, present wherever the total is the fuel sum;
    # gfa_ratio_permille is ratio_to_eui_x_gfa x 1000, present only where the floor area is not
    # consistent (gfa_consistent false).
    fields = ["id", "name", "address", "type", "gfa", "status", "rating", "of_buildings",
              "match_confidence", "match_method", "n_footprints", "fuel_mix_shown", "fuel_shares_permille",
              "drawn", "gfa_ratio_permille"]
    rows = []
    for r in e.itertuples():
        shares, mix = None, r.site_energy_basis == "fuel_sum"
        if mix:
            shares = [round(0.0 if pd.isna(v) else 1000 * float(v) / r.fuel_sum_kbtu)
                      for v in (getattr(r, c) for c in schema.FUEL_COLUMNS)]
        ratio = None if pd.isna(r.gfa_consistent) or bool(r.gfa_consistent) else round(1000 * r.ratio_to_eui_x_gfa)
        rows.append([int(r.id), shown_name(r.property_name, r.primary_property_type), r.address,
                     None if pd.isna(r.primary_property_type) else t_idx[r.primary_property_type],
                     num(r.gross_floor_area_buildings_sq_ft, 0), schema.STATUSES.index(r.status),
                     num(r.chicago_energy_rating), num(r.of_buildings),
                     schema.MATCH_CONFIDENCES.index(r.match_confidence),
                     schema.MATCH_METHODS.index(r.match_method), int(r.n_footprints),
                     int(mix), shares, DRAWN.index(r.located_by), ratio])
    h = hexes[hexes["data_year"] == year]
    a = areas[areas["data_year"] == year]

    def geo(frame, key, measures):
        out = {}
        for m in measures:
            out[m] = {str(k): num(v) for k, v in zip(frame[key], frame[m]) if not pd.isna(v)}
        return out
    # A hexagon's area is constant (manifest.geometry.hex_sqmi), so its total is not repeated.
    density = {"hex": geo(h, "hex_id", ("kbtu_per_sqmi", "n_properties", "n_submitted")),
               "ca": geo(a, "community_area_num", ("kbtu_per_sqmi", "site_energy_kbtu", "n_properties",
                                                   "n_submitted", "n_not_submitted"))}
    density["ca"]["share_not_submitted"] = {str(k): num(v) for k, v in
                                            zip(a["community_area_num"], a["share_not_submitted"]) if not pd.isna(v)}
    density["ca"]["name"] = {str(k): v for k, v in zip(a["community_area_num"], a["name"])}
    # MAP_SPEC §1: the community-area tooltip names its three largest reported site energies.
    top = {}
    pts = pd.read_parquet(INTERIM / "property_points.parquet")[["id", "community_area_num"]]
    ranked = sub[sub["site_energy_kbtu"].notna()].merge(pts, on="id").sort_values(
        ["community_area_num", "site_energy_kbtu", "id"], ascending=[True, False, True])
    for ca, g in ranked.groupby("community_area_num"):
        top[str(int(ca))] = [int(i) for i in g["id"].head(3)]
    density["ca"]["largest_reported_site_energy"] = top
    return {"year": year, "metrics": metrics, "density": density,
            "properties": {"fields": fields, "types": types, "statuses": schema.STATUSES,
                           "confidences": schema.MATCH_CONFIDENCES, "methods": schema.MATCH_METHODS,
                           "drawn": DRAWN, "fuels": schema.FUEL_COLUMNS, "rows": rows}}


# --- caveats: authored, checked --------------------------------------------------------------------------
def check_caveats(figures: dict) -> None:
    p = SITE / "caveats.json"
    if not p.exists():
        raise SystemExit("site/data/caveats.json is missing. It is authored copy (MAP_SPEC §5), not output.")
    c = json.loads(p.read_text())
    wrong = {k: (v, figures.get(k)) for k, v in c.get("checked_figures", {}).items() if figures.get(k) != v}
    if wrong:
        raise SystemExit("caveats.json quotes figures this build does not produce (quoted, built): "
                         + json.dumps(wrong) + ". Edit the copy, not the data.")
    text = json.dumps(c).lower()
    for w in schema.BANNED_FRAGMENTS:
        if re.search(rf"\b{w}s?\b", text):
            raise SystemExit("caveats.json uses ranking language MAP_SPEC §6 rules out")


def main() -> None:
    summary = json.loads((INTERIM / "normalize_summary.json").read_text())
    match = json.loads((INTERIM / "match_summary.json").read_text())
    raw_manifest = json.loads((ROOT / "data" / "raw" / summary["snapshot"] / "MANIFEST.json").read_text())
    classes = json.loads((PROCESSED / "classes.json").read_text())
    energy = pd.read_parquet(INTERIM / "energy_long.parquet")
    energy = energy[energy["data_year"].isin(schema.DISPLAY_YEARS)].copy()   # excluded years stop here
    assert not set(energy["data_year"]) & set(schema.EXCLUDED_YEARS)
    buildings = pd.read_parquet(INTERIM / "buildings.parquet").merge(
        pd.read_parquet(INTERIM / "property_points.parquet")[["id", "located_by"]], on="id", how="left")
    footprints = gpd.read_parquet(INTERIM / "footprints.parquet").drop_duplicates("bldg_id", keep=False).set_index("bldg_id")
    cas = gpd.read_parquet(INTERIM / "community_areas.parquet")
    hexes = pd.read_csv(PROCESSED / "density_hex.csv")
    areas = pd.read_csv(PROCESSED / "density_ca.csv")

    SITE.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    for old in SITE.glob("*"):
        if old.name not in AUTHORED:
            old.unlink()

    shown = buildings[buildings["id"].isin(energy["id"])].sort_values("id")
    drawn = shown[shown["located_by"] == "footprint"]
    feats = []
    for r in drawn.itertuples():
        geom = unary_union([footprints.geometry[int(b)] for b in r.footprint_ids])
        feats.append(({"id": int(r.id)}, shapely.make_valid(geom) if not geom.is_valid else geom))
    topo(("footprints", "footprints.topojson"), feats, ["-simplify", "dp", "interval=1", "planar", "keep-shapes"])

    marks = shown[shown["located_by"] == "trusted_coordinate"]
    (SITE / "points.json").write_text(json.dumps(
        [{"id": int(r.id), "x": round(r.trusted_x_ft), "y": round(r.trusted_y_ft)} for r in marks.itertuples()],
        separators=(",", ":")))
    not_drawn = shown[shown["located_by"] == "none"]

    cells = sorted(set(hexes["hex_id"]))
    topo(("hex", "hex.topojson"),
         [({"hex_id": h}, hexagon(*[int(x) for x in re.match(r"r(-?\d+)c(-?\d+)", h).groups()][::-1])) for h in cells], [])
    topo(("ca", "community.topojson"),
         [({"area_numbe": int(n), "community": c}, g) for n, c, g in zip(cas["area_numbe"], cas["community"], cas.geometry)],
         ["-simplify", "visvalingam", "planar", "keep-shapes", "percentage=0.08"])

    figures = {}
    for y in schema.DISPLAY_YEARS:
        v = values_for(y, energy, buildings, hexes, areas)
        (SITE / f"values_{y}.json").write_text(json.dumps(v, separators=(",", ":"), ensure_ascii=False))
        e = energy[energy["data_year"] == y]
        sub = e[e["status"] == "submitted"]
        fa = summary["years"][str(y)]["floor_area"]
        m_y = match["by_year"][str(y)]["submitted"]
        in_y = shown[shown["id"].isin(e["id"])]
        figures.update({
            f"properties_{y}": int(len(e)), f"submitted_{y}": int(len(sub)),
            f"not_submitted_{y}": int((e["status"] == "not_submitted").sum()),
            f"exempt_{y}": int((e["status"] == "exempt").sum()),
            f"not_covered_{y}": int((e["status"] == "not_covered").sum()),
            f"submitted_no_eui_{y}": int(sub["site_eui_kbtu_sq_ft"].isna().sum()),
            f"total_records_{y}": fa["submitted_with_total"], f"gfa_inflated_{y}": fa["overstated_3pct_or_more"],
            f"match_high_or_medium_pct_{y}": round(100 * m_y["high_or_medium_share"], 1),
            f"drawn_as_footprint_{y}": int((in_y["located_by"] == "footprint").sum()),
            f"drawn_as_marker_{y}": int((in_y["located_by"] == "trusted_coordinate").sum()),
            f"not_drawn_{y}": int((in_y["located_by"] == "none").sum()),
            f"excluded_year_pass_rate_pct": round(100 * min(summary["years"][str(x)]["coordinates"]["pass_rate"]
                                                            for x in schema.EXCLUDED_YEARS)),
            f"excluded_year_located_pct": round(100 * min(t["high_or_medium_share"] for t in
                                                          match["excluded_year_location_test"].values())),
            f"hex_with_value_{y}": int((hexes["data_year"] == y).mul(hexes["site_energy_kbtu"].notna()).sum()),
            f"hex_one_property_{y}": int(((hexes["data_year"] == y) & hexes["site_energy_kbtu"].notna()
                                          & (hexes["n_submitted"] == 1)).sum()),
            f"ca_under_five_{y}": int(((areas["data_year"] == y) & (areas["n_submitted"] < 5)).sum()),
        })
    prec_path = INTERIM / "precision.json"
    prec = json.loads(prec_path.read_text()) if prec_path.exists() else None
    if prec:
        figures.update({"precision_reviewed": prec["reviewed"],
                        "precision_drawn_pct": round(100 * prec["drawn_outlines"]["estimated_precision"]),
                        "precision_high_pct": round(100 * prec["by_confidence"]["high"]["estimated_precision"]),
                        "precision_medium_pct": round(100 * prec["by_confidence"]["medium"]["estimated_precision"])})
    check_caveats(figures)

    files = {p.name: {"bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
             for p in sorted(SITE.glob("*")) if p.is_file() and p.name != "manifest.json"}
    bench = raw_manifest["meta"]["sources"]["benchmarking"]
    manifest = {
        "project": "chicago-building-energy",
        "years": list(schema.DISPLAY_YEARS), "default_year": schema.DISPLAY_YEARS[-1],
        "snapshot_date": summary["snapshot"],
        "source": {k: {"dataset": s["dataset"], "name": s["name"], "rowsUpdatedAt": s["rowsUpdatedAt"],
                       "rowsUpdatedAt_iso": s["rowsUpdatedAt_iso"]}
                   for k, s in raw_manifest["meta"]["sources"].items()},
        "benchmarking_rows_updated": bench["rowsUpdatedAt_iso"][:10],
        "data_release": schema.RELEASE_DATE,
        "metrics": {mode: [{**m, "definition": m["definition"]} for m in ms] for mode, ms in METRICS.items()},
        "n_classes": classes["n_classes"], "class_rule": classes["method"],
        "classes": {k: v["years"] for k, v in classes["metrics"].items()},
        "geometry": {"crs": schema.CRS_PLANE, "units": "US survey feet",
                     "projection": "d3.geoIdentity().reflectY(true)",
                     "hex_flat_to_flat_m": schema.HEX_SIZE_M, "hex_sqmi": round(HEX_SQMI, 6)},
        "counts": figures, "files": files,
    }
    (SITE / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n")
    for p in sorted(SITE.glob("*")):
        print(f"  {p.stat().st_size:>10,}  {p.name}")


if __name__ == "__main__":
    main()
