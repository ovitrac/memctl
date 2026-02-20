"""
Inspect — Structural Injection Block Generator

Produces deterministic, token-bounded structural summaries from corpus
metadata. No LLM calls, no embeddings, no semantic analysis.

Observation rules use hardcoded constants for reproducibility:
    DOMINANCE_FRAC       = 0.40  — folder holds >= 40% of total chunks
    LOW_DENSITY_THRESHOLD = 0.10  — bottom decile of chunks/files
    EXT_CONCENTRATION_FRAC = 0.75 — one extension >= 75% of files
    SPARSE_THRESHOLD      = 1     — folders with chunk_count <= 1 and file_count >= 3

Path normalization:
    All paths in output are mount-relative.  Files ingested via push
    (no mount) use basename only.  Absolute filesystem paths never
    appear in injection blocks or --json output.

Public API:
    inspect_path(db_path, path, ...) -> InspectResult  (orchestration: automount + autosync)
    inspect_mount(db_path, mount_path_or_name, budget) -> str
    inspect_stats(db_path, mount_path_or_name) -> dict

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

from __future__ import annotations

import logging
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Observation constants (hardcoded for determinism — frozen in v0.3)
# ---------------------------------------------------------------------------

DOMINANCE_FRAC = 0.40
LOW_DENSITY_THRESHOLD = 0.10  # bottom decile
EXT_CONCENTRATION_FRAC = 0.75
SPARSE_THRESHOLD = 1  # chunk_count <= this AND file_count >= 3

OBSERVATION_THRESHOLDS = {
    "dominance_frac": DOMINANCE_FRAC,
    "low_density_threshold": LOW_DENSITY_THRESHOLD,
    "ext_concentration_frac": EXT_CONCENTRATION_FRAC,
    "sparse_threshold": SPARSE_THRESHOLD,
}


# ---------------------------------------------------------------------------
# Path normalization helpers
# ---------------------------------------------------------------------------

def _safe_rel_path(f: Dict[str, Any]) -> str:
    """Return a portable relative path for a corpus file entry.

    Priority:
        1. rel_path (set by sync — already mount-relative)
        2. basename of file_path (set by push — no mount context)

    Never returns an absolute path.
    """
    rel = f.get("rel_path")
    if rel:
        return rel
    # Fallback: basename only (push-ingested files have no mount)
    fp = f.get("file_path", "")
    return os.path.basename(fp) or fp


def _safe_size(f: Dict[str, Any]) -> int:
    """Return file size, falling back to os.stat() if DB value is NULL/0."""
    size = f.get("size_bytes")
    if size and size > 0:
        return size
    # Attempt stat() on the original file_path
    fp = f.get("file_path", "")
    if fp and os.path.isfile(fp):
        try:
            return os.path.getsize(fp)
        except OSError:
            pass
    return 0


# ---------------------------------------------------------------------------
# Stats computation
# ---------------------------------------------------------------------------

def inspect_stats(
    db_path: str,
    mount_id: Optional[str] = None,
    inspect_config: Optional[Any] = None,
) -> Dict[str, Any]:
    """Compute structural statistics from corpus metadata.

    Args:
        db_path: Path to the SQLite database.
        mount_id: Optional mount_id to filter. None = all files.
        inspect_config: Optional InspectConfig with custom thresholds.
            If None, uses module-level hardcoded constants.

    Returns:
        Dict with total_files, total_chunks, total_size, per_folder,
        per_extension, top_largest, observations, and
        observation_thresholds.
    """
    from memctl.store import MemoryStore

    store = MemoryStore(db_path=db_path)
    try:
        files = store.list_corpus_files(mount_id=mount_id)
    finally:
        store.close()

    empty = {
        "total_files": 0,
        "total_chunks": 0,
        "total_size": 0,
        "per_folder": {},
        "per_extension": {},
        "top_largest": [],
        "observations": [],
        "observation_thresholds": dict(OBSERVATION_THRESHOLDS),
    }
    if not files:
        return empty

    # Resolve sizes (stat fallback for push-ingested files)
    for f in files:
        f["_size"] = _safe_size(f)
        f["_rel"] = _safe_rel_path(f)

    # Aggregate stats
    total_files = len(files)
    total_chunks = sum(f.get("chunk_count", 0) for f in files)
    total_size = sum(f["_size"] for f in files)

    # Per-folder stats (using normalized rel_path directory)
    folder_stats: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {"file_count": 0, "chunk_count": 0, "size": 0}
    )
    for f in files:
        folder = os.path.dirname(f["_rel"]) or "."
        folder_stats[folder]["file_count"] += 1
        folder_stats[folder]["chunk_count"] += f.get("chunk_count", 0)
        folder_stats[folder]["size"] += f["_size"]

    # Per-extension stats
    ext_stats: Dict[str, int] = defaultdict(int)
    for f in files:
        ext = f.get("ext") or os.path.splitext(f.get("file_path", ""))[1].lower()
        if ext:
            ext_stats[ext] += 1

    # Top N largest files (use normalized rel_path)
    sorted_by_size = sorted(files, key=lambda f: f["_size"], reverse=True)
    top_largest = [
        {
            "path": f["_rel"],
            "size_bytes": f["_size"],
            "chunk_count": f.get("chunk_count", 0),
        }
        for f in sorted_by_size[:5]
    ]

    # Resolve thresholds: config overrides or module constants
    if inspect_config is not None:
        thresholds = {
            "dominance_frac": inspect_config.dominance_frac,
            "low_density_threshold": inspect_config.low_density_threshold,
            "ext_concentration_frac": inspect_config.ext_concentration_frac,
            "sparse_threshold": inspect_config.sparse_threshold,
        }
    else:
        thresholds = dict(OBSERVATION_THRESHOLDS)

    # Observations
    observations = _compute_observations(
        folder_stats, ext_stats, total_chunks, total_files,
        thresholds=thresholds,
    )

    return {
        "total_files": total_files,
        "total_chunks": total_chunks,
        "total_size": total_size,
        "per_folder": dict(folder_stats),
        "per_extension": dict(ext_stats),
        "top_largest": top_largest,
        "observations": observations,
        "observation_thresholds": thresholds,
    }


def _compute_observations(
    folder_stats: Dict[str, Dict[str, int]],
    ext_stats: Dict[str, int],
    total_chunks: int,
    total_files: int,
    *,
    thresholds: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Apply observation rules to computed statistics."""
    obs: List[str] = []

    if total_chunks == 0 or total_files == 0:
        return obs

    # Resolve thresholds
    t = thresholds or OBSERVATION_THRESHOLDS
    dominance = t.get("dominance_frac", DOMINANCE_FRAC)
    low_density = t.get("low_density_threshold", LOW_DENSITY_THRESHOLD)
    ext_conc = t.get("ext_concentration_frac", EXT_CONCENTRATION_FRAC)
    sparse = t.get("sparse_threshold", SPARSE_THRESHOLD)

    # Dominance: folder with >= dominance_frac of total chunks
    for folder, stats in sorted(folder_stats.items()):
        frac = stats["chunk_count"] / total_chunks if total_chunks > 0 else 0
        if frac >= dominance:
            pct = int(frac * 100)
            obs.append(f"{folder}/ dominates content ({pct}% of chunks)")

    # Low density: folders in bottom decile of chunks/files ratio
    if len(folder_stats) >= 3:
        densities = []
        for folder, stats in folder_stats.items():
            if stats["file_count"] > 0:
                density = stats["chunk_count"] / stats["file_count"]
                densities.append((folder, density, stats["file_count"]))

        if densities:
            densities.sort(key=lambda x: x[1])
            threshold_idx = max(1, int(len(densities) * low_density))
            for folder, density, fc in densities[:threshold_idx]:
                if fc >= 3:
                    obs.append(
                        f"{folder}/ has low chunk density "
                        f"({density:.1f} chunks/file, {fc} files)"
                    )

    # Extension concentration: one ext >= ext_concentration_frac of files
    for ext, count in sorted(ext_stats.items(), key=lambda x: -x[1]):
        frac = count / total_files if total_files > 0 else 0
        if frac >= ext_conc:
            pct = int(frac * 100)
            obs.append(f"{ext} files dominate ({pct}% of all files)")

    # Sparse: folders with chunk_count <= sparse_threshold and file_count >= 3
    for folder, stats in sorted(folder_stats.items()):
        if stats["chunk_count"] <= sparse and stats["file_count"] >= 3:
            obs.append(
                f"{folder}/ is sparse "
                f"({stats['chunk_count']} chunks across {stats['file_count']} files)"
            )

    return obs


