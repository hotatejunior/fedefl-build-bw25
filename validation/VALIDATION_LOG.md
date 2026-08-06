# Validation Log — brightway vs openLCA (apples-to-apples)

Running provenance + results log for the multi-layer validation of the brightway pipeline
against openLCA. Append as details are pinned down; nothing here is auto-generated yet —
the harness will later emit a machine-readable `validation_manifest.json` from the same facts.

> **Provenance note (added 2026-07-07).** This is a verbatim historical record, kept unedited so the
> chronology of what was tried, disproven, and fixed stays auditable. It therefore names artifacts by
> the identifiers in use *at the time of writing* — notably the brightway project / conda env
> `asphalt-lca` (renamed to `fedefl-build-bw25` on 2026-07-07; see `config.py`) and the parent
> project's local asset paths (`/Users/harrisonwatson/…/openlca_resources/`). Those names are
> historical; they are not the current repo's identifiers. Do not "correct" them here — the current
> names live in `config.py`, `environment.yml`, and `validation/README.md`.
>
> **Addendum (2026-08-06) — this file was condensed.** Until now it was kept strictly unedited. On
> 2026-08-06 the resolved openLCA-side operational blockers, the superseded build plan and the
> asset checklists were removed, and the chronology was tightened, to make it readable ahead of
> publishing the repo. **No dated entry, wrong turn, retraction or hash was dropped.** The complete
> unedited original remains in git history and is recoverable with
> `git log --follow -p -- validation/VALIDATION_LOG.md`, so the primary record is intact even though
> this file is no longer it.
>
> **Addendum (2026-08-05).** The same applies to filenames. Entries below cite `RELEASE_PLAN.md`,
> which was split on 2026-08-05 into `docs/ROADMAP.md` (open work) and a verbatim record inside
> `docs/DEVLOG.md`; the cited sections are in the latter. The methodology and how-to documents moved
> from the repo root into `docs/` on the same day.

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

_Per-process, per-layer results appended here as the harness runs._

### 2026-07-01 — first full-chain run, all 4 test cases, electricity baseline included

First pass (superseded below): found a technosphere-linking collision bug (bundle-internal flows
with multiple candidate producers were resolved last-write-wins, ignoring each exchange's own
`defaultProvider` hint — see DEVLOG "Bug 4"). Numbers below are POST-fix.

Per-category harness output from `05_validate_uslci.py` (`VALIDATION_MODE = "full_chain"`),
per 1 kg reference product. Band = tolerance ladder band on |BW/OL − 1|. Full per-category CSV:
`validation_full_chain_results.csv`. Interpretation and root-cause notes: DEVLOG.md, "First
4-process full-chain validation run" and "Chasing the petroleum overshoot" (both 2026-07-01).

| Process | Category | BW/OL | Band |
|---------|----------|-------|------|
| Petroleum | Global warming | 1.011 | Investigate |
| Petroleum | Acidification | 1.094 | Failure |
| Petroleum | Human health – particulate matter | 1.073 | Failure |
| Petroleum | Smog formation | 1.062 | Failure |
| Petroleum | Ozone depletion | 1.258 | Failure |
| Petroleum | Human health – cancer | 2.025 | Failure |
| Petroleum | Human health – non-cancer | 2.125 | Failure |
| Petroleum | Freshwater ecotoxicity | 2.109 | Failure |
| Petroleum | Eutrophication (Freshwater) | 3.941 | Failure |
| Petroleum | Eutrophication (Marine) | 0.151 | Failure |
| Corn | Global warming | 0.983 | Investigate |
| Corn | Acidification | 0.999 | Acceptable |
| Corn | Human health – particulate matter | 0.998 | Acceptable |
| Corn | Smog formation | 0.998 | Acceptable |
| Corn | Ozone depletion | 1.079 | Failure |
| Corn | Human health – cancer | 1.030 | Investigate |
| Corn | Human health – non-cancer | 1.037 | Investigate |
| Corn | Freshwater ecotoxicity | 1.003 | Acceptable |
| Corn | Eutrophication (Freshwater) | 2.774 | Failure |
| Corn | Eutrophication (Marine) | 0.175 | Failure |
| Cement | Global warming | 1.010 | Investigate |
| Cement | Acidification | 1.006 | Acceptable |
| Cement | Human health – particulate matter | 1.010 | Investigate |
| Cement | Smog formation | 1.026 | Investigate |
| Cement | Ozone depletion | 1.206 | Failure |
| Cement | Human health – cancer | 1.169 | Failure |
| Cement | Human health – non-cancer | 1.760 | Failure |
| Cement | Freshwater ecotoxicity | 3.220 | Failure |
| Cement | Eutrophication (Freshwater) | 3.402 | Failure |
| Cement | Eutrophication (Marine) | 0.147 | Failure |
| Steel | Global warming | 1.000 | Strict |
| Steel | Acidification | 1.000 | Strict |
| Steel | Human health – particulate matter | 1.000 | Strict |
| Steel | Smog formation | 1.000 | Strict |
| Steel | Ozone depletion | 1.000 | Strict |
| Steel | Human health – cancer | 1.000 | Strict |
| Steel | Human health – non-cancer | 1.000 | Strict |
| Steel | Freshwater ecotoxicity | 1.000 | Strict |
| Steel | Eutrophication (Freshwater) | 2.775 | Failure |
| Steel | Eutrophication (Marine) | 0.379 | Failure |

