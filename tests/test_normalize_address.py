"""The address parser. Every string here is literal, as the benchmarking release writes it or
as METHODS.md (stage 1) lists it. The parser may refuse an address; it may not guess one."""
import pytest

from normalize_address import NAME_KEEPS_TYPE, NAME_SYNONYMS, normalize


def fields(raw):
    d = normalize(raw)
    return (d["parse_ok"], d["number"], d["number_hi"], d["pre_dir"], d["st_name"], d["st_type"])


# --- the space-separated range: the largest single cause of 2022 misses in the first baseline -
@pytest.mark.parametrize("raw, want", [
    ("849 863 W BUENA AVE", (True, 849, 863, "W", "BUENA", "AVE")),
    ("3913 3959 W MADISON ST", (True, 3913, 3959, "W", "MADISON", "ST")),
    ("1610 1620 W Fargo Ave", (True, 1610, 1620, "W", "FARGO", "AVE")),
    ("4039 4051 LAPORTE AVE", (True, 4039, 4051, "", "LAPORTE", "AVE")),   # no direction typed
    ("7347 7365 Sheridan Rd", (True, 7347, 7365, "", "SHERIDAN", "RD")),
])
def test_space_separated_range(raw, want):
    assert fields(raw) == want


def test_a_numbered_street_is_not_read_as_a_range():
    # In '100 63 ST' the 63 is the street: nothing follows it but a street type.
    assert fields("100 63 ST") == (True, 100, None, "", "63", "ST")
    assert fields("801- 831 W 119th St.") == (True, 801, 831, "W", "119TH", "ST")


# --- hyphenated ranges, however the hyphen is spaced -----------------------------------------
@pytest.mark.parametrize("raw, want", [
    ("315-331 W Main St", (True, 315, 331, "W", "MAIN", "ST")),
    ("4612 - 4730 S Drexel Blvd", (True, 4612, 4730, "S", "DREXEL", "BLVD")),
    ("801- 831 W 119th St.", (True, 801, 831, "W", "119TH", "ST")),
    ("5020 - 5050 S Lake Shore Dr", (True, 5020, 5050, "S", "LAKE SHORE", "DR")),
    ("8725- 8745 W Higgins Rd", (True, 8725, 8745, "W", "HIGGINS", "RD")),
])
def test_hyphen_range(raw, want):
    assert fields(raw) == want


# --- Chicago short form: the second number replaces the last digits of the first ----------------
@pytest.mark.parametrize("raw, lo, hi", [
    ("8725-45 W. Higgins Road", 8725, 8745),
    ("1000-08 W Loyola Ave", 1000, 1008),
    ("1435 - 71 West Webster Avenue", 1435, 1471),
    ("6640 50 W Belden Ave", 6640, 6650),
    ("2301 15 S Archer Ave", 2301, 2315),
])
def test_short_form_range(raw, lo, hi):
    d = normalize(raw)
    assert (d["parse_ok"], d["number"], d["number_hi"]) == (True, lo, hi)


def test_a_range_that_runs_backwards_is_refused_not_repaired():
    d = normalize("123-05 W Grand Ave")
    assert d["parse_ok"] is False and d["number"] is None


# --- several numbers on one street ------------------------------------------------------------------
@pytest.mark.parametrize("raw, want", [
    ("205/225 N Michigan Ave", (True, 205, 225, "N", "MICHIGAN", "AVE")),
    ("120/120-134 North Green Street", (True, 120, 134, "N", "GREEN", "ST")),
    ("1353 & 1357 S Blue Island Ave", (True, 1353, 1357, "S", "BLUE ISLAND", "AVE")),
    ("101 and 111 North State Street E", (True, 101, 111, "N", "STATE", "ST")),
    ("4211,4215,4217,4219,4221 N Paulina st.", (True, 4211, 4221, "N", "PAULINA", "ST")),
])
def test_number_lists(raw, want):
    assert fields(raw) == want


# --- spelled-out numbers, labels, second addresses ------------------------------------------------
@pytest.mark.parametrize("raw, want", [
    ("One North Wacker Drive", (True, 1, None, "N", "WACKER", "DR")),
    ("One South Wacker", (True, 1, None, "S", "WACKER", "")),
    ("Two East Oak Street", (True, 2, None, "E", "OAK", "ST")),
])
def test_spelled_out_number(raw, want):
    assert fields(raw) == want


def test_leading_label_is_dropped():
    assert fields("North Tower: 315-331 W Main St") == (True, 315, 331, "W", "MAIN", "ST")


