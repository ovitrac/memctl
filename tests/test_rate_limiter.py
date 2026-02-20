"""
Tests for memctl.mcp.rate_limiter — Token-bucket throttling for MCP tool calls.

Invariants tested:
    R1: Rate limiter blocks runaway writes (>20/min without burst)
    R2: Rate limiter allows normal usage (<=20/min)
    R3: Read and write budgets are independent
    R4: Per-session isolation
    R5: Exempt tools bypass rate limiting (classify_tool)
    R6: Token bucket refills correctly after time passes

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

import pytest

from memctl.mcp.rate_limiter import (
    EXEMPT_TOOLS,
    READ_TOOLS,
    WRITE_TOOLS,
    RateLimitExceeded,
    RateLimiter,
    _Bucket,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def limiter():
    """Default rate limiter: 20 writes/min, 120 reads/min, burst x2."""
    return RateLimiter(
        writes_per_minute=20,
        reads_per_minute=120,
        burst_factor=2.0,
        max_proposals_per_turn=5,
    )


@pytest.fixture
def strict_limiter():
    """Strict limiter: 5 writes/min, 10 reads/min, burst x1 (no burst)."""
    return RateLimiter(
        writes_per_minute=5,
        reads_per_minute=10,
        burst_factor=1.0,
        max_proposals_per_turn=3,
    )


# ---------------------------------------------------------------------------
# Tool classification sets
# ---------------------------------------------------------------------------


class TestToolClassification:
    """Verify the locked tool classification sets."""

    def test_write_tools_membership(self):
        expected = {
            "memory_write", "memory_propose", "memory_import",
            "memory_consolidate", "memory_sync",
        }
        assert WRITE_TOOLS == expected

    def test_read_tools_membership(self):
        expected = {
            "memory_recall", "memory_search", "memory_read",
            "memory_export", "memory_inspect", "memory_ask", "memory_loop",
        }
        assert READ_TOOLS == expected

    def test_exempt_tools_membership(self):
        expected = {"memory_stats", "memory_mount"}
        assert EXEMPT_TOOLS == expected

    def test_no_overlap_between_sets(self):
        assert WRITE_TOOLS & READ_TOOLS == set()
        assert WRITE_TOOLS & EXEMPT_TOOLS == set()
        assert READ_TOOLS & EXEMPT_TOOLS == set()

    def test_classify_write_tool(self, limiter):
        for tool in WRITE_TOOLS:
            assert limiter.classify_tool(tool) == "write"

    def test_classify_read_tool(self, limiter):
        for tool in READ_TOOLS:
            assert limiter.classify_tool(tool) == "read"

    def test_classify_exempt_tool(self, limiter):
        """R5: memory_stats (and memory_mount) are exempt from rate limiting."""
        for tool in EXEMPT_TOOLS:
            assert limiter.classify_tool(tool) == "exempt"
        # Explicitly test memory_stats as stated in R5
        assert limiter.classify_tool("memory_stats") == "exempt"

    def test_classify_unknown_tool_is_exempt(self, limiter):
        assert limiter.classify_tool("some_unknown_tool") == "exempt"


# ---------------------------------------------------------------------------
# R2: Normal usage — within limits
# ---------------------------------------------------------------------------


class TestNormalUsage:
    """R2: Rate limiter allows normal usage (<=20/min)."""

    def test_single_write_allowed(self, limiter):
        limiter.check_write("sess1")  # should not raise

    def test_single_read_allowed(self, limiter):
        limiter.check_read("sess1")  # should not raise

    def test_writes_up_to_burst_capacity(self, limiter):
        """With burst_factor=2.0, initial capacity is 40 tokens."""
        for i in range(40):
            limiter.check_write("sess1")
        # 40th write consumes the last token; next should fail
        with pytest.raises(RateLimitExceeded):
            limiter.check_write("sess1")

    def test_reads_up_to_burst_capacity(self, limiter):
        """With burst_factor=2.0 and 120 reads/min, capacity is 240."""
        for i in range(240):
            limiter.check_read("sess1")
        with pytest.raises(RateLimitExceeded):
            limiter.check_read("sess1")


# ---------------------------------------------------------------------------
# R1: Runaway writes blocked
# ---------------------------------------------------------------------------


class TestRunawayWritesBlocked:
    """R1: Rate limiter blocks runaway writes (>20/min without burst)."""

    def test_exceed_write_limit_no_burst(self, strict_limiter):
        """strict_limiter: 5 writes/min, burst_factor=1.0 => capacity=5."""
        for _ in range(5):
            strict_limiter.check_write("sess1")
        with pytest.raises(RateLimitExceeded) as exc_info:
            strict_limiter.check_write("sess1")
        assert exc_info.value.retry_after_ms > 0

    def test_exceed_read_limit_no_burst(self, strict_limiter):
        """strict_limiter: 10 reads/min, burst_factor=1.0 => capacity=10."""
        for _ in range(10):
            strict_limiter.check_read("sess1")
        with pytest.raises(RateLimitExceeded) as exc_info:
            strict_limiter.check_read("sess1")
        assert exc_info.value.retry_after_ms > 0

    def test_rate_limit_error_has_retry_after(self, strict_limiter):
        for _ in range(5):
            strict_limiter.check_write("sess1")
        with pytest.raises(RateLimitExceeded) as exc_info:
            strict_limiter.check_write("sess1")
        err = exc_info.value
        assert isinstance(err.retry_after_ms, int)
        assert err.retry_after_ms > 0
        assert "Write rate limit exceeded" in str(err)


# ---------------------------------------------------------------------------
# R3: Read and write budgets are independent
# ---------------------------------------------------------------------------


class TestBudgetIndependence:
    """R3: Reading doesn't drain writes and vice versa."""

    def test_reads_dont_drain_writes(self, strict_limiter):
        """Exhaust all reads, writes should still work."""
        for _ in range(10):
            strict_limiter.check_read("sess1")
        # Reads exhausted
        with pytest.raises(RateLimitExceeded):
            strict_limiter.check_read("sess1")
        # Writes should still be available
        strict_limiter.check_write("sess1")  # should not raise

    def test_writes_dont_drain_reads(self, strict_limiter):
        """Exhaust all writes, reads should still work."""
        for _ in range(5):
            strict_limiter.check_write("sess1")
        with pytest.raises(RateLimitExceeded):
            strict_limiter.check_write("sess1")
        # Reads should still be available
        strict_limiter.check_read("sess1")  # should not raise


