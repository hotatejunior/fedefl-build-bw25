# Validation package — replicating the openLCA comparison

This directory contains everything needed to replicate the headline claim in
[`VALIDATION_REPORT.md`](../VALIDATION_REPORT.md): **all 40 category × process cells reproduce openLCA
within 0.1% on identical inputs** — every cell rounds to a ratio of 1.000. (The last gap, petroleum's
toxicity categories, closed 2026-07-21 when `setup/03` was fixed to honor USLCI's `isAvoidedProduct`
flag — see the report.)

Ported 2026-07-04 from the parent project this engine was extracted from. On the same day, the
harness was re-run **from this repo** and reproduced the locked results **byte-for-byte** (see
"Replication record" below).

## What's here

| File | Role |
|---|---|
| `05_validate_uslci.py` | The harness — runs brightway LCIA for the locked test cases and diffs against openLCA reference exports. Mode via `--mode {full_chain,direct}` (default `full_chain`) |
| `06_visualize_validation.py` | Renders `charts/validation/*.png` from the harness CSVs |
| `validation_full_chain_results.csv` | **Locked** full-chain results on a 2025-vintage build (the 40-cell table in the report: petroleum, corn, cement, steel) |
| `validation_full_chain_results_2026.csv` | **Locked** full-chain results on a `--vintage 2026` build (steel + recycled-HDPE flake — see the vintage note below) |
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
# 1. Full-chain comparison, all four locked test cases, all 10 TRACI categories
python validation/05_validate_uslci.py
#    → prints per-category BW / OL / ratio; writes validation/validation_full_chain_results.csv

# 2. Confirm you reproduced the locked results
git diff validation/validation_full_chain_results.csv   # empty diff = byte-for-byte replication

