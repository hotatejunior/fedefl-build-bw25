# fedefl-build-bw25

**A fast, fully-open, programmable life-cycle assessment pipeline in Python — the open US data stack (USLCI · FEDEFL · TRACI 2.2) loaded into brightway25, ready to script.**

This is a reusable, code-first LCA engine built on a fully open, EPA-aligned data stack. It is a
clean, reproducible recipe for doing US-context LCA in code — something that does not currently exist
in the public domain in published form.

> **Provenance.** This pipeline spun out of a larger (private) LCA project, where it began as the
> background-inventory engine for an asphalt dynamic-LCA study and grew into a general-purpose
> pipeline. This repo is the extracted, streamlined engine.

---

## Why this exists

Life-cycle assessment today forces a trade-off between *open* and *programmable*:

- **openLCA** is the leading open tool, but it is GUI-driven. Calculation is a black box, batch/parametric/time-series work is painful, and it does not slot cleanly into a reproducible, automated, version-controlled pipeline.
- **Commercial tools + ecoinvent** (SimaPro, GaBi) are programmable-ish but require expensive licenses and closed data.
- **brightway** is a powerful, programmable LCA engine in Python — but there is no published, reproducible path to load the specific open US stack (USLCI background + FEDEFL flows + TRACI 2.2 methods) into it. The obvious route, `bw2io`'s `JSONLDImporter`, has known bugs with USLCI's `isInput` field that silently misclassify exchanges.

This project closes that gap: an open, EPA-aligned, **code-first** LCA pipeline you can automate — with correctness verified against openLCA so it can be trusted.

### Why this way (design choices)

