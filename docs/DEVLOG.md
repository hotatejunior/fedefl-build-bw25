# Development Log — fedefl-build-bw25

> **Provenance.** This engine was extracted, 2026-07-04, from a larger (private) LCA project — an
> asphalt dynamic-LCA study whose background-inventory needs it originally served. That project holds
> the full chronological history: the openLCA validation study, the per-run parity debugging, and two
> disproven theories (the "USLCI trace-metal vintage" gap and the "electricity baseline Vanadium
> ~3.7×" mystery). This log is the curated engine provenance — the design decisions and the bugs that
> had to be fixed to reach validation. It will be expanded over time.

---

## Project Purpose

A programmatic brightway25 pipeline that loads the open US LCA stack — USLCI background processes,
FEDEFL elementary flows, TRACI 2.2 impact methods — and computes LCIA results correctly, verified
against openLCA on identical inputs. A reproducible, code-first alternative to the GUI-bound openLCA
workflow, with no ecoinvent license required.

## Stack and Architecture

- **brightway25** as the computation engine (`bw2data` 4.7, `bw2calc` 2.5)
- **FEDEFL** (Federal Elementary Flow List) as the canonical biosphere flow vocabulary
- **TRACI 2.2** for impact assessment (10 categories)
- **USLCI** as the background process library, sourced from **LCA Commons** in openLCA JSON-LD
- **US Electricity Baseline** injected from an openLCA *library* (pre-solved matrix) package

Compartmentalized into `setup/` (one-time, per machine) and `general/` (the practitioner-facing
engine). See the README for the layout and [`TUTORIAL.md`](TUTORIAL.md) for the run order.

## Key Design Choices

- **Custom JSON-LD parser instead of `bw2io`.** `bw2io`'s `JSONLDImporter` misclassifies USLCI
  exchanges via its `isInput` handling; `setup/03_import_uslci.py` reads JSON-LD directly for full
  control over exchange-direction detection. The full field-level mapping and how the parser resolves
  each openLCA↔brightway incompatibility (direction, provider resolution, units, allocation, waste
  flows, avoided products) is documented in [`SCHEMA_CROSSWALK.md`](SCHEMA_CROSSWALK.md); allocation
  specifically — including how N co-product outputs are reshaped to satisfy brightway's square-matrix
  requirement — in [`ALLOCATION.md`](ALLOCATION.md).
- **FEDEFL UUIDs as the universal key.** Every biosphere flow — in the brightway DB, the TRACI
  method, and the USLCI data — is keyed by FEDEFL UUID. Linking is deterministic; no name-matching.
- **openLCA library interoperability.** `fedefl_bw25/olca_library.py` decodes openLCA's pre-aggregated
  library (matrix) packages so their pre-solved background (e.g. the electricity baseline) can be
  injected directly — a format brightway cannot otherwise read.
- **Dedicated activities for causal co-products** (2026-07-15). Causal allocation is per-exchange,
  so a consumer of a causal process's NON-reference co-product cannot be re-based through the
  reference activity by any scalar multiplier. `setup/03` therefore builds a second brightway
  activity per causal co-product (code `<proc_uuid>__co__<flow_uuid>`): production = the
  co-product's own native yield, every exchange scaled by the co-product's own column of the
  openLCA causal factor grid (extracted by `setup/allocation.py::causal_coproducts`). Consumers
  are redirected to it at the link site, no multiplier. A consumed co-product whose grid column is
  empty hard-stops the build (mirrors the unknown-unit policy). Verified on the cellulosic-ethanol
  process: its uniform grid means the co-product activity is the reference activity rescaled by
  exactly λ_co/λ_ref = 1.158249156 on all 20 non-product exchanges, matching the JSON factors at
  full precision; non-uniform grids are pinned by synthetic fixtures in `tests/test_allocation.py`.
  openLCA parity for a *consumed* causal co-product was confirmed 2026-07-21/23 by the recycled
  HDPE-flake and PET-flake cases, which consume MRF-sorting co-products through exactly this path and
  reproduce openLCA within 0.001% on all 10 categories.
- **Per-process downloads, not the full USLCI zip.** LCA Commons bundles each process with its full
  upstream, so a handful of target processes arrive with their complete supply chains without
  importing the entire ~10,000-process database.

## Engine bugs fixed to reach validation

Condensed; the full diagnostic narrative for each lives in the parent project's history.

- **TRACI 2.2 spatial CF inflation.** Spatially-resolved characterization factors were being summed
  across locations; the fix selects the generic (non-located) variant to match openLCA. Same issue,
  same fix, drives the `EUTRO_LOCATION` toggle (defaulted to generic for parity).
- **Missing unit strings → matrix explosion.** Exchanges with empty unit strings fragmented the
  technosphere matrix; unit handling was made consistent.
- **Biosphere cross-property normalization on elementary flows.** A normalization meant for
  technosphere flows was wrongly applied to elementary flows; scoped out.
- **Multi-output allocation.** Multi-output processes need co-product allocation, initially missing,
  then wrongly assumed mass allocation universally — corrected to honor each process's declared
  allocation method (physical / economic / causal), applied **per-exchange** rather than flattened
  to a single mass factor.
- **Relink / flow-collision bugs.** Both the electricity-baseline relink fallback and bundle-internal
  technosphere linking resolved providers by a key that could collide across flows; keyed correctly.
- **Waste-treatment processes rejected as malformed.** `WASTE_FLOW` reference products were being
  dropped; accepted as valid reference products.
- **Waste-treatment OUTPUT links dropped (2026-07-20, found via first HDPE parity diff).** openLCA
  models disposal as the generating process *outputting* a `WASTE_FLOW` whose `defaultProvider` is
  the treatment process (whose own reference is that waste flow, as an input). `setup/03`'s
  technosphere-linking branch was gated `... and is_input`, so only *inputs* linked — a waste flow
  *output* to a treatment provider matched no branch and was silently dropped, omitting the entire
  treatment burden (notably landfill methane). Symptom: HDPE flake's MSW-landfilling burden
  (openLCA charged 0.0856 kg CO2-eq of the 0.52 total) was absent in brightway → GWP ratio 0.842.
  Fix: the branch now also links non-reference `WASTE_FLOW` outputs to their resolved treatment
  provider (positive consumption of the treatment service, linked by the same machinery as inputs);
  non-reference `PRODUCT_FLOW` outputs remain excluded (those are co-products, handled by
  allocation). 67 such links now form across the bundles; HDPE GWP 0.842 → 1.036. This pulled the
  MSW-landfilling process into the locked cases' supply chains for the first time — which **exposed a
  separate, dormant importer bug** (avoided-product handling; next entry). Petroleum GWP rose into
  agreement (0.987 → 1.005) but its toxicity cells jumped to ~1.05; that jump turned out to be the
  landfill-gas electricity credit being imported with the wrong sign, not a reference gap.

- **`isAvoidedProduct` ignored → avoided credits imported as burdens (2026-07-21, root cause of the
  petroleum toxicity residual).** USLCI marks byproduct energy/material recovery with `isInput=true` +
  `isAvoidedProduct=true` — e.g. MSW landfilling/combustion recovering landfill-gas electricity that
  displaces grid power (91.97 kWh on the landfilling process). openLCA credits these (they *lower* the
  result); `setup/03` had no `isAvoidedProduct` handling and imported them as positive consumption
  burdens. Since petroleum's toxicity is ~99% grid electricity, that one sign error on the landfill
  credit *was* the entire gap: openLCA's landfilling contributes **−0.072** to petroleum ecotox,
  brightway's **+0.072**, and that 0.145 flip = the whole 2.68 → 2.82 discrepancy. Fix: sign-flip
  avoided-product technosphere exchanges into credits (15 such exchanges per bundle — landfilling,
  combustion, sulfuric acid, sulfur, ethylene glycol…). A first-principles correctness fix, so it
  brought **all 40** cells to exact agreement, not just the three toxicity targets. **Supersedes the
  ledger-#2 "crude electricity" diagnosis below**: there was no crude-electricity discrepancy; openLCA
  was correct throughout, and the residual was always brightway's missing (then sign-flipped) landfill
  credit.

Outcome: **all 40 category × process cells then in the test set validate within 0.1% of openLCA** —
every cell rounds to a BW/OL ratio of 1.000. Full clean parity; the petroleum residual is closed. Five
further cases added 2026-07-23 (chlorine, hardboard, soy meal, PET flake, alongside HDPE flake) took
the total to **100 cells, all within 0.1%** — they landed on 1.000 on first run, having been validated
after every fix above had already shipped. See `VALIDATION_REPORT.md`.

**⚠️ Superseded 2026-07-21 — kept as history; the diagnosis in this paragraph was wrong.** The
petroleum residual was not a reference artifact and not about crude electricity — it was brightway's
`isAvoidedProduct` bug (entry above): the missing/sign-flipped landfill-gas electricity credit.
openLCA charged the correct value throughout. The original (incorrect) reasoning is preserved below.

**Resolved (2026-07-17): the ~1.027 petroleum residual is a reference-export artifact, not an
engine bug.** The entire electricity over-draw sits on the four crude-oil extraction processes.
Their JSON declares 0.1584 MJ electricity/kg — a value USLCI corrected "due to an error" (per the
exchange's own note) — and brightway charges exactly that, while the openLCA reference calculation
charged the pre-correction 0.1584/1.06 (the openLCA database on disk already stores the corrected
value). No code change; engine verdict rests on the export's own contribution tabs.
**Update 2026-07-20:** a fresh product system, rebuilt and re-exported per the exit criterion,
reproduced the stale calculation numerically identically — pre-correction charge included — so the
"stale cached product-system state" mechanism is disproven; the stale charge is live, reproducible
state in that openLCA database. Residual closure now waits on regeneration in a brand-new openLCA
database. Evidence chain: `validation/VALIDATION_LOG.md` (2026-07-17 and 2026-07-20 entries).

## Quality-of-life / reproducibility

- **Electricity-baseline vintage selector + stamp/assert guard (2026-07-20).** The US-average grid
  node is named identically across releases but carries a different UUID per vintage (`7068192a` in
  2025-06, `75d4be66` in 2026-06 — see the vintage-UUID note). The 4 locked cases were exported
  against 2025-06; the newer HDPE/PET bundles hardcode the 2026-06 provider UUID, so their brightway
  supply chain was being silently relinked down to 2025 (name-match fallback in `03`'s
  `_resolve_provider`), producing a vintage mismatch against their openLCA exports. Fix: a build
  injects exactly ONE vintage. `setup/03b --vintage {2025|2026}` (default 2025) selects the library
  (`VINTAGES` config, each with its own pinned SHA) and STAMPS `electricity_vintage` onto the
  `electricity-baseline` db; `setup/03` copies the stamp onto `uslci-subset`; `validation/05` reads
  it and HARD-STOPS if a case's expected vintage (`EXPECTED_VINTAGE`, steel = agnostic) doesn't match
  the build — so a 2025 case can never be silently diffed against a 2026 grid. Injecting BOTH vintages
  at once is deliberately unsupported: the two identically-named US-average nodes would make name-based
  resolution ambiguous AND would flip the locked cases' stray direct 2026 references. Verified: 2025
  build reproduces the locked CSV byte-for-byte (machine-epsilon float noise only), the guard trips on
  a 2026 stamp, pytest green.
- **Replication gate changed from byte-identity to tolerance (2026-07-23).** The documented check
  was "run the harness, `git diff` the results CSV, empty diff = replicated". That gate only holds
  for someone with a byte-identical `source_data`, which is nobody except the maintainer: measured on
  petroleum, a petroleum-only build (342 activities) differs from the locked table by ~1e-14 and the
  full 391-activity build by ~1e-15, because the sparse solve's summation order depends on which
  activities are in the matrix — adding *or removing* bundles moves the last ulp or two. It is not
  monotonic in size. So the documented gate would read FAIL for a peer doing everything right, at
  1e-14, which is the worst possible first impression. `validation/05` now ends with an explicit
  **REPLICATION GATE: PASS/FAIL** on every cell being within `TOLERANCE` (0.1%, the Strict band) of
  openLCA, and the per-row `!` marker is driven off the same constant so the table and the verdict
  cannot disagree. Two related fixes for the same use case — a curious user checking *one* process:
  a missing reference export now SKIPs by name instead of aborting the whole run, and a run with any
  missing export writes `…_partial.csv` so it cannot overwrite the locked table. The locked CSVs are
  now described as a published reference table, not a gate.
- **Electricity vintage exposed at run time (2026-07-23).** The vintage stamp existed on the
  databases and the harness guarded on it, but `general/` never read it — no console line, no
  manifest field. A practitioner running `general/04` against a 2025-era process on a 2026 build got
  materially different numbers with nothing on screen saying which grid produced them (measured on
  this build: corn 0.887–1.025 and cement 0.929–1.052 versus their locked 2025 values, two cells
  outside ±5%). Since the manifest's whole purpose is "audit any individual result", omitting the one
  field that determines the background electricity undercut the claim. `general/04` now prints the
  vintage in its target block — computed before the manifest section so `--no-manifest` runs still
  report it — and `run_manifest.summarize_electricity_vintage()` records it in the manifest
  (`schema` bumped to `validation-manifest/2`). It also names two states rather than hiding them:
  `unstamped` (a build predating the stamp — unattributable, not wrong) and **`inconsistent`**, where
  the baseline DB and the USLCI DB carry *different* stamps because `03b` was re-run without
  re-running `03`. That last one is the dangerous case: the injected grid is the new vintage while
  everything reading the stamp — the harness's own guard included — believes the old one.
  Same visibility gap at the chart layer: `validation/06`'s auto-detect glob
  (`validation_*_results.csv`) never matched the vintage-tagged `…_results_2026.csv`, so the 2026
  results had been silently uncharted since tagging was introduced. The glob now matches, and both
  the output filename and the chart title carry the vintage — necessary because both tables report
  `mode=full_chain`, so untagged output would have overwritten the locked 2025 images.
- **Electricity-baseline vintage auto-detection (2026-07-23).** The vintage selector (above) worked
  but made the operator carry the knowledge: pick `--vintage` correctly or get a build whose grid the
  bundles never referenced. The bundles already encode the answer — their `defaultProvider` entries
  name a specific US-average grid UUID — so `setup/03b` now reads it. The same bundle scan that
  discovers which library processes to inject also classifies each bundle's vintage
  (`fedefl_bw25/vintage_detect.py`, pure + unit-tested), and `--vintage` defaults to the detected value.
  **The trap, found while implementing: presence is not the test.** Every locked 2025 bundle also
  contains a single stray reference to the *2026* node (79–80 citations of `7068192a` against 1 of
  `75d4be66`), so a "does this bundle mention the 2026 UUID?" detector classifies **every** bundle in
  the repo as 2026 — silently injecting the wrong background for the four locked cases. Detection is
  therefore by **dominance** (most-referenced wins), with an exact tie and a no-grid-reference bundle
  both returning "undecided" rather than a guess. A vintage-agnostic bundle (steel billets: zero
  external providers) constrains nothing and doesn't block detection. A **mixed** bundle directory
  hard-stops and prints the split by vintage, because one build cannot satisfy both — this is the
  normal state of `source_data/` once 2026-drop cases sit alongside the locked four.
  `--vintage` still overrides everything, and `--library` still points at an arbitrary library file.
- **"Mg" misread as milligram (2026-07-20, found on first HDPE-flake build).** The `WITHIN_FP`
  lookup is case-insensitive, so `Mg` (megagram = tonne, the unit of every MRF sorting output in the
  recycling sector) resolved to the `mg` entry — 1e-6 instead of 1e3, a silent 1e9 error. Mg-based
  exchanges partially cancelled (both sides shrunk), but the sorting processes' kWh electricity
  didn't, inflating HDPE flake's GWP to ~1.6e7 kg CO2-eq/kg. Fix: `_WITHIN_FP_EXACT`, a
  case-sensitive table checked before the lowercase fallback. A census of the full USLCI unit
  universe confirmed Mg/mg is the *only* case collision among the 30 unit strings — so the earlier
  "100% coverage" claim was true for presence but not correctness; coverage now audited for case
  too. Post-fix HDPE flake GWP: 0.4499 kg CO2-eq/kg (literature range). The four locked cases carry
  no Mg exchanges; harness ratios unchanged (last-ulp float noise only, from the larger matrix —
  byte-for-byte parity still holds against the pinned asset set without the HDPE/PET bundles).
- **Per-result completeness was a database-wide total (2026-08-03, exposed by the full-DB build).**
  `general/04` derived a result's supply chain from `lca.dicts.activity` — which indexes *every*
  column of the technosphere matrix, i.e. every activity in the loaded databases, reachable from the
  functional unit or not. The manifest then summed each process's cutoffs over that set and reported
  it as "within this supply chain". Under the per-target bundle build the overstatement was small
  enough to pass review (petroleum: 351 activities actually supplied out of 402 indexed), because
  those bundles were assembled to be roughly the target's own chain. The full-database build removed
  the coincidence: a corrugated-product result claimed all 1,371 USLCI processes and all 2,801
  DB-wide cutoffs, against a real chain of 383 activities. Fix: `run_manifest.select_supplied_keys()`
  reduces the column index to non-zero entries of `lca.supply_array` before the crossing — exact
  inequality, not a tolerance, so an avoided-product credit's negative supply and any arbitrarily
  small contributor are kept while unreachable columns (which solve to exactly 0.0) are dropped.
  Post-fix the same corrugated result reports 383 activities / 736 cutoffs; petroleum reports 351 /
  624. No LCIA score changes — this is the audit layer, not the solve — so the harness is untouched.
  Worth noting what the bug's shape says about the feature: the numbers looked plausible precisely
  because the build made them nearly right, and only a build that broke that coincidence revealed
  the claim was never being computed.
- **Baseline discovery was scoped to bundles, so the full-DB build lost 42 processes to silent zeros
  (2026-08-03).** `03b` globs `????????-????-????-????-????????????_*.zip` to decide which library
  processes to inject. The full USLCI zip isn't named that way, so it was never scanned: `03b`
  injected the 11 providers the nine bundles referenced, while a `USLCI_FULL_DB=1` import needs 17.
  The six missing were all FERC regional consumption mixes (Northwest, SPP, Southwest, CAISO,
  Southeast, ERCOT) — present in the 2025 library under exactly the UUIDs the processes hint at, just
  never injected. `03`'s resolver then couldn't match the hint, fell back to flow-matching, found a
  dozen candidates all producing `Electricity, AC, 120 V`, and correctly refused to guess. The
  casualties were the 42 `Transport, … truck; electricity powered; <region>` processes, whose only
  real input is that grid: cutting it left them scoring exactly **0.0** — a clean, plausible-looking
  number rather than an error. The ambiguous-link count and the zero-score count were the same 42,
  1:1, with no overlap either way. Fix: `03b` also scans the full zip when present, resolving it
  through the conversion table's `_meta.source_zip` — the same single source of truth `03` uses, so
  the two can't drift apart again. Injecting the *union* is deliberate: `electricity-baseline` is one
  database shared by both USLCI builds, so scoping discovery to whichever build ran last would make
  the baseline order-dependent, which is the defect restated rather than fixed. The full zip
  contributes providers only and is barred from the vintage vote — it is a 2025-grid artifact, and
  letting it vote would pin auto-detection to 2025 and break the 2026 build. Unresolvable providers
  are now reported split by who needs them, so a 2026 build doesn't read as damaged by 2025-only
  grids it was never going to link. Result: full DB ambiguous 42 → **0**, linked +42, all 42
  transport processes score real values (0.036–0.040 kg CO2-eq), zeros 136 → 94 (the legitimate
  remainder: 55 USEEIO bridge stubs with no exchanges, 39 zero-allocation outputs). Every other
  process's score is bit-identical, and both locked tables reproduce **byte-for-byte** with the
  baseline at 17 nodes instead of 11 — aggregated background columns are independent, so unreferenced
  ones perturb nothing.
- **Builds now record which USLCI they came from (2026-08-05).** Nothing at run time could
  distinguish "no such process" from "that process is in a newer USLCI release than this build" —
  the two are identical to a caller, and the second is common because USLCI ships quarterly. Found
  the hard way: a fishmeal process (`97970125…`) missing from a full-database build that was, by
  construction, the whole database. `setup/03` now stamps `uslci_source` — the source zips' names and
  SHA256 — onto the brightway database and into the provenance sidecar; `general/04` prints it in the
  startup banner and records it in the manifest, so a result can state the release it came from.
  The identifier is deliberately a content hash rather than a version string: the full USLCI zip has
  no intrinsic version field (its `openlca.json` names only the electricity-library dependency), and
  the release hash in bundle filenames proved unreliable as a content marker — the same suffix was
  observed on bundles whose process versions disagree. A hash says exactly which bytes produced the
  database even when it cannot say what upstream calls them. The miss message uses it too: on the
  full build, where "import more processes" is not the answer, it now names the source and points at
  the quarterly release cycle instead.
- **Upgraded to USLCI v1.2026-06.0 (2026-08-05).** 1,341→1,425 processes, 4,314→4,471 flows,
  `uslci-full` 1,371→1,455 activities, and the embedded electricity baseline moves 2025→2026 (the
  release re-pointed every provider at 2023 electricity data). Three guards fired, each turning what
  would have been a silent wrong number into a stop:
  - **The conversion-table pin.** Swapping the zip invalidated `uslci_flow_conversions.json`'s
    `_meta` SHA256. Rebuilding changed 48 shared entries, but only **three** genuinely: the Energy
    conversions for Biomass (16.832→16.34), Softwood (19.8→20.7) and Hardwood (20.7→19.8) — the last
    two swapping values, which reads as USLCI correcting a transposition. The rest was float
    repr (`1.9600000000000002e-13` → `1.96e-13`). All 42 uses of those flows across the nine bundles
    are on the **Mass** property, so the changed *Energy* factors are never applied; confirmed
    empirically by re-running the 2025 gate with the new table before touching the vintage —
    byte-for-byte.
  - **The unit hard-stop** (ledger #7) on three units new to this release. `ft2` = 0.09290304 m²
    (international foot squared, consistent with the existing `ft`), `ha*a` = 1e4 m²·a. `kcal` =
    **4.1868e-3 MJ, the International Table calorie**, matching this table's IT Btu (1055.06 J) and
    openLCA's reference data — the thermochemical calorie (4.184 J) would be 0.07% low, i.e. *under*
    the 0.1% replication gate, so picking wrong would not have been caught by the harness. Noted at
    the entry because that is exactly the kind of error this project cannot detect by testing.
  - **A dangling upstream reference.** `Land use` (`69430702…`) is consumed by three processes but
    ships no flow file in the export. The exchange is self-describing (Area*Time, ref `m2*a`), so
    only the unit factor was missing — an upstream data defect, not a parser one.

  Verified after the upgrade: both locked tables reproduce **byte-for-byte** (40 cells on a 2025
  build, 60 on 2026), all 1,455 processes solve with zero errors, **0 ambiguous links**, and the
  cement unit test self-skips with the correct vintage-guard message.
- **Foreground CSV units were decorative on technosphere rows (2026-08-05).** The `unit` column was
  required by the schema but never applied to technosphere exchanges, and only property-checked on
  biosphere ones. Amounts went verbatim into the exchange, so they were used against the
  counterpart's reference unit whatever the column said. On a 2-process test foreground whose
  correct answer is 2.584981 kg CO2-eq — hand-verified against the TRACI CFs and the providers' own
  scores — writing a fishmeal input as `0.2 MJ` returned the same number as `0.2 kg` (unit ignored),
  and writing the *same quantity* as `200 g` returned **104.801**, a silent 1000× error. The
  biosphere branch was no safer where it mattered: its flow-property check catches MJ-vs-kg but
  passes g-vs-kg, since both are mass, so `1500 g` of CO2 scored **1501.08**. The check gave false
  assurance precisely on the likelier mistake, and the technosphere rows — the ones that pull entire
  supply chains — had no check at all. Fix: `convert_to_ref_unit()` and a `_UNIT_TO_REF` table
  mirroring `setup/03`'s `WITHIN_FP`, applied to both branches. Matching unit passes through; same
  flow property converts and reports the conversion; a different property or an unknown unit is a
  hard error rather than a passthrough — ledger #7's rule, which had been applied to the USLCI
  importer in 2026-07 but never to the foreground path. All four cases now return 2.584981 or
  refuse. This is the practitioner-facing "bring your own study" feature, so it was the highest-stakes
  place in the codebase for a silent unit error to live.
## Under-review ledger — where Claude was over-delegated

Running record of areas where AI-generated work was accepted without proportionate human review,
per the "use Claude smarter" directive. Convention: an item leaves the ledger only when a human
has either verified it against evidence or replaced the claim with a tested one.

| # | Area | What happened | Status / exit criterion |
|---|---|---|---|
| 1 | **Causal co-product consumption path** (`setup/03`, `_allocation_for` + link-site re-basis) | Most intricate code in the repo; written by Claude with confident comments; was exercised by **zero** validation cells | **CLOSED 2026-07-21** — the recycled-HDPE-flake case (`17664c37…`) consumes causal co-products from the MRF-sorting processes (non-uniform allocation grids) and reproduces openLCA **within 0.01%** on all 10 categories (`--vintage 2026` build, locked `validation_full_chain_results_2026.csv`). Path validated against a real case. |
| 2 | **Petroleum toxicity residual** | Accepted as "long feedback loop, within tolerance" without a proven mechanism | **CLOSED 2026-07-21.** Real root cause: `setup/03` did not honor USLCI's `isAvoidedProduct` flag, so the MSW-landfilling landfill-gas electricity *credit* was imported as a *burden* (sign-flipped). Petroleum toxicity is ~99% grid electricity, so that one exchange was the entire gap (openLCA landfilling −0.072 vs BW +0.072 on ecotox). Fix credits avoided-product exchanges → all 40 cells within 0.1% of openLCA, 41/41 pytest. The 2026-07-17 "pre-correction crude electricity" diagnosis was a **misattribution** (superseded); openLCA was correct throughout. |
| 3 | **Steel framed as a full-chain case** | 1-process bundle ⇒ full-chain ≡ direct; report presents 4 full-chain cases | **CLOSED 2026-07-23 by correcting the framing, not by adding a case.** The original error was presenting steel as a fourth *full-chain* case; it was in fact chosen early as the **Layer 1 (foreground-only) control** — an aggregated inventory characterized by both engines with no solve, so that any discrepancy could be localized to foreground (CFs/units/mapping) vs. background (parser/allocation/linking) vs. both. It fills that role and validates at 1.000 on all 10 categories. Docs now state that role rather than implying a missing full-chain case. Upgrading is also impossible: the census found USLCI's steel datasets are labelled unit processes but are strictly foreground, upstream pre-aggregated into a single inventory. Full-chain coverage rests on petroleum, corn, cement — plus HDPE flake on the 2026 build. |
| 4 | **TRACI CF hash printed, not enforced** (`setup/02`) | QC protocol calls for version logging; enforcement asymmetry vs `03b` never challenged | DONE 2026-07-07, committed `34cdef8` — both CF source files' SHA256 pinned + hard-enforced, mirroring `03b` |
| 5 | **mtime duplicate-zip precedence** (`setup/03`) | Same file rejects mtime for the conversion-table check but uses it for zip precedence; inconsistency not caught in audit | DONE 2026-07-07, committed `34cdef8` — precedence now by per-process `(version, lastChange)`, deterministic across machines |
| 6 | **"Validated" language** | Report/README language drifted from "engine parity" to "can be trusted" without the distinction being challenged | ADDRESSED 2026-07-07 — reworded to "engine parity on identical inputs" in README/VALIDATION_REPORT/CLAUDE.md; practitioner responsibility stated explicitly. Confirm wording holds on next read-through |
| 7 | **Unknown-unit passthrough** (`setup/03` `normalize()`) | "Never crash on import" default accepted without weighing silent-wrong-number risk for study use | DONE 2026-07-07, committed `34cdef8` — hard-stops before DB write by default; `ALLOW_UNIT_PASSTHROUGH=1` opt-in |
| 8 | **Harness ergonomics** (`validation/05`) | Mode switch requires editing a constant; direct mode covers petroleum only; accepted as-is | Mode switch DONE 2026-07-20, committed `b0eef0a` — `--mode {full_chain,direct}` CLI flag, default full_chain. **Coverage re-scoped 2026-07-23**, having been overstated as a gap: direct mode serves two purposes and they are covered differently. (a) *LCIA-math isolation* — verified on **two** processes, not one. Steel has no linked upstream, so its direct and full-chain scores are **bit-identical** (measured across all 10 categories), making its standard export a direct check on a second, independent 92-flow set. (b) *Foreground/background separation for a process that has a background* — petroleum only, and this is the part still OPEN. It matters because the split is lopsided and case-specific: petroleum's own emissions are <0.01% of its full-chain result, cement's run up to 76%. Without a cement or corn direct export, a hypothetical disagreement there could not be immediately attributed to foreground vs. solve. Lower value than (a) suggests — the CFs and flow list are shared across all cases and two processes already confirm them. Needs openLCA direct exports (kg-basis, `Direct impact contributions` sheet) |
| 9 | **Per-result completeness** (`general/04`) | Import-time diagnostics exist, but nothing at run time tells a user how complete *their* result is; gap not noticed until external critique | DONE 2026-07-13 — end-to-end on the data machine: harness re-run reproduced `validation_full_chain_results.csv` **byte-for-byte** (empty `git diff`) with the 4.4 `setup/03` changes in place, and a real petroleum `general/04` run emitted `validation_manifest.json` with the per-result completeness block populated from `uslci_db_provenance.json` (93 bio-unmatched / 409 tech-unlinked honestly reported for petroleum's solved chain) |
| 10 | **Report environment table accuracy** | Appendix A says Python 3.11.14 / conda `asphalt-lca`; actual replication env is 3.11.15 / `asp-lca-bw25` | ADDRESSED 2026-07-07 — Appendix A now reads Python 3.11.15, canonical env `fedefl-build-bw25`, with the legacy build env `asp-lca-bw25` noted (both records kept) |
| 11 | **`environment.yml` never installed as written** | The AI-authored env file pinned `fedelemflowlist@<commit>` on its own line *and* listed `lciafmt`, whose metadata declares an unpinned `fedelemflowlist` git URL. pip refuses two different direct-URL refs for one package, so `conda env create` fails with `ResolutionImpossible`. The "reproducible env" claim (and README Step 1) was never exercised end-to-end; the working env on the build machine was assembled another way. Release-blocker — a peer can't build the env from the repo. | FIXED 2026-07-09 (Claude-found, this session) — pin only `lciafmt`; `fedelemflowlist` pulled transitively, still lands `d2d690fb` (== current default-branch HEAD, so byte-identical to the validated build today). Two-pass hard-pin documented in `environment.yml` for when HEAD drifts. Verified: `lciafmt`-alone resolves cleanly to `d2d690fb` via `pip --dry-run` in a clean venv. CLOSED 2026-07-13 — `conda env create -f environment.yml` run from scratch on the data machine (fresh env name, hand-assembled env untouched): completed cleanly, `pip freeze` shows `fedelemflowlist @ d2d690fb` / `lciafmt @ 48d19af1` / exact bw2* pins, and the full 25-test pytest suite passes from the new env. (First attempt failed only on a full disk — machine-state, not the yml.) |

Reviewed and CLOSED items (keep for the record):

| # | Area | Resolution |
|---|---|---|
| C1 | Port integrity of validation package | All asset hashes match VALIDATION_LOG pins; harness + charts reproduce byte-for-byte from this repo (2026-07-04) |
| C2 | Config drift between parent and public repo | `config.py` verified byte-identical (2026-07-04) |

---

