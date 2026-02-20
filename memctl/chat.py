"""
Interactive memory-backed chat REPL for memctl.

Bounded, deterministic, stateless by default. Each turn:
  1. Recalls from the memory store via FTS5
  2. Prepends session context (if --session)
  3. Sends to an LLM via the loop controller
  4. Displays the answer on stdout
  5. Optionally stores the answer as STM (if --store)

The LLM is never autonomous. The controller enforces bounds,
dedup, and convergence stopping — same as `memctl loop`.

Author: Olivier Vitrac, PhD, HDR | olivier.vitrac@adservio.fr | Adservio
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from memctl.loop import LoopResult


# ---------------------------------------------------------------------------
# Readline history (XDG-compliant, TTY-only)
# ---------------------------------------------------------------------------

_HISTORY_DIR = Path(os.environ.get(
    "XDG_DATA_HOME", os.path.expanduser("~/.local/share")
)) / "memctl"
_HISTORY_FILE = _HISTORY_DIR / "chat_history"
_HISTORY_MAX = 1000

# ---------------------------------------------------------------------------
# Uncertainty markers (passive protocol hint)
# ---------------------------------------------------------------------------

_UNCERTAINTY_MARKERS = (
    "insufficient",
    "cannot find",
    "not enough context",
    "no information",
    "unclear",
    "unable to determine",
    "no relevant",
    "not available",
)

_REFINEMENT_HINT = (
    "[info] Tip: rerun with --protocol json --max-calls 3 "
    "to allow iterative refinement"
)


def _has_uncertainty(answer: str) -> bool:
    """Return True if the answer contains high-uncertainty markers."""
    lower = answer.lower()
    return any(marker in lower for marker in _UNCERTAINTY_MARKERS)


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------


@dataclass
class ChatSession:
    """In-memory sliding window of Q&A pairs."""

    history: list[tuple[str, str]] = field(default_factory=list)
    turn_count: int = 0


def format_session_context(
    session: ChatSession,
    history_turns: int = 5,
    budget_chars: int = 4000,
) -> str:
    """Format recent session history as a context block.

    Dual bound: at most *history_turns* pairs, and at most *budget_chars*
    total characters.  Oldest turns are trimmed first.

    Returns:
        Formatted session block, or empty string if no history.
    """
    if not session.history:
        return ""

    # Take last N turns
    window = session.history[-history_turns:]

    # Build from newest to oldest, trim if over budget
    lines: list[str] = ["## Session History"]
    total_chars = len(lines[0])

    kept: list[tuple[str, str]] = []
    for q, a in reversed(window):
        block = f"\nQ: {q}\nA: {a}"
        if total_chars + len(block) > budget_chars:
            break
        kept.append((q, a))
        total_chars += len(block)

    # Restore chronological order
    kept.reverse()

    if not kept:
        return ""

    for q, a in kept:
        lines.append(f"\nQ: {q}")
        lines.append(f"A: {a}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Single turn (pure function — no I/O, no env vars, no global state)
# ---------------------------------------------------------------------------

# Default callable types for dependency injection
RecallerType = Callable[[str, str, int], list[dict]]
LoopRunnerType = Callable[..., LoopResult]


def _default_recaller(
    db_path: str, query: str, limit: int = 50,
    *, mount_id: Optional[str] = None,
) -> list[dict]:
    """Default recaller: wraps loop.recall_items."""
    from memctl.loop import recall_items
    return recall_items(db_path, query, limit, mount_id=mount_id)


def _default_loop_runner(**kwargs) -> LoopResult:
    """Default loop runner: wraps loop.run_loop."""
    from memctl.loop import run_loop
    return run_loop(**kwargs)


def chat_turn(
    question: str,
    llm_cmd: str,
    *,
    db_path: str,
    session: Optional[ChatSession] = None,
    history_turns: int = 5,
    session_budget: int = 4000,
    budget: int = 2200,
    protocol: str = "passive",
    max_calls: int = 1,
    threshold: float = 0.92,
    query_threshold: float = 0.90,
    stable_steps: int = 2,
    system_prompt: Optional[str] = None,
    llm_mode: str = "stdin",
    timeout: int = 300,
    mount_id: Optional[str] = None,
    recaller: Optional[RecallerType] = None,
    loop_runner: Optional[LoopRunnerType] = None,
) -> str:
    """Execute a single chat turn. Pure function — no I/O.

    Args:
        question: The user's question.
        llm_cmd: Shell command to invoke the LLM.
        db_path: Path to the SQLite database.
        session: Optional ChatSession for turn-to-turn continuity.
        history_turns: Max Q&A pairs from session to include.
        session_budget: Max characters for session context block.
        budget: Token budget for recall context.
        protocol: LLM output protocol (passive/json/regex).
        max_calls: Max loop iterations per turn.
        threshold: Answer fixed-point similarity threshold.
        query_threshold: Query cycle similarity threshold.
        stable_steps: Consecutive stable steps for convergence.
        system_prompt: Optional system prompt.
        llm_mode: How to pass prompt to LLM (stdin/file).
        timeout: LLM subprocess timeout in seconds.
        mount_id: If set, scope recall to this mount's items only.
        recaller: Injectable recall function (for testing).
        loop_runner: Injectable loop function (for testing).

    Returns:
        The LLM's answer string.
    """
    _recaller = recaller or _default_recaller
    _loop_runner = loop_runner or _default_loop_runner

    # 1. Recall from memory store (optionally scoped to mount)
    items = _recaller(db_path, question, 50, mount_id=mount_id)

    # 2. Format recall items as context
    from memctl.loop import merge_context
    budget_chars = budget * 4
    seen_ids: set[str] = set()
    context, _, _ = merge_context("", items, seen_ids, budget_chars)

    # 3. Prepend session context (if any)
    if session is not None:
        session_block = format_session_context(
            session, history_turns, session_budget,
        )
        if session_block:
            context = session_block + "\n\n" + context if context else session_block

    # 4. Run the loop
    result = _loop_runner(
        initial_context=context,
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

    return result.answer


# ---------------------------------------------------------------------------
# Answer storage
# ---------------------------------------------------------------------------


def _store_answer(
    store,
    question: str,
    answer: str,
    tags: list[str],
) -> None:
    """Store a chat answer as an STM memory item via policy.

    Args:
        store: Connected MemoryStore instance.
        question: The user's question (used for title).
        answer: The LLM's answer to store.
        tags: Tags for the stored item.
    """
    from memctl.types import MemoryItem, MemoryProvenance
    from memctl.policy import MemoryPolicy

    # Ensure "chat" tag is present
    all_tags = list(set(tags + ["chat"]))

    item = MemoryItem(
        title=question[:80],
        content=answer,
        tier="stm",
        type="note",
        tags=all_tags,
        provenance=MemoryProvenance(
            source_kind="chat",
            source_id="memctl-chat",
        ),
    )

    # Policy check
    policy = MemoryPolicy()
    from memctl.types import MemoryProposal
    proposal = MemoryProposal(
        title=item.title,
        content=item.content,
        tags=item.tags,
        why_store="Chat answer stored by user request",
        provenance_hint={"source_kind": "chat", "source_id": "memctl-chat"},
    )
    verdict = policy.evaluate_proposal(proposal)

    if verdict.action == "reject":
        return  # silently skip policy-rejected answers

    if verdict.action == "quarantine":
        item.injectable = False

    store.write_item(item, reason="chat-store")


# ---------------------------------------------------------------------------
# REPL
# ---------------------------------------------------------------------------


def chat_repl(
    llm_cmd: str,
    *,
    db_path: str,
    store_answers: bool = False,
    session_enabled: bool = False,
    history_turns: int = 5,
    session_budget: int = 4000,
    budget: int = 2200,
    tags: list[str],
    protocol: str = "passive",
    max_calls: int = 1,
    threshold: float = 0.92,
    system_prompt: Optional[str] = None,
    llm_mode: str = "stdin",
    timeout: int = 300,
    quiet: bool = False,
    sources: Optional[list[str]] = None,
    mount_id: Optional[str] = None,
    readline_history_max: Optional[int] = None,
) -> None:
    """Run the interactive chat REPL.

    Answers go to stdout. Prompt, banner, and hints go to stderr.

    Args:
        llm_cmd: LLM command string.
        db_path: Path to the SQLite database.
        store_answers: Persist each answer as STM.
        session_enabled: Enable in-memory session context.
        history_turns: Max Q&A pairs in session window.
        session_budget: Max characters for session block.
        budget: Token budget for recall context.
        tags: Tags for stored items.
        protocol: LLM output protocol.
        max_calls: Max loop iterations per turn.
        threshold: Answer fixed-point threshold.
        system_prompt: Optional system prompt.
        llm_mode: stdin or file.
        timeout: LLM subprocess timeout.
        quiet: Suppress progress.
        sources: Files to pre-ingest before starting.
        mount_id: If set, scope recall to this mount's items only.
        readline_history_max: Max readline history entries (default: 1000).
    """
    from memctl.store import MemoryStore

    store = MemoryStore(db_path=db_path)

    # Pre-ingest sources
    if sources:
        from memctl.ingest import ingest_file, IngestResult, resolve_sources
        try:
            resolved = resolve_sources(sources)
        except FileNotFoundError as e:
            print(f"[chat] Error: {e}", file=sys.stderr)
            store.close()
            sys.exit(1)

        total = IngestResult()
        for path in resolved:
            r = ingest_file(store, path, format_mode="auto", injectable=True)
            total.files_processed += r.files_processed
            total.chunks_created += r.chunks_created

        if not quiet:
            print(
                f"[chat] Ingested {total.chunks_created} chunks "
                f"from {total.files_processed} file(s)",
                file=sys.stderr,
            )

    # Session state
    session = ChatSession() if session_enabled else None
    interactive = sys.stdin.isatty()

    # Readline history (TTY only)
    history_max = readline_history_max if readline_history_max is not None else _HISTORY_MAX
    if interactive:
        try:
            import readline
            _HISTORY_DIR.mkdir(parents=True, exist_ok=True)
            try:
                readline.read_history_file(str(_HISTORY_FILE))
            except FileNotFoundError:
                pass
            readline.set_history_length(history_max)
        except (ImportError, OSError):
            pass

    # Banner
    if not quiet:
        if interactive:
            print(
                "memctl chat \u2014 blank line to send, Ctrl-D to exit, Ctrl-C to cancel",
                file=sys.stderr,
            )
        else:
            print("memctl chat \u2014 Ctrl-D to exit", file=sys.stderr)
        if session_enabled:
            print("session: in-memory only (not persisted)", file=sys.stderr)

    # REPL loop
    try:
        while True:
            # --- Read question ---
            if interactive:
                # Multi-line input: blank line terminates
                print("> ", end="", file=sys.stderr, flush=True)
                lines: list[str] = []
                try:
                    while True:
                        try:
                            line = input()
                        except EOFError:
                            if lines:
                                break  # treat accumulated lines as question
                            raise  # real EOF, exit REPL
                        if line == "" and lines:
                            break  # blank line terminates multi-line block
                        if line == "" and not lines:
                            continue  # ignore leading blank lines
                        lines.append(line)
                        print("  ", end="", file=sys.stderr, flush=True)
                except KeyboardInterrupt:
                    print("", file=sys.stderr)
                    continue

                question = "\n".join(lines).strip()
            else:
                # Piped mode: one line per question (unchanged)
                try:
                    line = input()
                except KeyboardInterrupt:
                    print("", file=sys.stderr)
                    continue
                except EOFError:
                    break
                question = line.strip()

            if not question:
                continue

            # Execute turn
            try:
                answer = chat_turn(
                    question,
                    llm_cmd,
                    db_path=db_path,
                    session=session,
                    history_turns=history_turns,
                    session_budget=session_budget,
                    budget=budget,
                    protocol=protocol,
                    max_calls=max_calls,
                    threshold=threshold,
                    system_prompt=system_prompt,
                    llm_mode=llm_mode,
                    timeout=timeout,
                    mount_id=mount_id,
                )
            except RuntimeError as e:
                print(f"[chat] LLM error: {e}", file=sys.stderr)
                continue

            # Display answer (stdout only)
            print(answer)
            print()  # blank line for readability

            # Refinement hint (passive mode only)
            if protocol == "passive" and _has_uncertainty(answer) and not quiet:
                print(_REFINEMENT_HINT, file=sys.stderr)

            # Store answer
            if store_answers:
                _store_answer(store, question, answer, tags)

            # Update session
            if session is not None:
                session.history.append((question, answer))
                session.turn_count += 1

    finally:
        # Save readline history (TTY only)
        if interactive:
            try:
                import readline
                readline.write_history_file(str(_HISTORY_FILE))
            except (ImportError, OSError):
                pass
        store.close()
