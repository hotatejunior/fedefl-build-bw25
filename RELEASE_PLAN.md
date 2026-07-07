# Release plan — from "extracted engine" to peer-shareable scientific tool

Working document, started 2026-07-04. Owner: HW. Goal: share this repo with a small circle of
trusted LCA practitioners as a **positive example of AI-assisted analytical code** — meaning the
trust case must rest on reproducible evidence and documented human accountability, not on polish.

The bar: a skeptical peer can (a) legally use the code, (b) replicate the validation from what the
repo gives them, (c) see exactly what was AI-authored vs human-directed and audited, and (d) audit
any individual result they compute, not just the four locked test cases.

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
> (reference-data hosting) DECIDED — peers regenerate in their own openLCA; hosted files are an
> optional GitHub Release mirror; regeneration guide written; hash semantics clarified. **All of
> Phase 1 is now complete except uploading the release-asset mirror at tag time (Phase 5).**

1. **LICENSE** (blocker — without it peers legally can't touch the code). Leading candidate for an
   openly-shared scientific tool: BSD-3-Clause or MIT; check license compatibility notes for
   fedelemflowlist/lciafmt (both EPA/public-domain-ish) before choosing.
2. **CITATION.cff + named maintainer/contact** in README ("report discrepancies here").
3. **Reference-data hosting — DECIDED 2026-07-07.** The peer group will **regenerate** the openLCA
   exports in their own openLCA 2.6, so the hosted files are a *convenience mirror*, not the
   canonical object. Decision:
   - **Regeneration is the primary path.** Wrote `validation/REGENERATING_REFERENCE_EXPORTS.md`
     (openLCA session steps + the exact sheet/cell format `05` parses, distilled from
     `VALIDATION_LOG.md`).
   - **Optional mirror = GitHub Release asset** attached to `v0.1.0-beta` (free, no client tooling,
     tag-locked). Not yet uploaded; do at tag time (Phase 5).
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

## Phase 3 — Validation expansion & demystifying the 2.7% (target: ~2–3 weeks, needs openLCA operator sessions)

1. **New test cases chosen to hammer allocation**, not to pad the count:
   - ≥1 process that **consumes a causal-allocation co-product** (the importer's most complex
     path; currently zero validation coverage — the importer itself warns this path is
     "UNDER-supported" if hit). Candidate hunting ground: paper/pulp mills, petroleum coproducts.
   - ≥1 additional ECONOMIC and ≥1 PHYSICAL multi-output case from new sectors (chemicals,
     plastics, wood products).
   - Re-pull a **full-chain steel bundle** so steel actually tests the solve.
   - For each: download bundle, pin hash, openLCA export with baseline mounted + US-avg provider
     linked (the documented product-system setup), extend `TARGETS_FULL`, run, append to
     VALIDATION_LOG.
2. **Demystify the petroleum 2.7% (ecotox/cancer/non-cancer) mechanistically.** Working
   hypothesis: the electricity baseline enters brightway as a *pre-solved aggregated M column*
   (one lumped node), while openLCA solves the mounted library jointly — any feedback loop between
   electricity's upstream and the USLCI foreground is cut on the brightway side. Testable without
   new data: `olca_library.py` already decodes the library's full A and B matrices, so brightway
   can alternatively import the library *disaggregated* (its 771 processes as real activities) and
   solve jointly. If the residual collapses, the mechanism is proven and becomes a documented,
   quantified design trade-off (fast lumped background vs exact joint solve — possibly a user
   flag). If it doesn't, the hypothesis is disproven and that goes on the record too.
3. **Direct-mode coverage for all four (then all N) test cases**, and promote `VALIDATION_MODE`
   to a CLI flag so replicators don't edit source. (Code change — after Phase 2 lands.)

## Phase 4 — Engineering hardening (parallel with Phase 3; all items are code edits, deliberately deferred from the 2026-07-04 session)

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

1. Tag `v0.1.0-beta` once Phases 1–2 land and Phase 4.1 (minimal pytest) exists.
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
| 1 | **Causal co-product consumption path** (`setup/03`, `_allocation_for` + link-site re-basis) | Most intricate code in the repo; written by Claude with confident comments; exercised by **zero** validation cells (no bundle consumes a causal co-product) | OPEN — Phase 3.1: add a covering test case; until then treat path as unvalidated |
| 2 | **Petroleum 2.7% residual** | Accepted as "long feedback loop, within tolerance" without a proven mechanism | OPEN — Phase 3.2 experiment (joint-solve vs M-injection) |
| 3 | **Steel framed as a full-chain case** | 1-process bundle ⇒ full-chain ≡ direct; report presents 4 full-chain cases | Disclosure DONE — `validation/README.md` (2026-07-04) + `VALIDATION_REPORT.md` Honest-scope (2026-07-07). Full-chain re-pull still OPEN (Phase 3.1) |
| 4 | **TRACI CF hash printed, not enforced** (`setup/02`) | QC protocol calls for version logging; enforcement asymmetry vs `03b` never challenged | OPEN — Phase 4.2 |
| 5 | **mtime duplicate-zip precedence** (`setup/03`) | Same file rejects mtime for the conversion-table check but uses it for zip precedence; inconsistency not caught in audit | OPEN — Phase 4.3 |
| 6 | **"Validated" language** | Report/README language drifted from "engine parity" to "can be trusted" without the distinction being challenged | ADDRESSED 2026-07-07 — reworded to "engine parity on identical inputs" in README/VALIDATION_REPORT/CLAUDE.md; practitioner responsibility stated explicitly. Confirm wording holds on next read-through |
| 7 | **Unknown-unit passthrough** (`setup/03` `normalize()`) | "Never crash on import" default accepted without weighing silent-wrong-number risk for study use | OPEN — Phase 4.5 |
| 8 | **Harness ergonomics** (`validation/05`) | Mode switch requires editing a constant; direct mode covers petroleum only; accepted as-is | OPEN — Phase 3.3 |
| 9 | **Per-result completeness** (`general/04`) | Import-time diagnostics exist, but nothing at run time tells a user how complete *their* result is; gap not noticed until external critique | OPEN — Phase 4.4 |
| 10 | **Report environment table accuracy** | Appendix A says Python 3.11.14 / conda `asphalt-lca`; actual replication env is 3.11.15 / `asp-lca-bw25` | ADDRESSED 2026-07-07 — Appendix A now reads Python 3.11.15, canonical env `fedefl-build-bw25`, with the legacy build env `asp-lca-bw25` noted (both records kept) |

Reviewed and CLOSED items (keep for the record):

| # | Area | Resolution |
|---|---|---|
| C1 | Port integrity of validation package | All asset hashes match VALIDATION_LOG pins; harness + charts reproduce byte-for-byte from this repo (2026-07-04) |
| C2 | Config drift between parent and public repo | `config.py` verified byte-identical (2026-07-04) |
