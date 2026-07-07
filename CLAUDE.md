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
| `setup/03b_import_electricity_baseline.py` | Injects the US electricity baseline into brightway as aggregated background activities, discovered per-bundle; auto-fetches + hash-verifies the library |
| `setup/03_import_uslci.py` | Parses per-process USLCI JSON-LD exports into brightway |
| `general/04_run_lca.py` | Operational LCA runner — USLCI process or foreground CSV → 10-category TRACI results CSV |
| `general/foreground_importer.py` | Module (not standalone) — loaded by `general/04_run_lca.py` to parse and validate foreground inventory CSVs |
| `general/06_visualize.py` | Reads CSVs from `general/04`, produces general-use charts |
| `validation/05_validate_uslci.py` | Parity harness — runs brightway LCIA for the locked test cases and diffs against openLCA reference exports; overwrites `validation/validation_full_chain_results.csv` (an empty `git diff` on it is the byte-for-byte parity check). Mode set by `VALIDATION_MODE` constant |
| `validation/06_visualize_validation.py` | Renders `charts/validation/*.png` from the harness CSVs |

Design decisions and the bugs fixed to reach validation are in `DEVLOG.md`. Read it before making
changes to any script.

## Validation Status

All four locked test cases (petroleum, corn, cement, steel) validate against openLCA on all 10 TRACI
categories — every one of the 40 category × process cells lands within ±5%, 35 of 40 within ±1%.
Max deviation is petroleum's ecotox/cancer/non-cancer at ~1.027, within tolerance. Full write-up
(method, results, appendix) in `VALIDATION_REPORT.md`; the harness, locked result CSVs, and per-file
provenance (`VALIDATION_LOG.md`, SHA256-pinned asset manifest) live in `validation/`.

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
