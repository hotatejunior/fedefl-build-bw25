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
engine). See CLAUDE.md for the script table and run order.

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

- **Centralized `config.py`** — shared brightway identifiers in one place, imported everywhere.
- **Repo-root-anchored output paths** — scripts run from any directory; outputs land at the repo
  root via `config.REPO_ROOT`, explicit CLI paths resolve against CWD.
- **`source_data/` + `SOURCE_DATA_DIR`** — external assets resolve from one folder, overridable.
- **Rebuild-only `setup/00`** — ships a prebuilt `uslci_flow_conversions.json`; only re-parses the
  full USLCI zip on explicit `--rebuild`.
- **Auto-fetched electricity baseline** — `setup/03b` downloads the version-pinned baseline library
  from the Federal LCA Commons GitHub and verifies its SHA256 before use, removing a manual
  onboarding step. `--no-fetch` requires a local copy; `--library` points at your own.
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
- **New-process protocol hardening (2026-07-20)** — worked example: adding "Corn; at field" on a
  fresh machine hard-stopped on `p*km`, then (with passthrough toggled) `general/04` failed with
  `UnknownObject`. Root causes and fixes, all in `setup/03`:
  (1) a renamed bundle zip (`corn_at_field_<uuid>_<hash>.zip`) silently fails the
  `<uuid>_<hash>.zip` discovery glob — the target never imports and the first symptom is
  `UnknownObject` at run time. Now: any zip that looks like a bundle (openlca.json + `processes/`)
  but fails the naming pattern gets a loud WARNING at import; README documents "keep the original
  LCA Commons filename".
  (2) `p*km` (person-kilometre, passenger transport) was missing from `WITHIN_FP` — added as a
  reference unit (factor 1.0, like `t*km`). It rides in via passenger-car/aircraft transport flows
  in some 2026-07 bundles; consumed and produced in the same unit, so passthrough happened to be
  numerically harmless here — but only by luck. A follow-up census of the FULL USLCI zip found the
  entire database uses only 30 distinct unit strings; the two remaining gaps (`h` — Duration, a
  1.0-h service reference unit on chainsawing/skidding flows; `gal (Imp)` — 4.54609e-3 m3) were
  added too, so `WITHIN_FP` now covers 100% of the USLCI unit universe and the hard stop should be
  unreachable for USLCI-sourced bundles until USLCI itself introduces a new unit.
  (3) the unknown-unit hard stop now names example flows (name + UUID) per unit so the operator
  can see where the unit occurs; and `ALLOW_UNIT_PASSTHROUGH` is an **environment variable** again
  (a session's local `= 1` toggle had been committed in `f707205`, silently disabling the ledger #7
  hard stop for everyone — the edit-a-constant ergonomics bug striking again, cf. Phase 3.3).
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
- **The database toggle was CLI-only, and the miss message was dead code (2026-08-05).** `general/04`'s
  docstring offers the CONFIG block as the IDE/notebook workflow, but the `uslci-subset` /
  `uslci-full` choice existed only as `--database`. With no CONFIG entry, the nearest-looking
  constant is `USLCI_DB_DEFAULT` — which is a *naming anchor* for the provenance sidecar, not a
  toggle. Repointing it doesn't change the database; it makes each build look for the other's
  sidecar, so completeness reporting degrades to `available: false` while the run otherwise succeeds.
  Compounding it, the lookup's failure branch (`if target_act is None`) was unreachable: bw2data's
  `.get()` *raises* `UnknownObject` rather than returning `None`, so the carefully-worded "not found
  in '<db>'" message could never fire and a miss surfaced as a raw traceback from inside bw2data.
  Fixes: a real `USLCI_DATABASE` CONFIG entry (`--database` still overrides, and the banner reports
  which one set it); `USLCI_DB_DEFAULT` restored and commented as not-the-toggle; and the miss now
  catches `UnknownObject` to name the build searched, detect a bundle filename stem pasted in place
  of a UUID (`<uuid>_<release-hash>` — the easy mistake, and the fix is to strip it, not to switch
  databases), and say whether the other build has the process.
  The underlying confusion the banner addresses: "the bundle build" is not the nine validated
  targets, it is those nine **plus every upstream process their bundles shipped** — 391 activities.
  So a lookup can succeed on a process nobody deliberately imported (`Nitrogen fertilizer;
  production mix; at plant`, a corn dependency), which reads as the database toggle having silently
  done something. The startup banner now states the build, its activity count, what set it, the
  composition, the electricity vintage, and what else is built.
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
- **Presentation-layer fixes (2026-08-05).** Six defects found by the exploratory sweep, all in the
  layer the harness never touches. None changed a computed number; several changed what a number
  appeared to say.
  - **Credits rendered as burdens.** `impact_profile` plots `score.abs()` so the log axis works and
    marked no sign, so `Combustion of newspaper` — negative in all ten categories — drew ten ordinary
    bars. Negative bars are now a distinct colour, hatched, labelled with a leading minus on the
    unit, and counted in the axis label. The same shape as the `isAvoidedProduct` bug, in the chart
    layer.
  - **Wrong process's contributions, silently.** `general/06` fell back to the repo-root
    `lca_contributions.csv` with no check that it matched the results file, and it fired
    *automatically*: a zero-score run writes no contributions CSV, so the fallback engaged exactly
    when a result was empty and filled the directory, making failure look like success. Now refuses
    when the two files share no scenario, naming both sides.
  - **`scenario_comparison` had no supported input.** `04` opened the results CSV `"w"`, so every run
    overwrote the last and a multi-scenario CSV could only be built by hand — 0 of 43 chart runs
    produced the chart. New `--append` accumulates scenarios and replaces (not duplicates) a
    re-run scenario.
  - **Arbitrary near-zero baseline.** Every bar is a percentage of the first scenario, so an
    alphabetically-chosen baseline of 0.016 kg CO2-eq put the y-axis at 1e7. Now refused above a
    1000x ratio with the numbers named. `chart_units.py` guards incommensurable units; this guards
    incommensurable magnitudes.
  - **Unbounded legend.** At 43 scenarios the legend took ~85% of the figure and the palette cycled
    so entries shared colours. Capped at 8 with a note saying how many were omitted.
  - **Zero-score warning named the wrong cause.** It asserted a biosphere UUID mismatch "almost
    certainly", which was wrong for all three processes that tripped it. It now diagnoses from what
    the run can see: no exchanges at all (correct zero, e.g. USEEIO bridge stubs); no direct
    emissions (upstream cut); or — the case that exposed a third cause — every biosphere flow matched
    to FEDEFL but **uncharacterized by TRACI**. `Corn wet mill; gluten drying` emits 0.22 kg of
    `Particulate matter`, which maps cleanly to FEDEFL but has no TRACI 2.2 factor because TRACI
    characterizes PM2.5. The flow is carried, not dropped, and contributes exactly nothing. Matching
    and characterization are separate steps and only the first was ever surfaced.

---

## Release-plan record — 2026-07-04 to 2026-08-05

Moved here verbatim from `RELEASE_PLAN.md` on 2026-08-05, when the live plan split out to
[`ROADMAP.md`](ROADMAP.md). This is the dated record of how the repo got from an extracted engine to
a validated, packaged one: what was planned, what was done, what was struck, and why. Nothing below
is a current commitment — open work lives in ROADMAP.md.

