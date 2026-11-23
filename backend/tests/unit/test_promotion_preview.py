from decimal import Decimal

import pytest

from app.services.promotion_preview import parse_terms


@pytest.mark.parametrize("message", [
    "SKU-001 -8 折、持续 3 天",
    "SKU-001 8 到 9 折、持续 3 天",
    "SKU-001 8–9 折、持续 3 天",
    "SKU-001 8 折、持续 3 天半",
    "SKU-001 8 折、持续 3 天到 5 天",
    "SKU-001 8.50 折、持续 3.5 天",
])
def test_parse_terms_rejects_ambiguous_or_invalid_numbers(message):
    assert parse_terms(1, "SKU-001", message) is None


def test_parse_terms_accepts_a_single_decimal_discount_and_whole_day_duration():
    terms = parse_terms(1, "SKU-001", "给 SKU-001 做一个 8.50 折、持续 3 天的促销")
    assert terms is not None
    assert terms.discount == Decimal("8.50")
    assert terms.duration_days == 3


def test_parse_terms_accepts_llm_structured_slot_hints_for_natural_chinese():
    terms = parse_terms(
        1,
        "SKU-001",
        "帮我看看 SKU-001 最近 7 天销量，够的话打八折做三天",
        discount_hint=Decimal("8"),
        duration_hint=3,
    )
    assert terms is not None
    assert terms.discount == Decimal("8")
    assert terms.duration_days == 3


def test_parse_terms_rejects_when_llm_flags_ambiguous_terms():
    assert (
        parse_terms(
            1,
            "SKU-001",
            "给 SKU-001 做促销",
            discount_hint=Decimal("8"),
            duration_hint=3,
            ambiguous_hint=True,
        )
        is None
    )
