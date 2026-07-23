# Validation package — replicating the openLCA comparison

This directory contains everything needed to replicate the headline claim in
[`VALIDATION_REPORT.md`](../VALIDATION_REPORT.md): **all 100 category × process cells reproduce
openLCA within 0.1% on identical inputs** — every cell rounds to a ratio of 1.000.

The cells span **nine processes across two builds**, because a build injects exactly one
electricity-baseline vintage:

| Build | Cases | Cells | Max deviation |
|---|---|---|---|
| **2025 vintage** (`validation_full_chain_results.csv`) | petroleum, corn, cement, steel | 40 | < 0.0001% |
| **2026 vintage** (`validation_full_chain_results_2026.csv`) | steel, HDPE flake, PET flake, chlorine, hardboard, soy meal | 60 | 0.00077% |

Steel appears in both — it has no grid electricity, so it is vintage-agnostic. (The last gap,
petroleum's toxicity categories, closed 2026-07-21 when `setup/03` was fixed to honor USLCI's
`isAvoidedProduct` flag — see the report.)

Ported 2026-07-04 from the parent project this engine was extracted from. On the same day, the
harness was re-run **from this repo** and reproduced the locked results **byte-for-byte** (see
"Replication record" below).

## What's here

| File | Role |
|---|---|
| `05_validate_uslci.py` | The harness — runs brightway LCIA for the locked test cases and diffs against openLCA reference exports. Mode via `--mode {full_chain,direct}` (default `full_chain`) |
| `06_visualize_validation.py` | Renders `charts/validation/*.png` from the harness CSVs |
| `validation_full_chain_results.csv` | **Locked** full-chain results on a 2025-vintage build (the 40-cell table in the report: petroleum, corn, cement, steel) |
| `validation_full_chain_results_2026.csv` | **Locked** full-chain results on a `--vintage 2026` build (steel, HDPE flake, PET flake, chlorine, hardboard, soy meal — see the vintage note below) |
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
python validation/05_validate_uslci.py          # → per-category BW / OL / ratio, and a PASS/FAIL gate
python validation/06_visualize_validation.py    # → charts/validation/*.png
```

The harness ends with the verdict:

```
========================================================================
REPLICATION GATE: PASS — all 40 cell(s) within ±0.100% of openLCA.
  Largest deviation: 4.32e-07 (Petroleum refining; at refinery / Eutrophication (Marine))
