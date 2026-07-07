# Validation package — replicating the openLCA comparison

This directory contains everything needed to replicate the headline claim in
[`VALIDATION_REPORT.md`](../VALIDATION_REPORT.md): **all 40 category × process cells within ±5% of
openLCA (35 of 40 within ±1%) on identical inputs.**

Ported 2026-07-04 from the parent project this engine was extracted from. On the same day, the
harness was re-run **from this repo** and reproduced the locked results **byte-for-byte** (see
"Replication record" below).

## What's here

| File | Role |
|---|---|
| `05_validate_uslci.py` | The harness — runs brightway LCIA for the four locked test cases and diffs against openLCA reference exports |
| `06_visualize_validation.py` | Renders `charts/validation/*.png` from the harness CSVs |
| `validation_full_chain_results.csv` | **Locked** full-chain results (the 40-cell table in the report) |
| `validation_direct_results.csv` | **Locked** direct-mode results (LCIA-math-only layer, petroleum) |
| `VALIDATION_LOG.md` | The running provenance log: validation design, environment pins, asset SHA256s, and the full chronological results record — including the failed runs and disproven theories that preceded the passing state |
| `REGENERATING_REFERENCE_EXPORTS.md` | How to reproduce the openLCA reference exports in your own openLCA 2.6 — the intended path, since the exports are not shipped in the repo |
| `Petroleum_refining__at_refinery___US_kg_basis.xlsx` | openLCA reference export for **direct** mode (kg-basis, "Direct impact contributions" sheet) |

The openLCA reference exports for **full-chain** mode live in `source_data/` at the repo root
(currently untracked / assembled locally — see "Data assets" below).

## How to replicate

Prerequisites: the conda env from [`environment.yml`](../environment.yml), and the one-time setup
chain run on this machine (`setup/00 → 01 → 02 → 03b → 03`) against the pinned assets listed below.

```bash
# 1. Full-chain comparison, all four test cases, all 10 TRACI categories
python validation/05_validate_uslci.py
#    → prints per-category BW / OL / ratio; writes validation/validation_full_chain_results.csv

# 2. Confirm you reproduced the locked results
git diff validation/validation_full_chain_results.csv   # empty diff = byte-for-byte replication

# 3. Regenerate the charts
python validation/06_visualize_validation.py             # → charts/validation/*.png
```

Note that step 1 **overwrites** the locked CSV in place — that is intentional: the `git diff` in
step 2 *is* the replication check. Restore with `git checkout` if you want the locked copy back.

Direct mode (isolates CF/flow-mapping from the system solve) is currently selected by editing
`VALIDATION_MODE` at the top of `05_validate_uslci.py` — a CLI flag is on the roadmap.

## Data assets and pinned hashes

All SHA256s below were verified at port time (2026-07-04) against the pins recorded in
[`VALIDATION_LOG.md`](VALIDATION_LOG.md) where such pins existed; the openLCA reference exports had
no prior pins, so their hashes were **first recorded here at port time** from the files the locked
results were computed against.

> **What the two kinds of hash mean — they are not the same check.**
> - **Inputs** (USLCI bundles, electricity baseline, full USLCI zip): the hash **is** a replication
>   gate. Both engines must ingest these exact bytes, so `shasum -c` against the pins below is a
>   real go/no-go — a mismatch means you are not running the validated comparison.
> - **openLCA reference exports** (the `.xlsx`): the hash pins only the maintainer's **distributed
>   copy**, for download integrity. It is **not** something you can reproduce. openLCA bakes
>   timestamps, row ordering, and formatting into each file, so regenerating these in your own
>   openLCA (the intended path — see [`REGENERATING_REFERENCE_EXPORTS.md`](REGENERATING_REFERENCE_EXPORTS.md))
>   yields a **different hash even when the numbers are identical**. The replication gate for the
>   openLCA side is the **harness agreeing within tolerance**, not a byte match.

### USLCI supply-chain bundles (→ `source_data/`)

| Test case | Bundle | SHA256 |
|---|---|---|
| Petroleum refining; at refinery | `0aaf1e13-…_00a04057….zip` (proc v00.01.014) | `e2a81f9e77fb4336eaf7e09e726dbbaec899834b631fc3a13152572fbc6f78f7` |
| Corn; whole plant; at field | `11256034-…_00a04057….zip` (proc v00.00.019) | `455ff132881e3f30b8c859918de1cebd9057a700b56c7491b5f734b09191ce57` |
| Portland cement; at plant | `62993671-…_00a04057….zip` (proc v00.00.018) | `c53bfd7d35549ffb29aad3a445051f0a5fe9e961690aa19ab6dd17b29e6afcc5` |
| Steel; billets; at plant | `ac54bc7d-…_00a04057….zip` (proc v00.02.020, **1-process bundle** — no upstream, so full-chain ≡ direct for this case) | `b5ee3e935369a7f4c54f290a3eb60c87a8404f7a2503176d7b17fb2e6162d95e` |

