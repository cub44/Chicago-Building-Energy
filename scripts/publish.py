#!/usr/bin/env python3
"""Publish the release of record to the public repo and the website.

    python3 scripts/publish.py            # verify, then copy
    python3 scripts/publish.py --check    # verify only; copy nothing

Standard library only, so it runs without .venv. It rebuilds nothing: rebuild, run the tests
and read the build's working report of what did not resolve first. Then this script

  1. verifies every file listed in data/processed/checksums.sha256 (the build's own manifest,
     bare filenames) and stops if one is missing, altered, or unlisted;
  2. verifies the map's release the same way: every file site/checksums.sha256 lists (paths
     relative to site/: data/*, and vendor/* for d3 and topojson-client with their licenses),
     nothing unlisted in site/data/ or site/vendor/, and site/data/ against the hashes in
     site/data/manifest.json;
  3. copies the release set to <public>/data/processed/ and writes <public>/checksums.sha256
     with paths rooted at data/processed/, as the Chicago-Potholes release does;
  4. copies that same set and that same checksums.sha256 to
     <website>/projects/chicago-building-energy/, which is what the website's
     scripts/package_site.py verifies against;
  5. copies that map set and site/checksums.sha256 to <public>/site/ and to that project's
     site/ (the website verifies it as it does the data manifest; the map pages are its own);
  6. copies the code the release is made of -- scripts/, tests/ and requirements.txt -- to the
     public repo, so the matcher and the total-energy derivation can be read and rerun. It
     refuses to copy a source file that names this machine's home, this repo or its parent,
     which would leak the local layout. The raw snapshots stay private (the footprint export
     alone is about 450 MB), so the public repo documents the method; it cannot rebuild these
     bytes without them;
  7. copies the public documents kept under release/ -- METHODS.md, the method without the
     working notes, CITATION.cff, and .zenodo.json, the metadata Zenodo reads when it archives a
     GitHub release -- to the public repo's root. It refuses a document that names this
     machine's paths or a file that is not published, a CITATION.cff whose version or
     date-released is not schema.RELEASE_DATE, and a .zenodo.json whose version is not that date
     or whose title and dataset page are not CITATION.cff's.

It never deletes. If a destination folder holds a file this release does not list, it stops
and names it: the website build would refuse it anyway, and removing a published file is a
decision, not a side effect. <public> defaults to ../chicago-building-energy and <website>
to ../connorblandford-website, sibling folders of this repository.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import schema  # noqa: E402  (constants only, standard library)

ROOT = Path(__file__).resolve().parents[1]
SLUG = "chicago-building-energy"
PROCESSED = ROOT / "data" / "processed"
SITE = ROOT / "site"
SITE_DATA = SITE / "data"
CODE_DIRS = ["scripts", "tests"]          # published: the method, readable and rerunnable
CODE_FILES = ["requirements.txt"]         # published: the pins the figures were produced under
RELEASE_DOCS = ROOT / "release"
DOCS = ["METHODS.md", "CITATION.cff", ".zenodo.json"]   # published at the public repo's root, from release/
# What exists only in this working repository. A published document that cites one of these
# points its reader at something they cannot open.
UNPUBLISHED = ["PIPELINE", "MAP_SPEC", "RUBRIC", "reconciliation.md", "data/raw", "data/interim",
               "data/review", "data/overrides", "prototypes/", "correspondence/", "WEBSITE-GUIDE",
               "DESIGN-SYSTEM"]


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
        fail("data/processed/checksums.sha256 is missing; run scripts/build.py")
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
    """The map's release: the paths site/checksums.sha256 lists, relative to site/."""
    manifest = SITE / "checksums.sha256"
    if not manifest.is_file():
        fail("site/checksums.sha256 is missing; run scripts/export_site.py")
    listed = {}
    for line in manifest.read_text().splitlines():
        if line.strip():
            digest, name = line.split(maxsplit=1)
            listed[name.strip()] = digest
    for name, digest in listed.items():
        p = SITE / name
        if name.split("/")[0] not in ("data", "vendor") or not p.is_file():
            fail(f"site/{name} is listed in site/checksums.sha256 but missing")
        if sha256(p) != digest:
            fail(f"site/{name} does not match site/checksums.sha256; run scripts/export_site.py")
    for d in ("data", "vendor"):
        extra = unlisted(SITE / d, {n.split("/", 1)[1] for n in listed if n.startswith(f"{d}/")})
        if extra:
            fail(f"site/{d}/ holds files site/checksums.sha256 does not list: {', '.join(extra)}")
    files = json.loads((SITE_DATA / "manifest.json").read_text())["files"]
    for name, meta in files.items():
        if f"data/{name}" not in listed or listed[f"data/{name}"] != meta["sha256"]:
            fail(f"site/data/{name} does not match site/data/manifest.json; run scripts/export_site.py")
    return sorted(listed)


def local_paths(text: str) -> list[str]:
    """This machine's home, this repo or its parent, wherever the text names one."""
    return sorted(b for b in {str(Path.home()), str(ROOT), str(ROOT.parent)} if b in text)


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
        for bad in local_paths(p.read_text(encoding="utf-8")):
            fail(f"{rel} names this machine's own path ({bad}); it cannot be published as it stands")
    return out


