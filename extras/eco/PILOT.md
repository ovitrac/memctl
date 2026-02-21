# eco Mode — Pilot Guidance

Generic framework for running an eco mode pilot with a development team.

**Note:** Specific pilot plans for named customers or internal teams belong in
private deployment documents, not in the public repository. This document
provides a reusable template.

---

## Recommended Pilot Size

- **Team:** 20-30 developers
- **Duration:** 2-4 weeks
- **Codebase:** One medium-to-large project (500+ files, 50+ MB)

Smaller pilots (5-10 developers) are useful for validation but don't expose
the adoption patterns that matter: query discipline drift, support load,
and the "0 results" frustration curve.

---

## eco Default: OFF, Opt-in

eco mode is **installed but disabled** by default. Activation requires one
explicit step:

```bash
memctl eco on
```

This ensures:
- No behavioral changes without developer consent
- Security/ops teams can review before enabling
- First-time users aren't surprised by context injection

Status check:
```bash
memctl eco status
# or
/eco status
```

---

## Training Outline (30 minutes)

### Module 1: What eco does (10 min)

- eco is **structural retrieval**, not semantic search
- It replaces sequential file browsing with chunk-level precision
- FTS5 full-text search with AND logic — every term must match
- eco context tells Claude **what exists and where**; native tools tell Claude
  **what a file contains right now**
- eco is advisory for exploration, not restrictive for editing

### Module 2: Keyword discipline (10 min)

- **Rule:** 2-3 identifiers, not sentences
- **Best:** class names, function names, constants, annotations
  ```
  IncidentServiceImpl     → finds the service
  PreAuthorize            → finds secured controllers
  dateCreation            → finds the DTO field
  ```
- **Avoid:** natural language ("how does the incident creation system work")
- **Why:** FTS5 AND logic means every word must match in a single chunk
- **Since v0.10.0:** stop words (articles, prepositions) are stripped automatically,
  but fewer terms is still better

### Module 3: Escalation ladder (5 min)

1. `memory_inspect` — understand the folder structure first
2. `memory_recall` — retrieve relevant chunks (2-3 keywords)
3. `memory_loop` — iterative refinement if first recall is insufficient
4. Native `Read` — last resort for editing or line-level precision

**Do not skip levels.** Do not jump to native Read after a single failed recall.

### Module 4: When to bypass (5 min)

- **Known file path?** → Use native Read directly
- **Editing/modifying code?** → Use eco to LOCATE, native to EDIT
- **Understanding architecture?** → Use eco (inspect + recall)
- **NL query, 0 results?** → Try the class/function name instead

### Quick reference card

```
+------------------------------------------+
|           eco Quick Reference            |
+------------------------------------------+
| ENABLE:  memctl eco on                   |
| DISABLE: memctl eco off                  |
| STATUS:  memctl eco status               |
+------------------------------------------+
| GOOD QUERIES:                            |
|   IncidentServiceImpl                    |
|   PreAuthorize security                  |
|   Redis caching eviction                 |
+------------------------------------------+
| BAD QUERIES:                             |
|   "how does the system create incidents" |
|   "what is the authentication mechanism" |
+------------------------------------------+
| LADDER: inspect → recall → loop → Read   |
+------------------------------------------+
```

---

## Metrics to Collect

Track these metrics during the pilot to evaluate eco mode effectiveness:

| Metric | Target | How to Measure |
|--------|--------|---------------|
| Recall success rate | >= 70% | Count queries returning >= 1 result / total queries |
| Fallback frequency | < 30% | Count native Read uses after recall attempt / total attempts |
| Query length distribution | Median < 4 words | Audit log: query length per recall/search |
| Zero-result frustration | < 15% report | Developer survey: "How often do you get 0 results?" |
| Time savings | Subjective positive | Developer survey: "Does eco save you time?" |

### Data sources

- **Audit log:** If `--audit-log` is enabled on the MCP server, every recall and
  search is logged with query text, result count, and response time.
- **Developer survey:** Short (5 questions) anonymous survey at pilot end.
- **Git history:** Compare file-read patterns before/after eco (optional).

---

## Exit Criteria

The pilot succeeds when **all** of these are met:

1. **>= 70% of queries produce usable results**
   Measured via audit log (recall/search with matched >= 1).

2. **< 15% of developers report "0 results" frustration**
   Measured via developer survey.

3. **Developers report time savings**
   Measured via developer survey (majority positive).

4. **No security objections from compliance**
   No policy violations, no data leaks, no unauthorized access.

5. **No intent distortion incidents**
   No reports of eco context causing Claude to ignore user questions
   or edit files incorrectly.

### If the pilot fails

- **High 0-result rate:** Training may be insufficient. Check query lengths
  in audit log. If median > 4 words, re-emphasize keyword discipline.
- **Security objections:** Review audit logs for sensitive content in recall.
  Tighten policy patterns if needed.
- **Intent distortion:** Reduce injection budget. Check if budget calibration
  is working (`suggest_budget()` in `memctl/query.py`).
- **Low adoption:** eco may be too invisible. Add onboarding prompts or
  a "first-time use" tutorial.

---

## Rollout After Pilot

If the pilot succeeds:

1. **Enable eco by default for the team** — update shared `.claude/` config.
2. **Document team-specific query patterns** — add domain-specific examples to ECO.md.
3. **Set up ongoing metrics** — audit log analysis on a weekly cadence.
4. **Gradual expansion** — add teams 20-30 at a time, not all at once.

If the pilot fails:

1. Collect feedback, identify the top 3 pain points.
2. Address them (training, budget tuning, pattern additions).
3. Re-run with the same team for 2 more weeks.
4. If it fails twice, eco is not the right fit for this codebase or team.

---

> Specific deployment plans, team names, project identifiers, and internal metrics
> belong in private documents. This template is for the public repository.