# ---------------------------------------------------------------------------
# Injection block formatting
# ---------------------------------------------------------------------------

def _format_size(size_bytes: int) -> str:
    """Human-readable file size."""
    if size_bytes <= 0:
        return "unknown"
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def inspect_mount(
    db_path: str,
    mount_id: Optional[str] = None,
    mount_label: Optional[str] = None,
    budget: int = 2200,
) -> str:
    """Generate a structural injection block.

    Deterministic: same files -> same output. No LLM calls.
    All paths are mount-relative; absolute paths never appear.

    Args:
        db_path: Path to the SQLite database.
        mount_id: Optional mount_id to filter. None = all.
        mount_label: Human label for the mount (shown in header).
        budget: Token budget (approximate, chars/4).

    Returns:
        Formatted injection block string.
    """
    stats = inspect_stats(db_path, mount_id=mount_id)

    if stats["total_files"] == 0:
        return "## Structure (Injected)\nNo files found.\n"

    lines: List[str] = []
    lines.append("## Structure (Injected)")
    lines.append("format_version: 1")
    lines.append("injection_type: structure_inspect")
    if mount_label:
        lines.append(f"mount: {mount_label}")
    lines.append("")

    # Summary
    lines.append(f"Total files: {stats['total_files']}")
    lines.append(f"Total chunks: {stats['total_chunks']}")
    lines.append(f"Total size: {_format_size(stats['total_size'])}")
    lines.append("")

    # Top-level folders (sorted by chunk count descending)
    if stats["per_folder"]:
        lines.append("Folders:")
        sorted_folders = sorted(
            stats["per_folder"].items(),
            key=lambda x: x[1]["chunk_count"],
            reverse=True,
        )
        for folder, fs in sorted_folders:
            lines.append(
                f"- {folder}/ ({fs['file_count']} files, "
                f"{fs['chunk_count']} chunks, {_format_size(fs['size'])})"
            )
        lines.append("")

    # Largest files
    if stats["top_largest"]:
        lines.append("Largest files:")
        for f in stats["top_largest"]:
            lines.append(
                f"- {f['path']} ({_format_size(f['size_bytes'])}, "
                f"{f['chunk_count']} chunks)"
            )
        lines.append("")

    # Extensions
    if stats["per_extension"]:
        lines.append("Extensions:")
        for ext, count in sorted(stats["per_extension"].items(), key=lambda x: -x[1]):
            lines.append(f"- {ext}: {count}")
        lines.append("")

    # Observations
    if stats["observations"]:
        lines.append("Observations:")
        for o in stats["observations"]:
            lines.append(f"- {o}")
        lines.append("")

    text = "\n".join(lines)

    # Budget trimming (approximate: 4 chars ~ 1 token)
    max_chars = budget * 4
    if len(text) > max_chars:
        text = text[:max_chars].rsplit("\n", 1)[0] + "\n[...truncated]\n"

    return text


