#!/usr/bin/env python3
"""Stage 1 - normalize. Newest data/raw/<date>/ in, data/interim/*.parquet out.

    python scripts/normalize.py            # everything; reuses footprints.parquet if current
    python scripts/normalize.py --force    # rebuild footprints.parquet from the CSV

Reads only data/raw/. Writes only data/interim/ (gitignored, rebuildable):

    energy_long.parquet     one row per (id, data_year), EVERY year in the snapshot, typed,
                            with status, total site energy (the fuel sum; see site_energy),
                            the floor-area check, the parsed address, the
                            row coordinate in EPSG:3435 and its community-area test result.
                            Excluded years are kept here on purpose - the test that excludes
                            them has to be able to see them - and dropped by every later stage.
    covered.parquet         the covered-buildings list, same coordinate test
    community_areas.parquet the 77 polygons in EPSG:3435
    footprints.parquet      ACTIVE footprints, EPSG:3435 (GeoParquet)
    normalize_summary.json  per-year status labels, the floor-area check and its GHG
                            corroboration, coordinate pass rates; build.py turns it into
                            data/reconciliation.md

Nothing is filled in. A value the City did not publish is null here and empty downstream.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

sys.path.insert(0, str(Path(__file__).resolve().parent))
import schema  # noqa: E402
from normalize_address import normalize as parse_address  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
INTERIM = ROOT / "data" / "interim"

# Community-area names as the benchmarking release and the covered list spell them, where
# that differs from the boundary file's `community`. Anything else unmapped has no polygon
# to be tested against, so its coordinate is never trusted ('outside Chicago, in Bedford
# Park' is a real value).
CA_ALIASES = {"O'HARE": "OHARE", "LAKEVIEW": "LAKE VIEW"}

# The boundary file's `community` in capitals, as it is released beside the display name the
# pothole release spells (Chicago-Potholes `geo_name`): title case, except these two.
CA_DISPLAY = {"OHARE": "O'Hare", "MCKINLEY PARK": "McKinley Park"}


def display_name(community: str) -> str:
    """A community area's display name, from the boundary file's capitals."""
    return CA_DISPLAY.get(community, community.title())


class SchemaError(SystemExit):
    pass


def latest_snapshot() -> Path:
    snaps = sorted(d for d in RAW.iterdir() if d.is_dir() and (d / "MANIFEST.json").exists())
    if not snaps:
        raise SystemExit("no snapshot in data/raw/ - run scripts/fetch_sources.py first")
    return snaps[-1]


def load_manifest(snap: Path) -> dict:
    return json.loads((snap / "MANIFEST.json").read_text())


def check_schema(manifest: dict) -> None:
    """The portal's column list on the snapshot date must equal REQUIRED + ACKNOWLEDGED."""
    problems = []
    for key in schema.SOURCES:
        src = manifest["meta"]["sources"].get(key)
        if src is None:
            problems.append(f"{key}: not in MANIFEST.json (fetch with --footprints?)")
            continue
        have = set(src["columns"])
        want = set(schema.REQUIRED[key])
        known = want | set(schema.ACKNOWLEDGED[key])
        for c in sorted(want - have):
            problems.append(f"{key}: required column missing upstream: {c}")
        for c in sorted(have - known):
            problems.append(f"{key}: new upstream column not acknowledged in schema.py: {c}")
    if problems:
        raise SchemaError("schema check failed:\n  " + "\n  ".join(problems))


def check_hashes(snap: Path, manifest: dict) -> None:
    for name, rec in manifest["files"].items():
        p = snap / name
        if not p.exists():
            raise SystemExit(f"{name} is in MANIFEST.json but not in {snap}")
        h = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 22), b""):
                h.update(chunk)
        if h.hexdigest() != rec["sha256"]:
            raise SystemExit(f"{name}: SHA-256 differs from MANIFEST.json - raw snapshots are immutable")


# --- benchmarking ------------------------------------------------------------------------------
def map_status(labels: pd.Series) -> pd.Series:
    unknown = sorted(set(labels.dropna()) - set(schema.STATUS_MAP))
    if unknown or labels.isna().any():
        raise SchemaError(
            f"reporting_status labels not in schema.STATUS_MAP: {unknown or ['<null>']}. "
            "Decide which status each one is and add it; the build does not infer it.")
    return labels.map(schema.STATUS_MAP)


