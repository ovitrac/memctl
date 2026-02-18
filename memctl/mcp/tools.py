"""
memctl MCP Tools — 7 core memory tools for MCP integration.

Thin wrappers around MemoryStore, MemoryPolicy, and ConsolidationPipeline.
Each tool parses arguments, applies policy, and formats the response.
Zero business logic in this layer beyond wiring.

Tool hierarchy:
    PRIMARY:    memory_recall    — token-budgeted injection (canonical contract)
    SECONDARY:  memory_search    — interactive discovery
    WRITE:      memory_propose   — governed write, memory_write — privileged
    CRUD:       memory_read      — read by IDs
    LIFECYCLE:  memory_consolidate, memory_stats

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from memctl.config import MemoryConfig
from memctl.consolidate import ConsolidationPipeline
from memctl.mcp.formatting import (
    FORMAT_VERSION,
    format_injection_block,
    format_search_results,
)
from memctl.policy import MemoryPolicy
from memctl.store import MemoryStore
from memctl.types import MemoryItem, MemoryProposal, MemoryProvenance

logger = logging.getLogger(__name__)


def register_memory_tools(
    mcp,
    store: MemoryStore,
    policy: MemoryPolicy,
    config: MemoryConfig,
) -> None:
    """
    Register all 7 core memory MCP tools on a FastMCP server instance.

    Args:
        mcp: FastMCP server instance.
        store: Fully initialized MemoryStore.
        policy: MemoryPolicy for write governance.
        config: MemoryConfig for consolidation thresholds.
    """

    # -- PRIMARY: Token-budgeted injection ---------------------------------

    @mcp.tool()
    def memory_recall(
        query: str,
        budget_tokens: int = 1500,
        tier: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Token-budgeted memory retrieval for context injection.

        PRIMARY tool — returns items formatted for direct insertion into
        LLM context, respecting a token budget. Use this as the default
        way to retrieve prior knowledge.

        Args:
            query: Natural language search query.
            budget_tokens: Maximum tokens for injection block (default 1500).
            tier: Filter by tier (stm|mtm|ltm). None = all.
            scope: Filter by scope. None = all.

        Returns:
            inject_text: Formatted injection block (format_version=1).
            items: Structured item list.
            tokens_used: Actual tokens consumed.
            matched: Total items matching query.
        """
        try:
            raw_items = store.search_fulltext(
                query, tier=tier, scope=scope, limit=50,
            )
        except Exception as e:
            return {"status": "error", "message": f"Search failed: {e}"}

        # Filter non-injectable items
        injectable = [it for it in raw_items if it.injectable]

        # Build dicts for formatting
        enriched = [_item_to_format_dict(it) for it in injectable]

        inject_text = format_injection_block(
            enriched,
            budget_tokens=budget_tokens,
            total_matched=len(enriched),
            injection_type="memory_recall",
        )

        catalog = format_search_results(enriched, query=query)
        tokens_used = len(inject_text) // 4 if inject_text else 0

        return {
            "status": "ok",
            "inject_text": inject_text,
            "items": catalog,
            "tokens_used": tokens_used,
            "matched": len(enriched),
            "format_version": FORMAT_VERSION,
        }

    # -- SECONDARY: Interactive search -------------------------------------

    @mcp.tool()
    def memory_search(
        query: str,
        tags: Optional[str] = None,
        tier: Optional[str] = None,
        type_filter: Optional[str] = None,
        scope: Optional[str] = None,
        k: int = 10,
    ) -> Dict[str, Any]:
        """Search memory items by text query, tags, and filters.

        SECONDARY tool — for interactive discovery and exploration.
        Returns structured results (not formatted for injection).

        Args:
            query: Search text (FTS5 BM25 ranked).
            tags: Comma-separated tags to filter by.
            tier: Filter by tier (stm|mtm|ltm).
            type_filter: Filter by type (fact|decision|definition|...).
            scope: Filter by scope.
            k: Max results (default 10).

        Returns:
            count: Number of results.
            items: List of matching items with preview.
        """
        try:
            if tags:
                tag_list = [t.strip() for t in tags.split(",")]
                raw_items = store.search_by_tags(
                    tag_list, tier=tier, scope=scope, limit=k,
                )
                # Post-filter by query text if both tags and query given
                if query.strip():
                    query_lower = query.lower()
                    raw_items = [
                        it for it in raw_items
                        if query_lower in it.title.lower()
                        or query_lower in it.content.lower()
                    ]
            else:
                raw_items = store.search_fulltext(
                    query, tier=tier, type_filter=type_filter,
                    scope=scope, limit=k,
                )
        except Exception as e:
            return {"status": "error", "message": f"Search failed: {e}"}

        results = format_search_results(
            [_item_to_format_dict(it) for it in raw_items],
            query=query,
        )

        # Flag non-injectable items as quarantined
        for i, it in enumerate(raw_items):
            if not it.injectable:
                results[i]["quarantined"] = True

        return {
            "status": "ok",
            "count": len(results),
            "items": results,
        }

    # -- WRITE PATH --------------------------------------------------------

    @mcp.tool()
    def memory_propose(
        items: str,
        scope: str = "project",
        source_doc: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Submit memory candidates for policy evaluation and storage.

        DEFAULT write path — items are validated against governance policy
        (secret detection, injection patterns, size limits). Approved items
        are stored in STM.

        Args:
            items: JSON array of proposals. Each: {title, content, tags[], type}.
                   Optional: entities[], confidence, provenance_hint{}.
            scope: Memory scope (default "project").
            source_doc: Source document for provenance tracking.

        Returns:
            accepted: Count of stored items.
            rejected: Count of blocked items.
            items: Per-item results with status.
        """
        try:
            item_list = json.loads(items)
        except (json.JSONDecodeError, TypeError) as e:
            return {"status": "error", "message": f"Invalid JSON in items: {e}"}

        if not isinstance(item_list, list):
            item_list = [item_list]

        # Inject provenance from source_doc if provided
        if source_doc:
            for it in item_list:
                if "provenance_hint" not in it:
                    it["provenance_hint"] = {}
                it["provenance_hint"]["source_id"] = source_doc
                it["provenance_hint"]["source_kind"] = "doc"
                it.setdefault("scope", scope)

        accepted = 0
        rejected = 0
        quarantined = 0
        per_item: List[Dict[str, Any]] = []

        for item_d in item_list:
            try:
                proposal = MemoryProposal.from_dict(item_d)
                if not proposal.scope:
                    proposal.scope = scope

                verdict = policy.evaluate_proposal(proposal)

                if verdict.action == "reject":
                    rejected += 1
                    per_item.append({
                        "title": proposal.title,
                        "action": "reject",
                        "reasons": verdict.reasons,
                    })
                    continue

                # Convert proposal to item
                mem_item = proposal.to_memory_item(
                    tier=verdict.forced_tier or "stm",
                    scope=proposal.scope,
                )

                # Apply quarantine flags
                if verdict.action == "quarantine":
                    if verdict.forced_non_injectable:
                        mem_item.injectable = False
                    if verdict.forced_validation:
                        mem_item.validation = verdict.forced_validation
                    if verdict.forced_expires_at:
                        mem_item.expires_at = verdict.forced_expires_at
                    quarantined += 1

                store.write_item(mem_item, reason="propose")
                accepted += 1
                per_item.append({
                    "id": mem_item.id,
                    "title": mem_item.title,
                    "action": verdict.action,
                    "reasons": verdict.reasons,
                })

            except Exception as e:
                rejected += 1
                per_item.append({
                    "title": item_d.get("title", "(unknown)"),
                    "action": "error",
                    "reasons": [str(e)],
                })

        result: Dict[str, Any] = {
            "status": "ok",
            "accepted": accepted,
            "rejected": rejected,
            "quarantined": quarantined,
            "items": per_item,
        }

        # Auto-consolidation trigger
        if accepted > 0:
            consol = _maybe_auto_consolidate(store, config, scope=scope)
            if consol is not None:
                result["consolidation_triggered"] = True
                result["consolidation_merged"] = consol.get("items_merged", 0)

        return result

    @mcp.tool()
    def memory_write(
        title: str,
        content: str,
        tags: Optional[str] = None,
        tier: str = "stm",
        type: str = "note",
        scope: str = "project",
    ) -> Dict[str, Any]:
        """Direct write to memory store (privileged, policy-checked).

        DEV/ADMIN only — bypasses proposal workflow but still runs policy
        checks (secret + injection detection). Use memory_propose for
        normal write operations.

        Args:
            title: Item title.
            content: Item content (max 2000 chars).
            tags: Comma-separated tags.
            tier: Memory tier — stm|mtm|ltm (default stm).
            type: Item type — fact|decision|definition|constraint|pattern|todo|pointer|note.
            scope: Memory scope (default "project").

        Returns:
            id: Stored item ID (or rejection reason).
        """
        tag_list = [t.strip() for t in tags.split(",")] if tags else []

        item = MemoryItem(
            tier=tier,
            type=type,
            title=title,
            content=content,
            tags=tag_list,
            scope=scope,
            provenance=MemoryProvenance(
                source_kind="tool",
                source_id="memory_write",
            ),
        )

        verdict = policy.evaluate_item(item)
        if verdict.action == "reject":
            return {
                "status": "rejected",
                "reasons": verdict.reasons,
            }

        if verdict.action == "quarantine":
            if verdict.forced_non_injectable:
                item.injectable = False
            if verdict.forced_validation:
                item.validation = verdict.forced_validation

        store.write_item(item, reason="write")
        return {
            "status": "ok",
            "id": item.id,
            "action": verdict.action,
        }

    # -- CRUD --------------------------------------------------------------

    @mcp.tool()
    def memory_read(
        ids: str,
    ) -> Dict[str, Any]:
        """Read memory items by their IDs.

        Args:
            ids: Comma-separated item IDs (e.g. "MEM-abc123,MEM-def456").

        Returns:
            items: Full item data for each found ID.
        """
        id_list = [i.strip() for i in ids.split(",") if i.strip()]
        items = store.read_items(id_list)
        return {
            "status": "ok",
            "items": [it.to_dict() for it in items],
            "found": len(items),
            "requested": len(id_list),
        }

    # -- LIFECYCLE ---------------------------------------------------------

    @mcp.tool()
    def memory_consolidate(
        scope: str = "project",
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Trigger memory consolidation: deduplication, merge, and tier promotion.

        Deterministic consolidation: clusters STM items by type+tags (Jaccard),
        merges each cluster (longest content wins), promotes high-usage to LTM.

        Args:
            scope: Scope to consolidate (default "project").
            dry_run: If True, compute clusters but don't write (default False).

        Returns:
            Consolidation results (clusters merged, items promoted, etc.).
        """
        pipeline = ConsolidationPipeline(store, config.consolidate)
        try:
            stats = pipeline.run(scope=scope, dry_run=dry_run)
            stats["status"] = "ok"
            return stats
        except Exception as e:
            return {"status": "error", "message": f"Consolidation failed: {e}"}

    @mcp.tool()
    def memory_stats(
        scope: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Memory store statistics: item counts, tier distribution, search status.

        Args:
            scope: Filter stats by scope (optional).

        Returns:
            total_items, by_tier, by_type, events_count, fts5_available, etc.
        """
        stats = store.stats()
        stats["status"] = "ok"
        stats["format_version"] = FORMAT_VERSION
        return stats

    # -- Log registered tool count -----------------------------------------
    logger.info("Registered 7 memory MCP tools")


# -- Helpers ---------------------------------------------------------------

def _item_to_format_dict(item: MemoryItem) -> Dict[str, Any]:
    """Convert a MemoryItem to the dict format expected by formatting.py."""
    return {
        "id": item.id,
        "tier": item.tier,
        "validation": item.validation,
        "type": item.type,
        "title": item.title,
        "content": item.content,
        "provenance": item.provenance.to_dict(),
        "tags": item.tags,
        "confidence": item.confidence,
        "entities": item.entities,
        "injectable": item.injectable,
    }


def _maybe_auto_consolidate(
    store: MemoryStore,
    config: MemoryConfig,
    scope: str = "project",
) -> Optional[Dict[str, Any]]:
    """
    Check if STM count exceeds threshold and trigger consolidation.

    Returns consolidation result dict if triggered, None otherwise.
    """
    cfg = config.consolidate
    if not cfg.enabled:
        return None

    stm_count = store.count_items(tier="stm", scope=scope)
    if stm_count < cfg.stm_threshold:
        return None

    logger.info(
        "Auto-consolidation triggered: STM count %d >= threshold %d",
        stm_count, cfg.stm_threshold,
    )
    pipeline = ConsolidationPipeline(store, cfg)
    return pipeline.run(scope=scope)


def _item_matches_domain(item: Dict[str, Any], domain: str) -> bool:
    """Check if an item matches a domain filter (tag-based heuristic)."""
    domain_lower = domain.lower()
    tags = item.get("tags", [])
    title = item.get("title", "").lower()
    if any(domain_lower in t.lower() for t in tags):
        return True
    if domain_lower in title:
        return True
    return False