# ---------------------------------------------------------------------------
# Orchestration: inspect_path (automount + autosync + inspect)
# ---------------------------------------------------------------------------

@dataclass
class InspectResult:
    """Result of an inspect_path() orchestration call.

    Contains the structured stats dict (same keys as inspect_stats()) plus
    metadata about what the orchestrator did (mount, sync).
    """
    stats: Dict[str, Any]
    mount_id: str
    mount_label: str
    was_mounted: bool = False
    was_synced: bool = False
    sync_skipped: bool = False
    was_ephemeral: bool = False
    sync_files_new: int = 0
    sync_files_changed: int = 0
    sync_files_unchanged: int = 0
    sync_chunks_created: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dict.  Stats fields are inlined at root level."""
        d = dict(self.stats)
        d.update({
            "mount_id": self.mount_id,
            "mount_label": self.mount_label,
            "was_mounted": self.was_mounted,
            "was_synced": self.was_synced,
            "sync_skipped": self.sync_skipped,
            "was_ephemeral": self.was_ephemeral,
            "sync_files_new": self.sync_files_new,
            "sync_files_changed": self.sync_files_changed,
            "sync_files_unchanged": self.sync_files_unchanged,
            "sync_chunks_created": self.sync_chunks_created,
        })
        return d


def _default_log(msg: str) -> None:
    """Default log callable: writes to stderr."""
    print(msg, file=sys.stderr)


def _is_stale(
    store,
    mount: Dict[str, Any],
    ignore_patterns: Optional[List[str]] = None,
) -> bool:
    """Tier 0 staleness check: compare disk inventory vs stored corpus_hashes.

    Returns True if sync is needed, False if the store is up-to-date.

    Algorithm:
        1. If last_sync_at is None -> never synced -> stale.
        2. Scan disk (stat only, no hashing) via scan_mount().
        3. Build (abs_path, size_bytes, mtime_epoch) triple sets from disk
           and from stored corpus_hashes.
        4. If sets are equal -> fresh.  If different -> stale.

    This is O(n) in file count, uses only stat() -- no hashing.
    """
    from memctl.sync import scan_mount

    if mount.get("last_sync_at") is None:
        return True

    canonical = mount["path"]
    patterns = (
        ignore_patterns
        if ignore_patterns is not None
        else mount.get("ignore_patterns", [])
    )

    # Disk inventory (stat only -- scan_mount does not hash)
    scan = scan_mount(canonical, ignore_patterns=patterns)
    disk_triples = {
        (fi.abs_path, fi.size_bytes, fi.mtime_epoch)
        for fi in scan.files
    }

    # Stored inventory from corpus_hashes for this mount
    stored_files = store.list_corpus_files(mount_id=mount["mount_id"])
    stored_triples = {
        (f["file_path"], f["size_bytes"], f["mtime_epoch"])
        for f in stored_files
        if f.get("size_bytes") is not None and f.get("mtime_epoch") is not None
    }

    return disk_triples != stored_triples


def inspect_path(
    db_path: str,
    path: str,
    *,
    sync_mode: str = "auto",
    mount_mode: str = "persist",
    budget: int = 2200,
    ignore_patterns: Optional[List[str]] = None,
    log: Callable[[str], None] = _default_log,
) -> InspectResult:
    """Orchestrate mount + sync + inspect for a filesystem path.

    Makes ``memctl inspect <path>`` work without manual mount/sync steps.
    All implicit actions are announced via the *log* callable.

    Args:
        db_path:          Path to the SQLite database.
        path:             Filesystem directory to inspect.
        sync_mode:        "auto" (sync if stale), "always", or "never".
        mount_mode:       "persist" (keep mount) or "ephemeral" (remove after).
        budget:           Token budget for the injection block.
        ignore_patterns:  Glob patterns to exclude.  None uses mount's patterns.
        log:              Callable for informational messages (default: stderr).

    Returns:
        InspectResult with stats, mount metadata, and sync summary.

    Raises:
        FileNotFoundError:   If path does not exist.
        NotADirectoryError:  If path is not a directory.
        ValueError:          If sync_mode or mount_mode is invalid.
    """
    from memctl.mount import register_mount, remove_mount as _remove_mount
    from memctl.store import MemoryStore
    from memctl.sync import sync_mount

    # Validate arguments
    if sync_mode not in ("auto", "always", "never"):
        raise ValueError(
            f"Invalid sync_mode: {sync_mode!r}. Expected auto|always|never."
        )
    if mount_mode not in ("persist", "ephemeral"):
        raise ValueError(
            f"Invalid mount_mode: {mount_mode!r}. Expected persist|ephemeral."
        )

    canonical = os.path.realpath(path)
    if not os.path.exists(canonical):
        raise FileNotFoundError(f"Path does not exist: {canonical}")
    if not os.path.isdir(canonical):
        raise NotADirectoryError(f"Path is not a directory: {canonical}")

    result_was_mounted = False
    result_was_synced = False
    result_sync_skipped = False
    sync_new = sync_changed = sync_unchanged = sync_chunks = 0

    # -- Step 1: Ensure path is mounted --
    store = MemoryStore(db_path=db_path)
    try:
        mount = store.read_mount(canonical)
        if mount is None:
            store.close()
            mount_id = register_mount(
                db_path, canonical,
                ignore_patterns=ignore_patterns,
            )
            result_was_mounted = True
            log(f"[inspect] Mounted: {canonical}")
            store = MemoryStore(db_path=db_path)
            mount = store.read_mount(canonical)
        else:
            mount_id = mount["mount_id"]

        effective_patterns = (
            ignore_patterns
            if ignore_patterns is not None
            else mount.get("ignore_patterns", [])
        )

        # -- Step 2: Staleness check + sync decision --
        do_sync = False
        if sync_mode == "always":
            do_sync = True
            log(f"[inspect] sync=always — syncing {canonical}")
        elif sync_mode == "never":
            result_sync_skipped = True
            log(f"[inspect] sync=never — skipping sync")
        else:
            # auto: Tier 0 check
            stale = _is_stale(store, mount, ignore_patterns=effective_patterns)
            if stale:
                do_sync = True
                log(f"[inspect] Store is stale — syncing {canonical}")
            else:
                result_sync_skipped = True
                log(f"[inspect] Store is up-to-date — skipping sync")

        # Close store before sync (sync_mount opens its own)
        store.close()
        store = None

        # -- Step 3: Sync if needed --
        if do_sync:
            sr = sync_mount(
                db_path, canonical,
                delta=True,
                ignore_patterns=effective_patterns,
                quiet=True,
            )
            result_was_synced = True
            sync_new = sr.files_new
            sync_changed = sr.files_changed
            sync_unchanged = sr.files_unchanged
            sync_chunks = sr.chunks_created
            log(
                f"[inspect] Synced: {sync_new} new, "
                f"{sync_changed} changed, "
                f"{sync_unchanged} unchanged, "
                f"{sync_chunks} chunks"
            )

        # -- Step 4: Inspect --
        stats = inspect_stats(db_path, mount_id=mount_id)
        mount_label = mount.get("name") or canonical

        # -- Step 5: Ephemeral cleanup --
        if mount_mode == "ephemeral":
            _remove_mount(db_path, mount_id)
            log(f"[inspect] Ephemeral: mount removed")

    except Exception:
        if store is not None:
            store.close()
        raise

    return InspectResult(
        stats=stats,
        mount_id=mount_id,
        mount_label=mount_label,
        was_mounted=result_was_mounted,
        was_synced=result_was_synced,
        sync_skipped=result_sync_skipped,
        was_ephemeral=(mount_mode == "ephemeral"),
        sync_files_new=sync_new,
        sync_files_changed=sync_changed,
        sync_files_unchanged=sync_unchanged,
        sync_chunks_created=sync_chunks,
    )
