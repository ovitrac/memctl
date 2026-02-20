"""
Export/Import — JSONL Backup, Migration, and Sharing

Export serializes memory items as one JSON object per line (JSONL).
Import reads JSONL, routes every item through policy, and deduplicates
by content_hash.

stdout purity: export writes only JSONL to stdout. Progress goes to stderr.
Policy is never bypassed: every imported item passes through evaluate_item().

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, IO, Optional

from memctl.types import MemoryItem, _generate_id, content_hash


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class ImportResult:
    """Counts from an import operation."""

    total_lines: int = 0
    imported: int = 0
    skipped_dedup: int = 0
    skipped_policy: int = 0
    errors: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_lines": self.total_lines,
            "imported": self.imported,
            "skipped_dedup": self.skipped_dedup,
            "skipped_policy": self.skipped_policy,
            "errors": self.errors,
        }


# ---------------------------------------------------------------------------
# Default log
# ---------------------------------------------------------------------------


def _default_log(msg: str) -> None:
    """Log to stderr."""
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def export_items(
    db_path: str,
    *,
    tier: Optional[str] = None,
    type_filter: Optional[str] = None,
    scope: Optional[str] = None,
    exclude_archived: bool = True,
    output: IO[str] = sys.stdout,
    log: Callable[[str], None] = _default_log,
) -> int:
    """Export memory items as JSONL (one JSON object per line).

    Args:
        db_path: Path to the SQLite database.
        tier: Filter by tier (stm/mtm/ltm). None = all.
        type_filter: Filter by type. None = all.
        scope: Filter by scope. None = all.
        exclude_archived: Exclude archived items (default: True).
        output: Writable stream for JSONL output (default: stdout).
        log: Callable for progress messages (default: stderr).

    Returns:
        Number of items exported.
    """
    from memctl.store import MemoryStore

    store = MemoryStore(db_path=db_path)
    try:
        items = store.list_items(
            tier=tier,
            type_filter=type_filter,
            scope=scope,
            exclude_archived=exclude_archived,
            limit=999999,
        )
        count = 0
        for item in items:
            line = json.dumps(item.to_dict(), ensure_ascii=False)
            output.write(line + "\n")
            count += 1
    finally:
        store.close()

    log(f"[export] {count} item(s) exported")
    return count


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def import_items(
    db_path: str,
    source: IO[str] | str,
    *,
    preserve_ids: bool = False,
    dry_run: bool = False,
    log: Callable[[str], None] = _default_log,
) -> ImportResult:
    """Import memory items from JSONL.

    Each item passes through the policy engine before storage.
    Content-hash deduplication prevents duplicate items.

    Args:
        db_path: Path to the SQLite database.
        source: File path (str) or readable IO stream.
        preserve_ids: Keep original IDs. If False, generate new IDs.
        dry_run: Count items without writing.
        log: Callable for progress messages (default: stderr).

    Returns:
        ImportResult with counts.
    """
    from memctl.store import MemoryStore
    from memctl.policy import MemoryPolicy

    result = ImportResult()

    # Open source
    if isinstance(source, str):
        fh = open(source, "r", encoding="utf-8")
        should_close_fh = True
    else:
        fh = source
        should_close_fh = False

    store = MemoryStore(db_path=db_path)
    policy = MemoryPolicy()

    # Build set of existing content hashes for dedup
    existing_hashes: set[str] = set()
    existing_ids: set[str] = set()
    existing_items = store.list_items(exclude_archived=False, limit=999999)
    for it in existing_items:
        existing_hashes.add(it.content_hash)
        existing_ids.add(it.id)

    try:
        for line in fh:
            line = line.strip()
            if not line:
                continue

            result.total_lines += 1

            # Parse
            try:
                data = json.loads(line)
            except json.JSONDecodeError as e:
                log(f"[import] Malformed JSON on line {result.total_lines}: {e}")
                result.errors += 1
                continue

            # Deserialize
            try:
                item = MemoryItem.from_dict(data)
            except (TypeError, ValueError) as e:
                log(f"[import] Invalid item on line {result.total_lines}: {e}")
                result.errors += 1
                continue

            # ID handling
            if not preserve_ids:
                item.id = _generate_id("MEM")
            elif item.id in existing_ids:
                result.skipped_dedup += 1
                continue

            # Content dedup
            ch = content_hash(item.content)
            if ch in existing_hashes:
                result.skipped_dedup += 1
                continue

            # Policy check
            verdict = policy.evaluate_item(item)
            if verdict.action == "reject":
                result.skipped_policy += 1
                continue
            if verdict.action == "quarantine":
                if getattr(verdict, "forced_non_injectable", False):
                    item.injectable = False

            # Write
            if not dry_run:
                store.write_item(item, reason="import")
                existing_hashes.add(ch)
                existing_ids.add(item.id)

            result.imported += 1

    finally:
        if should_close_fh:
            fh.close()
        store.close()

    label = " (dry run)" if dry_run else ""
    log(
        f"[import]{label} {result.imported} imported, "
        f"{result.skipped_dedup} dedup, "
        f"{result.skipped_policy} policy, "
        f"{result.errors} error(s)"
    )
    return result