def _dec(v):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else Decimal(str(v))


def _round_kbtu(d: Decimal) -> int:
    return int(d.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def eui_x_gfa_kbtu(eui, gfa):
    """Site EUI x gross floor area, exact in decimal, rounded half-up to the kBtu. Null if
    either is null. The check on the published floor area, not the total (README §1)."""
    e, g = _dec(eui), _dec(gfa)
    if e is None or g is None:
        return None
    return _round_kbtu(e * g)


def _fuel_decimal(values):
    ds = [_dec(v) for v in values]
    if all(d is None for d in ds):
        return None
    return sum((d for d in ds if d is not None), Decimal(0))


def fuel_sum_kbtu(values):
    """Sum of the five fuel columns, nulls as 0, only when at least one is non-null."""
    d = _fuel_decimal(values)
    return None if d is None else float(d)


def site_energy(eui, gfa, fuels):
    """Total site energy -> (kBtu rounded half-up, basis).

    Formed only where the City published a site EUI: a record without one is one Portfolio
    Manager did not complete, and its fuel columns, where present, list one fuel in six of
    eight 2022 cases. Where there is an EUI the total is the sum of the five fuel columns;
    only if those are all empty or sum to zero does it fall back to EUI x GFA. The fuel sum is
    the side the published GHG figures corroborate (README §1)."""
    if _dec(eui) is None:
        return None, None
    f = _fuel_decimal(fuels)
    if f is not None and f > 0:
        return _round_kbtu(f), "fuel_sum"
    alt = eui_x_gfa_kbtu(eui, gfa)
    return (alt, "eui_x_gfa") if alt is not None else (None, None)


def ratio_to_eui_x_gfa(eui_x_gfa, total, basis):
    """Fuel total / (EUI x GFA), 6 decimals. 1 where the published floor area agrees with the
    EUI and the fuel figures; 0.835 where the floor area is about 1.2 times what they imply.
    The same quantity as the GHG check and as the 2023/2022 floor-area ratio for these ids.
    Null unless the total is a fuel sum and EUI x GFA is positive."""
    if basis != "fuel_sum" or not eui_x_gfa or total is None:
        return None
    return round(total / eui_x_gfa, 6)


def gfa_consistent(ratio):
    """True/False where the check is defined, None where it is not."""
    return None if ratio is None else abs(ratio - 1) < schema.GFA_TOLERANCE


def load_benchmarking(snap: Path) -> pd.DataFrame:
    rows = json.loads((snap / schema.SOURCES["benchmarking"]["file"]).read_text())
    raw = pd.DataFrame(rows).reindex(columns=schema.REQUIRED["benchmarking"])
    df = pd.DataFrame({
        "id": raw["id"].astype("int64"),
        "data_year": raw["data_year"].astype("int64"),
        "property_name": raw["property_name"],
        "reporting_status": raw["reporting_status"],
        "status": map_status(raw["reporting_status"]),
        "address": raw["address"],
        "zip_code": raw["zip_code"],
        "community_area": raw["community_area"],
        "primary_property_type": raw["primary_property_type"],
        "exempt_from_chicago_energy_rating": raw["exempt_from_chicago_energy_rating"],
        "row_id": raw["row_id"],
    })
    # Numeric columns keep the published text alongside nothing: the float is the value, and
    # the exact decimal arithmetic below goes back to the source string.
    for c in schema.NUMERIC_COLUMNS + ["latitude", "longitude"]:
        df[c] = pd.to_numeric(raw[c], errors="raise")
    for c in ("year_built", "of_buildings"):
        df[c] = df[c].astype("Int64")

    fuels = list(raw[schema.FUEL_COLUMNS].itertuples(index=False))
    eui, gfa = raw["site_eui_kbtu_sq_ft"], raw["gross_floor_area_buildings_sq_ft"]
    alt = [eui_x_gfa_kbtu(e, g) for e, g in zip(eui, gfa)]
    total = [site_energy(e, g, f) for e, g, f in zip(eui, gfa, fuels)]
    ratio = [ratio_to_eui_x_gfa(a, t, b) for a, (t, b) in zip(alt, total)]
    df["site_energy_kbtu"] = pd.array([t for t, _ in total], dtype="Int64")
    df["site_energy_basis"] = [b for _, b in total]
    df["fuel_sum_kbtu"] = pd.array([fuel_sum_kbtu(f) for f in fuels], dtype="Float64").astype("float64")
    df["eui_x_gfa_kbtu"] = pd.array(alt, dtype="Int64")
    df["ratio_to_eui_x_gfa"] = pd.array(ratio, dtype="Float64").astype("float64")
    df["gfa_consistent"] = pd.array([gfa_consistent(x) for x in ratio], dtype="boolean")

    parsed = [parse_address(a) for a in df["address"]]
    df["addr_number"] = pd.array([p["number"] for p in parsed], dtype="Int64")
    df["addr_number_hi"] = pd.array([p["number_hi"] for p in parsed], dtype="Int64")
    df["addr_pre_dir"] = [p["pre_dir"] for p in parsed]
    df["addr_st_name"] = [p["st_name"] for p in parsed]
    df["addr_st_type"] = [p["st_type"] for p in parsed]
    df["addr_unit"] = [p["unit"] for p in parsed]
    df["addr_extra"] = [p["extra"] for p in parsed]
    df["addr_parse_ok"] = [p["parse_ok"] for p in parsed]

    if df.duplicated(["id", "data_year"]).any():
        raise SchemaError("benchmarking: (id, data_year) is not unique")
    return df.sort_values(["data_year", "id"]).reset_index(drop=True)


def load_covered(snap: Path) -> pd.DataFrame:
    rows = json.loads((snap / schema.SOURCES["covered"]["file"]).read_text())
    raw = pd.DataFrame(rows).reindex(columns=schema.REQUIRED["covered"])
    df = pd.DataFrame({
        "id": raw["building_id"].astype("int64"),
        "address": raw["address"],
        "cohort_sector": raw["cohort_sector"],
        "cohort_size": raw["cohort_size"],
        "community_area": raw["community_area_name"],
        "community_area_number": pd.to_numeric(raw["community_area_number"]).astype("Int64"),
        "ward": pd.to_numeric(raw["ward"]).astype("Int64"),
        "latitude": pd.to_numeric(raw["latitude"]),
        "longitude": pd.to_numeric(raw["longitude"]),
    })
    if df["id"].duplicated().any():
        raise SchemaError("covered: building_id is not unique")
    return df.sort_values("id").reset_index(drop=True)


# --- community areas and the coordinate test -----------------------------------------------------
def load_community_areas(snap: Path) -> gpd.GeoDataFrame:
    g = gpd.read_file(snap / schema.SOURCES["community_areas"]["file"])
    g = g[["area_numbe", "community", "geometry"]].copy()
    g["area_numbe"] = g["area_numbe"].astype("int64")
    g = g.to_crs(schema.CRS_PLANE).sort_values("area_numbe").reset_index(drop=True)
    if len(g) != 77 or g["area_numbe"].tolist() != list(range(1, 78)):
        raise SchemaError(f"community areas: expected numbers 1-77, got {len(g)} rows")
    return g


def ca_number(names: pd.Series, cas: gpd.GeoDataFrame) -> pd.Series:
    lookup = dict(zip(cas["community"], cas["area_numbe"]))
    key = names.str.strip().str.upper().replace(CA_ALIASES)
    return key.map(lookup).astype("Int64")


def coordinate_test(df: pd.DataFrame, cas: gpd.GeoDataFrame) -> pd.DataFrame:
    """Add x_ft, y_ft, ca_num and whether the row's own coordinate is inside its own stated
    community area - strictly (the README §3a test) and with the 100 m buffer (the trust rule).
    A row with no coordinate, or no community area that names a polygon, passes neither.
    """
    df = df.copy()
    df["ca_num"] = ca_number(df["community_area"], cas)
    has = df["latitude"].notna() & df["longitude"].notna()
    pts = gpd.GeoSeries(gpd.points_from_xy(df.loc[has, "longitude"], df.loc[has, "latitude"]),
                        index=df.index[has], crs=schema.CRS_GEO).to_crs(schema.CRS_PLANE)
    df["x_ft"] = pts.x.reindex(df.index)
    df["y_ft"] = pts.y.reindex(df.index)
    poly = dict(zip(cas["area_numbe"], cas.geometry))
    buf = {k: v.buffer(schema.CA_BUFFER_M * schema.FT_PER_M) for k, v in poly.items()}
    strict, buffered = [], []
    for i, ca in zip(df.index, df["ca_num"]):
        if i not in pts.index or pd.isna(ca):
            strict.append(False)
            buffered.append(False)
            continue
        strict.append(bool(poly[int(ca)].contains(pts[i])))
        buffered.append(bool(buf[int(ca)].contains(pts[i])))
    df["coord_in_ca"] = strict
    df["coord_in_ca_buffered"] = buffered
    return df


# --- footprints ---------------------------------------------------------------------------------------
def footprints_to_parquet(snap: Path, manifest: dict, out: Path, force: bool) -> dict:
    """The CSV becomes a GeoParquet once per snapshot: ACTIVE only, EPSG:3435."""
    src = snap / schema.SOURCES["footprints"]["file"]
    sha = manifest["files"][src.name]["sha256"]
    stamp = out.with_suffix(".json")
    if out.exists() and stamp.exists() and not force:
        rep = json.loads(stamp.read_text())
        if rep.get("source_sha256") == sha:
            return rep
    raw = pd.read_csv(src, dtype=str, keep_default_na=False)
    raw.columns = [c.lower() for c in raw.columns]
    missing = [c for c in schema.REQUIRED["footprints"] if c not in raw.columns]
    if missing:
        raise SchemaError(f"footprints CSV lacks required columns: {missing}")
    status = raw["bldg_statu"].value_counts().to_dict()
    raw = raw[raw["bldg_statu"] == schema.FOOTPRINT_ACTIVE]
    geom = shapely.from_wkt(raw["the_geom"].to_numpy())
    g = gpd.GeoDataFrame({
        "bldg_id": raw["bldg_id"].astype("int64"),
        "f_add1": pd.to_numeric(raw["f_add1"]).astype("Int64"),
        "t_add1": pd.to_numeric(raw["t_add1"]).astype("Int64"),
        "pre_dir1": raw["pre_dir1"].str.strip().str.upper(),
        "st_name1": raw["st_name1"].str.strip().str.upper(),
        "st_type1": raw["st_type1"].str.strip().str.upper(),
        "bldg_name1": raw["bldg_name1"].str.strip(),
        "bldg_name2": raw["bldg_name2"].str.strip(),
        "bldg_statu": raw["bldg_statu"],
        "year_built": pd.to_numeric(raw["year_built"]).astype("Int64"),
        "stories": pd.to_numeric(raw["stories"]).astype("Int64"),
        "shape_area": pd.to_numeric(raw["shape_area"]),
        "x_coord": pd.to_numeric(raw["x_coord"]),
        "y_coord": pd.to_numeric(raw["y_coord"]),
    }, geometry=geom, crs=schema.CRS_GEO).to_crs(schema.CRS_PLANE)
    empty = g.geometry.is_empty | g.geometry.isna()
    g = g[~empty].sort_values("bldg_id").reset_index(drop=True)
    # shape_area is the City's figure; area_sqft is ours, from the geometry we draw.
    g["area_sqft"] = g.geometry.area
    rel = ((g["area_sqft"] - g["shape_area"]).abs() / g["shape_area"].where(g["shape_area"] > 0))
    rep = {
        "source_sha256": sha,
        "rows_in_csv": int(sum(status.values())),
        "bldg_statu": {k: int(v) for k, v in sorted(status.items())},
        "active_kept": int(len(g)),
        "active_empty_geometry_dropped": int(empty.sum()),
        "duplicate_bldg_id": int(g["bldg_id"].duplicated().sum()),
        "max_year_built": int(g["year_built"].max()),
        "shape_area_vs_geometry": {
            "compared": int(rel.notna().sum()),
            "differ_over_1pct": int((rel > 0.01).sum()),
            "differ_over_10pct": int((rel > 0.10).sum()),
            "source_area_zero_or_null": int(rel.isna().sum()),
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    g.to_parquet(out, index=False)
    stamp.write_text(json.dumps(rep, indent=1))
    return rep


# --- summaries -----------------------------------------------------------------------------------------
# Bins of fuel total / (EUI x GFA). Below 0.97 the published floor area is the inflated side.
RATIO_BINS = [
    ("within_3pct", lambda r: (r - 1).abs() < 0.03),
    ("ratio_0.90_to_0.97", lambda r: (r <= 0.97) & (r > 0.90)),
    ("ratio_0.70_to_0.90", lambda r: (r <= 0.90) & (r > 0.70)),
    ("ratio_0.70_or_below", lambda r: r <= 0.70),
    ("ratio_1.03_or_above", lambda r: r >= 1.03),
]


def _r(v, nd=4):
    return None if v is None or (isinstance(v, float) and not np.isfinite(v)) else round(float(v), nd)


def floor_area_check(year_rows: pd.DataFrame) -> dict:
    """Which side of EUI x GFA vs the fuel sum is wrong, answered from a third column.

    The City publishes total GHG and GHG intensity per sq ft. Where the floor area is right,
    total GHG / (GHG intensity x GFA) is 1. Where the published GFA is inflated it drops by
    the same factor as fuel sum / (EUI x GFA); where the fuel columns were incomplete instead,
    it would stay at 1. Computed over submitted records whose total is a fuel sum."""
    sub = year_rows[(year_rows["status"] == "submitted") & year_rows["site_energy_kbtu"].notna()]
    fs = sub[sub["site_energy_basis"] == "fuel_sum"]
    x = fs["ratio_to_eui_x_gfa"].dropna()
    out = {"submitted_with_total": int(len(sub)), "total_is_fuel_sum": int(len(fs)),
           "total_is_eui_x_gfa": int((sub["site_energy_basis"] == "eui_x_gfa").sum()),
           "checkable": int(len(x))}
    for name, rule in RATIO_BINS:
        out[name] = int(rule(x).sum())
    out["overstated_3pct_or_more"] = int((x <= 0.97).sum())
    out["share_consistent"] = _r(out["within_3pct"] / len(x)) if len(x) else None
    fuel_ratio = fs["site_energy_kbtu"].astype("float64") / fs["eui_x_gfa_kbtu"].astype("float64")
    ghg_ratio = (fs["total_ghg_emissions_metric_tons_co2e"] * 1000
                 / (fs["ghg_intensity_kg_co2e_sq_ft"] * fs["gross_floor_area_buildings_sq_ft"]))
    ok = fuel_ratio.notna() & ghg_ratio.notna() & np.isfinite(ghg_ratio)
    over = ok & (fs["ratio_to_eui_x_gfa"] <= 0.97)
    ctrl = ok & ((fs["ratio_to_eui_x_gfa"] - 1).abs() < 0.03)
    out["ghg"] = {
        "overstated_with_ghg": int(over.sum()),
        "overstated_median_fuel_ratio": _r(fuel_ratio[over].median()) if over.any() else None,
        "overstated_median_ghg_ratio": _r(ghg_ratio[over].median()) if over.any() else None,
        "overstated_r": _r(np.corrcoef(fuel_ratio[over], ghg_ratio[over])[0, 1]) if over.sum() > 2 else None,
        "overstated_within_3_points": int(((fuel_ratio - ghg_ratio).abs()[over] < 0.03).sum()),
        "consistent_with_ghg": int(ctrl.sum()),
        "consistent_median_ghg_ratio": _r(ghg_ratio[ctrl].median()) if ctrl.any() else None,
    }
    out["sum_total_kbtu"] = int(sub["site_energy_kbtu"].sum())
    out["sum_eui_x_gfa_kbtu"] = int(sub["eui_x_gfa_kbtu"].dropna().sum())
    return out


def summarize(df: pd.DataFrame, cov: pd.DataFrame) -> dict:
    years = {}
    for y, g in df.groupby("data_year"):
        coord = g["latitude"].notna() & g["longitude"].notna()
        testable = coord & g["ca_num"].notna()
        years[int(y)] = {
            "rows": int(len(g)),
            "excluded": schema.EXCLUDED_YEARS.get(int(y)),
            "status_labels": {k: int(v) for k, v in sorted(g["reporting_status"].value_counts().items())},
            "status": {k: int(v) for k, v in sorted(g["status"].value_counts().items())},
            "submitted_with_eui": int(((g["status"] == "submitted") & g["site_eui_kbtu_sq_ft"].notna()).sum()),
            "address_parse_failed": int((~g["addr_parse_ok"]).sum()),
            "coordinates": {
                "with_coordinate": int(coord.sum()),
                "no_community_area_to_test": int((coord & g["ca_num"].isna()).sum()),
                "testable": int(testable.sum()),
                "inside_own_community_area": int(g.loc[testable, "coord_in_ca"].sum()),
                "inside_with_100m_buffer": int(g.loc[testable, "coord_in_ca_buffered"].sum()),
                "pass_rate": round(float(g.loc[testable, "coord_in_ca"].mean()), 4) if testable.any() else None,
            },
            "floor_area": floor_area_check(g),
        }
    c = cov["latitude"].notna() & cov["ca_num"].notna()
    return {
        "years": years,
        "covered_list": {
            "rows": int(len(cov)), "testable": int(c.sum()),
            "inside_own_community_area": int(cov.loc[c, "coord_in_ca"].sum()),
            "inside_with_100m_buffer": int(cov.loc[c, "coord_in_ca_buffered"].sum()),
            "pass_rate": round(float(cov.loc[c, "coord_in_ca"].mean()), 4),
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="rebuild footprints.parquet from the CSV")
    args = ap.parse_args()
    snap = latest_snapshot()
    manifest = load_manifest(snap)
    print(f"snapshot {snap.name}", flush=True)
    check_schema(manifest)
    check_hashes(snap, manifest)
    INTERIM.mkdir(parents=True, exist_ok=True)

    cas = load_community_areas(snap)
    cas.to_parquet(INTERIM / "community_areas.parquet", index=False)
    df = coordinate_test(load_benchmarking(snap), cas)
    cov = coordinate_test(load_covered(snap), cas)
    df.to_parquet(INTERIM / "energy_long.parquet", index=False)
    cov.to_parquet(INTERIM / "covered.parquet", index=False)

    summary = summarize(df, cov)
    summary["snapshot"] = snap.name
    summary["footprints"] = footprints_to_parquet(snap, manifest, INTERIM / "footprints.parquet", args.force)
    (INTERIM / "normalize_summary.json").write_text(json.dumps(summary, indent=1))

    print(f"energy_long: {len(df):,} rows, years {df['data_year'].min()}-{df['data_year'].max()}")
    print(f"footprints : {summary['footprints']['active_kept']:,} ACTIVE of "
          f"{summary['footprints']['rows_in_csv']:,}")
    print("coordinate inside own community area, by year (strict test):")
    for y, s in summary["years"].items():
        c = s["coordinates"]
        flag = "  EXCLUDED" if s["excluded"] else ""
        print(f"  {y}: {c['inside_own_community_area']:>5,} of {c['testable']:>5,} testable "
              f"({c['pass_rate']:.1%}); {c['no_community_area_to_test']} with no community area{flag}")
    for y in schema.DISPLAY_YEARS:
        f = summary["years"][y]["floor_area"]
        print(f"{y} total site energy: {f['sum_total_kbtu']:,} kBtu over {f['submitted_with_total']:,} submitted "
              f"records ({f['total_is_fuel_sum']:,} fuel sums); EUI x GFA would give {f['sum_eui_x_gfa_kbtu']:,}")
        for k in [n for n, _ in RATIO_BINS]:
            print(f"  EUI x GFA vs fuel total, {k:<24}{f[k]:>6,}")
        g = f["ghg"]
        print(f"  GHG check: {g['overstated_with_ghg']:,} overstated records, fuel ratio {g['overstated_median_fuel_ratio']}, "
              f"GHG ratio {g['overstated_median_ghg_ratio']}, r = {g['overstated_r']}; "
              f"consistent records' GHG ratio {g['consistent_median_ghg_ratio']}")


if __name__ == "__main__":
    main()
