"""
memctl MCP Tools — 14 memory tools for MCP integration.

Thin wrappers around MemoryStore, MemoryPolicy, and module-level functions.
Each tool parses arguments, applies policy, and formats the response.
Zero business logic in this layer beyond wiring.

Tool hierarchy:
    PRIMARY:    memory_recall    — token-budgeted injection (canonical contract)
    SECONDARY:  memory_search    — interactive discovery
    WRITE:      memory_propose   — governed write, memory_write — privileged
    CRUD:       memory_read      — read by IDs
    LIFECYCLE:  memory_consolidate, memory_stats
    FOLDER:     memory_mount, memory_sync, memory_inspect, memory_ask
    DATA:       memory_export, memory_import
    LOOP:       memory_loop

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

from __future__ import annotations

import io
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
    Register all 14 memory MCP tools on a FastMCP server instance.

    Args:
        mcp: FastMCP server instance.
        store: Fully initialized MemoryStore.
        policy: MemoryPolicy for write governance.
        config: MemoryConfig for consolidation thresholds.
    """

    db_path = config.store.db_path

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
        id_list = [i.strip() for i in ids.split(",") if i.strip()]
        items = store.read_items(id_list)
        return {
            "status": "ok",
            "items": [it.to_dict() for it in items],
            "found": len(items),
            "requested": len(id_list),
        }

    # =====================================================================
    # LIFECYCLE
    # =====================================================================

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
        from memctl.mount import register_mount, list_mounts, remove_mount

        try:
            if action == "register":
                if not path:
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
                return {"status": "ok", "mount_id": mid, "path": path}

            elif action == "list":
                mounts = list_mounts(db_path)
                return {"status": "ok", "mounts": mounts, "count": len(mounts)}

            elif action == "remove":
                if not mount_id:
                    return {"status": "error", "message": "mount_id is required for remove"}
                ok = remove_mount(db_path, mount_id)
                if ok:
                    return {"status": "ok", "removed": mount_id}
                else:
                    return {"status": "error", "message": f"Mount not found: {mount_id}"}

            else:
                return {"status": "error", "message": f"Unknown action: {action}"}

        except (FileNotFoundError, NotADirectoryError) as e:
            return {"status": "error", "message": str(e)}
        except Exception as e:
            return {"status": "error", "message": f"Mount failed: {e}"}

    @mcp.tool()
    def memory_sync(
        path: Optional[str] = None,
        full: bool = False,
    ) -> Dict[str, Any]:
        """Sync mounted folders into the memory store.

        Scans files, detects changes (delta mode), and ingests new/modified
        content. Without a path, syncs all registered mounts.

        Args:
            path: Folder path to sync (auto-registers if not mounted).
                  None = sync all registered mounts.
            full: If True, re-process all files ignoring delta cache.

        Returns:
            Sync statistics (files scanned, new, changed, chunks created).
        """
        from memctl.sync import sync_mount, sync_all

        try:
            if path:
                result = sync_mount(
                    db_path, path,
                    delta=not full,
                    quiet=True,
                )
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
                for p, r in results.items():
                    synced[p] = r.to_dict()
                return {
                    "status": "ok",
                    "synced": synced,
                    "mount_count": len(results),
                }
        except (FileNotFoundError, NotADirectoryError) as e:
            return {"status": "error", "message": str(e)}
        except Exception as e:
            return {"status": "error", "message": f"Sync failed: {e}"}

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
        from memctl.inspect import inspect_path, inspect_mount, inspect_stats

        try:
            if path:
                result = inspect_path(
                    db_path, path,
                    sync_mode=sync_mode,
                    budget=budget,
                )
                if output_format == "json":
                    return {
                        "status": "ok",
                        **result.to_dict(),
                    }
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
                    text = inspect_mount(
                        db_path,
                        mount_id=mount_id,
                        budget=budget,
                    )
                    return {"status": "ok", "inject_text": text}

            else:
                # Whole store
                if output_format == "json":
                    stats = inspect_stats(db_path)
                    return {"status": "ok", **stats}
                else:
                    text = inspect_mount(db_path, budget=budget)
                    return {"status": "ok", "inject_text": text}

        except (FileNotFoundError, NotADirectoryError, ValueError) as e:
            return {"status": "error", "message": str(e)}
        except Exception as e:
            return {"status": "error", "message": f"Inspect failed: {e}"}

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
        from memctl.ask import ask_folder

        try:
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
        except (FileNotFoundError, NotADirectoryError, ValueError) as e:
            return {"status": "error", "message": str(e)}
        except RuntimeError as e:
            return {"status": "error", "message": f"LLM error: {e}"}
        except Exception as e:
            return {"status": "error", "message": f"Ask failed: {e}"}

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
        from memctl.export_import import export_items

        buf = io.StringIO()
        count = export_items(
            db_path,
            tier=tier,
            type_filter=type_filter,
            scope=scope,
            exclude_archived=not include_archived,
            output=buf,
            log=lambda msg: None,  # suppress stderr in MCP
        )

        # Parse JSONL back to list of dicts
        buf.seek(0)
        items = []
        for line in buf:
            line = line.strip()
            if line:
                items.append(json.loads(line))

        truncated = len(items) > 1000
        if truncated:
            items = items[:1000]

        return {
            "status": "ok",
            "count": len(items),
            "items": items,
            "truncated": truncated,
        }

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
        from memctl.export_import import import_items

        # Convert JSON array to JSONL for import_items()
        try:
            item_list = json.loads(items)
        except (json.JSONDecodeError, TypeError) as e:
            return {"status": "error", "message": f"Invalid JSON: {e}"}

        if not isinstance(item_list, list):
            item_list = [item_list]

        # Build JSONL stream
        jsonl_buf = io.StringIO()
        for item_d in item_list:
            jsonl_buf.write(json.dumps(item_d, ensure_ascii=False) + "\n")
        jsonl_buf.seek(0)

        try:
            result = import_items(
                db_path,
                jsonl_buf,
                preserve_ids=preserve_ids,
                dry_run=dry_run,
                log=lambda msg: None,  # suppress stderr in MCP
            )
            return {
                "status": "ok",
                "total_lines": result.total_lines,
                "imported": result.imported,
                "skipped_dedup": result.skipped_dedup,
                "skipped_policy": result.skipped_policy,
                "errors": result.errors,
            }
        except Exception as e:
            return {"status": "error", "message": f"Import failed: {e}"}

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
        from memctl.loop import run_loop

        try:
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
            return {
                "status": "ok",
                "answer": result.answer,
                "iterations": result.iterations,
                "converged": result.converged,
                "stop_reason": result.stop_reason,
                "traces": [t.to_dict() for t in result.traces],
            }
        except ValueError as e:
            return {"status": "error", "message": f"Protocol error: {e}"}
        except RuntimeError as e:
            return {"status": "error", "message": f"LLM error: {e}"}
        except Exception as e:
            return {"status": "error", "message": f"Loop failed: {e}"}

    # -- Log registered tool count -----------------------------------------
    logger.info("Registered 14 memory MCP tools")


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