Headings are one level deeper than they were in the original file. Dates, "UNCOMMITTED" markers and
target estimates are as written at the time; several are wrong in hindsight and are left that way.

### Phase 0 — Validation package ported (DONE 2026-07-04)

- Harness (`validation/05`, `validation/06`), locked result CSVs, `VALIDATION_LOG.md`,
  `QC_PROTOCOL.md`, and all four validation charts copied from the parent project.
- All source-data assets copied and **SHA256-verified against the pins in VALIDATION_LOG.md**
  (4 bundles, electricity baseline, full USLCI zip — 6/6 match).
- openLCA reference exports hashed for the first time; pins recorded in `validation/README.md`.
- **Replication proven:** harness re-run from this repo reproduced
  `validation_full_chain_results.csv` byte-for-byte (40/40 within 5%), and all four charts
  byte-for-byte. The "proof lives in a private repo" gap is closed.
- `validation/README.md` written — replication guide + asset manifest + honest-scope notes.

### Phase 1 — Repo hygiene & legal shareability (target: ~1–2 days of work)

> **Progress — 2026-07-07 session.** Items 4 (scrub + `PROJECT_NAME` rename) and 5 (`validation/` in
> pipeline tables) DONE. The rename `asphalt-lca` → `fedefl-build-bw25` was executed, the setup chain
> rebuilt, and the harness re-run — `validation_full_chain_results.csv` reproduced byte-for-byte
> (empty `git diff`), confirming the project name is non-load-bearing (logged in
> `validation/README.md`). Items 1 (LICENSE — MIT, © 2026 Harrison Watson) and 2 (CITATION.cff +
> "Maintainer, citation & reporting issues" README section, GitHub Issues as contact) DONE. Item 3
> (reference-data hosting) DECIDED — peers regenerate in their own openLCA; regeneration guide
> written; hash semantics clarified. (The optional release-asset mirror was dropped at tag time —
> see item 3.) **Phase 1 is complete.**

1. **LICENSE** (blocker — without it peers legally can't touch the code). Leading candidate for an
   openly-shared scientific tool: BSD-3-Clause or MIT; check license compatibility notes for
   fedelemflowlist/lciafmt (both EPA/public-domain-ish) before choosing.
2. **CITATION.cff + named maintainer/contact** in README ("report discrepancies here").
3. **Reference-data hosting — DECIDED 2026-07-07, simplified 2026-07-23.** The peer group
   **regenerates** the openLCA exports in their own openLCA 2.6. That is now the only path — the
   exports are not distributed at all. Decision:
   - **Regeneration is the primary path.** Wrote `validation/REGENERATING_REFERENCE_EXPORTS.md`
     (openLCA session steps + the exact sheet/cell format `05` parses, distilled from
     `VALIDATION_LOG.md`).
   - ~~**Optional mirror = GitHub Release asset** attached to `v0.1.0-beta`~~ **DROPPED
     2026-07-23 at tag time.** The mirror would have served a middle audience that doesn't exist:
     a casual reader does at most one spot-check in their own openLCA, and anything reaching formal
     peer review gets fully independent verification, where a maintainer-supplied export is
     irrelevant either way. Running the harness against the maintainer's own export only confirms
     the maintainer reported honestly — it produces no independent evidence. Regeneration is the
     only documented path. Reversible: the files can be attached to the existing release at any
     time if a peer actually wants them.
   - **Zenodo/DOI reserved for a citable release of the whole tool later**, not for these data files.
     **Git LFS rejected** (recurring cost + client-side `git lfs` friction for files nobody needs in
     the working tree).
   - **Hash semantics clarified** (`validation/README.md`): input hashes are a `shasum -c` replication
     gate; openLCA-export hashes pin only the maintainer's distributed copy and are **not**
     reproducible by regeneration (openLCA bakes in timestamps/ordering) — the harness tolerance
     check is the real openLCA-side gate.
   - The xlsx are now **git-ignored** (`validation/*.xlsx` in `.gitignore`); the 33 MB
     `validation/Petroleum_refining__at_refinery___US_kg_basis.xlsx` is no longer at risk of being
     committed.
4. **Scrub lurking `asphalt-lca` references** (inventory as of 2026-07-04):
   - `config.py` → `PROJECT_NAME = "asphalt-lca"` (the brightway project name).
   - `QC_PROTOCOL.md` title ("Asphalt LCA Pipeline").
   - `VALIDATION_REPORT.md` Appendix A (conda env name) and "Reproducing this" section (still says
     harness lives in the private parent — now false, update to point at `validation/`).
   - `validation/VALIDATION_LOG.md` — historical record; keep verbatim but add a one-line
     provenance header rather than rewriting history.
   - Decision needed on `PROJECT_NAME`: renaming invalidates every existing local brightway build.
     Correct sequencing is now cheap: rename → rebuild setup chain → re-run harness → confirm
     byte-identical CSV. Do it once, before first peer share, not after.
5. Update README/CLAUDE.md pipeline tables to include `validation/` as a first-class directory.

### Phase 2 — Messaging: honest claims + AI provenance (target: same week as Phase 1)

> **Progress — 2026-07-07 session.** All four items landed. (1) Validation claim reworded to
> "engine parity — reproduces openLCA on identical inputs" across README, VALIDATION_REPORT.md, and
> CLAUDE.md, with study-level responsibility explicitly left to the practitioner. (2) "Provenance &
> how this was built" section added to README. (3) The 2.0×→1.03 debugging arc + two disproven
> theories surfaced in a new VALIDATION_REPORT.md section. (4) Steel disclosed as direct-mode (1-proc
> bundle) in VALIDATION_REPORT.md's Honest-scope section.

1. **Reword the validation claim.** What exists is *engine verification against openLCA on
   identical inputs* (parity/benchmarking), not validation of study results against reality.
   Precise language: "reproduces openLCA to within X on identical inputs" — trust in mechanics,
   with study-level responsibility (allocation appropriateness, cutoffs, data vintage) explicitly
   left with the practitioner.
2. **Add a "Provenance & how this was built" README section** covering, in one tight passage:
   - AI-assisted development stated plainly (Claude wrote most implementation code).
   - Human accountability stated plainly: who defined the method decisions, directed the
     debugging, ran the openLCA sessions, and audited each script (per `QC_PROTOCOL.md`).
   - The governing principle from VALIDATION_LOG: **"VALIDATE, do not FIT"** — discrepancies were
     root-caused, never tuned away; two wrong theories were disproven and are kept on the record.
   - Where AI-authored code most warrants independent scrutiny (pointer to the ledger below).
   - The argument that provenance is orthogonal to correctness *because* the evidence chain
     (pinned inputs → reproducible harness → locked outputs) doesn't depend on who typed the code.
3. **Surface the debugging arc.** The 2.0×→1.03 causal-allocation story and the disproven-theory
   record in VALIDATION_LOG are the strongest trust artifacts in the project — currently invisible
   in the public-facing report. Add a short "how the bugs were found" section or link.
4. **Steel disclosure:** the steel bundle has 1 process; full-chain ≡ direct for it. Say so in
   VALIDATION_REPORT.md rather than presenting it as a fourth full-chain case.

### Phase 3 — Validation expansion (petroleum residual CLOSED 2026-07-21) (target: ~2–3 weeks, needs openLCA operator sessions)

1. **New test cases chosen to hammer allocation**, not to pad the count:
   - ~~≥1 process that **consumes a causal-allocation co-product**~~ **DONE 2026-07-21** — the
     recycled-HDPE-flake case covers it (MRF-sorting causal co-products), validated within 0.01% on a
     `--vintage 2026` build. Recycled-PET-flake is a second such case — **its reference export
     arrived 2026-07-23 and it validates at 0.00%** (was briefly blocked on that export).
   - ~~≥1 additional ECONOMIC multi-output case~~ **STRUCK 2026-07-23 — not achievable with USLCI
     data.** A census of all 1,342 processes in the full USLCI zip found 60 multi-output processes:
     27 ECONOMIC, 28 PHYSICAL, 5 CAUSAL. **All 29 processes that declare `ECONOMIC_ALLOCATION` have
     degenerate factors — every factor is exactly 0.0 or 1.0**, i.e. one product absorbs 100% of the
     burden and the co-products get zero. Zero processes in the database have two or more economic
     factors strictly between 0 and 1. (Verified directly: `Containerboard; at mill` declares
     `ECONOMIC_ALLOCATION` with `[containerboard 1.0, tall oil 0.0, turpentine 0.0]`.) The locked
     corn case already covers this degenerate path — its factors are `[0.0, 1.0]` too. Adding another
     one would consume an operator session and test nothing new. **The economic-allocation
     *arithmetic* is therefore untestable against USLCI**; state this as a scope limitation rather
     than pretending to cover it.
   - **≥1 additional PHYSICAL multi-output case — this is where the value is.** Unlike economic,
     physical allocation has real splits. Ranked candidates (all confirmed multi-output with genuine
     factor spreads and linked upstream providers):

     | Target | UUID | Products / split | Depth (techIn / linked / bio) | Why |
     |---|---|---|---|---|
     | **Chlorine; chlor-alkali electrolysis; at plant** | `a3e150d0-770e-4e2a-9b19-f7daa8cda38b` | NaOH 0.5453 / Cl₂ 0.4357 / H₂ 0.019 | 21 / 15 / 44 | **First pick.** The textbook multi-output chemical process, and the most balanced three-way physical split in USLCI. Chemicals = a genuinely new sector. |
     | **Hardboard; at hardboard plant** | `ca1d1dfa-fd3c-35f1-bea7-a037251deb04` | 7 products, 0.8634 → 0.0016 | 63 / 48 / 46 | **Second pick.** Deepest upstream of any candidate (48 linked providers) *and* the widest co-product fan. Stresses allocation breadth and the solve together. Wood products = new sector. |
     | Soybean oil; crude, degummed; at plant | `88aee762-4aa0-301f-b579-cca5d636aa0d` | meal 0.8051 / oil 0.1949 | 10 / 7 / 3 | Cheapest export; clean two-way split that is easy to hand-check. Good third if session time allows. |
     | Cellulosic fiberboard; uncoated; at plant | `cced9535-72f6-3b3a-b3c0-ce21b98eea2b` | 5 products, 0.961 → 0.0042 | 63 / 42 / 33 | Alternative to hardboard; similar depth, more lopsided split. |
     | Medium density fiberboard, MDF; at MDF mill | `4e01da4a-71e5-3d9d-93c3-0f0a230f2735` | 5 products, 0.844 → 0.0001 | 25 / 16 / 10 | Has a 1e-4 factor — useful for probing small-factor numerics. |

   - ~~Re-pull a **full-chain steel bundle** so steel actually tests the solve~~ **DROPPED
     2026-07-23 — steel is the foreground-only control and stays that way; ledger #3 closed by
     disclosure.** Steel billets was chosen early as the Layer 1 control (foreground only, no solve)
     precisely so discrepancies could be localized to foreground vs. background vs. both; it does
     that job and validates at 1.000. Upgrading it isn't possible anyway: the six large
     `Steel; * coil/plate/sections; at plant` processes look substantial (~740 exchanges) but carry
     **zero** technosphere inputs with a `defaultProvider` alongside ~690 elementary flows each —
     labelled unit processes while being strictly foreground, the whole upstream aggregated into one
     inventory. Re-pulling one would exercise the solve no more than the present bundle does.
     **Decision: do not pursue; state steel's role plainly** rather than shipping a thin case that
     technically solves.
   - For each: download bundle, pin hash, **check the bundle's grid vintage** (see
     `REGENERATING_REFERENCE_EXPORTS.md` → "Which baseline vintage"), openLCA export with the
     matching baseline mounted + US-avg provider linked, extend `TARGETS_FULL` *and*
     `EXPECTED_VINTAGE`, run, append to VALIDATION_LOG.
