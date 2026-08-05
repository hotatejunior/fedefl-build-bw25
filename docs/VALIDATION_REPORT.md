# Engine parity with openLCA

Nine USLCI processes were run through this pipeline and through openLCA 2.6 on identical inputs.
**All 100 category × process cells agree within 0.1%** — every cell rounds to a brightway/openLCA
ratio of 1.000, with a largest deviation of 0.00077%.

That is a check on the mechanics: the JSON-LD parser, allocation, provider linking, and the LCIA
solve. It says nothing about whether any study built on the engine is well posed. Section
[What this does not cover](#what-this-does-not-cover) is the part to read before citing this.

---

## How the check works

Feed both engines the same data and see if they disagree. Same source files, same method (TRACI 2.2
against the same FEDEFL flow list), same scope (full supply chain, per 1 kg of reference product,
all ten categories). Because the inputs are identical, any difference in the output is the
pipeline's doing rather than a data-vintage artifact, which is why the comparison is against openLCA
rather than against published literature values.

The cases were picked to span allocation styles, since allocation is where an importer is most
likely to be quietly wrong. They run as two builds, because the US electricity baseline renames its
grid node between releases and one build carries one vintage:

| Process | Sector | Allocation | Build |
|---|---|---|---|
| Petroleum refining; at refinery | Petroleum | Physical, 9 products | 2025 |
| Corn; whole plant; at field | Agriculture | Economic (degenerate) | 2025 |
| Portland cement; at plant | Minerals | Single-output | 2025 |
| Steel; billets; at plant | Metals | Foreground-only control | both |
| Chlorine; chlor-alkali electrolysis | Chemicals | Physical, 3 products | 2026 |
| Hardboard; at hardboard plant | Wood products | Physical, 7 products | 2026 |
| Soybean oil; crude, degummed | Agriculture | Physical, 2 products | 2026 |
| Recycled HDPE flake; at plant | Plastics recycling | Causal, consumed co-product | 2026 |
| Recycled PET flake; at plant | Plastics recycling | Causal, consumed co-product | 2026 |

The three physical cases carry more weight than the count suggests: they are the first
non-degenerate allocation grids in the set. Chlorine splits NaOH 0.5453 / Cl₂ 0.4357 / H₂ 0.019,
hardboard seven ways from 0.8634 down to 0.0016, soybean meal 0.8051 / oil 0.1949. Before them the
only real physical grid under test was petroleum's.

Two details worth knowing. `Soybean oil; crude, degummed; at plant` declares **soy meal** as its
quantitative reference rather than the oil it is named for, so both engines report soy meal; that is
USLCI's modelling, not a mix-up. And steel is the deliberate foreground-only control: a single
process with no upstream, so its direct and full-chain scores are identical and it isolates the LCIA
math from the supply-chain solve. A gap in steel would implicate characterization factors, units or
flow mapping; a gap only elsewhere implicates the parser, allocation or the solve. That is how the
petroleum residual was eventually cornered.

## The result

**2025 build** — petroleum, corn, cement, steel:

![brightway / openLCA ratio, 2025 build](../charts/validation/validation_ratio_full_chain.png)

**2026 build** — steel, HDPE flake, PET flake, chlorine, hardboard, soy meal:

![brightway / openLCA ratio, 2026 build](../charts/validation/validation_ratio_full_chain_2026.png)

Each dot is one impact category for one process; the vertical line is perfect agreement and the
green band is ±5%. All 100 dots sit on the line.

| Band | Margin from 1.000 | 2025 (40 cells) | 2026 (60 cells) | Total |
|---|---|---|---|---|
| Strict | ≤ 0.1% | 40 | 60 | **100** |
| Acceptable | ≤ 1% | 0 | 0 | 0 |
| Investigate | ≤ 5% | 0 | 0 | 0 |
| Above tolerance | > 5% | 0 | 0 | 0 |

Before the fix described next, three petroleum toxicity cells sat above ±5%.

## How the last gap closed

Petroleum's ecotoxicity, cancer and non-cancer scores ran high for months, around 1.027 in early
builds and drifting to 1.05 later. The cause, found 2026-07-21, was one importer bug, and fixing it
moved all ten of petroleum's categories to 1.000 at once.

Petroleum's toxicity is roughly 99% grid electricity, so a contribution analysis pointed at
electricity routing. One node carried the entire gap with the wrong sign: openLCA's "MSW landfilling
of mixed MSW" contributes **−0.072** to petroleum's ecotoxicity, a credit, while this pipeline
contributed **+0.072**, a burden. That 0.145 flip is exactly the petroleum ecotoxicity gap, and
identically so for all three toxicity categories.

The landfilling process recovers landfill-gas electricity that displaces grid power, recorded as an
exchange with `isInput=true` **and** `isAvoidedProduct=true`. openLCA subtracts avoided products.
`setup/03` had no handling for the flag and imported the exchange as ordinary consumption, turning a
credit into a burden.

The fix sign-flips avoided-product exchanges, which is a first-principles correctness change rather
than a tweak aimed at the failing cases. Two things support that reading. It moved **all 40** cells
then under test to exact agreement rather than only the three it was chased for, tightening corn and
cement from residuals of 0.1–0.5% as well. And the five cases added afterwards reproduced openLCA on
first run.

This supersedes an earlier diagnosis. A 2026-07-17 investigation had attributed the residual to
openLCA charging a pre-correction crude-oil electricity value; that was wrong, there was no
crude-electricity discrepancy, and openLCA computed correctly throughout. The 2026-07-20
waste-treatment linking fix was itself correct and merely exposed this dormant bug by pulling the
landfilling process into the supply chains for the first time. The superseded diagnosis is kept on
the record rather than deleted, in [`validation/VALIDATION_LOG.md`](../validation/VALIDATION_LOG.md)
and [`DEVLOG.md`](DEVLOG.md), which also carry the earlier arc from 2.0× down to 1.03.

## What this does not cover

**Modelling judgment.** Whether the allocation choices, system boundary, cutoffs and data vintage
suit your study is yours to defend. Parity means the arithmetic is trustworthy.

**Economic allocation**, which cannot be tested against USLCI at all. All 29 processes declaring
`ECONOMIC_ALLOCATION` use factors of exactly 0.0 or 1.0 (census, 2026-07-23): one product absorbs
everything and the co-products get nothing. Corn covers that degenerate path. No process in the
database splits a burden economically, so the economic arithmetic is unverified — not skipped, but
unexercisable with this data.

**Full-chain coverage rests on eight of the nine cases.** Steel has no linked upstream by design.
That is also not extensible: a 2026-07-23 census found USLCI's steel datasets are labelled unit
processes while being strictly foreground, with upstream aggregated into a single elementary-flow
inventory and zero default-provider inputs.

**Foreground/background separation is verified on petroleum only.** Direct-mode reference exports
exist for it alone, and the split is strongly case-specific: petroleum's own emissions are under
0.01% of its full-chain result while cement's reach 76%. A disagreement in cement could not
currently be attributed to foreground versus solve.

**Avoided products are lightly exercised.** The fix above is general, but the cases activate only a
handful of such exchanges, chiefly the landfill-gas credit. One variant, a negative-amount avoided
product, is in no case's active chain.

**Reproducibility is to about 1e-14, not bit-for-bit.** Absolute scores move in roughly the 14th
significant figure depending on which bundles share a database, because the sparse solve sums in a
matrix-dependent order. That is why the replication gate is a tolerance rather than a byte
comparison, and why a `git diff` on a result CSV is not the check that matters.

---

# Appendix

## A. Environment

| Component | Version |
|---|---|
| openLCA (reference tool) | 2.6 |
| brightway `bw2data` / `bw2calc` / `bw2io` | 4.7 / 2.5.0 / 0.9.17 |
| `lciafmt` / `fedelemflowlist` / `olca-schema` | 1.2.0 / 1.3.1 / 2.4.0 |
| Python | 3.11.15, env pinned by `environment.yml` |
| TRACI 2.2 method | file `v1.2.0`, method object `1.4.0`, 10 categories |
| US Electricity Baseline | `v1.2025-06.0` and `v1.2026-06.0`, one per build |
| FEDEFL biosphere | 332,133 flows, keyed by UUID |

Per-file SHA-256 hashes for every input are pinned in
[`validation/README.md`](../validation/README.md). The locked results were computed in a
legacy-named build env, `asp-lca-bw25`, with the same pinned package set.

## B. Characterization-factor parity

For every category, the number of CFs this pipeline loads equals the number of generic (non-located)
factors in openLCA's own TRACI 2.2 source, and every surviving factor matches a biosphere flow with
zero unmatched. FEDEFL-UUID keying is what guarantees the second part: there is no name-matching step
to go wrong.

| Category | openLCA generic CFs | loaded here | Category | openLCA | loaded |
|---|---|---|---|---|---|
| Acidification | 221 | 221 | Human health – cancer | 31,059 | 31,059 |
| Eutrophication (Freshwater) | 128 | 128 | Human health – non-cancer | 23,001 | 23,001 |
| Eutrophication (Marine) | 391 | 391 | Human health – particulate matter | 136 | 136 |
| Freshwater ecotoxicity | 129,120 | 129,120 | Ozone depletion | 1,615 | 1,615 |
| Global warming | 1,530 | 1,530 | Smog formation | 13,498 | 13,498 |

The two eutrophication categories ship spatial variants (25,472 and 82,917 factors). The pipeline
selects the generic variant to match openLCA, reducing them to 128 and 391. Choosing the US-national
variant instead would inflate freshwater ~2.8× and deflate marine ~0.15×; it is a documented toggle
(`--eutro-location`), defaulted to generic.

## C. How each script was audited

Every script was reviewed against a fixed procedure before being trusted. It is recorded here
because the audit is part of the evidence, not separate from it.

1. **Read through, module by module**, describing each chunk in plain English before asking
   questions about it. What comes in, what goes out, what external dependencies are called, where
   fallback logic hides, and whether writes are reversible.
2. **List the assumptions** each chunk makes about its data: schema stability, completeness, value
   ranges, uniqueness, ordering, and geographic semantics. The load-bearing unasserted ones are the
   audit targets.
3. **Rank by impact × likelihood.** High means a silent wrong result affecting scientific output
   with no error raised. Resolve high and medium before moving on, preferring the simplest fix that
   makes the failure loud — a `raise` with a clear message beats a clever recovery path.
4. **Add diagnostics** at each risk point: fail fast with the offending value and a remediation
   hint, assert column names before use, guard result-set sizes, log versions and file hashes, and
   accumulate warnings for a post-loop report rather than dropping them silently.
5. **Hunt the remaining silent failures.** Passthrough fallbacks, dict overwrites that keep one
   value on a key collision, near-zero factors, upstream packages changing their schema, and partial
   success where a script completes but writes something incomplete. Anything not worth fixing is
   written down rather than forgotten.
6. **Run against live data and compare to baselines.** Counts, diagnostic values, and at least one
   LCIA score against the openLCA reference. An unexpected shift in any count is treated as a data
   change event and re-validated before the results are used.

Several guards in the pipeline exist because of step 5 specifically: the unknown-unit hard stop, the
conversion-table hash check, the TRACI CF hash pin, and the ambiguous-provider refusal.

## Reproducing this

The harness, the locked result CSVs, and the SHA-256 asset manifest are all in
[`validation/`](../validation/), which has the full replication guide.

```bash
python examples/full_pipeline.py --setup-only   # build the databases
python validation/05_validate_uslci.py          # ends in REPLICATION GATE: PASS/FAIL
python validation/06_visualize_validation.py    # charts/validation/*.png
```

The gate — every cell within 0.1% — is the check that means something. The openLCA reference exports
it diffs against are hash-pinned in `validation/README.md`; they are not redistributed, and a
replicator regenerates them in openLCA 2.6 by following
[`REGENERATING_REFERENCE_EXPORTS.md`](../validation/REGENERATING_REFERENCE_EXPORTS.md).
