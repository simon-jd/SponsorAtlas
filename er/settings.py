"""Splink 5 settings shared with the explicitly gated Spark entry point."""

import splink.comparison_library as cl
from splink import SettingsCreator

SEED = 42
# The only place the deployed match threshold is set (er/tests/test_threshold_constant.py).
# User decision 2026-10-29: 7/8, chosen after the agent-label holdout results had been seen
# (0.85 set 2026-10-28, 0.9 before). Precision and recall targets stay 0.97 and 0.90.
THRESHOLD = 0.875
CURVE = (0.5, 0.7, 0.8, 0.85, 0.875, 0.9, 0.95, 0.97, 0.99)
MAX_CANDIDATES = 5_000_000
MAX_RECORDS = 250_000
MAX_U_PAIRS = 2_000_000
# The strong ID block is independent of name/state, including changed names or offices.
NAME_BLOCK = "l.first_token = r.first_token AND l.state = r.state"
ID_BLOCK = "l.strong_id = r.strong_id"
# Same normalized name and tax-ID suffix, regardless of state (USCIS rows often lack one).
NAME_TAX_BLOCK = "l.name_norm = r.name_norm AND l.tax_last4 = r.tax_last4"


def settings():
    return SettingsCreator(
        link_type="dedupe_only",
        unique_id_column_name="unique_id",
        comparisons=[
            cl.LevenshteinAtThresholds("name_norm", [1, 3]),
            cl.ExactMatch("city"),
            cl.ExactMatch("postcode"),
            cl.ExactMatch("tax_last4"),
            cl.ExactMatch("strong_id"),
        ],
        blocking_rules_to_generate_predictions=[NAME_BLOCK, ID_BLOCK, NAME_TAX_BLOCK],
        probability_two_random_records_match=0.0001,
        max_iterations=25,
        em_convergence=0.0001,
        linker_uid="sponsoratlas_er_v1",
        retain_matching_columns=False,
    )
