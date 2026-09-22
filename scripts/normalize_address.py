"""Parse a Chicago street address string into the fields the city's building
footprint table uses (f_add1/t_add1, pre_dir1, st_name1, st_type1).

Benchmarking addresses are free text typed by building owners: '2800 S. Ashland Ave',
'427 S. Lasalle`', '301 East Cermak Road', '5061  N Pulaski Rd', '1653 W Congress Pkwy'.
Footprint fields are upper-case, no punctuation, USPS-style type codes, and the street
type is blank for ~41% of footprints, so the type is a tiebreaker, never a key.

Returns a dict with number (int|None), number_hi (int|None), pre_dir, st_name, st_type,
unit, extra, raw, and parse_ok. Nothing is guessed: an address that does not yield a house
number and a street name is parse_ok=False and stays unmatched.

House-number forms read (each one appears in the benchmarking release; tests/ carries the
literal strings):

  315-331, 4612 - 4730, 801- 831     a range, however the hyphen is spaced
  1610 1620, 849 863 W BUENA AVE     a range written with a space - the 2022 release's form
  8725-45, 6640 50                   Chicago short form: the second number replaces the last
                                     digits of the first (8725-8745). Never read as 45.
  205/225, 1353 & 1357, 101 and 111,
  4211,4215,4217                     several numbers on one street: lowest and highest kept
  One, Two ... Ten                   a spelled-out house number
  6101E N Sheridan                   a letter suffix before a direction is a sub-address
  710N Lakeshore                     a direction glued to the number
  North Tower: 123 ...               a leading label, dropped
  425 S Financial Pl (440 S LaSalle),
  2000 N ...; 2052 N ...             a second address: the first is parsed, the rest is
                                     carried in `extra` and never matched on

What is refused: a second number that does not exceed the first after expansion
('123-05'), an intersection ('California Avenue and 15th Street'), a place name
("O'Hare Int'l Airport"). Those stay parse_ok=False.
"""
import re

DIRS = {"N": "N", "NORTH": "N", "S": "S", "SOUTH": "S", "E": "E", "EAST": "E", "W": "W", "WEST": "W"}
TYPES = {
    "AVENUE": "AVE", "AVE": "AVE", "AV": "AVE",
    "STREET": "ST", "ST": "ST", "STR": "ST",
    "BOULEVARD": "BLVD", "BLVD": "BLVD", "BL": "BLVD",
    "ROAD": "RD", "RD": "RD",
    "DRIVE": "DR", "DR": "DR", "DRV": "DR",
    "PLACE": "PL", "PL": "PL",
    "PARKWAY": "PKWY", "PKWY": "PKWY", "PKY": "PKWY", "PARKWY": "PKWY",
    "COURT": "CT", "CT": "CT",
    "TERRACE": "TER", "TER": "TER", "TERR": "TER",
    "LANE": "LN", "LN": "LN",
    "HIGHWAY": "HWY", "HWY": "HWY",
    "PLAZA": "PLZ", "PLZ": "PLZ",
    "EXPRESSWAY": "EXPY", "EXPY": "EXPY", "EXPWY": "EXPY",
    "CIRCLE": "CIR", "CIR": "CIR",
    "SQUARE": "SQ", "SQ": "SQ",
    "WAY": "WAY", "ROW": "ROW", "PATH": "PATH", "TRAIL": "TRL", "TRL": "TRL",
}
# Street-name spellings that differ between owner-typed addresses and the footprint table.
# Extend from the reconciliation report; never guess here. Every target below is a value of
# st_name1 in the footprint snapshot (tests/test_normalize_address.py checks that it is).
NAME_SYNONYMS = {
    # The footprint table spells it 'LA SALLE'. (The first version of this table mapped it the
    # other way, which left every LaSalle Street address unmatched in the README §4 baseline.)
    "LASALLE": "LA SALLE",
    "KING": "DR MARTIN LUTHER KING JR",
    "KING JR": "DR MARTIN LUTHER KING JR",
    "MARTIN LUTHER KING JR": "DR MARTIN LUTHER KING JR",
    "MARTIN LUTHER KING": "DR MARTIN LUTHER KING JR",
    "DR MARTIN LUTHER KING": "DR MARTIN LUTHER KING JR",
    "DR MARTIN L KING JR": "DR MARTIN LUTHER KING JR",
    "MLK": "DR MARTIN LUTHER KING JR",
    "IDA B WELLS": "CONGRESS",          # renamed 2018; footprints are 2015 vintage
    "HOLLYWOOD": "HOLLYWOOD",
    "LAKESHORE": "LAKE SHORE",
    "LINCOLN PARK WEST": "LINCOLN PARK",  # the footprint table carries N LINCOLN PARK, no type
    "5TH": "FIFTH",                        # W 5TH AVE; the numbered streets start at 8TH
    "N WATER": "NORTH WATER", "S WATER": "SOUTH WATER",
    "E END": "EAST END", "W END": "WEST END",
}
# Streets whose footprint name includes the word this parser would take for the street type.
NAME_KEEPS_TYPE = {("CITYFRONT", "PLZ"): "CITYFRONT PLAZA", ("LITHUANIAN", "PLZ"): "LITHUANIAN PLAZA"}
SPELLED = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5,
           "SIX": 6, "SEVEN": 7, "EIGHT": 8, "NINE": 9, "TEN": 10}
