# Regenerating the openLCA reference exports

The parity harness (`05_validate_uslci.py`) diffs brightway's LCIA scores against reference exports
computed in **openLCA**. This guide tells you how to reproduce those exports yourself, so you don't
have to trust — or download — the maintainer's copies.

> **Read this first — your files will NOT byte-match the maintainer's.** openLCA bakes export
> timestamps, row ordering, and formatting into every `.xlsx`, so a numerically-perfect regeneration
> still produces a *different file hash*. **The replication gate is not a hash match — it is the
> harness agreeing within tolerance** (`05_validate_uslci.py` compares the numeric impact cells and
> flags any BW/OL ratio outside ±5%). The SHA256s in [`README.md`](README.md) pin the maintainer's
> *distributed* copies for download integrity; they are **not** a check you can run against your own
> openLCA output. See [`README.md`](README.md) → "Data assets and pinned hashes".

---

## What you are producing

Ten Excel exports — one per validation case. Nine feed full-chain mode; one feeds direct mode.
The vintage column is the electricity baseline that must be mounted (see below).

| File (exact name the harness expects) | Mode | Vintage | Sheet the harness reads | Lives in |
|---|---|---|---|---|
| `Petroleum_refining__at_refinery___US__AVG_ELEC_SELECTION.xlsx` | full-chain | 2025 | `Impacts` | `source_data/` |
| `Corn__whole_plant__at_field___US_AVG_ELEC_SELECTION.xlsx` | full-chain | 2025 | `Impacts` | `source_data/` |
| `Portland_cement__at_plant___US__US_AVG_ELEC_SELECTION.xlsx` | full-chain | 2025 | `Impacts` | `source_data/` |
| `Steel__billets__at_plant___RNA_results.xlsx` | full-chain | either | `Impacts` | `source_data/` |
| `Recycled_postconsumer_high_density_polyethylene__HDPE__flake__at_plant___RNA_July_20.xlsx` | full-chain | 2026 | `Impacts` | `source_data/` |
| `Recycled_postconsumer_polyethylene_terephthalate__PET__flake__at_plant___RNA.xlsx` | full-chain | 2026 | `Impacts` | `source_data/` |
| `Chlorine__chlor_alkali_electrolysis__at_plant___US.xlsx` | full-chain | 2026 | `Impacts` | `source_data/` |
| `Hardboard__at_hardboard_plant___RNA.xlsx` | full-chain | 2026 | `Impacts` | `source_data/` |
| `Soybean_oil__crude__degummed__at_plant___RNA.xlsx` | full-chain | 2026 | `Impacts` | `source_data/` |
| `Petroleum_refining__at_refinery___US_kg_basis.xlsx` | direct | 2025 | `Direct impact contributions` | `validation/` |

Filenames matter: they are hardcoded in `TARGETS_FULL` / `TARGETS_DIRECT` at the top of
`05_validate_uslci.py`. Either save with these exact names, or edit those dicts / set
`SOURCE_DATA_DIR` to point at your own layout.

## Prerequisites

- **openLCA 2.6** (the version the locked results were computed against).
- The **US Electricity Baseline library** — **at the vintage the bundle itself references.** For the
  four locked cases that is `v1.2025-06.0`. It is **not** automatically the right answer for a new
  bundle; see "Which baseline vintage" immediately below. Hashes pinned in
  [`README.md`](README.md) / [`VALIDATION_LOG.md`](VALIDATION_LOG.md).
- The **USLCI supply-chain bundles** (openLCA JSON-LD), each hash-pinned in
  [`README.md`](README.md). These are the *same* bundles brightway imports — feeding both engines
  identical JSON-LD is the whole point of the comparison.

### Which baseline vintage — check, don't assume

The US-average grid node is **named identically across baseline releases but carries a different
UUID per vintage**. A bundle hardcodes one of them, and mounting the other in openLCA silently
compares against a grid the bundle never referenced.

| Baseline release | US-average grid UUID |
|---|---|
| `v1.2025-06.0` | `7068192a-999c-39b6-bf66-234a294bdf92` |
| `v1.2026-06.0` | `75d4be66-…` |

