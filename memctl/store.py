"""
Memory Store — SQLite Persistent Backend

Tables:
    memory_items           - Canonical memory items (current state)
    memory_revisions       - Full revision history (append-only)
    memory_embeddings      - Vector embeddings per item
    memory_links           - Typed relationships between items
    memory_palace_locations - Palace location assignments
    memory_events          - Audit log (append-only)

Thread safety: uses sqlite3 check_same_thread=False with explicit serialization.
All writes create audit events automatically.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio | 2026-02-14
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from memctl.types import (
    CorpusMetadata,
    MemoryEvent,
    MemoryItem,
    MemoryLink,
    MemoryProvenance,
    SearchMeta,
    _generate_id,
    _now_iso,
    content_hash,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2  # v0.3: corpus_hashes extended, memory_mounts added
# Forward-compatible with RAGIX (RAGIX can open memctl DBs).
# Schema identity is not guaranteed after v0.3.

# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS memory_items (
    id              TEXT PRIMARY KEY,
    tier            TEXT NOT NULL CHECK(tier IN ('stm','mtm','ltm')),
    type            TEXT NOT NULL,
    title           TEXT NOT NULL DEFAULT '',
    content         TEXT NOT NULL DEFAULT '',
    tags            TEXT NOT NULL DEFAULT '[]',       -- JSON array
    entities        TEXT NOT NULL DEFAULT '[]',       -- JSON array
    links_json      TEXT NOT NULL DEFAULT '[]',       -- JSON array of {rel, to}
    provenance_json TEXT NOT NULL DEFAULT '{}',       -- JSON object
    confidence      REAL NOT NULL DEFAULT 0.5,
    validation      TEXT NOT NULL DEFAULT 'unverified',
    scope           TEXT NOT NULL DEFAULT 'project',
    expires_at      TEXT,
    usage_count     INTEGER NOT NULL DEFAULT 0,
    last_used_at    TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    rule_id         TEXT,
    superseded_by   TEXT,
    archived        INTEGER NOT NULL DEFAULT 0,
    content_hash    TEXT NOT NULL DEFAULT '',
    corpus_id       TEXT,              -- V3.0: corpus identity for cross-corpus ops
    injectable      INTEGER NOT NULL DEFAULT 1  -- V3.3: 0=excluded from recall/inject
);

CREATE TABLE IF NOT EXISTS memory_revisions (
    revision_id  TEXT PRIMARY KEY,
    item_id      TEXT NOT NULL,
    revision_num INTEGER NOT NULL,
    snapshot     TEXT NOT NULL,     -- full JSON of item at this revision
    changed_at   TEXT NOT NULL,
    reason       TEXT NOT NULL DEFAULT 'update'
);

CREATE TABLE IF NOT EXISTS memory_embeddings (
    item_id    TEXT PRIMARY KEY,
    model_name TEXT NOT NULL,
    dimension  INTEGER NOT NULL,
    vector     BLOB NOT NULL,      -- float32 packed bytes
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_links (
    src_id     TEXT NOT NULL,
    dst_id     TEXT NOT NULL,
    rel        TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (src_id, dst_id, rel)
);

CREATE TABLE IF NOT EXISTS memory_palace_locations (
    item_id    TEXT PRIMARY KEY,
    domain     TEXT NOT NULL DEFAULT 'default',
    room       TEXT NOT NULL DEFAULT '',
    shelf      TEXT NOT NULL DEFAULT '',
    card       TEXT NOT NULL DEFAULT '',
    assigned_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_events (
    id            TEXT PRIMARY KEY,
    action        TEXT NOT NULL,
    item_id       TEXT,
    details_json  TEXT NOT NULL DEFAULT '{}',
    content_hash  TEXT NOT NULL DEFAULT '',
    timestamp     TEXT NOT NULL
);

-- V2.4: Corpus hash registry for delta mode
-- V0.3: Extended with mount-related columns
CREATE TABLE IF NOT EXISTS corpus_hashes (
    file_path   TEXT PRIMARY KEY,
    sha256      TEXT NOT NULL,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    item_ids    TEXT NOT NULL DEFAULT '[]',  -- JSON array of memory item IDs
    ingested_at TEXT NOT NULL,
    mount_id    TEXT,                        -- FK to memory_mounts
    rel_path    TEXT,                        -- path relative to mount root
    ext         TEXT,                        -- file extension (.md, .py, ...)
    size_bytes  INTEGER,                     -- file size
    mtime_epoch INTEGER,                     -- int(stat.st_mtime), UTC
    lang_hint   TEXT                         -- fr|en|mix|null
);

-- V3.0: Corpus metadata for cross-corpus operations
CREATE TABLE IF NOT EXISTS corpus_metadata (
    corpus_id        TEXT PRIMARY KEY,
    corpus_label     TEXT NOT NULL DEFAULT '',
    parent_corpus_id TEXT,
    doc_count        INTEGER NOT NULL DEFAULT 0,
    item_count       INTEGER NOT NULL DEFAULT 0,
    scope            TEXT NOT NULL DEFAULT 'project',
    ingested_at      TEXT NOT NULL
);

-- V0.3: Mount registry for folder-level sync
CREATE TABLE IF NOT EXISTS memory_mounts (
    mount_id     TEXT PRIMARY KEY,
    path         TEXT NOT NULL UNIQUE,
    name         TEXT,
    ignore_json  TEXT NOT NULL DEFAULT '[]',
    lang_hint    TEXT,
    created_at   TEXT NOT NULL,
    last_sync_at TEXT
);

-- Schema metadata for forward compatibility
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_items_tier ON memory_items(tier);
CREATE INDEX IF NOT EXISTS idx_items_type ON memory_items(type);
CREATE INDEX IF NOT EXISTS idx_items_scope ON memory_items(scope);
CREATE INDEX IF NOT EXISTS idx_items_archived ON memory_items(archived);
CREATE INDEX IF NOT EXISTS idx_revisions_item ON memory_revisions(item_id);
CREATE INDEX IF NOT EXISTS idx_events_action ON memory_events(action);
CREATE INDEX IF NOT EXISTS idx_events_item ON memory_events(item_id);
CREATE INDEX IF NOT EXISTS idx_palace_domain ON memory_palace_locations(domain);
CREATE INDEX IF NOT EXISTS idx_palace_room ON memory_palace_locations(domain, room);
CREATE INDEX IF NOT EXISTS idx_items_corpus ON memory_items(corpus_id);
CREATE INDEX IF NOT EXISTS idx_corpus_mount ON corpus_hashes(mount_id);
"""

# ---------------------------------------------------------------------------
# FTS5 Schema (separate — requires SQLite FTS5 extension)
# ---------------------------------------------------------------------------
# Uses external-content mode: the FTS index mirrors memory_items but stores
# no duplicate data.  Three triggers keep the index in sync with the main
# table.  INSERT OR REPLACE in SQLite fires DELETE then INSERT, so both
# AFTER DELETE and AFTER INSERT triggers handle that case correctly.
# ---------------------------------------------------------------------------

# Conservative whitelist for FTS5 tokenizer strings: only alphanumeric, space,
# underscore, dot, hyphen, and digits are allowed.  Rejects quotes, semicolons,
# parentheses — prevents SQL injection via config or CLI.
_FTS_TOKENIZER_PATTERN = re.compile(r"^[a-zA-Z0-9_ .\-]+$")

