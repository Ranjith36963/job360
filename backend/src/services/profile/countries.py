"""ISO 3166-1 alpha-2 country table (a finite, closed set) and a forgiving lookup.

Job360 stores ISO alpha-2 codes. An assistant often sends what the user SAID
("United Kingdom", "UK", "Holland"); :func:`to_iso2` turns that into the code so
the fact is kept instead of refused. Pure data + one function, no I/O.
"""
from __future__ import annotations

import re
import unicodedata

_TABLE = """
AD Andorra|AE United Arab Emirates|AF Afghanistan|AG Antigua and Barbuda|AI Anguilla|AL Albania|AM Armenia
AO Angola|AQ Antarctica|AR Argentina|AS American Samoa|AT Austria|AU Australia|AW Aruba|AX Aland Islands
AZ Azerbaijan|BA Bosnia and Herzegovina|BB Barbados|BD Bangladesh|BE Belgium|BF Burkina Faso|BG Bulgaria
BH Bahrain|BI Burundi|BJ Benin|BL Saint Barthelemy|BM Bermuda|BN Brunei Darussalam|BO Bolivia
BQ Bonaire Sint Eustatius and Saba|BR Brazil|BS Bahamas|BT Bhutan|BV Bouvet Island|BW Botswana|BY Belarus
BZ Belize|CA Canada|CC Cocos Keeling Islands|CD Democratic Republic of the Congo|CF Central African Republic
CG Congo|CH Switzerland|CI Cote d Ivoire|CK Cook Islands|CL Chile|CM Cameroon|CN China|CO Colombia
CR Costa Rica|CU Cuba|CV Cabo Verde|CW Curacao|CX Christmas Island|CY Cyprus|CZ Czechia|DE Germany
DJ Djibouti|DK Denmark|DM Dominica|DO Dominican Republic|DZ Algeria|EC Ecuador|EE Estonia|EG Egypt
EH Western Sahara|ER Eritrea|ES Spain|ET Ethiopia|FI Finland|FJ Fiji|FK Falkland Islands|FM Micronesia
FO Faroe Islands|FR France|GA Gabon|GB United Kingdom|GD Grenada|GE Georgia|GF French Guiana|GG Guernsey
GH Ghana|GI Gibraltar|GL Greenland|GM Gambia|GN Guinea|GP Guadeloupe|GQ Equatorial Guinea|GR Greece
GS South Georgia and the South Sandwich Islands|GT Guatemala|GU Guam|GW Guinea-Bissau|GY Guyana|HK Hong Kong
HM Heard Island and McDonald Islands|HN Honduras|HR Croatia|HT Haiti|HU Hungary|ID Indonesia|IE Ireland
IL Israel|IM Isle of Man|IN India|IO British Indian Ocean Territory|IQ Iraq|IR Iran|IS Iceland|IT Italy
JE Jersey|JM Jamaica|JO Jordan|JP Japan|KE Kenya|KG Kyrgyzstan|KH Cambodia|KI Kiribati|KM Comoros
KN Saint Kitts and Nevis|KP North Korea|KR South Korea|KW Kuwait|KY Cayman Islands|KZ Kazakhstan|LA Laos
LB Lebanon|LC Saint Lucia|LI Liechtenstein|LK Sri Lanka|LR Liberia|LS Lesotho|LT Lithuania|LU Luxembourg
LV Latvia|LY Libya|MA Morocco|MC Monaco|MD Moldova|ME Montenegro|MF Saint Martin|MG Madagascar
MH Marshall Islands|MK North Macedonia|ML Mali|MM Myanmar|MN Mongolia|MO Macao|MP Northern Mariana Islands
MQ Martinique|MR Mauritania|MS Montserrat|MT Malta|MU Mauritius|MV Maldives|MW Malawi|MX Mexico|MY Malaysia
MZ Mozambique|NA Namibia|NC New Caledonia|NE Niger|NF Norfolk Island|NG Nigeria|NI Nicaragua|NL Netherlands
NO Norway|NP Nepal|NR Nauru|NU Niue|NZ New Zealand|OM Oman|PA Panama|PE Peru|PF French Polynesia
PG Papua New Guinea|PH Philippines|PK Pakistan|PL Poland|PM Saint Pierre and Miquelon|PN Pitcairn
PR Puerto Rico|PS Palestine|PT Portugal|PW Palau|PY Paraguay|QA Qatar|RE Reunion|RO Romania|RS Serbia
RU Russia|RW Rwanda|SA Saudi Arabia|SB Solomon Islands|SC Seychelles|SD Sudan|SE Sweden|SG Singapore
SH Saint Helena|SI Slovenia|SJ Svalbard and Jan Mayen|SK Slovakia|SL Sierra Leone|SM San Marino|SN Senegal
SO Somalia|SR Suriname|SS South Sudan|ST Sao Tome and Principe|SV El Salvador|SX Sint Maarten|SY Syria
SZ Eswatini|TC Turks and Caicos Islands|TD Chad|TF French Southern Territories|TG Togo|TH Thailand
TJ Tajikistan|TK Tokelau|TL Timor-Leste|TM Turkmenistan|TN Tunisia|TO Tonga|TR Turkiye|TT Trinidad and Tobago
TV Tuvalu|TW Taiwan|TZ Tanzania|UA Ukraine|UG Uganda|UM US Minor Outlying Islands|US United States
UY Uruguay|UZ Uzbekistan|VA Holy See|VC Saint Vincent and the Grenadines|VE Venezuela
VG British Virgin Islands|VI US Virgin Islands|VN Vietnam|VU Vanuatu|WF Wallis and Futuna|WS Samoa|XK Kosovo
YE Yemen|YT Mayotte|ZA South Africa|ZM Zambia|ZW Zimbabwe
"""

