#!/usr/bin/env python3
"""Snapshot every upstream source into data/raw/<YYYY-MM-DD>/ with a SHA-256 manifest.

Raw snapshots are immutable, dated inputs (repo rule). Rerun on a new date to pull a
newer benchmarking release; nothing here overwrites an earlier date.

Sources (all Socrata; no API key needed at these volumes, but set SOCRATA_APP_TOKEN
to avoid throttling):
  benchmarking   data.cityofchicago.org  xq83-jr8c   all data years, ~30k rows
  covered        data.cityofchicago.org  g5i5-yz37   covered-buildings list (IDs + addresses)
  community      data.cityofchicago.org  igwz-8jzy   77 community areas, GeoJSON
  (census tracts are not pulled here: the portal's tract layers are map views without a
   row API; take 2020 TIGER tracts from the Census Bureau if a tract aggregate is wanted)
  footprints     data.cityofchicago.org  syp8-uezg   820,606 building footprints incl. geometry;
                 --footprints only (large, ~1 GB CSV). Vintage: rows last updated 2015-08-06.

Usage:
  python scripts/fetch_sources.py                # small sources
  python scripts/fetch_sources.py --footprints   # plus the full footprint table
"""
import argparse, csv, datetime as dt, hashlib, io, json, os, sys, time, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TODAY = dt.date.today().isoformat()
RAW = ROOT / "data" / "raw" / TODAY
CHI = "https://data.cityofchicago.org"
PAGE = 50000
TOKEN = os.environ.get("SOCRATA_APP_TOKEN")


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"X-App-Token": TOKEN} if TOKEN else {})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError("unreachable")


def paged_json(domain: str, dataset: str, order: str, where: str = "") -> list:
    rows, offset = [], 0
    while True:
        q = {"$limit": PAGE, "$offset": offset, "$order": order}
        if where:
            q["$where"] = where
        batch = json.loads(get(f"{domain}/resource/{dataset}.json?{urllib.parse.urlencode(q)}"))
        rows.extend(batch)
        print(f"  {dataset}: {len(rows)} rows", file=sys.stderr)
        if len(batch) < PAGE:
            return rows
        offset += PAGE


def write(path: Path, data: bytes, manifest: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    manifest[path.name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    print(f"wrote {path.relative_to(ROOT)} ({len(data):,} bytes)", file=sys.stderr)


def metadata(domain: str, dataset: str) -> dict:
    m = json.loads(get(f"{domain}/api/views/{dataset}.json"))
    return {"name": m.get("name"), "rowsUpdatedAt": m.get("rowsUpdatedAt"),
            "rowsUpdatedAt_iso": dt.datetime.fromtimestamp(m["rowsUpdatedAt"], dt.timezone.utc).isoformat()
            if m.get("rowsUpdatedAt") else None,
            "columns": [c["fieldName"] for c in m.get("columns", [])]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--footprints", action="store_true", help="also pull the full footprint table")
    args = ap.parse_args()
    manifest, meta = {}, {"fetched_on": TODAY, "sources": {}}

    for key, ds, order in [("benchmarking", "xq83-jr8c", "row_id"), ("covered", "g5i5-yz37", ":id")]:
        meta["sources"][key] = {"dataset": ds, **metadata(CHI, ds)}
        rows = paged_json(CHI, ds, order)
        write(RAW / f"{key}_{ds}.json", json.dumps(rows, indent=None).encode(), manifest)

    for key, ds in [("community_areas", "igwz-8jzy")]:
        meta["sources"][key] = {"dataset": ds, **metadata(CHI, ds)}
        write(RAW / f"{key}_{ds}.geojson", get(f"{CHI}/resource/{ds}.geojson?$limit=50000"), manifest)

    if args.footprints:
        ds = "syp8-uezg"
        meta["sources"]["footprints"] = {"dataset": ds, **metadata(CHI, ds)}
        # CSV export streams the whole table with the_geom as WKT; ~1 GB. Kept out of git
        # (see .gitignore); the manifest hash is what makes the snapshot reproducible.
        write(RAW / f"footprints_{ds}.csv", get(f"{CHI}/api/views/{ds}/rows.csv?accessType=DOWNLOAD"), manifest)

    write(RAW / "MANIFEST.json", json.dumps({"meta": meta, "files": manifest}, indent=2).encode(), {})


if __name__ == "__main__":
    main()
