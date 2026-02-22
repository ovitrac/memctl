"""
Query normalization and intent classification for eco mode.

Provides two capabilities:
  1. normalize_query() — strip stop words from FTS queries for better recall.
  2. classify_mode() — classify user intent as "exploration" or "modification".

Both are deterministic, stdlib-only, and designed for integration into
store.search_fulltext() and MCP tool responses.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

from __future__ import annotations

import logging
import re
from typing import Callable, List, Literal, Tuple

logger = logging.getLogger(__name__)

# ── Stop words ──────────────────────────────────────────────────────────

FR_STOP_WORDS = frozenset({
    "le", "la", "les", "un", "une", "des", "du", "de", "en", "dans",
    "pour", "avec", "sur", "par", "qui", "que", "est", "sont", "au",
    "aux", "ce", "cette", "ces", "se", "sa", "son", "ses", "ne", "pas",
    "ou", "et", "mais", "donc", "car", "ni", "si", "comme", "comment",
    "il", "elle", "on", "nous", "vous", "ils", "elles", "je", "tu",
    "mon", "ton", "notre", "votre", "leur", "leurs",
    "y", "en", "dont", "où",
})

EN_STOP_WORDS = frozenset({
    "the", "a", "an", "in", "on", "at", "to", "for", "of", "with",
    "by", "from", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall",
    "it", "its", "this", "that", "these", "those",
    "i", "me", "my", "we", "our", "you", "your", "he", "him", "his",
    "she", "her", "they", "them", "their",
    "not", "no", "nor", "so", "but", "or", "and", "if", "then",
    "about", "up", "out", "into", "over", "after", "before",
})

# Question words — stripped from FTS queries but used in mode classification
QUESTION_WORDS = frozenset({
    "how", "what", "where", "when", "why", "which", "who", "whom",
    "comment", "quoi", "quel", "quelle", "quels", "quelles", "pourquoi",
})

_ALL_STOP_WORDS = FR_STOP_WORDS | EN_STOP_WORDS | QUESTION_WORDS

# ── Identifier detection ────────────────────────────────────────────────

_CAMEL_RE = re.compile(r"[a-z][A-Z]")           # camelCase or PascalCase
_SNAKE_RE = re.compile(r"[a-zA-Z]_[a-zA-Z]")    # snake_case
_UPPER_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,}$") # UPPER_CASE constant


def _is_identifier(word: str) -> bool:
    """Return True if word looks like a code identifier."""
    if _CAMEL_RE.search(word):
        return True
    if _SNAKE_RE.search(word):
        return True
    if _UPPER_RE.match(word):
        return True
    # Dotted path (e.g., com.example.Foo)
    if "." in word and not word.endswith("."):
        return True
    return False


# ── Query normalization ─────────────────────────────────────────────────

def normalize_query(text: str) -> str:
    """Strip stop words from an FTS query for better recall.

    Preserves identifiers (CamelCase, snake_case, UPPER_CASE, dotted paths).
    Strips French and English stop words plus question words.
    Never returns an empty string — falls back to the original text.

    Examples:
        >>> normalize_query("comment créer un incident dans le système")
        'créer incident système'
        >>> normalize_query("how does SomeServiceImpl work")
        'SomeServiceImpl work'
        >>> normalize_query("PreAuthorize Controller")
        'PreAuthorize Controller'
        >>> normalize_query("the")
        'the'
    """
    words = text.strip().split()
    if not words:
        return text

    kept: list[str] = []
    for w in words:
        # Always keep identifiers regardless of stop-word status
        if _is_identifier(w):
            kept.append(w)
            continue
        # Strip stop words (case-insensitive)
        if w.lower() in _ALL_STOP_WORDS:
            continue
        kept.append(w)

    # Never return empty — fall back to original
    return " ".join(kept) if kept else text


# ── Mode classification ─────────────────────────────────────────────────

# Action verbs that signal modification intent
_MODIFICATION_VERBS = frozenset({
    # English
    "add", "replace", "refactor", "fix", "create", "delete", "update",
    "modify", "remove", "rename", "implement", "migrate", "upgrade",
    "configure", "install", "uninstall", "change", "move", "copy",
    "write", "rewrite", "patch", "merge", "split", "convert",
    "enable", "disable", "set", "reset",
    # French
    "ajouter", "remplacer", "corriger", "créer", "supprimer", "modifier",
    "renommer", "implémenter", "migrer", "configurer", "installer",
    "changer", "déplacer", "copier", "écrire", "réécrire", "activer",
    "désactiver",
})

# Exploration verbs/words that signal comprehension intent
_EXPLORATION_WORDS = frozenset({
    # English
    "how", "where", "what", "which", "who", "whom",
    "explain", "describe", "show", "list", "find", "search",
    "understand", "trace", "check", "compare", "analyze", "review",
    "structure", "dependency", "module", "layer", "flow", "pattern",
    "architecture", "overview", "summary", "diagram",
    # French
    "comment", "où", "quel", "quelle", "quels", "quelles", "qui",
    "expliquer", "décrire", "montrer", "lister", "trouver", "chercher",
    "comprendre", "tracer", "vérifier", "comparer", "analyser",
})

Mode = Literal["exploration", "modification"]


def classify_mode(text: str) -> Mode:
    """Classify user intent as 'exploration' or 'modification'.

    Uses deterministic verb-based classification:
    - If any modification verb is found → "modification"
    - Otherwise → "exploration" (default)

    Modification verbs take priority because modification queries often
    contain exploration words too ("explain how to add X" → modification).

    Examples:
        >>> classify_mode("How does SomeServiceImpl work?")
        'exploration'
        >>> classify_mode("Add logging to SomeServiceImpl")
        'modification'
        >>> classify_mode("Where is MSG_ERR_042 defined?")
        'exploration'
        >>> classify_mode("Replace MSG_ERR_042 with MSG_ERR_043")
        'modification'
    """
    words = text.lower().split()

    # Check for modification verbs (higher priority)
    for w in words:
        # Strip punctuation for matching
        clean = w.strip(".,;:!?\"'()[]{}")
        if clean in _MODIFICATION_VERBS:
            return "modification"

    # Check for explicit exploration signals
    for w in words:
        clean = w.strip(".,;:!?\"'()[]{}")
        if clean in _EXPLORATION_WORDS:
            return "exploration"

    # Default: exploration (comprehension is the safe default)
    return "exploration"


def suggest_budget(question_length: int) -> int:
    """Suggest injection budget proportional to question length.

    Short questions get smaller budgets to prevent intent distortion.
    Long questions can use more context.

    Args:
        question_length: Length of user question in characters.

    Returns:
        Recommended token budget for injection.
    """
    if question_length < 80:
        return 600
    elif question_length < 200:
        return 800
    elif question_length < 400:
        return 1200
    else:
        return 1500


# ── FTS Cascade ────────────────────────────────────────────────────────

# Import type here to avoid circular imports at module level
_SearchStrategy = Literal["AND", "REDUCED_AND", "OR_FALLBACK"]


def _drop_order(terms: List[str]) -> List[int]:
    """Return indices of terms sorted by drop priority (shortest first).

    Tie-break: later position in the original query is dropped first.
    This is a heuristic, not a guarantee — length does not equal
    discriminative power, but it is O(1) and adequate after stop-word
    removal.

    Returns:
        List of indices into ``terms``, ordered from first-to-drop to
        last-to-drop.
    """
    indexed = list(enumerate(terms))
    # Sort by (length ASC, position DESC) — shortest first, later first on tie
    indexed.sort(key=lambda x: (len(x[1]), -x[0]))
    return [i for i, _ in indexed]


def cascade_query(
    terms: List[str],
    search_and_fn: Callable[[List[str]], list],
    search_or_fn: Callable[[List[str]], list],
    min_results: int = 1,
) -> Tuple[list, _SearchStrategy, List[str], List[str]]:
    """Execute FTS query with deterministic fallback cascade.

    Cascade order:
      1. AND(all terms)
      2. AND(N-1 terms), dropping shortest term first
      3. ... repeat until AND(1 term)
      4. OR(all terms) — last resort

    Each transition is logged. The cascade is deterministic and auditable.

    Args:
        terms: Normalized query terms (non-empty).
        search_and_fn: Callable that takes terms list, returns results (AND mode).
        search_or_fn: Callable that takes terms list, returns results (OR mode).
        min_results: Minimum result count to accept (default 1).

    Returns:
        (results, strategy, effective_terms, dropped_terms)
    """
    if not terms:
        return [], "AND", [], []

    # --- Step 1: AND(all terms) ---
    results = search_and_fn(terms)
    if len(results) >= min_results:
        logger.debug(
            '[search] AND(%s) → %d hits',
            " ".join(f'"{t}"' for t in terms), len(results),
        )
        return results, "AND", list(terms), []

    logger.debug(
        '[search] AND(%s) → 0 hits',
        " ".join(f'"{t}"' for t in terms),
    )

    # --- Step 2: REDUCED_AND (drop terms one at a time) ---
    if len(terms) > 1:
        drop_indices = _drop_order(terms)
        dropped_so_far: List[str] = []

        for drop_idx in drop_indices:
            remaining = [t for i, t in enumerate(terms) if i != drop_idx
                         and i not in {drop_indices[j] for j in range(drop_indices.index(drop_idx))}]
            # Build the reduced term list by excluding all previously dropped + current
            dropped_so_far.append(terms[drop_idx])
            remaining = [t for t in terms if t not in dropped_so_far]

            if not remaining:
                break

            results = search_and_fn(remaining)
            if len(results) >= min_results:
                logger.debug(
                    '[search] REDUCED_AND(%s) → %d hits [dropped: %s]',
                    " ".join(f'"{t}"' for t in remaining),
                    len(results),
                    ", ".join(f'"{t}"' for t in dropped_so_far),
                )
                return results, "REDUCED_AND", remaining, dropped_so_far

            logger.debug(
                '[search] REDUCED_AND(%s) → 0 hits',
                " ".join(f'"{t}"' for t in remaining),
            )

    # --- Step 3: OR(all terms) — last resort ---
    results = search_or_fn(terms)
    logger.debug(
        '[search] OR_FALLBACK(%s) → %d hits [coverage-ranked]',
        " OR ".join(f'"{t}"' for t in terms), len(results),
    )
    return results, "OR_FALLBACK", list(terms), []
