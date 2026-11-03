"""Artificial attributes for execution regression; never used by real evaluation."""

from er.normalize import prepare_record


def records():
    rows = []
    for group in range(24):
        for variant in range(4):
            uid = f"fixture-{group:02}-{variant}"
            rows.append(
                prepare_record(
                    {
                        "unique_id": uid,
                        "source_system": "lca" if variant % 2 == 0 else "uscis",
                        "source_employer_key": uid,
                        "source_name": f"Company{group} Research "
                        + ("LLC" if variant < 2 else "Services Inc"),
                        "state": "CA" if group % 2 else "TX",
                        "country": "US",
                        "city": f"City{group % 6}",
                        "postcode": f"{10000 + group % 8}",
                        "tax_id": f"82{1000000 + group}" if variant < 2 else f"{1000 + group}",
                    }
                )
            )
    return rows
