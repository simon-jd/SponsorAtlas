"""Context rules of constrained_clusters on explicit synthetic records."""

from er.predict import constrained_clusters


def rec(uid, name="acme analytics", state="NJ", city="newark", postcode="07102", **kw):
    row = {
        "unique_id": uid,
        "name_norm": name,
        "state": state,
        "city": city,
        "postcode": postcode,
        "country": None,
        "tax_last4": None,
        "strong_id": None,
        "eligible": True,
    }
    row.update(kw)
    return row


def decide(left, right, p=0.99):
    membership, decisions = constrained_clusters([left, right], [("a", "b", p)])
    return membership["a"] == membership["b"], decisions[0]["reason"]


def test_same_location_still_accepted():
    assert decide(rec("a"), rec("b")) == (True, "ACCEPTED")


def test_exact_name_and_tax_suffix_replace_location_context():
    # Same employer filed from another office in the same state.
    left = rec("a", tax_last4="8133", city="naperville", postcode="60564", state="IL")
    right = rec("b", tax_last4="8133", city="aurora", postcode="60504", state="IL")
    assert decide(left, right) == (True, "ACCEPTED")
    # USCIS rows without any address.
    left = rec("a", tax_last4="6944", state=None, city=None, postcode=None)
    right = rec("b", tax_last4="6944", state="OH", city="cincinnati", postcode="45202")
    assert decide(left, right) == (True, "ACCEPTED")


def test_name_and_suffix_rule_needs_exact_name_suffix_and_threshold():
    far = {"city": "aurora", "postcode": "60504"}
    assert decide(rec("a", tax_last4="8133"), rec("b", tax_last4="8133", **far))[0]
    assert decide(rec("a", tax_last4="8133"), rec("b", name="acme analytic", tax_last4="8133", **far)) == (
        False,
        "INSUFFICIENT_CONTEXT",
    )
    assert decide(rec("a", tax_last4="8133"), rec("b", **far)) == (False, "INSUFFICIENT_CONTEXT")
    assert decide(rec("a", tax_last4="8133"), rec("b", tax_last4="9999", **far)) == (
        False,
        "TAX_SUFFIX_CONFLICT",
    )
    assert decide(rec("a", tax_last4="8133"), rec("b", tax_last4="8133", **far), p=0.5) == (
        False,
        "BELOW_THRESHOLD",
    )


def test_country_typo_ignored_only_for_same_ein_at_same_postcode():
    ein = {"strong_id": "US_EIN:991791760", "tax_last4": "1760"}
    left = rec("a", country="US", **ein)
    assert decide(left, rec("b", country="GEORGIA", state=None, **ein)) == (True, "ACCEPTED")
    moved = rec("b", country="INDIA", city="pune", postcode="411001", state=None, **ein)
    assert decide(left, moved) == (False, "COUNTRY_CONFLICT")
    # Without the shared EIN a country conflict still vetoes.
    assert decide(rec("a", country="US"), rec("b", country="INDIA")) == (False, "COUNTRY_CONFLICT")


def test_overridden_country_does_not_poison_later_edges():
    ein = {"strong_id": "US_EIN:861059468", "tax_last4": "9468"}
    rows = [
        rec("a", country="US", **ein),
        rec("b", country="NAMIBIA", state=None, **ein),
        rec("c", country="US", tax_last4="9468"),
    ]
    membership, decisions = constrained_clusters(rows, [("a", "b", 0.999), ("a", "c", 0.99)])
    assert membership["a"] == membership["b"] == membership["c"]
    assert [d["reason"] for d in decisions] == ["ACCEPTED", "ACCEPTED"]
