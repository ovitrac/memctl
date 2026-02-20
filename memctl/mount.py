"""
Mount — Folder Registration for Structured Sync

Registers folders as mount points in the memory store.
Mount registration is metadata-only — no file scanning, no ingestion.
Content sync is handled separately by sync.py.

Public API:
    register_mount(db_path, folder_path, ...) -> mount_id
    list_mounts(db_path) -> list[dict]
    remove_mount(db_path, mount_id_or_name) -> bool

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional

logger = logging.getLogger(__name__)


def register_mount(
    db_path: str,
    folder_path: str,
    *,
    name: Optional[str] = None,
    ignore_patterns: Optional[List[str]] = None,
    lang_hint: Optional[str] = None,
) -> str:
    """Register a folder as a mount point.

    Resolves folder_path to an absolute canonical path. If the same canonical
    path is already registered, returns the existing mount_id (idempotent).

    Args:
        db_path: Path to the SQLite database.
        folder_path: Folder to register.
        name: Optional human-readable label.
        ignore_patterns: Glob patterns to exclude during sync.
        lang_hint: Language hint (fr|en|mix|None).

    Returns:
        mount_id (MNT-xxxxxxxxxxxx).

    Raises:
        FileNotFoundError: If folder_path does not exist.
        NotADirectoryError: If folder_path is not a directory.
    """
    from memctl.store import MemoryStore

    canonical = os.path.realpath(folder_path)

    if not os.path.exists(canonical):
        raise FileNotFoundError(f"Mount path does not exist: {canonical}")
    if not os.path.isdir(canonical):
        raise NotADirectoryError(f"Mount path is not a directory: {canonical}")

    store = MemoryStore(db_path=db_path)
    try:
        mount_id = store.write_mount(
            canonical,
            name=name,
            ignore_patterns=ignore_patterns,
            lang_hint=lang_hint,
        )
    finally:
        store.close()

    logger.info("Registered mount %s → %s", mount_id, canonical)
    return mount_id


def list_mounts(db_path: str) -> list:
    """List all registered mounts.

    Args:
        db_path: Path to the SQLite database.

    Returns:
        List of mount dicts with keys: mount_id, path, name,
        ignore_patterns, lang_hint, created_at, last_sync_at.
    """
    from memctl.store import MemoryStore

    store = MemoryStore(db_path=db_path)
    try:
        return store.list_mounts()
    finally:
        store.close()


def remove_mount(db_path: str, mount_id_or_name: str) -> bool:
    """Remove a mount by ID or name.

    Args:
        db_path: Path to the SQLite database.
        mount_id_or_name: Mount ID (MNT-...) or human label.

    Returns:
        True if a mount was removed, False if not found.
    """
    from memctl.store import MemoryStore

    store = MemoryStore(db_path=db_path)
    try:
        return store.remove_mount(mount_id_or_name)
    finally:
        store.close()