# Well-known presets for --fts-tokenizer CLI flag
FTS_TOKENIZER_PRESETS = {
    "fr": "unicode61 remove_diacritics 2",
    "en": "porter unicode61 remove_diacritics 2",
    "raw": "unicode61",
}


def _validate_fts_tokenizer(tokenizer: str) -> str:
    """Validate and return a safe FTS5 tokenizer string."""
    tokenizer = tokenizer.strip()
    if not tokenizer:
        raise ValueError("FTS5 tokenizer string cannot be empty")
    if not _FTS_TOKENIZER_PATTERN.match(tokenizer):
        raise ValueError(
            f"Unsafe FTS5 tokenizer string: {tokenizer!r} — "
            "only [a-zA-Z0-9_ .-] characters allowed"
        )
    return tokenizer


def _fts5_schema_sql(tokenizer: str) -> str:
    """Generate FTS5 schema SQL with a validated tokenizer string."""
    safe = _validate_fts_tokenizer(tokenizer)
    return f"""
CREATE VIRTUAL TABLE IF NOT EXISTS memory_items_fts USING fts5(
    title, content, tags, entities,
    content='memory_items',
    content_rowid='rowid',
    tokenize='{safe}'
);

-- Sync trigger: after INSERT on memory_items, populate FTS
CREATE TRIGGER IF NOT EXISTS memory_items_fts_ai
AFTER INSERT ON memory_items BEGIN
    INSERT INTO memory_items_fts(rowid, title, content, tags, entities)
    VALUES (new.rowid, new.title, new.content, new.tags, new.entities);
END;

-- Sync trigger: before DELETE on memory_items, remove from FTS
-- (must be BEFORE DELETE so the old rowid is still accessible)
CREATE TRIGGER IF NOT EXISTS memory_items_fts_bd
BEFORE DELETE ON memory_items BEGIN
    INSERT INTO memory_items_fts(memory_items_fts, rowid, title, content, tags, entities)
    VALUES ('delete', old.rowid, old.title, old.content, old.tags, old.entities);
END;

-- Sync trigger: before UPDATE on memory_items, delete old + insert new
CREATE TRIGGER IF NOT EXISTS memory_items_fts_bu
BEFORE UPDATE ON memory_items BEGIN
    INSERT INTO memory_items_fts(memory_items_fts, rowid, title, content, tags, entities)
    VALUES ('delete', old.rowid, old.title, old.content, old.tags, old.entities);
END;

CREATE TRIGGER IF NOT EXISTS memory_items_fts_au
AFTER UPDATE ON memory_items BEGIN
    INSERT INTO memory_items_fts(rowid, title, content, tags, entities)
    VALUES (new.rowid, new.title, new.content, new.tags, new.entities);
END;
"""


# ---------------------------------------------------------------------------
# Vector packing helpers
# ---------------------------------------------------------------------------

def _pack_vector(vec: List[float]) -> bytes:
    """Pack float list to bytes (float32)."""
    import struct
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack_vector(data: bytes, dim: int) -> List[float]:
    """Unpack bytes to float list (float32)."""
    import struct
    return list(struct.unpack(f"{dim}f", data))


# ---------------------------------------------------------------------------
# Search helpers (module-level)
# ---------------------------------------------------------------------------

def _rank_by_coverage(
    items: List[MemoryItem], terms: List[str],
) -> List[MemoryItem]:
    """Rank items by how many query terms they contain.

    Used after OR-fallback to recover precision. Deterministic scoring:
    score = number of query terms found in item content (case-insensitive).

    Tie-breaking: Python's sorted() is stable, so items with equal coverage
    preserve their original FTS5 rank order (BM25).

    Complexity: O(N*M) where N = len(items), M = len(terms). Trivial at
    typical sizes (limit=10, terms<=5).
    """
    lower_terms = [t.lower() for t in terms]

    def score(item: MemoryItem) -> int:
        text = ((item.title or "") + " " + (item.content or "")).lower()
        return sum(1 for t in lower_terms if t in text)

    return sorted(items, key=lambda x: -score(x))


# ---------------------------------------------------------------------------
# MemoryStore
# ---------------------------------------------------------------------------

