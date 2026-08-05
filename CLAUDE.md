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

**Every step is also callable, so the whole pipeline fits in one script** —
`examples/full_pipeline.py` is that script. Setup steps take `overwrite=True` (or a `confirm`
callback) rather than prompting, return a build object rather than printing, and send progress to
an optional `log`. The chain is directional: rebuilding a step renumbers brightway's internal ids,
so everything downstream of it must be rebuilt too or the next solve goes non-square.

Scripts can be invoked from any directory: default output locations (`lca_results.csv`,
`lca_contributions.csv`, `charts/`) anchor to the repo root via `config.REPO_ROOT`. Paths passed
explicitly on the CLI resolve against the CWD, as is standard.

| Script | Role |
|--------|------|
| `fedefl_bw25/` | **The importable package** — every step of the pipeline as a function, plus the pure helpers (`config`, `allocation`, `olca_library`, `vintage_detect`, `foreground_importer`, `run_manifest`, `chart_units`, `setup_conversions`), which are brightway-free and unit-test without a built database. `pip install -e .` to use. The numbered scripts below are CLI front-ends over it |
| `examples/full_pipeline.py` | **The whole pipeline in one file** — conversion table → biosphere → TRACI → baseline → USLCI → a multi-target study, as library calls. Idempotent (each step's `…Exists` means "skip"); `--rebuild` redoes the whole chain. Meant to be copied and edited |
| `fedefl_bw25/run.py` | Module — `run_lca()` returns an `LcaRun` instead of writing files; the callable core behind `general/04` |
| `fedefl_bw25/setup_uslci.py` | Module — `import_uslci()` behind `setup/03`; **builds the database the locked validation is computed against**, so treat any change as a validation event (rebuild both builds, re-run the gate). Structured as named stages (`load_conversion_table`, `UnitNormalizer`, `discover_sources`, `load_sources`, `index_reference_flows`, `load_external_providers`, `ProviderResolver`, `plan_allocation`, `build_activities`), each unit-tested by `tests/test_setup_uslci.py` |
| `fedefl_bw25/setup_baseline.py` | Module — `inject_baseline()` behind `setup/03b`; scans sources, decodes the openLCA library, writes the baseline. Prints nothing and never prompts (`overwrite=` / `confirm=`) |
| `fedefl_bw25/config.py` | Shared identifiers (`PROJECT_NAME`, `BIOSPHERE_DB`, `USLCI_DB`, `USLCI_FULL_DB`, `ELECTRICITY_BASELINE_DB`, `METHOD_ROOT`) and `REPO_ROOT` — single source of truth, imported by every script below as `from fedefl_bw25.config import ...` |
| `fedefl_bw25/setup_conversions.py` | Module — `build_conversions()` / `write_conversion_table()` / `describe_conversions()` behind `setup/00`. No brightway; unit-tested by `tests/test_setup_conversions.py` |
| `setup/00_build_flow_conversion_table.py` | CLI front-end — parses the full USLCI zip for cross-flow-property conversion factors (rebuild-only; a prebuilt `uslci_flow_conversions.json` ships). Without `--rebuild` it only reports the committed table's provenance |
| `fedefl_bw25/setup_biosphere.py` | Module — `import_biosphere()` behind `setup/01`. Guards the FEDEFL fetch (renamed columns, short fetch, UUID collisions); unit-tested by `tests/test_setup_biosphere.py` |
| `setup/01_setup_biosphere_fedefl.py` | CLI front-end — loads FEDEFL elementary flows into brightway. `--yes` skips the rebuild prompt |
| `fedefl_bw25/setup_traci.py` | Module — `import_traci()` behind `setup/02`; enforces the pinned CF-source SHA256s (ledger #4). No exists-guard: methods carry no downstream ids, so each run rewrites them. Unit-tested by `tests/test_setup_traci.py` |
| `setup/02_setup_traci22.py` | CLI front-end — loads TRACI 2.2 CFs mapped to FEDEFL UUIDs. `--eutro-location` selects the eutrophication spatial variant (`''` = generic, the openLCA-parity default) |
| `fedefl_bw25/olca_library.py` | Module (not standalone) — decodes openLCA library/matrix packages (e.g. the electricity baseline); no brightway dependency |
| `fedefl_bw25/vintage_detect.py` | Module (not standalone) — reads which electricity-baseline vintage each bundle's own `defaultProvider` references point at, so `03b` can default to it. Classifies by *dominance*, not presence (the 2025 bundles each carry one stray 2026 reference). Unit-tested by `tests/test_vintage_detect.py` |
| `setup/03b_import_electricity_baseline.py` | CLI front-end over `fedefl_bw25/setup_baseline.py` — injects the US electricity baseline as aggregated background activities, discovered from the bundles **and** the full USLCI zip; auto-fetches + hash-verifies the library. Vintage auto-detected via `fedefl_bw25/vintage_detect.py`: bundles decide it when any of them references a grid node, and the full USLCI zip decides it when none do (the whole-database workflow, where there are no bundles). `--vintage` overrides; mixed-vintage bundles hard-stop. `--yes` skips the overwrite prompt for scripted rebuilds |
| `fedefl_bw25/allocation.py` | Module (not standalone) — multi-output allocation logic: reference-product factors, scalar co-product re-basis multipliers, and causal per-exchange factor columns; no brightway dependency. Unit-tested by `tests/test_allocation.py`. Behaviour documented in `docs/ALLOCATION.md` |
| `setup/03_import_uslci.py` | CLI front-end over `fedefl_bw25/setup_uslci.py` — parses USLCI JSON-LD into brightway; multi-output allocation via `fedefl_bw25/allocation.py`, with a dedicated per-exchange activity per causal co-product (see DEVLOG). Writes `uslci_db_provenance.json` for `general/04`'s audit manifest. `--full-db` (or `USLCI_FULL_DB=1`) builds the whole database; `--yes` skips the overwrite prompt |
| `fedefl_bw25/search.py` | Module — `search_processes()` behind `general/04 --search`; finds a process by name when the runner needs a UUID. All query words must appear somewhere in the name, in any order (`hdpe flake` → `Recycled postconsumer high-density polyethylene, HDPE, flake; at plant`, which contains no such substring). Ranks a hit in the pre-semicolon product segment above one anywhere else, so `diesel` returns fuels before the 200-odd diesel-powered transport processes. Matching and ranking are brightway-free; unit-tested by `tests/test_search.py` |
| `general/04_run_lca.py` | Operational LCA runner — USLCI process or foreground CSV → 10-category TRACI results CSV. `--search TEXT` looks a process up by name across every built database, prints the UUID and the exact command to run it, and exits (before importing bw2calc). States the build's **electricity-baseline vintage** in the console target block (always, even under `--no-manifest`) and emits `validation_manifest.json` (per-run audit manifest: provenance + electricity vintage + per-result completeness); `--no-manifest` to skip |
| `fedefl_bw25/foreground_importer.py` | Module (not standalone) — parses and validates foreground inventory CSVs behind `general/04`. `read_rows` / `group_by_process` / `parse_row` are separable; warnings and the parse summary go to `log=` (they used to `print`, which leaked through `run_lca`'s no-op log). Unit-tested by `tests/test_foreground_importer.py` |
| `fedefl_bw25/run_manifest.py` | Module (not standalone) — pure (no brightway) assembly of `general/04`'s audit manifest; crosses a result's solved supply chain against `uslci_db_provenance.json` for per-result completeness, and attests the electricity-baseline vintage behind the result (flagging an `inconsistent` build where `03b` was re-run without `03`). Unit-tested by `tests/test_run_manifest.py` |
| `fedefl_bw25/chart_units.py` | Module (not standalone) — functional-unit commensurability guard for the scenario-comparison chart: only scenarios sharing the baseline's functional unit are compared, so a "1 m3" vs "1 kg" ratio can't be read as an impact difference. Unit-tested by `tests/test_chart_units.py` |
| `general/06_visualize.py` | Reads CSVs from `general/04`, produces general-use charts |
| `validation/05_validate_uslci.py` | Parity harness — runs brightway LCIA for the locked test cases and diffs against openLCA reference exports, ending with an explicit **REPLICATION GATE: PASS/FAIL** (every cell within `TOLERANCE`, 0.1%). That gate — not a `git diff` on the CSV — is the replication check: absolute scores reproduce only to ~1e-14 and shift with the build's bundle composition. Cases with no reference export SKIP by name, and any such run writes `…_partial.csv` so it can't overwrite the locked table. Mode via `--mode {full_chain,direct}`, default `full_chain`; a non-default electricity vintage writes a tagged CSV (e.g. `…_2026.csv`) |
| `validation/06_visualize_validation.py` | Renders `charts/validation/*.png` from the harness CSVs |

Design decisions and the bugs fixed to reach validation are in `docs/DEVLOG.md`. Read it before making
changes to any script.

## Documentation layout

Only `README.md` and this file live at the repo root; everything else is under `docs/` (moved
2026-08-05). `docs/DEVLOG.md` carries both the engine history and the verbatim release-plan record;
`docs/ROADMAP.md` carries open work only, and is the file to update when something lands. `docs/TUTORIAL.md` is the one narrative path for a new practitioner and owns the widget foreground example; `docs/HOWTO.md` is the reference and links to it rather than repeating it. The
`validation/` directory is separate on purpose: it holds evidence and verbatim history, and
`VALIDATION_LOG.md` in particular is never rewritten — stale names and paths there are corrected by
appending to its provenance note, not by editing the body.

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
`docs/VALIDATION_REPORT.md`; the harness, locked result CSVs, and per-file provenance (`VALIDATION_LOG.md`,
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
- **Reusable logic lives in the `fedefl_bw25` package; the numbered scripts are CLI front-ends.**
  Install once with `pip install -e .` — the old `sys.path.insert` bootstraps are gone, and scripts
  import normally (`from fedefl_bw25.config import ...`). Anything a test needs to reach is a
  reason to move that logic into the package: the numbered scripts are not importable (leading
  digits, and they execute on import).
- **Package code neither prints nor prompts.** Progress goes to an optional `log` callable
  (defaulting to a no-op); replacing an existing database takes `overwrite=True` or a `confirm`
  callback, so a scripted build never blocks on stdin. Each step returns a build object rather
  than printing its diagnostics — the CLI owns all console output and all file writing.
- Extractions of a numbered script are done as a **mechanical copy-and-indent**, then verified by
  rebuilding — not by retyping. Reconstructing `03b`'s helpers from memory introduced three silent
  bugs; the copy-and-verify method has since been clean. For `01`/`02` the check was a build into a
  throwaway project diffed cell-for-cell against the live one, which leaves the validated build
  untouched.
- Shared brightway identifiers live in `fedefl_bw25/config.py`, not redefined per script. A
  script's own local name can still differ (e.g. `setup/03_import_uslci.py`'s `USLCI_DB_NAME`) —
  import the value with `as` rather than hardcoding a fresh literal: `from fedefl_bw25.config
  import USLCI_DB as USLCI_DB_NAME`. Anything not duplicated elsewhere (e.g. `FOREGROUND_DB`,
  `TEMP_DB_NAME`) stays local to its script.
- Default output paths anchor to `config.REPO_ROOT`; explicit CLI path args resolve against CWD.
  `REPO_ROOT` is the checkout when one is detectable (the package's parent contains `setup/`) and
  falls back to CWD otherwise, so a non-editable install never writes into site-packages.