### Background / method assets (→ `source_data/`)

| Asset | SHA256 |
|---|---|
| `U.S._electricity_baseline_v1.2025-06.0_from_olca` (canonical — the version openLCA computed against) | `60b92381ce83f576a08cd873cf7fa587cc293421a9204ff49a6d5662d47d1f68` |
| `National_Renewable_Energy_Laboratory-USLCI_Database_Public.zip` (full USLCI; feeds `setup/00`) | `e0ad4ff560fc4ddce7b7b8645a94efb24e4ec342fd593fb243617def70a8281e` |

### openLCA reference exports (hashes first pinned 2026-07-04)

These hashes identify the maintainer's distributed copies only; see the boxed note above — a peer
regenerating in openLCA will not (and is not expected to) reproduce them byte-for-byte.

| Export | Mode | SHA256 |
|---|---|---|
| `Petroleum_refining__at_refinery___US__AVG_ELEC_SELECTION.xlsx` | full-chain | `eacec53aa704f0708732185cb2cb7ec28167c659167b3aa8586146337d0716f2` |
| `Corn__whole_plant__at_field___US_AVG_ELEC_SELECTION.xlsx` | full-chain | `1b48ab0d0289e42575fd49e8499570c2257d8e9f54c166d52b9dc6779c629163` |
| `Portland_cement__at_plant___US__US_AVG_ELEC_SELECTION.xlsx` | full-chain | `dbf0fc7e773db7b35db14e1f40fa1d2ac42d23d2f2a192f128df1a79c43bf8b1` |
| `Steel__billets__at_plant___RNA_results.xlsx` | full-chain | `2c01912bf79b63cea61d1fb38c982c95edd4de7a51932e724f5fe8f0e3506e76` |
| `Petroleum_refining__at_refinery___US_kg_basis.xlsx` (in `validation/`) | direct | `8ac8c13c05a954ee895dd7d84949805b1dd683ac5a055b7d6371d974d6f9c79a` |

## Replication record

| Date | Machine / env | Result |
|---|---|---|
| 2026-07-02 | parent project; conda `asp-lca-bw25` (py 3.11.x, bw2data 4.7, bw2calc 2.5.0) | Original locked run — 40/40 within 5%, 35/40 within 1%, max dev 2.71% (petroleum ecotox/cancer/non-cancer) |
| 2026-07-04 | **this repo**, post-port; conda `asp-lca-bw25` (py 3.11.15, bw2data 4.7, bw2calc 2.5.0, pandas 3.0.3) | `validation_full_chain_results.csv` reproduced **byte-for-byte**; all four `charts/validation/*.png` reproduced byte-for-byte |
| 2026-07-07 | **this repo**, post-rename; conda `asp-lca-bw25` (py 3.11.15, bw2data 4.7, bw2calc 2.5.0, pandas 3.0.3) | brightway project renamed `asphalt-lca` → `fedefl-build-bw25` (`config.py`); full setup chain (`01→02→03b→03`) rebuilt from scratch under the new name, harness re-run → `validation_full_chain_results.csv` reproduced **byte-for-byte** (empty `git diff`). Confirms the project name is non-load-bearing. |

## Known limitations of this package (honest scope)

- The openLCA reference exports (~130 MB of xlsx) are **not committed** to the repo (they are
  git-ignored; see `.gitignore`). The intended replication path is that a peer **regenerates them in
  their own openLCA 2.6** — a step-by-step guide is in
  [`REGENERATING_REFERENCE_EXPORTS.md`](REGENERATING_REFERENCE_EXPORTS.md). The maintainer's copies
  may additionally be attached as a **GitHub Release asset** (a convenience mirror / fast path for
  running the harness without a full openLCA session), but that is optional — the reference numbers
  are meant to be reproduced, not downloaded and trusted. (A Zenodo DOI is reserved for a citable
  release of the whole tool later, not for these data files; Git LFS was rejected — cost + client
  tooling for files nobody needs in the working tree.)
- The steel test case is a 1-process bundle: it exercises LCIA math and biosphere mapping but not
  the technosphere solve. Full-chain coverage rests on petroleum, corn, and cement.
- Direct mode currently covers petroleum only, and mode switching requires editing a constant.
- No process that **consumes** a causal-allocation co-product exists in the current bundle set, so
  that importer path is exercised by zero validation cells (the importer warns loudly if it ever
  triggers). Expanding the test set to cover it is the top validation roadmap item.
