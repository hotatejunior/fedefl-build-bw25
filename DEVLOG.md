# Development Log — fedefl-build-bw25

> **Provenance.** This engine was extracted, 2026-07-04, from a larger (private) LCA project — an
> asphalt dynamic-LCA study whose background-inventory needs it originally served. That project holds
> the full chronological history: the openLCA validation study, the per-run parity debugging, and two
> disproven theories (the "USLCI trace-metal vintage" gap and the "electricity baseline Vanadium
> ~3.7×" mystery). This log is the curated engine provenance — the design decisions and the bugs that
> had to be fixed to reach validation. It will be expanded over time.

---

## Project Purpose

A programmatic brightway25 pipeline that loads the open US LCA stack — USLCI background processes,
FEDEFL elementary flows, TRACI 2.2 impact methods — and computes LCIA results correctly, verified
against openLCA on identical inputs. A reproducible, code-first alternative to the GUI-bound openLCA
workflow, with no ecoinvent license required.

## Stack and Architecture

- **brightway25** as the computation engine (`bw2data` 4.7, `bw2calc` 2.5)
- **FEDEFL** (Federal Elementary Flow List) as the canonical biosphere flow vocabulary
- **TRACI 2.2** for impact assessment (10 categories)
- **USLCI** as the background process library, sourced from **LCA Commons** in openLCA JSON-LD
- **US Electricity Baseline** injected from an openLCA *library* (pre-solved matrix) package

Compartmentalized into `setup/` (one-time, per machine) and `general/` (the practitioner-facing
engine). See CLAUDE.md for the script table and run order.

## Key Design Choices

- **Custom JSON-LD parser instead of `bw2io`.** `bw2io`'s `JSONLDImporter` misclassifies USLCI
  exchanges via its `isInput` handling; `setup/03_import_uslci.py` reads JSON-LD directly for full
  control over exchange-direction detection.
- **FEDEFL UUIDs as the universal key.** Every biosphere flow — in the brightway DB, the TRACI
  method, and the USLCI data — is keyed by FEDEFL UUID. Linking is deterministic; no name-matching.
- **openLCA library interoperability.** `setup/olca_library.py` decodes openLCA's pre-aggregated
  library (matrix) packages so their pre-solved background (e.g. the electricity baseline) can be
  injected directly — a format brightway cannot otherwise read.
- **Dedicated activities for causal co-products** (2026-07-15). Causal allocation is per-exchange,
  so a consumer of a causal process's NON-reference co-product cannot be re-based through the
  reference activity by any scalar multiplier. `setup/03` therefore builds a second brightway
  activity per causal co-product (code `<proc_uuid>__co__<flow_uuid>`): production = the
  co-product's own native yield, every exchange scaled by the co-product's own column of the
  openLCA causal factor grid (extracted by `setup/allocation.py::causal_coproducts`). Consumers
  are redirected to it at the link site, no multiplier. A consumed co-product whose grid column is
  empty hard-stops the build (mirrors the unknown-unit policy). Verified on the cellulosic-ethanol
  process: its uniform grid means the co-product activity is the reference activity rescaled by
  exactly λ_co/λ_ref = 1.158249156 on all 20 non-product exchanges, matching the JSON factors at
  full precision; non-uniform grids are pinned by synthetic fixtures in `tests/test_allocation.py`.
  openLCA parity for a *consumed* causal co-product is still pending a Phase 3.1 test case (the
  USLCI recycling/MRF sector has real consumers — recovered HDPE/PET from sorting processes).
- **Per-process downloads, not the full USLCI zip.** LCA Commons bundles each process with its full
  upstream, so a handful of target processes arrive with their complete supply chains without
  importing the entire ~10,000-process database.

## Engine bugs fixed to reach validation

Condensed; the full diagnostic narrative for each lives in the parent project's history.

- **TRACI 2.2 spatial CF inflation.** Spatially-resolved characterization factors were being summed
  across locations; the fix selects the generic (non-located) variant to match openLCA. Same issue,
  same fix, drives the `EUTRO_LOCATION` toggle (defaulted to generic for parity).
- **Missing unit strings → matrix explosion.** Exchanges with empty unit strings fragmented the
  technosphere matrix; unit handling was made consistent.
- **Biosphere cross-property normalization on elementary flows.** A normalization meant for
  technosphere flows was wrongly applied to elementary flows; scoped out.
- **Multi-output allocation.** Multi-output processes need co-product allocation, initially missing,
  then wrongly assumed mass allocation universally — corrected to honor each process's declared
  allocation method (physical / economic / causal), applied **per-exchange** rather than flattened
  to a single mass factor.
- **Relink / flow-collision bugs.** Both the electricity-baseline relink fallback and bundle-internal
  technosphere linking resolved providers by a key that could collide across flows; keyed correctly.
- **Waste-treatment processes rejected as malformed.** `WASTE_FLOW` reference products were being
  dropped; accepted as valid reference products.

Outcome: all 40 category × process cells validate within 5% of openLCA (35/40 within 1%). See
`VALIDATION_REPORT.md`.

**Resolved (2026-07-17): the ~1.027 petroleum residual is a reference-export artifact, not an
engine bug.** The entire electricity over-draw sits on the four crude-oil extraction processes.
Their JSON declares 0.1584 MJ electricity/kg — a value USLCI corrected "due to an error" (per the
exchange's own note) — and brightway charges exactly that, while the openLCA reference calculation
charged the pre-correction 0.1584/1.06: it ran on stale process state (the openLCA database on disk
already stores the corrected value). No code change; closes with an openLCA re-export, petroleum
expected at ~1.000. Evidence chain: `validation/VALIDATION_LOG.md` (2026-07-17 entry).

## Quality-of-life / reproducibility

- **Centralized `config.py`** — shared brightway identifiers in one place, imported everywhere.
- **Repo-root-anchored output paths** — scripts run from any directory; outputs land at the repo
  root via `config.REPO_ROOT`, explicit CLI paths resolve against CWD.
- **`source_data/` + `SOURCE_DATA_DIR`** — external assets resolve from one folder, overridable.
- **Rebuild-only `setup/00`** — ships a prebuilt `uslci_flow_conversions.json`; only re-parses the
  full USLCI zip on explicit `--rebuild`.
- **Auto-fetched electricity baseline** — `setup/03b` downloads the version-pinned baseline library
  from the Federal LCA Commons GitHub and verifies its SHA256 before use, removing a manual
  onboarding step. `--no-fetch` requires a local copy; `--library` points at your own.
