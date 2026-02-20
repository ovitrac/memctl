"""
Sync — File Scanning and Delta Synchronization

Scans mounted folders, detects changes via a 3-tier delta rule:
  1. file_path not in corpus_hashes → new → ingest
  2. size_bytes AND mtime_epoch unchanged → fast skip (no hashing)
  3. compute sha256 → same → update metadata, skip ingest;
                       different → ingest new chunks

Public API:
    scan_mount(db_path, mount_path, ignore_patterns) -> ScanResult
    sync_mount(db_path, mount_path, ...) -> SyncResult
    sync_all(db_path, ...) -> dict[str, SyncResult]

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

from __future__ import annotations

import fnmatch
import hashlib
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from memctl.extract import ALL_INGESTABLE_EXTS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class FileInfo:
    """Metadata for a single scanned file."""
    abs_path: str
    rel_path: str
    ext: str
    size_bytes: int
    mtime_epoch: int
    sha256: Optional[str] = None  # computed lazily (only when needed)


@dataclass
class ScanResult:
    """Result of scanning a mount folder."""
    mount_path: str
    files: List[FileInfo] = field(default_factory=list)
    total_size: int = 0
    extensions: Dict[str, int] = field(default_factory=dict)


@dataclass
class SyncResult:
    """Result of syncing a mount."""
    mount_path: str
    files_scanned: int = 0
    files_new: int = 0
    files_changed: int = 0
    files_unchanged: int = 0
    chunks_created: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mount_path": self.mount_path,
            "files_scanned": self.files_scanned,
            "files_new": self.files_new,
            "files_changed": self.files_changed,
            "files_unchanged": self.files_unchanged,
            "chunks_created": self.chunks_created,
        }


# ---------------------------------------------------------------------------
# File hashing
# ---------------------------------------------------------------------------

def _file_sha256(path: str) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Ignore matching
# ---------------------------------------------------------------------------

def _is_ignored(rel_path: str, patterns: List[str]) -> bool:
    """Check if a relative path matches any ignore pattern."""
    for pattern in patterns:
        if fnmatch.fnmatch(rel_path, pattern):
            return True
        # Also match against basename for simple patterns like "*.log"
        if fnmatch.fnmatch(os.path.basename(rel_path), pattern):
            return True
    return False


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------

def scan_mount(
    mount_path: str,
    ignore_patterns: Optional[List[str]] = None,
) -> ScanResult:
    """Scan a folder for ingestable files.

    Walks the directory tree, collects metadata (size, mtime, extension)
    for each file with a known extension.  SHA-256 is NOT computed here
    (deferred to sync for efficiency).

    Args:
        mount_path: Absolute canonical path to the folder.
        ignore_patterns: Glob patterns to exclude.

    Returns:
        ScanResult with file metadata (sha256=None until sync computes it).
    """
    patterns = ignore_patterns or []
    result = ScanResult(mount_path=mount_path)
    exts: Dict[str, int] = {}

    for root, _dirs, files in os.walk(mount_path):
        for fname in sorted(files):
            abs_path = os.path.join(root, fname)
            rel_path = os.path.relpath(abs_path, mount_path)

            # Skip ignored
            if _is_ignored(rel_path, patterns):
                continue

            # Extension filter
            ext = Path(fname).suffix.lower()
            if ext not in ALL_INGESTABLE_EXTS:
                continue

            try:
                stat = os.stat(abs_path)
            except OSError:
                logger.warning("Cannot stat %s, skipping", abs_path)
                continue

            fi = FileInfo(
                abs_path=abs_path,
                rel_path=rel_path,
                ext=ext,
                size_bytes=stat.st_size,
                mtime_epoch=int(stat.st_mtime),
                sha256=None,  # deferred — computed only when needed
            )
            result.files.append(fi)
            result.total_size += stat.st_size
            exts[ext] = exts.get(ext, 0) + 1

    result.extensions = exts
    return result


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

def sync_mount(
    db_path: str,
    mount_path: str,
    *,
    delta: bool = True,
    ignore_patterns: Optional[List[str]] = None,
    lang_hint: Optional[str] = None,
    max_tokens: int = 1800,
    quiet: bool = False,
) -> SyncResult:
    """Sync a folder into the memory store.

    Auto-registers the mount if not already registered.

    Delta mode (default) uses a 3-tier rule:
      1. file_path not in DB → new → ingest
      2. size_bytes AND mtime_epoch unchanged → fast skip (no hashing)
      3. compute sha256 → same → update metadata only; different → ingest

    Full mode (delta=False): re-processes everything.

    Args:
        db_path: Path to the SQLite database.
        mount_path: Folder to sync.
        delta: If True, skip unchanged files.
        ignore_patterns: Override ignore patterns (else uses mount's patterns).
        lang_hint: Language hint for new mount registration.
        max_tokens: Max tokens per chunk for ingestion.
        quiet: Suppress stderr progress.

    Returns:
        SyncResult with counts.
    """
    from memctl.ingest import ingest_file
    from memctl.mount import register_mount
    from memctl.store import MemoryStore

    canonical = os.path.realpath(mount_path)
    result = SyncResult(mount_path=canonical)

    # Open store
    store = MemoryStore(db_path=db_path)
    try:
        # Auto-register mount if missing
        mount = store.read_mount(canonical)
        if mount is None:
            mount_id = register_mount(
                db_path, canonical,
                ignore_patterns=ignore_patterns,
                lang_hint=lang_hint,
            )
            # Re-open store (register_mount opens its own)
            mount = store.read_mount(canonical)
        else:
            mount_id = mount["mount_id"]

        # Use mount's ignore patterns if none provided
        patterns = ignore_patterns if ignore_patterns is not None else mount["ignore_patterns"]
        mount_lang = lang_hint or mount.get("lang_hint")

        # Scan (no hashing — just stat)
        scan = scan_mount(canonical, patterns)
        result.files_scanned = len(scan.files)

        if not quiet:
            print(
                f"[sync] Scanned {len(scan.files)} files in {canonical}",
                file=sys.stderr,
            )

        # Process each file with 3-tier delta rule
        for fi in scan.files:
            if delta:
                existing = store.read_corpus_hash(fi.abs_path)
                if existing:
                    stored_size = existing.get("size_bytes")
                    stored_mtime = existing.get("mtime_epoch")
                    # Tier 2: size + mtime unchanged → fast skip
                    if (stored_size == fi.size_bytes
                            and stored_mtime == fi.mtime_epoch):
                        result.files_unchanged += 1
                        continue
                    # Tier 3: size or mtime changed → hash to confirm
                    fi.sha256 = _file_sha256(fi.abs_path)
                    if existing.get("sha256") == fi.sha256:
                        # Content identical — update metadata only
                        store.write_corpus_hash(
                            fi.abs_path, fi.sha256,
                            existing.get("chunk_count", 0),
                            existing.get("item_ids", []),
                            mount_id=mount_id,
                            rel_path=fi.rel_path,
                            ext=fi.ext,
                            size_bytes=fi.size_bytes,
                            mtime_epoch=fi.mtime_epoch,
                            lang_hint=mount_lang,
                        )
                        result.files_unchanged += 1
                        continue
                    # Hash differs → fall through to ingest
                    result.files_changed += 1
                else:
                    # Tier 1: not in DB → new
                    result.files_new += 1
            else:
                # Full mode: always ingest
                existing = store.read_corpus_hash(fi.abs_path)
                if existing is None:
                    result.files_new += 1
                else:
                    result.files_changed += 1

            # Compute hash if not yet done
            if fi.sha256 is None:
                fi.sha256 = _file_sha256(fi.abs_path)

            # Ingest through existing pipeline
            ingest_result = ingest_file(
                store, fi.abs_path,
                scope="project",
                max_tokens=max_tokens,
                tags=[],
                format_mode="auto",
                injectable=True,
            )

            # Update corpus_hash with mount metadata
            if ingest_result.chunks_created > 0 or ingest_result.files_processed > 0:
                store.write_corpus_hash(
                    fi.abs_path,
                    fi.sha256,
                    ingest_result.chunks_created,
                    ingest_result.item_ids,
                    mount_id=mount_id,
                    rel_path=fi.rel_path,
                    ext=fi.ext,
                    size_bytes=fi.size_bytes,
                    mtime_epoch=fi.mtime_epoch,
                    lang_hint=mount_lang,
                )
                result.chunks_created += ingest_result.chunks_created

        # Update sync timestamp
        store.update_mount_sync_time(mount_id)

        if not quiet:
            print(
                f"[sync] Done: {result.files_new} new, {result.files_changed} changed, "
                f"{result.files_unchanged} unchanged, {result.chunks_created} chunks",
                file=sys.stderr,
            )

    finally:
        store.close()

    return result


def sync_all(
    db_path: str,
    *,
    delta: bool = True,
    max_tokens: int = 1800,
    quiet: bool = False,
) -> Dict[str, SyncResult]:
    """Sync all registered mounts.

    Args:
        db_path: Path to the SQLite database.
        delta: If True, skip unchanged files.
        max_tokens: Max tokens per chunk.
        quiet: Suppress stderr progress.

    Returns:
        Dict mapping mount_path -> SyncResult.
    """
    from memctl.mount import list_mounts

    mounts = list_mounts(db_path)
    results: Dict[str, SyncResult] = {}

    for m in mounts:
        path = m["path"]
        if not os.path.isdir(path):
            if not quiet:
                print(f"[sync] Mount path missing, skipping: {path}", file=sys.stderr)
            continue
        results[path] = sync_mount(
            db_path, path,
            delta=delta,
            ignore_patterns=m["ignore_patterns"],
            lang_hint=m.get("lang_hint"),
            max_tokens=max_tokens,
            quiet=quiet,
        )

    return results