**Check any bundle before you open openLCA.** `setup/03b` classifies every bundle it finds and prints
the verdict per file, so the quickest check is to run it against your bundle directory:

```bash
python setup/03b_import_electricity_baseline.py --bundle-dir source_data
#   …_a900b507….zip: 11 external provider(s) referenced  [grid vintage: 2026]
```

Or read it straight out of the JSON:

```bash
unzip -p source_data/<bundle>.zip "processes/*.json" | grep -o -e 7068192a -e 75d4be66 | sort | uniq -c
```

Whichever UUID **dominates** is the vintage that bundle wants; mount that baseline. Note that
presence alone is not the test — a handful of stray references to the other vintage is normal. The
four locked bundles cite `7068192a` ~80 times and `75d4be66` exactly once, so "does it mention the
2026 node?" would wrongly answer yes for every bundle in the repo.

> **New bundles from LCA Commons are 2026-vintage.** Everything in the July-2026 drop — recycled HDPE
> flake, recycled PET flake, "Corn; at field" — references `75d4be66` **exclusively** (91, 91, and 86
> references, zero to 2025). If you are pulling a fresh bundle today, expect to mount
> `v1.2026-06.0`, not the 2025 baseline the four locked cases use.

On the brightway side you normally don't have to specify anything: `setup/03b` **auto-detects** the
vintage from the bundles' own references and defaults to it. **A build injects exactly one vintage**,
stamps it onto the database, and `05_validate_uslci.py` hard-skips any case whose expected vintage
doesn't match the build — so a mismatch shows up as a `SKIP:` line, not a wrong number.

If `source_data/` holds bundles from *both* releases (as it does once you add 2026-drop cases
alongside the four locked ones), `03b` stops and prints which bundles want which vintage: one build
cannot satisfy both. Build and validate one group, then rebuild for the other —
`--vintage {2025|2026}` selects explicitly. Add each new case's expected vintage to
`EXPECTED_VINTAGE` in `05_validate_uslci.py` when you add it to `TARGETS_FULL`.

## The openLCA session — per test case

Do this once per bundle. The controlling decision is **"Path 2 — baseline on both sides"**
(see [`VALIDATION_LOG.md`](VALIDATION_LOG.md), "openLCA blocker" and "Governing principle"): openLCA
keeps the Electricity Baseline library mounted and computes *with* electricity upstream, and brightway
matches by importing that same baseline via `setup/03b`.

1. **Fresh database.** Create a new, empty openLCA database. Do **not** reuse a full-USLCI project —
   a stray extra provider would re-confound the comparison. Import **only** the one bundle's JSON-LD.
2. **Keep the Electricity Baseline library mounted. Do NOT delete it.** Deleting the library orphans
   ~2,500 flows (it supplies the physical flow properties Mass/Energy/Volume that the per-process
   bundle does not ship) and the database won't calculate. This was tested and abandoned — leave the
   library in place.