class MemoryStore:
    """
    SQLite-backed persistent store for memory items.

    Thread-safe via explicit lock. All mutations create audit events.
    """

    def __init__(
        self,
        db_path: str = ":memory:",
        wal_mode: bool = True,
        fts_tokenizer: Optional[str] = None,
    ):
        """Initialize SQLite-backed memory store with schema migration and FTS5.

        Args:
            db_path: SQLite database path (or ":memory:" for in-memory).
            wal_mode: Enable WAL journal mode for concurrent readers.
            fts_tokenizer: FTS5 tokenizer string.  Defaults to
                ``"unicode61 remove_diacritics 2"`` (accent-insensitive,
                good for French corpora).  Use ``"porter"`` for English
                stemming.  Must match ``[a-zA-Z0-9_ .-]+``.
        """
        self._db_path = db_path
        self._lock = threading.Lock()
        self._fts5_available: bool = False
        self._fts_tokenizer = fts_tokenizer or "unicode61 remove_diacritics 2"
        self._last_search_meta: Optional[SearchMeta] = None
        # Auto-create parent directory for disk-backed databases.
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            db_path, check_same_thread=False,
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        self._conn.row_factory = sqlite3.Row
        if wal_mode and db_path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        # V3.0 migration: add corpus_id column to existing tables
        self._migrate_v3(self._conn)
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()
        # Populate schema_meta (idempotent)
        self._conn.execute(
            "INSERT OR IGNORE INTO schema_meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self._conn.execute(
            "INSERT OR IGNORE INTO schema_meta (key, value) VALUES ('created_by', 'memctl')",
        )
        self._conn.execute(
            "INSERT OR IGNORE INTO schema_meta (key, value) VALUES ('created_at', datetime('now'))",
        )
        self._conn.commit()
        # V3.1: FTS5 full-text search (graceful fallback if unavailable)
        # V3.2: configurable tokenizer (default: unicode61 remove_diacritics 2)
        self._init_fts5()
        logger.info(
            f"MemoryStore initialized: {db_path} "
            f"(fts5={'yes' if self._fts5_available else 'no'}"
            f"{', tokenizer=' + self._fts_tokenizer if self._fts5_available else ''})"
        )

    @staticmethod
    def _migrate_v3(conn):
        """Add V3.0+ columns to pre-existing databases (safe if already present)."""
        try:
            conn.execute("ALTER TABLE memory_items ADD COLUMN corpus_id TEXT")
        except sqlite3.OperationalError:
            pass  # Column already exists
        # V3.3: injectable flag
        try:
            conn.execute(
                "ALTER TABLE memory_items ADD COLUMN injectable INTEGER NOT NULL DEFAULT 1"
            )
        except sqlite3.OperationalError:
            pass  # Column already exists
        # v0.3: Extend corpus_hashes with mount-related columns
        for col_def in (
            "mount_id TEXT",
            "rel_path TEXT",
            "ext TEXT",
            "size_bytes INTEGER",
            "mtime_epoch INTEGER",
            "lang_hint TEXT",
        ):
            try:
                conn.execute(f"ALTER TABLE corpus_hashes ADD COLUMN {col_def}")
            except sqlite3.OperationalError:
                pass  # Column already exists or table doesn't exist yet

    def _init_fts5(self) -> None:
        """
        V3.1: Create FTS5 virtual table and sync triggers.
        V3.2: Configurable tokenizer with mismatch detection.

        If the SQLite build does not include FTS5 (very old or custom builds),
        this silently sets ``_fts5_available = False`` and all full-text queries
        fall back to the LIKE-based implementation.
        """
        try:
            # Check if FTS table already exists (before CREATE IF NOT EXISTS)
            fts_existed = self._conn.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='memory_items_fts'"
            ).fetchone() is not None
            # Check for existing FTS table with different tokenizer
            self._check_fts_tokenizer_mismatch()
            self._conn.executescript(_fts5_schema_sql(self._fts_tokenizer))
            self._conn.commit()
            self._fts5_available = True
            # Persist tokenizer metadata only on fresh creation — not when
            # the table pre-existed (which would overwrite correct metadata
            # if the store is opened with a different tokenizer preset).
            if not fts_existed:
                self._conn.execute(
                    "INSERT OR REPLACE INTO schema_meta (key, value) "
                    "VALUES ('fts_tokenizer', ?)",
                    (self._fts_tokenizer,),
                )
                self._conn.execute(
                    "INSERT OR REPLACE INTO schema_meta (key, value) "
                    "VALUES ('fts_indexed_at', datetime('now'))",
                )
                self._conn.commit()
            logger.debug(
                f"FTS5 virtual table initialized (tokenizer={self._fts_tokenizer})"
            )
        except sqlite3.OperationalError as exc:
            # Typical message: "no such module: fts5"
            self._fts5_available = False
            logger.info(f"FTS5 not available, falling back to LIKE search: {exc}")

    def _check_fts_tokenizer_mismatch(self) -> None:
        """Detect if existing FTS table uses a different tokenizer than configured."""
        row = self._conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name='memory_items_fts'"
        ).fetchone()
        if row is None:
            return  # No existing FTS table — will be created fresh
        existing_sql = row[0] or ""
        configured = self._fts_tokenizer.strip()
        # Extract tokenize='...' from existing CREATE VIRTUAL TABLE
        match = re.search(r"tokenize='([^']*)'", existing_sql)
        if match:
            existing_tok = match.group(1).strip()
            if existing_tok != configured:
                logger.warning(
                    f"FTS tokenizer mismatch: existing='{existing_tok}', "
                    f"configured='{configured}'. Call rebuild_fts() to "
                    f"recreate the FTS index with the new tokenizer."
                )
        elif configured != "unicode61":
            # No tokenize clause = SQLite default (unicode61).
            # Warn only if configured tokenizer differs from that default.
            logger.warning(
                f"FTS tokenizer mismatch: existing=(default unicode61), "
                f"configured='{configured}'. Call rebuild_fts() to "
                f"recreate the FTS index with the new tokenizer."
            )

    def rebuild_fts(self, tokenizer: Optional[str] = None) -> int:
        """
        Rebuild the FTS5 index from scratch using current memory_items data.

        If *tokenizer* is provided and differs from the current tokenizer,
        the FTS table is dropped and recreated with the new tokenizer.
        Otherwise performs an in-place rebuild (``'rebuild'`` command).

        Useful after bulk imports, if the FTS index becomes stale, or when
        changing the tokenizer (e.g. switching from ``"porter"`` to
        ``"unicode61 remove_diacritics 2"``).

        Returns the number of items indexed, or -1 if FTS5 is unavailable.
        """
        if tokenizer and tokenizer.strip() != self._fts_tokenizer:
            # Tokenizer change: drop + recreate
            new_tok = _validate_fts_tokenizer(tokenizer)
            logger.info(
                f"FTS tokenizer change: '{self._fts_tokenizer}' → '{new_tok}'"
            )
            with self._lock:
                # Drop old FTS table and triggers
                self._conn.execute("DROP TABLE IF EXISTS memory_items_fts")
                for suffix in ("ai", "bd", "bu", "au"):
                    self._conn.execute(
                        f"DROP TRIGGER IF EXISTS memory_items_fts_{suffix}"
                    )
                self._conn.commit()
                self._fts_tokenizer = new_tok
            # Reinitialize with new tokenizer
            self._init_fts5()
            if not self._fts5_available:
                return -1

        if not self._fts5_available:
            logger.warning("rebuild_fts called but FTS5 is not available")
            return -1

        with self._lock:
            # Rebuild command for external-content FTS tables
            self._conn.execute(
                "INSERT INTO memory_items_fts(memory_items_fts) VALUES ('rebuild')"
            )
            count = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM memory_items"
            ).fetchone()["cnt"]
            # Update tokenizer metadata in schema_meta
            self._conn.execute(
                "INSERT OR REPLACE INTO schema_meta (key, value) "
                "VALUES ('fts_tokenizer', ?)",
                (self._fts_tokenizer,),
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO schema_meta (key, value) "
                "VALUES ('fts_indexed_at', datetime('now'))",
            )
            # Increment reindex counter
            prev = self._conn.execute(
                "SELECT value FROM schema_meta WHERE key='fts_reindex_count'"
            ).fetchone()
            reindex_count = int(prev[0]) if prev else 0
            self._conn.execute(
                "INSERT OR REPLACE INTO schema_meta (key, value) "
                "VALUES ('fts_reindex_count', ?)",
                (str(reindex_count + 1),),
            )
            self._conn.commit()
            logger.info(
                f"FTS5 index rebuilt: {count} items indexed "
                f"(tokenizer={self._fts_tokenizer})"
            )
            return count

    def _is_porter_tokenizer(self) -> bool:
        """Return True if the current tokenizer includes Porter stemming."""
        return "porter" in self._fts_tokenizer.lower()

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._lock:
            self._conn.close()

    # -- Write operations --------------------------------------------------

    def write_item(self, item: MemoryItem, reason: str = "create") -> MemoryItem:
        """
        Insert or replace a memory item. Creates revision + audit event.
        """
        with self._lock:
            item.updated_at = _now_iso()
            ch = item.content_hash
            self._conn.execute(
                """INSERT OR REPLACE INTO memory_items
                   (id, tier, type, title, content, tags, entities,
                    links_json, provenance_json, confidence, validation,
                    scope, expires_at, usage_count, last_used_at,
                    created_at, updated_at, rule_id, superseded_by, archived,
                    content_hash, corpus_id, injectable)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    item.id, item.tier, item.type, item.title, item.content,
                    json.dumps(item.tags), json.dumps(item.entities),
                    json.dumps(item.links),
                    json.dumps(item.provenance.to_dict()),
                    item.confidence, item.validation, item.scope,
                    item.expires_at, item.usage_count, item.last_used_at,
                    item.created_at, item.updated_at,
                    item.rule_id, item.superseded_by, int(item.archived), ch,
                    item.corpus_id, int(item.injectable),
                ),
            )
            # Revision
            rev_num = self._next_revision_num(item.id)
            self._conn.execute(
                """INSERT INTO memory_revisions
                   (revision_id, item_id, revision_num, snapshot, changed_at, reason)
                   VALUES (?,?,?,?,?,?)""",
                (
                    _generate_id("REV"), item.id, rev_num,
                    item.to_json(), _now_iso(), reason,
                ),
            )
            # Audit event
            self._log_event("write", item.id, {"reason": reason}, ch)
            self._conn.commit()
        return item

    def read_item(self, item_id: str) -> Optional[MemoryItem]:
        """Read a single item by ID."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM memory_items WHERE id=?", (item_id,)
            ).fetchone()
            if row is None:
                return None
            item = self._row_to_item(row)
            # Touch usage
            self._conn.execute(
                "UPDATE memory_items SET usage_count=usage_count+1, last_used_at=? WHERE id=?",
                (_now_iso(), item_id),
            )
            self._log_event("read", item_id, {}, "")
            self._conn.commit()
            return item

    def read_items(self, item_ids: List[str]) -> List[MemoryItem]:
        """Read multiple items by ID."""
        if not item_ids:
            return []
        items = []
        for iid in item_ids:
            item = self.read_item(iid)
            if item is not None:
                items.append(item)
        return items

    def update_item(self, item_id: str, patch: Dict[str, Any]) -> Optional[MemoryItem]:
        """
        Patch fields on an existing item. Creates revision.
        Does NOT allow changing id or created_at.
        """
        item = self.read_item(item_id)
        if item is None:
            return None
        forbidden = {"id", "created_at"}
        for key, val in patch.items():
            if key in forbidden:
                continue
            if key == "provenance" and isinstance(val, dict):
                item.provenance = MemoryProvenance.from_dict(val)
            elif hasattr(item, key):
                setattr(item, key, val)
        return self.write_item(item, reason="update")

    def delete_item(self, item_id: str) -> bool:
        """Soft-delete: mark archived, never physically remove."""
        return self.update_item(item_id, {"archived": True}) is not None

    def supersede_item(self, old_id: str, new_id: str) -> bool:
        """Mark old item as superseded by new item."""
        return self.update_item(
            old_id, {"superseded_by": new_id, "archived": True}
        ) is not None

    # -- Query operations --------------------------------------------------

    def search_by_tags(
        self,
        tags: List[str],
        tier: Optional[str] = None,
        type_filter: Optional[str] = None,
        scope: Optional[str] = None,
        exclude_archived: bool = True,
        limit: int = 50,
    ) -> List[MemoryItem]:
        """Search items by tag overlap (any match)."""
        with self._lock:
            conditions = []
            params: list = []
            if exclude_archived:
                conditions.append("archived=0")
            if tier:
                conditions.append("tier=?")
                params.append(tier)
            if type_filter:
                conditions.append("type=?")
                params.append(type_filter)
            if scope:
                conditions.append("scope=?")
                params.append(scope)
            where = " AND ".join(conditions) if conditions else "1=1"
            rows = self._conn.execute(
                f"SELECT * FROM memory_items WHERE {where} ORDER BY updated_at DESC LIMIT ?",
                params + [limit],
            ).fetchall()

        # Filter by tag overlap in Python (SQLite JSON support varies)
        results = []
        tag_set = set(t.lower() for t in tags)
        for row in rows:
            item = self._row_to_item(row)
            item_tags = set(t.lower() for t in item.tags)
            if tag_set & item_tags:
                results.append(item)
        return results

    def list_items(
        self,
        tier: Optional[str] = None,
        type_filter: Optional[str] = None,
        scope: Optional[str] = None,
        corpus_id: Optional[str] = None,
        exclude_archived: bool = True,
        limit: int = 100,
    ) -> List[MemoryItem]:
        """List items with optional filters."""
        with self._lock:
            conditions = []
            params: list = []
            if exclude_archived:
                conditions.append("archived=0")
            if tier:
                conditions.append("tier=?")
                params.append(tier)
            if type_filter:
                conditions.append("type=?")
                params.append(type_filter)
            if scope:
                conditions.append("scope=?")
                params.append(scope)
            if corpus_id:
                conditions.append("corpus_id=?")
                params.append(corpus_id)
            where = " AND ".join(conditions) if conditions else "1=1"
            rows = self._conn.execute(
                f"SELECT * FROM memory_items WHERE {where} ORDER BY updated_at DESC LIMIT ?",
                params + [limit],
            ).fetchall()
            return [self._row_to_item(row) for row in rows]

    def search_fulltext(
        self,
        query: str,
        tier: Optional[str] = None,
        type_filter: Optional[str] = None,
        scope: Optional[str] = None,
        corpus_id: Optional[str] = None,
        exclude_archived: bool = True,
        limit: int = 100,
    ) -> List[MemoryItem]:
        """
        Full-text search with FTS5 cascade and LIKE fallback.

        Strategy (v0.11 cascade):
            1. Normalize query (strip stop words).
            2. If FTS5 available, run cascade:
               AND(all) → REDUCED_AND(N-1) → ... → AND(1) → OR(all)
            3. On any FTS5 error, fall back to LIKE.

        The cascade is deterministic and auditable. Search metadata is
        stored in ``self._last_search_meta`` for callers who need it
        (MCP tools, audit trail). This preserves backward compatibility.

        Stop-word normalization is applied automatically: French and English
        stop words are stripped unless the query consists entirely of stop
        words (in which case the original query is preserved).
        """
        from memctl.query import cascade_query, normalize_query

        normalized = normalize_query(query)
        terms = normalized.strip().split()
        if not terms:
            self._last_search_meta = SearchMeta(
                strategy="AND", original_terms=[], effective_terms=[],
                dropped_terms=[], total_candidates=0,
            )
            return self.list_items(
                tier=tier, type_filter=type_filter, scope=scope,
                corpus_id=corpus_id, exclude_archived=exclude_archived,
                limit=limit,
            )

        # Common filter kwargs for all FTS methods
        fts_kw = dict(
            tier=tier, type_filter=type_filter, scope=scope,
            corpus_id=corpus_id, exclude_archived=exclude_archived,
            limit=limit,
        )

        if self._fts5_available:
            try:
                # Build closures for the cascade
                def and_fn(t: List[str]) -> List[MemoryItem]:
                    return self._search_fts5_and(t, **fts_kw)

                def or_fn(t: List[str]) -> List[MemoryItem]:
                    return self._search_fts5_or(t, **fts_kw)

                results, strategy, effective, dropped = cascade_query(
                    terms, and_fn, or_fn,
                )

                # PREFIX_AND: try prefix expansion before accepting OR results
                # Skipped when Porter stemming is active (redundant).
                if strategy == "OR_FALLBACK" and not self._is_porter_tokenizer():
                    prefix_results = self._search_fts5_prefix_and(terms, **fts_kw)
                    if prefix_results:
                        results = prefix_results
                        strategy = "PREFIX_AND"
                        effective = list(terms)
                        dropped = []
                        logger.debug(
                            '[search] PREFIX_AND(%s) → %d hits',
                            " ".join(f'"{t}"*' if len(t) >= self._PREFIX_MIN_LEN else f'"{t}"' for t in terms),
                            len(results),
                        )

                # OR results need coverage ranking
                if strategy == "OR_FALLBACK" and results:
                    results = _rank_by_coverage(results, terms)
                    if limit:
                        results = results[:limit]

                self._last_search_meta = SearchMeta(
                    strategy=strategy,
                    original_terms=list(terms),
                    effective_terms=effective,
                    dropped_terms=dropped,
                    total_candidates=len(results),
                )

                # Morphological miss hint (v0.12.1):
                # When the cascade fell through to a weak strategy on a
                # non-Porter tokenizer with multi-term queries, suggest
                # reindexing with stemming.
                if (
                    strategy in ("OR_FALLBACK", "PREFIX_AND", "LIKE")
                    and len(terms) > 1
                    and not self._is_porter_tokenizer()
                ):
                    self._last_search_meta.morphological_hint = (
                        "Some query terms may not match due to inflection "
                        "(e.g. 'monitored' vs 'monitoring'). "
                        "Consider: memctl reindex --tokenizer en"
                    )

                return results

            except (sqlite3.OperationalError, sqlite3.DatabaseError) as exc:
                logger.warning("FTS5 cascade failed, falling back to LIKE: %s", exc)

        # LIKE fallback (unchanged)
        results = self._search_like(
            terms, tier=tier, type_filter=type_filter,
            scope=scope, corpus_id=corpus_id,
            exclude_archived=exclude_archived, limit=limit,
        )
        self._last_search_meta = SearchMeta(
            strategy="LIKE", original_terms=list(terms),
            effective_terms=list(terms), dropped_terms=[],
            total_candidates=len(results),
        )
        return results

    def _search_fts5_and(
        self,
        terms: List[str],
        tier: Optional[str] = None,
        type_filter: Optional[str] = None,
        scope: Optional[str] = None,
        corpus_id: Optional[str] = None,
        exclude_archived: bool = True,
        limit: int = 100,
    ) -> List[MemoryItem]:
        """FTS5 AND search — all terms must co-occur in one item."""
        escaped = ['"' + t.replace('"', '""') + '"' for t in terms]
        fts_query = " AND ".join(escaped)
        return self._search_fts5_raw(
            fts_query, tier=tier, type_filter=type_filter,
            scope=scope, corpus_id=corpus_id,
            exclude_archived=exclude_archived, limit=limit,
        )

    def _search_fts5_or(
        self,
        terms: List[str],
        tier: Optional[str] = None,
        type_filter: Optional[str] = None,
        scope: Optional[str] = None,
        corpus_id: Optional[str] = None,
        exclude_archived: bool = True,
        limit: int = 100,
    ) -> List[MemoryItem]:
        """FTS5 OR search — any term matches. Results need coverage ranking."""
        escaped = ['"' + t.replace('"', '""') + '"' for t in terms]
        fts_query = " OR ".join(escaped)
        return self._search_fts5_raw(
            fts_query, tier=tier, type_filter=type_filter,
            scope=scope, corpus_id=corpus_id,
            exclude_archived=exclude_archived, limit=limit,
        )

    _PREFIX_MIN_LEN = 5  # Minimum term length for prefix expansion

    def _search_fts5_prefix_and(
        self,
        terms: List[str],
        tier: Optional[str] = None,
        type_filter: Optional[str] = None,
        scope: Optional[str] = None,
        corpus_id: Optional[str] = None,
        exclude_archived: bool = True,
        limit: int = 100,
    ) -> List[MemoryItem]:
        """FTS5 AND search with prefix expansion on eligible terms (≥5 chars)."""
        escaped = []
        for t in terms:
            safe = t.replace('"', '""')
            if len(t) >= self._PREFIX_MIN_LEN:
                escaped.append('"' + safe + '"*')
            else:
                escaped.append('"' + safe + '"')
        fts_query = " AND ".join(escaped)
        return self._search_fts5_raw(
            fts_query, tier=tier, type_filter=type_filter,
            scope=scope, corpus_id=corpus_id,
            exclude_archived=exclude_archived, limit=limit,
        )

    def _search_fts5_raw(
        self,
        fts_query: str,
        tier: Optional[str] = None,
        type_filter: Optional[str] = None,
        scope: Optional[str] = None,
        corpus_id: Optional[str] = None,
        exclude_archived: bool = True,
        limit: int = 100,
    ) -> List[MemoryItem]:
        """Execute a raw FTS5 MATCH query with filters."""
        with self._lock:
            conditions: list = []
            params: list = []
            if exclude_archived:
                conditions.append("i.archived=0")
            if tier:
                conditions.append("i.tier=?")
                params.append(tier)
            if type_filter:
                conditions.append("i.type=?")
                params.append(type_filter)
            if scope:
                conditions.append("i.scope=?")
                params.append(scope)
            if corpus_id:
                conditions.append("i.corpus_id=?")
                params.append(corpus_id)

            conditions.append("memory_items_fts MATCH ?")
            params.append(fts_query)

            where = " AND ".join(conditions)
            sql = (
                "SELECT i.* FROM memory_items i "
                "JOIN memory_items_fts fts ON i.rowid = fts.rowid "
                f"WHERE {where} "
                "ORDER BY fts.rank LIMIT ?"
            )
            params.append(limit)
            rows = self._conn.execute(sql, params).fetchall()
            return [self._row_to_item(row) for row in rows]

    def _search_like(
        self,
        terms: List[str],
        tier: Optional[str] = None,
        type_filter: Optional[str] = None,
        scope: Optional[str] = None,
        corpus_id: Optional[str] = None,
        exclude_archived: bool = True,
        limit: int = 100,
    ) -> List[MemoryItem]:
        """
        LIKE-based fallback search (original V3.0 implementation).

        Each term must appear in title OR content OR tags (AND across terms).
        """
        with self._lock:
            conditions: list = []
            params: list = []
            if exclude_archived:
                conditions.append("archived=0")
            if tier:
                conditions.append("tier=?")
                params.append(tier)
            if type_filter:
                conditions.append("type=?")
                params.append(type_filter)
            if scope:
                conditions.append("scope=?")
                params.append(scope)
            if corpus_id:
                conditions.append("corpus_id=?")
                params.append(corpus_id)

            for term in terms:
                like = f"%{term}%"
                conditions.append("(title LIKE ? OR content LIKE ? OR tags LIKE ?)")
                params.extend([like, like, like])

            where = " AND ".join(conditions) if conditions else "1=1"
            rows = self._conn.execute(
                f"SELECT * FROM memory_items WHERE {where} ORDER BY updated_at DESC LIMIT ?",
                params + [limit],
            ).fetchall()
            return [self._row_to_item(row) for row in rows]

    def count_items(
        self,
        tier: Optional[str] = None,
        scope: Optional[str] = None,
        exclude_archived: bool = True,
    ) -> int:
        """Count items matching filters."""
        with self._lock:
            conditions = []
            params: list = []
            if exclude_archived:
                conditions.append("archived=0")
            if tier:
                conditions.append("tier=?")
                params.append(tier)
            if scope:
                conditions.append("scope=?")
                params.append(scope)
            where = " AND ".join(conditions) if conditions else "1=1"
            row = self._conn.execute(
                f"SELECT COUNT(*) as cnt FROM memory_items WHERE {where}",
                params,
            ).fetchone()
            return row["cnt"]

    # -- Embeddings --------------------------------------------------------

    def write_embedding(
        self, item_id: str, vector: List[float], model_name: str, dimension: int
    ) -> None:
        """Store or update embedding for an item."""
        with self._lock:
            packed = _pack_vector(vector)
            self._conn.execute(
                """INSERT OR REPLACE INTO memory_embeddings
                   (item_id, model_name, dimension, vector, created_at)
                   VALUES (?,?,?,?,?)""",
                (item_id, model_name, dimension, packed, _now_iso()),
            )
            self._conn.commit()

    def read_embedding(self, item_id: str) -> Optional[Tuple[List[float], str]]:
        """Read embedding for an item. Returns (vector, model_name) or None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM memory_embeddings WHERE item_id=?", (item_id,)
            ).fetchone()
            if row is None:
                return None
            vec = _unpack_vector(row["vector"], row["dimension"])
            return (vec, row["model_name"])

    def all_embeddings(
        self, exclude_archived: bool = True,
    ) -> List[Tuple[str, List[float]]]:
        """Return all (item_id, vector) pairs for non-archived items."""
        with self._lock:
            if exclude_archived:
                rows = self._conn.execute(
                    """SELECT e.item_id, e.vector, e.dimension
                       FROM memory_embeddings e
                       JOIN memory_items i ON e.item_id = i.id
                       WHERE i.archived=0"""
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT item_id, vector, dimension FROM memory_embeddings"
                ).fetchall()
            return [
                (row["item_id"], _unpack_vector(row["vector"], row["dimension"]))
                for row in rows
            ]

    # -- Links -------------------------------------------------------------

    def write_link(self, link: MemoryLink) -> None:
        """Create a link between two items."""
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO memory_links
                   (src_id, dst_id, rel, created_at) VALUES (?,?,?,?)""",
                (link.src_id, link.dst_id, link.rel, link.created_at),
            )
            self._log_event(
                "link", link.src_id,
                {"dst_id": link.dst_id, "rel": link.rel}, "",
            )
            self._conn.commit()

    def read_links(self, item_id: str) -> List[MemoryLink]:
        """Get all links from or to an item."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memory_links WHERE src_id=? OR dst_id=?",
                (item_id, item_id),
            ).fetchall()
            return [
                MemoryLink(
                    src_id=r["src_id"], dst_id=r["dst_id"],
                    rel=r["rel"], created_at=r["created_at"],
                )
                for r in rows
            ]

    # -- Palace locations --------------------------------------------------

    def write_palace_location(
        self, item_id: str, domain: str, room: str, shelf: str, card: str
    ) -> None:
        """Assign a palace location to an item."""
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO memory_palace_locations
                   (item_id, domain, room, shelf, card, assigned_at)
                   VALUES (?,?,?,?,?,?)""",
                (item_id, domain, room, shelf, card, _now_iso()),
            )
            self._conn.commit()

    def read_palace_location(self, item_id: str) -> Optional[Dict[str, str]]:
        """Read palace location for an item."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM memory_palace_locations WHERE item_id=?",
                (item_id,),
            ).fetchone()
            if row is None:
                return None
            return {
                "domain": row["domain"], "room": row["room"],
                "shelf": row["shelf"], "card": row["card"],
            }

    def list_palace_locations(
        self, domain: Optional[str] = None, room: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """List palace locations with optional filters."""
        with self._lock:
            conditions = []
            params: list = []
            if domain:
                conditions.append("domain=?")
                params.append(domain)
            if room:
                conditions.append("room=?")
                params.append(room)
            where = " AND ".join(conditions) if conditions else "1=1"
            rows = self._conn.execute(
                f"SELECT * FROM memory_palace_locations WHERE {where} ORDER BY domain, room, shelf",
                params,
            ).fetchall()
            return [
                {
                    "item_id": r["item_id"], "domain": r["domain"],
                    "room": r["room"], "shelf": r["shelf"], "card": r["card"],
                }
                for r in rows
            ]

    # -- Events (audit log) ------------------------------------------------

    def read_events(
        self,
        item_id: Optional[str] = None,
        action: Optional[str] = None,
        limit: int = 100,
    ) -> List[MemoryEvent]:
        """Query audit events."""
        with self._lock:
            conditions = []
            params: list = []
            if item_id:
                conditions.append("item_id=?")
                params.append(item_id)
            if action:
                conditions.append("action=?")
                params.append(action)
            where = " AND ".join(conditions) if conditions else "1=1"
            rows = self._conn.execute(
                f"SELECT * FROM memory_events WHERE {where} ORDER BY timestamp DESC LIMIT ?",
                params + [limit],
            ).fetchall()
            return [
                MemoryEvent(
                    id=r["id"], action=r["action"], item_id=r["item_id"],
                    details=json.loads(r["details_json"]),
                    content_hash=r["content_hash"], timestamp=r["timestamp"],
                )
                for r in rows
            ]

    # -- Revisions ---------------------------------------------------------

    def read_revisions(self, item_id: str) -> List[Dict[str, Any]]:
        """Get full revision history for an item."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memory_revisions WHERE item_id=? ORDER BY revision_num",
                (item_id,),
            ).fetchall()
            return [
                {
                    "revision_id": r["revision_id"],
                    "revision_num": r["revision_num"],
                    "snapshot": json.loads(r["snapshot"]),
                    "changed_at": r["changed_at"],
                    "reason": r["reason"],
                }
                for r in rows
            ]

    # -- Stats -------------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        """Summary statistics for the memory store."""
        with self._lock:
            total = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM memory_items WHERE archived=0"
            ).fetchone()["cnt"]
            by_tier = {}
            for row in self._conn.execute(
                "SELECT tier, COUNT(*) as cnt FROM memory_items WHERE archived=0 GROUP BY tier"
            ).fetchall():
                by_tier[row["tier"]] = row["cnt"]
            by_type = {}
            for row in self._conn.execute(
                "SELECT type, COUNT(*) as cnt FROM memory_items WHERE archived=0 GROUP BY type"
            ).fetchall():
                by_type[row["type"]] = row["cnt"]
            events_count = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM memory_events"
            ).fetchone()["cnt"]
            embeddings_count = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM memory_embeddings"
            ).fetchone()["cnt"]
            # Read tokenizer metadata from schema_meta
            meta_tok = self._conn.execute(
                "SELECT value FROM schema_meta WHERE key='fts_tokenizer'"
            ).fetchone()
            meta_indexed = self._conn.execute(
                "SELECT value FROM schema_meta WHERE key='fts_indexed_at'"
            ).fetchone()
            meta_reindex = self._conn.execute(
                "SELECT value FROM schema_meta WHERE key='fts_reindex_count'"
            ).fetchone()
            stored_tok = meta_tok[0] if meta_tok else None
            mismatch = (
                stored_tok is not None and stored_tok != self._fts_tokenizer
            ) if self._fts5_available else False
            return {
                "total_items": total,
                "by_tier": by_tier,
                "by_type": by_type,
                "events_count": events_count,
                "embeddings_count": embeddings_count,
                "fts5_available": self._fts5_available,
                "fts_tokenizer": self._fts_tokenizer if self._fts5_available else None,
                "fts_tokenizer_stored": stored_tok,
                "fts_indexed_at": meta_indexed[0] if meta_indexed else None,
                "fts_reindex_count": int(meta_reindex[0]) if meta_reindex else 0,
                "fts_tokenizer_mismatch": mismatch,
            }

    def last_event(
        self,
        actions: Optional[List[str]] = None,
    ) -> Optional[str]:
        """Return ISO timestamp of the most recent event, optionally filtered.

        Args:
            actions: If provided, filter to events matching these action names.

        Returns:
            ISO-8601 timestamp string, or None if no matching events.
        """
        with self._lock:
            if actions:
                placeholders = ",".join("?" for _ in actions)
                row = self._conn.execute(
                    f"SELECT timestamp FROM memory_events "
                    f"WHERE action IN ({placeholders}) "
                    f"ORDER BY timestamp DESC LIMIT 1",
                    actions,
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT timestamp FROM memory_events "
                    "ORDER BY timestamp DESC LIMIT 1"
                ).fetchone()
            return row["timestamp"] if row else None

    # -- Reset (v0.13) -----------------------------------------------------

    def reset(self, preserve_mounts: bool = True, dry_run: bool = False) -> dict:
        """Truncate all memory content. Preserves schema and optionally mounts.

        Clears 8 content tables in a single transaction. FTS is cleared
        automatically via the BEFORE DELETE trigger on memory_items.

        Args:
            preserve_mounts: Keep memory_mounts table (default True).
            dry_run: Report counts without deleting (default False).

        Returns:
            Dict with ``dry_run`` flag and per-table counts of deleted records.
        """
        tables_to_clear = [
            "memory_items",        # triggers auto-clear memory_items_fts
            "memory_revisions",
            "memory_embeddings",
            "memory_links",
            "memory_palace_locations",
            "memory_events",
            "corpus_hashes",
            "corpus_metadata",
        ]
        if not preserve_mounts:
            tables_to_clear.append("memory_mounts")
        # schema_meta is NEVER cleared

        if dry_run:
            counts = {}
            with self._lock:
                for table in tables_to_clear:
                    row = self._conn.execute(
                        f"SELECT COUNT(*) as cnt FROM {table}"
                    ).fetchone()
                    counts[table] = row["cnt"]
            return {"dry_run": True, **counts}

        counts = {}
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                for table in tables_to_clear:
                    row = self._conn.execute(
                        f"SELECT COUNT(*) as cnt FROM {table}"
                    ).fetchone()
                    counts[table] = row["cnt"]
                    self._conn.execute(f"DELETE FROM {table}")
                # Log the reset event (written AFTER clearing memory_events)
                self._log_event("reset", None, {
                    "preserve_mounts": preserve_mounts,
                    "tables_cleared": len(tables_to_clear),
                }, "")
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return {"dry_run": False, **counts}

    # -- Export/Import -----------------------------------------------------

    def export_jsonl(self) -> str:
        """Export all non-archived items as JSONL string (one JSON object per line)."""
        items = self.list_items(exclude_archived=False, limit=100000)
        lines = [json.dumps(item.to_dict(), ensure_ascii=False) for item in items]
        return "\n".join(lines)

    def import_jsonl(self, data: str) -> int:
        """Import items from JSONL string. Returns count imported."""
        count = 0
        for line in data.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            item = MemoryItem.from_dict(d)
            self.write_item(item, reason="import")
            count += 1
        return count

    # -- Internal helpers --------------------------------------------------

    def _row_to_item(self, row: sqlite3.Row) -> MemoryItem:
        """Convert a SQLite Row to MemoryItem."""
        # Columns that may not exist in older databases
        try:
            rule_id = row["rule_id"]
        except (IndexError, KeyError):
            rule_id = None
        try:
            corpus_id = row["corpus_id"]
        except (IndexError, KeyError):
            corpus_id = None
        try:
            injectable = bool(row["injectable"])
        except (IndexError, KeyError):
            injectable = True
        return MemoryItem(
            id=row["id"],
            tier=row["tier"],
            type=row["type"],
            title=row["title"],
            content=row["content"],
            tags=json.loads(row["tags"]),
            entities=json.loads(row["entities"]),
            links=json.loads(row["links_json"]),
            provenance=MemoryProvenance.from_dict(json.loads(row["provenance_json"])),
            confidence=row["confidence"],
            validation=row["validation"],
            scope=row["scope"],
            expires_at=row["expires_at"],
            usage_count=row["usage_count"],
            last_used_at=row["last_used_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            rule_id=rule_id,
            corpus_id=corpus_id,
            superseded_by=row["superseded_by"],
            archived=bool(row["archived"]),
            injectable=injectable,
        )

    def _next_revision_num(self, item_id: str) -> int:
        """Get next revision number for an item."""
        row = self._conn.execute(
            "SELECT MAX(revision_num) as mx FROM memory_revisions WHERE item_id=?",
            (item_id,),
        ).fetchone()
        return (row["mx"] or 0) + 1

    def _log_event(
        self, action: str, item_id: Optional[str],
        details: Dict[str, Any], ch: str,
    ) -> None:
        """Write an audit event (must be called within lock)."""
        self._conn.execute(
            """INSERT INTO memory_events
               (id, action, item_id, details_json, content_hash, timestamp)
               VALUES (?,?,?,?,?,?)""",
            (
                _generate_id("EVT"), action, item_id,
                json.dumps(details), ch, _now_iso(),
            ),
        )

    # -- V2.4: Corpus hash registry ----------------------------------------

    def write_corpus_hash(
        self, file_path: str, sha256: str,
        chunk_count: int = 0, item_ids: Optional[List[str]] = None,
        *,
        mount_id: Optional[str] = None,
        rel_path: Optional[str] = None,
        ext: Optional[str] = None,
        size_bytes: Optional[int] = None,
        mtime_epoch: Optional[int] = None,
        lang_hint: Optional[str] = None,
    ) -> None:
        """Record or update the hash of an ingested corpus file."""
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO corpus_hashes
                   (file_path, sha256, chunk_count, item_ids, ingested_at,
                    mount_id, rel_path, ext, size_bytes, mtime_epoch, lang_hint)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    file_path, sha256, chunk_count,
                    json.dumps(item_ids or []), _now_iso(),
                    mount_id, rel_path, ext, size_bytes, mtime_epoch, lang_hint,
                ),
            )
            self._conn.commit()

    def read_corpus_hash(self, file_path: str) -> Optional[Dict[str, Any]]:
        """Read the stored hash for a corpus file. Returns None if not found."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM corpus_hashes WHERE file_path=?", (file_path,)
            ).fetchone()
            if row is None:
                return None
            d = {
                "file_path": row["file_path"],
                "sha256": row["sha256"],
                "chunk_count": row["chunk_count"],
                "item_ids": json.loads(row["item_ids"]),
                "ingested_at": row["ingested_at"],
            }
            # v0.3 columns (may be NULL on pre-migration rows)
            for col in ("mount_id", "rel_path", "ext", "size_bytes",
                        "mtime_epoch", "lang_hint"):
                try:
                    d[col] = row[col]
                except (IndexError, KeyError):
                    d[col] = None
            return d

    # -- V3.0: Corpus metadata ────────────────────────────────────────────────

    def write_corpus_metadata(self, meta: CorpusMetadata) -> None:
        """Insert or update corpus metadata."""
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO corpus_metadata
                   (corpus_id, corpus_label, parent_corpus_id,
                    doc_count, item_count, scope, ingested_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    meta.corpus_id, meta.corpus_label, meta.parent_corpus_id,
                    meta.doc_count, meta.item_count, meta.scope,
                    meta.ingested_at,
                ),
            )
            self._log_event(
                "corpus_register", None,
                {"corpus_id": meta.corpus_id, "parent": meta.parent_corpus_id}, "",
            )
            self._conn.commit()

    def read_corpus_metadata(self, corpus_id: str) -> Optional[CorpusMetadata]:
        """Read metadata for a specific corpus."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM corpus_metadata WHERE corpus_id=?", (corpus_id,)
            ).fetchone()
            if row is None:
                return None
            return CorpusMetadata(
                corpus_id=row["corpus_id"],
                corpus_label=row["corpus_label"],
                parent_corpus_id=row["parent_corpus_id"],
                doc_count=row["doc_count"],
                item_count=row["item_count"],
                scope=row["scope"],
                ingested_at=row["ingested_at"],
            )

    def list_corpora(self, scope: Optional[str] = None) -> List[CorpusMetadata]:
        """List all registered corpora, optionally filtered by scope."""
        with self._lock:
            if scope:
                rows = self._conn.execute(
                    "SELECT * FROM corpus_metadata WHERE scope=? ORDER BY ingested_at DESC",
                    (scope,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM corpus_metadata ORDER BY ingested_at DESC"
                ).fetchall()
            return [
                CorpusMetadata(
                    corpus_id=r["corpus_id"],
                    corpus_label=r["corpus_label"],
                    parent_corpus_id=r["parent_corpus_id"],
                    doc_count=r["doc_count"],
                    item_count=r["item_count"],
                    scope=r["scope"],
                    ingested_at=r["ingested_at"],
                )
                for r in rows
            ]

    def find_changed_files(
        self, file_registry: List[Dict[str, Any]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        V2.4 delta detection: compare file_registry against stored hashes.

        Args:
            file_registry: list of {name, path, sha256, ...} from summary_collect

        Returns:
            {"new": [...], "modified": [...], "unchanged": [...], "deleted": [...]}
        """
        result: Dict[str, List[Dict[str, Any]]] = {
            "new": [], "modified": [], "unchanged": [], "deleted": [],
        }

        current_paths = set()
        for f in file_registry:
            fpath = f.get("path", f.get("name", ""))
            current_paths.add(fpath)
            stored = self.read_corpus_hash(fpath)
            if stored is None:
                result["new"].append(f)
            elif stored["sha256"] != f.get("sha256", ""):
                result["modified"].append(f)
            else:
                result["unchanged"].append(f)

        # Find deleted files (in registry but not in current corpus)
        with self._lock:
            all_stored = self._conn.execute(
                "SELECT file_path FROM corpus_hashes"
            ).fetchall()
        for row in all_stored:
            if row["file_path"] not in current_paths:
                result["deleted"].append({"path": row["file_path"]})

        return result

    # -- v0.3: Mount registry ------------------------------------------------

    def write_mount(
        self,
        path: str,
        *,
        name: Optional[str] = None,
        ignore_patterns: Optional[List[str]] = None,
        lang_hint: Optional[str] = None,
    ) -> str:
        """Register a folder mount. Returns mount_id (existing or new)."""
        with self._lock:
            # Check for duplicate path
            row = self._conn.execute(
                "SELECT mount_id FROM memory_mounts WHERE path=?", (path,)
            ).fetchone()
            if row:
                return row["mount_id"]
            mount_id = _generate_id("MNT")
            self._conn.execute(
                """INSERT INTO memory_mounts
                   (mount_id, path, name, ignore_json, lang_hint, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (
                    mount_id, path, name,
                    json.dumps(ignore_patterns or []),
                    lang_hint, _now_iso(),
                ),
            )
            self._log_event("mount_register", None, {"mount_id": mount_id, "path": path}, "")
            self._conn.commit()
            return mount_id

    def read_mount(self, mount_id_or_path: str) -> Optional[Dict[str, Any]]:
        """Read a mount by ID or canonical path. Returns None if not found."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM memory_mounts WHERE mount_id=? OR path=?",
                (mount_id_or_path, mount_id_or_path),
            ).fetchone()
            if row is None:
                return None
            return {
                "mount_id": row["mount_id"],
                "path": row["path"],
                "name": row["name"],
                "ignore_patterns": json.loads(row["ignore_json"]),
                "lang_hint": row["lang_hint"],
                "created_at": row["created_at"],
                "last_sync_at": row["last_sync_at"],
            }

    def list_mounts(self) -> List[Dict[str, Any]]:
        """List all registered mounts."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memory_mounts ORDER BY created_at"
            ).fetchall()
            return [
                {
                    "mount_id": r["mount_id"],
                    "path": r["path"],
                    "name": r["name"],
                    "ignore_patterns": json.loads(r["ignore_json"]),
                    "lang_hint": r["lang_hint"],
                    "created_at": r["created_at"],
                    "last_sync_at": r["last_sync_at"],
                }
                for r in rows
            ]

    def remove_mount(self, mount_id_or_name: str) -> bool:
        """Remove a mount by ID or name. Returns True if deleted."""
        with self._lock:
            row = self._conn.execute(
                "SELECT mount_id FROM memory_mounts WHERE mount_id=? OR name=?",
                (mount_id_or_name, mount_id_or_name),
            ).fetchone()
            if row is None:
                return False
            mid = row["mount_id"]
            self._conn.execute("DELETE FROM memory_mounts WHERE mount_id=?", (mid,))
            self._log_event("mount_remove", None, {"mount_id": mid}, "")
            self._conn.commit()
            return True

    def update_mount_sync_time(self, mount_id: str) -> None:
        """Update last_sync_at for a mount to now."""
        with self._lock:
            self._conn.execute(
                "UPDATE memory_mounts SET last_sync_at=? WHERE mount_id=?",
                (_now_iso(), mount_id),
            )
            self._conn.commit()

    def list_corpus_files(
        self, mount_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List corpus files, optionally filtered by mount_id. For inspect."""
        with self._lock:
            if mount_id:
                rows = self._conn.execute(
                    "SELECT * FROM corpus_hashes WHERE mount_id=? ORDER BY file_path",
                    (mount_id,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM corpus_hashes ORDER BY file_path"
                ).fetchall()
            result = []
            for row in rows:
                d = {
                    "file_path": row["file_path"],
                    "sha256": row["sha256"],
                    "chunk_count": row["chunk_count"],
                    "item_ids": json.loads(row["item_ids"]),
                    "ingested_at": row["ingested_at"],
                }
                for col in ("mount_id", "rel_path", "ext", "size_bytes",
                            "mtime_epoch", "lang_hint"):
                    try:
                        d[col] = row[col]
                    except (IndexError, KeyError):
                        d[col] = None
                result.append(d)
            return result
