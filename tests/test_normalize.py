"""Stage 1: the schema contract, the status mapping, the definition of total site energy and the
floor-area check."""
import copy
from decimal import Decimal

import pandas as pd
import pytest

import normalize
import schema


# --- Schema -----------------------------------------------------------------------------------------
def test_raw_column_lists_match_schema(raw_manifest):
    normalize.check_schema(raw_manifest)
    for key in schema.SOURCES:
        have = set(raw_manifest["meta"]["sources"][key]["columns"])
        assert have == set(schema.REQUIRED[key]) | set(schema.ACKNOWLEDGED[key]), key


def test_a_missing_required_column_fails(raw_manifest):
    m = copy.deepcopy(raw_manifest)
    m["meta"]["sources"]["benchmarking"]["columns"].remove("site_eui_kbtu_sq_ft")
    with pytest.raises(SystemExit, match="site_eui_kbtu_sq_ft"):
        normalize.check_schema(m)


def test_a_new_unacknowledged_column_fails(raw_manifest):
    m = copy.deepcopy(raw_manifest)
    m["meta"]["sources"]["benchmarking"]["columns"].append("beam_property_id")
    with pytest.raises(SystemExit, match="beam_property_id"):
        normalize.check_schema(m)


def test_unknown_reporting_status_fails():
    with pytest.raises(SystemExit, match="Pending Review"):
        normalize.map_status(pd.Series(["Submitted", "Pending Review"]))
    with pytest.raises(SystemExit):
        normalize.map_status(pd.Series(["Submitted", None]))


def test_every_label_in_the_snapshot_is_mapped(energy_long):
    assert set(energy_long["reporting_status"]) <= set(schema.STATUS_MAP)
    assert set(energy_long["status"]) <= set(schema.STATUSES)


def test_fixed_decisions():
    assert schema.DISPLAY_YEARS == [2022]
    assert 2023 in schema.EXCLUDED_YEARS
    assert not set(schema.DISPLAY_YEARS) & set(schema.EXCLUDED_YEARS)


# --- Total site energy ----------------------------------------------------------------------------------
def test_eui_x_gfa_is_exact_to_the_kbtu(energy_long):
    both = energy_long["site_eui_kbtu_sq_ft"].notna() & energy_long["gross_floor_area_buildings_sq_ft"].notna()
    assert energy_long.loc[~both, "eui_x_gfa_kbtu"].isna().all()
    assert energy_long.loc[both, "eui_x_gfa_kbtu"].notna().all()
    rows = energy_long[both]
    for eui, gfa, got in zip(rows["site_eui_kbtu_sq_ft"], rows["gross_floor_area_buildings_sq_ft"],
                             rows["eui_x_gfa_kbtu"]):
        exact = Decimal(repr(eui)) * Decimal(repr(gfa))
        assert abs(Decimal(int(got)) - exact) <= Decimal("0.5"), (eui, gfa, got)


def test_unit_cases():
    assert normalize.eui_x_gfa_kbtu("85.3", "100000") == 8530000      # not 8529999.99...
    assert normalize.eui_x_gfa_kbtu("1296.4", "1279228") == 1658391179  # Digital Lakeside, 2022
    assert normalize.eui_x_gfa_kbtu(None, "100000") is None
    fuels = ["10.5", None, "2", None, None]
    assert normalize.fuel_sum_kbtu([None] * 5) is None                  # nothing published
    assert normalize.fuel_sum_kbtu(fuels) == 12.5                       # nulls as 0
    assert normalize.site_energy("85.3", "100000", fuels) == (13, "fuel_sum")          # 12.5 rounds half up
    assert normalize.site_energy("85.3", "100000", [None] * 5) == (8530000, "eui_x_gfa")
    assert normalize.site_energy("85.3", "100000", ["0", "0", None, None, None]) == (8530000, "eui_x_gfa")
    assert normalize.site_energy(None, "100000", fuels) == (None, None)  # no EUI, no total
    assert normalize.ratio_to_eui_x_gfa(1000, 835, "fuel_sum") == 0.835
    assert normalize.ratio_to_eui_x_gfa(1000, 835, "eui_x_gfa") is None
    assert normalize.gfa_consistent(0.970001) and not normalize.gfa_consistent(0.97)
    assert normalize.gfa_consistent(None) is None


