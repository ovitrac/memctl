"""
One-shot folder Q&A for memctl.

Orchestrates mount + sync + inspect + scoped recall + loop to answer
a single question about a folder's contents. Deterministic, bounded,
no REPL — print answer to stdout, progress to stderr.

Public API:
    ask_folder(path, question, llm_cmd, ...) -> AskResult

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Callable, Optional


# ---------------------------------------------------------------------------
# Default log callable
# ---------------------------------------------------------------------------


def _default_log(msg: str) -> None:
    """Print informational message to stderr."""
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class AskResult:
    """Result of an ask_folder() call."""

    answer: str
    mount_id: str
    was_mounted: bool
    was_synced: bool
    recall_items_used: int
    loop_iterations: int
    converged: bool
    stop_reason: str

    def to_dict(self) -> dict:
        """Serialize to a plain dict for JSON output."""
        return {
            "answer": self.answer,
            "mount_id": self.mount_id,
            "was_mounted": self.was_mounted,
            "was_synced": self.was_synced,
            "recall_items_used": self.recall_items_used,
            "loop_iterations": self.loop_iterations,
            "converged": self.converged,
            "stop_reason": self.stop_reason,
        }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def ask_folder(
    path: str,
    question: str,
    llm_cmd: str,
    *,
    db_path: str,
    sync_mode: str = "auto",
    mount_mode: str = "persist",
    budget: int = 2200,
    inspect_cap: int = 600,
    protocol: str = "passive",
    max_calls: int = 1,
    threshold: float = 0.92,
    query_threshold: float = 0.90,
    stable_steps: int = 2,
    system_prompt: Optional[str] = None,
    llm_mode: str = "stdin",
    timeout: int = 300,
    ignore_patterns: Optional[list[str]] = None,
    log: Callable[[str], None] = _default_log,
) -> AskResult:
    """Answer a question about a folder.

    Orchestrates: inspect_path (automount + sync) → structural context →
    scoped recall → loop → answer.

    Args:
        path:             Filesystem directory to ask about.
        question:         The user's question.
        llm_cmd:          Shell command to invoke the LLM.
        db_path:          Path to the SQLite database.
        sync_mode:        "auto" (sync if stale), "always", or "never".
        mount_mode:       "persist" (keep mount) or "ephemeral" (remove after).
        budget:           Total token budget (inspect + recall).
        inspect_cap:      Tokens reserved for structural context (default 600).
        protocol:         LLM output protocol (passive/json/regex).
        max_calls:        Max loop iterations.
        threshold:        Answer fixed-point similarity threshold.
        query_threshold:  Query cycle similarity threshold.
        stable_steps:     Consecutive stable steps for convergence.
        system_prompt:    Optional system prompt.
        llm_mode:         How to pass prompt to LLM (stdin/file).
        timeout:          LLM subprocess timeout in seconds.
        ignore_patterns:  Glob patterns to exclude.
        log:              Callable for informational messages.

    Returns:
        AskResult with answer and orchestration metadata.

    Raises:
        ValueError: If inspect_cap >= budget.
    """
    from memctl.inspect import inspect_path, inspect_mount
    from memctl.loop import recall_items, merge_context, run_loop

    # Validate budget split
    if inspect_cap >= budget:
        raise ValueError(
            f"inspect_cap ({inspect_cap}) must be less than budget ({budget})"
        )

    # -- Step 1: Auto-mount + auto-sync + stats via inspect_path --
    # Always use persist internally; handle ephemeral cleanup ourselves
    # so mount_id is available for scoped recall.
    ir = inspect_path(
        db_path, path,
        sync_mode=sync_mode,
        mount_mode="persist",
        budget=budget,
        ignore_patterns=ignore_patterns,
        log=log,
    )

    mount_id = ir.mount_id

    # -- Step 2: Structural context (inspect block) --
    inspect_cap_chars = inspect_cap * 4
    inspect_block = inspect_mount(
        db_path,
        mount_id=mount_id,
        mount_label=ir.mount_label,
        budget=inspect_cap,
    )
    # Truncate if over cap (inspect_mount may exceed slightly)
    if len(inspect_block) > inspect_cap_chars:
        inspect_block = inspect_block[:inspect_cap_chars]

    # -- Step 3: Scoped recall --
    recall_budget = budget - inspect_cap
    recall_budget_chars = recall_budget * 4

    items = recall_items(db_path, question, mount_id=mount_id)
    seen_ids: set[str] = set()
    recall_block, used_items, recall_count = merge_context(
        "", items, seen_ids, recall_budget_chars,
    )

    log(f"[ask] Context: {len(inspect_block)} chars inspect + "
        f"{len(recall_block)} chars recall ({recall_count} items)")

    # -- Step 4: Combine context --
    if inspect_block and recall_block:
        combined = inspect_block + "\n\n" + recall_block
    elif inspect_block:
        combined = inspect_block
    else:
        combined = recall_block

    # -- Step 5: Run loop --
    result = run_loop(
        initial_context=combined,
        query=question,
        llm_cmd=llm_cmd,
        db_path=db_path,
        max_calls=max_calls,
        threshold=threshold,
        query_threshold=query_threshold,
        stable_steps=stable_steps,
        protocol=protocol,
        llm_mode=llm_mode,
        system_prompt=system_prompt,
        budget=budget,
        timeout=timeout,
        quiet=True,
    )

    log(f"[ask] {result.iterations} iteration(s), "
        f"stop={result.stop_reason}, converged={result.converged}")

    # -- Step 6: Ephemeral cleanup --
    if mount_mode == "ephemeral":
        from memctl.mount import remove_mount
        remove_mount(db_path, mount_id)
        log("[ask] Ephemeral: mount removed")

    return AskResult(
        answer=result.answer,
        mount_id=mount_id,
        was_mounted=ir.was_mounted,
        was_synced=ir.was_synced,
        recall_items_used=recall_count,
        loop_iterations=result.iterations,
        converged=result.converged,
        stop_reason=result.stop_reason,
    )
