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
  control over exchange-direction detection.
- **FEDEFL UUIDs as the universal key.** Every biosphere flow — in the brightway DB, the TRACI
  method, and the USLCI data — is keyed by FEDEFL UUID. Linking is deterministic; no name-matching.
- **openLCA library interoperability.** `setup/olca_library.py` decodes openLCA's pre-aggregated
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
  openLCA parity for a *consumed* causal co-product is still pending a Phase 3.1 test case (the
  USLCI recycling/MRF sector has real consumers — recovered HDPE/PET from sorting processes).
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
  allocation). 67 such links now form across the bundles; HDPE GWP 0.842 → 1.036. NOTE: this changes
  every case that sends waste to treatment, including the locked ones (petroleum/cement/corn) — the
  locked CSV and `tests/test_validation_cement.py` expectations must be re-established once the
  openLCA reference exports are regenerated with waste treatment included.

Outcome: all 40 category × process cells validate within 5% of openLCA (35/40 within 1%). See
`VALIDATION_REPORT.md`.

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
