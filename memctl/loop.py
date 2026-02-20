"""
Bounded recall-answer loop controller for memctl.

Implements a deterministic, auditable loop that:
  1. Sends context + question to an LLM via subprocess
  2. Parses the LLM response for refinement directives
  3. Performs additional recalls from the memory store
  4. Detects convergence (fixed-point) and query cycles
  5. Emits structured JSONL trace to stderr or file

The LLM is never autonomous — it only proposes queries. The controller
enforces bounds, deduplication, and convergence stopping.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import IO, Optional

from memctl.similarity import is_fixed_point, is_query_cycle

# ---------------------------------------------------------------------------
# Protocol system prompt (prepended to every LLM call)
# ---------------------------------------------------------------------------

PROTOCOL_SYSTEM_PROMPT = """\
You are answering a question using retrieved context. Follow this protocol exactly:

1. Your FIRST line of output MUST be a JSON object with these fields:
   {"need_more": <bool>, "query": "<string or null>", "rationale": "<string or null>", "stop": <bool>}

2. After the JSON line, leave ONE blank line, then write your answer.

3. If the provided context is SUFFICIENT to answer fully:
   {"need_more": false, "query": null, "rationale": null, "stop": true}

4. If the provided context is INSUFFICIENT and you need more information:
   {"need_more": true, "query": "specific refined search query", "rationale": "what is missing", "stop": false}