- **Fully open, EPA-aligned data stack.** FEDEFL elementary flows, TRACI 2.2 impact methods, and USLCI background processes — all sourced from [LCA Commons](https://www.lcacommons.gov) in openLCA JSON-LD. No ecoinvent license required.
- **Custom JSON-LD parser, not `bw2io`.** Full control over exchange-direction detection avoids the `isInput` misclassification bugs.
- **FEDEFL UUIDs as the universal key.** Every biosphere flow — in the brightway DB, the TRACI method, and the USLCI data — is keyed by FEDEFL UUID. Linking is deterministic; no fragile name-matching.
- **openLCA library interoperability.** `olca_library.py` decodes openLCA's pre-aggregated *library* (matrix) packages (e.g. the US Electricity Baseline) — a format brightway cannot otherwise read — so their pre-solved background can be injected directly.

---

## The pipeline

Compartmentalized into two directories: `setup/` (one-time per machine, shared) and `general/`
(practitioner-facing — the actual engine, no openLCA dependency). Run `setup/` once, in order — the
scripts are deliberately numbered and single-purpose.

| Script | Role |
|--------|------|
| `config.py` | Shared brightway identifiers (project, database, and method names) — imported by every script |
| `setup/00_build_flow_conversion_table.py` | Parse the full USLCI zip for substance-specific unit-conversion factors (rebuild-only; ships with a prebuilt table) |
| `setup/01_setup_biosphere_fedefl.py` | Load FEDEFL elementary flows into brightway |
| `setup/02_setup_traci22.py` | Load TRACI 2.2 characterization factors, mapped to FEDEFL UUIDs |
| `setup/03b_import_electricity_baseline.py` | Inject the US electricity baseline (an openLCA library package) into brightway as aggregated background — run before `03`; auto-fetches the library |
| `setup/03_import_uslci.py` | Parse per-process USLCI JSON-LD exports → brightway database |
| `setup/olca_library.py` | Standalone decoder for openLCA library (matrix) packages |
| `general/04_run_lca.py` | Operational LCA runner — USLCI process or foreground CSV → 10-category TRACI results |
| `general/foreground_importer.py` | Module — parses/validates foreground inventory CSVs for `04` |
| `general/06_visualize.py` | General-use charts from `04`'s CSVs (no brightway dependency) |

A practitioner can bring a foreground inventory as CSV, pull the relevant background processes from LCA Commons, and get TRACI results on a fully open stack — the classic brightway workflow, made reproducible end to end.

---

## Getting started (zero to first result)

### 1. Create the Python environment

```bash
conda env create -f environment.yml
conda activate fedefl-build-bw25
```

This pins the exact package set the pipeline was validated against, including the two EPA
packages (`fedelemflowlist`, `lciafmt`) that install from GitHub rather than PyPI.

### 2. Assemble the data assets (`source_data/`)

`setup/01` (FEDEFL flows) and `setup/02` (TRACI methods) need **no local files** — they pull from
their pip packages, and `02` downloads and caches the TRACI factors on first run. The remaining
scripts read external LCA Commons / openLCA assets from a `source_data/` folder at the repo root.
Point elsewhere with the `SOURCE_DATA_DIR` environment variable if you keep assets outside the repo.

| Needed by | Asset |
|-----------|-------|
| `setup/03` | Per-process USLCI supply-chain **bundle zips** (openLCA JSON-LD) for the processes you want |
| `setup/03b` | The **US electricity baseline** openLCA library package — **auto-fetched** if absent (see below) |

The full USLCI zip that `setup/00` parses ships as a prebuilt table already, so `00` needs no asset
unless you rebuild it. The electricity baseline is sourced automatically: `setup/03b` downloads the
version-pinned library from the Federal LCA Commons GitHub and verifies its SHA256 before use (pass
`--no-fetch` to require a local copy, or `--library` to point at your own). That leaves just the
USLCI process bundles to assemble by hand from [LCA Commons](https://www.lcacommons.gov) — process
selection is left to your discretion by design.

### 3. Build the brightway databases (once per machine)

Run the setup scripts in order. `03b` must precede `03` — it injects the electricity background that
`03`'s relink step resolves against.

```bash
python setup/00_build_flow_conversion_table.py   # USLCI unit-conversion table (prebuilt; rebuild optional)
python setup/01_setup_biosphere_fedefl.py        # FEDEFL flows      (from pip package)
python setup/02_setup_traci22.py                 # TRACI 2.2 methods (downloads on first run)
python setup/03b_import_electricity_baseline.py  # electricity baseline background (auto-fetches)
python setup/03_import_uslci.py                  # USLCI processes
```

### 4. Run an analysis

```bash
# A USLCI background process, by UUID:
python general/04_run_lca.py --uuid <USLCI-process-UUID>

# ...or your own foreground system from a CSV inventory:
python general/04_run_lca.py --foreground my_inventory.csv --target-process "My process"
```

Writes `lca_results.csv` (10-category TRACI scores) and `lca_contributions.csv` to the repo root.

### 5. Visualize

```bash
python general/06_visualize.py    # auto-detects the CSVs → charts/general/
```

Scripts can be run from any directory — default inputs/outputs anchor to the repo root; explicit
path arguments resolve against your current directory.

---

## Trust — validated against openLCA

The numbers are established by a rigorous, **apples-to-apples** comparison against openLCA: identical
USLCI JSON-LD fed to both engines, so any difference is attributable to the pipeline, not to
data-version drift. Across four locked test cases (petroleum refining, corn, Portland cement, steel
billets), **all 40 category × process cells validate within 5%**, and 35 of 40 within ±1%.

A governing principle keeps that honest: **validate, don't fit** — the harness is a neutral observer
with no knowledge of the expected answers; discrepancies are diagnosed to root cause, never tuned
away. The full write-up — method, per-process results, and the technical appendix — is in
[`VALIDATION_REPORT.md`](VALIDATION_REPORT.md). The validation harness itself and the running
provenance log (per-file SHA256s and dataset versions) live in the private parent project this
engine was extracted from.

---

## Documentation

| File | Purpose |
|------|---------|
| [`DEVLOG.md`](DEVLOG.md) | Engine design decisions and the bugs fixed to reach validation — read before changing any script |
| [`VALIDATION_REPORT.md`](VALIDATION_REPORT.md) | The openLCA validation write-up — method, results, and technical appendix |
| [`CLAUDE.md`](CLAUDE.md) | Environment, conventions, and pipeline status |

---

## Environment

- **Conda env:** `fedefl-build-bw25` (Python 3.11) — defined in [`environment.yml`](environment.yml).
  Core stack: brightway25 (`bw2data` 4.7, `bw2calc` 2.5), the EPA open-data packages `fedelemflowlist`
  and `lciafmt` (git installs), and `pandas` / `numpy` / `scipy` / `matplotlib` / `seaborn` /
  `openpyxl`. All versions are pinned to the validated combination.
- **Brightway project:** `asphalt-lca`
- **Data assets:** external LCA Commons / openLCA files live in `source_data/` (override with
  `SOURCE_DATA_DIR`). See "Getting started" above.
- **Platform:** macOS

See [`CLAUDE.md`](CLAUDE.md) for conventions and pipeline status.

---

## Roadmap

- **Now:** the general pipeline is built, audited, and validated against openLCA across four locked test cases.
- **Next:** full USLCI database import (~10,000 processes) with fast process selection, replacing per-process downloads.
- **Then:** dynamic LCA — time-resolved, parameterized, scenario-swept impact modeling on top of this engine, run programmatically and faster than the incumbent GUI tools.