3. **Preferred process type → Unit process.** (A no-op for these bundles' 8 aggregated background
   materials, but set it explicitly to match brightway's disaggregated solve.)
4. **Link the US-average grid provider** for petroleum, corn, and cement. Each has a direct
   `Electricity, AC, 120 V` input that ships with **no default provider** (a product-system setup gap
   on the openLCA side). Manually link it to the US average consumption mix, provider UUID
   **`7068192a-999c-39b6-bf66-234a294bdf92`** — note this is the **2025** node; for a 2026-vintage
   bundle link `75d4be66-…` instead (see "Which baseline vintage" above). This is what the
   `_AVG_ELEC_SELECTION` in the filenames records. **Steel has no electricity input — skip this step
   for steel.**
5. **Build the product system for the target process** — *after* step 4, so the manual provider link
   is captured in the system. Target UUIDs:
   - Petroleum refining: `0aaf1e13-5d80-37f9-b7bb-81a6b8965c71`
   - Corn; whole plant: `11256034-2355-3add-ade9-59983025dded`
   - Portland cement: `62993671-574c-3fc5-b66a-6be3bb21ad3d`
   - Steel; billets: `ac54bc7d-5db5-3b4f-9175-5dd02f678312`
   - Chlorine; chlor-alkali: `a3e150d0-770e-4e2a-9b19-f7daa8cda38b`
   - Hardboard: `ca1d1dfa-fd3c-35f1-bea7-a037251deb04`
   - Soybean oil (reference product is **Soy meal; at plant**, not the oil): `88aee762-4aa0-301f-b579-cca5d636aa0d`
   - Recycled HDPE flake: `17664c37-72c0-4813-a4b9-93f962962c63`
   - Recycled PET flake: `f7b7280d-f372-3a4b-86cf-caa588ca67ea`
6. **Set the functional unit to `Amount: 1.0 kg`** of the reference product — *not* the process's
   native declared amount (e.g. petroleum's own reference is 0.2523 L, not 1 kg). The harness
   normalizes brightway to this same 1 kg basis.
7. **Calculate with TRACI 2.2.** Use the same TRACI 2.2 method file the pipeline uses (10 categories).
8. **Export to Excel** and confirm the sheets below are present.

> **Steel is direct-only by design** — it is the foreground-only (Layer 1) control, included so
> discrepancies can be localized to foreground vs. background. Its bundle contains a single process
> with no upstream, so its `Impacts`
> sheet already equals a direct result. No product-system solve is exercised for steel — full-chain
> coverage rests on petroleum, corn, and cement (see [`README.md`](README.md), "Known limitations").

## The exact format the harness parses

The parsers in `05_validate_uslci.py` read fixed cell positions. If your export's layout differs
(different openLCA version, renamed sheet), the parse will fail loudly or return no categories.

**Full-chain — sheet `Impacts`** (`parse_full_chain_impacts`):
- Row 1 (and 2) are headers; **impact-category rows start at row 3**.
- **Column C** (3rd column) holds the impact-category **name**.
- **Column E** (5th column) holds the **result** value.

**Direct — sheet `Direct impact contributions`** (`parse_direct_impacts`, petroleum kg-basis only):
- **Row 2** holds process **UUIDs**, starting at **column E** and extending right (one column per
  process). The harness finds the column whose row-2 UUID equals the target process.
- **Impact-category rows start at row 6**; **column C** holds the category name; the matched
  process's column holds the value.

**Category-name matching (both modes).** The harness matches openLCA's impact-category names to
brightway's registered TRACI 2.2 names by **exact string**. If openLCA 2.6's TRACI 2.2 names differ
from brightway's (e.g. loader vs. tool wording), the harness prints
`WARNING: N BW category name(s) have no match ...` and shows `-` instead of a ratio for those rows.
If you see that, align the names (rename in the export, or adjust the method labels) — the locked
exports were produced with names that match, so a same-version openLCA + TRACI 2.2 should match too.

Also useful: each standard export carries a **`Calculation setup`** sheet — check it to confirm
`Amount: 1.0 kg` before trusting a full-chain file.

## Verify your regeneration

Place the four full-chain files in `source_data/` (and the direct file in `validation/`), then:

```bash
python validation/05_validate_uslci.py     # --mode full_chain is the default
```

Expect every BW/OL ratio inside ±5% — in fact all 40 cells of the 2025 build reproduce openLCA
within 0.1% (every cell rounds to 1.000), as do the 60 cells of the 2026 build. The harness overwrites
`validation/validation_full_chain_results.csv`; an empty `git diff` on it means your regenerated
exports reproduce the locked comparison. Rows flagged `!` are outside tolerance — if you get those,
re-check steps 2 (library mounted), 4 (electricity provider linked), and 6 (1 kg basis), which are
the three setup choices that most affect the full-chain numbers.

**If a case prints `SKIP:` instead of a row**, its expected vintage doesn't match the build — the
guard is doing its job. Rebuild against the matching baseline (`setup/03b --vintage <year>` then
`setup/03`) and re-run. A non-2025 build writes a vintage-tagged CSV (e.g.
`validation_full_chain_results_2026.csv`) so it can never overwrite the locked 2025 table.