def test_a_second_address_is_carried_not_parsed():
    d = normalize("425 South Financial Place (440 South LaSalle)")
    assert fields(d["raw"]) == (True, 425, None, "S", "FINANCIAL", "PL")
    assert d["extra"] == "440 SOUTH LASALLE"
    d = normalize("2000 N. Lincoln Park West; 2052 N. Lincoln Park West")
    assert (d["number"], d["st_name"], d["extra"]) == (2000, "LINCOLN PARK", "2052 N LINCOLN PARK WEST")
    d = normalize("900 910 912 W Lake St & 212 N Peoria St")
    assert (d["number"], d["number_hi"], d["st_name"], d["extra"]) == (900, 912, "LAKE", "212 N PEORIA ST")
    d = normalize("201 W Grand Ave/516 N. Wells")
    assert (d["number"], d["st_name"], d["extra"]) == (201, "GRAND", "516 N WELLS")


# --- the four parse failures in the first baseline run ------------------------------------------
def test_street_name_is_not_swallowed_as_a_unit():
    # 'STE' + 'WART': the unit keyword has to end at a word boundary.
    assert fields("6345 S Stewart") == (True, 6345, None, "S", "STEWART", "")


def test_letter_suffix_on_the_house_number():
    d = normalize("6101E N Sheridan Rd")
    assert fields(d["raw"]) == (True, 6101, None, "N", "SHERIDAN", "RD")
    assert d["unit"] == "E"
    # glued direction: the letter is the direction only when no direction follows it
    assert fields("710N Lakeshore Dr") == (True, 710, None, "N", "LAKE SHORE", "DR")


# --- names --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("raw, name", [
    ("3000 S King Dr", "DR MARTIN LUTHER KING JR"),
    ("3857 S DR MARTIN L KING JR DR", "DR MARTIN LUTHER KING JR"),
    ("115 S. LaSalle Street", "LA SALLE"),
    ("427 S. Lasalle`", "LA SALLE"),
    ("1653 W Congress Pkwy", "CONGRESS"),
    ("633 N St Clair", "ST CLAIR"),
    ("2333 W. Saint Paul Ave.", "ST PAUL"),
    ("450 North Cityfront Plaza", "CITYFRONT PLAZA"),
    ("7531-45 STONY-ISLAND AVE", "STONY ISLAND"),
    ("850 N.State Street", "STATE"),
    ("301 E N Water St", "NORTH WATER"),
])
def test_street_names(raw, name):
    d = normalize(raw)
    assert d["parse_ok"] and d["st_name"] == name


def test_city_state_zip_tail():
    assert fields("1116 East 59th Street  Chicago, IL 60637") == (True, 1116, None, "E", "59TH", "ST")
    assert fields("800 W Chicago") == (True, 800, None, "W", "CHICAGO", "")   # the street, kept


def test_campus_mail_code_is_a_unit():
    d = normalize("5841 S. Maryland Ave MC0985")
    assert fields(d["raw"]) == (True, 5841, None, "S", "MARYLAND", "AVE") and d["unit"] == "MC0985"
    assert normalize("1200 S MC CLURG CT")["st_name"] == "MC CLURG"   # the street, untouched


def test_building_designator_is_a_unit():
    d = normalize("5801 N Pulaski Rd - Bldg H")
    assert fields(d["raw"]) == (True, 5801, None, "N", "PULASKI", "RD") and "BLDG H" in d["unit"]


# --- what is refused ----------------------------------------------------------------------------------
@pytest.mark.parametrize("raw", [
    "California Avenue and 15th Street", "O'Hare Int'l Airport",
    "Chicago O'Hare International Airport", "50th Street and Champlain", "1816", "4600-4624",
    "", None,
])
def test_not_an_address_stays_unparsed(raw):
    assert normalize(raw)["parse_ok"] is False


def test_typos_are_not_corrected():
    # Typos go to the coordinate tiers, or to an override: a reviewed answer from the AI desk
    # review, recorded in footprint_overrides.csv.
    assert normalize("4645 N. Sherdian Road")["st_name"] == "SHERDIAN"
    assert normalize("2247 N. Halstead Ave.")["st_name"] == "HALSTEAD"


def test_every_synonym_target_is_a_footprint_street(footprints):
    names = set(footprints["st_name1"])
    targets = set(NAME_SYNONYMS.values()) | set(NAME_KEEPS_TYPE.values())
    assert targets <= names, sorted(targets - names)
