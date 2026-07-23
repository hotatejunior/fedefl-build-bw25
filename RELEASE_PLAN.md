# Release plan — from "extracted engine" to peer-shareable scientific tool

Working document, started 2026-07-04. Owner: HW. Goal: share this repo with a small circle of
trusted LCA practitioners as a **positive example of AI-assisted analytical code** — meaning the
trust case must rest on reproducible evidence and documented human accountability, not on polish.

The bar: a skeptical peer can (a) legally use the code, (b) replicate the validation from what the
repo gives them, (c) see exactly what was AI-authored vs human-directed and audited, and (d) audit
any individual result they compute, not just the locked test cases.

---

## Phase 0 — Validation package ported (DONE 2026-07-04)

- Harness (`validation/05`, `validation/06`), locked result CSVs, `VALIDATION_LOG.md`,
  `QC_PROTOCOL.md`, and all four validation charts copied from the parent project.
- All source-data assets copied and **SHA256-verified against the pins in VALIDATION_LOG.md**
  (4 bundles, electricity baseline, full USLCI zip — 6/6 match).
- openLCA reference exports hashed for the first time; pins recorded in `validation/README.md`.
- **Replication proven:** harness re-run from this repo reproduced
  `validation_full_chain_results.csv` byte-for-byte (40/40 within 5%), and all four charts
  byte-for-byte. The "proof lives in a private repo" gap is closed.
- `validation/README.md` written — replication guide + asset manifest + honest-scope notes.

## Phase 1 — Repo hygiene & legal shareability (target: ~1–2 days of work)

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

## Phase 2 — Messaging: honest claims + AI provenance (target: same week as Phase 1)

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

## Phase 3 — Validation expansion (petroleum residual CLOSED 2026-07-21) (target: ~2–3 weeks, needs openLCA operator sessions)

1. **New test cases chosen to hammer allocation**, not to pad the count:
   - ~~≥1 process that **consumes a causal-allocation co-product**~~ **DONE 2026-07-21** — the
     recycled-HDPE-flake case covers it (MRF-sorting causal co-products), validated within 0.01% on a
     `--vintage 2026` build. Recycled-PET-flake is a second such case, built and solving, but blocked
     on an openLCA reference export.
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