def docs_set() -> list[str]:
    """The public documents under release/: none names a local path or an unpublished file, and
    CITATION.cff's version and date-released are this release's date."""
    for name in DOCS:
        p = RELEASE_DOCS / name
        if not p.is_file():
            fail(f"release/{name} is missing; it is published with the release")
        text = p.read_text(encoding="utf-8")
        for bad in local_paths(text):
            fail(f"release/{name} names this machine's own path ({bad}); it cannot be published as it stands")
        cited = [w for w in UNPUBLISHED if w in text]
        if cited:
            fail(f"release/{name} cites what is not published ({', '.join(cited)})")
    cff = (RELEASE_DOCS / "CITATION.cff").read_text(encoding="utf-8")
    for key, want in (("version", f'"{schema.RELEASE_DATE}"'), ("date-released", schema.RELEASE_DATE)):
        m = re.search(rf"^{key}: *(.*?) *$", cff, re.M)
        if not m or m.group(1) != want:
            fail(f"release/CITATION.cff gives {key} {m.group(1) if m else '(none)'}; "
                 f"the release is {schema.RELEASE_DATE} (scripts/schema.py RELEASE_DATE)")
    # .zenodo.json: Zenodo's legacy deposit metadata. The ORCID is the bare id there (CITATION.cff
    # gives the full URL); the dataset page is the record's documentation.
    try:
        z = json.loads((RELEASE_DOCS / ".zenodo.json").read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        fail(f"release/.zenodo.json is not valid JSON: {e}")
    cff_value = {k: (m.group(1) if (m := re.search(rf'^{k}: "(.*)"$', cff, re.M)) else None) for k in ("title", "url")}
    checks = [
        ("version", z.get("version") == schema.RELEASE_DATE, schema.RELEASE_DATE),
        ("upload_type", z.get("upload_type") == "dataset", "dataset"),
        ("license", z.get("license") == "cc-by-4.0", "cc-by-4.0"),
        ("access_right", z.get("access_right") == "open", "open"),
        ("title", z.get("title") == cff_value["title"], f"CITATION.cff's title {cff_value['title']!r}"),
        ("creators", bool(z.get("creators")) and all(
            re.fullmatch(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]", c.get("orcid", "")) for c in z["creators"]),
         "each creator with a bare ORCID id"),
        ("related_identifiers", {"identifier": cff_value["url"], "relation": "isDocumentedBy",
                                 "resource_type": "publication-other"} in z.get("related_identifiers", []),
         f"the dataset page {cff_value['url']} as isDocumentedBy"),
    ]
    for key, ok, want in checks:
        if not ok:
            fail(f"release/.zenodo.json {key} is {z.get(key)!r}; it must be {want}")
    return list(DOCS)


def copy_into(names: list[str], src: Path, dst: Path, keep: set[str] = frozenset()) -> None:
    stale = unlisted(dst, set(names) | set(keep))
    if stale:
        fail(f"{dst} holds files this release does not list: {', '.join(stale)}. "
             "Remove them if they are meant to go, then rerun.")
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

    release, site, code, docs = release_set(), site_set(), code_set(), docs_set()
    print(f"release: {len(release)} files verified against data/processed/checksums.sha256")
    print(f"map: {len(site)} files verified against site/checksums.sha256 and site/data/manifest.json")
    print(f"code: {len(code)} files, none naming an absolute path")
    print(f"docs: {', '.join(docs)}, none naming an absolute path or an unpublished file")
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
    for dest in (a.public, project):
        for d in ("data", "vendor"):
            copy_into([n.split("/", 1)[1] for n in site if n.startswith(f"{d}/")], SITE / d, dest / "site" / d)
        shutil.copyfile(SITE / "checksums.sha256", dest / "site" / "checksums.sha256")
        print(f"  {dest}/site/: data/ and vendor/ ({len(site)} files) + checksums.sha256")

    for d in CODE_DIRS:
        copy_into([rel.split("/", 1)[1] for _, rel in code if rel.startswith(f"{d}/")],
                  ROOT / d, a.public / d)
    for src, rel in ((s, r) for s, r in code if "/" not in r):
        shutil.copyfile(src, a.public / rel)
    print(f"  {a.public}/: {len(code)} code files ({', '.join(CODE_DIRS)}, "
          f"{', '.join(CODE_FILES)})")
    for n in docs:
        shutil.copyfile(RELEASE_DOCS / n, a.public / n)
        if sha256(a.public / n) != sha256(RELEASE_DOCS / n):
            fail(f"copy of {n} into {a.public} does not match its source")
    print(f"  {a.public}/: {', '.join(docs)} (from release/)")
    print("Done. Re-check the hand-written figures in the public README against this release and commit "
          "in both repos. Then, in the website repo, run scripts/sync_facts.py --write and "
          "scripts/sync_map_hash.py --write, and build.")


if __name__ == "__main__":
    main()
