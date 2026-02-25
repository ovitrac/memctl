"""
memctl MCP Tools — 21 memory tools for MCP integration.

Thin wrappers around MemoryStore, MemoryPolicy, and module-level functions.
Each tool follows the locked middleware order:

    ① Path guard       — validate db path, reject traversal (L0)
    ② Session resolve  — get or create session from MCP context (L1)
    ③ Rate limiter     — check read/write budget for session (L1)
    ④ Tool execution   — guard size caps → policy → business logic (L0+L2)
    ⑤ Audit log        — always, including on failure (L1, in finally block)

Tool hierarchy:
    PRIMARY:    memory_recall    — token-budgeted injection (canonical contract)
                memory_recall_best_effort — coached multi-step retrieval with cascade trace
    SECONDARY:  memory_search    — interactive discovery
    WRITE:      memory_propose   — governed write, memory_write — privileged
    CRUD:       memory_read      — read by IDs
    LIFECYCLE:  memory_consolidate, memory_stats, memory_promote
    COMPARE:    memory_diff
    FOLDER:     memory_mount, memory_sync, memory_inspect, memory_ask
    DATA:       memory_export, memory_import
    LOOP:       memory_loop
    CONFIG:     memory_eco
    ADMIN:      memory_reindex, memory_reset

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

from __future__ import annotations

import io
import json
import logging
import time
from typing import Any, Dict, List, Optional

from memctl.config import MemoryConfig
from memctl.consolidate import ConsolidationPipeline
from memctl.mcp.formatting import (
    FORMAT_VERSION,
    format_combined_prompt,
    format_injection_block,
    format_search_results,
)
from memctl.policy import MemoryPolicy
from memctl.query import classify_mode, normalize_query, suggest_budget
from memctl.store import MemoryStore
from memctl.types import MemoryItem, MemoryProposal, MemoryProvenance

logger = logging.getLogger(__name__)


def register_memory_tools(
    mcp,
    store: MemoryStore,
    policy: MemoryPolicy,
    config: MemoryConfig,
    *,
    guard=None,
    rate_limiter=None,
    session_tracker=None,
    audit=None,
) -> None:
    """
    Register all 21 memory MCP tools on a FastMCP server instance.

    Args:
        mcp: FastMCP server instance.
        store: Fully initialized MemoryStore.
        policy: MemoryPolicy for write governance.
        config: MemoryConfig for consolidation thresholds.
        guard: ServerGuard for path/size validation (L0).
        rate_limiter: RateLimiter for throttling (L1).
        session_tracker: SessionTracker for session state (L1).
        audit: AuditLogger for structured logging (L1).
    """
    # Import middleware types only when available
    from memctl.mcp.audit import AuditLogger
    from memctl.mcp.guard import GuardError, ServerGuard
    from memctl.mcp.rate_limiter import RateLimitExceeded
    from memctl.mcp.session import DEFAULT_SESSION_ID, SessionTracker

    db_path = config.store.db_path

    # Fallback middleware if not provided
    if guard is None:
        guard = ServerGuard()
    if session_tracker is None:
        session_tracker = SessionTracker()
    if audit is None:
        audit = AuditLogger()

    # Compute audit db path once (root-relative)
    from pathlib import Path
    _audit_db = guard.relative_db_path(Path(db_path).resolve())

    def _sid() -> str:
        """Resolve session ID (FastMCP context or fallback)."""
        return session_tracker.resolve_session_id(None)

    # =====================================================================
    # PRIMARY: Token-budgeted injection
    # =====================================================================

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
        t0 = time.monotonic()
        rid = audit.new_rid()
        session_id = _sid()
        outcome = "ok"
        detail: Dict[str, Any] = {}
        try:
            # ③ Rate limiter (read)
            if rate_limiter:
                rate_limiter.check_read(session_id)

            # ④ Business logic
            raw_items = store.search_fulltext(
                query, tier=tier, scope=scope, limit=50,
            )

            # Filter non-injectable items
            injectable = [it for it in raw_items if it.injectable]

            # Build dicts for formatting
            enriched = [_item_to_format_dict(it) for it in injectable]

            # FTS cascade metadata (v0.11)
            meta = store._last_search_meta

            inject_text = format_injection_block(
                enriched,
                budget_tokens=budget_tokens,
                total_matched=len(enriched),
                injection_type="memory_recall",
                fts_strategy=meta.strategy if meta else None,
                fts_dropped_terms=meta.dropped_terms if meta else None,
            )

            catalog = format_search_results(enriched, query=query)
            tokens_used = len(inject_text) // 4 if inject_text else 0

            detail = {"query_len": len(query), "matched": len(enriched), "tokens": tokens_used}
            fts_info: Dict[str, Any] = {}
            if meta:
                fts_info = {
                    "fts_strategy": meta.strategy,
                    "fts_original_terms": meta.original_terms,
                    "fts_effective_terms": meta.effective_terms,
                    "fts_dropped_terms": meta.dropped_terms,
                }
                detail["fts_strategy"] = meta.strategy

            result: Dict[str, Any] = {
                "status": "ok",
                "inject_text": inject_text,
                "items": catalog,
                "tokens_used": tokens_used,
                "matched": len(enriched),
                "format_version": FORMAT_VERSION,
                **fts_info,
            }

            # Query-length hint (eco guardrail)
            query_words = query.strip().split()
            if len(query_words) > 4:
                normalized = normalize_query(query)
                if normalized != query:
                    result["hint"] = (
                        "FTS works best with 2-3 keywords. "
                        f"Try: '{normalized}' instead of full sentences."
                    )

            # Zero-result guidance (eco guardrail)
            if not enriched:
                strategy_note = ""
                if meta and meta.strategy != "AND":
                    strategy_note = (
                        f" (tried cascade: {meta.strategy}, "
                        f"dropped: {meta.dropped_terms})"
                    )
                result["hint"] = (
                    f"No results found{strategy_note}. Try:\n"
                    "1. Use class/method names instead of descriptions\n"
                    "2. Remove articles and prepositions\n"
                    "3. Use 'memory_inspect' to see the folder structure first"
                )

            # Morphological miss hint (v0.12.1)
            if meta and meta.morphological_hint and "hint" not in result:
                result["hint"] = meta.morphological_hint

            return result

        except RateLimitExceeded as e:
            outcome = "rate_limited"
            return {"status": "rate_limited", "retry_after_ms": e.retry_after_ms, "message": str(e)}
        except Exception as e:
            outcome = "error"
            return {"status": "error", "message": f"Recall failed: {e}"}
        finally:
            audit.log("memory_recall", rid, session_id, _audit_db,
                      outcome, detail, (time.monotonic() - t0) * 1000)

    # =====================================================================
    # PRIMARY: Best-effort retrieval with cascade trace
    # =====================================================================

    @mcp.tool()
    def memory_recall_best_effort(
        query: str,
        budget_tokens: int = 1500,
        tier: Optional[str] = None,
        scope: Optional[str] = None,
        max_steps: int = 3,
        mode: str = "auto",
    ) -> Dict[str, Any]:
        """Multi-step best-effort memory retrieval with full cascade transparency.

        Use this tool for **exploratory recall** when you need prior knowledge
        from the memory store and want full visibility into what happened.

        Query discipline (effectiveness by query type):
            identifiers (CamelCase, snake_case, UPPER_CASE) → ~100% recall
            domain term pairs (e.g. "auth middleware")       → ~90% recall
            natural language 2-3 keywords                    → 60-80% recall
            full sentences                                   → ~40% recall

        When to use which tool:
            memory_recall_best_effort → exploration, coached multi-step retrieval
            memory_recall             → programmatic injection (stable contract)
            Grep / Glob               → after a miss, for direct file search

        Answer contract for consumers of the returned context:
            1. Retrieved — cite sources from inject_text (tier, provenance)
            2. Analysis  — your reasoning on top of retrieved context

        Zero-result guidance: check query_used vs your raw query. Try
        identifiers or base forms. Use memory_inspect to verify indexed scope.
        Use memory_reindex if content was added after last index.

        Args:
            query: Natural language or identifier search query.
            budget_tokens: Maximum tokens for injection block (default 1500).
            tier: Filter by tier (stm|mtm|ltm). None = all.
            scope: Filter by scope. None = all.
            max_steps: Maximum retrieval attempts (1-5, default 3).
            mode: "auto" (default), "strict" (single-pass, no retry).

        Returns:
            status: "ok" or "error".
            inject_text: Formatted injection block (format_version=1).
            items: Structured item list.
            tokens_used: Actual tokens consumed.
            matched: Total items matching query.
            format_version: Injection format version.
            query_used: Normalized query actually sent to FTS.
            strategy_used: Final FTS strategy (AND/REDUCED_AND/OR_FALLBACK/LIKE).
            steps: List of step dicts [{step, action, query, strategy, hits}].
            hint: Guidance text (present on zero results or query coaching).
        """
        t0 = time.monotonic()
        rid = audit.new_rid()
        session_id = _sid()
        outcome = "ok"
        detail: Dict[str, Any] = {}
        try:
            # ③ Rate limiter (read)
            if rate_limiter:
                rate_limiter.check_read(session_id)

            # Clamp max_steps to [1, 5]
            effective_max_steps = max(1, min(5, max_steps))
            if mode == "strict":
                effective_max_steps = 1

            steps: List[Dict[str, Any]] = []
            raw_items: List[MemoryItem] = []
            final_query = query
            final_meta = None

            # -- Step 1: NORMALIZE --
            from memctl.query import _is_identifier
            normalized = normalize_query(query)
            norm_changed = normalized != query
            steps.append({
                "step": 1,
                "action": "normalize",
                "query_in": query,
                "query_out": normalized,
                "changed": norm_changed,
            })

            # -- Step 2: SEARCH with normalized query --
            raw_items = store.search_fulltext(
                normalized, tier=tier, scope=scope, limit=50,
            )
            meta = store._last_search_meta
            final_meta = meta
            final_query = normalized

            steps.append({
                "step": 2,
                "action": "search",
                "query": normalized,
                "strategy": meta.strategy if meta else "unknown",
                "hits": len(raw_items),
            })

            # -- Step 3: RETRY with identifiers (if 0 results) --
            step_num = 3
            if not raw_items and step_num <= effective_max_steps:
                retry_q = _extract_best_retry_query(query, normalized)
                if retry_q and retry_q != normalized:
                    raw_items = store.search_fulltext(
                        retry_q, tier=tier, scope=scope, limit=50,
                    )
                    meta = store._last_search_meta
                    if raw_items:
                        final_meta = meta
                        final_query = retry_q
                    steps.append({
                        "step": step_num,
                        "action": "retry_identifiers",
                        "query": retry_q,
                        "strategy": meta.strategy if meta else "unknown",
                        "hits": len(raw_items),
                    })

            # -- Step 4: RETRY with broadest single term (if still 0) --
            step_num = 4
            if not raw_items and step_num <= effective_max_steps:
                words = query.strip().split()
                longest = max(words, key=len) if words else ""
                if longest and longest != normalized and longest != final_query:
                    raw_items = store.search_fulltext(
                        longest, tier=tier, scope=scope, limit=50,
                    )
                    meta = store._last_search_meta
                    if raw_items:
                        final_meta = meta
                        final_query = longest
                    steps.append({
                        "step": step_num,
                        "action": "retry_broadest",
                        "query": longest,
                        "strategy": meta.strategy if meta else "unknown",
                        "hits": len(raw_items),
                    })

            # Filter non-injectable items
            injectable = [it for it in raw_items if it.injectable]

            # Build dicts for formatting
            enriched = [_item_to_format_dict(it) for it in injectable]

            inject_text = format_injection_block(
                enriched,
                budget_tokens=budget_tokens,
                total_matched=len(enriched),
                injection_type="memory_recall",
                fts_strategy=final_meta.strategy if final_meta else None,
                fts_dropped_terms=final_meta.dropped_terms if final_meta else None,
            )

            catalog = format_search_results(enriched, query=query)
            tokens_used = len(inject_text) // 4 if inject_text else 0

            detail = {
                "query_len": len(query), "matched": len(enriched),
                "tokens": tokens_used, "steps": len(steps),
            }

            result: Dict[str, Any] = {
                "status": "ok",
                "inject_text": inject_text,
                "items": catalog,
                "tokens_used": tokens_used,
                "matched": len(enriched),
                "format_version": FORMAT_VERSION,
                "query_used": final_query,
                "strategy_used": final_meta.strategy if final_meta else "unknown",
                "steps": steps,
            }

            # Zero-result hint with two suggestions
            if not enriched:
                suggestions = _suggest_next_queries(query, normalized, final_meta)
                strategy_note = ""
                if final_meta and final_meta.strategy != "AND":
                    strategy_note = (
                        f" (cascade reached: {final_meta.strategy})"
                    )
                result["hint"] = (
                    f"No results after {len(steps)} steps{strategy_note}. "
                    "Suggestions:\n"
                    + "\n".join(f"  {i+1}. {s}" for i, s in enumerate(suggestions))
                    + "\nAlso try: memory_inspect to verify indexed scope, "
                    "memory_reindex if content was added recently."
                )

            # Morphological hint when results exist but may miss variants
            if final_meta and final_meta.morphological_hint and "hint" not in result:
                result["hint"] = final_meta.morphological_hint

            return result

        except RateLimitExceeded as e:
            outcome = "rate_limited"
            return {"status": "rate_limited", "retry_after_ms": e.retry_after_ms, "message": str(e)}
        except Exception as e:
            outcome = "error"
            return {"status": "error", "message": f"Recall best-effort failed: {e}"}
        finally:
            audit.log("memory_recall_best_effort", rid, session_id, _audit_db,
                      outcome, detail, (time.monotonic() - t0) * 1000)

    # =====================================================================
    # SECONDARY: Interactive search
    # =====================================================================

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
        t0 = time.monotonic()
        rid = audit.new_rid()
        session_id = _sid()
        outcome = "ok"
        detail: Dict[str, Any] = {}
        try:
            if rate_limiter:
                rate_limiter.check_read(session_id)

            if tags:
                tag_list = [t.strip() for t in tags.split(",")]
                raw_items = store.search_by_tags(
                    tag_list, tier=tier, scope=scope, limit=k,
                )
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

            results = format_search_results(
                [_item_to_format_dict(it) for it in raw_items],
                query=query,
            )

            for i, it in enumerate(raw_items):
                if not it.injectable:
                    results[i]["quarantined"] = True

            detail = {"query_len": len(query), "results": len(results)}

            # FTS cascade metadata (v0.11)
            meta = store._last_search_meta
            fts_info: Dict[str, Any] = {}
            if meta:
                fts_info = {
                    "fts_strategy": meta.strategy,
                    "fts_original_terms": meta.original_terms,
                    "fts_effective_terms": meta.effective_terms,
                    "fts_dropped_terms": meta.dropped_terms,
                }
                detail["fts_strategy"] = meta.strategy

            search_result: Dict[str, Any] = {
                "status": "ok",
                "count": len(results),
                "items": results,
                **fts_info,
            }

            # Query-length hint (eco guardrail)
            query_words = query.strip().split()
            if len(query_words) > 4:
                normalized = normalize_query(query)
                if normalized != query:
                    search_result["hint"] = (
                        "FTS works best with 2-3 keywords. "
                        f"Try: '{normalized}' instead of full sentences."
                    )

            # Zero-result guidance (eco guardrail)
            if not results:
                strategy_note = ""
                if meta and meta.strategy != "AND":
                    strategy_note = (
                        f" (tried cascade: {meta.strategy}, "
                        f"dropped: {meta.dropped_terms})"
                    )
                search_result["hint"] = (
                    f"No results found{strategy_note}. Try:\n"
                    "1. Use class/method names instead of descriptions\n"
                    "2. Remove articles and prepositions\n"
                    "3. Use 'memory_inspect' to see the folder structure first"
                )

            # Morphological miss hint (v0.12.1)
            if meta and meta.morphological_hint and "hint" not in search_result:
                search_result["hint"] = meta.morphological_hint

            return search_result

        except RateLimitExceeded as e:
            outcome = "rate_limited"
            return {"status": "rate_limited", "retry_after_ms": e.retry_after_ms, "message": str(e)}
        except Exception as e:
            outcome = "error"
            return {"status": "error", "message": f"Search failed: {e}"}
        finally:
            audit.log("memory_search", rid, session_id, _audit_db,
                      outcome, detail, (time.monotonic() - t0) * 1000)

    # =====================================================================
    # WRITE PATH
    # =====================================================================

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
        t0 = time.monotonic()
        rid = audit.new_rid()
        session_id = _sid()
        outcome = "ok"
        detail: Dict[str, Any] = {}
        try:
            # ③ Rate limiter (write)
            if rate_limiter:
                rate_limiter.check_write(session_id)

            # Parse items
            try:
                item_list = json.loads(items)
            except (json.JSONDecodeError, TypeError) as e:
                outcome = "error"
                return {"status": "error", "message": f"Invalid JSON in items: {e}"}

            if not isinstance(item_list, list):
                item_list = [item_list]

            # ④a Guard: size cap on total content
            total_bytes = len(items.encode("utf-8"))
            guard.check_write_size(items)
            guard.check_write_budget(session_id, total_bytes)

            # Rate limit proposal count
            if rate_limiter:
                rate_limiter.check_proposals(session_id, len(item_list))

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

                    mem_item = proposal.to_memory_item(
                        tier=verdict.forced_tier or "stm",
                        scope=proposal.scope,
                    )

                    if verdict.action == "quarantine":
                        if verdict.forced_non_injectable:
                            mem_item.injectable = False
                        if verdict.forced_validation:
                            mem_item.validation = verdict.forced_validation
                        if verdict.forced_expires_at:
                            mem_item.expires_at = verdict.forced_expires_at
                        quarantined += 1

                    if store.exists_by_content_hash(mem_item.content_hash):
                        per_item.append({
                            "title": mem_item.title,
                            "action": "duplicate",
                            "reasons": ["content already exists"],
                        })
                        continue

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

            detail = {
                "proposed": len(item_list),
                "accepted": accepted,
                "rejected": rejected,
                "quarantined": quarantined,
                "bytes": total_bytes,
            }

            result: Dict[str, Any] = {
                "status": "ok",
                "accepted": accepted,
                "rejected": rejected,
                "quarantined": quarantined,
                "items": per_item,
            }

            if accepted > 0:
                consol = _maybe_auto_consolidate(store, config, scope=scope)
                if consol is not None:
                    result["consolidation_triggered"] = True
                    result["consolidation_merged"] = consol.get("items_merged", 0)

            return result

        except GuardError as e:
            outcome = "error"
            return {"status": "error", "message": str(e)}
        except RateLimitExceeded as e:
            outcome = "rate_limited"
            return {"status": "rate_limited", "retry_after_ms": e.retry_after_ms, "message": str(e)}
        except Exception as e:
            outcome = "error"
            return {"status": "error", "message": f"Propose failed: {e}"}
        finally:
            audit.log("memory_propose", rid, session_id, _audit_db,
                      outcome, detail, (time.monotonic() - t0) * 1000)

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
        checks (secret + injection + PII detection). Use memory_propose for
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
        t0 = time.monotonic()
        rid = audit.new_rid()
        session_id = _sid()
        outcome = "ok"
        detail: Dict[str, Any] = {}
        policy_detail = None
        try:
            # ③ Rate limiter (write)
            if rate_limiter:
                rate_limiter.check_write(session_id)

            # ④a Guard: size cap
            guard.check_write_size(content)
            content_bytes = len(content.encode("utf-8"))
            guard.check_write_budget(session_id, content_bytes)

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

            # ④b Policy (L2)
            verdict = policy.evaluate_item(item)
            if verdict.reasons:
                policy_detail = {"action": verdict.action, "rule": verdict.reasons[0]}

            if verdict.action == "reject":
                outcome = "rejected"
                detail = audit.make_content_detail(content, policy_detail)
                return {
                    "status": "rejected",
                    "reasons": verdict.reasons,
                }

            if verdict.action == "quarantine":
                if verdict.forced_non_injectable:
                    item.injectable = False
                if verdict.forced_validation:
                    item.validation = verdict.forced_validation

            # ④c Business logic — dedup check
            if store.exists_by_content_hash(item.content_hash):
                detail = audit.make_content_detail(content, policy_detail)
                return {
                    "status": "duplicate",
                    "message": "content already exists",
                }

            store.write_item(item, reason="write")
            detail = audit.make_content_detail(content, policy_detail)

            return {
                "status": "ok",
                "id": item.id,
                "action": verdict.action,
            }

        except GuardError as e:
            outcome = "error"
            return {"status": "error", "message": str(e)}
        except RateLimitExceeded as e:
            outcome = "rate_limited"
            return {"status": "rate_limited", "retry_after_ms": e.retry_after_ms, "message": str(e)}
        except Exception as e:
            outcome = "error"
            return {"status": "error", "message": f"Write failed: {e}"}
        finally:
            audit.log("memory_write", rid, session_id, _audit_db,
                      outcome, detail, (time.monotonic() - t0) * 1000)

    # =====================================================================
    # CRUD
    # =====================================================================

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
        t0 = time.monotonic()
        rid = audit.new_rid()
        session_id = _sid()
        outcome = "ok"
        detail: Dict[str, Any] = {}
        try:
            if rate_limiter:
                rate_limiter.check_read(session_id)

            id_list = [i.strip() for i in ids.split(",") if i.strip()]
            found_items = store.read_items(id_list)
            detail = {"ids": len(id_list)}
            return {
                "status": "ok",
                "items": [it.to_dict() for it in found_items],
                "found": len(found_items),
                "requested": len(id_list),
            }

        except RateLimitExceeded as e:
            outcome = "rate_limited"
            return {"status": "rate_limited", "retry_after_ms": e.retry_after_ms, "message": str(e)}
        except Exception as e:
            outcome = "error"
            return {"status": "error", "message": f"Read failed: {e}"}
        finally:
            audit.log("memory_read", rid, session_id, _audit_db,
                      outcome, detail, (time.monotonic() - t0) * 1000)

    # =====================================================================
    # LIFECYCLE
    # =====================================================================

    @mcp.tool()
    def memory_consolidate(
        scope: str = "project",
        dry_run: bool = False,
        all_scopes: bool = False,
    ) -> Dict[str, Any]:
        """Trigger memory consolidation: deduplication, merge, and tier promotion.

        Deterministic consolidation: clusters STM items by type+tags (Jaccard)
        + source affinity, merges each cluster (longest content wins),
        promotes high-usage to LTM.

        Args:
            scope: Scope to consolidate (default "project").
            dry_run: If True, compute clusters but don't write (default False).
            all_scopes: If True, consolidate all scopes independently
                        (ignores scope parameter).

        Returns:
            Consolidation results (clusters merged, items promoted, etc.).
        """
        t0 = time.monotonic()
        rid = audit.new_rid()
        session_id = _sid()
        outcome = "ok"
        detail: Dict[str, Any] = {}
        try:
            if rate_limiter:
                rate_limiter.check_write(session_id)

            pipeline = ConsolidationPipeline(store, config.consolidate)
            effective_scope = None if all_scopes else scope
            stats = pipeline.run(scope=effective_scope, dry_run=dry_run)
            detail = {
                "merged": stats.get("items_merged", 0),
                "archived": stats.get("items_archived", 0),
            }
            stats["status"] = "ok"
            return stats

        except RateLimitExceeded as e:
            outcome = "rate_limited"
            return {"status": "rate_limited", "retry_after_ms": e.retry_after_ms, "message": str(e)}
        except Exception as e:
            outcome = "error"
            return {"status": "error", "message": f"Consolidation failed: {e}"}
        finally:
            audit.log("memory_consolidate", rid, session_id, _audit_db,
                      outcome, detail, (time.monotonic() - t0) * 1000)

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
        # EXEMPT from rate limiting (health-check must always respond)
        t0 = time.monotonic()
        rid = audit.new_rid()
        session_id = _sid()
        outcome = "ok"
        try:
            stats = store.stats()
            stats["status"] = "ok"
            stats["format_version"] = FORMAT_VERSION
            return stats
        except Exception as e:
            outcome = "error"
            return {"status": "error", "message": f"Stats failed: {e}"}
        finally:
            audit.log("memory_stats", rid, session_id, _audit_db,
                      outcome, {}, (time.monotonic() - t0) * 1000)

    @mcp.tool()
    def memory_status() -> Dict[str, Any]:
        """Project memory health dashboard: eco state, stats, mounts, last scan.

        Aggregated read-only view combining eco mode state, store statistics,
        mount points, and last scan timestamp. Use this for a quick overview
        of the project's memory health.

        Returns:
            eco_mode, db_path, db_exists, total_items, by_tier, by_type,
            fts5_available, fts_tokenizer, mounts, last_scan, events_count.
        """
        # EXEMPT from rate limiting (health-check must always respond)
        t0 = time.monotonic()
        rid = audit.new_rid()
        session_id = _sid()
        outcome = "ok"
        try:
            # Eco mode state
            eco_config_path = Path(".claude/eco/config.json")
            eco_disabled = Path(".memory/.eco-disabled")
            # Backward compat: migrate old flag location
            old_eco_disabled = Path(".claude/eco/.disabled")
            if old_eco_disabled.exists() and not eco_disabled.exists():
                try:
                    eco_disabled.parent.mkdir(parents=True, exist_ok=True)
                    old_eco_disabled.rename(eco_disabled)
                except OSError:
                    pass
            eco_state = "disabled" if eco_disabled.exists() else (
                "active" if eco_config_path.exists() else "not installed"
            )

            db_exists = Path(db_path).exists()

            if not db_exists:
                return {
                    "status": "ok",
                    "eco_mode": eco_state,
                    "db_path": db_path,
                    "db_exists": False,
                }

            stats = store.stats()
            mounts = store.list_mounts()
            last_scan = store.last_event(actions=["memory_inspect", "sync"])

            return {
                "status": "ok",
                "eco_mode": eco_state,
                "db_path": db_path,
                "db_exists": True,
                "total_items": stats["total_items"],
                "by_tier": stats["by_tier"],
                "by_type": stats["by_type"],
                "fts5_available": stats["fts5_available"],
                "fts_tokenizer": stats.get("fts_tokenizer"),
                "fts_tokenizer_mismatch": stats.get("fts_tokenizer_mismatch", False),
                "mounts": len(mounts),
                "last_scan": last_scan,
                "events_count": stats["events_count"],
            }
        except Exception as e:
            outcome = "error"
            return {"status": "error", "message": f"Status failed: {e}"}
        finally:
            audit.log("memory_status", rid, session_id, _audit_db,
                      outcome, {}, (time.monotonic() - t0) * 1000)

    # =====================================================================
    # CONFIG: memory_eco  (v0.16)
    # =====================================================================

    @mcp.tool()
    def memory_eco(
        action: str = "status",
    ) -> Dict[str, Any]:
        """Toggle or query eco mode state.

        Args:
            action: "on", "off", or "status" (default: "status").

        Returns:
            eco_mode: current state after action ("active", "disabled", "not installed").
            action_taken: what was done.
        """
        # EXEMPT from rate limiting (config toggle must always respond)
        t0 = time.monotonic()
        rid = audit.new_rid()
        session_id = _sid()
        outcome = "ok"
        try:
            eco_config_path = Path(".claude/eco/config.json")
            eco_disabled = Path(".memory/.eco-disabled")

            # Backward compat: migrate old flag location
            old_eco_disabled = Path(".claude/eco/.disabled")
            if old_eco_disabled.exists() and not eco_disabled.exists():
                try:
                    eco_disabled.parent.mkdir(parents=True, exist_ok=True)
                    old_eco_disabled.rename(eco_disabled)
                except OSError:
                    pass

            if action == "on":
                if not eco_config_path.exists():
                    return {
                        "status": "error",
                        "eco_mode": "not installed",
                        "message": "eco mode not installed. Run install_eco.sh first.",
                    }
                eco_disabled.unlink(missing_ok=True)
                return {
                    "status": "ok",
                    "eco_mode": "active",
                    "action_taken": "enabled",
                }
            elif action == "off":
                eco_disabled.parent.mkdir(parents=True, exist_ok=True)
                eco_disabled.touch()
                return {
                    "status": "ok",
                    "eco_mode": "disabled",
                    "action_taken": "disabled",
                }
            else:
                # status
                eco_state = "disabled" if eco_disabled.exists() else (
                    "active" if eco_config_path.exists() else "not installed"
                )
                return {
                    "status": "ok",
                    "eco_mode": eco_state,
                    "action_taken": "none",
                }
        except Exception as e:
            outcome = "error"
            return {"status": "error", "message": f"Eco toggle failed: {e}"}
        finally:
            audit.log("memory_eco", rid, session_id, _audit_db,
                      outcome, {"action": action}, (time.monotonic() - t0) * 1000)

    # =====================================================================
    # COMPARE: memory_diff  (v0.15)
    # =====================================================================

    @mcp.tool()
    def memory_diff(
        id1: str,
        id2: str = "",
        revision: int = 0,
    ) -> Dict[str, Any]:
        """Compare two memory items or an item against a past revision.

        Produces a unified content diff, metadata change summary, and
        similarity score. Read-only — no mutations.

        Args:
            id1: First item ID (required).
            id2: Second item ID (item-vs-item mode). Empty = revision mode.
            revision: Compare against specific revision number.
                      0 = latest revision (when id2 is empty).

        Returns:
            content_diff: Unified diff lines.
            metadata_changes: List of {field, old, new} for changed metadata.
            similarity_score: Float in [0.0, 1.0].
            identical: True if content and metadata match.
        """
        t0 = time.monotonic()
        rid = audit.new_rid()
        session_id = _sid()
        outcome = "ok"
        detail: Dict[str, Any] = {"id1": id1}
        try:
            if rate_limiter:
                rate_limiter.check_read(session_id)

            from memctl.diff import compute_diff, resolve_diff_targets

            item_a, item_b, label_a, label_b = resolve_diff_targets(
                store, id1, id2=id2, revision=revision,
            )
            result = compute_diff(
                item_a, item_b, label_a=label_a, label_b=label_b,
            )

            detail["identical"] = result["identical"]
            return {
                "status": "ok",
                "label_a": label_a,
                "label_b": label_b,
                "identical": result["identical"],
                "similarity_score": result["similarity_score"],
                "content_diff": result["content_diff"],
                "metadata_changes": result["metadata_changes"],
            }

        except ValueError as e:
            outcome = "error"
            return {"status": "error", "message": str(e)}
        except RateLimitExceeded as e:
            outcome = "rate_limited"
            return {"status": "rate_limited", "retry_after_ms": e.retry_after_ms, "message": str(e)}
        except Exception as e:
            outcome = "error"
            return {"status": "error", "message": f"Diff failed: {e}"}
        finally:
            audit.log("memory_diff", rid, session_id, _audit_db,
                      outcome, detail, (time.monotonic() - t0) * 1000)

    # =====================================================================
    # FOLDER: mount, sync, inspect, ask  (v0.7)
    # =====================================================================

    @mcp.tool()
    def memory_mount(
        action: str = "list",
        path: Optional[str] = None,
        name: Optional[str] = None,
        ignore_patterns: Optional[str] = None,
        lang: Optional[str] = None,
        mount_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Register, list, or remove folder mounts.

        Manages folder mount points for structured source ingestion.
        Metadata-only — no content written on register.

        Args:
            action: "register" to add a mount, "list" to show all, "remove" to delete.
            path: Folder path (required for register).
            name: Human-readable label for the mount.
            ignore_patterns: Comma-separated glob patterns to exclude during sync.
            lang: Language hint for FTS tokenizer (fr|en|mix).
            mount_id: Mount ID or name (required for remove).

        Returns:
            mount_id and path (register), mounts list (list), or removal status.
        """
        # EXEMPT from rate limiting (metadata-only)
        t0 = time.monotonic()
        rid = audit.new_rid()
        session_id = _sid()
        outcome = "ok"
        detail: Dict[str, Any] = {"action": action}
        try:
            from memctl.mount import register_mount, list_mounts, remove_mount

            if action == "register":
                if not path:
                    outcome = "error"
                    return {"status": "error", "message": "path is required for register"}
                ignore = (
                    [p.strip() for p in ignore_patterns.split(",") if p.strip()]
                    if ignore_patterns else None
                )
                mid = register_mount(
                    db_path, path,
                    name=name,
                    ignore_patterns=ignore,
                    lang_hint=lang,
                )
                detail["path"] = path
                return {"status": "ok", "mount_id": mid, "path": path}

            elif action == "list":
                mounts = list_mounts(db_path)
                return {"status": "ok", "mounts": mounts, "count": len(mounts)}

            elif action == "remove":
                if not mount_id:
                    outcome = "error"
                    return {"status": "error", "message": "mount_id is required for remove"}
                ok = remove_mount(db_path, mount_id)
                if ok:
                    return {"status": "ok", "removed": mount_id}
                else:
                    outcome = "error"
                    return {"status": "error", "message": f"Mount not found: {mount_id}"}

            else:
                outcome = "error"
                return {"status": "error", "message": f"Unknown action: {action}"}

        except (FileNotFoundError, NotADirectoryError) as e:
            outcome = "error"
            return {"status": "error", "message": str(e)}
        except Exception as e:
            outcome = "error"
            return {"status": "error", "message": f"Mount failed: {e}"}
        finally:
            audit.log("memory_mount", rid, session_id, _audit_db,
                      outcome, detail, (time.monotonic() - t0) * 1000)

    @mcp.tool()
    def memory_sync(
        path: Optional[str] = None,
        full: bool = False,
        scope: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Sync mounted folders into the memory store.

        Scans files, detects changes (delta mode), and ingests new/modified
        content. Without a path, syncs all registered mounts.

        Args:
            path: Folder path to sync (auto-registers if not mounted).
                  None = sync all registered mounts.
            full: If True, re-process all files ignoring delta cache.
            scope: Override scope for synced items. If None, scope is
                   derived from the folder name automatically.

        Returns:
            Sync statistics (files scanned, new, changed, chunks created).
        """
        t0 = time.monotonic()
        rid = audit.new_rid()
        session_id = _sid()
        outcome = "ok"
        detail: Dict[str, Any] = {}
        try:
            if rate_limiter:
                rate_limiter.check_write(session_id)

            from memctl.sync import sync_mount, sync_all

            if path:
                result = sync_mount(
                    db_path, path,
                    delta=not full,
                    quiet=True,
                    scope=scope,
                )
                detail = {
                    "synced": 1,
                    "new": result.files_new,
                    "updated": result.files_changed,
                }
                return {
                    "status": "ok",
                    "files_scanned": result.files_scanned,
                    "files_new": result.files_new,
                    "files_changed": result.files_changed,
                    "chunks_created": result.chunks_created,
                }
            else:
                results = sync_all(db_path, delta=not full, quiet=True)
                synced = {}
                total_new = 0
                total_changed = 0
                for p, r in results.items():
                    synced[p] = r.to_dict()
                    total_new += r.files_new
                    total_changed += r.files_changed
                detail = {
                    "synced": len(results),
                    "new": total_new,
                    "updated": total_changed,
                }
                return {
                    "status": "ok",
                    "synced": synced,
                    "mount_count": len(results),
                }

        except RateLimitExceeded as e:
            outcome = "rate_limited"
            return {"status": "rate_limited", "retry_after_ms": e.retry_after_ms, "message": str(e)}
        except (FileNotFoundError, NotADirectoryError) as e:
            outcome = "error"
            return {"status": "error", "message": str(e)}
        except Exception as e:
            outcome = "error"
            return {"status": "error", "message": f"Sync failed: {e}"}
        finally:
            audit.log("memory_sync", rid, session_id, _audit_db,
                      outcome, detail, (time.monotonic() - t0) * 1000)

    @mcp.tool()
    def memory_inspect(
        path: Optional[str] = None,
        mount_id: Optional[str] = None,
        budget: int = 2200,
        sync_mode: str = "auto",
        output_format: str = "text",
    ) -> Dict[str, Any]:
        """Generate a structural injection block from corpus metadata.

        Produces a deterministic, token-bounded summary of folder structure:
        file counts, size, per-folder breakdown, extension distribution,
        top-5 largest files, and structural observations.

        Args:
            path: Folder to inspect (auto-mounts and auto-syncs as needed).
            mount_id: Existing mount ID to inspect (alternative to path).
            budget: Token budget for injection block (default 2200).
            sync_mode: "auto" (sync if stale), "always", or "never".
            output_format: "text" for injection block, "json" for structured stats.

        Returns:
            inject_text (text mode) or stats dict (json mode).
        """
        t0 = time.monotonic()
        rid = audit.new_rid()
        session_id = _sid()
        outcome = "ok"
        detail: Dict[str, Any] = {"format": output_format}
        try:
            if rate_limiter:
                rate_limiter.check_read(session_id)

            from memctl.inspect import inspect_path, inspect_mount, inspect_stats

            if path:
                detail["path"] = path
                result = inspect_path(
                    db_path, path,
                    sync_mode=sync_mode,
                    budget=budget,
                )
                if output_format == "json":
                    return {"status": "ok", **result.to_dict()}
                else:
                    text = inspect_mount(
                        db_path,
                        mount_id=result.mount_id,
                        mount_label=result.mount_label,
                        budget=budget,
                    )
                    return {
                        "status": "ok",
                        "inject_text": text,
                        "total_files": result.stats.get("total_files", 0),
                        "total_chunks": result.stats.get("total_chunks", 0),
                        "was_mounted": result.was_mounted,
                        "was_synced": result.was_synced,
                    }

            elif mount_id:
                if output_format == "json":
                    stats = inspect_stats(db_path, mount_id=mount_id)
                    return {"status": "ok", **stats}
                else:
                    text = inspect_mount(db_path, mount_id=mount_id, budget=budget)
                    return {"status": "ok", "inject_text": text}

            else:
                if output_format == "json":
                    stats = inspect_stats(db_path)
                    return {"status": "ok", **stats}
                else:
                    text = inspect_mount(db_path, budget=budget)
                    return {"status": "ok", "inject_text": text}

        except RateLimitExceeded as e:
            outcome = "rate_limited"
            return {"status": "rate_limited", "retry_after_ms": e.retry_after_ms, "message": str(e)}
        except (FileNotFoundError, NotADirectoryError, ValueError) as e:
            outcome = "error"
            return {"status": "error", "message": str(e)}
        except Exception as e:
            outcome = "error"
            return {"status": "error", "message": f"Inspect failed: {e}"}
        finally:
            audit.log("memory_inspect", rid, session_id, _audit_db,
                      outcome, detail, (time.monotonic() - t0) * 1000)

    @mcp.tool()
    def memory_ask(
        path: str,
        question: str,
        llm_cmd: str,
        budget: int = 2200,
        inspect_cap: int = 600,
        protocol: str = "passive",
        max_calls: int = 1,
        threshold: float = 0.92,
        sync_mode: str = "auto",
        timeout: int = 300,
    ) -> Dict[str, Any]:
        """Answer a question about a folder (one-shot Q&A).

        Orchestrates: auto-mount, auto-sync, structural inspect, scoped
        recall, and bounded loop to answer a question about folder contents.

        Args:
            path: Folder path to ask about.
            question: Question to answer.
            llm_cmd: LLM command (e.g. "claude -p", "ollama run mistral").
            budget: Total token budget (default 2200).
            inspect_cap: Tokens reserved for structural context (default 600).
            protocol: LLM output protocol — passive|json|regex (default passive).
            max_calls: Max loop iterations (default 1).
            threshold: Answer similarity threshold for convergence.
            sync_mode: "auto" (sync if stale), "always", or "never".
            timeout: LLM subprocess timeout in seconds (default 300).

        Returns:
            answer, mount_id, loop_iterations, stop_reason, converged.
        """
        t0 = time.monotonic()
        rid = audit.new_rid()
        session_id = _sid()
        outcome = "ok"
        detail: Dict[str, Any] = {"question_len": len(question)}
        try:
            if rate_limiter:
                rate_limiter.check_read(session_id)

            from memctl.ask import ask_folder

            result = ask_folder(
                path=path,
                question=question,
                llm_cmd=llm_cmd,
                db_path=db_path,
                sync_mode=sync_mode,
                budget=budget,
                inspect_cap=inspect_cap,
                protocol=protocol,
                max_calls=max_calls,
                threshold=threshold,
                timeout=timeout,
            )
            return {
                "status": "ok",
                "answer": result.answer,
                "mount_id": result.mount_id,
                "was_mounted": result.was_mounted,
                "was_synced": result.was_synced,
                "loop_iterations": result.loop_iterations,
                "stop_reason": result.stop_reason,
                "converged": result.converged,
            }

        except RateLimitExceeded as e:
            outcome = "rate_limited"
            return {"status": "rate_limited", "retry_after_ms": e.retry_after_ms, "message": str(e)}
        except (FileNotFoundError, NotADirectoryError, ValueError) as e:
            outcome = "error"
            return {"status": "error", "message": str(e)}
        except RuntimeError as e:
            outcome = "error"
            return {"status": "error", "message": f"LLM error: {e}"}
        except Exception as e:
            outcome = "error"
            return {"status": "error", "message": f"Ask failed: {e}"}
        finally:
            audit.log("memory_ask", rid, session_id, _audit_db,
                      outcome, detail, (time.monotonic() - t0) * 1000)

    # =====================================================================
    # DATA: export, import  (v0.7)
    # =====================================================================

    @mcp.tool()
    def memory_export(
        tier: Optional[str] = None,
        type_filter: Optional[str] = None,
        scope: Optional[str] = None,
        include_archived: bool = False,
    ) -> Dict[str, Any]:
        """Export memory items as structured data.

        Read-only export of memory items with optional filters.
        Capped at 1000 items per call.

        Args:
            tier: Filter by tier (stm|mtm|ltm). None = all.
            type_filter: Filter by type. None = all.
            scope: Filter by scope. None = all.
            include_archived: Include archived items (default False).

        Returns:
            count: Number of items exported.
            items: List of item dicts.
            truncated: True if more items exist beyond the cap.
        """
        t0 = time.monotonic()
        rid = audit.new_rid()
        session_id = _sid()
        outcome = "ok"
        detail: Dict[str, Any] = {}
        try:
            if rate_limiter:
                rate_limiter.check_read(session_id)

            from memctl.export_import import export_items

            buf = io.StringIO()
            count = export_items(
                db_path,
                tier=tier,
                type_filter=type_filter,
                scope=scope,
                exclude_archived=not include_archived,
                output=buf,
                log=lambda msg: None,
            )

            buf.seek(0)
            exported_items = []
            for line in buf:
                line = line.strip()
                if line:
                    exported_items.append(json.loads(line))

            truncated = len(exported_items) > 1000
            if truncated:
                exported_items = exported_items[:1000]

            detail = {"exported": len(exported_items), "truncated": truncated}

            return {
                "status": "ok",
                "count": len(exported_items),
                "items": exported_items,
                "truncated": truncated,
            }

        except RateLimitExceeded as e:
            outcome = "rate_limited"
            return {"status": "rate_limited", "retry_after_ms": e.retry_after_ms, "message": str(e)}
        except Exception as e:
            outcome = "error"
            return {"status": "error", "message": f"Export failed: {e}"}
        finally:
            audit.log("memory_export", rid, session_id, _audit_db,
                      outcome, detail, (time.monotonic() - t0) * 1000)

    @mcp.tool()
    def memory_import(
        items: str,
        preserve_ids: bool = False,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Import memory items from a JSON array.

        Every item passes through the policy engine before storage.
        Content-hash deduplication prevents duplicate items.

        Args:
            items: JSON array string of item dicts. Each dict should have
                   at minimum: title, content. Optional: tier, type, tags,
                   scope, provenance, confidence, entities.
            preserve_ids: Keep original item IDs (default False).
            dry_run: Count items without writing (default False).

        Returns:
            total_lines, imported, skipped_dedup, skipped_policy, errors.
        """
        t0 = time.monotonic()
        rid = audit.new_rid()
        session_id = _sid()
        outcome = "ok"
        detail: Dict[str, Any] = {}
        try:
            # Parse JSON first to get item count
            try:
                item_list = json.loads(items)
            except (json.JSONDecodeError, TypeError) as e:
                outcome = "error"
                return {"status": "error", "message": f"Invalid JSON: {e}"}

            if not isinstance(item_list, list):
                item_list = [item_list]

            # ③ Rate limiter: import counted as N writes
            if rate_limiter:
                rate_limiter.check_write_n(session_id, len(item_list))

            # ④a Guard: batch size + total bytes
            guard.check_import_batch(len(item_list))
            total_bytes = len(items.encode("utf-8"))
            guard.check_write_budget(session_id, total_bytes)

            from memctl.export_import import import_items

            # Build JSONL stream
            jsonl_buf = io.StringIO()
            for item_d in item_list:
                jsonl_buf.write(json.dumps(item_d, ensure_ascii=False) + "\n")
            jsonl_buf.seek(0)

            result = import_items(
                db_path,
                jsonl_buf,
                preserve_ids=preserve_ids,
                dry_run=dry_run,
                log=lambda msg: None,
            )
            detail = {
                "items": result.total_lines,
                "imported": result.imported,
                "skipped": result.skipped_dedup + result.skipped_policy,
                "errors": result.errors,
                "bytes": total_bytes,
            }
            return {
                "status": "ok",
                "total_lines": result.total_lines,
                "imported": result.imported,
                "skipped_dedup": result.skipped_dedup,
                "skipped_policy": result.skipped_policy,
                "errors": result.errors,
            }

        except GuardError as e:
            outcome = "error"
            return {"status": "error", "message": str(e)}
        except RateLimitExceeded as e:
            outcome = "rate_limited"
            return {"status": "rate_limited", "retry_after_ms": e.retry_after_ms, "message": str(e)}
        except Exception as e:
            outcome = "error"
            return {"status": "error", "message": f"Import failed: {e}"}
        finally:
            audit.log("memory_import", rid, session_id, _audit_db,
                      outcome, detail, (time.monotonic() - t0) * 1000)

    # =====================================================================
    # LOOP  (v0.7)
    # =====================================================================

    @mcp.tool()
    def memory_loop(
        query: str,
        initial_context: str,
        llm_cmd: str,
        max_calls: int = 3,
        threshold: float = 0.92,
        query_threshold: float = 0.90,
        stable_steps: int = 2,
        protocol: str = "json",
        budget: int = 2200,
        timeout: int = 300,
    ) -> Dict[str, Any]:
        """Run a bounded recall-answer loop with an LLM.

        Iteratively queries the LLM with expanding context from memory
        recall until the answer converges (fixed-point) or limits are reached.

        Args:
            query: Question to answer via iterative recall.
            initial_context: Starting context (e.g. from memory_recall inject_text).
            llm_cmd: LLM command (e.g. "claude -p", "ollama run mistral").
            max_calls: Maximum LLM invocations (default 3).
            threshold: Answer fixed-point similarity threshold (default 0.92).
            query_threshold: Query cycle similarity threshold (default 0.90).
            stable_steps: Consecutive stable steps for convergence (default 2).
            protocol: LLM output protocol — json|regex|passive (default json).
            budget: Token budget for context (default 2200).
            timeout: LLM subprocess timeout in seconds (default 300).

        Returns:
            answer, iterations, converged, stop_reason, traces.
        """
        t0 = time.monotonic()
        rid = audit.new_rid()
        session_id = _sid()
        outcome = "ok"
        detail: Dict[str, Any] = {}
        try:
            if rate_limiter:
                rate_limiter.check_read(session_id)

            from memctl.loop import run_loop

            result = run_loop(
                initial_context=initial_context,
                query=query,
                llm_cmd=llm_cmd,
                db_path=db_path,
                max_calls=max_calls,
                threshold=threshold,
                query_threshold=query_threshold,
                stable_steps=stable_steps,
                protocol=protocol,
                budget=budget,
                timeout=timeout,
                quiet=True,
            )
            detail = {
                "iterations": result.iterations,
                "stop_reason": result.stop_reason,
            }
            return {
                "status": "ok",
                "answer": result.answer,
                "iterations": result.iterations,
                "converged": result.converged,
                "stop_reason": result.stop_reason,
                "traces": [t.to_dict() for t in result.traces],
            }

        except RateLimitExceeded as e:
            outcome = "rate_limited"
            return {"status": "rate_limited", "retry_after_ms": e.retry_after_ms, "message": str(e)}
        except ValueError as e:
            outcome = "error"
            return {"status": "error", "message": f"Protocol error: {e}"}
        except RuntimeError as e:
            outcome = "error"
            return {"status": "error", "message": f"LLM error: {e}"}
        except Exception as e:
            outcome = "error"
            return {"status": "error", "message": f"Loop failed: {e}"}
        finally:
            audit.log("memory_loop", rid, session_id, _audit_db,
                      outcome, detail, (time.monotonic() - t0) * 1000)

    # =====================================================================
    # ADMIN: reindex  (v0.12)
    # =====================================================================

    @mcp.tool()
    def memory_reindex(
        tokenizer: Optional[str] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Rebuild the FTS5 index, optionally with a new tokenizer.

        Use this to switch between tokenizer presets (fr/en/raw) or rebuild
        a stale index after bulk imports.

        Args:
            tokenizer: Tokenizer preset (fr/en/raw) or full string. None = rebuild in place.
            dry_run: If true, report what would change without executing.

        Returns:
            previous_tokenizer, new_tokenizer, items_indexed, duration_seconds.
        """
        t0 = time.monotonic()
        rid = audit.new_rid()
        session_id = _sid()
        outcome = "ok"
        detail: Dict[str, Any] = {}
        try:
            if rate_limiter:
                rate_limiter.check_write(session_id)

            from memctl.store import FTS_TOKENIZER_PRESETS

            old_tokenizer = store._fts_tokenizer
            new_tokenizer = (
                FTS_TOKENIZER_PRESETS.get(tokenizer, tokenizer)
                if tokenizer else old_tokenizer
            )
            changing = old_tokenizer != new_tokenizer

            if dry_run:
                stats = store.stats()
                detail = {"dry_run": True, "tokenizer_change": changing}
                return {
                    "status": "dry_run",
                    "current_tokenizer": old_tokenizer,
                    "new_tokenizer": new_tokenizer,
                    "tokenizer_change": changing,
                    "items_to_reindex": stats["total_items"],
                }

            count = store.rebuild_fts(
                tokenizer=new_tokenizer if changing else None,
            )
            dt = time.monotonic() - t0

            if count < 0:
                outcome = "error"
                return {"status": "error", "message": "FTS5 not available"}

            # Log reindex event for auditability
            store._log_event("reindex", None, {
                "previous_tokenizer": old_tokenizer,
                "new_tokenizer": new_tokenizer,
                "tokenizer_changed": changing,
                "items_indexed": count,
                "duration_seconds": round(dt, 2),
            }, "")
            store._conn.commit()

            detail = {
                "tokenizer_changed": changing,
                "items_indexed": count,
            }
            return {
                "status": "ok",
                "previous_tokenizer": old_tokenizer,
                "new_tokenizer": new_tokenizer,
                "tokenizer_changed": changing,
                "items_indexed": count,
                "duration_seconds": round(dt, 2),
            }

        except RateLimitExceeded as e:
            outcome = "rate_limited"
            return {"status": "rate_limited", "retry_after_ms": e.retry_after_ms, "message": str(e)}
        except Exception as e:
            outcome = "error"
            return {"status": "error", "message": f"Reindex failed: {e}"}
        finally:
            audit.log("memory_reindex", rid, session_id, _audit_db,
                      outcome, detail, (time.monotonic() - t0) * 1000)

    # =====================================================================
    # LIFECYCLE: promote  (v0.17)
    # =====================================================================

    @mcp.tool()
    def memory_promote(
        id: str,
        tier: str = "ltm",
    ) -> Dict[str, Any]:
        """Promote a memory item to a higher tier (STM→MTM, STM→LTM, MTM→LTM).

        This is human-initiated curation, not automatic promotion.
        The consolidation algorithm remains deterministic; promote is
        a separate, explicit action for curating key knowledge.

        Args:
            id: Memory item ID to promote (e.g. MEM-abc123).
            tier: Target tier — "mtm" or "ltm" (default: "ltm").

        Returns:
            Status dict with from_tier and to_tier.
        """
        t0 = time.monotonic()
        rid = audit.new_rid()
        session_id = _sid()
        outcome = "ok"
        detail: Dict[str, Any] = {}
        try:
            if rate_limiter:
                rate_limiter.check_write(session_id)

            item = store.read_item(id)
            if item is None:
                outcome = "not_found"
                return {"status": "error", "message": f"Item not found: {id}"}

            order = {"stm": 0, "mtm": 1, "ltm": 2}
            current_rank = order.get(item.tier, 0)
            target_rank = order.get(tier, 0)

            if tier not in order:
                outcome = "invalid_tier"
                return {"status": "error", "message": f"Invalid tier: {tier}"}

            if target_rank <= current_rank:
                outcome = "already_at_tier"
                return {
                    "status": "error",
                    "message": f"Item already at {item.tier} (target: {tier})",
                }

            from_tier = item.tier
            store.update_item(item.id, {"tier": tier})
            store._log_event("promote", item.id, {
                "from_tier": from_tier,
                "to_tier": tier,
            }, "")
            store._conn.commit()

            detail = {"id": id, "from_tier": from_tier, "to_tier": tier}
            return {
                "status": "ok",
                "id": id,
                "from_tier": from_tier,
                "to_tier": tier,
            }

        except RateLimitExceeded as e:
            outcome = "rate_limited"
            return {"status": "rate_limited", "retry_after_ms": e.retry_after_ms, "message": str(e)}
        except Exception as e:
            outcome = "error"
            return {"status": "error", "message": f"Promote failed: {e}"}
        finally:
            audit.log("memory_promote", rid, session_id, _audit_db,
                      outcome, detail, (time.monotonic() - t0) * 1000)

    # =====================================================================
    # ADMIN: reset  (v0.13)
    # =====================================================================

    @mcp.tool()
    def memory_reset(
        preserve_mounts: bool = True,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Truncate all memory content. Preserves schema and mount config.

        DESTRUCTIVE — all items, events, links, and sync cache are removed.
        Mount registrations preserved by default (use preserve_mounts=False to clear).
        Use dry_run=True to preview without deleting.

        Args:
            preserve_mounts: Keep mount registrations (default True).
            dry_run: Preview counts without deleting (default False).

        Returns:
            Per-table counts of deleted (or would-delete) records.
        """
        t0 = time.monotonic()
        rid = audit.new_rid()
        session_id = _sid()
        outcome = "ok"
        detail: Dict[str, Any] = {}
        try:
            if rate_limiter:
                rate_limiter.check_write(session_id)

            result = store.reset(
                preserve_mounts=preserve_mounts,
                dry_run=dry_run,
            )

            total = sum(v for k, v in result.items() if k != "dry_run")
            detail = {
                "dry_run": dry_run,
                "preserve_mounts": preserve_mounts,
                "total_records": total,
            }

            return {
                "status": "dry_run" if dry_run else "ok",
                "dry_run": dry_run,
                "preserve_mounts": preserve_mounts,
                "total_records": total,
                **{k: v for k, v in result.items() if k != "dry_run"},
            }

        except RateLimitExceeded as e:
            outcome = "rate_limited"
            return {"status": "rate_limited", "retry_after_ms": e.retry_after_ms, "message": str(e)}
        except Exception as e:
            outcome = "error"
            return {"status": "error", "message": f"Reset failed: {e}"}
        finally:
            audit.log("memory_reset", rid, session_id, _audit_db,
                      outcome, detail, (time.monotonic() - t0) * 1000)

    # -- Log registered tool count -----------------------------------------
    logger.info("Registered 21 memory MCP tools (with L0/L1 middleware)")


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


def _extract_best_retry_query(raw: str, normalized: str) -> Optional[str]:
    """Extract the best retry query from identifiers or longest term.

    Priority: identifiers in raw query, then longest non-stop word.
    Returns None if no useful retry query can be formed.
    """
    from memctl.query import _is_identifier

    words = raw.strip().split()
    # Try identifiers first
    identifiers = [w for w in words if _is_identifier(w)]
    if identifiers:
        return " ".join(identifiers)

    # Fall back to longest word from normalized query
    norm_words = normalized.strip().split()
    if norm_words:
        longest = max(norm_words, key=len)
        if len(longest) >= 3:
            return longest

    return None


def _suggest_next_queries(
    raw: str,
    normalized: str,
    meta: Optional[Any],
) -> List[str]:
    """Generate exactly 2 query suggestions for zero-result guidance.

    Returns:
        List of 2 suggestion strings.
    """
    from memctl.query import _is_identifier

    suggestions: List[str] = []

    # Suggestion 1: identifier-based
    words = raw.strip().split()
    identifiers = [w for w in words if _is_identifier(w)]
    if identifiers:
        suggestions.append(
            f"Try identifier query: '{' '.join(identifiers)}'"
        )
    else:
        suggestions.append(
            "Use identifiers (CamelCase, snake_case) instead of descriptions"
        )

    # Suggestion 2: base form / shortest meaningful terms
    norm_words = normalized.strip().split()
    if len(norm_words) > 1:
        # Keep only the two longest terms
        by_len = sorted(norm_words, key=len, reverse=True)[:2]
        suggestions.append(
            f"Try shorter query: '{' '.join(by_len)}'"
        )
    elif norm_words:
        suggestions.append(
            f"Try single term: '{norm_words[0]}'"
        )
    else:
        suggestions.append(
            "Remove articles and prepositions, keep domain terms"
        )

    return suggestions[:2]
