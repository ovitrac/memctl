"""
Stdlib text similarity for fixed-point detection in memctl loop.

Provides normalized text comparison using two complementary measures:
- **Token Jaccard**: set-overlap of word tokens (order-insensitive).
- **SequenceMatcher ratio**: character-level similarity (order-sensitive).

Combined via weighted average to detect semantic convergence of LLM answers
and query-cycle repetition — without any external dependency.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

from __future__ import annotations

import re
import string
from difflib import SequenceMatcher

# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

# Precompiled translation table: strip all punctuation
_PUNCT_TABLE = str.maketrans("", "", string.punctuation)

# Collapse runs of whitespace
_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Normalize text for similarity comparison.

    Steps:
      1. Lowercase
      2. Strip punctuation
      3. Collapse whitespace
      4. Strip leading/trailing whitespace

    Returns empty string for empty/whitespace-only input.
    """
    text = text.lower()
    text = text.translate(_PUNCT_TABLE)
    text = _WS_RE.sub(" ", text)
    return text.strip()


def tokenize(text: str) -> list[str]:
    """Split normalized text into word tokens.

    Operates on already-normalized text (lowercase, no punctuation).
    Returns empty list for empty input.
    """
    if not text:
        return []
    return text.split()


# ---------------------------------------------------------------------------
# Similarity measures
# ---------------------------------------------------------------------------


def jaccard(a: str, b: str) -> float:
    """Token-level Jaccard similarity between two texts.

    J(A, B) = |A ∩ B| / |A ∪ B|

    Inputs are normalized internally. Returns 1.0 if both are empty
    (vacuous similarity), 0.0 if one is empty and the other is not.
    """
    tokens_a = set(tokenize(normalize(a)))
    tokens_b = set(tokenize(normalize(b)))

    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0

    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


def sequence_ratio(a: str, b: str) -> float:
    """Character-level similarity via difflib.SequenceMatcher.

    Returns a float in [0.0, 1.0]. Inputs are normalized internally.
    Returns 1.0 if both are empty, 0.0 if one is empty and the other is not.
    """
    norm_a = normalize(a)
    norm_b = normalize(b)

    if not norm_a and not norm_b:
        return 1.0
    if not norm_a or not norm_b:
        return 0.0

    return SequenceMatcher(None, norm_a, norm_b).ratio()


def similarity(
    a: str,
    b: str,
    *,
    jaccard_weight: float = 0.4,
    sequence_weight: float = 0.6,
) -> float:
    """Combined text similarity score.

    Weighted average of token Jaccard and character-level SequenceMatcher:

        sim = w_j * jaccard(a, b) + w_s * sequence_ratio(a, b)

    Default weights (0.4 / 0.6) favour order-sensitive matching, which
    better captures paraphrasing vs. genuine content change.

    Args:
        a: First text.
        b: Second text.
        jaccard_weight: Weight for Jaccard component (default 0.4).
        sequence_weight: Weight for SequenceMatcher component (default 0.6).

    Returns:
        Float in [0.0, 1.0].

    Raises:
        ValueError: If weights are negative or both zero.
    """
    if jaccard_weight < 0 or sequence_weight < 0:
        raise ValueError("Weights must be non-negative")
    total = jaccard_weight + sequence_weight
    if total == 0:
        raise ValueError("At least one weight must be positive")

    j = jaccard(a, b)
    s = sequence_ratio(a, b)
    return (jaccard_weight * j + sequence_weight * s) / total


# ---------------------------------------------------------------------------
# Fixed-point and cycle detection helpers
# ---------------------------------------------------------------------------


def is_fixed_point(a: str, b: str, threshold: float = 0.92) -> bool:
    """Test whether two texts are similar enough to declare convergence.

    Args:
        a: Current answer.
        b: Previous answer.
        threshold: Similarity threshold (default 0.92).

    Returns:
        True if similarity(a, b) >= threshold.
    """
    return similarity(a, b) >= threshold


def is_query_cycle(
    query: str,
    history: list[str],
    threshold: float = 0.90,
) -> bool:
    """Detect whether a refined query repeats or is too similar to a previous one.

    Checks:
      1. Exact match (after normalization) against any historical query.
      2. Similarity >= threshold against the most recent query.

    Args:
        query: The new refined query.
        history: List of previous queries (unnormalized; normalization applied here).
        threshold: Similarity threshold for near-duplicate detection (default 0.90).

    Returns:
        True if the query is a cycle (should stop).
    """
    if not query or not query.strip():
        return True

    norm_query = normalize(query)
    if not norm_query:
        return True

    # Check exact match against all history
    for prev in history:
        if normalize(prev) == norm_query:
            return True

    # Check similarity against most recent query
    if history:
        if similarity(query, history[-1]) >= threshold:
            return True

    return False