2. ~~**Demystify the petroleum 2.7% (ecotox/cancer/non-cancer) mechanistically.**~~ **RESOLVED
   2026-07-21** — not a background-aggregation effect and not a reference issue. `setup/03` was
   ignoring USLCI's `isAvoidedProduct` flag, so the landfill-gas electricity credit on the
   MSW-landfilling process was imported as a burden (sign-flipped). Since petroleum's toxicity is
   ~99% grid electricity, that one exchange was the whole gap. The fix (credit avoided-product
   exchanges) closed petroleum to 1.000 on all ten categories and moved all 40 cells within 0.1%.
   See DEVLOG / VALIDATION_REPORT. (The joint-solve hypothesis below was never needed.)
3. **Direct-mode coverage for all four (then all N) test cases**, and promote `VALIDATION_MODE`
   to a CLI flag so replicators don't edit source. (Code change — after Phase 2 lands.)

> **Progress — 3.2 superseded then reopened.** The 2.7% was root-caused 2026-07-17 without the
> joint-solve experiment: the reference export charged a pre-correction USLCI electricity value on
> the crude-oil processes; engine correct (DEVLOG, VALIDATION_LOG 2026-07-17). But the operator's
> re-export (tested 2026-07-20) reproduced the stale calculation *numerically identically*,
> disproving the "stale cached product-system state" mechanism — the stale charge is live state in
> that openLCA database. Next discriminating tests (operator): model-graph amount on the four
> crude-oil processes; regenerate in a **brand-new** openLCA database; duplicate-process check
> (VALIDATION_LOG 2026-07-20).
>
> **Progress — 2026-07-21 (RESOLVED, supersedes everything above).** The waste-treatment
> output-linking fix (commit 9592364) pulled the MSW-landfilling process into the locked chains,
> which briefly pushed petroleum toxicity to ~1.05 — and that *exposed* the real bug: `setup/03` did
> not honor `isAvoidedProduct`, so the landfill-gas electricity credit was imported as a burden. The
> fix (sign-flip avoided-product technosphere exchanges) closed petroleum to 1.000 on all ten
> categories; **all 40 cells now reproduce openLCA within 0.1%**, and 41/41 pytest pass. This
> **supersedes the ledger-#2 crude-electricity diagnosis** — there was no crude-electricity
> discrepancy; openLCA was correct throughout. Docs corrected across README, VALIDATION_REPORT,
> CLAUDE.md, DEVLOG, and `validation/`; the locked CSV and charts re-generated.

