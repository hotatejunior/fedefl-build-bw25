# Validation Log — brightway vs openLCA (apples-to-apples)

Running provenance + results log for the multi-layer validation of the brightway pipeline
against openLCA. Append as details are pinned down; nothing here is auto-generated yet —
the harness will later emit a machine-readable `validation_manifest.json` from the same facts.

> **What this file is, and what it is not (2026-08-06).** It was kept strictly unedited until
> 2026-08-06, when it was condensed for publication: resolved openLCA-side operational blockers, a
> superseded build plan, asset checklists and the long-form per-session narrative were removed, and
> the chronology became the table below. **No date, finding, wrong turn or retraction was dropped.**
> The complete unedited original is in git history —
> `git log --follow -p -- validation/VALIDATION_LOG.md` — so the primary record still exists; this
> file is now a readable index to it.
>
> Names here are those in use at the time of writing: the brightway project and conda env were
> `asphalt-lca` before the 2026-07-07 rename to `fedefl-build-bw25`, local asset paths are the parent
> project's, and entries cite `RELEASE_PLAN.md`, which was later split into `docs/ROADMAP.md` and a
> record inside `docs/DEVLOG.md`. Current identifiers live in `config.py`, `environment.yml` and
> `validation/README.md`.

## Validation design (agreed)

**Goal:** validate the *pipeline* (parser + allocation + LCIA + solve), not USLCI data provenance.
Achieved by feeding **identical full-chain JSON-LD** to both engines, so any difference is
attributable to the pipeline alone — not to USLCI version drift between per-process downloads
and openLCA's full database.

**Layers:**
- **Layer 1 — LCIA math.** Same aggregated inventory vector → both engines → characterize.
  Difference = genuine CF/unit/mapping bug. Target: ≤0.1%.
- **Layer 2 — LCI / system solve.** Compare computed inventory vectors flow-by-flow, same JSON.
  Isolates parser + allocation. Allocation parity is the prime suspect for any residual.
- **Layer 3 — end-to-end scores.** Composition of L1 + L2.

**Tolerance ladder (applied at the layer where inputs are identical):**

| Band | Margin |
|------|--------|
| Strict | ≤ 0.1% |
| Acceptable | ≤ 1% |
| Investigate / justify | ≤ 5% |
| Failure | > 5% |

**Critical control (DB scope):** openLCA result Excels for full-chain must be computed in a *fresh
openLCA database containing only the imported JSON-LD* — not the existing full-USLCI project — or
the comparison is re-confounded. (Unit-process *direct* results are DB-independent and clean either way.)

**Critical control (electricity baseline boundary):** the per-process bundles are not
self-contained for electricity. The petroleum bundle carries 7 electricity processes inline while 11
providers point outside it, all US Electricity Baseline consumption mixes. If one engine resolves
those and the other cuts them off, the system boundaries differ and the comparison is confounded.

The original decision here was to run **both** engines with the baseline unmounted, leaving those 11
mixes as cutoffs. That was **superseded**: the locked cases are computed with the 2025 baseline
mounted in both engines, which is why `setup/03b` exists and why a build carries exactly one vintage.
The requirement that survived is the one that mattered — identical boundaries on both sides, never
openLCA with the baseline against brightway without it.

## Environment versions (pinned)

| Component | Version |
|-----------|---------|
| openLCA (application) | 2.6 |
| brightway `bw2data` | 4.7 |
| brightway `bw2calc` | 2.5.0 |
| `bw2io` | 0.9.17 |
| `lciafmt` | 1.2.0 |
| `fedelemflowlist` | 1.3.1 |
| `olca-schema` | 2.4.0 |
| `pandas` | 3.0.3 |
| `openpyxl` | 3.1.5 |
| Python | 3.11 (conda env `asphalt-lca`) |
| brightway project | `asphalt-lca` |
| USLCI release label | per-process bundles individually versioned (see asset table); full zip hash below |
| TRACI 2.2 method JSON | file `v1.2.0`; method object version `1.4.0`; `@id 52ce6d64-4e91-347e-8c0b-4b616b2c0339`; 10 categories |
| US Electricity Baseline | **`v1.2025-06.0`** (`_from_olca`, openLCA library/matrix package) — the version openLCA actually computed the results against. (The `v1.2026-06.0` download is set aside; wrong version, would re-introduce UUID drift.) |

**CURRENT asset location:** `/Users/harrisonwatson/Desktop/code/openlca_resources/` (as of 2026-06-30).
The zips inside `asphalt-dynamic-lca/` are **STALE** — do not use. (e.g. petroleum stale v00.01.012 →
current v00.01.014.)