# The keyword must end at a word boundary: without it 'STE' swallows 'STEWART' and
# '6345 S Stewart' loses its street name.
UNIT_RE = re.compile(r"(?:-\s*)?(?:\b(?:SUITE|STE|UNIT|APT|FLOOR|FL|BLDG|BUILDING)\b|#)\s*[\w-]*$"
                     r"|\bMC\s?\d{3,5}$", re.I)   # a campus mail code: '5841 S MARYLAND AVE MC0985'
ZIP_RE = re.compile(r"^\d{5}(?:-\d{4})?$")
LABEL_RE = re.compile(r"^[A-Z][A-Z .]*:\s*(?=\S)")
NUM_GROUP_RE = re.compile(r"^\d+(?:[-/&]\d+)*$")
NUM_LETTER_RE = re.compile(r"^(\d+)([A-Z])$")


def _expand(numbers: list) -> list | None:
    """Expand Chicago short-form numbers against the first ('8725', '45' -> 8725, 8745).

    Returns None when a later number does not come out at or above the first: the string is
    then not a range this parser understands, and it is not turned into one.
    """
    lo = numbers[0]
    out = [int(lo)]
    for h in numbers[1:]:
        if len(h) < len(lo):
            h = lo[: len(lo) - len(h)] + h
        if int(h) < int(lo):
            return None
        out.append(int(h))
    return out