### Phase 4 — Engineering hardening (parallel with Phase 3; all items are code edits, deliberately deferred from the 2026-07-04 session)

> **Status: all of Phase 4 (4.1–4.5) is DONE and committed** on branch `release-prep-phase1-2` —
> 4.1/4.2/4.3/4.5 in `34cdef8`, 4.4 in `50a0ac7`, CI + the `--mode` flag in `b0eef0a`. The
> session-by-session notes below are kept as the work record; their "UNCOMMITTED" markers refer to the
> state at the time of writing, not today.
>
> **Progress — 2026-07-07 session (uncommitted at the time; landed in `34cdef8`).** Items 4.1,
> 4.2, 4.3, 4.5 DONE and verified; 4.4 paused at a scope decision (see handoff note below). After the
> setup/02 + setup/03 edits, the full setup chain was rebuilt and the harness re-run —
> `validation_full_chain_results.csv` still reproduces **byte-for-byte**, so all changes are
> behavior-preserving.
> - **4.1 pytest suite** — DONE. New `tests/` (18 tests, all pass): `test_foreground_importer.py`
>   (12 pure-unit validation tests, no brightway), `test_olca_library.py` (5 decode self-checks vs
>   the real baseline, data-gated), `test_validation_cement.py` (cement full-chain cell vs locked
>   CSV, data-gated). `pytest.ini` added; `pytest` added to `environment.yml` as a dev dep. Scoped
>   to NOT refactor the validated `setup/03` (decision: normalize()/_allocation_for() unit tests
>   deferred — they'd need setup/03 made import-safe). CI wired 2026-07-20 (landed in `b0eef0a`):
>   **CI has run green on every push since it was wired** (confirmed 2026-07-23), which also
>   exercises the `environment.yml` build end-to-end on a clean runner — the ledger-#11 failure mode.
>   `.github/workflows/ci.yml` — unit-tests job on every push/PR (pinned env from
>   `environment.yml`, data-gated tests self-skip); validation job manual-only
>   (workflow_dispatch), probes for `source_data/` and skips with a notice when absent.
> - **4.2 enforce TRACI CF hash** (`setup/02`) — DONE (closes ledger #4). Pins + enforces SHA256 of
>   both CF source files (base `traci_2.1.xlsx`, eutro file); hard-raises on mismatch. Also fixed a
>   latent bug: provenance logging looked up the wrong base-file cache name so it always printed
>   "not yet cached".
> - **4.3 content/version zip precedence** (`setup/03`) — DONE (closes ledger #5). Replaced mtime
>   with per-process `(version, lastChange)` precedence (100% present in USLCI JSON); zips iterated
>   in deterministic filename order. Verified the 2 colliding UUIDs still resolve to the same copy.
> - **4.5 unit-passthrough hard stop** (`setup/03`) — DONE (closes ledger #7). Unknown units now
>   hard-stop before the DB write (batched, reports all); opt-in `ALLOW_UNIT_PASSTHROUGH=1` to
>   permit with a warning. Current validated build hits zero unknown units, so default is safe.
> - **4.4 per-run auditability** (`general/04`) — NOT STARTED, paused at scope decision. See handoff.

> **Progress — 2026-07-09 session (uncommitted at the time; landed in `50a0ac7`).** 4.4
> IMPLEMENTED, resolving the paused scope decision toward **option B+** (the full per-*result*
> completeness view, not just DB-level totals). Closes ledger #9 pending human end-to-end run.
> - **`setup/03`** now persists per-process import diagnostics (bio matched/unmatched, tech
>   linked/external/unlinked/ambiguous, + `version`/`lastChange`) to `uslci_db_provenance.json`.
>   **Strictly additive** — it never touches `db_data`, so the harness must stay byte-for-byte
>   (mirrors the existing global counters into a per-process dict + one file write after the DB write).
> - **`general/04`** emits `validation_manifest.json` alongside the results: target + scenario,
>   package versions, DB identity (activity count + `modified`, with a stale-sidecar warning), the 10
>   methods/units, solved-system size, the 10 scores, and a **per-result completeness** block that
>   crosses this result's solved supply chain (`lca.dicts.activity`) against the sidecar. Aggregated
>   background (electricity baseline) is counted but flagged as not per-exchange auditable, not
>   penalized. `--manifest` / `--no-manifest` control it.
> - **New pure module `fedefl_bw25/run_manifest.py`** holds the completeness + assembly logic (no
>   brightway import) so it unit-tests without a built DB. **`tests/test_run_manifest.py`** (7 tests)
>   passes; full suite still green.
> - **NOT YET VERIFIED END-TO-END** (this machine has no `source_data/`): the byte-for-byte harness
>   re-run and a real `general/04` manifest emission must be done on the data machine. Because the
>   `setup/03` change is additive-only, byte-identical is expected — but must be *shown*.

> **Progress — 2026-07-13 session (data machine; uncommitted at the time, landed in `50a0ac7`).** 4.4 verification completed:
> harness re-run → `validation_full_chain_results.csv` byte-for-byte (empty `git diff`), closing
> ledger #9. Along the way a real UX failure surfaced: a fresh petroleum `general/04` run reads
> ~849× the locked values because the process's reference unit is m³, not kg (the locked validation
> is kg-basis; 849 kg/m³ is exactly the harness's `DENSITY_KG_M3` pin) — the author initially read
> the per-m³ numbers as wrong. Fix: the functional unit ("1 m3") is now stated in `general/04`'s
> console target block and results header, written as a `functional_unit` column in both
> `lca_results.csv` and `lca_contributions.csv`, recorded in the manifest's `target` block, and
> rendered in every `general/06` chart title/axis/legend (older CSVs without the column still plot).
> Verified: 25/25 pytest, end-to-end petroleum run + chart regeneration.

1. **pytest suite** wrapping the harness: cheapest first test = cement full-chain cell vs locked
   CSV; plus pure-unit tests for `normalize()`, `_allocation_for()`, `foreground_importer`
   validation, and `olca_library` decode self-checks. Then CI (GitHub Actions; unit tests always,
   validation job gated on data availability).
2. **Enforce (not just print) the TRACI CF file hash** in `setup/02` — same guard pattern as
   `03b`'s baseline fetch. A changed upstream CF file should stop the build, not decorate a log.
3. **Replace mtime-based duplicate-zip precedence** in `setup/03` with content hash or bundle
   version — mtime doesn't survive copies/clones; two users with identical files can build
   different databases.
4. **Per-run auditability in `general/04`** (the "inspect every result" feature): emit a
   provenance/completeness block alongside the results CSV — target UUID + bundle version, DB
   hashes, unlinked-technosphere and unmatched-biosphere counts *for the solved system*, cutoff
   share, package versions. VALIDATION_LOG already names the target artifact:
   `validation_manifest.json`. Documentation twin: a "How to audit a result" guide built from
   QC_PROTOCOL steps 5–6.
5. **Unit passthrough → hard stop by default** in `setup/03` (opt-in flag to permit passthrough
   with warning). An unconverted unit is a wrong number wearing a plausible one's clothes.

### Phase 5 — Slow-roll release

> **Progress — 2026-07-23.** PR #1 merged to `main`; `v0.1.0-beta` tagged at `6bc8e83` and published
> as a GitHub **pre-release**, no attachments (the reference-export mirror was dropped — see Phase
> 1.3). Verified on the merged tree before tagging: 68/68 pytest, replication gate PASS on both
> builds (40/40 cells 2025, 60/60 cells 2026), CI green. Items 2 and 3 below are outstanding.

1. ~~Tag `v0.1.0-beta`~~ **DONE 2026-07-23** — tagged and published as a pre-release.
2. Share with 2–3 trusted peers with a specific ask: "try to break the validation replication;
   try a study-shaped foreground CSV; tell me where you stopped trusting it."
3. Fold feedback into VALIDATION_LOG/DEVLOG (public record of external review — more trust
   capital). Wider release + Zenodo DOI after at least one external replication.

#### Loose timeline

| When | What |
|---|---|
| Week of Jul 6 | Phases 1 + 2 (docs, license, naming decision + rename/re-verify) |
| Weeks of Jul 13 + 20 | Phase 3 (new bundles + openLCA sessions + loop-cut experiment); Phase 4 items 1–3 in parallel |
| Week of Jul 27 | Phase 4 items 4–5; freeze, tag `v0.1.0-beta`, share with first peers |
| August | Peer feedback cycle → wider release decision + Zenodo |

---

### Phase 6 — Full-database mode (opened 2026-08-03, prototype working)

`USLCI_FULL_DB=1 setup/03` builds `uslci-full` (1,371 activities) beside the bundle build, so
`general/04 --database` can run any USLCI process without a rebuild. Verified 2026-08-03: all 1,371
processes solve (zero errors, zero non-finite), and the replication gate passes on `uslci-full`
itself, with absolute scores within 6.66e-14 of the bundle build.

Two defects surfaced and were fixed, both invisible under the bundle build:

- **Per-result completeness was a database-wide total.** `general/04` derived the supply chain from
  `lca.dicts.activity` — every technosphere column, reachable or not. Fixed via
  `run_manifest.select_supplied_keys()`. See DEVLOG.
- **42 processes scored a silent `0.0`.** `03b` discovered providers from the bundle glob only, so a
  full-DB build got 11 of the 17 baseline nodes it needs. Fixed by scanning the full zip too. See
  DEVLOG.

#### Candidate pieces (not scheduled)

1. **Hard-stop on ambiguous links** (`setup/03`), default on with `ALLOW_AMBIGUOUS_LINKS=1` to
   override — the `ALLOW_UNIT_PASSTHROUGH` pattern (ledger #7). An ambiguous link means the resolver
   had candidates and declined to choose: a build defect, never a data property. Passes clean on both
   builds today, so it can land without breaking anything.
2. **Run-time guard in `general/04`** — refuse or warn loudly when the target's own solved chain
   contains ambiguous links. Covers a database built with the override, or built before item 1.
3. **Explain a zero rather than printing it bare.** When a result is 0.0 across all ten categories and
   the target has non-zero inputs, name the cause: all its inputs are cutoffs. Measured on the current
   full DB this fires on 10 of 1,371 — the other 84 zeros are legitimately zero (82 have no exchanges
   at all, 2 have all-zero amounts). Not a stop; those 10 are correct answers, badly presented.
4. ~~**Process discovery** (`--search` / `--list` by name)~~ DONE 2026-08-05, ahead of the docs
   rewrite. Without it, step 3 of the practitioner tutorial would have read "open LCA Commons in a
   browser and copy the UUID out of the URL". `general/04 --search`
   now takes words in any order: the deciding case is `hdpe flake`, which appears nowhere in
   `Recycled postconsumer high-density polyethylene, HDPE, flake; at plant` as a substring and so
   returns nothing under a naive search. Hits in the pre-semicolon product segment rank first, which
   is what keeps `diesel` from burying its five fuels under 219 diesel-powered transport processes.
   Output names which build each hit is in and prints the exact command to run it. 22 tests. Took
   about an hour, not the 1–2 days estimated.
5. ~~**Tests for `03b`.**~~ DONE 2026-08-05 as a side effect of the extraction — its logic moved to
   `fedefl_bw25/setup_baseline.py` and became reachable, so `tests/test_setup_baseline.py` now covers
   provider discovery (the gap behind the 42 silent zeros) and the vintage config.
6. **Decide whether full-DB mode is a supported feature or stays opt-in.** Today: an env var, absent
   from the README, with the vintage forced to 2025 by the full zip.

Not in scope: *under-reporting* — a non-zero result that is too low because part of its inventory was
cut. That is endemic to LCA rather than a defect, and the per-result completeness block is the right
instrument for it now that it reports the actual supply chain.

#### Presentation & messaging defects — ALL FIXED 2026-08-05 (found by a 37-process exploratory sweep)

Ran 37 processes chosen for *weirdness* rather than representativeness — the units added that day,
dangling flow refs, unusual locations, magnitude extremes, negative results, degenerate zeros — plus
43 chart sets. Every one solved, and the runner agreed with an independent sweep 37/37 to 1e-9. The
defects were all in the layer the harness by construction never touches: presentation and messaging.
None would fail a gate or a test.

1. **FIXED — `impact_profile` rendered credits as burdens.** `general/06` takes `sub["score"].abs()` so the
   log scale works, and nothing marks the sign. `Combustion of newspaper` is negative in all ten
   categories; its chart shows ten positive bars labelled "Impact score per 1 kg". This is the
   `isAvoidedProduct` bug's shape — a credit read as a burden — moved into the chart layer. 6 of 43
   scenarios had ≥1 negative score; the locked tables carry 3 (steel billets). No shipped artifact is
   currently wrong (`charts/general/` is petroleum/corn/cement), and
   `validation/06_visualize_validation.py` plots ratios with no `abs()`, so it is unaffected.
2. **FIXED — `general/06` silently charted an unrelated process's contributions.** With no `--contributions`
   it falls back to the repo-root `lca_contributions.csv` and never checks it corresponds to the
   results file. Worse, it fires *automatically*: a zero-score process makes `general/04` write no
   contributions CSV, so the fallback engages exactly when the result is empty — and fills the
   directory, so the failure looks like success. Three of the 37 hit this. `general/04` gained a
   sidecar/database mismatch guard; `06` needs the equivalent.
3. **FIXED — the zero-score warning named the wrong cause.** "all 10 TRACI scores are 0.0 — this almost
   certainly indicates a biosphere UUID mismatch" fired on three processes whose zeros are
   legitimate (every technosphere input is a genuine cutoff) and whose biosphere mapping is fine.
4. **FIXED — `scenario_comparison`'s baseline was arbitrary and could be ~zero.** It plots "% of baseline"
   against whichever scenario sorts first. With `Alfalfa hay` (GWP 0.016 kg CO2-eq/kg) as
   denominator the y-axis reached **1e7** — fifty million percent — with no warning. `chart_units.py`
   already guards incommensurable *units*; nothing guards an incommensurable *magnitude*. Wants an
   explicit `--baseline`, and a refusal when the denominator is near zero.
5. **FIXED — legend and palette were unbounded.** At 43 scenarios the legend consumed ~85% of
   `normalized_profile`'s figure — plot squeezed to a strip, title rendered behind the legend box —
   and ~60% of `scenario_comparison`, overlapping the tick labels. Colors cycle past the palette
   length, so entries share swatches. Fine at the 2–4 scenarios the script was built for; degrades
   silently past that. Wants a top-N cap with an "N others omitted" note.

#### Foreground CSV unit handling — FIXED 2026-08-05

The most serious defect found this session, and the only one that produced wrong
*numbers* rather than wrong presentation. Found by probing the foreground path, which the
2026-08-05 sweep had not touched.

The `unit` column was **decorative on technosphere rows and only half-checked on biosphere rows**.
Amounts were used verbatim against the counterpart's reference unit, so on a 2-process test
foreground whose correct answer is 2.584981 kg CO2-eq (hand-verified against the CFs and the
providers' own scores):

| CSV row | Before | |
|---|---|---|
| `fishmeal, 0.2, kg` | 2.584981 | correct |
| `fishmeal, 0.2, MJ` | 2.584981 | nonsense unit, silently ignored |
| `fishmeal, 200, g` | **104.801** | same quantity — silent 1000× error |
| `CO2, 1500, g` | **1501.08** | same quantity — silent 1000× error |

The biosphere branch's flow-property check gave *false assurance*: it catches MJ-vs-kg but passes
g-vs-kg, which is the likelier mistake, because both are mass. The technosphere branch — the rows
that pull whole supply chains — had no check at all.

Fix: `foreground_importer.convert_to_ref_unit()` plus a `_UNIT_TO_REF` table mirroring `setup/03`'s
`WITHIN_FP`, applied to both branches. Same unit → untouched; same property → converted and
reported; different property or unknown unit → hard error, per ledger #7 ("an unconverted unit is a
wrong number wearing a plausible one's clothes"). All four cases above now return 2.584981 or refuse.
7 new unit tests; 82 pass. The importer docstring had described `unit` as inert and now states the
contract.

**Consequence for the docs:** the "linking a foreground CSV to USLCI" tutorial below was unwritable
before this — the honest instruction would have been "always use the provider's reference unit,
because nothing checks."

#### Parameterized processes — sized 2026-08-05: a frozen capability, not a wrong number

`SCHEMA_CROSSWALK.md` lists `amountFormula` as dropped and calls it "a known limitation for
parametric USLCI processes". Measured on v1.2026-06.0, that framing is more alarming than the
facts warrant, and less useful than the real one.

Exposure: **105** processes carry process-level parameters, **264** carry at least one exchange
`amountFormula` (1,477 exchanges) — 18.5% of the database.

It is **not** a correctness problem at default parameters, on two independent lines of evidence:

- **Four of the nine validated cases carry formulas** (petroleum, chlorine, HDPE, PET) and reproduce
  openLCA at 1.000. openLCA evaluates formulas; we use the stored `amount`. Matching to 1.000 *is*
  the proof that the stored amount already equals openLCA's evaluation. Petroleum's are constant
  arithmetic anyway — `6.5E-10+9.3E-19`, whose stored amount is the exact sum, with zero
  process-level parameters.
- **All 29 formula exchanges with a zero/absent stored amount evaluate to exactly 0** at their
  shipped parameter values (checked by restricted evaluation). They are configuration switches that
  ship off — `1.0*disinfect` with `disinfect = 0.0`, `0.78*HCl` with `HCl = 0.0` — plus six
  combustion processes whose fossil-CO2 term is `non_biomass_C_content * … = 0`. The stored 0 is the
  correct evaluation, not a dropped value.

What it actually costs is a **capability**: those parameters exist to be *changed*. The wastewater
treatment models ship with `disinfect`, `filter_include`, `chem_clarifier` and similar toggled off,
and an openLCA user would flip them to model a different treatment train. Here they are frozen at
whatever the export shipped, and nothing tells the user a knob exists. (Incidentally this explains
the negative combustion GWPs: paper products have `non_biomass_C_content = 0`, so their combustion
CO2 is entirely biogenic and uncounted, leaving only the avoided-electricity credit.)

Restate the crosswalk's limitation in these terms rather than as an unqualified gap.

##### Future development — parametric modelling (raised 2026-08-05, scoped 2026-08-05)

Priority set by HW: **parameterize the foreground and sweep parameter ranges quickly.** USLCI-side
(supply-chain) parameters are explicitly **pinned** — not merely deferred, because pinning them is
what makes fast sweeps possible.

**Why the pin is a design constraint, not a postponement.** With the background fixed, a foreground
result is *linear* in its exchange amounts: for a foreground process drawing `a_i` of background
activity `i` with direct emissions `e_j`,

    score = Σ a_i · S_i  +  Σ e_j · CF_j

where `S_i` is that background activity's own unit score — computed **once**. Every parameter
combination after that is a dot product, so thousands of scenarios cost about what one solve costs
today. This form was verified empirically on 2026-08-05: the two-process test foreground came to
`1.5·CF_CO2 + 0.02·CF_CH4 + 0.5·(subassembly) + 0.2·(fishmeal) = 2.584981`, matching the pipeline to
4e-9. Allow USLCI parameters in and `S_i` itself becomes variable, so every combination needs a real
solve (~10 s each) and interactivity is gone.

**CSV shape — declare in the inventory, override in a params file.** Two mechanisms with different
jobs:

- **Declare** parameters in the foreground CSV, since they are process-scoped and belong with the
  process. A new `exchange_type = parameter` row keeps the file self-contained and needs no schema
  change; the `amount` column then accepts a formula referencing declared parameters:

      Widget assembly,parameter,,,transport_km,250,km,false,,
      Widget assembly,technosphere,,<provider>,Transport,transport_km * 0.004,t*km,false,,

  Same formula language USLCI uses — pure arithmetic, `+ - * /`, no function calls — so one
  evaluator serves both sides.
- **Override** in a params file for sweeps, so varying a value never means editing the inventory. The
  CSV says *what varies*; the params file (or `--sweep transport_km=100:2000:20`) says *what to try*.

Rejected: a `params` column on exchange rows. It puts process-scoped data on every row of the
process and invites rows of the same process contradicting each other.

**Stages**

1. **Parameter declarations + formula-valued amounts in the foreground CSV.** `ast`-based evaluator
   (arithmetic only, topologically ordered — see the dependency note below), the `parameter` row
   type, and manifest recording of every declared value.
2. **Params file + `--sweep`.** Run N scenarios, accumulate with the `--append` added 2026-08-05,
   chart with the baseline/legend guards fixed the same day. This is the deliverable that makes a
   sweep a curve instead of a point.
3. **Fast path.** Precompute background unit scores and evaluate combinations as arithmetic. Do this
   when a sweep feels slow, not up front — correctness first, and stage 2 is already usable at ~10 s
   per scenario.
4. **PINNED — USLCI process and upstream parameters.** Overriding a shipped process parameter, and
   propagating a change to an activity *inside* a supply chain (which means substituting it for its
   consumers). Revisit only if a study actually needs it.

**What the USLCI data looks like**, measured on v1.2026-06.0 — relevant to stage 4 and to the shared
evaluator:

- 1,247 process parameters (max 28 on one process): 927 input, 320 calculated, of which **291
  reference another parameter** — so evaluation must be topologically ordered.
- 1 global parameter (`CH4_LEAKAGE_METHOD = 1.0`); process scope shadows global scope.
- Formulas are **pure arithmetic**: only `+ - * /`, zero function calls. An `ast` evaluator is small.
- The most common parameters are transport terms — `longHaul_dist`, `longHaul_mass_frac`,
  `rail_dist`, `lightTruck_dist`. "What if my supplier were 1,200 km away instead of 795?" is the
  question these would answer, which is worth remembering when stage 4 is reconsidered.
- **Gotcha:** a calculated parameter's stored `value` is unreliable. In
  `Transport; average mix; other chemical products`, `longHaul_kgkm` stores `0.0` while its formula
  `longHaul_dist * longHaul_mass_frac` evaluates to 601.925 — which is what the exchange's stored
  amount correctly holds. Evaluate formulas; never read a calculated parameter's value.

**Design constraints**

- **Evaluation must not be `eval`.** These formulas arrive from data files, and this project was
  already bitten by naive evaluation of a data string — brightway's geomapping `eval()`ing a location
  containing parentheses crashed the first full-DB build. `ast`-based, restricted to arithmetic and
  known symbols. (The one-off analyses behind this section used a restricted `eval` with no builtins;
  fine for a scan, not for a feature.)
- **Provenance, not prohibition.** The manifest must record every parameter value a result was
  computed at. A hard refusal to run parameterized against the validation build was considered and
  **rejected**: the harness runs `validation/05`, which never goes through `general/04`, so overrides
  cannot reach the locked comparison and there is no false-PASS mechanism to guard. The locked
  results are pinned to frozen inputs regardless (see the validation-build/working-build split
  above).

#### Packaging — the whole pipeline in one file (DONE 2026-08-05)

The workflow was "run five numbered scripts in order, then one per study." It is now "configure and
run the pipeline from a single script," which is the shape a programmable engine needs. Five
extractions, each a thin CLI left over a package function:

| Script | Was | Now | Package module |
|---|---|---|---|
| `general/04` | 750 | 181 | `run.py` — `run_lca()` → `LcaRun` |
| `setup/03b` | 474 | 69 | `setup_baseline.py` — `inject_baseline()` |
| `setup/03` | 1036 | 78 | `setup_uslci.py` — `import_uslci()` |
| `setup/00` | 255 | 85 | `setup_conversions.py` — `build_conversions()` |
| `setup/01` | 179 | 59 | `setup_biosphere.py` — `import_biosphere()` |
| `setup/02` | 301 | 56 | `setup_traci.py` — `import_traci()` |

Two conventions make the result scriptable rather than merely importable: package code **prints
nothing** (progress goes to an optional `log`) and **prompts for nothing** (`overwrite=` /
`confirm=`), and every step returns a build object instead of writing files, so a caller can assert
on what it got. `examples/full_pipeline.py` is the deliverable — setup through a multi-target study
in one file, idempotent, `--rebuild` to redo the chain.

**Method note, learned the hard way.** Reconstructing `03b`'s helpers from memory introduced three
silent bugs (a dropped `not exc.get("isInput")`, a location read from the wrong field, and
`reference_product_flow_uuid` taken from `ref.get("flow")` instead of `ref.get("id")` — load-bearing
for `03`'s relink). Every extraction since has been a **mechanical copy-and-indent** verified by
rebuild: `03` by dedenting the extracted body and diffing it against the original (105 differing
lines, 76 of them `print(` → `log(`, 29 exactly the intended changes), then rebuilding both
databases; `00` by rebuilding the conversion table to a scratch path (byte-identical apart from
`generated_at`); `01`/`02` by building into a **throwaway brightway project** and diffing it
cell-for-cell against the live one — 332,133 flows at an identical content hash, all 10 TRACI methods
identical in unit, CF count and CF content hash — which verifies the extraction without disturbing
the validated build. Gate PASS 60/60 after, locked CSVs byte-identical, 123 tests.

Left as-is deliberately: `_meta.flow_count` in the conversion table is off by one (the dict literal
is evaluated before `_meta` is assigned, so the `-1` undercounts). Carried over verbatim so a rebuild
still reproduces the committed table byte-for-byte; nothing reads the field — `setup/03` pins
`source_zip_sha256`. Worth fixing in a commit that regenerates the table.

##### Structural pass (DONE 2026-08-05)

Extraction moved the bulk without reshaping it: `import_uslci` was one 955-line function with helpers
nested inside it — a faithful move, not a good design. Split into named stages, longest function now
133 lines, and the same pass caught the two other outliers:

| | Was | Now |
|---|---|---|
| `import_uslci` | 955 | 124, over 9 named stages |
| `load_foreground_csv` | 233 | 75, over 5 |
| `import_traci` | 167 | 55, over 4 |

Nothing above 133 lines remains anywhere in the repo (`allocation_for`, at 133, is dense domain logic
that is already unit-tested and was left alone).

**Verification, since this touches the validated build.** Both databases were dumped
activity-by-activity and exchange-by-exchange (exact float reprs, deterministic order) before and
after: **identical**, 17,140 and 78,571 lines. Both provenance sidecars identical apart from their
timestamp. Gate PASS 60/60, locked CSVs unchanged. TRACI verified the same way — method units, CF
counts and CF content hashes identical across all 10. The foreground path reproduces the documented
2.584980824789767 kg CO₂-eq, including via the `200 g` unit-conversion route.

**The payoff is testability**, which was the argument for splitting rather than the pretext:
`tests/test_setup_uslci.py` adds 31 tests over logic that previously could only be exercised by
building a database — among them the Mg/mg case collision (a silent 1e9 error), the parenthesised
country name that crashed brightway's geomapping, version-based process precedence (ledger #5), and
the stale-hint-resolved-by-name path that keeps the grid linked across baseline vintages. 123 → 154
tests.

**One defect fixed in passing:** `load_foreground_csv` printed its warnings and parse summary
unconditionally, so `run_lca()` printed despite documenting that it doesn't — the output leaked past
the no-op log. It now takes `log=`, and `run.build_foreground` passes it through.

#### Documentation (scoped 2026-08-05)

Two methodology documents, three how-to tutorials. The split matters: the first two explain *what the
engine does and why*, the last three are task-shaped for a practitioner with their own study.

| Topic | Kind | Status |
|---|---|---|
| olca JSON-LD → brightway schema crosswalk | methodology | **DONE** — [`SCHEMA_CROSSWALK.md`](SCHEMA_CROSSWALK.md) |
| Allocation handling | methodology | **DONE** — [`ALLOCATION.md`](ALLOCATION.md) |
| Defining and changing the functional unit | how-to | **DONE** — [`HOWTO.md`](HOWTO.md) §1 |
| Linking a foreground CSV to USLCI | how-to | **DONE** — [`HOWTO.md`](HOWTO.md) §2 |
| Toggling foreground vs full-background calculation | how-to | **DONE with a stated gap** — [`HOWTO.md`](HOWTO.md) §3 |

All five written as of 2026-08-05. Every command and figure in `HOWTO.md` was executed against a
`uslci-full` build of v1.2026-06.0 before being written down. The remaining work on these is not
prose but the two gaps the guides had to disclose: `chart_units.py` guards commensurable units but
not commensurable magnitudes (§1), and `general/04` has no foreground-only mode for a USLCI process
target (§3) — both above.

- **Functional unit.** The machinery exists and is scattered: `general/04` states it in the target
  block and writes a `functional_unit` column, `chart_units.py` refuses to compare across units, and
  the harness re-bases to 1 kg. What's missing is the practitioner-facing account of *how to choose
  and change it* — including the petroleum m3-vs-kg trap that produced an 849× surprise (DEVLOG,
  2026-07-13), and the fact that a process's declared reference amount is often an arbitrary
  quantity rather than a sensible basis.
- **Foreground CSV → USLCI.** `fedefl_bw25/foreground_importer.py` validates the format and `general/04`
  builds a transient `FOREGROUND_DB` that links into the USLCI background, but the README gives it
  one example line. Needs the column contract, how a foreground row resolves to a USLCI provider,
  what happens when it doesn't, and a worked end-to-end example.
- **Foreground vs full background — the gap.** `--mode {direct,full_chain}` exists **only in
  `validation/05`**. `general/04` has no equivalent, so a practitioner cannot compute
  foreground-only impacts through the general runner. This can't be documented as a how-to until the
  capability is exposed in `general/`; document the concept, then add the flag (or decide the
  harness is the only place it belongs and say so).

---

### Under-review ledger — where Claude was over-delegated

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

### Session handoff — 2026-07-23 (historical record; superseded by 2026-08-03 below)

> **Read the newest handoff at the bottom of this file first.** The "NEXT STEP" list in this
> section was written *before* the openLCA sessions that happened later the same day, and all of
> its codeable items are now done. Kept verbatim as a record of what the session set out to do.

**Branch:** `release-prep-phase1-2`, 8 commits ahead of `main`, pushed and in sync with origin.
PR not yet opened — the maintainer will handle the PR. *(Merged as PR #1; branch deleted.)*

**Where the project stands:** Phases 0, 1, 2, and 4 are complete and committed. Phase 3.2 (the
petroleum residual) closed 2026-07-21 with ledgers #1 and #2. All 40 locked cells reproduce openLCA
within 0.1%; the HDPE case validates within 0.01% on a 2026-vintage build; 41/41 pytest pass.

**Environment (correct as of 2026-07-23):** `~/miniconda3/envs/fedefl-build-bw25/bin/python`. Call the
env's Python directly — `conda run` is unreliable here. The legacy `asp-lca-bw25` env no longer
exists; `VALIDATION_REPORT.md` Appendix A still names it as the *historical* env the original locked
results were computed in, which is correct and should stay.

**Done this session (2026-07-23) — documentation coherence pass:**
- `validation/VALIDATION_LOG.md` — added the two missing entries (2026-07-20 waste-linking / vintage
  selector / `Mg` fix, and 2026-07-21 `isAvoidedProduct` root cause + HDPE validation). The log had
  ended on a since-retracted conclusion. Superseded entries (07-15, 07-17, 07-20) now carry forward
  pointers; their text is kept verbatim per the log's append-only convention.
- `validation/README.md` — pinned the 2026-vintage assets (HDPE bundle, 2026 baseline, HDPE openLCA
  export, PET bundle), documented the HDPE replication path, explained why the 2025 baseline has two
  legitimate different hashes, and corrected the stale "mode requires editing a constant" note.
- `CLAUDE.md`, `README.md` — harness `--mode`/vintage description corrected; the debugging-arc
  narrative now runs through to 1.000 instead of stopping at ~1.03; HDPE surfaced as a fifth case.
- `RELEASE_PLAN.md` — Phase 4 and ledger rows marked committed with their commit refs; this handoff.
- `charts/general/*.png` regenerated on the current engine (they were two correctness fixes stale).

**NEXT STEP — Phase 3, which needs openLCA operator sessions (not codeable):**
~~1. **Chlorine; chlor-alkali electrolysis** (`a3e150d0-…`) — the real three-way physical split; top
   priority, tests allocation arithmetic nothing currently covers.~~
~~2. **Hardboard; at hardboard plant** (`ca1d1dfa-…`) — 7 co-products, deepest linked upstream in USLCI.~~
~~3. openLCA reference export for **recycled-PET flake** (`f7b7280d-…`) — bundle already on disk; a
   second exercise of the causal *consumption* path (the target process itself is `NO_ALLOCATION`).~~
4. **Direct-mode exports beyond petroleum** (ledger #8) — still open, re-scoped as lower-value.

**Items 1–3 were all completed later the same day** (operator sessions run 2026-07-23) along with
soybean oil. See `validation/VALIDATION_LOG.md` 2026-07-23 (later) and
`validation_full_chain_results_2026.csv`.

Struck after the 2026-07-23 census: a full-chain steel re-pull (ledger #3, closed by disclosure) and
an additional ECONOMIC case (degenerate throughout USLCI). Check each bundle's grid vintage before
the openLCA session — new LCA Commons pulls are 2026.

**Then Phase 5:** tag `v0.1.0-beta`, open the PR, share with 2–3 peers.

---

### Session handoff — 2026-08-03 (pick up here)

**Branch:** `full-db-import-prototype`, 2 commits ahead of `main`, tracking origin.
`release-prep-phase1-2` merged as PR #1 and was deleted; `v0.1.0-beta` is published.

**Where the project stands — Phases 0–4 complete, Phase 5 tagged.** Validation now covers **two
builds, ten process-cases, 100 category cells**:

| Build | Cases | Cells | Max deviation |
|---|---|---|---|
| 2025 | petroleum, corn, cement, steel | 40 | 0.1% |
| 2026 | steel, HDPE flake, PET flake, chlorine, hardboard, soy meal | 60 | 0.00077% |

Physical allocation is exercised on real splits (chlorine 3-way, hardboard 7-way, soy 2-way);
causal consumption on two cases (HDPE, PET). Economic allocation is **untestable against USLCI** —
all 29 declaring processes have degenerate 0/1 factors (census, 2026-07-23); this is a stated scope
limitation, not a gap to close.

**Environment on the current machine:** `/opt/miniconda3/envs/asp-lca-bw25/bin/python`
(Python 3.11.15, bw2data 4.7). Note the 2026-07-23 handoff above names
`~/miniconda3/envs/fedefl-build-bw25` and says `asp-lca-bw25` no longer exists — that is true of
the *data machine*, not this one. Both records are correct for their own host; check which machine
you are on before trusting either.

**On this branch (not yet in `main`):**
- `cd6aef6` — `SCHEMA_CROSSWALK.md`, the field-by-field USLCI JSON-LD → brightway `db_data` map;
  README tightened 16.9K→14.3K.
- `d7718e1` — opt-in `USLCI_FULL_DB=1` in `setup/03`: import the whole database from the full zip
  instead of per-process bundles, so `general/04` can run any of ~1,341 processes without a
  rebuild. 1,371 activities; 2025 cases still reproduce at 1.000. Also fixes a latent brightway
  crash (geomapping `eval()`s locations containing `(`).

**Open:** direct-mode exports beyond petroleum (ledger #8, low value); the full-DB prototype's
path to a supported feature; and the doc/narrative/technical-report work scoped 2026-08-03.

**Convention note for future sessions:** `validation/VALIDATION_LOG.md` is append-only and ends
with the newest entry — it is the authoritative record of what has actually been run. Handoff
sections in *this* file are point-in-time and can be overtaken within the same day. Check the log
before trusting plan prose.