# 3. Regenerate the charts
python validation/06_visualize_validation.py             # → charts/validation/*.png
```

Note that step 1 **overwrites** the locked CSV in place — that is intentional: the `git diff` in
step 2 *is* the replication check. Restore with `git checkout` if you want the locked copy back.

Direct mode (isolates CF/flow-mapping from the system solve) is a flag, not a source edit:
`python validation/05_validate_uslci.py --mode direct`. It currently covers petroleum only, because
that is the only case with a kg-basis openLCA export.

### Replicating the HDPE case (2026 vintage)

A build injects exactly **one** electricity-baseline vintage, so the HDPE case is a separate build,
not an extra row in the run above. Its bundle hardcodes the 2026-06 grid UUID:

```bash
python setup/03b_import_electricity_baseline.py --vintage 2026
python setup/03_import_uslci.py
python validation/05_validate_uslci.py     # → validation_full_chain_results_2026.csv
git diff validation/validation_full_chain_results_2026.csv
```

The harness reads the vintage stamp the build wrote and **skips** any case whose expected vintage
doesn't match, writing a vintage-tagged CSV — so a 2026 build cannot silently overwrite or be diffed
against the locked 2025 table. Rebuild with `--vintage 2025` (the default) to return to it.

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
| Steel; billets; at plant | `ac54bc7d-…_00a04057….zip` (proc v00.02.020, **1-process bundle** — the foreground-only Layer 1 control, so full-chain ≡ direct for this case) | `b5ee3e935369a7f4c54f290a3eb60c87a8404f7a2503176d7b17fb2e6162d95e` |

### Background / method assets (→ `source_data/`)

| Asset | SHA256 |
|---|---|
| `U.S._electricity_baseline_v1.2025-06.0_from_olca` (canonical — the version openLCA computed against) | `60b92381ce83f576a08cd873cf7fa587cc293421a9204ff49a6d5662d47d1f68` |
| `National_Renewable_Energy_Laboratory-USLCI_Database_Public.zip` (full USLCI; feeds `setup/00`) | `e0ad4ff560fc4ddce7b7b8645a94efb24e4ec342fd593fb243617def70a8281e` |

> **Two different pins exist for the 2025 baseline, and that is expected.** The table above pins the
> *hand-placed* copy exported from openLCA (`…_from_olca`, extensionless zip). `setup/03b` separately
> pins the copy it **fetches** from the FLCAC GitHub
> (`U.S._electricity_baseline_v1.2025-06.0.zip`, `9fec32a8…`) — same library version, different byte
> stream (different packaging of the same content). `03b` prefers a hand-placed copy when present and
> verifies whichever it uses against the matching pin; the two hashes are not meant to agree.

### 2026-vintage assets (→ `source_data/`) — for the HDPE case only

A build injects exactly **one** electricity-baseline vintage (see "Electricity-baseline vintage" in
the DEVLOG). These assets belong to the `03b --vintage 2026` build, which produces the separate
locked table `validation_full_chain_results_2026.csv`. They are **not** used by, and cannot affect,
the 2025 locked results above.

| Asset | Role | SHA256 |
|---|---|---|
| `17664c37-…_a900b507….zip` | Recycled-HDPE-flake bundle — the causal co-product consumption case | `cfb579d9a60034d70fad4a0e6bfb58d1dfcc28e4b428b6442618868769c21bae` |
| `U.S._electricity_baseline_v1.2026-06.0.zip` | 2026-06 baseline library (also pinned in `setup/03b`) | `fb545416220e6b3739496661f623081f6fd96de4b1c6508dd6353d88c2b33143` |
| `Recycled_postconsumer_high_density_polyethylene__HDPE__flake__at_plant___RNA_July_20.xlsx` | openLCA reference export (full-chain) — distributed-copy pin only, see the boxed note above | `d3d65b1f8986bc8354071a756b7c6036c9573116caf91adf122a7cd6dd0aaa5b` |
| `f7b7280d-…_a900b507….zip` | Recycled-PET-flake bundle — builds and solves, but **no openLCA export exists yet**, so it is not a validation case | `5eaac06844c8e134bd43d588ad45dcf8f2a7faa3caad5261776502666a525051` |

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
| 2026-07-21 | **this repo**; branch `release-prep-phase1-2` | **Re-locked** after the `isAvoidedProduct` fix in `setup/03` (byproduct energy-recovery credits — chiefly landfill-gas electricity — were being imported as burdens). Closed the last petroleum residual: **all 40 cells now within 0.1% of openLCA** (every cell rounds to 1.000); corn and cement tightened too. Supersedes the earlier "crude-electricity / reference-completeness" reading of the petroleum gap. All 41 pytest checks pass. |

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
- The steel test case is a 1-process bundle, and that is deliberate: it is the **foreground-only
  (Layer 1) control**, the same aggregated inventory characterized by both engines, so that a
  discrepancy can be localized to the foreground (CFs, units, flow mapping) versus the background
  (parser, allocation, linking) versus both. It validates at 1.000 on all ten categories, so the
  foreground layer is clean. The corollary is that steel exercises **none** of the technosphere
  solve — full-chain coverage rests on petroleum, corn, and cement (plus HDPE flake on the 2026
  build). Steel cannot be upgraded into a full-chain case either: a 2026-07-23 census found USLCI's
  steel datasets are labelled unit processes but are strictly foreground, with the whole upstream
  aggregated into one elementary-flow inventory (~740 exchanges, ~690 elementary flows, zero
  default-provider inputs), so it stays in the role it was chosen for.
- **Economic allocation is covered only in its degenerate form, and cannot be covered otherwise with
  USLCI data.** All 29 processes in USLCI that declare `ECONOMIC_ALLOCATION` have factors of exactly
  0.0 or 1.0 — one product takes 100% of the burden and the co-products take none. No process in the
  database splits a burden economically, so the corn case (factors `[0.0, 1.0]`) already covers
  everything USLCI can exercise. Physical and causal allocation, by contrast, are tested against real
  splits (petroleum's 9-product physical grid; the HDPE case's causal grids).
- Direct mode currently covers petroleum only — it is the only case with a kg-basis openLCA export.
  (Mode switching itself is no longer a source edit: `--mode {full_chain,direct}`.)
- The causal-allocation co-product **consumption** path is now covered: the recycled-HDPE-flake case
  (`17664c37…`, a `--vintage 2026` build) consumes causal co-products from the MRF-sorting processes
  and reproduces openLCA within 0.01% on all 10 categories, locked in
  `validation_full_chain_results_2026.csv`. (The recycled-PET-flake case builds and solves too, but has
  no openLCA reference export yet, so it is not validated.)
