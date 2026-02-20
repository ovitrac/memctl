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


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class ValidationError(ValueError):
    """Raised when config values are out of valid range."""

    pass


def _check_range(
    errors: List[str], name: str, value, lo, hi, typ=None,
) -> None:
    """Append an error message if value is out of [lo, hi] or wrong type."""
    if typ is not None and not isinstance(value, typ):
        errors.append(f"{name}: expected {typ.__name__}, got {type(value).__name__}")
        return
    if value < lo or value > hi:
        errors.append(f"{name}: {value} not in [{lo}, {hi}]")


@dataclass
class StoreConfig:
    """SQLite store configuration."""
    db_path: str = ".memory/memory.db"
    wal_mode: bool = True
    fts_tokenizer: str = "unicode61 remove_diacritics 2"

    def validate(self) -> List[str]:
        """Return list of validation error messages (empty = valid)."""
        return []


@dataclass
class PolicyConfig:
    """Write governance configuration."""
    max_content_length: int = 2000
    secret_patterns_enabled: bool = True
    injection_patterns_enabled: bool = True
    instructional_content_enabled: bool = True
    pii_patterns_enabled: bool = True
    require_provenance_for: List[str] = field(
        default_factory=lambda: ["mtm", "ltm"]
    )
    low_confidence_threshold: float = 0.3
    quarantine_expiry_hours: int = 72

    def validate(self) -> List[str]:
        """Return list of validation error messages (empty = valid)."""
        errors: List[str] = []
        _check_range(errors, "policy.max_content_length",
                      self.max_content_length, 100, 100000, int)
        _check_range(errors, "policy.low_confidence_threshold",
                      self.low_confidence_threshold, 0.0, 1.0, float)
        _check_range(errors, "policy.quarantine_expiry_hours",
                      self.quarantine_expiry_hours, 1, 8760, int)
        return errors


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

    def validate(self) -> List[str]:
        """Return list of validation error messages (empty = valid)."""
        errors: List[str] = []
        _check_range(errors, "consolidate.cluster_distance_threshold",
                      self.cluster_distance_threshold, 0.0, 1.0, float)
        _check_range(errors, "consolidate.stm_threshold",
                      self.stm_threshold, 1, 10000, int)
        _check_range(errors, "consolidate.usage_count_for_ltm",
                      self.usage_count_for_ltm, 1, 1000, int)
        return errors


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

    def validate(self) -> List[str]:
        """Return list of validation error messages (empty = valid)."""
        return []


@dataclass
class InspectConfig:
    """Observation threshold configuration for inspect."""
    dominance_frac: float = 0.40
    low_density_threshold: float = 0.10
    ext_concentration_frac: float = 0.75
    sparse_threshold: int = 1

    def validate(self) -> List[str]:
        """Return list of validation error messages (empty = valid)."""
        errors: List[str] = []
        _check_range(errors, "inspect.dominance_frac",
                      self.dominance_frac, 0.01, 1.0, float)
        _check_range(errors, "inspect.low_density_threshold",
                      self.low_density_threshold, 0.0, 1.0, float)
        _check_range(errors, "inspect.ext_concentration_frac",
                      self.ext_concentration_frac, 0.01, 1.0, float)
        _check_range(errors, "inspect.sparse_threshold",
                      self.sparse_threshold, 0, 100, int)
        return errors


@dataclass
class ChatConfig:
    """Chat REPL configuration."""
    history_max: int = 1000

    def validate(self) -> List[str]:
        """Return list of validation error messages (empty = valid)."""
        errors: List[str] = []
        _check_range(errors, "chat.history_max",
                      self.history_max, 10, 100000, int)
        return errors


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

    def validate(self) -> List[str]:
        """Validate all config sections. Returns list of error messages."""
        errors: List[str] = []
        errors.extend(self.store.validate())
        errors.extend(self.policy.validate())
        errors.extend(self.consolidate.validate())
        errors.extend(self.proposer.validate())
        errors.extend(self.inspect.validate())
        errors.extend(self.chat.validate())
        return errors


def load_config(
    path: Optional[str] = None, *, strict: bool = False,
) -> MemoryConfig:
    """Load config from a JSON file. Returns defaults if file missing/invalid.

    Args:
        path: Path to config.json. If None, returns compiled defaults.
        strict: If True, raise ValidationError on invalid config values.

    Returns:
        MemoryConfig with values from file or defaults.

    Raises:
        ValidationError: If strict=True and config values are out of range.
    """
    if path is None:
        cfg = MemoryConfig()
    else:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg = MemoryConfig.from_dict(data)
        except (FileNotFoundError, json.JSONDecodeError, TypeError, KeyError):
            cfg = MemoryConfig()

    if strict:
        errors = cfg.validate()
        if errors:
            raise ValidationError(
                f"Config validation failed: {'; '.join(errors)}"
            )

    return cfg
