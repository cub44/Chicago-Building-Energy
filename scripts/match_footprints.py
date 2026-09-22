#!/usr/bin/env python3
"""Stage 2 - match. One row per benchmarking id -> data/interim/buildings.parquet.

    python scripts/match_footprints.py

Implements PIPELINE §2 in its order; the first tier that hits wins and `match_note` records
what was tried on the way:

  1  trusted coordinate   row_<display year>, covered_list, then other admitted years newest
                          first. A candidate is accepted only if it lies inside ITS OWN row's
                          stated community area buffered 100 m. No year in EXCLUDED_YEARS and no
                          year whose pass rate is under 80% is ever a candidate.
  2  T1 / T1b             number in range, same direction, same street; one footprint, or the
                          street type narrows several to one                       -> high
  3  T1_multi             several remain: all attached if of_buildings > 1 or they sit within
                          60 m of each other (high); else the one nearest the trusted
                          coordinate (medium); with no coordinate, all (low)
  4  T2                   number in range, same street, direction blank on one side or
                          different -> medium if within 60 m of the trusted coordinate; farther
                          than that it is not matched and the coordinate tiers run (a North/South
                          mirror); with no coordinate, low
  5  coord_pip            trusted coordinate inside an ACTIVE footprint             -> medium
  6  coord_nearest        nearest ACTIVE footprint <= 30 m -> medium. Nothing farther: the
                          2026-09-21 precision check found 1 of 17 matches at 30-75 m right
  7  T3                   retired 2026-09-21. It took the nearest address range on the same
                          street within 24 house numbers; two precision checks found it right 2
                          times in 10 with no coordinate and 2 in 20 with one within 60 m, most
                          often the building across the street. A property that reaches this
                          point is placed at its trusted coordinate, or not at all
  8  override             data/overrides/footprint_overrides.csv, applied last, always wins.
                          It may also name a City-published coordinate accepted on review
                          (coord_source 'override'). Empty footprint_ids with no coordinate
                          is a veto: not drawn, not located.
  9  year guard           at every tier a footprint built more than 3 years after the
                          benchmarked building is another building, and is noted - except
                          where the address and the trusted coordinate agree on it (it holds
                          the number and lies within 30 m of the coordinate): then the guard
                          is advisory, noted and not applied
 10  ranges               with addr_number_hi set, a footprint whose range overlaps
                          [addr_number, addr_number_hi] on the same side of the street is in range
 11  size check           after the tiers, before overrides: gross floor area over attached
                          footprint area (a campus's scaled by attached / reported buildings).
                          Over 120 floors rejects the footprint. Over 60, or more than twice
                          the stories the City's layer gives the attached footprints,
                          downgrades the match one step, unless the layer gives 40+ stories

Three readings of that text are made here, each one narrower than the text, none wider. They
are module constants so that a test can show what each one changes:

  SAME_SIDE_ONLY          Footprint ranges in this table are single-parity without exception,
                          so parity is the side of the street. Rule 10 asks for the same side
                          for a range; the same is asked of a single number.
  TYPE_CONFLICT_REJECTS   236 of 1,520 (direction, street) pairs exist with two street types
                          (E 100TH ST and E 100TH PL are different streets). A footprint whose
                          type differs from the address's is not a candidate when the
                          footprint table has that street under the address's type as well.
                          Otherwise the type stays what PIPELINE calls it: a tiebreaker.
  POST_VINTAGE_UNMATCHED  README §3b: the footprint layer is a 2015 snapshot, so a property
                          built after 2015 has no footprint in it and is drawn as a point. No
                          tier runs for it; only an override can attach a footprint.

Nothing is geocoded. A coordinate is only ever one the City published, and only after it has
passed the community-area test.
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import Point

sys.path.insert(0, str(Path(__file__).resolve().parent))
import schema  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
INTERIM = ROOT / "data" / "interim"
OVERRIDES = ROOT / "data" / "overrides" / "footprint_overrides.csv"
OVERRIDE_COLUMNS = ["id", "footprint_ids", "coordinate", "note", "source", "verified_on"]

SAME_SIDE_ONLY = True
TYPE_CONFLICT_REJECTS = True
POST_VINTAGE_UNMATCHED = True

M = schema.FT_PER_M   # feet per meter: every distance below is measured in EPSG:3435 feet


def name_key(name: str) -> str:
    """Street names compare without spaces. The footprint table itself writes MC CLURG and
    MCCLURG, RIVER WALK and RIVERWALK; owners write LASALLE, VANBUREN, IRVINGPARK."""
    return (name or "").replace(" ", "")


# --- 1. trusted coordinate -------------------------------------------------------------------------
def admitted_years(summary: dict) -> list[int]:
    """Years whose coordinates may be candidates, display years first, then newest first."""
    ok = []
    for y, s in summary["years"].items():
        y = int(y)
        rate = s["coordinates"]["pass_rate"]
        if y in schema.EXCLUDED_YEARS or rate is None or rate < schema.MIN_CA_PASS_RATE:
            continue
        ok.append(y)
    rest = sorted((y for y in ok if y not in schema.DISPLAY_YEARS), reverse=True)
    return [y for y in schema.DISPLAY_YEARS if y in ok] + rest


def trusted_coordinates(energy: pd.DataFrame, covered: pd.DataFrame, summary: dict):
    """-> ({id: (source, lat, lon, x_ft, y_ft)}, [rejected candidate records])."""
    years = admitted_years(summary)
    for y in years:
        assert y not in schema.EXCLUDED_YEARS
    by_year = {y: energy[energy["data_year"] == y].set_index("id") for y in years}
    cov = covered.set_index("id")
    order = [(f"row_{y}", by_year[y]) for y in years if y in schema.DISPLAY_YEARS]
    order += [("covered_list", cov)]
    order += [(f"row_{y}", by_year[y]) for y in years if y not in schema.DISPLAY_YEARS]
    trusted, rejected = {}, []
    ids = sorted(set(energy.loc[~energy["data_year"].isin(schema.EXCLUDED_YEARS), "id"]))
    for i in ids:
        for source, table in order:
            if i not in table.index:
                continue
            r = table.loc[i]
            if pd.isna(r["latitude"]) or pd.isna(r["longitude"]):
                continue
            if bool(r["coord_in_ca_buffered"]):
                trusted[i] = (source, float(r["latitude"]), float(r["longitude"]),
                              float(r["x_ft"]), float(r["y_ft"]))
                break
            rejected.append({
                "id": i, "source": source, "latitude": float(r["latitude"]),
                "longitude": float(r["longitude"]),
                "community_area": None if pd.isna(r["community_area"]) else r["community_area"],
                "reason": "no community area stated" if pd.isna(r["ca_num"])
                else "outside stated community area (100 m buffer)"})
    return trusted, rejected


# --- footprint index -----------------------------------------------------------------------------------
class Footprints:
    def __init__(self, fp: gpd.GeoDataFrame):
        dup = fp["bldg_id"].duplicated(keep=False)
        self.shared_ids = sorted(set(fp.loc[dup, "bldg_id"]))
        # A bldg_id shared by two footprints cannot be named in footprint_ids, so neither is
        # a candidate. (One such id in the 2015 table: 882804.)
        fp = fp[~dup].reset_index(drop=True)
        self.fp = fp
        self.geom = fp.geometry.values
        self.bldg_id = fp["bldg_id"].to_numpy()
        self.f = fp["f_add1"].fillna(0).to_numpy(dtype="int64")
        self.t = fp["t_add1"].fillna(0).to_numpy(dtype="int64")
        self.dir = fp["pre_dir1"].to_numpy()
        self.type = fp["st_type1"].to_numpy()
        self.year = fp["year_built"].fillna(0).to_numpy(dtype="int64")
        self.stories = fp["stories"].fillna(0).to_numpy(dtype="int64")
        self.area = fp["area_sqft"].to_numpy()
        keys = fp["st_name1"].map(name_key).to_numpy()
        self.by_key: dict[str, np.ndarray] = {}
        addressed = np.flatnonzero((keys != "") & (self.f > 0))
        for k, pos in pd.Series(addressed).groupby(keys[addressed]):
            self.by_key[k] = pos.to_numpy()
        self.types_of = {}
        for k, d, t in zip(keys[addressed], self.dir[addressed], self.type[addressed]):
            if t:
                self.types_of.setdefault((k, d), set()).add(t)
        self.tree = shapely.STRtree(self.geom)
        self.pos_of = {int(b): p for p, b in enumerate(self.bldg_id)}

    def on_street(self, key: str) -> np.ndarray:
        return self.by_key.get(key, np.empty(0, dtype="int64"))

    def year_ok(self, pos: np.ndarray, bench_year) -> np.ndarray:
        if bench_year is None or pd.isna(bench_year):
            return np.ones(len(pos), dtype=bool)
        fy = self.year[pos]
        return ~((fy > 0) & (fy > int(bench_year) + schema.YEAR_GUARD_YEARS))

    def dist_ft(self, pos: np.ndarray, pt: Point) -> np.ndarray:
        return shapely.distance(self.geom[pos], pt)


def in_range(fp: Footprints, pos: np.ndarray, number: int, hi) -> np.ndarray:
    f, t = fp.f[pos], fp.t[pos]
    if hi is None or pd.isna(hi):
        ok = (f <= number) & (number <= t)
    else:
        ok = (f <= int(hi)) & (t >= number)          # rule 10: the ranges overlap
    if SAME_SIDE_ONLY:
        ok &= (f % 2) == (number % 2)
    return ok


def drop_type_conflicts(fp: Footprints, pos: np.ndarray, key: str, addr_type: str) -> np.ndarray:
    """Remove candidates on the same-named street of another type (63RD PL for 63RD ST)."""
    if not (TYPE_CONFLICT_REJECTS and addr_type) or len(pos) == 0:
        return np.ones(len(pos), dtype=bool)
    keep = np.ones(len(pos), dtype=bool)
    for j, p in enumerate(pos):
        t = fp.type[p]
        if t and t != addr_type and addr_type in fp.types_of.get((key, fp.dir[p]), ()):
            keep[j] = False
    return keep


def all_within(fp: Footprints, pos: np.ndarray, limit_ft: float) -> bool:
    g = fp.geom[pos]
    for a in range(len(g)):
        if (shapely.distance(g[a], g[a + 1:]) > limit_ft).any():
            return False
    return True


def near_coordinate(fp: Footprints, pt) -> set[int]:
    """Footprints within the medium distance of the trusted coordinate: the ones the coordinate
    tiers would accept at medium confidence. An address candidate among them is one the address
    and the coordinate agree on. Empty without a point."""
    if pt is None:
        return set()
    return {int(p) for p in fp.tree.query(pt, predicate="dwithin", distance=schema.NEAREST_MEDIUM_M * M)}


# --- tiers 2-7 for one property ------------------------------------------------------------------------
def match_one(rec: dict, fp: Footprints) -> dict:
    """rec: number, number_hi, pre_dir, st_name, st_type, parse_ok, year_built, of_buildings,
    pt (shapely Point in EPSG:3435, or None). -> footprint positions, method, confidence, note."""
    notes: list[str] = []
    pt, by = rec["pt"], rec["year_built"]
    # What the year guard turned away, kept as data so the reconciliation report can say
    # which footprint it was and how far it sits from the trusted coordinate.
    guarded: dict[int, dict] = {}
    advised: set[int] = set()

    def result(pos, method, confidence):
        pos = sorted((int(p) for p in pos), key=lambda p: int(fp.bldg_id[p]))
        rej = [guarded[k] for k in sorted(guarded)]
        return {"pos": pos, "method": method, "confidence": confidence, "note": "; ".join(notes),
                "guard_advisory_ids": sorted(advised),
                "guard_rejected_ids": [r["bldg_id"] for r in rej],
                "guard_rejected_years": [r["year"] for r in rej],
                "guard_rejected_tiers": [r["tier"] for r in rej],
                "guard_rejected_min_dist_m": min((r["dist_m"] for r in rej if r["dist_m"] is not None),
                                                 default=None)}

    def guard(pos, tier, agreed=None):
        ok = fp.year_ok(pos, by)
        for j in np.flatnonzero(~ok):
            p = pos[j]
            b = int(fp.bldg_id[p])
            if agreed is not None and int(p) in agreed:
                # Rule 9's exception: the address and the trusted coordinate agree on this
                # footprint, so the year is what the sources disagree about, not the building.
                ok[j] = True
                if b not in advised:
                    advised.add(b)
                    notes.append(f"year guard advisory: {tier} candidate {b} built {int(fp.year[p])}, "
                                 f"property built {int(by)}; kept because the address and the trusted "
                                 "coordinate agree on it (within 30 m)")
                continue
            if b not in guarded:
                d = None if pt is None else round(float(fp.geom[p].distance(pt)) / M, 1)
                guarded[b] = {"bldg_id": b, "year": int(fp.year[p]), "tier": tier, "dist_m": d}
                notes.append(f"year guard: {tier} candidate {b} built {int(fp.year[p])}, "
                             f"property built {int(by)}")
        return pos[ok]

    if POST_VINTAGE_UNMATCHED and by is not None and not pd.isna(by) \
            and int(by) > schema.FOOTPRINT_VINTAGE_YEAR:
        notes.append(f"built {int(by)}, after the City's {schema.FOOTPRINT_VINTAGE_YEAR} "
                     "footprint layer: no footprint in it can be this building")
        return result([], "none", "none")

    same = other = np.empty(0, dtype="int64")
    street = np.empty(0, dtype="int64")
    agreed = near_coordinate(fp, pt)
    if rec["parse_ok"]:
        key = name_key(rec["st_name"])
        street = fp.on_street(key)
        if len(street) == 0:
            notes.append(f"street {rec['st_name']!r} is not in the footprint table")
        cand = street[in_range(fp, street, rec["number"], rec["number_hi"])]
        keep = drop_type_conflicts(fp, cand, key, rec["st_type"])
        if (~keep).any():
            notes.append(f"{int((~keep).sum())} in-range footprint(s) on the same-named street "
                         f"of another type ignored")
        cand = guard(cand[keep], "address", agreed)
        is_same = fp.dir[cand] == rec["pre_dir"]
        same, other = cand[is_same], cand[~is_same]
    else:
        notes.append("address did not parse")

    # 2. T1 / T1b
    if len(same) == 1:
        return result(same, "T1", "high")
    if len(same) > 1:
        if rec["st_type"]:
            typed = same[fp.type[same] == rec["st_type"]]
            if len(typed) == 1:
                return result(typed, "T1b", "high")
            if len(typed) > 1:
                same = typed
        # 3. T1_multi
        n_b = rec["of_buildings"]
        if (n_b is not None and not pd.isna(n_b) and int(n_b) > 1) \
                or all_within(fp, same, schema.CLUSTER_M * M):
            return result(same, "T1_multi", "high")
        if pt is not None:
            d = fp.dist_ft(same, pt)
            best = same[np.lexsort((fp.bldg_id[same], d))[0]]
            notes.append(f"{len(same)} in-range footprints more than {schema.CLUSTER_M:.0f} m "
                         "apart; nearest the trusted coordinate kept")
            return result([best], "T1_multi", "medium")
        notes.append(f"{len(same)} in-range footprints more than {schema.CLUSTER_M:.0f} m apart, "
                     "no trusted coordinate to choose by; all attached")
        return result(same, "T1_multi", "low")

    # 4. T2
    if len(other) and pt is not None:
        d = fp.dist_ft(other, pt)
        j = np.lexsort((fp.bldg_id[other], d))[0]
        best, dist = other[j], float(d[j])
        if dist <= schema.AGREE_M * M:
            notes.append(f"direction {fp.dir[best] or 'blank'} vs {rec['pre_dir'] or 'blank'}; "
                         f"{dist / M:.0f} m from the trusted coordinate")
            return result([best], "T2", "medium")
        notes.append(f"T2 candidate {int(fp.bldg_id[best])} (direction {fp.dir[best] or 'blank'} vs "
                     f"{rec['pre_dir'] or 'blank'}) is {dist / M:.0f} m from the trusted coordinate; not matched")
    elif len(other) == 1:
        notes.append(f"direction {fp.dir[other[0]] or 'blank'} vs {rec['pre_dir'] or 'blank'}; "
                     "no trusted coordinate")
        return result(other, "T2", "low")
    elif len(other) > 1:
        notes.append(f"T2: {len(other)} candidates under another direction and no trusted "
                     "coordinate to choose by; not matched on them")

    if pt is not None:
        # 5. coord_pip
        hits = fp.tree.query(pt, predicate="intersects")
        hits = guard(np.sort(hits), "coord_pip")
        if len(hits):
            best = hits[np.lexsort((fp.bldg_id[hits], -fp.area[hits]))[0]]
            return result([best], "coord_pip", "medium")
        # 6. coord_nearest
        near = fp.tree.query(pt, predicate="dwithin", distance=schema.NEAREST_MEDIUM_M * M)
        near = guard(np.sort(near), "coord_nearest")
        if len(near):
            d = fp.dist_ft(near, pt)
            j = np.lexsort((fp.bldg_id[near], d))[0]
            notes.append(f"nearest footprint {float(d[j]) / M:.0f} m from the trusted coordinate")
            return result([near[j]], "coord_nearest", "medium")
        notes.append(f"no footprint within {schema.NEAREST_MEDIUM_M:.0f} m of the trusted coordinate")
    else:
        notes.append("no trusted coordinate")

    return result([], "none", "none")


# --- 11. size check ------------------------------------------------------------------------------------
DOWNGRADE = {"high": "medium", "medium": "low", "low": "low", "none": "none"}


def implied_floors(fp: Footprints, pos: list, gfa, n_buildings):
    """Gross floor area over attached footprint area. A campus that reports more buildings than
    are attached has its floor area scaled by attached / reported first, so one correctly
    matched building of ten is not read as a tower. None without footprints or floor area."""
    if not pos or gfa is None or pd.isna(gfa):
        return None
    area = float(fp.area[pos].sum())
    if area <= 0:
        return None
    share = 1.0
    if n_buildings is not None and not pd.isna(n_buildings) and int(n_buildings) > len(pos):
        share = len(pos) / int(n_buildings)
    return float(gfa) * share / area


def size_verdict(fp: Footprints, pos: list, floors) -> str:
    if floors is None:
        return ""
    if floors > schema.SIZE_FAIL_FLOORS:
        return "fail"
    tall = int(fp.stories[pos].max())
    if tall >= schema.SIZE_TOWER_STORIES:
        return "pass"
    if floors > schema.SIZE_DOUBT_FLOORS or (tall > 0 and floors > schema.SIZE_STORY_FACTOR * tall):
        return "doubtful"
    return "pass"


def size_check(match: dict, fp: Footprints, gfa, n_buildings) -> dict:
    """-> match with size_check (pass / doubtful / fail / ''), implied_floors, and the rule
    applied: fail rejects the footprint, doubtful downgrades one step."""
    pos = match["pos"]
    floors = implied_floors(fp, pos, gfa, n_buildings)
    out = {**match, "implied_floors": None if floors is None else round(floors, 1),
           "size_check": "", "size_rejected_ids": []}
    verdict = size_verdict(fp, pos, floors)
    if not verdict:
        return out
    tall = int(fp.stories[pos].max())
    ids = [int(fp.bldg_id[p]) for p in pos]
    area = float(fp.area[pos].sum())
    if verdict == "fail":
        note = (f"size check: rejected {match['method']}/{match['confidence']} footprint(s) "
                f"{', '.join(map(str, ids))} ({area:,.0f} sq ft for {float(gfa):,.0f} sq ft of floor area, "
                f"{floors:,.0f} floors)")
        return {**out, "size_check": "fail", "size_rejected_ids": ids, "pos": [], "method": "none",
                "confidence": "none", "note": "; ".join(x for x in [match["note"], note] if x)}
    if verdict == "doubtful":
        note = (f"size check: {floors:,.1f} floors on {area:,.0f} sq ft"
                + (f", the City's layer gives {tall} stories" if tall else ", no story count in the City's layer")
                + f"; downgraded from {match['confidence']}")
        return {**out, "size_check": "doubtful", "confidence": DOWNGRADE[match["confidence"]],
                "note": "; ".join(x for x in [match["note"], note] if x)}
    return {**out, "size_check": "pass"}


# --- 8. overrides -----------------------------------------------------------------------------------------
def load_overrides(path: Path = OVERRIDES) -> dict:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != OVERRIDE_COLUMNS:
            raise SystemExit(f"{path.name}: header must be {','.join(OVERRIDE_COLUMNS)}")
        out = {}
        for row in reader:
            i = int(row["id"])
            if i in out:
                raise SystemExit(f"{path.name}: id {i} appears twice")
            for c in ("note", "source", "verified_on"):
                if not row[c].strip():
                    raise SystemExit(f"{path.name}: id {i} has no {c} - every override is sourced")
            coord = row["coordinate"].strip()
            if coord and coord != "covered_list":
                if not coord.startswith("row_") or not coord[4:].isdigit():
                    raise SystemExit(f"{path.name}: id {i}: coordinate must be empty, covered_list or row_<year>")
                if int(coord[4:]) in schema.EXCLUDED_YEARS:
                    raise SystemExit(f"{path.name}: id {i}: {coord} is an excluded year; its coordinates are never used")
            ids = [int(x) for x in row["footprint_ids"].replace(";", " ").split()]
            out[i] = {"footprint_ids": ids, "coordinate": coord or None, "note": row["note"].strip(),
                      "source": row["source"].strip(), "verified_on": row["verified_on"].strip()}
    return out


def override_coordinate(ov: dict, prop_id: int, energy: pd.DataFrame, covered: pd.DataFrame):
    """The City-published coordinate an override names -> (lat, lon, x_ft, y_ft)."""
    if ov["coordinate"] == "covered_list":
        rows = covered[covered["id"] == prop_id]
    else:
        y = int(ov["coordinate"][4:])
        rows = energy[(energy["id"] == prop_id) & (energy["data_year"] == y)]
    if len(rows) != 1 or rows[["latitude", "longitude"]].isna().any(axis=None):
        raise SystemExit(f"override for id {prop_id}: {ov['coordinate']} has no coordinate to verify")
    r = rows.iloc[0]
    return float(r["latitude"]), float(r["longitude"]), float(r["x_ft"]), float(r["y_ft"])


def apply_override(match: dict, ov: dict, fp: Footprints, prop_id: int) -> dict:
    missing = [b for b in ov["footprint_ids"] if b not in fp.pos_of]
    if missing:
        raise SystemExit(f"override for id {prop_id}: bldg_id {missing} is not an ACTIVE footprint "
                         "with an id of its own")
    was = f"tiers gave {match['method']}/{match['confidence']}"
    where = f"; coordinate: {ov['coordinate']}, accepted on review" if ov["coordinate"] else ""
    src = f"[{ov['source']}, {ov['verified_on']}]"
    if not ov["footprint_ids"]:
        if ov["coordinate"]:
            note = f"override (no footprint, drawn at the City coordinate): {ov['note']} {src}{where}; {was}"
        else:
            note = f"override (veto, not drawn or located): {ov['note']} {src}; {was}"
        return {**match, "pos": [], "method": "override", "confidence": "none", "note": note,
                "veto": not ov["coordinate"]}
    note = f"override: {ov['note']} {src}{where}; {was}"
    pos = sorted((fp.pos_of[b] for b in ov["footprint_ids"]), key=lambda p: int(fp.bldg_id[p]))
    return {**match, "pos": pos, "method": "override", "confidence": "high", "note": note, "veto": False}


# --- assembly -------------------------------------------------------------------------------------------------
def address_norm(r) -> str:
    if not r["addr_parse_ok"]:
        return ""
    num = str(int(r["addr_number"]))
    if not pd.isna(r["addr_number_hi"]):
        num += f"-{int(r['addr_number_hi'])}"
    return " ".join(x for x in [num, r["addr_pre_dir"], r["addr_st_name"], r["addr_st_type"]] if x)


def representative_rows(energy: pd.DataFrame) -> pd.DataFrame:
    """One row per id from the admitted years: the display-year row where there is one,
    otherwise the newest. `_latest` columns come from the newest admitted year regardless."""
    adm = energy[~energy["data_year"].isin(schema.EXCLUDED_YEARS)].copy()
    adm["_rank"] = np.where(adm["data_year"].isin(schema.DISPLAY_YEARS), 1, 0)
    rep = adm.sort_values(["id", "_rank", "data_year"]).groupby("id").tail(1).set_index("id")
    newest = adm.sort_values(["id", "data_year"]).groupby("id").tail(1).set_index("id")
    rep["primary_property_type_latest"] = newest["primary_property_type"]
    rep["of_buildings_latest"] = newest["of_buildings"]
    # An address that does not parse falls back to the same id's newest admitted address
    # that does. Same property, same publisher; nothing is inferred.
    parsed = adm[adm["addr_parse_ok"]].sort_values(["id", "data_year"]).groupby("id").tail(1).set_index("id")
    rep["_addr_year"] = rep["data_year"]
    addr_cols = ["address", "addr_number", "addr_number_hi", "addr_pre_dir", "addr_st_name",
                 "addr_st_type", "addr_unit", "addr_extra", "addr_parse_ok"]
    for i in rep.index[~rep["addr_parse_ok"]]:
        if i in parsed.index:
            rep.loc[i, addr_cols] = parsed.loc[i, addr_cols]
            rep.loc[i, "_addr_year"] = parsed.loc[i, "data_year"]
    return rep.sort_index()


def record(r, of_buildings, pt) -> dict:
    """The fields match_one reads, from a benchmarking row."""
    return {"number": None if pd.isna(r["addr_number"]) else int(r["addr_number"]),
            "number_hi": None if pd.isna(r["addr_number_hi"]) else int(r["addr_number_hi"]),
            "pre_dir": r["addr_pre_dir"], "st_name": r["addr_st_name"],
            "st_type": r["addr_st_type"], "parse_ok": bool(r["addr_parse_ok"]),
            "year_built": None if pd.isna(r["year_built"]) else int(r["year_built"]),
            "of_buildings": None if pd.isna(of_buildings) else int(of_buildings), "pt": pt}


def build(energy, covered, footprints, summary, overrides) -> tuple[pd.DataFrame, list]:
    fp = Footprints(footprints)
    trusted, rejected = trusted_coordinates(energy, covered, summary)
    rep = representative_rows(energy)
    unknown = sorted(set(overrides) - set(rep.index))
    if unknown:
        raise SystemExit(f"footprint_overrides.csv names ids not in any admitted year: {unknown}")
    rows = []
    for i, r in rep.iterrows():
        src, lat, lon, x, y = trusted.get(i, ("none", None, None, None, None))
        m = match_one(record(r, r["of_buildings_latest"], None if x is None else Point(x, y)), fp)
        if r["_addr_year"] != r["data_year"]:
            m["note"] = "; ".join(x for x in [f"address taken from data year {int(r['_addr_year'])}", m["note"]] if x)
        m = size_check(m, fp, r["gross_floor_area_buildings_sq_ft"], r["of_buildings"])
        if i in overrides:
            ov = overrides[i]
            m = apply_override(m, ov, fp, i)
            if ov["coordinate"]:
                src, (lat, lon, x, y) = "override", override_coordinate(ov, i, energy, covered)
            elif m["veto"]:
                src, lat, lon, x, y = "none", None, None, None, None
            # Reported, not acted on: an override is a hand check and outranks the size rule.
            floors = implied_floors(fp, m["pos"], r["gross_floor_area_buildings_sq_ft"], r["of_buildings"])
            m["implied_floors"] = None if floors is None else round(floors, 1)
            m["size_check"], m["size_rejected_ids"] = size_verdict(fp, m["pos"], floors), []
        pos = m["pos"]
        years = fp.year[pos][fp.year[pos] > 0] if pos else np.empty(0)
        rows.append({
            "id": int(i), "address_raw": r["address"], "address_norm": address_norm(r),
            "community_area": r["community_area"],
            "primary_property_type_latest": r["primary_property_type_latest"],
            "year_built": r["year_built"], "of_buildings_latest": r["of_buildings_latest"],
            "trusted_lat": lat, "trusted_lon": lon, "coord_source": src,
            "trusted_x_ft": x, "trusted_y_ft": y,
            "footprint_ids": [int(fp.bldg_id[p]) for p in pos], "n_footprints": len(pos),
            "footprint_year_built": int(years.max()) if len(years) else None,
            "footprint_area_sqft": round(float(fp.area[pos].sum()), 1) if pos else None,
            "match_method": m["method"], "match_confidence": m["confidence"],
            "match_note": m["note"],
            "implied_floors": m["implied_floors"], "size_check": m["size_check"],
            "size_rejected_ids": m["size_rejected_ids"],
            "guard_advisory_ids": m["guard_advisory_ids"],
            "guard_rejected_ids": m["guard_rejected_ids"],
            "guard_rejected_years": m["guard_rejected_years"],
            "guard_rejected_tiers": m["guard_rejected_tiers"],
            "guard_rejected_min_dist_m": m["guard_rejected_min_dist_m"],
        })
    out = pd.DataFrame(rows)
    for c in ("year_built", "of_buildings_latest", "footprint_year_built"):
        out[c] = out[c].astype("Int64")
    assert set(out["match_method"]) <= set(schema.MATCH_METHODS)
    assert set(out["match_confidence"]) <= set(schema.MATCH_CONFIDENCES)
    return out, rejected


def displaced(b: pd.DataFrame) -> pd.Series:
    """Matches the year guard may have moved to a neighbor: it turned away a footprint within
    the medium distance of the trusted coordinate, and a coordinate tier then attached another
    one at high or medium. The 2026-09-18 release counted 224 of these among 2022 submitters."""
    near = b["guard_rejected_min_dist_m"].notna() & (b["guard_rejected_min_dist_m"] <= schema.NEAREST_MEDIUM_M)
    return (near & b["match_method"].isin(["coord_pip", "coord_nearest"])
            & b["match_confidence"].isin(["high", "medium"]))


def tabulate(b: pd.DataFrame) -> dict:
    t = Counter(zip(b["match_method"], b["match_confidence"]))
    table = {m: {c: t[(m, c)] for c in schema.MATCH_CONFIDENCES if t[(m, c)]}
             for m in schema.MATCH_METHODS if any(t[(m, c)] for c in schema.MATCH_CONFIDENCES)}
    conf = Counter(b["match_confidence"])
    hm = conf["high"] + conf["medium"]
    none = b[b["match_method"].isin(["none"]) | (b["n_footprints"] == 0)]
    disp = int(displaced(b).sum())
    return {"properties": int(len(b)), "by_method": table,
            "by_confidence": {c: conf[c] for c in schema.MATCH_CONFIDENCES},
            "high_or_medium": hm,
            "high_or_medium_share": round(hm / len(b), 4) if len(b) else None,
            "displaced": disp,
            "high_or_medium_share_without_displaced": round((hm - disp) / len(b), 4) if len(b) else None,
            "size_check": {k: int(v) for k, v in sorted(Counter(b["size_check"]).items()) if k},
            "no_footprint_with_trusted_coordinate": int((none["coord_source"] != "none").sum()),
            "no_footprint_no_coordinate": int((none["coord_source"] == "none").sum()),
            "built_after_footprint_layer": int((b["year_built"] > schema.FOOTPRINT_VINTAGE_YEAR).sum())}


def match_summary(buildings: pd.DataFrame, energy: pd.DataFrame, fp_shared: list) -> dict:
    out = {"ids": int(len(buildings)),
           "coord_source": {k: int(v) for k, v in sorted(Counter(buildings["coord_source"]).items())},
           "bldg_ids_shared_by_two_footprints": fp_shared,
           "all_ids": tabulate(buildings), "by_year": {}}
    b = buildings.set_index("id")
    for y in sorted(set(energy["data_year"]) - set(schema.EXCLUDED_YEARS)):
        e = energy[energy["data_year"] == y]
        out["by_year"][str(y)] = {
            "all": tabulate(b.loc[e["id"]].reset_index()),
            "submitted": tabulate(b.loc[e.loc[e["status"] == "submitted", "id"]].reset_index())}
    return out


def excluded_year_location_test(energy: pd.DataFrame, buildings: pd.DataFrame,
                                footprints: gpd.GeoDataFrame) -> dict:
    """PIPELINE "Adding a new data year" step 4, run the way this pipeline actually locates a
    property: an id already in `buildings` keeps the location it has there (README §5.1: a new
    year only needs matching for ids not already matched); any other id is matched on that
    year's own address with no coordinate, since the year's coordinates are what failed. A
    property counts as located at high or medium confidence. Also reports address alone, for
    every id, which is the literal reading of step 4(a)."""
    fp = Footprints(footprints)
    b = buildings.set_index("id")
    out = {}
    for y in sorted(schema.EXCLUDED_YEARS):
        rows = energy[(energy["data_year"] == y) & (energy["status"] == "submitted")]
        conf_id, conf_addr, by_id = [], [], 0
        for _, r in rows.iterrows():
            addr = match_one(record(r, r["of_buildings"], None), fp)
            addr = size_check(addr, fp, r["gross_floor_area_buildings_sq_ft"], r["of_buildings"])
            conf_addr.append(addr["confidence"])
            if r["id"] in b.index:
                by_id += 1
                conf_id.append(b.loc[r["id"], "match_confidence"])
            else:
                conf_id.append(addr["confidence"])
        hm = sum(c in ("high", "medium") for c in conf_id)
        hm_a = sum(c in ("high", "medium") for c in conf_addr)
        out[str(y)] = {"submitted": int(len(rows)), "located_by_id": by_id,
                       "matched_on_own_address": int(len(rows)) - by_id,
                       "high_or_medium": hm, "high_or_medium_share": round(hm / len(rows), 4) if len(rows) else None,
                       "address_alone_high_or_medium": hm_a,
                       "address_alone_share": round(hm_a / len(rows), 4) if len(rows) else None}
    return out


def print_summary(s: dict, year: int) -> None:
    for label in ("all", "submitted"):
        t = s["by_year"][str(year)][label]
        print(f"\n{year} {label}: {t['properties']:,} properties")
        print(f"  {'method':<14}" + "".join(f"{c:>8}" for c in schema.MATCH_CONFIDENCES) + f"{'total':>8}")
        for m, row in t["by_method"].items():
            print(f"  {m:<14}" + "".join(f"{row.get(c, 0):>8,}" for c in schema.MATCH_CONFIDENCES)
                  + f"{sum(row.values()):>8,}")
        print(f"  {'':<14}" + "".join(f"{t['by_confidence'][c]:>8,}" for c in schema.MATCH_CONFIDENCES)
              + f"{t['properties']:>8,}")
        print(f"  high or medium: {t['high_or_medium']:,} ({t['high_or_medium_share']:.1%}; "
              f"{t['high_or_medium_share_without_displaced']:.1%} without {t['displaced']:,} displaced); "
              f"built after {schema.FOOTPRINT_VINTAGE_YEAR}: {t['built_after_footprint_layer']:,}; "
              f"no footprint but a trusted coordinate: {t['no_footprint_with_trusted_coordinate']:,}; "
              f"neither: {t['no_footprint_no_coordinate']:,}")


def main() -> None:
    energy = pd.read_parquet(INTERIM / "energy_long.parquet")
    covered = pd.read_parquet(INTERIM / "covered.parquet")
    footprints = gpd.read_parquet(INTERIM / "footprints.parquet")
    summary = json.loads((INTERIM / "normalize_summary.json").read_text())
    overrides = load_overrides()
    buildings, rejected = build(energy, covered, footprints, summary, overrides)
    buildings.to_parquet(INTERIM / "buildings.parquet", index=False)
    pd.DataFrame(rejected, columns=["id", "source", "latitude", "longitude", "community_area",
                                    "reason"]).to_parquet(INTERIM / "coord_rejections.parquet", index=False)
    dup = footprints.loc[footprints["bldg_id"].duplicated(keep=False), "bldg_id"]
    s = match_summary(buildings, energy, sorted(set(int(x) for x in dup)))
    s["overrides_applied"] = len(overrides)
    s["coordinate_years_admitted"] = admitted_years(summary)
    s["excluded_year_location_test"] = excluded_year_location_test(energy, buildings, footprints)
    (INTERIM / "match_summary.json").write_text(json.dumps(s, indent=1))
    print(f"{len(buildings):,} ids; coordinate years admitted: {s['coordinate_years_admitted']}; "
          f"overrides: {len(overrides)}")
    print("coord_source:", s["coord_source"])
    for y in schema.DISPLAY_YEARS:
        print_summary(s, y)
    for y, t in s["excluded_year_location_test"].items():
        print(f"\nexcluded year {y}, located by id then address: {t['high_or_medium']:,} of {t['submitted']:,} "
              f"submitted at high or medium ({t['high_or_medium_share']:.1%}); {t['located_by_id']:,} by id. "
              f"Address alone: {t['address_alone_share']:.1%}")


if __name__ == "__main__":
    main()
