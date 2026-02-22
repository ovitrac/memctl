You are storing an observation as a persistent memory item.

Argument received: $ARGUMENTS

Behavior:

- **With text argument** (e.g. `/remember The auth service uses JWT with 15-min tokens`):
  Call `memory_propose` with a JSON array containing one item:
  [{"title": "<5-10 word summary>", "content": "<full text>",
    "tags": ["manual", "observation"], "type": "fact"}]
  Display: accepted/rejected status and item ID if stored.

- **No argument**: Ask for the observation text.

Notes:
- Stored in STM by default. Policy engine applies (secrets, injection, PII).
- Use `/recall` to retrieve later. Use `memory_consolidate` to promote.
