"""Stage 2: the tier rules on planted footprints, and the rules as they came out on the real build.

The planted tests build a three-footprint street in code, so each rule is shown doing exactly
one thing. The data tests read data/interim/ and data/processed/ as the last build left them.
"""
import geopandas as gpd
import pytest
from shapely.geometry import Point, box

import match_footprints as mf
import schema
from conftest import PROCESSED, need

FT = schema.FT_PER_M


def street(**years):
    """Three 100 x 100 ft footprints on W TEST ST, 200 ft apart: 101 (x 0-100), 103 (x 300-400),
    105 (x 600-700). Keyword arguments set year_built by bldg_id, e.g. b103=2010."""
    rows = []
    for i, (num, x0) in enumerate([(101, 0), (103, 300), (105, 600)]):
        rows.append({"bldg_id": 9000 + i, "f_add1": num, "t_add1": num, "pre_dir1": "W",
                     "st_name1": "TEST", "st_type1": "ST", "year_built": years.get(f"b{num}", 1950),
                     "stories": 4, "area_sqft": 10000.0, "geometry": box(x0, 0, x0 + 100, 100)})
    g = gpd.GeoDataFrame(rows, geometry="geometry", crs=schema.CRS_PLANE)
    for c in ("f_add1", "t_add1", "year_built", "stories"):
        g[c] = g[c].astype("Int64")
    return mf.Footprints(g)


def rec(number=103, pre_dir="W", year_built=1950, pt=None, of_buildings=1):
    return {"number": number, "number_hi": None, "pre_dir": pre_dir, "st_name": "TEST", "st_type": "ST",
            "parse_ok": True, "year_built": year_built, "of_buildings": of_buildings, "pt": pt}


def ids(fp, m):
    return [int(fp.bldg_id[p]) for p in m["pos"]]


# --- Rule 9: the year guard -----------------------------------------------------------------------------
def test_the_guard_rejects_a_later_footprint_the_coordinate_does_not_confirm():
    fp = street(b103=2010)
    m = mf.match_one(rec(pt=Point(650, 50)), fp)          # coordinate on 105, not on 103
    assert 9001 not in ids(fp, m)
    assert m["guard_rejected_ids"] == [9001] and m["guard_advisory_ids"] == []


def test_the_guard_is_advisory_where_address_and_coordinate_agree():
    fp = street(b103=2010)
    m = mf.match_one(rec(pt=Point(350, 50)), fp)          # coordinate inside 103
    assert ids(fp, m) == [9001] and (m["method"], m["confidence"]) == ("T1", "high")
    assert m["guard_advisory_ids"] == [9001] and m["guard_rejected_ids"] == []
    assert "year guard advisory" in m["note"]


def test_a_coordinate_near_but_outside_counts_as_agreeing():
    fp = street(b103=2010)
    m = mf.match_one(rec(pt=Point(350, 100 + 20 * FT)), fp)   # 20 m north of 103
    assert ids(fp, m) == [9001]


def test_agreement_reaches_30_m_even_when_the_point_is_on_a_neighbor():
    """The audit's displaced matches: the point sits on the next building, the footprint that
    holds the address is a few feet off. The guard must not throw the address footprint out."""
    fp = street(b103=2010)
    m = mf.match_one(rec(pt=Point(450, 50)), fp)            # between 103 and 105, 15 m from 103
    assert ids(fp, m) == [9001] and m["guard_advisory_ids"] == [9001]


def test_agreement_does_not_reach_past_30_m():
    fp = street(b103=2010)
    m = mf.match_one(rec(pt=Point(650, 50)), fp)            # inside 105, 76 m from 103
    assert 9001 not in ids(fp, m)


def test_the_guard_still_rejects_with_no_coordinate():
    fp = street(b103=2010)
    m = mf.match_one(rec(pt=None), fp)
    assert 9001 not in ids(fp, m)


