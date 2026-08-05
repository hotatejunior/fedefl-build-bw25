# QC Protocol — fedefl-build-bw25 Pipeline

A repeatable audit process for each script in the pipeline. Apply in full when a script is first audited, and re-apply any relevant steps when a script is significantly modified.

---

## Step 1 — Human Read-Through, Module by Module

Read the script top to bottom in logical chunks (constants, helper functions, main loop, output). For each chunk, write a plain-English description of what it does before asking any questions. This forces genuine comprehension rather than pattern-matching on variable names.

**What to look for:**
- What data comes in and what goes out
- What external dependencies are called (packages, files, databases)
- Where branching or fallback logic exists
- Where results are written and whether writes are reversible

---

## Step 2 — Identify Assumptions

For each chunk, prompt: *"What assumptions is this code making about the data it receives?"*

Common assumption categories:
- Column names and schema stability (external packages may rename without notice)
- Data completeness (no missing UUIDs, no empty DataFrames)
- Value ranges (units are known, CFs are positive, allocation factors are non-zero)
- Uniqueness (one UUID per flow, one reference product per process)
- Ordering (first-seen vs. last-seen behavior when duplicates exist)
- Spatial/geographic semantics (location codes meaning what the comment says they mean)

Document assumptions explicitly. The ones that are load-bearing and unasserted are the audit targets.

---

## Step 3 — List and Systematically Resolve Risk Points

Rank each assumption by impact × likelihood:

| Priority | Criteria |
|----------|----------|
| **High** | Silent wrong result; affects scientific output; no error raised |
| **Medium** | Wrong result detectable post-hoc; or low-probability data error |
| **Low** | Cosmetic, recoverable, or requires unusual input to trigger |

Resolve high and medium items before moving to the next script. For each resolution, prefer the simplest fix that makes the failure loud — a `raise RuntimeError` with a clear message is better than a complex recovery path.

---

## Step 4 — Add Diagnostics at Each High/Medium Risk Point

Diagnostics serve two purposes: catching failures at runtime and giving operators a baseline to compare against on future runs.

**Diagnostic patterns used in this pipeline:**

- **Fail fast with context** — raise immediately with the offending value and a remediation hint rather than silently continuing
- **Column assertions** — check expected column names exist before accessing them; include actual column list in the error
- **Count guards** — check that DataFrames or result sets meet a minimum size before proceeding
- **Version logging** — print package versions and file hashes at runtime; record baseline values after first run
- **Accumulator + post-loop report** — collect warnings during a loop (e.g. unknown units, unmatched UUIDs) and report all at once after, rather than printing per-item or silently dropping
- **Baseline printouts** — print counts and distributions (flow types, unit strings, CF counts per category) that are stable across runs; deviations on future runs indicate upstream data changes

---

## Step 5 — Assess Silent Failure Modes

After adding diagnostics, prompt: *"What failure modes could still sneak past everything we've implemented?"*

Focus on:
- **Passthrough fallbacks** — any `return amount, unit` or `continue` without logging
- **Dict overwrites** — anywhere a key collision silently keeps one value over another
- **Zero or near-zero values** — allocation factors, conversion factors, CF values that produce wrong results without errors
- **External package behavior** — what happens if an upstream package changes its output schema, adds rows, or renames a field
- **Partial success** — cases where a script completes and writes output but that output is incomplete or wrong (e.g. empty methods, processes with zero exchanges)

For each remaining silent failure mode that is not worth fixing now, document it explicitly in CLAUDE.md and DEVLOG.md so it is not forgotten.

---

## Step 6 — Run Scripts and Compare Outputs to Expected

Run the script against live data and check outputs against known baselines.

**Checks to perform:**
- No unexpected warnings or errors in terminal output
- Counts match or exceed prior run (flow counts, process counts, CF counts per category)
- Known-good diagnostic values are stable (flow type split, unit string list, package versions)
- For LCIA scripts: spot-check one or more LCIA scores against the openLCA reference
- For import scripts: verify exchange counts (biosphere matched, technosphere linked) are consistent with prior runs

If any count shifts unexpectedly, treat it as a data change event and re-validate before using results.

---

## Audit Checklist (per script)

- [ ] Read-through complete, each chunk summarized in plain English
- [ ] Assumptions listed and ranked by risk
- [ ] All high-risk items resolved or explicitly deferred with documentation
- [ ] All medium-risk items resolved or explicitly deferred with documentation
- [ ] Diagnostics added at each high/medium risk point
- [ ] Silent failure modes assessed and documented
- [ ] Script run successfully against live data
- [ ] Output counts and baselines recorded
- [ ] CLAUDE.md and DEVLOG.md updated with audit status and any remaining open items
