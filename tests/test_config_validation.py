"""
Tests for config validation in memctl.config.

Tests validate() methods on all config dataclasses and strict mode
in load_config().

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

import json
import pytest

from memctl.config import (
    ChatConfig,
    ConsolidateConfig,
    InspectConfig,
    MemoryConfig,
    PolicyConfig,
    StoreConfig,
    ValidationError,
    load_config,
)


# ---------------------------------------------------------------------------
# PolicyConfig validation
# ---------------------------------------------------------------------------


class TestPolicyConfigValidation:
    def test_defaults_valid(self):
        assert PolicyConfig().validate() == []

    def test_max_content_length_too_low(self):
        cfg = PolicyConfig(max_content_length=50)
        errors = cfg.validate()
        assert any("max_content_length" in e for e in errors)

    def test_max_content_length_too_high(self):
        cfg = PolicyConfig(max_content_length=200000)
        errors = cfg.validate()
        assert any("max_content_length" in e for e in errors)

    def test_low_confidence_threshold_negative(self):
        cfg = PolicyConfig(low_confidence_threshold=-0.1)
        errors = cfg.validate()
        assert any("low_confidence_threshold" in e for e in errors)

    def test_low_confidence_threshold_above_one(self):
        cfg = PolicyConfig(low_confidence_threshold=1.5)
        errors = cfg.validate()
        assert any("low_confidence_threshold" in e for e in errors)

    def test_quarantine_expiry_zero(self):
        cfg = PolicyConfig(quarantine_expiry_hours=0)
        errors = cfg.validate()
        assert any("quarantine_expiry_hours" in e for e in errors)

    def test_quarantine_expiry_too_high(self):
        cfg = PolicyConfig(quarantine_expiry_hours=9000)
        errors = cfg.validate()
        assert any("quarantine_expiry_hours" in e for e in errors)

    def test_valid_ranges(self):
        cfg = PolicyConfig(
            max_content_length=5000,
            low_confidence_threshold=0.5,
            quarantine_expiry_hours=168,
        )
        assert cfg.validate() == []


# ---------------------------------------------------------------------------
# ConsolidateConfig validation
# ---------------------------------------------------------------------------


class TestConsolidateConfigValidation:
    def test_defaults_valid(self):
        assert ConsolidateConfig().validate() == []

    def test_cluster_distance_negative(self):
        cfg = ConsolidateConfig(cluster_distance_threshold=-0.1)
        errors = cfg.validate()
        assert any("cluster_distance_threshold" in e for e in errors)

    def test_cluster_distance_above_one(self):
        cfg = ConsolidateConfig(cluster_distance_threshold=1.5)
        errors = cfg.validate()
        assert any("cluster_distance_threshold" in e for e in errors)

    def test_stm_threshold_zero(self):
        cfg = ConsolidateConfig(stm_threshold=0)
        errors = cfg.validate()
        assert any("stm_threshold" in e for e in errors)

    def test_usage_count_zero(self):
        cfg = ConsolidateConfig(usage_count_for_ltm=0)
        errors = cfg.validate()
        assert any("usage_count_for_ltm" in e for e in errors)

    def test_usage_count_too_high(self):
        cfg = ConsolidateConfig(usage_count_for_ltm=2000)
        errors = cfg.validate()
        assert any("usage_count_for_ltm" in e for e in errors)


# ---------------------------------------------------------------------------
# InspectConfig validation
# ---------------------------------------------------------------------------


class TestInspectConfigValidation:
    def test_defaults_valid(self):
        assert InspectConfig().validate() == []

    def test_dominance_frac_zero(self):
        cfg = InspectConfig(dominance_frac=0.0)
        errors = cfg.validate()
        assert any("dominance_frac" in e for e in errors)

    def test_dominance_frac_above_one(self):
        cfg = InspectConfig(dominance_frac=1.5)
        errors = cfg.validate()
        assert any("dominance_frac" in e for e in errors)

    def test_low_density_negative(self):
        cfg = InspectConfig(low_density_threshold=-1.0)
        errors = cfg.validate()
        assert any("low_density_threshold" in e for e in errors)

    def test_ext_concentration_zero(self):
        cfg = InspectConfig(ext_concentration_frac=0.0)
        errors = cfg.validate()
        assert any("ext_concentration_frac" in e for e in errors)

    def test_sparse_threshold_negative(self):
        cfg = InspectConfig(sparse_threshold=-1)
        errors = cfg.validate()
        assert any("sparse_threshold" in e for e in errors)

    def test_sparse_threshold_too_high(self):
        cfg = InspectConfig(sparse_threshold=200)
        errors = cfg.validate()
        assert any("sparse_threshold" in e for e in errors)


# ---------------------------------------------------------------------------
# ChatConfig validation
# ---------------------------------------------------------------------------


class TestChatConfigValidation:
    def test_defaults_valid(self):
        assert ChatConfig().validate() == []

    def test_history_max_too_low(self):
        cfg = ChatConfig(history_max=5)
        errors = cfg.validate()
        assert any("history_max" in e for e in errors)

    def test_history_max_too_high(self):
        cfg = ChatConfig(history_max=200000)
        errors = cfg.validate()
        assert any("history_max" in e for e in errors)


# ---------------------------------------------------------------------------
# StoreConfig validation
# ---------------------------------------------------------------------------


class TestStoreConfigValidation:
    def test_defaults_valid(self):
        assert StoreConfig().validate() == []


# ---------------------------------------------------------------------------
# MemoryConfig.validate() aggregation
# ---------------------------------------------------------------------------


class TestMemoryConfigValidation:
    def test_defaults_valid(self):
        assert MemoryConfig().validate() == []

    def test_aggregates_all_errors(self):
        cfg = MemoryConfig(
            policy=PolicyConfig(max_content_length=1),
            consolidate=ConsolidateConfig(stm_threshold=0),
            inspect=InspectConfig(dominance_frac=0.0),
            chat=ChatConfig(history_max=1),
        )
        errors = cfg.validate()
        assert len(errors) >= 4
        assert any("max_content_length" in e for e in errors)
        assert any("stm_threshold" in e for e in errors)
        assert any("dominance_frac" in e for e in errors)
        assert any("history_max" in e for e in errors)


# ---------------------------------------------------------------------------
# load_config(strict=True)
# ---------------------------------------------------------------------------


class TestLoadConfigStrict:
    def test_strict_valid_config(self, tmp_path):
        """Valid config passes strict mode."""
        cfg_data = {"chat": {"history_max": 500}}
        path = str(tmp_path / "config.json")
        with open(path, "w") as f:
            json.dump(cfg_data, f)

        cfg = load_config(path, strict=True)
        assert cfg.chat.history_max == 500

    def test_strict_invalid_config_raises(self, tmp_path):
        """Invalid values raise ValidationError in strict mode."""
        cfg_data = {"policy": {"max_content_length": 1}}
        path = str(tmp_path / "config.json")
        with open(path, "w") as f:
            json.dump(cfg_data, f)

        with pytest.raises(ValidationError) as exc:
            load_config(path, strict=True)
        assert "max_content_length" in str(exc.value)

    def test_strict_defaults_pass(self):
        """Defaults always pass strict mode."""
        cfg = load_config(None, strict=True)
        assert isinstance(cfg, MemoryConfig)

    def test_non_strict_ignores_errors(self, tmp_path):
        """Non-strict mode silently accepts invalid values."""
        cfg_data = {"policy": {"max_content_length": 1}}
        path = str(tmp_path / "config.json")
        with open(path, "w") as f:
            json.dump(cfg_data, f)

        cfg = load_config(path, strict=False)
        assert cfg.policy.max_content_length == 1  # accepted without error
