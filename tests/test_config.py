"""
Tests for memctl.config — configuration loading and dataclasses.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

import json
import os
import pytest

from memctl.config import (
    MemoryConfig,
    InspectConfig,
    ChatConfig,
    load_config,
)


class TestLoadConfig:
    def test_load_valid_json(self, tmp_path):
        """Parses all sections from a valid JSON config."""
        cfg_data = {
            "store": {"fts_tokenizer": "porter unicode61"},
            "inspect": {"dominance_frac": 0.50, "sparse_threshold": 2},
            "chat": {"history_max": 500},
        }
        path = str(tmp_path / "config.json")
        with open(path, "w") as f:
            json.dump(cfg_data, f)

        cfg = load_config(path)
        assert cfg.store.fts_tokenizer == "porter unicode61"
        assert cfg.inspect.dominance_frac == 0.50
        assert cfg.inspect.sparse_threshold == 2
        assert cfg.chat.history_max == 500

    def test_load_missing_file(self, tmp_path):
        """Returns defaults silently when file is missing."""
        path = str(tmp_path / "nonexistent.json")
        cfg = load_config(path)
        assert isinstance(cfg, MemoryConfig)
        assert cfg.inspect.dominance_frac == 0.40
        assert cfg.chat.history_max == 1000

    def test_load_invalid_json(self, tmp_path):
        """Returns defaults silently when file has invalid JSON."""
        path = str(tmp_path / "bad.json")
        with open(path, "w") as f:
            f.write("not json {{{")

        cfg = load_config(path)
        assert isinstance(cfg, MemoryConfig)
        assert cfg.inspect.dominance_frac == 0.40

    def test_partial_config(self, tmp_path):
        """Missing sections get defaults."""
        cfg_data = {"chat": {"history_max": 200}}
        path = str(tmp_path / "partial.json")
        with open(path, "w") as f:
            json.dump(cfg_data, f)

        cfg = load_config(path)
        assert cfg.chat.history_max == 200
        # Other sections use defaults
        assert cfg.inspect.dominance_frac == 0.40
        assert cfg.store.fts_tokenizer == "unicode61 remove_diacritics 2"

    def test_inspect_thresholds(self, tmp_path):
        """Custom thresholds propagate correctly."""
        cfg_data = {
            "inspect": {
                "dominance_frac": 0.30,
                "low_density_threshold": 0.20,
                "ext_concentration_frac": 0.60,
                "sparse_threshold": 3,
            }
        }
        path = str(tmp_path / "thresholds.json")
        with open(path, "w") as f:
            json.dump(cfg_data, f)

        cfg = load_config(path)
        assert cfg.inspect.dominance_frac == 0.30
        assert cfg.inspect.low_density_threshold == 0.20
        assert cfg.inspect.ext_concentration_frac == 0.60
        assert cfg.inspect.sparse_threshold == 3


class TestLoadConfigNone:
    def test_none_path_returns_defaults(self):
        """None path returns compiled defaults."""
        cfg = load_config(None)
        assert isinstance(cfg, MemoryConfig)
        assert cfg.inspect.dominance_frac == 0.40


class TestInspectConfig:
    def test_defaults(self):
        cfg = InspectConfig()
        assert cfg.dominance_frac == 0.40
        assert cfg.low_density_threshold == 0.10
        assert cfg.ext_concentration_frac == 0.75
        assert cfg.sparse_threshold == 1


class TestChatConfig:
    def test_defaults(self):
        cfg = ChatConfig()
        assert cfg.history_max == 1000