**Confirmed fix (Bug 4):** bundle-internal technosphere linking ignored each exchange's own
`defaultProvider` hint, resolving 7 multi-producer flows (crude oil sourcing, natural gas
extraction method, marine transport mode/route — 69 consuming exchanges) via a naive last-write-
wins dict instead. All 69 hints correctly named a real candidate; fixing this improved petroleum's
mainstream categories substantially (GWP 1.080→1.011, PM 1.110→1.073, Smog 1.154→1.062).

**Open thread (unresolved):** the injected electricity baseline's Vanadium contribution is, by
itself, larger than openLCA's *entire* reference ecotoxicity total for cement (both engines agree
Vanadium-via-electricity dominates; magnitudes disagree by ~3.7x). Ruled out: CF inflation (the
Problem #1 pattern), M-column decode error, and gross unit/scaling error (the same electricity
activity's GWP works out to a realistic 0.469 kg CO2eq/kWh). Root cause not yet found — see DEVLOG
"Chasing the petroleum overshoot" for the full ruled-out list. Likely needs openLCA's own upstream
contribution breakdown for the electricity sub-chain to compare against directly.

Eutrophication (both directions, all 4 processes) and petroleum's ecotox/cancer/non-cancer gap
remain consistent with previously-documented, already-explained findings (FEDEFL nitrogen/
phosphorus sparsity; USLCI-version trace-metal gap) — not new, not re-investigated this pass.

### 2026-07-01 — electricity/Vanadium open thread resolved: co-product allocation bug found

The "open thread" above is closed out (root cause found, fix not yet applied). Using openLCA's
"Total upstream impacts"/"Total upstream inventories" sheets directly (per-process columns, UUID in
row 1) as an independent check:

- Electricity node `7068192a` is a single, unambiguous column; its per-unit Vanadium content is
  guaranteed identical to our decoded library column (same library file, same 2025 UUID — the export
  does not use the 2026-renamed UUID). Rules out "wrong regional mix" and "wrong library version."
- "Petroleum refining" appears as **5 separate columns** under the same UUID — openLCA decomposes a
  multi-output process into one instance per allocated product, not one per physical process. Summed
  across all 5, the Vanadium-via-petroleum-refining gap is **21x**, much sharper than the 3.77x
  aggregate — the error concentrates on this path specifically.
- Traced via `bw2calc` supply_array + raw JSON-LD: cement pulls petroleum refining almost entirely
  (94%) through one exchange requesting "Petroleum refining coproduct" — a minor non-reference
  by-product (0.0515 kg/run) — but our importer built petroleum refining's single brightway activity
  using the *reference* product's (Diesel's) allocation factor, 0.2188, instead of the coproduct's
  own native factor, 0.0515 (both confirmed present in the process's own `allocationFactors` list).
  Consumers of any non-reference output inherit the wrong (larger) allocation share.

**Not fixed yet.** Next step: apply each exchange's target product's own native allocation factor
in `03_import_uslci.py`, not just the reference product's, and check the other multi-output processes
(9 of 18 across the 4 bundles use non-mass allocation) for the same pattern. Also flagged: petroleum's
own long-standing ecotox/cancer/non-cancer gap may be partly this same bug (petroleum is reached via
its own coproduct link inside cement's chain) rather than purely the USLCI-version trace-metal story
— worth re-checking after the fix. Full trace: DEVLOG.md, "Root cause found for the electricity/
Vanadium open question: co-product allocation" (2026-07-01).

### 2026-07-02 — co-product fix applied; 8/10 categories validate across all 4 processes

Applied the co-product allocation fix, linked the electricity baseline on both engines, and aligned
the `03`/`03b` bundle directories. All four locked test cases now validate on 8 of 10 categories.
Two previously-recorded "explanations" were disproven (petroleum's trace-metal vintage; the
electricity Vanadium mystery) — both were setup issues, not pipeline bugs. Full narrative:
DEVLOG.md, "Co-product allocation fix + two disproven theories" (2026-07-02).

Per 1 kg reference product. Band = tolerance ladder on |BW/OL − 1|. CSV: `validation_full_chain_results.csv`.

| Process | Category | BW/OL | Band |
|---|---|---|---|
| Petroleum | Global warming | 1.004 | Acceptable |
| Petroleum | Acidification | 1.070 | Failure |
| Petroleum | Human health – particulate matter | 1.042 | Investigate |
| Petroleum | Smog formation | 1.052 | Failure |
| Petroleum | Ozone depletion | 1.172 | Failure |
| Petroleum | Freshwater ecotoxicity | 1.029 | Investigate |
| Petroleum | Human health – cancer | 1.033 | Investigate |
| Petroleum | Human health – non-cancer | 1.029 | Investigate |
| Petroleum | Eutrophication (Freshwater) | 2.830 | Failure |
| Petroleum | Eutrophication (Marine) | 0.149 | Failure |
| Corn | Global warming | 1.000 | Strict |
| Corn | Acidification | 1.000 | Strict |
| Corn | Human health – particulate matter | 1.000 | Strict |
| Corn | Smog formation | 1.000 | Strict |
| Corn | Ozone depletion | 1.032 | Investigate |
| Corn | Freshwater ecotoxicity | 1.001 | Acceptable |
| Corn | Human health – cancer | 1.003 | Acceptable |
| Corn | Human health – non-cancer | 1.003 | Acceptable |
| Corn | Eutrophication (Freshwater) | 2.775 | Failure |
| Corn | Eutrophication (Marine) | 0.175 | Failure |
| Cement | Global warming | 1.000 | Strict |
| Cement | Acidification | 1.000 | Strict |
| Cement | Human health – particulate matter | 1.000 | Strict |
| Cement | Smog formation | 1.000 | Strict |
| Cement | Ozone depletion | 1.001 | Acceptable |
| Cement | Freshwater ecotoxicity | 1.000 | Strict |
| Cement | Human health – cancer | 1.000 | Strict |
| Cement | Human health – non-cancer | 1.000 | Strict |
| Cement | Eutrophication (Freshwater) | 2.775 | Failure |
| Cement | Eutrophication (Marine) | 0.146 | Failure |
| Steel | Global warming | 1.000 | Strict |
| Steel | Acidification | 1.000 | Strict |
| Steel | Human health – particulate matter | 1.000 | Strict |
| Steel | Smog formation | 1.000 | Strict |
| Steel | Ozone depletion | 1.000 | Strict |
| Steel | Freshwater ecotoxicity | 1.000 | Strict |
| Steel | Human health – cancer | 1.000 | Strict |
| Steel | Human health – non-cancer | 1.000 | Strict |
| Steel | Eutrophication (Freshwater) | 2.775 | Failure |
| Steel | Eutrophication (Marine) | 0.379 | Failure |

**Only open thread — eutrophication.** Freshwater is a uniform ~2.775× across all four, root-caused to
the phosphorus characterization factors (inventories of Orthophosphate/Phosphorus water emissions are
identical between engines at BW/OL = 1.000; the gap is entirely CF). Marine (~0.15–0.38×, BW too low)
is a separate NOx-driven issue. Next: audit `02_setup_traci22.py`'s phosphorus freshwater-eutroph CFs
against published TRACI 2.2. Steel's negative ecotox/non-cancer values are its avoided-burden credits
and validate exactly (BW/OL = 1.000).

**Note on openLCA references.** Petroleum, corn, cement now validate against the
`*_US_AVG_ELEC_SELECTION` exports (electricity provider manually linked). Steel is unchanged — it's a
pure foreground process with no electricity input.

### 2026-07-02 (later) — eutrophication resolved via generic CFs; corn/cement/steel validate 10/10

Switched `02_setup_traci22.py` eutrophication CFs from US-national (`Location "00000"`) to generic
(`Location ""`, `EUTRO_LOCATION` toggle) to match openLCA, which pairs the non-located USLCI inventory
flows with the non-located CF. Both eutrophication categories now validate. Root cause and source-file
verification: DEVLOG.md, "Eutrophication regionalization" (2026-07-02).

| Process | Category | BW/OL | Band |
|---|---|---|---|
| Petroleum | Global warming | 1.004 | Acceptable |
| Petroleum | Acidification | 1.070 | Failure |
| Petroleum | Human health – particulate matter | 1.042 | Investigate |
| Petroleum | Smog formation | 1.052 | Failure |
| Petroleum | Ozone depletion | 1.172 | Failure |
| Petroleum | Freshwater ecotoxicity | 1.029 | Investigate |
| Petroleum | Human health – cancer | 1.033 | Investigate |
| Petroleum | Human health – non-cancer | 1.029 | Investigate |
| Petroleum | Eutrophication (Freshwater) | 1.019 | Investigate |
| Petroleum | Eutrophication (Marine) | 1.076 | Failure |
| Corn | (all 10) | 1.000–1.032 | Strict/Acceptable |
| Cement | (all 10) | 1.000–1.001 | Strict/Acceptable |
| Steel | (all 10) | 1.000 | Strict |

Corn's only non-strict category is Ozone (1.032); cement's is Ozone (1.001). **Corn, cement, and steel
validate on all 10 categories.**

**Only open thread — petroleum-specific residuals.** Petroleum alone runs slightly high on
Acidification (1.070), Smog (1.052), Marine eutro (1.076), and Ozone depletion (1.172). [RESOLVED
below — traced to causal allocation.]

### 2026-07-02 (final) — per-exchange causal allocation; all 40 cells validate within 5%

Petroleum's residuals were the importer flattening `CAUSAL_ALLOCATION` to a mass fraction. The one
multi-output causal process in the bundles ("Ethanol; forest residues, thermochem", reached only via
petroleum's biofuel-blending path) over-attributed its forest-residue feedstock to ethanol by 1.83×
(mass 0.8465 vs causal 0.4633). Fixed by applying each exchange's own causal factor. Root cause and
verification: DEVLOG.md, "Causal allocation" (2026-07-02).

| Category | Petroleum | Corn | Cement | Steel |
|---|---|---|---|---|
| Global warming | 0.987 | 0.999 | 1.000 | 1.000 |
| Acidification | 0.996 | 1.000 | 1.000 | 1.000 |
| Human health – particulate matter | 1.001 | 1.000 | 1.000 | 1.000 |
| Smog formation | 0.996 | 1.000 | 1.000 | 1.000 |
| Ozone depletion | 1.003 | 1.001 | 1.000 | 1.000 |
| Freshwater ecotoxicity | 1.027 | 1.001 | 1.000 | 1.000 |
| Human health – cancer | 1.026 | 1.002 | 1.000 | 1.000 |
| Human health – non-cancer | 1.027 | 1.002 | 1.000 | 1.000 |
| Eutrophication (Freshwater) | 1.016 | 1.000 | 1.000 | 1.000 |
| Eutrophication (Marine) | 0.994 | 1.000 | 1.000 | 1.000 |

**All 40 category×process cells within [0.95, 1.05]; 35 of 40 within ±1%.** Max deviation is
petroleum's ecotox/cancer/non-cancer at ~1.027 — the only remaining >1% gap, within tolerance, and
plausibly the genuine (now bounded) USLCI trace-metal vintage effect. Not investigated further.
Petroleum's earlier trace-metal "2× gap" was disproven; this ~3% residual is a different, small thing.

### 2026-07-15 — petroleum residual localized: electricity over-draw, not trace-metal vintage (WIP)

> ⚠️ **Superseded — see 2026-07-21.** The localization here (the residual rides on electricity) was
> right; the attribution to a petroleum-specific *over-draw* was not. Real cause: the
> `isAvoidedProduct` landfill-gas credit imported with the wrong sign. Kept verbatim.

**Supersedes the "trace-metal vintage" guess above for the ~1.027 petroleum residual.** Investigated
ledger #2 without a new openLCA session, using the existing exports' per-flow/per-process
contribution tabs. Findings:

- Per-flow comparison ("Impact contributions by flow" tab): EVERY electricity-borne emission
  (Vanadium, arsenic, lead, mercury, thallium, zinc, cobalt) is brightway = openLCA × **1.027
  exactly**; every non-electricity flow (atrazine, acrolein) = **1.000**. Uniform factor ⇒ not a
  per-CF error.
- brightway TRACI CFs == openLCA TRACI JSON exactly (checked vs `source_data/TRACI_2.2_json_v1.2.0.zip`).
- `03b` injection reproduces the electricity library's `M` inventory exactly (max rel diff 0.0 / 2181 flows).
- Electricity background is correct: **cement draws the same grid node at 99.9% and validates at 1.000.**
- Disaggregation hypothesis disproven: library ships `M = B·A⁻¹` (holds ~1e-15) with zero product
  overlap with the bundles; re-solving live is a mathematical no-op.

**Conclusion:** the residual is a petroleum-specific **~2.7% over-draw of electricity demand** — a
scaling/allocation effect on petroleum's route to the grid. **Open (WIP):** localize the exact
over-drawing node (suspect: multi-output cellulosic-ethanol processes / petroleum refining's physical
allocation) and decide fix-vs-document. Not yet remediated; harness numbers above unchanged.

### 2026-07-17 — petroleum residual ROOT-CAUSED: stale reference export, engine correct (ledger #2 closed)

> ⚠️ **RETRACTED — see 2026-07-21.** This entry's conclusion is wrong. There was no crude-oil
> electricity discrepancy and no stale reference export; openLCA was correct throughout. The residual
> was brightway's own `isAvoidedProduct` bug. Kept verbatim as the record of a disproven theory.

Localized and diagnosed; no engine change needed. Method: attributed the US-average grid node's
(`7068192a`) demand by consuming process in brightway, then compared per-process direct and
total-upstream impacts against the export's own tabs.

- The entire gap sits on the **four crude-oil extraction processes** (upstream ecotox each exactly
  **1.0597×** openLCA; hydrogen, pipeline transport, ethanol all 1.000). The NG-extraction chain
  carries the small remainder (~0.2 pp, scaling drift of the same vintage character).
- Crude's USLCI JSON declares **0.1584 MJ electricity/kg** (provider `7068192a`), with the exchange
  note *"this electricity value has been updated from the original report inventory due to an
  error."* brightway charges exactly 0.1584. The same 0.1584 appears in every surviving USLCI drop
  (2026-06-22 bundle, 2026-06-30 bundle, full-DB zip).
- The openLCA reference calculation charged **0.1494 MJ/kg = 0.1584 / 1.06** — the pre-correction
  value — confirmed to 5 significant figures independently in ecotox, cancer, and non-cancer from
  the export's upstream tabs. Alternative explanations eliminated: not a provider swap (at-grid
  would be ÷1.0458, and no library node matches the implied intensity), not allocation (crude is
  single-output, no formulas/parameters), not a duplicate process copy.
- Inspecting `bw_comp_petroleum_rebuild` (Derby, offline copy): `TBL_EXCHANGES` stores the
  **corrected 0.1584**, data files frozen 2026-06-30 16:19, exports made 2026-07-02 ⇒ the openLCA
  calculation ran on stale product-system state. The openLCA-internal mechanism is unresolved and
  moot — the export's own numbers prove the pre-correction charge.

**Verdict: brightway is correct; the ~1.027 is a reference-side vintage artifact.** Locked CSVs are
unchanged (verbatim history). **Exit criterion:** rebuild the petroleum product system fresh in
openLCA and re-export (`REGENERATING_REFERENCE_EXPORTS.md`); petroleum expected → ~1.000.

### 2026-07-20 — July re-export tested: NUMERICALLY IDENTICAL to the stale export, exit criterion NOT met

> ⚠️ **Superseded — see 2026-07-21.** The negative result here is sound and worth keeping: the
> re-export *was* numerically identical. But it was read as evidence about openLCA-side state, when in
> fact both exports were correct and the bug was in brightway. Kept verbatim.

Ran the harness against the operator's fresh export
(`Petroleum_refining__at_refinery___US___1kg_diesel___July_run.xlsx`, SHA256
`b0b62e2caab5abf06633b8104f8153fcd5505ac1cbe8aa0b4e004c93866a905b`, product system
"…1kg diesel-- July run", calculation dated 2026-07-17 16:36, same setup: Diesel; at refinery,
1.0 kg, TRACI 2.2, process defaults, no cutoff). Result: petroleum ratios unchanged
(ecotox/cancer/non-cancer still ~1.026–1.027).

Cell-wise comparison of the July export against the 2026-07-02 `US_AVG_ELEC_SELECTION` export:
`Impacts` (10 cells), `Total upstream impacts` (8,294 cells across all 1,129 nodes), `Inventory`
(4,491 cells), and `Impact contributions by flow` are **identical to float-serialization noise**
(max rel diff ~3e-15); the only larger deltas are adjacent-row ordering swaps of identical data.
A new product system, recalculated 15 days later, byte-reproduced the stale result — including the
pre-correction 0.1494 MJ/kg electricity charge on the crude-oil processes.

Implication: the 2026-07-17 "stale product-system state" mechanism is **disproven** — whatever
charges the pre-correction value is live, reproducible state in that openLCA database, surviving a
product-system rebuild (while `TBL_EXCHANGES` and the process editor show the corrected 0.1584).
The engine-correct verdict stands (it rests on the export's own upstream tabs, not the mechanism),
but ledger #2's openLCA-side mechanism is reopened. Next discriminating tests (operator): (1) in
the July product system's model graph, read the electricity input amount actually linked on the
four crude-oil extraction processes; (2) repeat in a **brand-new openLCA database** built only from
the bundle imports per `REGENERATING_REFERENCE_EXPORTS.md`; (3) re-check for duplicate crude-oil
process copies in that database.

Repo state: harness re-pointed back at the locked `US_AVG_ELEC_SELECTION` export; re-run confirms
`validation_full_chain_results.csv` byte-for-byte (empty `git diff`). Locked baseline unchanged.

### 2026-07-20 (later) — waste-treatment OUTPUT links restored; vintage selector; `Mg` unit fix; 2025 baseline re-locked

Three `setup/03` findings, all surfaced while building the first causal co-product case
(recycled-HDPE flake, `17664c37…`) — the case that had never been exercised (ledger #1).

- **Waste-treatment OUTPUT links were silently dropped.** openLCA models disposal as the generating
  process *outputting* a `WASTE_FLOW` whose `defaultProvider` is the treatment process (whose own
  reference is that waste flow, as an input). `setup/03`'s technosphere-linking branch was gated
  `… and is_input`, so only *inputs* linked; a waste flow *output* matched no branch and vanished,
  omitting the entire treatment burden (notably landfill methane). Symptom: HDPE flake's
  MSW-landfilling burden (openLCA charges 0.0856 of its 0.52 kg CO2-eq) was absent → GWP ratio
  **0.842**. Fix links non-reference `WASTE_FLOW` outputs to their resolved treatment provider;
  non-reference `PRODUCT_FLOW` outputs stay excluded (co-products, handled by allocation).
  **67 such links** now form across the bundles. HDPE GWP **0.842 → 1.036**; petroleum GWP
  **0.987 → 1.005**.
- **Electricity-baseline vintage selector + stamp/guard.** The US-average grid node is named
  identically across releases but carries a different UUID per vintage (`7068192a` in 2025-06,
  `75d4be66` in 2026-06). The 4 locked cases were exported against 2025-06; the newer HDPE/PET
  bundles hardcode the 2026-06 UUID, so their supply chain was being silently relinked down to 2025
  by `03`'s name-match fallback. A build now injects exactly ONE vintage: `03b --vintage
  {2025|2026}` (default 2025) selects the library and stamps `electricity_vintage` onto
  `electricity-baseline`; `03` copies the stamp to `uslci-subset`; the harness reads it and **skips**
  any case whose `EXPECTED_VINTAGE` doesn't match, writing a vintage-tagged CSV for non-default
  builds so they cannot clobber the locked 2025 table. Injecting both vintages at once is
  deliberately unsupported (two identically-named US-average nodes ⇒ ambiguous name resolution).
- **`Mg` misread as milligram.** The `WITHIN_FP` lookup was case-insensitive, so `Mg` (megagram =
  tonne, the unit of every MRF sorting output in the recycling sector) resolved to the `mg` entry —
  1e-6 instead of 1e3, a silent **1e9** error. Mg-based exchanges partially cancelled, but the
  sorting processes' kWh electricity did not, inflating HDPE flake GWP to ~1.6e7 kg CO2-eq/kg. Fix:
  `_WITHIN_FP_EXACT`, a case-sensitive table checked before the lowercase fallback. A census of the
  full USLCI zip confirms Mg/mg is the **only** case collision among its 30 unit strings. Post-fix
  HDPE flake GWP: 0.4499 kg CO2-eq/kg (literature range). The four locked cases carry no `Mg`
  exchanges.

**2025 baseline re-locked on this build.** Corn/cement/steel within ±5%; petroleum's three toxicity
cells moved to **~1.05** (from ~1.027) because the waste-linking fix pulled MSW-landfilling into the
locked cases' supply chains for the first time.

> ⚠️ **The reading recorded at the time — that the ~1.05 was a "reference-completeness gap with
> brightway the more-complete engine" — was WRONG, and is superseded by the 2026-07-21 entry below.**
> The waste-linking fix was itself correct; what it actually did was *expose a dormant second bug*
> (`isAvoidedProduct`). Kept here as the record of what was believed on 2026-07-20.

### 2026-07-21 — petroleum residual CLOSED: `isAvoidedProduct` ignored. All 40 cells within 0.1%; HDPE validates (ledgers #1 and #2 closed)

**Root cause found, and it supersedes every earlier diagnosis of the petroleum residual.** USLCI
marks byproduct energy/material recovery with `isInput=true` **+ `isAvoidedProduct=true`** — e.g. the
MSW-landfilling process recovering 91.97 kWh of landfill-gas electricity that displaces grid power.
openLCA *credits* these (they lower the result). `setup/03` had no `isAvoidedProduct` handling at all
and imported them as positive consumption **burdens**.

Since petroleum's toxicity is ~99% grid electricity, that one sign error was the entire gap:

| | openLCA | brightway (pre-fix) |
|---|---|---|
| MSW-landfilling contribution to petroleum freshwater ecotox | **−0.072** | **+0.072** |

That 0.145 flip is exactly the 2.678 → 2.824 discrepancy, and it reproduces identically across
ecotox, cancer, and non-cancer. Decisive check: brightway-minus-landfilling matched
openLCA-minus-its-landfilling-credit to 0.01%.

**Fix:** sign-flip avoided-product technosphere exchanges into credits — 15 such exchanges per bundle
(landfilling, MSW combustion, sulfuric acid, sulfur, ethylene glycol…). This is a first-principles
correctness change, not a tuning: it was aimed at three toxicity cells and moved **all 40** to exact
agreement.

**Results — 2025 locked table (`validation_full_chain_results.csv`), re-locked on this build:**

| | before (2026-07-20 build) | after |
|---|---|---|
| Cells within 0.1% (Strict) | 26 / 40 | **40 / 40** |
| Cells outside ±5% | 3 | **0** |
| Max deviation | ~5.4% (petroleum ecotox) | **< 0.0001%** |

Every cell now rounds to a BW/OL ratio of **1.000**; petroleum ecotox lands at 2.6784642 vs openLCA
2.6784639. Full clean parity, no residual.

**HDPE flake validates — ledger #1 closed.** The recycled-HDPE-flake case (`17664c37…`) consumes
causal co-products from the MRF-sorting processes (non-uniform allocation grids) — the most intricate
importer path in the repo, previously exercised by **zero** validation cells. Added to
`TARGETS_FULL`; on a `03b --vintage 2026` build it reproduces openLCA **within 0.01% on all 10
categories**, locked in `validation_full_chain_results_2026.csv`. The vintage guard skips it on the
default 2025 build, so the locked 2025 table is untouched.

**Ledger #2 closed, and its prior diagnosis retracted.** The 2026-07-17 "openLCA charged a
pre-correction crude-oil electricity value" finding was a **misattribution**. There was no
crude-electricity discrepancy; openLCA was correct throughout, and the residual was always
brightway's missing (then sign-flipped) landfill credit. The 2026-07-15, 07-17, and 07-20 entries
above are kept verbatim as the record of the wrong path — see the forward pointers on each.

Repo state: `validation_full_chain_results.csv` re-locked (40/40 at 1.000);
`validation_full_chain_results_2026.csv` added (steel + HDPE, 2026 build); `charts/validation/*`
regenerated; 41/41 pytest pass. Commit `2f9eb3c`.

### 2026-07-23 — USLCI allocation census: economic allocation is untestable, steel is aggregated

Census of all 1,342 processes in the full USLCI zip, run to choose Phase 3.1 test cases from evidence
rather than by picking sectors by intuition. **60 multi-output processes: 27 ECONOMIC, 28 PHYSICAL,
5 CAUSAL.** Two findings changed the plan:

- **Every `ECONOMIC_ALLOCATION` process in USLCI is degenerate.** All 29 have factors of exactly 0.0
  or 1.0 — one product absorbs 100% of the burden, co-products get zero. **Zero** processes have two
  or more economic factors strictly between 0 and 1. Confirmed directly on `Containerboard; at mill`,
  whose `allocationFactors` list `ECONOMIC_ALLOCATION` = `[containerboard 1.0, tall oil 0.0,
  turpentine 0.0]`. The locked **corn** case is the same shape (`[0.0, 1.0]`).
  ⇒ The economic-allocation *arithmetic* cannot be validated against USLCI at all, because USLCI
  never actually divides a burden economically. Corn already covers the degenerate path. Phase 3.1's
  "≥1 additional ECONOMIC case" is **struck**, and recorded as a scope limitation instead.
- **USLCI steel is strictly foreground ⇒ ledger #3 closed by correcting steel's framing.** The six
  large `Steel; * coil/plate/sections; at plant` processes (~740 exchanges each) have **zero**
  technosphere inputs with a `defaultProvider` and ~690 elementary flows apiece. They are *labelled*
  unit processes but behave like **system processes**: the entire upstream is aggregated into one
  inventory rather than linked. Re-pulling one would not exercise the solve any more than the present
  steel billets bundle does.
  **Decision (2026-07-23): do not pursue — and restate steel's actual role.** Steel billets was
  chosen early as the **Layer 1 control** of the layered design at the top of this log: an aggregated
  inventory vector characterized by both engines, no solve, so a discrepancy could be localized to
  the foreground (CFs, units, flow mapping), the background (parser, allocation, provider linking),
  or the combination. It does that job — 1.000 on all ten categories, inside Layer 1's ≤0.1% target —
  which is what let the petroleum residual be attributed to the background side with confidence. The
  earlier framing ("a fourth full-chain case, full-chain re-pull pending") was the actual defect;
  `VALIDATION_REPORT.md` / `validation/README.md` / `README.md` now state the control role and its
  corollary (steel exercises none of the solve) instead of implying a missing case.

By contrast PHYSICAL allocation has real splits and is worth the operator sessions — best candidate
`Chlorine; chlor-alkali electrolysis; at plant` (`a3e150d0-770e-4e2a-9b19-f7daa8cda38b`), a genuine
three-way NaOH 0.5453 / Cl₂ 0.4357 / H₂ 0.019 split with 15 linked upstream providers. Ranked
shortlist with UUIDs in `RELEASE_PLAN.md` Phase 3.1.

Also confirmed this session: **every bundle in the July-2026 LCA Commons drop is 2026-vintage** —
recycled HDPE flake, recycled PET flake, and "Corn; at field" reference the 2026 grid node
(`75d4be66`) 91/91/86 times and the 2025 node (`7068192a`) **zero** times, while the locked-case
bundles from the June drop reference 2025 85 times. Any newly pulled bundle therefore needs a
`--vintage 2026` build and a 2026 baseline mounted in openLCA.
`REGENERATING_REFERENCE_EXPORTS.md` previously told the operator to mount 2025 unconditionally and
has been corrected.

No engine change; locked CSVs untouched.

### 2026-07-23 (later) — five new cases validate: physical allocation exercised on real splits for the first time

Operator session produced openLCA exports for recycled-PET flake, chlorine (chlor-alkali),
hardboard, and soybean oil. All four bundles are 2026-vintage, so they join HDPE flake and steel on
a `03b --vintage 2026` build. Added to `TARGETS_FULL` + `EXPECTED_VINTAGE`; the three 2025 cases
were skipped by the vintage guard as designed.

**Result: 60/60 cells within 0.001% — max deviation 0.00077%.** Every cell rounds to 1.000.

| Case | Allocation | Max deviation |
|---|---|---|
| Steel; billets (Layer 1 control) | none | 0.000009% |
| Recycled HDPE flake | causal (upstream) | 0.00077% |
| Recycled PET flake | causal (upstream) | 0.00054% |
| **Chlorine; chlor-alkali electrolysis** | **physical, 3-way** | **0.000077%** |
| **Hardboard; at hardboard plant** | **physical, 7-way** | **0.000060%** |
| **Soybean oil; crude, degummed** | **physical, 2-way** | **0.000097%** |

Why this matters more than the cell count: **these are the first non-degenerate allocation grids in
the test set.** Chlorine splits three ways (NaOH 0.5453 / Cl₂ 0.4357 / H₂ 0.019), hardboard seven
ways (0.8634 → 0.0016), soybean two (meal 0.8051 / oil 0.1949). Until now the only real physical
grid under test was petroleum's; corn's ECONOMIC factors are `[0.0, 1.0]`, and the 2026-07-23 census
showed every ECONOMIC process in USLCI is degenerate the same way. Physical-allocation arithmetic is
now verified against openLCA on three independent non-trivial grids, at ~1e-6 relative agreement.

PET is also a second causal co-product *consumption* case, independently reproducing the path that
closed ledger #1.

**Setup notes for replication.** All five exports confirmed `Amount: 1.0 kg`, TRACI 2.2, process
defaults, no cutoff, from their `Calculation setup` sheets. One counterintuitive case: the process
`Soybean oil; crude, degummed; at plant` declares **`Soy meal; at plant` (4131 kg) as its
quantitative reference**, not the oil it is named after — so both engines report soy meal at the
0.8051 factor. The openLCA export names soy meal as its Product, which is correct, not an operator
error.

Repo state: `validation_full_chain_results_2026.csv` re-locked with 60 rows (was 20). The locked
2025 table is untouched — the guard tagged the output, as intended. Charts:
`charts/validation/validation_{ratio,pct}_full_chain_2026.png` are new; the four 2025/direct charts
regenerated **byte-for-byte**. Note `06_visualize_validation.py` had been silently skipping
vintage-tagged tables — its auto-detect glob (`validation_*_results.csv`) did not match
`…_results_2026.csv`, so the 2026 results had never been charted; fixed, and chart filenames + titles
now carry the vintage so a tagged run cannot overwrite the locked 2025 images. **This machine's build is now
2026**; reproducing the locked 2025 table requires `03b --vintage 2025` + `03` first.

### 2026-07-23 (later) — direct-mode coverage re-scoped: steel is a second LCIA-math check

Ledger #8 had recorded "direct mode covers petroleum only" as an open gap. Measurement says that
overstated it. Steel has zero linked technosphere inputs, so its solve contributes nothing and its
**direct and full-chain scores are bit-identical** — verified across all 10 categories (direct/full
ratio 1.0000 on every one). Its standard `Impacts` export is therefore already a direct-mode check,
validating FEDEFL flow mapping, TRACI CFs, and unit conversion on a second, independent 92-flow set.

Direct mode serves two purposes, and they are covered differently:

| Purpose | Coverage |
|---|---|
| Isolate the LCIA math (CFs, mapping, units) | petroleum **and steel** — two independent flow sets |
| Separate a process's own emissions from its background | petroleum only — still open |

The second is the real remaining gap, and it is case-specific rather than generic: measured
direct/full-chain shares are petroleum <0.01%, cement up to 76%, steel 100%. So steel cannot stand in
for cement — a cement or corn direct export would be needed to attribute a hypothetical cement
disagreement to foreground vs. solve. Lower priority than the old phrasing implied: the CF table and
flow list are shared across every case, and two processes already confirm them.

No engine change; locked tables untouched.
