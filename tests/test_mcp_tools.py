"""
Tests for all 14 MCP tools in memctl.mcp.tools.

Tests use direct function calls (not MCP protocol) via a mock FastMCP.
memory_ask and memory_loop tests are marked with skipif since they
require subprocess LLM.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

import json
import os
import sys
import pytest

from memctl.config import MemoryConfig, StoreConfig
from memctl.policy import MemoryPolicy
from memctl.store import MemoryStore
from memctl.types import MemoryItem, MemoryProvenance


# ---------------------------------------------------------------------------
# Mock FastMCP
# ---------------------------------------------------------------------------


class MockMCP:
    """Minimal FastMCP mock that captures tool registrations."""

    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorator


@pytest.fixture
def mcp_env(tmp_path):
    """Create store, policy, config, mock MCP, and register all tools."""
    db_path = str(tmp_path / "memory.db")
    config = MemoryConfig(store=StoreConfig(db_path=db_path))
    store = MemoryStore(db_path=db_path)
    policy = MemoryPolicy(config.policy)
    mcp = MockMCP()

    from memctl.mcp.tools import register_memory_tools
    register_memory_tools(mcp, store, policy, config)

    yield {
        "mcp": mcp,
        "store": store,
        "db_path": db_path,
        "config": config,
        "tmp_path": tmp_path,
    }
    store.close()


def call(env, tool_name, **kwargs):
    """Call a registered MCP tool by name."""
    return env["mcp"].tools[tool_name](**kwargs)


# ---------------------------------------------------------------------------
# Tool count
# ---------------------------------------------------------------------------


class TestToolCount:
    def test_14_tools_registered(self, mcp_env):
        assert len(mcp_env["mcp"].tools) == 14

    def test_all_tool_names(self, mcp_env):
        expected = {
            "memory_recall", "memory_search", "memory_propose", "memory_write",
            "memory_read", "memory_stats", "memory_consolidate",
            "memory_mount", "memory_sync", "memory_inspect",
            "memory_ask", "memory_export", "memory_import", "memory_loop",
        }
        assert set(mcp_env["mcp"].tools.keys()) == expected


# ---------------------------------------------------------------------------
# memory_recall
# ---------------------------------------------------------------------------


class TestMemoryRecall:
    def test_recall_empty_store(self, mcp_env):
        result = call(mcp_env, "memory_recall", query="test")
        assert result["status"] == "ok"
        assert result["matched"] == 0

    def test_recall_with_items(self, mcp_env):
        store = mcp_env["store"]
        item = MemoryItem(
            title="Architecture Guide",
            content="We use microservices for scalability",
            tags=["architecture"],
        )
        store.write_item(item, reason="test")

        result = call(mcp_env, "memory_recall", query="microservices")
        assert result["status"] == "ok"
        assert result["matched"] >= 1


# ---------------------------------------------------------------------------
# memory_search
# ---------------------------------------------------------------------------


class TestMemorySearch:
    def test_search_empty(self, mcp_env):
        result = call(mcp_env, "memory_search", query="test")
        assert result["status"] == "ok"
        assert result["count"] == 0

    def test_search_with_results(self, mcp_env):
        store = mcp_env["store"]
        item = MemoryItem(
            title="Database design",
            content="PostgreSQL for main storage",
            tags=["database"],
        )
        store.write_item(item, reason="test")

        result = call(mcp_env, "memory_search", query="PostgreSQL")
        assert result["status"] == "ok"
        assert result["count"] >= 1


# ---------------------------------------------------------------------------
# memory_propose
# ---------------------------------------------------------------------------


class TestMemoryPropose:
    def test_propose_valid(self, mcp_env):
        items = json.dumps([{
            "title": "Test fact",
            "content": "The sky is blue",
            "tags": ["test"],
            "type": "fact",
            "why_store": "testing",
            "provenance_hint": {"source_id": "test"},
        }])
        result = call(mcp_env, "memory_propose", items=items)
        assert result["status"] == "ok"
        assert result["accepted"] == 1
        assert result["rejected"] == 0

    def test_propose_invalid_json(self, mcp_env):
        result = call(mcp_env, "memory_propose", items="not json")
        assert result["status"] == "error"

    def test_propose_secret_rejected(self, mcp_env):
        items = json.dumps([{
            "title": "Config",
            "content": "api_key = sk-verysecretkey12345678",
            "tags": ["config"],
            "type": "note",
        }])
        result = call(mcp_env, "memory_propose", items=items)
        assert result["rejected"] >= 1


# ---------------------------------------------------------------------------
# memory_write
# ---------------------------------------------------------------------------


class TestMemoryWrite:
    def test_write_valid(self, mcp_env):
        result = call(mcp_env, "memory_write",
                       title="Note", content="A simple note")
        assert result["status"] == "ok"
        assert "id" in result

    def test_write_secret_rejected(self, mcp_env):
        result = call(mcp_env, "memory_write",
                       title="Secret", content="password = mysecretpassword123")
        assert result["status"] == "rejected"

    def test_write_pii_quarantined(self, mcp_env):
        """PII content should be quarantined (v0.7 soft-block fix)."""
        result = call(mcp_env, "memory_write",
                       title="Contact", content="Email: user@example.com")
        assert result["status"] == "ok"
        assert result["action"] == "quarantine"

    def test_write_instructional_quarantined(self, mcp_env):
        """Instructional-quarantine content via evaluate_item (v0.7 fix)."""
        result = call(mcp_env, "memory_write",
                       title="Rule",
                       content="You must always validate inputs first")
        assert result["status"] == "ok"
        assert result["action"] == "quarantine"


# ---------------------------------------------------------------------------
# memory_read
# ---------------------------------------------------------------------------


class TestMemoryRead:
    def test_read_existing(self, mcp_env):
        store = mcp_env["store"]
        item = MemoryItem(title="Test", content="Content")
        store.write_item(item, reason="test")

        result = call(mcp_env, "memory_read", ids=item.id)
        assert result["status"] == "ok"
        assert result["found"] == 1

    def test_read_nonexistent(self, mcp_env):
        result = call(mcp_env, "memory_read", ids="MEM-nonexistent")
        assert result["status"] == "ok"
        assert result["found"] == 0


# ---------------------------------------------------------------------------
# memory_stats
# ---------------------------------------------------------------------------


class TestMemoryStats:
    def test_stats(self, mcp_env):
        result = call(mcp_env, "memory_stats")
        assert result["status"] == "ok"
        assert "total_items" in result


# ---------------------------------------------------------------------------
# memory_consolidate
# ---------------------------------------------------------------------------


class TestMemoryConsolidate:
    def test_consolidate_empty(self, mcp_env):
        result = call(mcp_env, "memory_consolidate")
        assert result["status"] == "ok"

    def test_consolidate_dry_run(self, mcp_env):
        result = call(mcp_env, "memory_consolidate", dry_run=True)
        assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# memory_mount
# ---------------------------------------------------------------------------


class TestMemoryMount:
    def test_mount_list_empty(self, mcp_env):
        result = call(mcp_env, "memory_mount", action="list")
        assert result["status"] == "ok"
        assert result["count"] == 0

    def test_mount_register(self, mcp_env):
        folder = str(mcp_env["tmp_path"] / "test_folder")
        os.makedirs(folder, exist_ok=True)
        result = call(mcp_env, "memory_mount",
                       action="register", path=folder)
        assert result["status"] == "ok"
        assert "mount_id" in result

    def test_mount_register_missing_path(self, mcp_env):
        result = call(mcp_env, "memory_mount", action="register")
        assert result["status"] == "error"

    def test_mount_register_and_list(self, mcp_env):
        folder = str(mcp_env["tmp_path"] / "test_folder2")
        os.makedirs(folder, exist_ok=True)
        call(mcp_env, "memory_mount", action="register", path=folder)
        result = call(mcp_env, "memory_mount", action="list")
        assert result["count"] == 1

    def test_mount_remove(self, mcp_env):
        folder = str(mcp_env["tmp_path"] / "test_folder3")
        os.makedirs(folder, exist_ok=True)
        reg = call(mcp_env, "memory_mount", action="register", path=folder)
        mid = reg["mount_id"]
        result = call(mcp_env, "memory_mount", action="remove", mount_id=mid)
        assert result["status"] == "ok"

    def test_mount_remove_nonexistent(self, mcp_env):
        result = call(mcp_env, "memory_mount",
                       action="remove", mount_id="MNT-nonexistent")
        assert result["status"] == "error"

    def test_mount_unknown_action(self, mcp_env):
        result = call(mcp_env, "memory_mount", action="invalid")
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# memory_sync
# ---------------------------------------------------------------------------


class TestMemorySync:
    def test_sync_empty_store(self, mcp_env):
        """Sync all with no mounts."""
        result = call(mcp_env, "memory_sync")
        assert result["status"] == "ok"
        assert result["mount_count"] == 0

    def test_sync_folder(self, mcp_env):
        folder = str(mcp_env["tmp_path"] / "sync_test")
        os.makedirs(folder, exist_ok=True)
        (mcp_env["tmp_path"] / "sync_test" / "test.txt").write_text(
            "Hello world content for sync",
            encoding="utf-8",
        )
        result = call(mcp_env, "memory_sync", path=folder)
        assert result["status"] == "ok"
        assert result["files_scanned"] >= 1

    def test_sync_nonexistent_path(self, mcp_env):
        result = call(mcp_env, "memory_sync", path="/nonexistent/path")
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# memory_inspect
# ---------------------------------------------------------------------------


class TestMemoryInspect:
    def test_inspect_empty_store(self, mcp_env):
        result = call(mcp_env, "memory_inspect")
        assert result["status"] == "ok"

    def test_inspect_folder(self, mcp_env):
        folder = str(mcp_env["tmp_path"] / "inspect_test")
        os.makedirs(folder, exist_ok=True)
        (mcp_env["tmp_path"] / "inspect_test" / "readme.md").write_text(
            "# Test\n\nSample content.\n",
            encoding="utf-8",
        )
        result = call(mcp_env, "memory_inspect", path=folder)
        assert result["status"] == "ok"
        assert "inject_text" in result

    def test_inspect_json_format(self, mcp_env):
        folder = str(mcp_env["tmp_path"] / "inspect_json")
        os.makedirs(folder, exist_ok=True)
        (mcp_env["tmp_path"] / "inspect_json" / "doc.txt").write_text(
            "Content for inspect",
            encoding="utf-8",
        )
        result = call(mcp_env, "memory_inspect",
                       path=folder, output_format="json")
        assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# memory_export
# ---------------------------------------------------------------------------


class TestMemoryExport:
    def test_export_empty(self, mcp_env):
        result = call(mcp_env, "memory_export")
        assert result["status"] == "ok"
        assert result["count"] == 0
        assert result["items"] == []

    def test_export_with_items(self, mcp_env):
        store = mcp_env["store"]
        item = MemoryItem(title="Export test", content="Content to export")
        store.write_item(item, reason="test")

        result = call(mcp_env, "memory_export")
        assert result["status"] == "ok"
        assert result["count"] >= 1

    def test_export_with_filter(self, mcp_env):
        store = mcp_env["store"]
        item = MemoryItem(
            title="Filtered", content="Filtered content",
            tier="mtm", type="decision",
        )
        store.write_item(item, reason="test")

        result = call(mcp_env, "memory_export", tier="mtm")
        assert result["status"] == "ok"
        assert all(i.get("tier") == "mtm" for i in result["items"])


# ---------------------------------------------------------------------------
# memory_import
# ---------------------------------------------------------------------------


class TestMemoryImport:
    def test_import_valid(self, mcp_env):
        items = json.dumps([{
            "title": "Imported item",
            "content": "Content from import",
            "tier": "stm",
            "type": "note",
            "tags": [],
            "scope": "project",
        }])
        result = call(mcp_env, "memory_import", items=items)
        assert result["status"] == "ok"
        assert result["imported"] == 1

    def test_import_invalid_json(self, mcp_env):
        result = call(mcp_env, "memory_import", items="not valid json")
        assert result["status"] == "error"

    def test_import_dry_run(self, mcp_env):
        items = json.dumps([{
            "title": "Dry run item",
            "content": "Should not be stored",
            "tier": "stm",
            "type": "note",
            "tags": [],
            "scope": "project",
        }])
        result = call(mcp_env, "memory_import", items=items, dry_run=True)
        assert result["status"] == "ok"
        assert result["imported"] == 1  # counted but not written

        # Verify not actually stored
        stats = call(mcp_env, "memory_stats")
        assert stats["total_items"] == 0

    def test_import_dedup(self, mcp_env):
        """Importing the same content twice should dedup."""
        items = json.dumps([{
            "title": "Dedup item",
            "content": "Identical content for dedup test",
            "tier": "stm",
            "type": "note",
            "tags": [],
            "scope": "project",
        }])
        call(mcp_env, "memory_import", items=items)
        result = call(mcp_env, "memory_import", items=items)
        assert result["skipped_dedup"] >= 1


# ---------------------------------------------------------------------------
# memory_ask (requires LLM subprocess — skipped if unavailable)
# ---------------------------------------------------------------------------


class TestMemoryAsk:
    @pytest.mark.skipif(
        not os.path.exists("demos/mock_llm.sh"),
        reason="mock_llm.sh not available",
    )
    def test_ask_basic(self, mcp_env):
        folder = str(mcp_env["tmp_path"] / "ask_test")
        os.makedirs(folder, exist_ok=True)
        (mcp_env["tmp_path"] / "ask_test" / "readme.md").write_text(
            "# Project\n\nWe use microservices.\n",
            encoding="utf-8",
        )
        result = call(mcp_env, "memory_ask",
                       path=folder,
                       question="What architecture?",
                       llm_cmd="bash demos/mock_llm.sh")
        assert result["status"] in ("ok", "error")

    def test_ask_invalid_path(self, mcp_env):
        result = call(mcp_env, "memory_ask",
                       path="/nonexistent",
                       question="test",
                       llm_cmd="echo test")
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# memory_loop (requires LLM subprocess — skipped if unavailable)
# ---------------------------------------------------------------------------


class TestMemoryLoop:
    @pytest.mark.skipif(
        not os.path.exists("demos/mock_llm.sh"),
        reason="mock_llm.sh not available",
    )
    def test_loop_basic(self, mcp_env):
        result = call(mcp_env, "memory_loop",
                       query="What is the architecture?",
                       initial_context="We use microservices.",
                       llm_cmd="bash demos/mock_llm.sh",
                       protocol="passive",
                       max_calls=1)
        assert result["status"] in ("ok", "error")

    def test_loop_invalid_llm(self, mcp_env):
        result = call(mcp_env, "memory_loop",
                       query="test",
                       initial_context="context",
                       llm_cmd="/nonexistent_binary_xyz",
                       max_calls=1)
        assert result["status"] == "error"