## Confirmed parity findings (from `Petroleum_refining__at_refinery___US_kg_basis.xlsx`)

- **Flow UUID alignment:** 3,736 / 3,946 openLCA output flows are byte-for-byte in brightway
  `biosphere-fedefl`. The 210 misses are exactly openLCA's own "non-FEDEFL" + "CUTOFF" tags —
  nothing FEDEFL-mappable is dropped.
- **CF parity (GWP, Acidification):** openLCA's effective CFs (backed out from "Impact
  contributions by flow") match brightway's TRACI 2.2 table flow-by-flow, including across the
  spatial context hierarchy (troposphere/urban, troposphere/rural carry the same CF as
  emission/air). CO₂=1, CH₄=25, N₂O=298, HCFC-22=1810; SO₂=1, NOₓ=0.7, NH₃=1.88.
  → openLCA is **not** on a different TRACI 2.2. (Full 10-category CF diff pending method JSON.)
- brightway CF counts: GWP=1530, Acidification=221.

## Bundle provenance (SHA256 + target identity) — CURRENT (openlca_resources/, 2026-06-30)

`shasum -a 256 <file>` on macOS. Per-process bundles carry 340–341 versioned processes; target row
is the reference process only — full per-process version manifest to be auto-extracted by the harness.

### Per-process full-chain bundles (test cases)

| Target process | Bundle UUID | Proc ver | lastChange | #proc | SHA256 |
|---|---|---|---|---|---|
| Petroleum refining; at refinery | `0aaf1e13-5d80-37f9-b7bb-81a6b8965c71` | 00.01.014 | 2026-06-24 | 340 | `e2a81f9e77fb4336eaf7e09e726dbbaec899834b631fc3a13152572fbc6f78f7` |
| Corn; whole plant; at field | `11256034-2355-3add-ade9-59983025dded` | 00.00.019 | 2026-06-24 | 341 | `455ff132881e3f30b8c859918de1cebd9057a700b56c7491b5f734b09191ce57` |
| Portland cement; at plant | `62993671-574c-3fc5-b66a-6be3bb21ad3d` | 00.00.018 | 2026-06-24 | 340 | `c53bfd7d35549ffb29aad3a445051f0a5fe9e961690aa19ab6dd17b29e6afcc5` |
| Steel; billets; at plant ⚠ | `ac54bc7d-5db5-3b4f-9175-5dd02f678312` | 00.02.020 | 2026-03-10 | **1** | `b5ee3e935369a7f4c54f290a3eb60c87a8404f7a2503176d7b17fb2e6162d95e` |

⚠ **Steel billets bundle has only 1 process — no upstream supply chain.** Full-chain (Layer 2/3)
not possible from this bundle; **direct/Layer-1 only** unless re-downloaded as a full-chain bundle
like the others. Also dated 2026-03-10 (older than the other three, re-pulled 2026-06-24).

NOTE: only **4** test cases present (petroleum, corn, cement, steel). Earlier plan referenced 5 —
confirm whether a 5th is coming or the lineup is these 4. (Prior natural-gas/diesel combustion
bundles from the stale set are not in the current drop.)

### Reference / background artifacts (CURRENT)

| Artifact | Note | SHA256 |
|---|---|---|
| `National_Renewable_Energy_Laboratory-USLCI_Database_Public.zip` | Full USLCI DB (feeds `00` conversion table). Hash **unchanged** from stale set → conversion table still valid. | `e0ad4ff560fc4ddce7b7b8645a94efb24e4ec342fd593fb243617def70a8281e` |
| `TRACI_2.2_json_v1.2.0.zip` | TRACI method JSON; method obj v1.4.0; `@id 52ce6d64-…`; 10 categories; incl. spatial factors (ecotox 129,120) | `ef7ba98524c48300ebea1505f6d478e9d84ce1f0dd5923246187b1f0a741c9bf` |
| `U.S._electricity_baseline_v1.2025-06.0_from_olca` | **CANONICAL baseline** — openLCA library (matrix); A 771×771, B 14818×771; the version the result exports were computed against. | `60b92381ce83f576a08cd873cf7fa587cc293421a9204ff49a6d5662d47d1f68` |
| `U.S._electricity_baseline_v1.2026-06.0.zip` | SET ASIDE (wrong version) — library (matrix), A 947×947, regionalized 133683 flows. NOT used. | `fb545416220e6b3739496661f623081f6fd96de4b1c6508dd6353d88c2b33143` |

## Governing principle — VALIDATE, do not FIT (operator directive)