def normalize(raw: str) -> dict:
    out = {"raw": raw, "number": None, "number_hi": None, "pre_dir": "", "st_name": "",
           "st_type": "", "unit": "", "extra": "", "parse_ok": False}
    if not raw:
        return out
    s = raw.upper()
    s = s.replace("`", "").replace("'", "")
    s = s.replace(".", " ")                     # 'S.' 'St.' and the glued 'N.State', 'E.59th'
    # A second address is carried, not parsed: a parenthetical, or whatever follows ';'.
    extra = []
    for m in re.finditer(r"\(([^)]*)\)", s):
        extra.append(m.group(1).strip())
    s = re.sub(r"\([^)]*\)", " ", s)
    if ";" in s:
        s, rest = s.split(";", 1)
        extra.append(rest.strip())
    s = LABEL_RE.sub("", s.strip())             # 'NORTH TOWER: 123 N ...'
    # Separators between house numbers, however they are spaced, become one token:
    # '4612 - 4730' -> '4612-4730', '801- 831' -> '801-831', '1353 & 1357' -> '1353&1357',
    # '101 AND 111' -> '101&111', '4211,4215' -> '4211&4215'.
    s = re.sub(r"(?<=\d)\s*-\s*(?=\d)", "-", s)
    s = re.sub(r"(?<=\d)\s*/\s*(?=\d)", "/", s)
    s = re.sub(r"(?<=\d)\s*(?:&|,|\bAND\b)\s*(?=\d)", "&", s)
    # A second address after the street: '... W LAKE ST & 212 N PEORIA ST',
    # '201 W GRAND AVE/516 N WELLS', '2700-14 N SPAULDING, 3300-3312 W SCHUBERT'.
    m = re.search(r"(?<=[A-Z])\s*(?:/|,|\s&\s|\sAND\s)\s*(?=\d)", s)
    if m:
        extra.append(s[m.end():].strip())
        s = s[: m.start()]
    s = s.replace(",", " ")
    s = re.sub(r"(?<=[A-Z])-(?=[A-Z])", " ", s)  # 'STONY-ISLAND'
    out["extra"] = "; ".join(re.sub(r"\s+", " ", e) for e in extra if e)
    m = UNIT_RE.search(s)
    if m:
        out["unit"] = m.group(0).strip()
        s = s[: m.start()]
    s = re.sub(r"\s+", " ", s).strip()
    toks = s.split(" ")
    if not toks or not toks[0]:
        return out

    # --- house number -------------------------------------------------------------------
    first, toks = toks[0], toks[1:]
    if first in SPELLED and toks:
        numbers = [str(SPELLED[first])]
    elif NUM_GROUP_RE.match(first):
        numbers = re.split(r"[-/&]", first)
    elif NUM_LETTER_RE.match(first):
        digits, letter = NUM_LETTER_RE.match(first).groups()
        numbers = [digits]
        if toks and toks[0] in DIRS:
            out["unit"] = (letter + " " + out["unit"]).strip()   # '6101E N SHERIDAN'
        elif letter in DIRS:
            toks = [letter] + toks                               # '710N LAKESHORE'
        else:
            out["unit"] = (letter + " " + out["unit"]).strip()   # '123A MAIN'
    else:
        return out
    # A range written with a space: '849 863 W BUENA AVE'. A bare number is read as a house
    # number only when a street still follows it - in '100 63 ST' the 63 is the street.
    while toks and toks[0].isdigit() and len(toks) >= 2 and not (
            len(toks) == 2 and toks[1] in TYPES):
        numbers.append(toks[0])
        toks = toks[1:]
    expanded = _expand(numbers)
    if expanded is None:
        return out
    out["number"] = expanded[0]
    hi = max(expanded)
    out["number_hi"] = hi if hi > expanded[0] else None

    # --- direction, type, name ----------------------------------------------------------
    if toks and toks[0] in DIRS:
        out["pre_dir"] = DIRS[toks[0]]
        toks = toks[1:]
    # The city, state and ZIP typed after the street ('... 59TH STREET CHICAGO IL 60637'). Every
    # address here is in Chicago; the word is dropped only when a street name is left in front
    # of it, so '800 W CHICAGO' keeps its street.
    tail = list(toks)
    if len(tail) >= 2 and ZIP_RE.match(tail[-1]):
        tail = tail[:-1]
    if len(tail) >= 2 and tail[-1] in ("IL", "ILLINOIS"):
        tail = tail[:-1]
    if len(tail) >= 2 and tail[-1] == "CHICAGO":
        toks = tail[:-1]
    # strip a trailing post-directional ('DR W' style is rare in Chicago; keep it simple)
    if len(toks) >= 2 and toks[-1] in DIRS and toks[-2] in TYPES:
        toks = toks[:-1]
    if len(toks) >= 2 and toks[-1] in TYPES:
        out["st_type"] = TYPES[toks[-1]]
        toks = toks[:-1]
    if toks and toks[0] == "SAINT" and len(toks) >= 2:
        toks = ["ST"] + toks[1:]                # the footprint table writes ST CLAIR, ST LOUIS
    name = " ".join(toks).strip()
    name = NAME_SYNONYMS.get(name, name)
    if (name, out["st_type"]) in NAME_KEEPS_TYPE:
        name, out["st_type"] = NAME_KEEPS_TYPE[(name, out["st_type"])], ""
    out["st_name"] = name
    out["parse_ok"] = bool(name) and out["number"] is not None
    return out


if __name__ == "__main__":
    import sys, json
    for a in sys.argv[1:]:
        print(json.dumps(normalize(a)))
