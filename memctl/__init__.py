"""
memctl — A Unix-native memory control plane for LLM orchestration.

One file, one truth. All memory is in a single SQLite + FTS5 + WAL database.
Policy-governed, content-addressed, and forward-compatible with RAGIX.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

__version__ = "0.6.0"

from memctl.types import (
    MemoryItem,
    MemoryProposal,
    MemoryEvent,
    MemoryLink,
    MemoryProvenance,
    CorpusMetadata,
)
from memctl.store import MemoryStore, SCHEMA_VERSION
from memctl.policy import MemoryPolicy
from memctl.config import MemoryConfig

__all__ = [
    "__version__",
    "MemoryItem",
    "MemoryProposal",
    "MemoryEvent",
    "MemoryLink",
    "MemoryProvenance",
    "CorpusMetadata",
    "MemoryStore",
    "MemoryPolicy",
    "MemoryConfig",
    "SCHEMA_VERSION",
]
