# CLAUDE.md — fedefl-build-bw25 (open USLCI · FEDEFL · TRACI brightway pipeline)

Extracted from a larger (private) LCA project, which retains the full exploratory history. This repo
is the streamlined general engine — and, as of the 2026-07-04 port, it now carries its own validation
harness, locked result CSVs, and running provenance log in `validation/` (no longer parent-only).

## Environment

- **Conda env:** `fedefl-build-bw25` — defined in `environment.yml`.
- **Brightway project name:** `fedefl-build-bw25` (renamed from `asphalt-lca` on 2026-07-07; the full
  setup chain was rebuilt and the harness re-run under the new name, reproducing the locked results
  byte-for-byte — see the replication record in `validation/README.md`. Older records, notably
  `validation/VALIDATION_LOG.md`, still say `asphalt-lca` as verbatim history; do not rewrite them).
- **Platform:** macOS (Darwin)
- **`SOURCE_DATA_DIR`** (optional env var): overrides where `setup/03_import_uslci.py` and
  `setup/03b_import_electricity_baseline.py` look for the external USLCI bundle zips / openLCA
  library. Defaults to `source_data/` at the repo root (`config.REPO_ROOT / "source_data"`) if unset.

## Script Pipeline

Three directories: `setup/` (shared, one-time per machine), `general/` (practitioner-facing —
foreground CSV or USLCI UUID → LCIA results, no openLCA dependency), and `validation/` (the openLCA
parity harness + locked result CSVs + `VALIDATION_LOG.md`; run to reproduce the validation claim).

Run once per machine, in order: `setup/00` → `setup/01` → `setup/02` → `setup/03b` → `setup/03`.
Then run `general/04` per study, or `validation/05` to reproduce the parity check. `setup/03b` must run before `setup/03` — it injects the electricity
baseline background that `03`'s relink fallback resolves against. `setup/03b` auto-fetches the
version-pinned baseline library from the Federal LCA Commons GitHub and verifies its SHA256
(`--no-fetch` / `--library` to override).

Scripts can be invoked from any directory: default output locations (`lca_results.csv`,
`lca_contributions.csv`, `charts/`) anchor to the repo root via `config.REPO_ROOT`. Paths passed
explicitly on the CLI resolve against the CWD, as is standard.

| Script | Role |
|--------|------|
| `config.py` | Shared identifiers (`PROJECT_NAME`, `BIOSPHERE_DB`, `USLCI_DB`, `ELECTRICITY_BASELINE_DB`, `METHOD_ROOT`) and `REPO_ROOT` — single source of truth, imported by every script below via a `sys.path.insert(0, repo_root)` + `from config import ...` at the top |
| `setup/00_build_flow_conversion_table.py` | Parses full USLCI zip for unit conversion factors (rebuild-only; a prebuilt `uslci_flow_conversions.json` ships) |
| `setup/01_setup_biosphere_fedefl.py` | Loads FEDEFL elementary flows into brightway |
| `setup/02_setup_traci22.py` | Loads TRACI 2.2 CFs mapped to FEDEFL UUIDs |
| `setup/olca_library.py` | Module (not standalone) — decodes openLCA library/matrix packages (e.g. the electricity baseline); no brightway dependency |
| `setup/vintage_detect.py` | Module (not standalone) — reads which electricity-baseline vintage each bundle's own `defaultProvider` references point at, so `03b` can default to it. Classifies by *dominance*, not presence (the 2025 bundles each carry one stray 2026 reference). Unit-tested by `tests/test_vintage_detect.py` |
| `setup/03b_import_electricity_baseline.py` | Injects the US electricity baseline into brightway as aggregated background activities, discovered per-bundle; auto-fetches + hash-verifies the library. Vintage is **auto-detected from the bundles** via `setup/vintage_detect.py` (`--vintage` overrides; a mixed-vintage bundle dir hard-stops with the split named) |
| `setup/allocation.py` | Module (not standalone) — multi-output allocation logic: reference-product factors, scalar co-product re-basis multipliers, and causal per-exchange factor columns; no brightway dependency. Unit-tested by `tests/test_allocation.py` |
| `setup/03_import_uslci.py` | Parses per-process USLCI JSON-LD exports into brightway; multi-output allocation via `setup/allocation.py`, with a dedicated per-exchange activity per causal co-product (see DEVLOG). Also writes `uslci_db_provenance.json` (per-process import diagnostics) for `general/04`'s audit manifest — additive, does not affect `db_data`/the harness |
| `general/04_run_lca.py` | Operational LCA runner — USLCI process or foreground CSV → 10-category TRACI results CSV. States the build's **electricity-baseline vintage** in the console target block (always, even under `--no-manifest`) and emits `validation_manifest.json` (per-run audit manifest: provenance + electricity vintage + per-result completeness); `--no-manifest` to skip |
| `general/foreground_importer.py` | Module (not standalone) — loaded by `general/04_run_lca.py` to parse and validate foreground inventory CSVs |
| `general/run_manifest.py` | Module (not standalone) — pure (no brightway) assembly of `general/04`'s audit manifest; crosses a result's solved supply chain against `uslci_db_provenance.json` for per-result completeness, and attests the electricity-baseline vintage behind the result (flagging an `inconsistent` build where `03b` was re-run without `03`). Unit-tested by `tests/test_run_manifest.py` |
| `general/chart_units.py` | Module (not standalone) — functional-unit commensurability guard for the scenario-comparison chart: only scenarios sharing the baseline's functional unit are compared, so a "1 m3" vs "1 kg" ratio can't be read as an impact difference. Unit-tested by `tests/test_chart_units.py` |
| `general/06_visualize.py` | Reads CSVs from `general/04`, produces general-use charts |
| `validation/05_validate_uslci.py` | Parity harness — runs brightway LCIA for the locked test cases and diffs against openLCA reference exports, ending with an explicit **REPLICATION GATE: PASS/FAIL** (every cell within `TOLERANCE`, 0.1%). That gate — not a `git diff` on the CSV — is the replication check: absolute scores reproduce only to ~1e-14 and shift with the build's bundle composition. Cases with no reference export SKIP by name, and any such run writes `…_partial.csv` so it can't overwrite the locked table. Mode via `--mode {full_chain,direct}`, default `full_chain`; a non-default electricity vintage writes a tagged CSV (e.g. `…_2026.csv`) |
| `validation/06_visualize_validation.py` | Renders `charts/validation/*.png` from the harness CSVs |

