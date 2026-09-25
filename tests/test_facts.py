"""facts.json (scripts/facts.py), checked on the file as written.

Shape: every fact has a numeric value,
the display string the page prints, a label, a one-sentence definition, a released source file
and its sources; display equals the value under the stated rounding; percents sit in [0, 100];
nothing carries a metric unit. The sums the page relies on hold, and the map manifest's counts
equal the facts. The determinism test in test_release.py covers facts.json's bytes.
"""
import json
import re
from decimal import ROUND_HALF_UP, Decimal

import pytest

import schema
from conftest import PROCESSED, SITE, need, read_csv

REQUIRED_TOP = ["schema", "project", "release", "generated_by", "sources", "facts", "invariants"]
REQUIRED_FACT = ["value", "display", "label", "definition", "source_file", "sources", "rounding"]
METRIC = re.compile(r"\b(km|kilomet\w*|met(er|re)s?)\b|\d\s?m\b", re.I)
NUMERAL = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
# Displays written as words, and the value each states under its rounding.
WORDS = {"quarter-mile": Decimal("0.25")}


@pytest.fixture(scope="module")
def doc():
    return json.loads(need(PROCESSED / "facts.json").read_text())


def rounded(value, rounding):
    v = Decimal(str(value))
    if rounding is None:
        return v
    if rounding == "nearest quarter mile":
        return (v * 4).quantize(Decimal(1), rounding=ROUND_HALF_UP) / 4
    if rounding == "nearest 0.1 billion":
        return (v / 1_000_000_000).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    m = re.fullmatch(r"nearest ([\d,]+)", rounding)
    if m:
        mult = int(m.group(1).replace(",", ""))
        return (v / mult).quantize(Decimal(1), rounding=ROUND_HALF_UP) * mult
    places = {"nearest percent": 0, "nearest foot": 0, "nearest tenth": 1,
              "nearest tenth of a percent": 1, "nearest hundredth of a percent": 2}
    assert rounding in places, rounding
    q = Decimal(1).scaleb(-places[rounding]) if places[rounding] else Decimal(1)
    return v.quantize(q, rounding=ROUND_HALF_UP)


def test_top_level(doc):
    for k in REQUIRED_TOP:
        assert k in doc, k
    assert doc["schema"] == 1 and doc["project"] == "chicago-building-energy"
    assert doc["release"] == schema.RELEASE_DATE and re.fullmatch(r"\d{4}-\d{2}-\d{2}", doc["release"])
    ids = [s["id"] for s in doc["sources"]]
    assert len(ids) == len(set(ids)) == 4
    for s in doc["sources"]:
        assert s["url"].startswith("https://") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", s["pulled"]), s


def test_every_fact_is_complete(doc):
    ids = {s["id"] for s in doc["sources"]}
    present = {p.name for p in PROCESSED.iterdir() if p.is_file()}
    for key, f in doc["facts"].items():
        assert re.fullmatch(r"[a-z][a-z0-9_]*", key), key
        for k in REQUIRED_FACT:
            assert k in f, (key, k)
        assert isinstance(f["value"], (int, float)) and not isinstance(f["value"], bool), key
        assert isinstance(f["display"], str) and f["display"], key
        assert f["label"] and f["definition"].rstrip().endswith("."), key
        assert f["source_file"] in present, (key, f["source_file"])
        assert f["sources"] and set(f["sources"]) <= ids, key
        assert not METRIC.search(f.get("unit", "")), (key, f.get("unit"))
        assert not METRIC.search(f["display"]), (key, f["display"])
        # Definitions feed the website's JSON-LD and dataset pages: no meters, no internal names.
        assert not METRIC.search(f["definition"]), (key, f["definition"])
        assert not re.search(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b", f["definition"]), (key, f["definition"])


def test_display_equals_the_rounded_value(doc):
    for key, f in doc["facts"].items():
        printed = WORDS.get(f["display"]) or Decimal(NUMERAL.search(f["display"]).group(0).replace(",", ""))
        assert printed == rounded(f["value"], f["rounding"]), (key, f["value"], f["display"], f["rounding"])


def test_percents_and_counts(doc):
    for key, f in doc["facts"].items():
        if f.get("unit") == "percent":
            assert 0 <= f["value"] <= 100, key
        elif f["rounding"] is None:
            assert float(f["value"]).is_integer(), (key, f["value"])


def test_invariants(doc):
    F = doc["facts"]
    for inv in doc["invariants"]:
        assert sum(F[k]["value"] for k in inv["sum"]) == F[inv["equals"]]["value"], inv
    assert F["hex_one_property"]["value"] <= F["hex_with_value"]["value"]
    assert F["matched_high_medium"]["value"] <= F["reported"]["value"]
    assert F["hex_width"]["display"] == "1312-ft"
    assert F["hex_width_mi"]["display"] == "quarter-mile"
    assert F["hex_width_mi"]["value"] == round(F["hex_width"]["value"] / 5280, 4)
    assert abs(F["hex_width_mi"]["value"] - 0.25) / 0.25 < 0.01


def test_facts_recompute_from_the_tables(doc):
    F = doc["facts"]
    e = read_csv("energy.csv").merge(read_csv("buildings.csv")[["id", "located_by", "match_confidence"]], on="id")
    e = e[e["data_year"] == str(F["data_year"]["value"])]
    sub = e[e["status"] == "submitted"]
    assert F["covered"]["value"] == len(e) and F["reported"]["value"] == len(sub)
    assert F["not_mapped"]["value"] == int((e["located_by"] == "none").sum())
    assert F["matched_high_medium"]["value"] == int(sub["match_confidence"].isin(["high", "medium"]).sum())
    assert F["total_site_energy"]["value"] == sub.loc[sub["site_energy_kbtu"] != "", "site_energy_kbtu"].astype("int64").sum()
    assert F["gfa_inflated"]["value"] == int(((sub["site_eui_kbtu_sq_ft"] != "") & (sub["gfa_consistent"] == "false")).sum())
    assert F["precision_reviewed"]["value"] == len(read_csv("match_review.csv"))
    assert F["overrides"]["value"] == len(read_csv("footprint_overrides.csv"))


def test_manifest_counts_equal_the_facts(doc):
    F = doc["facts"]
    c = json.loads(need(SITE / "manifest.json").read_text())["counts"]
    y = F["data_year"]["value"]
    assert c[f"properties_{y}"] == F["covered"]["value"]
    assert c[f"submitted_{y}"] == F["reported"]["value"]
    assert c[f"drawn_as_footprint_{y}"] == F["drawn_as_footprints"]["value"]
    assert c[f"not_drawn_{y}"] == F["not_mapped"]["value"]
    assert c[f"match_high_or_medium_pct_{y}"] == round(F["matched_high_medium_pct"]["value"], 1)
    assert c["precision_drawn_pct"] == round(F["precision_drawn_pct"]["value"])
    assert c[f"ca_under_five_{y}"] == F["ca_under_five"]["value"]
    geo = json.loads(need(SITE / "manifest.json").read_text())["geometry"]
    assert (geo["hex_width_mi"], geo["hex_width_display"]) == (F["hex_width_mi"]["value"], F["hex_width_mi"]["display"])
