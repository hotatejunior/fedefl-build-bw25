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
`--no-fetch` to require a local copy, or `--library` to point at your own).

That leaves the **USLCI process bundles**, which you download by hand from
[LCA Commons](https://www.lcacommons.gov). Pick each unit process you want to analyze and download it
as an openLCA JSON-LD export — the Commons packages each process together with its full upstream
supply chain, so one download gives you the process *plus* everything it draws on. Drop the bundle
zip(s) in `source_data/`; `setup/03` imports whatever it finds there. You then run LCA on the
processes you imported this way.

> **Keep the original filename.** `setup/03` discovers bundles by their download naming pattern,
> `<process-uuid>_<hash>.zip` (e.g. `1cbbcd09-…-2cf42efe26ba_a900b507….zip`). Do **not** rename the
> zips — a renamed bundle is skipped, and the import prints a WARNING naming any zip it skipped on
> naming grounds. If a process you expected is missing at run time (`general/04` reports it not
> found in the database), a renamed bundle is the first thing to check.

> **Workflow note.** The intended workflow today is per-process: download the specific unit
> processes you need. Pointing the engine at a single whole-USLCI database and running *any* process
> out of it — no per-process downloads — is a **work-in-progress feature** (see [Roadmap](#roadmap)).

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
# A USLCI process you imported in Step 2, by UUID:
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

## Trust — engine parity with openLCA

What's established here is **engine parity**: on *identical* inputs, this pipeline reproduces the
numbers openLCA computes. It is a check on the mechanics — the JSON-LD parser, allocation, provider
linking, and the LCIA solve — not a validation of any study's real-world results. The comparison is
deliberately apples-to-apples: identical USLCI JSON-LD is fed to both engines, so any difference is
attributable to the pipeline, not to data-version drift.

Across four locked test cases (petroleum refining, corn, Portland cement, steel billets), **all 40
category × process cells reproduce openLCA within 0.1%** — every cell rounds to a ratio of 1.000. The
last gap to close was petroleum's toxicity categories: they traced to a single importer bug (brightway
was not honoring USLCI's `isAvoidedProduct` flag, so byproduct energy-recovery *credits* — chiefly the
landfill-gas electricity that displaces grid power — were imported as burdens). Fixing it snapped all
of petroleum, and tightened corn and cement, to exact agreement. (One caveat: the steel bundle is a
single process with no upstream, and it's there on purpose — as the foreground-only control. Because
it has no supply chain to solve, it isolates the LCIA math and flow mapping, so a discrepancy can be
pinned to the foreground, the background, or both. It validates at 1.000, so full-chain coverage
rests on petroleum, corn, and cement.)

A fifth case, **recycled-HDPE flake**, validates separately within 0.01% on all 10 categories. It is
the case that exercises the engine's most intricate path — consumption of a *causal-allocation
co-product* — and it runs on its own build because its bundle is pinned to a newer electricity-grid
vintage (see [`validation/README.md`](validation/README.md)).

**What this does *not* cover** stays with the practitioner: whether the allocation choices, system
boundary, cutoffs, and data vintage are appropriate for *your* study is a modeling judgment the
engine can't make for you. Parity means the arithmetic is trustworthy; the science of the study is
still yours to defend.

The full write-up — method, per-process results, the debugging arc that reached parity, and the
technical appendix — is in [`VALIDATION_REPORT.md`](VALIDATION_REPORT.md), with the reproducible
harness and running provenance log in [`validation/`](validation/).

---

## Provenance & how this was built

This engine was built with heavy AI assistance and it's worth being direct about that, because the
trust case here does **not** rest on who typed the code.

- **AI-assisted development, stated plainly.** Most of the implementation code was written by Claude
  (Anthropic), working under close human direction. The trickiest logic — the JSON-LD parser, the
  co-product allocation handling, the openLCA-library matrix decoder — was AI-authored.
- **Human accountability, stated plainly.** A human (the maintainer) made the method decisions
  (allocation approach, electricity-boundary control, generic-vs-regional CF selection), ran every
  openLCA reference session by hand, directed the debugging, and audited each script against
  [`QC_PROTOCOL.md`](QC_PROTOCOL.md) — a module-by-module read-through, assumption inventory, and
  risk resolution pass. Where AI-generated work was accepted without proportionate review, that is
  tracked openly in the "under-review ledger" of the release plan rather than hidden.
- **The governing principle: VALIDATE, do not FIT.** Discrepancies against openLCA were root-caused,
  never tuned away. The code is general and data-driven — no hardcoded per-dataset constants, no
  special-casing of the four test processes. Two comfortable-sounding explanations for early
  discrepancies (a petroleum "trace-metal vintage" story and an electricity-Vanadium mystery) were
  *disproven* during debugging and are kept on the record in
  [`validation/VALIDATION_LOG.md`](validation/VALIDATION_LOG.md), not quietly deleted.
- **The debugging arc is the real trust artifact.** Petroleum's toxicity categories started at
  **2.0×** openLCA. That gap was traced to the importer applying a multi-output process's *reference*
  product allocation factor to a *co-product* exchange, then to a causal-allocation factor being
  flattened to a mass fraction — two genuine correctness bugs. Fixing them (not fitting them) brought
  the residual to **~1.03**. That last ~3% then survived two *wrong* explanations of its own — both
  written down, both later disproven by evidence, including one that blamed the openLCA reference and
  had to be publicly retracted — before the real cause turned up in this engine: the
  `isAvoidedProduct` sign error. Fixing that took all 40 cells to **1.000**. The full trace lives in
  [`DEVLOG.md`](DEVLOG.md) and the chronological `VALIDATION_LOG.md`.

**Why provenance is orthogonal to correctness here.** The evidence chain is *pinned inputs →
reproducible harness → locked outputs*. Anyone can re-run the harness from [`validation/`](validation/)
against the SHA256-pinned inputs and check the locked result CSVs byte-for-byte. That chain doesn't
depend on who — or what — typed the code. The most intricate AI-authored path — the causal co-product
**consumption** path in `setup/03` — is now covered: the recycled-HDPE-flake case exercises it
(non-uniform MRF-sorting allocation grids) and reproduces openLCA within 0.01% on all 10 categories on
a `--vintage 2026` build (locked in `validation_full_chain_results_2026.csv`).

## Documentation

| File | Purpose |
|------|---------|
| [`DEVLOG.md`](DEVLOG.md) | Engine design decisions and the bugs fixed to reach validation — read before changing any script |
| [`VALIDATION_REPORT.md`](VALIDATION_REPORT.md) | The openLCA parity write-up — method, results, and technical appendix |
| [`validation/`](validation/) | The reproducible parity harness, the locked result CSVs, and `VALIDATION_LOG.md` (the running provenance log — every run, disproven theory, and fix, in order). See [`validation/README.md`](validation/README.md) to replicate |
| [`QC_PROTOCOL.md`](QC_PROTOCOL.md) | The per-script human audit process each script was reviewed against |

---

## Maintainer, citation & reporting issues

- **Maintainer:** Harrison Watson.
- **Report a discrepancy or bug:** open a
  [GitHub issue](https://github.com/hotatejunior/fedefl-build-bw25/issues). Parity results and
  bug reports are especially welcome — the trust case here is built on independent scrutiny.
- **License:** [MIT](LICENSE) — free to use, modify, and redistribute; no warranty.
- **Citing this work:** see [`CITATION.cff`](CITATION.cff) (GitHub renders a "Cite this repository"
  button from it).

## Environment

- **Conda env:** `fedefl-build-bw25` (Python 3.11) — defined in [`environment.yml`](environment.yml).
  Core stack: brightway25 (`bw2data` 4.7, `bw2calc` 2.5), the EPA open-data packages `fedelemflowlist`
  and `lciafmt` (git installs), and `pandas` / `numpy` / `scipy` / `matplotlib` / `seaborn` /
  `openpyxl`. All versions are pinned to the validated combination.
- **Brightway project:** `fedefl-build-bw25`
- **Data assets:** external LCA Commons / openLCA files live in `source_data/` (override with
  `SOURCE_DATA_DIR`). See "Getting started" above.
- **Platform:** macOS

---

## Roadmap

- **Now:** the general pipeline is built, audited, and validated against openLCA — four locked test cases at full parity (40/40 cells within 0.1%), plus the recycled-HDPE-flake case covering causal co-product consumption.
- **Next:** full USLCI database import (~10,000 processes) with fast process selection, replacing per-process downloads.
- **Then:** dynamic LCA — time-resolved, parameterized, scenario-swept impact modeling on top of this engine, run programmatically and faster than the incumbent GUI tools.
