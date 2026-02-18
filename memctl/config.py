"""
Memory Subsystem Configuration

Pruned configuration for memctl: store, policy, consolidation, proposer.
Dropped: embedder, recall, Q*-search, palace, graph, secrecy, rate-limit.
Those belong in full RAGIX.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


@dataclass
class StoreConfig:
    """SQLite store configuration."""
    db_path: str = ".memory/memory.db"
    wal_mode: bool = True
    fts_tokenizer: str = "unicode61 remove_diacritics 2"


@dataclass
class PolicyConfig:
    """Write governance configuration."""
    max_content_length: int = 2000
    secret_patterns_enabled: bool = True
    injection_patterns_enabled: bool = True
    instructional_content_enabled: bool = True
    require_provenance_for: List[str] = field(
        default_factory=lambda: ["mtm", "ltm"]
    )
    low_confidence_threshold: float = 0.3
    quarantine_expiry_hours: int = 72


@dataclass
class ConsolidateConfig:
    """Deterministic consolidation configuration."""
    enabled: bool = True
    stm_threshold: int = 20
    cluster_distance_threshold: float = 0.3
    usage_count_for_ltm: int = 5
    auto_promote_types: List[str] = field(
        default_factory=lambda: ["constraint", "decision", "definition"]
    )
    fallback_to_deterministic: bool = True


@dataclass
class ProposerConfig:
    """LLM proposal parsing configuration."""
    strategy: Literal["tool", "delimiter", "both"] = "both"
    delimiter_open: str = "<MEMORY_PROPOSALS_JSON>"
    delimiter_close: str = "</MEMORY_PROPOSALS_JSON>"
    system_instruction: str = (
        "If you see information that should persist across turns, emit "
        "a memory.propose tool call with concise items, tags, and provenance hints. "
        "Do not include secrets or raw data dumps."
    )


@dataclass
class MemoryConfig:
    """Top-level memctl configuration."""
    store: StoreConfig = field(default_factory=StoreConfig)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    consolidate: ConsolidateConfig = field(default_factory=ConsolidateConfig)
    proposer: ProposerConfig = field(default_factory=ProposerConfig)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> MemoryConfig:
        """Build config from a nested dict (e.g. YAML/JSON)."""
        return cls(
            store=StoreConfig(**d.get("store", {})),
            policy=PolicyConfig(**d.get("policy", {})),
            consolidate=ConsolidateConfig(**d.get("consolidate", {})),
            proposer=ProposerConfig(**d.get("proposer", {})),
        )