# Common spellings people actually say, each pointing at ONE code.
_ALIASES: dict[str, str] = {
    "uk": "GB", "great britain": "GB", "britain": "GB", "england": "GB", "scotland": "GB", "wales": "GB",
    "northern ireland": "GB", "united kingdom of great britain and northern ireland": "GB",
    "usa": "US", "us": "US", "u s": "US", "u s a": "US", "america": "US", "united states of america": "US",
    "uae": "AE", "emirates": "AE", "korea": "KR", "republic of korea": "KR", "russian federation": "RU",
    "czech republic": "CZ", "turkey": "TR", "holland": "NL", "the netherlands": "NL", "ivory coast": "CI",
    "cape verde": "CV", "swaziland": "SZ", "burma": "MM", "macedonia": "MK", "viet nam": "VN", "brunei": "BN",
    "lao pdr": "LA", "islamic republic of iran": "IR", "hong kong sar": "HK", "macau": "MO", "east timor": "TL",
    "vatican": "VA", "vatican city": "VA", "drc": "CD", "dr congo": "CD", "congo kinshasa": "CD",
    "congo brazzaville": "CG", "republic of the congo": "CG", "republic of ireland": "IE", "eire": "IE",
    "palestinian territories": "PS", "state of palestine": "PS", "syrian arab republic": "SY",
    "united republic of tanzania": "TZ", "bolivarian republic of venezuela": "VE", "the gambia": "GM",
    "the bahamas": "BS", "p r china": "CN", "peoples republic of china": "CN",
    "republic of china": "TW", "kingdom of saudi arabia": "SA", "ksa": "SA",
}

# Nationalities, for `citizenship` ("British" -> GB). Closed list of the common ones.
_DEMONYMS: dict[str, str] = {
    "british": "GB", "american": "US", "indian": "IN", "irish": "IE", "french": "FR", "german": "DE",
    "spanish": "ES", "italian": "IT", "dutch": "NL", "portuguese": "PT", "polish": "PL", "canadian": "CA",
    "australian": "AU", "new zealander": "NZ", "chinese": "CN", "japanese": "JP", "korean": "KR",
    "pakistani": "PK", "bangladeshi": "BD", "sri lankan": "LK", "nigerian": "NG", "kenyan": "KE",
    "south african": "ZA", "egyptian": "EG", "brazilian": "BR", "mexican": "MX", "argentinian": "AR",
    "argentine": "AR", "swedish": "SE", "norwegian": "NO", "danish": "DK", "finnish": "FI", "swiss": "CH",
    "austrian": "AT", "belgian": "BE", "greek": "GR", "turkish": "TR", "russian": "RU", "ukrainian": "UA",
    "romanian": "RO", "bulgarian": "BG", "hungarian": "HU", "czech": "CZ", "singaporean": "SG",
    "malaysian": "MY", "filipino": "PH", "indonesian": "ID", "thai": "TH", "vietnamese": "VN",
    "emirati": "AE", "saudi": "SA", "israeli": "IL", "iranian": "IR", "scottish": "GB", "english": "GB",
    "welsh": "GB",
}


# Names that point at more than one country: refused (never guessed) so the user's meaning is asked for.
_AMBIGUOUS: frozenset[str] = frozenset({
    "congo", "korea", "korean", "georgia", "guinea", "sudan", "ireland island", "china taiwan", "yemen arab",
    "virgin islands", "samoa islands", "saint martin island", "sint maarten saint martin", "timor",
})


def _fold(text: str) -> str:
    """Lower-case, accents removed, punctuation to spaces, '&' -> 'and'."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    plain = plain.replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", plain).strip()


NAMES: dict[str, str] = {}
for _row in _TABLE.replace("\n", "|").split("|"):
    _row = _row.strip()
    if _row:
        NAMES[_row[:2]] = _row[3:]
CODES: frozenset[str] = frozenset(NAMES)
_BY_NAME: dict[str, str] = {_fold(name): code for code, name in NAMES.items()}


def to_iso2(text: str, *, allow_demonym: bool = False, strict: bool = False) -> str | None:
    """The ISO alpha-2 code for ``text`` (a code, a name, a common spelling) or ``None``.

    Two letters that are not a known code (and not ``UK``) are returned as given,
    upper-cased: the old "two letters is the whole rule" stays for codes the table
    has not heard of (``strict=True`` refuses those). ``allow_demonym`` also accepts
    "British"-style nationalities.
    """
    folded = _fold(text)
    if not folded or folded in _AMBIGUOUS:
        return None
    if folded in _ALIASES:
        return _ALIASES[folded]
    if len(folded) == 2 and folded.isalpha():
        return folded.upper() if not strict or folded.upper() in CODES else None
    if folded in _BY_NAME:
        return _BY_NAME[folded]
    if folded.startswith("the ") and folded[4:] in _BY_NAME:
        return _BY_NAME[folded[4:]]
    if allow_demonym and folded in _DEMONYMS:
        return _DEMONYMS[folded]
    return None


def is_plain_code(text: str) -> bool:
    """True when ``text`` is already a bare upper-case 2-letter code other than ``UK``."""
    t = text.strip()
    return len(t) == 2 and t.isalpha() and t.isupper() and t != "UK"