Design decisions and the bugs fixed to reach validation are in `DEVLOG.md`. Read it before making
changes to any script.

## Validation Status

Nine test cases run against openLCA on all 10 TRACI categories, and **every one of the 100
category × process cells reproduces openLCA within 0.1%** — each cell rounds to a BW/OL ratio of
1.000. Full clean parity, no outstanding residual. Two builds, because one build carries one
electricity vintage:

| Build | Cases | Cells | Max dev | Locked table |
|---|---|---|---|---|
| 2025 | petroleum, corn, cement, steel | 40 | <0.0001% | `validation_full_chain_results.csv` |
| 2026 | steel, HDPE flake, PET flake, chlorine, hardboard, soy meal | 60 | 0.00077% | `validation_full_chain_results_2026.csv` |

Steel is in both (no grid electricity ⇒ vintage-agnostic), so the nine distinct processes yield 100
cells. Charts for each build are in `charts/validation/`, vintage-tagged.

Getting there closed the last long-standing gap, the petroleum residual (which had wandered ~1.027 →
~1.05 across earlier builds). Root cause, found 2026-07-21: `setup/03` did not honor USLCI's
`isAvoidedProduct` flag. USLCI marks byproduct energy/material recovery this way — landfill-gas and
MSW-combustion electricity that displaces grid power (`isInput=true` + `isAvoidedProduct=true`).
openLCA credits these (they lower the result); brightway was importing them as ordinary consumption
**burdens**. Because petroleum's toxicity is ~99% grid electricity, that sign error on the landfill
credit was the entire gap. The fix sign-flips avoided-product exchanges into credits — a
first-principles correctness change (not tuned to those cases), which is why it snapped **all 40**
cells then in the test set to 1.000, not just the three it was aimed at — and why the five cases
added later landed on 1.000 on first run. This **supersedes ledger #2**: the earlier
"stale pre-correction crude electricity" attribution was a misattribution — there was no
crude-electricity discrepancy; openLCA was correct throughout. The waste-treatment output-linking fix
(prior commit) was itself correct — it *exposed* the dormant avoided-product bug by pulling the
landfilling process into the supply chains. Full write-up (method, results, appendix) in
`VALIDATION_REPORT.md`; the harness, locked result CSVs, and per-file provenance (`VALIDATION_LOG.md`,
SHA256-pinned asset manifest) live in `validation/`.

**The 2026 build (added 2026-07-23) is where allocation is actually stressed.** `chlorine`
(`a3e150d0…`, 3-way physical split NaOH .5453 / Cl₂ .4357 / H₂ .019), `hardboard` (`ca1d1dfa…`, 7
co-products), and `soybean oil` (`88aee762…`, meal .8051 / oil .1949) are the **first non-degenerate
allocation grids** in the test set — before them the only real physical grid tested was petroleum's.
`recycled-HDPE-flake` (`17664c37…`) and `recycled-PET-flake` (`f7b7280d…`) cover the causal
co-product *consumption* path (ledger #1), the most intricate importer path.

Two gotchas worth knowing: `soybean oil`'s quantitative reference is **Soy meal; at plant**, not the
oil it is named for, so both engines report soy meal. And **economic allocation cannot be validated
against USLCI at all** — all 29 processes declaring `ECONOMIC_ALLOCATION` use factors of exactly 0.0
or 1.0 (census 2026-07-23), so corn already covers the only economic path the data exercises.

Note the parity framing: this is *engine verification against openLCA on identical inputs* — trust in
the mechanics (parser, allocation, solve, LCIA), not certification of any study's real-world results.
Study-level judgment (allocation appropriateness, cutoffs, boundary, data vintage) stays with the
practitioner.

## Conventions

- Never use `bw2io`'s `JSONLDImporter` — known bugs with USLCI `isInput` field (see DEVLOG)
- All biosphere flows keyed by FEDEFL UUID throughout — no name matching
- TRACI methods registered as `('TRACI', '2.2', <indicator>)`
- `bd.projects.migrate_project_25()` is guarded with `if not bd.projects.twofive` — do not remove the guard
- Shared brightway identifiers live in `config.py` at repo root, not redefined per script. A
  script's own local name can still differ (e.g. `setup/03_import_uslci.py`'s `USLCI_DB_NAME`) —
  import the value with `as` rather than hardcoding a fresh literal: `from config import USLCI_DB
  as USLCI_DB_NAME`. Anything not duplicated elsewhere (e.g. `FOREGROUND_DB`, `TEMP_DB_NAME`) stays
  local to its script.
- Default output paths anchor to `config.REPO_ROOT`; explicit CLI path args resolve against CWD.