# --- Rule 4: T2 ------------------------------------------------------------------------------------------
def test_t2_is_refused_when_the_coordinate_is_elsewhere():
    """A North/South mirror: the number and street match under the other direction, and the
    trusted coordinate is miles away. Not matched; the coordinate tiers decide."""
    fp = street()
    far = Point(50, 100 + 5000 * FT)
    m = mf.match_one(rec(pre_dir="E", pt=far), fp)
    assert m["method"] != "T2"
    assert "not matched" in m["note"]


def test_t2_is_medium_when_the_coordinate_agrees():
    fp = street()
    m = mf.match_one(rec(pre_dir="E", pt=Point(350, 150)), fp)
    assert (m["method"], m["confidence"], ids(fp, m)) == ("T2", "medium", [9001])


def test_a_nearby_range_is_not_a_match():
    """T3 is retired: a range two numbers away is not the building, with or without a coordinate."""
    fp = street()
    assert "T3" not in schema.MATCH_METHODS
    for pt in (None, Point(650, 100 + 45 * FT)):             # no coordinate; one 45 m away
        m = mf.match_one(rec(number=107, pt=pt), fp)        # nearest range 105, two numbers away
        assert m["method"] == "none" and not m["pos"]


def test_coord_nearest_stops_at_30_m():
    fp = street()
    m = mf.match_one(rec(number=999, pt=Point(50, 100 + 25 * FT)), fp)     # 25 m north of 101
    assert (m["method"], m["confidence"], ids(fp, m)) == ("coord_nearest", "medium", [9000])
    m = mf.match_one(rec(number=999, pt=Point(50, 100 + 40 * FT)), fp)     # 40 m from everything
    assert m["method"] == "none"


def test_t2_is_low_with_no_coordinate():
    fp = street()
    m = mf.match_one(rec(pre_dir="E", pt=None), fp)
    assert (m["method"], m["confidence"]) == ("T2", "low")


# --- Rule 11: the size check -----------------------------------------------------------------------------
def test_implied_floors_scales_a_partly_attached_campus():
    fp = street()
    assert mf.implied_floors(fp, [1], 100000, 1) == 10.0
    assert mf.implied_floors(fp, [1], 100000, 10) == 1.0     # one of ten buildings attached
    assert mf.implied_floors(fp, [], 100000, 1) is None
    assert mf.implied_floors(fp, [1], None, 1) is None


@pytest.mark.parametrize("gfa, stories, verdict, confidence, kept", [
    (70_000, 4, "pass", "high", True),          # 7 floors on 4 stories: within twice the stories
    (90_000, 4, "doubtful", "medium", True),    # 9 floors on 4 stories: over twice
    (500_000, 30, "pass", "high", True),        # 50 floors on a 30-story tower
    (700_000, 39, "doubtful", "medium", True),  # 70 floors: over 60
    (700_000, 0, "doubtful", "medium", True),   # 70 floors, stories unknown
    (80_000, 0, "pass", "high", True),          # 8 floors, stories unknown: nothing to test
    (1_300_000, 45, "fail", "none", False),     # 130 floors: rejected even on a tower
])
def test_size_check(gfa, stories, verdict, confidence, kept):
    fp = street()
    fp.stories[1] = stories
    m = mf.size_check({"pos": [1], "method": "T1", "confidence": "high", "note": ""}, fp, gfa, 1)
    assert (m["size_check"], m["confidence"], bool(m["pos"])) == (verdict, confidence, kept)
    if not kept:
        assert m["method"] == "none" and m["size_rejected_ids"] == [9001]


def test_a_tower_in_the_city_layer_is_not_downgraded():
    fp = street()
    fp.stories[1] = 45
    m = mf.size_check({"pos": [1], "method": "T1", "confidence": "high", "note": ""}, fp, 700_000, 1)
    assert (m["size_check"], m["confidence"]) == ("pass", "high")


