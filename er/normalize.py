"""Conservative employer features. Full EIN format is not legal ownership verification."""

import re
import unicodedata

SUFFIXES = {"llc", "inc", "corp", "co", "ltd", "llp", "pllc"}
# IRS IRM21.7.13.2.4.1 / valid-eins, checked 2026-10-24. Some retired
# prefixes can exist historically: require review rather than deterministic reuse.
REVIEW_EIN_PREFIXES = {
    "00",
    "07",
    "08",
    "09",
    "17",
    "18",
    "19",
    "28",
    "29",
    "49",
    "69",
    "70",
    "78",
    "79",
    "89",
    "96",
    "97",
}
US_STATES = {
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "DC",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
    "PR",
    "VI",
    "GU",
    "AS",
    "MP",
}


def normalize(name: str) -> str:
    text = "".join(
        c
        for c in unicodedata.normalize("NFKC", name).lower()
        if not unicodedata.category(c).startswith("P")
    )
    # Symbols are separators; punctuation is removed (Amazon.com -> amazoncom).
    words = "".join(c if c.isalnum() or c.isspace() else " " for c in text).split()
    while words and words[-1] in SUFFIXES:
        words.pop()
    return " ".join(words)


def strong_ein(raw: str | None, source_system: str) -> str | None:
    if source_system not in {"lca", "uscis"} or not raw:
        return None
    value = raw.strip()
    if not re.fullmatch(r"(?:\d{9}|\d{2}-\d{7})", value):
        return None
    digits = value.replace("-", "")
    if (
        digits[:2] in REVIEW_EIN_PREFIXES
        or len(set(digits)) == 1
        or digits in {"123456789", "987654321"}
    ):
        # Sequential examples occur as placeholders. Explicitly quarantine them.
        return None
    return "US_EIN:" + digits


def prepare_record(row: dict) -> dict:
    row = dict(row)
    row["name_norm"] = normalize(row.get("source_name") or "")
    row["first_token"] = row["name_norm"].split(" ")[0] or None
    state = (row.get("state") or "").upper().strip()
    row["state"] = state if state in US_STATES else None
    country = (row.get("country") or "").upper().strip()
    row["country"] = (
        "US"
        if country in {"US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"}
        else country or None
    )
    row["city"] = normalize(row.get("city") or "") or None
    postcode = re.sub(r"\D", "", row.get("postcode") or "")
    row["postcode"] = postcode[:5] if len(postcode) in {5, 9} else None
    raw = row.get("tax_id") or ""
    row["strong_id"] = strong_ein(raw, row.get("source_system"))
    digits = re.sub(r"\D", "", raw)
    row["tax_last4"] = (
        row["strong_id"][-4:]
        if row["strong_id"]
        else digits
        if len(digits) == 4 and len(set(digits)) > 1
        else None
    )
    issues = []
    if not row["name_norm"]:
        issues.append("MISSING_NAME")
    if not row.get("source_employer_key"):
        issues.append("MISSING_KEY")
    if not row["state"]:
        issues.append("MISSING_STATE")
    if row["country"] not in {None, "US"} and row["state"]:
        issues.append("COUNTRY_STATE_CONFLICT")
    if raw and not row["strong_id"]:
        issues.append("WEAK_OR_INVALID_TAX_ID")
    if re.fullmatch(r"(?:\d{9}|\d{2}-\d{7})", raw.strip()) and digits[:2] in REVIEW_EIN_PREFIXES:
        issues.append("EIN_PREFIX_REQUIRES_REVIEW")
    row["issues"] = "|".join(issues)
    row["eligible"] = not any(
        x in issues for x in ("MISSING_NAME", "MISSING_KEY", "COUNTRY_STATE_CONFLICT")
    )
    return row