def test_total_is_the_fuel_sum_wherever_an_eui_is_published(energy_long):
    e = energy_long
    has_eui = e["site_eui_kbtu_sq_ft"].notna()
    assert e.loc[~has_eui, "site_energy_kbtu"].isna().all()
    fuel = has_eui & (e["fuel_sum_kbtu"] > 0)
    assert (e.loc[fuel, "site_energy_basis"] == "fuel_sum").all()
    diff = (e.loc[fuel, "site_energy_kbtu"].astype("float64") - e.loc[fuel, "fuel_sum_kbtu"]).abs()
    assert (diff <= 0.5).all()
    alt = e["site_energy_basis"] == "eui_x_gfa"
    assert (e.loc[alt, "site_energy_kbtu"] == e.loc[alt, "eui_x_gfa_kbtu"]).all()
    assert not (alt & (e["fuel_sum_kbtu"] > 0)).any()


def test_gfa_consistent_is_the_ratio_within_tolerance(energy_long):
    r = energy_long["ratio_to_eui_x_gfa"]
    flag = energy_long["gfa_consistent"]
    assert flag[r.isna()].isna().all()
    assert (flag[r.notna()] == ((r[r.notna()] - 1).abs() < schema.GFA_TOLERANCE)).all()


def test_2022_floor_area_check(normalize_summary):
    """In 2022 every submitted record with an EUI has fuel figures, and for 989 of
    2,562 EUI x GFA runs 3% or more above them; none runs below."""
    s = normalize_summary
    fa = s["years"]["2022"]["floor_area"]
    assert fa["submitted_with_total"] == 2562 and fa["total_is_fuel_sum"] == 2562
    assert fa["within_3pct"] == 1573
    assert fa["overstated_3pct_or_more"] == 989
    assert fa["ratio_1.03_or_above"] == 0


def test_the_ghg_columns_say_the_floor_area_is_inflated(normalize_summary):
    """Where EUI x GFA overstates the fuel total, total GHG / (GHG intensity x GFA) falls by the
    same factor; where it does not, that ratio is 1. An incomplete fuel column would leave the
    GHG ratio at 1 everywhere. If a new snapshot breaks this, every passage that explains the
    total, in the README and on the page, must be re-read before release."""
    s = normalize_summary
    g = s["years"]["2022"]["floor_area"]["ghg"]
    assert g["overstated_r"] >= 0.99
    assert abs(g["overstated_median_ghg_ratio"] - g["overstated_median_fuel_ratio"]) < 0.01
    assert g["overstated_within_3_points"] >= 0.98 * g["overstated_with_ghg"]
    assert abs(g["consistent_median_ghg_ratio"] - 1) < 0.002


def test_the_excluded_years_floor_areas_are_consistent(normalize_summary):
    """Quoted in the public README and on the page: 2023 is the release whose floor areas agree."""
    s = normalize_summary
    for y in schema.EXCLUDED_YEARS:
        assert s["years"][str(y)]["floor_area"]["share_consistent"] >= 0.99


# --- Coordinates ---------------------------------------------------------------------------------------------
def test_the_excluded_year_fails_the_test_that_excludes_it(energy_long):
    for year in schema.EXCLUDED_YEARS:
        g = energy_long[(energy_long["data_year"] == year) & energy_long["ca_num"].notna()
                        & energy_long["latitude"].notna()]
        assert g["coord_in_ca"].mean() < schema.MIN_CA_PASS_RATE
    for year in schema.DISPLAY_YEARS:
        g = energy_long[(energy_long["data_year"] == year) & energy_long["ca_num"].notna()
                        & energy_long["latitude"].notna()]
        assert g["coord_in_ca"].mean() >= schema.MIN_CA_PASS_RATE


def test_no_community_area_means_no_pass(energy_long):
    assert not energy_long.loc[energy_long["ca_num"].isna(), "coord_in_ca_buffered"].any()
    assert not energy_long.loc[energy_long["latitude"].isna(), "coord_in_ca_buffered"].any()
