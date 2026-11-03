from er.normalize import normalize, prepare_record, strong_ein


def test_normalize_strips_suffixes():
    assert normalize("Amazon.com Services LLC") == "amazoncom services"
    assert normalize(" Acme, INC. LLC ") == "acme"
    assert normalize("Lincoln CO Research") == "lincoln co research"
    assert normalize("Café & Sons, PLLC") == "café sons"
    assert normalize("LLC") == ""


def test_strong_identifiers_are_not_last_four_or_placeholders():
    assert strong_ein("12-3456780", "lca") == "US_EIN:123456780"
    for raw in ("****6789", "6789", "00-0000000", "11-1111111", "12-345678", "99-9999999"):
        assert strong_ein(raw, "uscis") is None
    assert strong_ein("00-1234567", "lca") is None
    assert strong_ein("96-1234567", "lca") is None
    assert (
        "EIN_PREFIX_REQUIRES_REVIEW"
        in prepare_record({"source_system": "lca", "tax_id": "96-1234567"})["issues"]
    )
    assert strong_ein("123456780", "uscis") == "US_EIN:123456780"
    assert strong_ein("12-3456780", "unknown") is None
    assert prepare_record({"source_system": "lca", "tax_id": "123456789"})["tax_last4"] is None


def test_missing_identity_is_explicit():
    row = prepare_record({"source_system": "lca", "source_employer_key": None, "source_name": ""})
    assert "MISSING_NAME" in row["issues"]
    assert "MISSING_KEY" in row["issues"]
    assert "MISSING_STATE" in row["issues"]
    assert row["eligible"] is False