# ---------------------------------------------------------------------------
# R4: Per-session isolation
# ---------------------------------------------------------------------------


class TestSessionIsolation:
    """R4: Session A's usage doesn't affect session B."""

    def test_separate_sessions_independent(self, strict_limiter):
        """Exhaust session A, session B still has full budget."""
        for _ in range(5):
            strict_limiter.check_write("session_a")
        with pytest.raises(RateLimitExceeded):
            strict_limiter.check_write("session_a")

        # Session B is untouched
        for _ in range(5):
            strict_limiter.check_write("session_b")
        # Session B also exhausted now
        with pytest.raises(RateLimitExceeded):
            strict_limiter.check_write("session_b")

    def test_new_session_gets_full_budget(self, strict_limiter):
        """Even after heavy usage on one session, new sessions start fresh."""
        for _ in range(5):
            strict_limiter.check_write("old_session")
        for _ in range(10):
            strict_limiter.check_read("old_session")
        # New session has full capacity
        strict_limiter.check_write("new_session")
        strict_limiter.check_read("new_session")


# ---------------------------------------------------------------------------
# R6: Token bucket refill
# ---------------------------------------------------------------------------


class TestTokenRefill:
    """R6: Token bucket refills correctly after time passes."""

    def test_bucket_refill_restores_tokens(self):
        """Directly manipulate _Bucket to test refill logic."""
        bucket = _Bucket(
            capacity=10.0,
            tokens=0.0,
            refill_rate=10.0,  # 10 tokens/second
        )
        # Simulate 1 second passing by adjusting last_refill
        bucket.last_refill -= 1.0
        bucket.refill()
        # Should have ~10 tokens (capped at capacity)
        assert bucket.tokens >= 9.0  # allow small timing tolerance
        assert bucket.tokens <= 10.0

    def test_bucket_refill_caps_at_capacity(self):
        """Refill cannot exceed capacity."""
        bucket = _Bucket(
            capacity=5.0,
            tokens=0.0,
            refill_rate=100.0,  # very fast
        )
        bucket.last_refill -= 10.0  # 10 seconds = 1000 tokens attempted
        bucket.refill()
        assert bucket.tokens == 5.0  # capped at capacity

    def test_partial_refill_after_partial_drain(self):
        """Drain some tokens, wait partial time, verify partial refill."""
        bucket = _Bucket(
            capacity=10.0,
            tokens=10.0,
            refill_rate=5.0,  # 5 tokens/second
        )
        # Consume 6 tokens
        assert bucket.try_consume(6) == 0
        assert bucket.tokens == 4.0
        # Simulate 0.5 second => +2.5 tokens => 6.5 total
        bucket.last_refill -= 0.5
        bucket.refill()
        assert 6.0 <= bucket.tokens <= 7.0  # ~6.5, timing tolerance

    def test_try_consume_returns_wait_ms(self):
        """When tokens are insufficient, try_consume returns wait time in ms."""
        bucket = _Bucket(
            capacity=10.0,
            tokens=0.0,
            refill_rate=2.0,  # 2 tokens/second
        )
        wait = bucket.try_consume(1)
        # Need 1 token at 2/sec => 500ms
        assert wait > 0
        assert 400 <= wait <= 600  # ~500ms, small timing tolerance

    def test_limiter_recovers_after_time(self, strict_limiter):
        """Integration: exhaust writes, simulate time, verify recovery."""
        for _ in range(5):
            strict_limiter.check_write("sess1")
        with pytest.raises(RateLimitExceeded):
            strict_limiter.check_write("sess1")

        # Directly manipulate the bucket's last_refill to simulate time
        buckets = strict_limiter._get_buckets("sess1")
        buckets.write.last_refill -= 60.0  # simulate 1 full minute passing

        # Should recover (5 writes/min * 60s refill = 5 tokens, capped)
        strict_limiter.check_write("sess1")  # should not raise