5. Do NOT emit anything before the JSON line. Do NOT wrap it in markdown."""


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class LoopDirective:
    """Parsed LLM refinement directive."""

    need_more: bool = False
    query: Optional[str] = None
    rationale: Optional[str] = None
    stop: bool = False


@dataclass
class LoopTrace:
    """Single iteration trace entry (serializable to JSONL)."""

    iter: int
    query: Optional[str]
    new_items: int
    sim: Optional[float]
    action: str  # continue, fixed_point, query_cycle, no_new_items, max_calls, llm_stop

    def to_dict(self) -> dict:
        return {
            "iter": self.iter,
            "query": self.query,
            "new_items": self.new_items,
            "sim": self.sim,
            "action": self.action,
        }


@dataclass
class LoopResult:
    """Final result of the recall-answer loop."""

    answer: str
    iterations: int
    converged: bool
    traces: list[LoopTrace] = field(default_factory=list)
    stop_reason: str = ""  # fixed_point, query_cycle, no_new_items, max_calls, llm_stop


# ---------------------------------------------------------------------------
# Protocol parsers
# ---------------------------------------------------------------------------

# Regex fallback patterns
_NEED_MORE_RE = re.compile(r"NEED_MORE\s*:\s*(.+)", re.IGNORECASE)
_QUERY_RE = re.compile(r"QUERY\s*:\s*(.+)", re.IGNORECASE)


def parse_json_directive(output: str, *, strict: bool = False) -> tuple[LoopDirective, str]:
    """Parse JSON protocol: first line is JSON, rest is the answer.

    Args:
        output: Raw LLM output.
        strict: If True, raise ValueError on invalid JSON.

    Returns:
        (directive, answer) tuple.
    """
    lines = output.split("\n", 1)
    first_line = lines[0].strip()
    rest = lines[1].lstrip("\n") if len(lines) > 1 else ""

    try:
        obj = json.loads(first_line)
        directive = LoopDirective(
            need_more=bool(obj.get("need_more", False)),
            query=obj.get("query"),
            rationale=obj.get("rationale"),
            stop=bool(obj.get("stop", False)),
        )
        # Empty query with need_more=true → treat as stop
        if directive.need_more and not (directive.query and directive.query.strip()):
            directive.need_more = False
            directive.stop = True
        return directive, rest
    except (json.JSONDecodeError, TypeError, AttributeError):
        if strict:
            raise ValueError(f"Invalid JSON protocol line: {first_line!r}")
        # Fallback: treat entire output as the answer, no refinement
        return LoopDirective(need_more=False, stop=True), output


def parse_regex_directive(output: str) -> tuple[LoopDirective, str]:
    """Parse regex protocol: scan for NEED_MORE: / QUERY: patterns.

    Returns:
        (directive, answer) tuple. Answer is the full output
        (patterns are metadata, not removed).
    """
    need_more_match = _NEED_MORE_RE.search(output)
    query_match = _QUERY_RE.search(output)

    if need_more_match or query_match:
        query = query_match.group(1).strip() if query_match else None
        rationale = need_more_match.group(1).strip() if need_more_match else None
        need_more = bool(query)
        return LoopDirective(
            need_more=need_more,
            query=query,
            rationale=rationale,
            stop=not need_more,
        ), output

    return LoopDirective(need_more=False, stop=True), output


def parse_passive_directive(output: str) -> tuple[LoopDirective, str]:
    """Passive protocol: no refinement, answer is the full output."""
    return LoopDirective(need_more=False, stop=True), output


def parse_directive(
    output: str,
    protocol: str = "json",
    *,
    strict: bool = False,
) -> tuple[LoopDirective, str]:
    """Dispatch to the appropriate protocol parser.

    Args:
        output: Raw LLM output.
        protocol: "json", "regex", or "passive".
        strict: For json protocol, raise on invalid JSON.

    Returns:
        (directive, answer) tuple.
    """
    if protocol == "json":
        return parse_json_directive(output, strict=strict)
    elif protocol == "regex":
        return parse_regex_directive(output)
    elif protocol == "passive":
        return parse_passive_directive(output)
    else:
        raise ValueError(f"Unknown protocol: {protocol!r}")


# ---------------------------------------------------------------------------
# LLM invocation
# ---------------------------------------------------------------------------


def invoke_llm(
    cmd: str,
    prompt: str,
    *,
    mode: str = "stdin",
    timeout: int = 300,
) -> str:
    """Invoke an LLM command as a subprocess.

    Args:
        cmd: Shell command string (e.g. "claude -p", "ollama run mistral").
        prompt: The full prompt text to send.
        mode: "stdin" (pipe prompt to stdin) or "file" (write temp file, append path).
        timeout: Subprocess timeout in seconds (default: 5 minutes).

    Returns:
        LLM output (stdout).

    Raises:
        RuntimeError: If the LLM command fails or times out.
    """
    args = shlex.split(cmd)

    if mode == "file":
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, prefix="memctl_prompt_"
        ) as f:
            f.write(prompt)
            f.flush()
            args.append(f.name)
        stdin_data = None
    else:
        stdin_data = prompt

    try:
        result = subprocess.run(
            args,
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"LLM command timed out after {timeout}s: {cmd}")
    except FileNotFoundError:
        raise RuntimeError(f"LLM command not found: {args[0]!r}")

    if result.returncode != 0:
        stderr_preview = (result.stderr or "").strip()[:200]
        raise RuntimeError(
            f"LLM command failed (exit {result.returncode}): {stderr_preview}"
        )

    return result.stdout


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------


def build_prompt(
    context: str,
    query: str,
    *,
    system_prompt: Optional[str] = None,
    protocol: str = "json",
) -> str:
    """Build the full prompt for an LLM call.

    Assembles: protocol system prompt + user system prompt + context + query.

    Args:
        context: Memory injection block or accumulated context.
        query: The user's question or refined query.
        system_prompt: Optional user-provided system prompt (appended).
        protocol: Protocol mode — protocol instructions only added for "json".

    Returns:
        Complete prompt string.
    """
    parts: list[str] = []

    # Protocol instructions (only for json mode)
    if protocol == "json":
        parts.append(PROTOCOL_SYSTEM_PROMPT)
        parts.append("")

    # User system prompt (appended, never replaces protocol)
    if system_prompt:
        parts.append(system_prompt)
        parts.append("")

    # Context
    if context.strip():
        parts.append("## Context")
        parts.append(context.strip())
        parts.append("")

    # Query
    parts.append("## Question")
    parts.append(query)

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Context management
# ---------------------------------------------------------------------------


def merge_context(
    existing_context: str,
    new_items: list[dict],
    seen_ids: set[str],
    budget_chars: int,
) -> tuple[str, list[dict], int]:
    """Merge new recall items into existing context with dedup and budget.

    Args:
        existing_context: Current accumulated context string.
        new_items: New items from recall (dicts with at least "id" and "content").
        seen_ids: Set of item IDs already included (mutated in-place).
        budget_chars: Maximum total context size in characters.

    Returns:
        (merged_context, truly_new_items, new_count) tuple.
    """
    # Filter already-seen items
    truly_new = [it for it in new_items if it.get("id") not in seen_ids]

    if not truly_new:
        return existing_context, [], 0

    # Format new items as text blocks
    new_blocks: list[str] = []
    for it in truly_new:
        title = it.get("title", "(untitled)")
        content = it.get("content", "")
        item_id = it.get("id", "")
        block = f"[{title}]\n{content}"
        new_blocks.append(block)
        seen_ids.add(item_id)

    new_text = "\n\n".join(new_blocks)

    # Merge: existing + new
    if existing_context.strip():
        merged = existing_context.rstrip() + "\n\n" + new_text
    else:
        merged = new_text

    # Trim to budget (keep from the start — earlier context is higher priority)
    if len(merged) > budget_chars:
        merged = merged[:budget_chars]
        # Don't cut mid-word: find last space
        last_space = merged.rfind(" ")
        if last_space > budget_chars * 0.8:
            merged = merged[:last_space]

    return merged, truly_new, len(truly_new)


# ---------------------------------------------------------------------------
# Recall helper
# ---------------------------------------------------------------------------


def recall_items(
    db_path: str,
    query: str,
    limit: int = 50,
    *,
    mount_id: Optional[str] = None,
) -> list[dict]:
    """Perform FTS5 recall from the memory store.

    Args:
        db_path: Path to the SQLite database.
        query: Search query string.
        limit: Max results.
        mount_id: If set, restrict recall to items belonging to this mount
            (via corpus_hashes item_ids). None = all items.

    Returns:
        List of item dicts with id, title, content, tier, tags, confidence.
    """
    import json as _json
    from memctl.store import MemoryStore

    store = MemoryStore(db_path=db_path)
    try:
        # Build allowed item ID set for scoped recall
        allowed_ids: Optional[set[str]] = None
        if mount_id is not None:
            corpus_files = store.list_corpus_files(mount_id=mount_id)
            allowed_ids = set()
            for cf in corpus_files:
                ids = cf.get("item_ids", [])
                if isinstance(ids, str):
                    ids = _json.loads(ids)
                allowed_ids.update(ids)

        items = store.search_fulltext(query, limit=limit)
        # Filter non-injectable items (and scope to mount if requested)
        items = [
            it for it in items
            if it.injectable and (allowed_ids is None or it.id in allowed_ids)
        ]
        return [
            {
                "id": it.id,
                "title": it.title,
                "content": it.content,
                "tier": it.tier,
                "tags": it.tags,
                "confidence": it.confidence,
            }
            for it in items
        ]
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Trace emission
# ---------------------------------------------------------------------------


def emit_trace(
    trace: LoopTrace,
    trace_file: Optional[IO] = None,
    quiet: bool = False,
) -> None:
    """Emit a trace entry as JSONL.

    Args:
        trace: The trace entry to emit.
        trace_file: File to write to (if None, writes to stderr).
        quiet: If True, suppress stderr output (trace_file still written).
    """
    line = json.dumps(trace.to_dict(), ensure_ascii=False)

    if trace_file is not None:
        trace_file.write(line + "\n")
        trace_file.flush()

    if not quiet and trace_file is None:
        print(line, file=sys.stderr)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def run_loop(
    initial_context: str,
    query: str,
    llm_cmd: str,
    *,
    db_path: str = ".memory/memory.db",
    max_calls: int = 3,
    threshold: float = 0.92,
    query_threshold: float = 0.90,
    stable_steps: int = 2,
    stop_on_no_new: bool = True,
    protocol: str = "json",
    llm_mode: str = "stdin",
    system_prompt: Optional[str] = None,
    budget: int = 2200,
    strict: bool = False,
    trace: bool = False,
    trace_file: Optional[IO] = None,
    quiet: bool = False,
    timeout: int = 300,
) -> LoopResult:
    """Run the bounded recall-answer loop.

    Args:
        initial_context: Initial injection block (from stdin / memctl push).
        query: The user's original question.
        llm_cmd: Shell command to invoke the LLM.
        db_path: Path to the SQLite database for recall.
        max_calls: Maximum LLM invocations (hard cap).
        threshold: Answer fixed-point similarity threshold.
        query_threshold: Query cycle similarity threshold.
        stable_steps: Consecutive stable steps required for convergence.
        stop_on_no_new: Stop if recall returns no new items.
        protocol: "json", "regex", or "passive".
        llm_mode: "stdin" or "file".
        system_prompt: Optional user system prompt (appended to protocol).
        budget: Token budget (converted to chars via *4).
        strict: Raise on invalid JSON protocol (json mode only).
        trace: Enable trace emission.
        trace_file: File for trace output (None = stderr).
        quiet: Suppress stderr output.
        timeout: LLM subprocess timeout in seconds.

    Returns:
        LoopResult with final answer, iteration count, and convergence status.

    Raises:
        RuntimeError: If LLM invocation fails.
        ValueError: If strict=True and JSON protocol is violated.
    """
    budget_chars = budget * 4
    context = initial_context
    seen_ids: set[str] = set()
    query_history: list[str] = [query]
    answers: list[str] = []
    traces: list[LoopTrace] = []
    consecutive_stable = 0
    current_query = query

    for iteration in range(1, max_calls + 1):
        # Build and send prompt
        prompt = build_prompt(
            context,
            current_query,
            system_prompt=system_prompt,
            protocol=protocol,
        )
        llm_output = invoke_llm(llm_cmd, prompt, mode=llm_mode, timeout=timeout)

        # Parse response
        directive, answer = parse_directive(llm_output, protocol, strict=strict)
        answers.append(answer)

        # Fixed-point test (from iteration 2 onward)
        sim: Optional[float] = None
        if len(answers) >= 2:
            from memctl.similarity import similarity as compute_sim

            sim = compute_sim(answers[-1], answers[-2])
            if is_fixed_point(answers[-1], answers[-2], threshold=threshold):
                consecutive_stable += 1
            else:
                consecutive_stable = 0

        # Determine action
        action = "continue"

        # Check: LLM explicitly stopped
        if directive.stop or not directive.need_more:
            action = "llm_stop"

        # Check: fixed point reached
        elif consecutive_stable >= stable_steps:
            action = "fixed_point"

        # Check: query cycle
        elif directive.query and is_query_cycle(
            directive.query, query_history, threshold=query_threshold
        ):
            action = "query_cycle"

        # Check: max calls on next iteration (this is the last one)
        elif iteration == max_calls:
            action = "max_calls"

        # Emit trace
        new_count = 0
        if action == "continue" and directive.query:
            # Perform recall for next iteration
            raw_items = recall_items(db_path, directive.query)
            context, truly_new, new_count = merge_context(
                context, raw_items, seen_ids, budget_chars
            )

            # Check: no new items
            if new_count == 0 and stop_on_no_new:
                action = "no_new_items"

            # Record query
            query_history.append(directive.query)
            current_query = directive.query

        trace_entry = LoopTrace(
            iter=iteration,
            query=directive.query if directive.need_more else None,
            new_items=new_count,
            sim=round(sim, 4) if sim is not None else None,
            action=action,
        )
        traces.append(trace_entry)

        if trace:
            emit_trace(trace_entry, trace_file=trace_file, quiet=quiet)

        # Stop conditions
        if action != "continue":
            converged = action in ("fixed_point", "llm_stop")
            return LoopResult(
                answer=answer,
                iterations=iteration,
                converged=converged,
                traces=traces,
                stop_reason=action,
            )

    # Should not reach here (max_calls handled above), but defensive
    return LoopResult(
        answer=answers[-1] if answers else "",
        iterations=max_calls,
        converged=False,
        traces=traces,
        stop_reason="max_calls",
    )


# ---------------------------------------------------------------------------
# Replay mode
# ---------------------------------------------------------------------------


def replay_trace(trace_path: str) -> list[LoopTrace]:
    """Replay a JSONL trace file, returning parsed trace entries.

    Args:
        trace_path: Path to a JSONL trace file.

    Returns:
        List of LoopTrace entries.

    Raises:
        FileNotFoundError: If trace file does not exist.
    """
    traces: list[LoopTrace] = []
    with open(trace_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            traces.append(LoopTrace(
                iter=obj["iter"],
                query=obj.get("query"),
                new_items=obj.get("new_items", 0),
                sim=obj.get("sim"),
                action=obj.get("action", "unknown"),
            ))
    return traces