========================================================================
```

**That gate is the replication check** — every cell agreeing with openLCA within 0.1%, the "Strict"
band of the tolerance ladder. Published cells currently sit around 1e-6 or better, four orders of
magnitude inside it.

### You only need the case you care about

You do **not** need the full asset set. Download one bundle from LCA Commons, produce (or fetch) that
one openLCA export, and run the harness: cases without a reference export are skipped by name and the
gate reports on what you *did* check. A single-process run prints something like

```
3 case(s) SKIPPED (no reference export): Corn; whole plant; at field, …
REPLICATION GATE: PASS — all 10 cell(s) within ±0.100% of openLCA.
```

Partial runs write `validation_full_chain_results_partial.csv` (git-ignored) rather than overwriting
the locked table, so checking one process can't clobber the published 40-row artifact.

### What is *not* a replication check: a byte comparison

The locked CSVs are a **published reference table, not a byte gate.** Absolute scores reproduce to
about **1e-14**, and only on an identical bundle set — brightway's sparse solve sums in an order that
depends on which activities are in the matrix, so adding *or removing* bundles moves the last ulp or
two. Measured on petroleum: a petroleum-only build (342 activities) differs from the locked table by
~1e-14; the full 391-activity build by ~1e-15. Neither is disagreement — it is float arithmetic about
twelve orders of magnitude below anything an LCA conclusion rests on.

So `git diff` on the CSV is useful to the maintainer as a regression check on a fixed asset set, and
it is **not** the thing a replicator should judge by. If your diff is non-empty in the 14th
significant figure, you replicated the result.

Direct mode (isolates CF/flow-mapping from the system solve) is a flag, not a source edit:
`python validation/05_validate_uslci.py --mode direct`. It currently covers petroleum only, because
that is the only case with a kg-basis openLCA export.

### Replicating the 2026-vintage cases

A build injects exactly **one** electricity-baseline vintage, so the five 2026 cases (HDPE flake,
PET flake, chlorine, hardboard, soy meal — plus steel, which runs on either) are a separate build,
not extra rows in the run above. Their bundles hardcode the 2026-06 grid UUID:

```bash
python setup/03b_import_electricity_baseline.py --vintage 2026
python setup/03_import_uslci.py
python validation/05_validate_uslci.py     # → validation_full_chain_results_2026.csv
git diff validation/validation_full_chain_results_2026.csv
```

`setup/03b` auto-detects the vintage from the bundles, so `--vintage` is only needed to disambiguate
when `source_data/` holds both generations (it stops and shows the split rather than guessing).

The harness reads the vintage stamp the build wrote and **skips** any case whose expected vintage
doesn't match, writing a vintage-tagged CSV — so a 2026 build cannot silently overwrite or be diffed
against the locked 2025 table. Rebuild with `--vintage 2025` to return to it.
`06_visualize_validation.py` tags its output the same way, writing
`charts/validation/validation_{ratio,pct}_full_chain_2026.png` alongside the 2025 images.

## Data assets and pinned hashes

All SHA256s below were verified at port time (2026-07-04) against the pins recorded in
[`VALIDATION_LOG.md`](VALIDATION_LOG.md) where such pins existed; the openLCA reference exports had
no prior pins, so their hashes were **first recorded here at port time** from the files the locked
results were computed against.

> **What the two kinds of hash mean — they are not the same check.**
> - **Inputs** (USLCI bundles, electricity baseline, full USLCI zip): the hash **is** a replication
>   gate. Both engines must ingest these exact bytes, so `shasum -c` against the pins below is a
>   real go/no-go — a mismatch means you are not running the validated comparison.
> - **openLCA reference exports** (the `.xlsx`): the hash is a **provenance record** — it identifies
>   exactly which files the published results were computed against, so the maintainer can tell later
>   whether a given export is the one behind a locked table. It is **not** a check you can run:
>   openLCA bakes timestamps, row ordering, and formatting into each file, so regenerating these in
>   your own openLCA (the intended path — see
>   [`REGENERATING_REFERENCE_EXPORTS.md`](REGENERATING_REFERENCE_EXPORTS.md)) yields a **different
>   hash even when the numbers are identical**. The gate for the openLCA side is the **harness
>   agreeing within tolerance**, not a byte match.

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

### 2026-vintage assets (→ `source_data/`) — the five-case 2026 build

A build injects exactly **one** electricity-baseline vintage (see "Electricity-baseline vintage" in
the DEVLOG). These assets belong to the `03b --vintage 2026` build, which produces the separate
locked table `validation_full_chain_results_2026.csv`. They are **not** used by, and cannot affect,
the 2025 locked results above. `setup/03b` detects which vintage a bundle needs and defaults to it,
so you do not have to track this by hand — it stops and shows the split if a bundle directory mixes
both.

**Bundles** (→ `source_data/`):

| Test case | Bundle | SHA256 |
|---|---|---|
| Recycled HDPE flake; at plant | `17664c37-…_a900b507….zip` (proc v00.01.029) | `cfb579d9a60034d70fad4a0e6bfb58d1dfcc28e4b428b6442618868769c21bae` |
| Recycled PET flake; at plant | `f7b7280d-…_a900b507….zip` (proc v00.01.025) | `5eaac06844c8e134bd43d588ad45dcf8f2a7faa3caad5261776502666a525051` |
| Chlorine; chlor-alkali electrolysis | `a3e150d0-…_a900b507….zip` (proc v00.01.022) | `7491b15382589dff4bb6a4c1cf80b8a438d0c84230d0a687f7e914d1ff5ae34e` |
| Hardboard; at hardboard plant | `ca1d1dfa-…_a900b507….zip` (proc v00.01.012) | `b229a1832328e63c18330661aea7f138859c5b0ddec9baa39131697b31d27593` |
| Soybean oil; crude, degummed | `88aee762-…_a900b507….zip` (proc v00.00.015) | `0c6a97d9446766ca7d4dc30fea997fb8a031c393629020f802fcd88dd936f7dd` |

**Background + openLCA reference exports** (→ `source_data/`; export hashes pin the maintainer's
a provenance record of the files behind the published results, not a download check — see the boxed note above):

| Asset | SHA256 |
|---|---|
| `U.S._electricity_baseline_v1.2026-06.0.zip` (also pinned in `setup/03b`) | `fb545416220e6b3739496661f623081f6fd96de4b1c6508dd6353d88c2b33143` |
| `Recycled_postconsumer_high_density_polyethylene__HDPE__flake__at_plant___RNA_July_20.xlsx` | `d3d65b1f8986bc8354071a756b7c6036c9573116caf91adf122a7cd6dd0aaa5b` |
| `Recycled_postconsumer_polyethylene_terephthalate__PET__flake__at_plant___RNA.xlsx` | `e101e5f883709eb190f59544733208da14792c3669bbf968dbe9f29d295811c0` |
| `Chlorine__chlor_alkali_electrolysis__at_plant___US.xlsx` | `c031f56568ef279d2bee6b0a1f03b55541b65176d2d97bee176f21c8c21b5c35` |
| `Hardboard__at_hardboard_plant___RNA.xlsx` | `72622e49c9719e10377940d4e90a9e8fb42c3bd49b7434f9c1420ebd5de6426c` |
| `Soybean_oil__crude__degummed__at_plant___RNA.xlsx` | `be681554eb0f50cb92d144e73761e85301f79fed33c799827c168679933e92e1` |

> **The soybean case reports soy meal, not oil.** The process `Soybean oil; crude, degummed; at
> plant` declares **`Soy meal; at plant` (4131 kg) as its quantitative reference**, with the oil as
> the co-product (physical split: meal 0.8051 / oil 0.1949). Both engines therefore report soy meal,
> and the openLCA export correctly names it as the Product. This is USLCI's modelling, not a setup
> error — do not "fix" it.

### openLCA reference exports (hashes first pinned 2026-07-04)

These hashes record which files the published results were computed against; see the boxed note
above — a peer regenerating in openLCA will not (and is not expected to) reproduce them
byte-for-byte.

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
| 2026-07-23 | **this repo**, `--vintage 2026` build | **Five new cases validated** from a fresh openLCA operator session: recycled-PET flake, chlorine (chlor-alkali), hardboard, soybean oil — joining HDPE flake and steel. **60/60 cells within 0.001%** (max 0.00077%). First non-degenerate allocation grids in the test set: chlorine's 3-way physical split (NaOH .5453 / Cl₂ .4357 / H₂ .019), hardboard's 7 co-products, soy's .8051/.1949. Locked in `validation_full_chain_results_2026.csv`; the 2025 table was untouched (vintage guard skipped its three grid-dependent cases). |
| 2026-07-21 | **this repo**; branch `release-prep-phase1-2` | **Re-locked** after the `isAvoidedProduct` fix in `setup/03` (byproduct energy-recovery credits — chiefly landfill-gas electricity — were being imported as burdens). Closed the last petroleum residual: **all 40 cells now within 0.1% of openLCA** (every cell rounds to 1.000); corn and cement tightened too. Supersedes the earlier "crude-electricity / reference-completeness" reading of the petroleum gap. All 41 pytest checks pass. |

## Known limitations of this package (honest scope)

- The openLCA reference exports (~290 MB of xlsx) are **not distributed** — not committed (they are
  git-ignored) and not attached to releases. Checking parity means generating the openLCA side in
  your own openLCA 2.6; [`REGENERATING_REFERENCE_EXPORTS.md`](REGENERATING_REFERENCE_EXPORTS.md) is
  the step-by-step guide. (A Zenodo DOI is reserved for a citable release of the whole tool later,
  not for these data files.)
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
- **Absolute scores are reproducible to ~1e-14, not bit-for-bit.** The figure depends on which
  bundles share the build (see "What is *not* a replication check" above). The parity claim is the
  ±0.1% gate, which the harness enforces and prints; the locked CSVs are a reference table.
- **Direct mode has one explicit case (petroleum), but the LCIA-math check it provides covers two
  processes.** Steel has no linked upstream, so its direct and full-chain scores are bit-identical
  and its standard export already validates flow mapping, CFs, and unit conversion on a second,
  independent set of 92 elementary flows. What petroleum alone still covers is the other purpose of
  direct mode — separating a process's *own* emissions from its background. That split is
  case-specific (petroleum's direct share is <0.01% of its full-chain result; cement's reaches 76%),
  so a cement or corn direct export would be needed to attribute a hypothetical disagreement there
  to foreground vs. solve. (Mode switching itself is no longer a source edit:
  `--mode {full_chain,direct}`.)
- The causal-allocation co-product **consumption** path is covered twice over: the
  recycled-HDPE-flake (`17664c37…`) and recycled-PET-flake (`f7b7280d…`) cases both consume causal
  co-products from the MRF-sorting processes and reproduce openLCA within 0.001% on all 10
  categories, locked in `validation_full_chain_results_2026.csv`.