## Phase 4 — Engineering hardening (parallel with Phase 3; all items are code edits, deliberately deferred from the 2026-07-04 session)

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
> - **New pure module `general/run_manifest.py`** holds the completeness + assembly logic (no
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

## Phase 5 — Slow-roll release

> **Progress — 2026-07-23.** PR #1 merged to `main`; `v0.1.0-beta` tagged at `6bc8e83` and published
> as a GitHub **pre-release**, no attachments (the reference-export mirror was dropped — see Phase
> 1.3). Verified on the merged tree before tagging: 68/68 pytest, replication gate PASS on both
> builds (40/40 cells 2025, 60/60 cells 2026), CI green. Items 2 and 3 below are outstanding.

1. ~~Tag `v0.1.0-beta`~~ **DONE 2026-07-23** — tagged and published as a pre-release.
2. Share with 2–3 trusted peers with a specific ask: "try to break the validation replication;
   try a study-shaped foreground CSV; tell me where you stopped trusting it."
3. Fold feedback into VALIDATION_LOG/DEVLOG (public record of external review — more trust
   capital). Wider release + Zenodo DOI after at least one external replication.

### Loose timeline

| When | What |
|---|---|
| Week of Jul 6 | Phases 1 + 2 (docs, license, naming decision + rename/re-verify) |
| Weeks of Jul 13 + 20 | Phase 3 (new bundles + openLCA sessions + loop-cut experiment); Phase 4 items 1–3 in parallel |
| Week of Jul 27 | Phase 4 items 4–5; freeze, tag `v0.1.0-beta`, share with first peers |
| August | Peer feedback cycle → wider release decision + Zenodo |

---

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
| 8 | **Harness ergonomics** (`validation/05`) | Mode switch requires editing a constant; direct mode covers petroleum only; accepted as-is | Mode switch DONE 2026-07-20, committed `b0eef0a` — `--mode {full_chain,direct}` CLI flag, default full_chain; verified byte-for-byte in full_chain and a clean petroleum direct run. Direct-mode coverage beyond petroleum still OPEN (needs openLCA direct exports) |
| 9 | **Per-result completeness** (`general/04`) | Import-time diagnostics exist, but nothing at run time tells a user how complete *their* result is; gap not noticed until external critique | DONE 2026-07-13 — end-to-end on the data machine: harness re-run reproduced `validation_full_chain_results.csv` **byte-for-byte** (empty `git diff`) with the 4.4 `setup/03` changes in place, and a real petroleum `general/04` run emitted `validation_manifest.json` with the per-result completeness block populated from `uslci_db_provenance.json` (93 bio-unmatched / 409 tech-unlinked honestly reported for petroleum's solved chain) |
| 10 | **Report environment table accuracy** | Appendix A says Python 3.11.14 / conda `asphalt-lca`; actual replication env is 3.11.15 / `asp-lca-bw25` | ADDRESSED 2026-07-07 — Appendix A now reads Python 3.11.15, canonical env `fedefl-build-bw25`, with the legacy build env `asp-lca-bw25` noted (both records kept) |
| 11 | **`environment.yml` never installed as written** | The AI-authored env file pinned `fedelemflowlist@<commit>` on its own line *and* listed `lciafmt`, whose metadata declares an unpinned `fedelemflowlist` git URL. pip refuses two different direct-URL refs for one package, so `conda env create` fails with `ResolutionImpossible`. The "reproducible env" claim (and README Step 1) was never exercised end-to-end; the working env on the build machine was assembled another way. Release-blocker — a peer can't build the env from the repo. | FIXED 2026-07-09 (Claude-found, this session) — pin only `lciafmt`; `fedelemflowlist` pulled transitively, still lands `d2d690fb` (== current default-branch HEAD, so byte-identical to the validated build today). Two-pass hard-pin documented in `environment.yml` for when HEAD drifts. Verified: `lciafmt`-alone resolves cleanly to `d2d690fb` via `pip --dry-run` in a clean venv. CLOSED 2026-07-13 — `conda env create -f environment.yml` run from scratch on the data machine (fresh env name, hand-assembled env untouched): completed cleanly, `pip freeze` shows `fedelemflowlist @ d2d690fb` / `lciafmt @ 48d19af1` / exact bw2* pins, and the full 25-test pytest suite passes from the new env. (First attempt failed only on a full disk — machine-state, not the yml.) |

Reviewed and CLOSED items (keep for the record):

| # | Area | Resolution |
|---|---|---|
| C1 | Port integrity of validation package | All asset hashes match VALIDATION_LOG pins; harness + charts reproduce byte-for-byte from this repo (2026-07-04) |
| C2 | Config drift between parent and public repo | `config.py` verified byte-identical (2026-07-04) |

---

## Session handoff — 2026-07-23 (pick up here)

**Branch:** `release-prep-phase1-2`, 8 commits ahead of `main`, pushed and in sync with origin.
PR not yet opened — the maintainer will handle the PR.

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
1. **Chlorine; chlor-alkali electrolysis** (`a3e150d0-…`) — the real three-way physical split; top
   priority, tests allocation arithmetic nothing currently covers.
2. **Hardboard; at hardboard plant** (`ca1d1dfa-…`) — 7 co-products, deepest linked upstream in USLCI.
3. openLCA reference export for **recycled-PET flake** (`f7b7280d-…`) — bundle already on disk; a
   second exercise of the causal *consumption* path (the target process itself is `NO_ALLOCATION`).
4. **Direct-mode exports beyond petroleum** (ledger #8).

Struck after the 2026-07-23 census: a full-chain steel re-pull (ledger #3, closed by disclosure) and
an additional ECONOMIC case (degenerate throughout USLCI). Check each bundle's grid vintage before
the openLCA session — new LCA Commons pulls are 2026.

**Then Phase 5:** tag `v0.1.0-beta`, open the PR, share with 2–3 peers.