# ---------------------------------------------------------------------------
# check_write_n — batch import
# ---------------------------------------------------------------------------


class TestBatchImport:
    """check_write_n consumes n tokens for batch operations."""

    def test_batch_within_capacity(self, strict_limiter):
        """5 items at once within capacity of 5."""
        strict_limiter.check_write_n("sess1", 5)  # should not raise

    def test_batch_exceeds_capacity(self, strict_limiter):
        """6 items at once exceeds capacity of 5."""
        with pytest.raises(RateLimitExceeded) as exc_info:
            strict_limiter.check_write_n("sess1", 6)
        assert "6 items" in str(exc_info.value)

    def test_batch_after_partial_drain(self, strict_limiter):
        """2 individual writes + batch of 4 => exceeds capacity of 5."""
        strict_limiter.check_write("sess1")
        strict_limiter.check_write("sess1")
        with pytest.raises(RateLimitExceeded):
            strict_limiter.check_write_n("sess1", 4)


# ---------------------------------------------------------------------------
# check_proposals — per-turn limits
# ---------------------------------------------------------------------------


class TestProposalLimits:
    """Per-turn proposal counting and reset."""

    def test_proposals_within_limit(self, strict_limiter):
        """3 proposals within limit of 3."""
        strict_limiter.check_proposals("sess1", 3)  # should not raise

    def test_proposals_exceed_limit(self, strict_limiter):
        """4 proposals exceed limit of 3."""
        with pytest.raises(RateLimitExceeded) as exc_info:
            strict_limiter.check_proposals("sess1", 4)
        assert "Proposal limit exceeded" in str(exc_info.value)

    def test_proposals_accumulate_across_calls(self, strict_limiter):
        """Two calls: 2 + 2 = 4 > limit of 3."""
        strict_limiter.check_proposals("sess1", 2)
        with pytest.raises(RateLimitExceeded):
            strict_limiter.check_proposals("sess1", 2)

    def test_reset_turn_clears_proposals(self, strict_limiter):
        """reset_turn allows a new batch of proposals."""
        strict_limiter.check_proposals("sess1", 3)
        strict_limiter.reset_turn("sess1")
        strict_limiter.check_proposals("sess1", 3)  # should not raise

    def test_reset_turn_unknown_session_is_noop(self, strict_limiter):
        """reset_turn on non-existent session does not raise."""
        strict_limiter.reset_turn("nonexistent")  # should not raise

    def test_proposals_zero_retry_after(self, strict_limiter):
        """Proposal limit returns retry_after_ms=0 (not time-based)."""
        with pytest.raises(RateLimitExceeded) as exc_info:
            strict_limiter.check_proposals("sess1", 4)
        assert exc_info.value.retry_after_ms == 0
