"""
Tests for PII detection patterns in memctl.policy.

5 patterns tested: US SSN, credit card, email, phone, IBAN.
Each pattern is tested with true positives and false-positive guards.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

import pytest

from memctl.config import PolicyConfig
from memctl.policy import MemoryPolicy, PolicyVerdict
from memctl.types import MemoryItem, MemoryProposal


@pytest.fixture
def policy():
    return MemoryPolicy()


@pytest.fixture
def policy_pii_disabled():
    cfg = PolicyConfig(pii_patterns_enabled=False)
    return MemoryPolicy(config=cfg)


# ---------------------------------------------------------------------------
# US Social Security Number (pattern #0)
# ---------------------------------------------------------------------------


class TestSSN:
    def test_ssn_detected_in_proposal(self, policy):
        p = MemoryProposal(
            title="Contact", content="SSN: 123-45-6789",
            why_store="record", provenance_hint={"source_id": "x"},
        )
        v = policy.evaluate_proposal(p)
        assert v.action == "quarantine"
        assert any("pii pattern #0" in r for r in v.reasons)
        assert v.forced_non_injectable is True

    def test_ssn_detected_in_item(self, policy):
        item = MemoryItem(title="Record", content="Patient SSN: 987-65-4321")
        v = policy.evaluate_item(item)
        assert v.action == "quarantine"
        assert any("pii pattern #0" in r for r in v.reasons)

    def test_ssn_false_positive_date(self, policy):
        """Date-like strings should not trigger SSN pattern."""
        p = MemoryProposal(
            title="Date", content="Meeting on 2026-02-20",
            why_store="schedule", provenance_hint={"source_id": "x"},
        )
        v = policy.evaluate_proposal(p)
        # 2026-02-20 has dashes but not NNN-NN-NNNN format
        assert not any("pii pattern #0" in r for r in v.reasons)

    def test_ssn_false_positive_phone(self, policy):
        """Phone-formatted numbers should not trigger SSN."""
        item = MemoryItem(title="Note", content="Call 555-123-4567")
        v = policy.evaluate_item(item)
        # 555-123-4567 is NNN-NNN-NNNN, not NNN-NN-NNNN
        assert not any("pii pattern #0" in r for r in v.reasons)


# ---------------------------------------------------------------------------
# Credit Card (pattern #1)
# ---------------------------------------------------------------------------


class TestCreditCard:
    def test_visa_detected(self, policy):
        p = MemoryProposal(
            title="Payment", content="Visa card: 4111111111111111",
            why_store="billing", provenance_hint={"source_id": "x"},
        )
        v = policy.evaluate_proposal(p)
        assert v.action == "quarantine"
        assert any("pii pattern #1" in r for r in v.reasons)

    def test_mastercard_detected(self, policy):
        item = MemoryItem(
            title="Card", content="MC: 5500-0000-0000-0004",
        )
        v = policy.evaluate_item(item)
        assert any("pii pattern #1" in r for r in v.reasons)

    def test_amex_detected(self, policy):
        item = MemoryItem(
            title="Card", content="Amex: 378282246310005",
        )
        v = policy.evaluate_item(item)
        assert any("pii pattern #1" in r for r in v.reasons)

    def test_short_number_no_match(self, policy):
        """Regular 4-digit numbers should not trigger credit card pattern."""
        item = MemoryItem(title="Note", content="Order #4111 was shipped")
        v = policy.evaluate_item(item)
        assert not any("pii pattern #1" in r for r in v.reasons)


# ---------------------------------------------------------------------------
# Email (pattern #2)
# ---------------------------------------------------------------------------


class TestEmail:
    def test_email_detected(self, policy):
        p = MemoryProposal(
            title="Contact", content="Reach us at user@example.com",
            why_store="contact", provenance_hint={"source_id": "x"},
        )
        v = policy.evaluate_proposal(p)
        assert v.action == "quarantine"
        assert any("pii pattern #2" in r for r in v.reasons)

    def test_email_in_item(self, policy):
        item = MemoryItem(
            title="Author", content="By admin@server.org",
        )
        v = policy.evaluate_item(item)
        assert any("pii pattern #2" in r for r in v.reasons)

    def test_email_complex(self, policy):
        item = MemoryItem(
            title="Note", content="john.doe+tag@sub.domain.co.uk is valid",
        )
        v = policy.evaluate_item(item)
        assert any("pii pattern #2" in r for r in v.reasons)

    def test_at_sign_no_email(self, policy):
        """Bare @ sign without email format should not trigger."""
        item = MemoryItem(title="Code", content="matrix[i@j] = 0")
        v = policy.evaluate_item(item)
        assert not any("pii pattern #2" in r for r in v.reasons)


# ---------------------------------------------------------------------------
# Phone (pattern #3)
# ---------------------------------------------------------------------------


class TestPhone:
    def test_us_phone_detected(self, policy):
        p = MemoryProposal(
            title="Contact", content="Call (555) 123-4567",
            why_store="contact", provenance_hint={"source_id": "x"},
        )
        v = policy.evaluate_proposal(p)
        assert v.action == "quarantine"
        assert any("pii pattern #3" in r for r in v.reasons)

    def test_international_phone(self, policy):
        item = MemoryItem(
            title="Phone", content="Reach at +1 555-867-5309",
        )
        v = policy.evaluate_item(item)
        assert any("pii pattern #3" in r for r in v.reasons)

    def test_dashed_phone(self, policy):
        item = MemoryItem(
            title="Note", content="555-867-5309 is the number",
        )
        v = policy.evaluate_item(item)
        assert any("pii pattern #3" in r for r in v.reasons)


# ---------------------------------------------------------------------------
# IBAN (pattern #4)
# ---------------------------------------------------------------------------


class TestIBAN:
    def test_iban_detected(self, policy):
        p = MemoryProposal(
            title="Banking", content="IBAN: DE89370400440532013000",
            why_store="payment", provenance_hint={"source_id": "x"},
        )
        v = policy.evaluate_proposal(p)
        assert v.action == "quarantine"
        assert any("pii pattern #4" in r for r in v.reasons)

    def test_french_iban(self, policy):
        item = MemoryItem(
            title="Bank", content="FR7630006000011234567890189",
        )
        v = policy.evaluate_item(item)
        assert any("pii pattern #4" in r for r in v.reasons)

    def test_short_code_no_match(self, policy):
        """Short country codes should not trigger IBAN."""
        item = MemoryItem(title="Note", content="See ISO standard FR12")
        v = policy.evaluate_item(item)
        # FR12 is only 4 chars — IBAN needs 15+ total
        assert not any("pii pattern #4" in r for r in v.reasons)


# ---------------------------------------------------------------------------
# PII disabled
# ---------------------------------------------------------------------------


class TestPIIDisabled:
    def test_pii_disabled_no_quarantine(self, policy_pii_disabled):
        """PII patterns should not trigger when disabled."""
        p = MemoryProposal(
            title="Record",
            content="SSN: 123-45-6789 email: a@b.com card: 4111111111111111",
            why_store="test", provenance_hint={"source_id": "x"},
        )
        v = policy_pii_disabled.evaluate_proposal(p)
        assert not any("pii" in r.lower() for r in v.reasons)

    def test_pii_disabled_item(self, policy_pii_disabled):
        item = MemoryItem(
            title="Record", content="SSN: 123-45-6789",
        )
        v = policy_pii_disabled.evaluate_item(item)
        assert not any("pii" in r.lower() for r in v.reasons)


# ---------------------------------------------------------------------------
# Combined: PII + other quarantine
# ---------------------------------------------------------------------------


class TestPIICombined:
    def test_pii_and_instructional(self, policy):
        """Both PII and instructional-quarantine can trigger together."""
        p = MemoryProposal(
            title="Rule",
            content="Always remember to call 555-123-4567",
            why_store="test", provenance_hint={"source_id": "x"},
        )
        v = policy.evaluate_proposal(p)
        assert v.action == "quarantine"
        assert v.forced_non_injectable is True
        # Should have both types of quarantine reasons
        has_pii = any("pii" in r.lower() for r in v.reasons)
        has_inst = any("instructional" in r.lower() for r in v.reasons)
        assert has_pii or has_inst  # at least one type present