The 4 test cases (petroleum, corn, cement, steel) are the locked validation set. The code must be
**general and data-driven**, never tuned to make these 4 agree:
- No hardcoded per-dataset UUIDs/constants; no special-casing the 4 processes.
- Harness is a neutral observer — reads BW output + OL output, diffs them, knows no expected answers.
- Discrepancy → **diagnose root cause; do not tune to close it.** A change lands only if it's a
  first-principles correctness fix that applies to any dataset.
- openLCA numbers are used during the build only to verify the **library decoder reads the matrix
  correctly**, and even then via internal-consistency checks (M = B·A⁻¹, index round-trip,
  meta.zip cross-check) — not circular fitting.
- New code is isolated (standalone library-importer module); audited `00`–`05` stay untouched except
  one **general** linking rule (resolve technosphere by flow when hinted provider absent).

## Results log

Thirteen sessions, 2026-07-01 to 2026-07-23, from a first run that disagreed with openLCA by up to
3.9× to all 100 cells within 0.1%. Every wrong turn is listed, including the one that was published
as a root cause and later retracted. The full long-form entries, with per-category tables, are in
git history: `git log --follow -p -- validation/VALIDATION_LOG.md`.

| Date | What happened | Outcome |
|---|---|---|
| 07-01 | First full-chain run, 4 cases. A technosphere-linking collision was found first (last-write-wins ignored each exchange's `defaultProvider`) and fixed. Post-fix ratios still bad: petroleum cancer 2.025, non-cancer 2.125, ecotox 2.109; cement ecotox 3.220; eutrophication freshwater up to 3.941, marine down to 0.147 | Baseline established. Nothing near tolerance |
| 07-01 | The "electricity/Vanadium" theory investigated and **disproven** — it was a setup artifact, not a pipeline bug. Chasing it surfaced the real one: a co-product exchange was built on the *reference* product's allocation factor (0.219) instead of its own (0.052) | Wrong theory recorded, real bug found |
| 07-02 | Co-product allocation fix applied | 8/10 categories validate on all 4 cases |
| 07-02 | Eutrophication traced to spatial CF variants being summed. Selecting the generic (non-located) factor matches openLCA | corn, cement, steel 10/10 |
| 07-02 | `CAUSAL_ALLOCATION` was being flattened to a mass fraction, over-attributing a forest-residue feedstock by 1.83×. Fixed by honouring each exchange's own factor | All 40 cells within 5%. Petroleum toxicity still ~1.03 |
| 07-15 | Petroleum residual localized to electricity over-draw rather than the earlier trace-metal-vintage theory (**also disproven**) | Narrowed, not closed |
| 07-17 | Residual attributed to a stale openLCA reference export, "engine correct". Ledger #2 closed on that basis | **This diagnosis was wrong. See 07-20 and 07-21** |
| 07-20 | The exit criterion was tested rather than assumed: a fresh July re-export proved **numerically identical** to the supposedly stale one | 07-17 diagnosis **retracted**. Ledger #2 reopened |
| 07-20 | Waste-treatment `WASTE_FLOW` outputs were being dropped instead of linked to their treatment provider. Restoring them pulled landfilling into the supply chains for the first time. Also: `Mg` (tonne) was being read as `mg` (milligram), a 10⁹ error; vintage selector added | Correct fix, and it exposed a dormant bug |
| 07-21 | **Root cause found.** USLCI marks byproduct recovery `isInput=true` + `isAvoidedProduct=true`; openLCA subtracts these as credits, the importer added them as burdens. One landfill-gas electricity exchange carried the entire petroleum gap, sign-flipped | All 40 cells within 0.1%. Ledgers #1 and #2 closed |
| 07-23 | Allocation census over all USLCI processes: every process declaring `ECONOMIC_ALLOCATION` uses factors of exactly 0.0 or 1.0, so economic arithmetic is untestable against this data. USLCI's steel datasets are strictly foreground | Two scope limits established from data, not assumption |
| 07-23 | Five cases added — chlorine (3-way), hardboard (7-way), soy (2-way), PET flake, HDPE flake. First non-degenerate allocation grids in the set | All validated **on first run**, after every fix above had shipped |
| 07-23 | Direct-mode coverage re-scoped: steel's direct and full-chain scores are bit-identical, so it doubles as a second independent LCIA-math check | Coverage claim corrected downward |

The last row of that table is the load-bearing one. Five cases added *after* every fix had already
landed reproduced openLCA immediately, which is what distinguishes a fix made on first principles
from one fitted to the cases that motivated it.

