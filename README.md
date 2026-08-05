# fedefl-build-bw25

**A fast, fully-open, programmable life-cycle assessment pipeline in Python — the open US data stack
(USLCI · FEDEFL · TRACI 2.2) loaded into brightway25, ready to script.**

A reproducible, code-first recipe for US-context LCA on an open, EPA-aligned data stack — with the
engine verified against openLCA cell for cell.

---

## Why this exists

LCA today forces a trade-off between *open* and *programmable*:

- **openLCA** is the leading open tool, but it is GUI-driven. Calculation is a black box, and
  batch/parametric/time-series work doesn't slot into a version-controlled pipeline.
- **SimaPro / GaBi with ecoinvent** are scriptable-ish, but require expensive licenses and closed data.
- **brightway** is a genuinely programmable LCA engine in Python — but there is no published,
  reproducible path to load the open US stack into it. The obvious route, `bw2io`'s `JSONLDImporter`,
  has known bugs with USLCI's `isInput` field that silently misclassify exchanges.

This project closes that gap. Four choices carry the design:

- **Fully open, EPA-aligned data.** FEDEFL elementary flows, TRACI 2.2 impact methods, and USLCI
  background processes — all from [LCA Commons](https://www.lcacommons.gov) in openLCA JSON-LD. No
  ecoinvent license required.
- **A custom JSON-LD parser, not `bw2io`.** Full control over exchange-direction detection, which is
  what avoids the `isInput` misclassification.
- **FEDEFL UUIDs as the universal key.** Every biosphere flow — in the brightway DB, in the TRACI
  method, in the USLCI data — is keyed by FEDEFL UUID. Linking is deterministic; no name-matching.
- **openLCA library interoperability.** `fedefl_bw25/olca_library.py` decodes openLCA's pre-aggregated
  *library* (matrix) packages — a format brightway cannot otherwise read — so a pre-solved background
  such as the US Electricity Baseline can be injected directly.

---

## The pipeline

Three directories: **`setup/`** (one-time per machine), **`general/`** (the practitioner-facing
engine — no openLCA dependency), and **`validation/`** (the openLCA parity harness and locked
results). The scripts are deliberately numbered and single-purpose.

The reusable logic lives in an importable package, **`fedefl_bw25`**; the numbered scripts are CLI
front-ends over it.

| Script | Role |
|--------|------|
| `fedefl_bw25/config.py` | Shared brightway identifiers (project, database, method names) — imported by every script |
| `examples/full_pipeline.py` | The whole pipeline in one file — every step below as a library call, from setup through a multi-target study |
| `setup/00_build_flow_conversion_table.py` | Parse the full USLCI zip for substance-specific unit-conversion factors (rebuild-only; ships prebuilt) |
| `setup/01_setup_biosphere_fedefl.py` | Load FEDEFL elementary flows into brightway |
| `setup/02_setup_traci22.py` | Load TRACI 2.2 characterization factors, mapped to FEDEFL UUIDs |
| `setup/03b_import_electricity_baseline.py` | Inject the US electricity baseline as aggregated background — **run before `03`**; auto-fetches the library and auto-detects the vintage your bundles need |
| `setup/03_import_uslci.py` | Parse per-process USLCI JSON-LD exports → brightway database, with multi-output allocation |
| `general/04_run_lca.py` | The runner — USLCI process or foreground CSV → 10-category TRACI results, plus a per-run audit manifest |
| `general/06_visualize.py` | Charts from `04`'s CSVs (no brightway dependency) |
| `validation/05_validate_uslci.py` | Parity harness — diffs brightway against the locked openLCA reference exports, ending in a PASS/FAIL replication gate |

Every script above is a thin front-end over a package function — `import_biosphere()`,
`import_traci()`, `inject_baseline()`, `import_uslci()`, `run_lca()` — so the whole pipeline can be
configured and run from a single script instead of five invocations. Supporting modules (not run
directly): `fedefl_bw25/allocation.py`, `fedefl_bw25/olca_library.py`, `fedefl_bw25/vintage_detect.py`,
`fedefl_bw25/foreground_importer.py`, `fedefl_bw25/run_manifest.py`, `fedefl_bw25/chart_units.py`.
All are unit-tested under `tests/`.

A practitioner brings a foreground inventory as CSV, pulls the relevant background processes from
LCA Commons, and gets TRACI results on a fully open stack — the classic brightway workflow, made
reproducible end to end.

---

## Getting started (zero to first result)

### 1. Create the Python environment

```bash
conda env create -f environment.yml
conda activate fedefl-build-bw25
```

This pins the exact package set the pipeline was validated against, including the two EPA packages
(`fedelemflowlist`, `lciafmt`) that install from GitHub rather than PyPI.

Then install this repo itself, so `fedefl_bw25` is importable and the scripts resolve it:

```bash
pip install -e .
```

Editable is the supported mode: the scripts, `source_data/` and the default output locations all
anchor to the checkout. With that in place you can also drive the pipeline as a library —
`from fedefl_bw25.foreground_importer import load_foreground_csv` — rather than only as scripts.

### 2. Assemble the data assets (`source_data/`)

`setup/01` and `setup/02` need **no local files** — they pull from their pip packages, and `02`
downloads and caches the TRACI factors on first run. The remaining scripts read external LCA Commons
/ openLCA assets from `source_data/` at the repo root; point elsewhere with the `SOURCE_DATA_DIR`
environment variable.

| Needed by | Asset | How you get it |
|-----------|-------|----------------|
| `setup/03b` | US electricity baseline (openLCA library package) | **Auto-fetched** — downloaded version-pinned from the Federal LCA Commons GitHub and SHA256-verified. `--no-fetch` to require a local copy, `--library` for your own |
| `setup/03` | Per-process USLCI supply-chain **bundle zips** (openLCA JSON-LD) | **Download by hand** from [LCA Commons](https://www.lcacommons.gov) |
| `setup/00` | Full USLCI zip | Only if you rebuild the conversion table — it ships prebuilt |

For the bundles, pick each unit process you want to analyze on LCA Commons and download it as an
openLCA JSON-LD export. The Commons packages each process together with its full upstream supply
chain, so one download gives you the process *plus* everything it draws on. Drop the zips in
`source_data/`; `setup/03` imports whatever it finds.

> ⚠️ **Keep the original filename.** `setup/03` discovers bundles by their download naming pattern,
> `<process-uuid>_<hash>.zip`. A renamed bundle is skipped — the import prints a WARNING naming it,
> and `general/04` will later report the process as not found in the database.

> **Which baseline vintage?** You don't have to know. The US-average grid node is renamed in place
> across baseline releases — same name, different UUID each vintage — and a build injects exactly
> one. `setup/03b` reads which vintage your bundles actually reference and defaults to it. If
> `source_data/` mixes releases it stops and shows you the split, since one build cannot satisfy
> both. `--vintage {2025|2026}` overrides.

### 3. Build the brightway databases (once per machine)

Run in order. `03b` must precede `03` — it injects the electricity background that `03`'s relink
step resolves against.

```bash
python setup/00_build_flow_conversion_table.py   # conversion table (prebuilt; rebuild optional)
python setup/01_setup_biosphere_fedefl.py        # FEDEFL flows      (from pip package)
python setup/02_setup_traci22.py                 # TRACI 2.2 methods (downloads on first run)
python setup/03b_import_electricity_baseline.py  # electricity background (auto-fetches)
python setup/03_import_uslci.py                  # USLCI processes
```

Or do all five — and the analysis in step 4 — from one script:

```bash
python examples/full_pipeline.py
```

It builds whatever is missing, skips what already exists, and runs a study across several
processes. Copy it and edit the CONFIGURE block for your own work; see docs/HOWTO.md §4.

### 4. Run an analysis

Find the process you want by name — every word has to appear somewhere in it, in any order:

```bash
python general/04_run_lca.py --search "hdpe flake"
```

```
1 match(es) for 'hdpe flake':

  17664c37-72c0-4813-a4b9-93f962962c63  subset,full  RNA  kg  Recycled postconsumer high-density polyethylene, HDPE, flake; at plant

Run the first with:
  python general/04_run_lca.py --uuid 17664c37-72c0-4813-a4b9-93f962962c63
```

Then run it, or point the runner at your own inventory instead:

```bash
python general/04_run_lca.py --uuid <USLCI-process-UUID>
python general/04_run_lca.py --foreground my_inventory.csv --target-process "My process"
```

Writes `lca_results.csv` (10-category TRACI scores), `lca_contributions.csv`, and
`validation_manifest.json` (per-run audit trail: provenance, electricity vintage, per-result
completeness) to the repo root.

### 5. Visualize

```bash
python general/06_visualize.py    # auto-detects the CSVs → charts/general/
```

Scripts run from any directory — default inputs/outputs anchor to the repo root; explicit path
arguments resolve against your current directory.

---

## Trust — engine parity with openLCA

What is established here is **engine parity**: on *identical* inputs, this pipeline reproduces the
numbers openLCA computes. It is a check on the mechanics — the JSON-LD parser, allocation, provider
linking, and the LCIA solve — not a validation of any study's real-world results. The comparison is
deliberately apples-to-apples: the same USLCI JSON-LD goes into both engines, so any difference is
attributable to the pipeline, not to data-version drift.

Across nine test cases, **all 100 category × process cells reproduce openLCA within 0.1%** — every
cell rounds to a ratio of 1.000. They run as two builds, because the electricity baseline renames its
grid node between releases and one build carries one vintage:

| Build | Cases | Cells | Max deviation |
|---|---|---|---|
| 2025 baseline | petroleum refining, corn, Portland cement, steel billets | 40 | <0.0001% |
| 2026 baseline | steel billets, HDPE flake, PET flake, chlorine, hardboard, soy meal | 60 | 0.00077% |

Steel appears in both — it has no grid electricity, so it is vintage-agnostic — which is why nine
distinct processes yield 100 cells. Steel is also the deliberate **foreground-only control**: a
single process with no upstream, so it isolates the LCIA math and flow mapping from the supply-chain
solve. Full-chain coverage therefore rests on the other eight.

**Where allocation gets stressed.** Chlorine (chlor-alkali) splits three ways, hardboard seven, and
soybean oil two — the first non-degenerate allocation grids in the test set, all reproducing openLCA
within 0.001%. Recycled HDPE and PET flake exercise the engine's most intricate path, consumption of
a *causal-allocation co-product*.

**One honest gap:** economic allocation cannot be tested against USLCI at all. Every one of its 29
economic-allocation processes assigns a factor of exactly 0.0 or 1.0, so no real economic split
exists in the data to check against.

**What parity does *not* cover** stays with the practitioner: whether the allocation choices, system
boundary, cutoffs, and data vintage suit *your* study is a modeling judgment the engine cannot make
for you. Parity means the arithmetic is trustworthy; the science of the study is still yours to
defend.

Method, per-process results, and the technical appendix are in
[`VALIDATION_REPORT.md`](docs/VALIDATION_REPORT.md); the reproducible harness and running provenance log
are in [`validation/`](validation/).

---

## Provenance & how this was built

This engine was built with heavy AI assistance, and it is worth being direct about that — because
the trust case does **not** rest on who typed the code.

- **AI-assisted development.** Most of the implementation was written by Claude (Anthropic) under
  close human direction, including the JSON-LD parser, the co-product allocation handling, and the
  openLCA-library matrix decoder.
- **Human accountability.** A human expert made the method decisions (allocation approach,
  electricity-boundary control, generic-vs-regional CF selection), ran every openLCA reference
  session by hand, directed the debugging, and audited each script against
  [`QC_PROTOCOL.md`](docs/QC_PROTOCOL.md). Where AI-generated work was accepted without proportionate
  review, that is tracked openly in the [devlog's](docs/DEVLOG.md) under-review ledger to be checked later.
- **The governing principle: VALIDATE, do not FIT.** Discrepancies against openLCA were root-caused,
  never tuned away. The code is general and data-driven — no hardcoded per-dataset constants, no
  special-casing of the test processes. Petroleum's toxicity categories started at **2.0×** openLCA
  and took three genuine bug fixes to reach 1.000; two comfortable-sounding intermediate explanations
  were *disproven* by evidence, including one that wrongly blamed the openLCA reference and had to be
  retracted. All of it is kept on the record in [`DEVLOG.md`](docs/DEVLOG.md) and the chronological
  [`validation/VALIDATION_LOG.md`](validation/VALIDATION_LOG.md), not quietly deleted.

**Why provenance is orthogonal to correctness here.** The evidence chain is *pinned inputs →
reproducible harness → locked outputs*. Anyone can re-run the harness from
[`validation/`](validation/) against the SHA256-pinned inputs and check the locked result CSVs. That
chain does not depend on who — or what — typed the code.

---

## Documentation

| File | Purpose |
|------|---------|
| [`HOWTO.md`](docs/HOWTO.md) | Task guides for running your own study: reading and changing the functional unit, linking a foreground CSV to USLCI, and choosing what background a result is calculated against |
| [`SCHEMA_CROSSWALK.md`](docs/SCHEMA_CROSSWALK.md) | Field-by-field map of USLCI openLCA JSON-LD → the brightway schema, and how the parser resolves the places the two data models don't line up |
| [`ALLOCATION.md`](docs/ALLOCATION.md) | How multi-output processes are split — the four allocation paths, how N co-products are reshaped into brightway's one-product-per-activity matrix, and what USLCI actually contains |
| [`DEVLOG.md`](docs/DEVLOG.md) | Engine design decisions, the bugs fixed to reach validation, and the dated release-plan record — read before changing any script |
| [`ROADMAP.md`](docs/ROADMAP.md) | What is still open, and what has been ruled out |
| [`VALIDATION_REPORT.md`](docs/VALIDATION_REPORT.md) | The openLCA parity write-up — method, results, technical appendix |
| [`validation/`](validation/) | The parity harness, locked result CSVs, and `VALIDATION_LOG.md` (every run, disproven theory, and fix, in order). See [`validation/README.md`](validation/README.md) to replicate |
| [`QC_PROTOCOL.md`](docs/QC_PROTOCOL.md) | The per-script human audit process each script was reviewed against |

---

## Environment

- **Conda env:** `fedefl-build-bw25` (Python 3.11) — see [`environment.yml`](environment.yml). Core
  stack: brightway25 (`bw2data` 4.7, `bw2calc` 2.5), the EPA packages `fedelemflowlist` and `lciafmt`
  (git installs), and `pandas` / `numpy` / `scipy` / `matplotlib` / `seaborn` / `openpyxl`. All
  versions pinned to the validated combination.
- **Brightway project:** `fedefl-build-bw25`
- **Data assets:** `source_data/` at the repo root (override with `SOURCE_DATA_DIR`)
- **Platform:** macOS

---

## Roadmap

- **Now:** the engine is built, audited, and validated against openLCA — nine test cases at full
  parity (100/100 cells within 0.1%), spanning physical, causal, and no-allocation processes across
  two electricity-baseline vintages.
- **Next:** parameterized foregrounds and fast scenario sweeps — declare parameters in the
  inventory CSV, vary them from a params file, and get a curve instead of a point.
- **Then:** dynamic LCA — time-resolved, scenario-swept impact modeling on top of this engine, run
  programmatically and faster than the incumbent GUI tools.

Open items in detail: [`docs/ROADMAP.md`](docs/ROADMAP.md).

---

## Author, citation & reporting issues

- **Author:** Harrison Watson
- **Report a discrepancy or bug:** open a
  [GitHub issue](https://github.com/hotatejunior/fedefl-build-bw25/issues). Parity results and bug
  reports are especially welcome — the trust case here is built on independent scrutiny.
- **License:** [MIT](LICENSE) — free to use, modify, and redistribute; no warranty.
- **Citing this work:** see [`CITATION.cff`](CITATION.cff) (GitHub renders a "Cite this repository"
  button from it).