# --- Rule 8: overrides -----------------------------------------------------------------------------------
def test_overrides_file_is_well_formed():
    # data/overrides/ is the working copy; the release carries the same bytes under data/processed/.
    path = mf.OVERRIDES if mf.OVERRIDES.exists() else PROCESSED / "footprint_overrides.csv"
    ov = mf.load_overrides(need(path))
    for i, o in ov.items():
        assert o["note"] and o["source"] and o["verified_on"]
        assert o["coordinate"] is None or o["coordinate"] == "covered_list" or o["coordinate"].startswith("row_")


def test_overrides_win(buildings_interim):
    b = buildings_interim.set_index("id")
    for i, o in mf.load_overrides().items():
        r = b.loc[i]
        assert r["match_method"] == "override"
        assert sorted(int(x) for x in r["footprint_ids"]) == sorted(o["footprint_ids"])
        if o["coordinate"]:
            assert r["coord_source"] == "override"
        elif not o["footprint_ids"]:
            assert r["coord_source"] == "none"          # a veto places nothing


def test_an_excluded_year_cannot_be_an_override_coordinate(tmp_path):
    y = next(iter(schema.EXCLUDED_YEARS))
    p = tmp_path / "o.csv"
    p.write_text(",".join(mf.OVERRIDE_COLUMNS) + f"\n1,,row_{y},n,s,2026-01-01\n")
    with pytest.raises(SystemExit, match="excluded year"):
        mf.load_overrides(p)


# --- Rule 1: coordinates, on the build -------------------------------------------------------------------
def test_coord_source_never_names_an_excluded_year(buildings_interim):
    assert not set(buildings_interim["coord_source"]) & {f"row_{y}" for y in schema.EXCLUDED_YEARS}


def test_every_trusted_row_coordinate_passed_its_test(buildings_interim, energy_long):
    b = buildings_interim[buildings_interim["coord_source"].str.startswith("row_")]
    e = energy_long.set_index(["id", "data_year"])
    for r in b.itertuples():
        row = e.loc[(r.id, int(r.coord_source[4:]))]
        assert bool(row["coord_in_ca_buffered"])
        assert (row["latitude"], row["longitude"]) == (r.trusted_lat, r.trusted_lon)


# --- The rules as built ----------------------------------------------------------------------------------
def test_no_t2_match_disagrees_with_its_coordinate(buildings_interim, footprints):
    fp = footprints.drop_duplicates("bldg_id", keep=False).set_index("bldg_id")
    b = buildings_interim[(buildings_interim["match_method"] == "T2") & buildings_interim["trusted_x_ft"].notna()]
    for r in b.itertuples():
        d = fp.loc[[int(x) for x in r.footprint_ids]].geometry.distance(Point(r.trusted_x_ft, r.trusted_y_ft)).min()
        assert d <= schema.AGREE_M * FT, r.id


def test_no_tier_match_fails_the_size_check(buildings_interim):
    tier = buildings_interim[buildings_interim["match_method"] != "override"]
    assert not ((tier["n_footprints"] > 0) & (tier["size_check"] == "fail")).any()
    assert (tier.loc[tier["size_check"] == "fail", "n_footprints"] == 0).all()


def test_low_confidence_matches_are_located_by_their_coordinate(buildings_csv):
    b = buildings_csv
    low = b[(b["match_confidence"] == "low") & (b["trusted_lat"] != "")]
    assert (low["located_by"] == "trusted_coordinate").all()
    assert (b.loc[(b["n_footprints"] == "0") & (b["trusted_lat"] == ""), "located_by"] == "none").all()


def test_the_2022_rate_is_reported_against_the_original_target(match_summary):
    assert schema.MATCH_TARGET == 0.90
    t = match_summary["by_year"]["2022"]["submitted"]
    assert t["high_or_medium_share_without_displaced"] <= t["high_or_medium_share"]


def test_the_excluded_year_location_test_ran(match_summary):
    for y in schema.EXCLUDED_YEARS:
        t = match_summary["excluded_year_location_test"][str(y)]
        assert t["submitted"] > 0 and 0 < t["high_or_medium_share"] <= 1
