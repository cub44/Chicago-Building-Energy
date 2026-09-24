"""Community-area display names, in the pothole release's spelling.

density_ca.csv carries the boundary layer's `name` in capitals and, beside it, `display_name`
as Chicago-Potholes spells `geo_name` in its community_area_summary.csv, so every project's
tables name an area the same way and the website prints the release rather than reformatting
it. The 77 names below are that release's, copied from it; the test runs without any data.
"""
import pytest

from conftest import PROCESSED, read_csv
from normalize import display_name

POTHOLE_NAMES = {
    1: 'Rogers Park',
    2: 'West Ridge',
    3: 'Uptown',
    4: 'Lincoln Square',
    5: 'North Center',
    6: 'Lake View',
    7: 'Lincoln Park',
    8: 'Near North Side',
    9: 'Edison Park',
    10: 'Norwood Park',
    11: 'Jefferson Park',
    12: 'Forest Glen',
    13: 'North Park',
    14: 'Albany Park',
    15: 'Portage Park',
    16: 'Irving Park',
    17: 'Dunning',
    18: 'Montclare',
    19: 'Belmont Cragin',
    20: 'Hermosa',
    21: 'Avondale',
    22: 'Logan Square',
    23: 'Humboldt Park',
    24: 'West Town',
    25: 'Austin',
    26: 'West Garfield Park',
    27: 'East Garfield Park',
    28: 'Near West Side',
    29: 'North Lawndale',
    30: 'South Lawndale',
    31: 'Lower West Side',
    32: 'Loop',
    33: 'Near South Side',
    34: 'Armour Square',
    35: 'Douglas',
    36: 'Oakland',
    37: 'Fuller Park',
    38: 'Grand Boulevard',
    39: 'Kenwood',
    40: 'Washington Park',
    41: 'Hyde Park',
    42: 'Woodlawn',
    43: 'South Shore',
    44: 'Chatham',
    45: 'Avalon Park',
    46: 'South Chicago',
    47: 'Burnside',
    48: 'Calumet Heights',
    49: 'Roseland',
    50: 'Pullman',
    51: 'South Deering',
    52: 'East Side',
    53: 'West Pullman',
    54: 'Riverdale',
    55: 'Hegewisch',
    56: 'Garfield Ridge',
    57: 'Archer Heights',
    58: 'Brighton Park',
    59: 'McKinley Park',
    60: 'Bridgeport',
    61: 'New City',
    62: 'West Elsdon',
    63: 'Gage Park',
    64: 'Clearing',
    65: 'West Lawn',
    66: 'Chicago Lawn',
    67: 'West Englewood',
    68: 'Englewood',
    69: 'Greater Grand Crossing',
    70: 'Ashburn',
    71: 'Auburn Gresham',
    72: 'Beverly',
    73: 'Washington Heights',
    74: 'Mount Greenwood',
    75: 'Morgan Park',
    76: "O'Hare",
    77: 'Edgewater',
}


def test_the_rule_gives_the_pothole_spelling_for_all_77():
    from normalize import CA_DISPLAY
    capitals = {n: name.upper().replace("'", "") for n, name in POTHOLE_NAMES.items()}
    assert {n: display_name(c) for n, c in capitals.items()} == POTHOLE_NAMES
    assert set(CA_DISPLAY) == {"OHARE", "MCKINLEY PARK"}


def test_density_ca_carries_the_display_name():
    if not (PROCESSED / "density_ca.csv").exists():
        pytest.skip("density_ca.csv not built - run `make build`")
    rows = read_csv("density_ca.csv")
    assert list(rows.columns[:3]) == ["community_area_num", "name", "display_name"]
    assert {int(n): d for n, d in zip(rows["community_area_num"], rows["display_name"])} == POTHOLE_NAMES
    assert all(display_name(c) == d for c, d in zip(rows["name"], rows["display_name"]))
