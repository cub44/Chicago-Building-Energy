#!/usr/bin/env python3
"""Publish the release of record to the public repo and the website.

    python3 scripts/publish.py            # verify, then copy
    python3 scripts/publish.py --check    # verify only; copy nothing

Standard library only, so it runs without .venv. It rebuilds nothing: run `make check` and
read data/reconciliation.md first. Then this script

  1. verifies every file listed in data/processed/checksums.sha256 (the build's own manifest,
     bare filenames) and stops if one is missing, altered, or unlisted;
  2. verifies site/data/ against the hashes in site/data/manifest.json the same way;
  3. copies the release set to <public>/data/processed/ and writes <public>/checksums.sha256
     with paths rooted at data/processed/, as POTHOLES_REPO does;
  4. copies that same set and that same checksums.sha256 to
     <website>/projects/chicago-building-energy/, which is what the website's
     scripts/package_site.py verifies against;
  5. copies site/data/ and the vendored d3 and topojson-client into that project's site/;
  6. copies the code the release is made of -- scripts/, tests/ and requirements.txt -- to the
     public repo, so the matcher and the total-energy derivation can be read and rerun. It
     refuses to copy a source file that names this machine's home, this repo or its parent,
     which would leak the local layout. The raw snapshots stay private (the footprint export alone is 1 GB), so the public
     repo documents the method; it cannot rebuild these bytes without them.

It never deletes. If a destination folder holds a file this release does not list, it stops
and names it: the website build would refuse it anyway, and removing a published file is a
decision, not a side effect. <public> defaults to ../chicago-building-energy and <website>
to ../connorblandford-website, the layout under "Personal Website".
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SLUG = "chicago-building-energy"
PROCESSED = ROOT / "data" / "processed"
SITE_DATA = ROOT / "site" / "data"
VENDOR = ROOT / "prototypes" / "vendor"
VENDOR_FILES = ["d3.min.js", "topojson-client.min.js"]
CODE_DIRS = ["scripts", "tests"]          # published: the method, readable and rerunnable
CODE_FILES = ["requirements.txt"]         # published: the pins the figures were produced under


def fail(msg: str) -> None:
    sys.exit(f"publish: {msg}")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def unlisted(folder: Path, allowed: set[str]) -> list[str]:
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.iterdir()
                  if p.is_file() and not p.name.startswith(".") and p.name not in allowed)


def release_set() -> dict[str, str]:
    manifest = PROCESSED / "checksums.sha256"
    if not manifest.is_file():
        fail("data/processed/checksums.sha256 is missing; run `make build`")
    listed = {}
    for line in manifest.read_text().splitlines():
        if line.strip():
            digest, name = line.split(maxsplit=1)
            listed[name.strip()] = digest
    for name, digest in listed.items():
        p = PROCESSED / name
        if not p.is_file():
            fail(f"data/processed/{name} is listed in its checksums.sha256 but missing")
        if sha256(p) != digest:
            fail(f"data/processed/{name} does not match data/processed/checksums.sha256; rebuild")
    extra = unlisted(PROCESSED, set(listed) | {"checksums.sha256"})
    if extra:
        fail(f"data/processed/ holds files its checksums.sha256 does not list: {', '.join(extra)}")
    return dict(sorted(listed.items()))


def site_set() -> list[str]:
    man = SITE_DATA / "manifest.json"
    if not man.is_file():
        fail("site/data/manifest.json is missing; run `make export`")
    files = json.loads(man.read_text())["files"]
    for name, meta in files.items():
        p = SITE_DATA / name
        if not p.is_file():
            fail(f"site/data/{name} is listed in manifest.json but missing")
        if sha256(p) != meta["sha256"]:
            fail(f"site/data/{name} does not match site/data/manifest.json; run `make export`")
    names = sorted(files) + ["manifest.json"]
    extra = unlisted(SITE_DATA, set(names))
    if extra:
        fail(f"site/data/ holds files manifest.json does not list: {', '.join(extra)}")
    for v in VENDOR_FILES:
        if not (VENDOR / v).is_file():
            fail(f"prototypes/vendor/{v} is missing")
    return names


def code_set() -> list[tuple[Path, str]]:
    """(source, path-relative-to-repo-root) for every code file published, newest layout first."""
    out = [(ROOT / f, f) for f in CODE_FILES]
    for d in CODE_DIRS:
        folder = ROOT / d
        if not folder.is_dir():
            fail(f"{d}/ is missing; it is part of the published release")
        out += [(p, f"{d}/{p.name}") for p in sorted(folder.iterdir())
                if p.is_file() and p.suffix == ".py"]
    for p, rel in out:
        if not p.is_file():
            fail(f"{rel} is listed for publication but missing")
        text = p.read_text(encoding="utf-8")
        for bad in {str(Path.home()), str(ROOT), str(ROOT.parent)}:
            if bad in text:
                fail(f"{rel} names this machine's own path ({bad}); it cannot be published as it stands")
    return out


def copy_into(names: list[str], src: Path, dst: Path, keep: set[str] = frozenset()) -> None:
    stale = unlisted(dst, set(names) | set(keep))
    if stale:
        fail(f"{dst} holds files this release does not list: {', '.join(stale)}. "
             "Remove them by hand if they are meant to go, then rerun.")
    dst.mkdir(parents=True, exist_ok=True)
    for n in names:
        shutil.copyfile(src / n, dst / n)
        if sha256(dst / n) != sha256(src / n):
            fail(f"copy of {n} into {dst} does not match its source")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--public", type=Path, default=ROOT.parent / SLUG)
    ap.add_argument("--website", type=Path, default=ROOT.parent / "connorblandford-website")
    ap.add_argument("--check", action="store_true", help="verify only; copy nothing")
    a = ap.parse_args()

    release, site, code = release_set(), site_set(), code_set()
    print(f"release: {len(release)} files verified against data/processed/checksums.sha256")
    print(f"map contract: {len(site)} files verified against site/data/manifest.json")
    print(f"code: {len(code)} files, none naming an absolute path")
    if a.check:
        return
    if not (a.public / "README.md").is_file():
        fail(f"{a.public} does not look like the public repo (no README.md)")
    if not (a.website / "scripts" / "package_site.py").is_file():
        fail(f"{a.website} does not look like the website repo (no scripts/package_site.py)")
    project = a.website / "projects" / SLUG
    if not (project / "index.html").is_file():
        fail(f"{project}/index.html is missing; add the project page before publishing into it")

    checksums = "".join(f"{d}  data/processed/{n}\n" for n, d in release.items())
    names = list(release)
    for dest in (a.public, project):
        copy_into(names, PROCESSED, dest / "data" / "processed")
        (dest / "checksums.sha256").write_text(checksums)
        print(f"  {dest}/: data/processed/ ({len(names)} files) + checksums.sha256")
    copy_into(site, SITE_DATA, project / "site" / "data")
    copy_into(VENDOR_FILES, VENDOR, project / "site" / "vendor")
    print(f"  {project}/site/: data/ ({len(site)} files) + vendor/ ({len(VENDOR_FILES)} files)")

    for d in CODE_DIRS:
        copy_into([rel.split("/", 1)[1] for _, rel in code if rel.startswith(f"{d}/")],
                  ROOT / d, a.public / d)
    for src, rel in ((s, r) for s, r in code if "/" not in r):
        shutil.copyfile(src, a.public / rel)
    print(f"  {a.public}/: {len(code)} code files ({', '.join(CODE_DIRS)}, "
          f"{', '.join(CODE_FILES)})")
    print("Done. Re-check the figures written into the public README and on the project page "
          "against this release, then commit in both repos. The map's ?v= hash changes only when "
          "site/index.html does.")


if __name__ == "__main__":
    main()
