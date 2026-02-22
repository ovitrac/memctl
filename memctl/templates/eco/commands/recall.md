You are searching memctl memory for relevant content.

Argument received: $ARGUMENTS

Behavior:

- **With query terms** (e.g. `/recall authentication flow`):
  Call `memory_recall` with `query` set to the argument text.
  Display: matched items, tokens used, FTS strategy, hints if any.
  If zero results, suggest: try identifiers, run /scan, consider /reindex en.

- **No argument**: Ask for 2-3 keywords.

Notes:
- Identifiers (class/function names) work best.
- FTS cascade runs automatically (AND → REDUCED_AND → PREFIX_AND → OR_FALLBACK).
