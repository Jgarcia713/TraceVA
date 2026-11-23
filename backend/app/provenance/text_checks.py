"""Soft checks on model-written text (no verification claims are made).

Minimum M3 check: list numbers in the free-text explanation that appear in no claim.
Claims are the cited, structured part of an answer; numbers only in the explanation
are not tied to any evidence. Stronger checks (claim numbers vs. cited evidence, or
backend-bound values) are candidate evaluation experiments.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation

_NUMBER = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.])\d+(?:\.\d+)?")


def numbers_in(text: str) -> set[str]:
    """Normalized numbers: thousands separators removed, trailing zeros dropped."""
    found: set[str] = set()
    for match in _NUMBER.findall(text):
        try:
            value = Decimal(match.replace(",", ""))
        except InvalidOperation:  # pragma: no cover - regex only matches numbers
            continue
        normalized = value.normalize()
        found.add(format(normalized, "f"))
    return found


CAVEATS_MARKER = "\n\nCaveats:\n"  # how the orchestrator appends answer-level caveats


def explanation_part(answer_content: str) -> str:
    """The free-text explanation of a stored answer, without its appended caveats."""
    return answer_content.split(CAVEATS_MARKER, 1)[0]


def unverified_numbers(explanation: str, claim_texts: Iterable[str]) -> list[str]:
    in_claims: set[str] = set()
    for text in claim_texts:
        in_claims |= numbers_in(text)
    return sorted(numbers_in(explanation) - in_claims, key=lambda s: Decimal(s))
