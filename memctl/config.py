"""
Memory Subsystem Configuration

Configuration dataclasses for memctl: store, policy, consolidation, proposer,
inspect thresholds, and chat settings.  Includes load_config() for reading
a JSON config file with silent fallback to compiled defaults.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

from __future__ import annotations

import json
import sys
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
class InspectConfig:
    """Observation threshold configuration for inspect."""
    dominance_frac: float = 0.40
    low_density_threshold: float = 0.10
    ext_concentration_frac: float = 0.75
    sparse_threshold: int = 1


@dataclass
class ChatConfig:
    """Chat REPL configuration."""
    history_max: int = 1000


@dataclass
class MemoryConfig:
    """Top-level memctl configuration."""
    store: StoreConfig = field(default_factory=StoreConfig)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    consolidate: ConsolidateConfig = field(default_factory=ConsolidateConfig)
    proposer: ProposerConfig = field(default_factory=ProposerConfig)
    inspect: InspectConfig = field(default_factory=InspectConfig)
    chat: ChatConfig = field(default_factory=ChatConfig)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> MemoryConfig:
        """Build config from a nested dict (e.g. JSON)."""
        kwargs: Dict[str, Any] = {}
        if "store" in d:
            kwargs["store"] = StoreConfig(**d["store"])
        if "policy" in d:
            kwargs["policy"] = PolicyConfig(**d["policy"])
        if "consolidate" in d:
            kwargs["consolidate"] = ConsolidateConfig(**d["consolidate"])
        if "proposer" in d:
            kwargs["proposer"] = ProposerConfig(**d["proposer"])
        if "inspect" in d:
            kwargs["inspect"] = InspectConfig(**d["inspect"])
        if "chat" in d:
            kwargs["chat"] = ChatConfig(**d["chat"])
        return cls(**kwargs)


def load_config(path: Optional[str] = None) -> MemoryConfig:
    """Load config from a JSON file. Returns defaults if file missing/invalid.

    Args:
        path: Path to config.json. If None, returns compiled defaults.

    Returns:
        MemoryConfig with values from file or defaults.
    """
    if path is None:
        return MemoryConfig()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return MemoryConfig.from_dict(data)
    except (FileNotFoundError, json.JSONDecodeError, TypeError, KeyError):
        return MemoryConfig()
