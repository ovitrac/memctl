"""
Memory Item Diff — Unified diff between two items or item revisions.

Compares content (unified diff) and metadata fields, reports similarity score.
All stdlib — uses difflib.unified_diff and memctl.similarity.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

from __future__ import annotations

import difflib
from typing import Any, Dict, List, Optional, Tuple

from memctl.types import MemoryItem

# Fields compared for metadata diff (excludes volatile/internal fields)
_DIFF_FIELDS = [
    "title", "tier", "type", "tags", "validation", "confidence",
    "scope", "injectable", "archived", "expires_at",
]


def compute_diff(
    item_a: MemoryItem,
    item_b: MemoryItem,
    *,
    label_a: str = "a",
    label_b: str = "b",
    context_lines: int = 3,
) -> Dict[str, Any]:
    """
    Compute unified diff between two memory items.

    Returns a dict with:
        content_diff: list of unified diff lines (str)
        metadata_changes: list of {field, old, new} dicts
        similarity_score: float in [0.0, 1.0]
        identical: True if content and metadata match
    """
    from memctl.similarity import similarity

    # Content diff (unified)
    lines_a = item_a.content.splitlines(keepends=True)
    lines_b = item_b.content.splitlines(keepends=True)
    content_diff = list(difflib.unified_diff(
        lines_a, lines_b,
        fromfile=label_a, tofile=label_b,
        n=context_lines,
    ))

    # Metadata diff
    metadata_changes: List[Dict[str, Any]] = []
    for field in _DIFF_FIELDS:
        val_a = getattr(item_a, field, None)
        val_b = getattr(item_b, field, None)
        if val_a != val_b:
            metadata_changes.append({
                "field": field,
                "old": val_a,
                "new": val_b,
            })

    # Similarity score
    score = similarity(item_a.content, item_b.content)

    # Identical = no content diff and no metadata diff
    identical = len(content_diff) == 0 and len(metadata_changes) == 0

    return {
        "content_diff": content_diff,
        "metadata_changes": metadata_changes,
        "similarity_score": round(score, 4),
        "identical": identical,
    }


def resolve_diff_targets(
    store,
    id1: str,
    id2: str = "",
    revision: int = 0,
) -> Tuple[MemoryItem, MemoryItem, str, str]:
    """
    Resolve two diff targets from the store.

    Modes:
        - id2 set: item vs item
        - revision > 0: item vs specific revision number
        - neither: item vs its most recent revision

    Returns (item_a, item_b, label_a, label_b).
    Raises ValueError if item not found or revisions unavailable.
    """
    # Read primary item
    item_a = store.read_item(id1)
    if item_a is None:
        raise ValueError(f"Item not found: {id1}")

    if id2:
        # Mode 1: item vs item
        item_b = store.read_item(id2)
        if item_b is None:
            raise ValueError(f"Item not found: {id2}")
        return item_a, item_b, id1, id2

    # Need revisions for modes 2 and 3
    revisions = store.read_revisions(id1)
    if not revisions:
        raise ValueError(f"No revisions for {id1}")

    if revision > 0:
        # Mode 2: item vs specific revision
        rev = None
        for r in revisions:
            if r["revision_num"] == revision:
                rev = r
                break
        if rev is None:
            available = [r["revision_num"] for r in revisions]
            raise ValueError(
                f"Revision {revision} not found for {id1}. "
                f"Available: {available}"
            )
        item_b = MemoryItem.from_dict(rev["snapshot"])
        label_b = f"{id1}@rev{revision}"
        return item_a, item_b, f"{id1} (current)", label_b

    # Mode 3: item vs previous revision
    # write_item creates a revision on every call, so the latest revision
    # typically matches the current state. Compare against the penultimate
    # revision to show the actual change. Fall back to the only revision
    # if there's just one (may be identical — that's fine to report).
    if len(revisions) >= 2:
        prev_rev = revisions[-2]
    else:
        prev_rev = revisions[-1]
    item_b = MemoryItem.from_dict(prev_rev["snapshot"])
    rev_num = prev_rev["revision_num"]
    return item_a, item_b, f"{id1} (current)", f"{id1}@rev{rev_num}"
