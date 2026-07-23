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

Five Excel exports — one per validation cell. Four feed full-chain mode; one feeds direct mode.

| File (exact name the harness expects) | Mode | Sheet the harness reads | Lives in |
|---|---|---|---|
| `Petroleum_refining__at_refinery___US__AVG_ELEC_SELECTION.xlsx` | full-chain | `Impacts` | `source_data/` |
| `Corn__whole_plant__at_field___US_AVG_ELEC_SELECTION.xlsx` | full-chain | `Impacts` | `source_data/` |
| `Portland_cement__at_plant___US__US_AVG_ELEC_SELECTION.xlsx` | full-chain | `Impacts` | `source_data/` |
| `Steel__billets__at_plant___RNA_results.xlsx` | full-chain | `Impacts` | `source_data/` |
| `Petroleum_refining__at_refinery___US_kg_basis.xlsx` | direct | `Direct impact contributions` | `validation/` |

Filenames matter: they are hardcoded in `TARGETS_FULL` / `TARGETS_DIRECT` at the top of
`05_validate_uslci.py`. Either save with these exact names, or edit those dicts / set
`SOURCE_DATA_DIR` to point at your own layout.

## Prerequisites

- **openLCA 2.6** (the version the locked results were computed against).
- The **US Electricity Baseline library, `v1.2025-06.0`** — the exact version openLCA mounted (later
  baseline versions renamed UUIDs and will reintroduce drift). Hash pinned in
  [`README.md`](README.md) / [`VALIDATION_LOG.md`](VALIDATION_LOG.md).
- The four **USLCI supply-chain bundles** (openLCA JSON-LD), each hash-pinned in
  [`README.md`](README.md). These are the *same* bundles brightway imports — feeding both engines
  identical JSON-LD is the whole point of the comparison.

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
   **`7068192a-999c-39b6-bf66-234a294bdf92`**. This is what the `_AVG_ELEC_SELECTION` in the
   filenames records. **Steel has no electricity input — skip this step for steel.**
5. **Build the product system for the target process** — *after* step 4, so the manual provider link
   is captured in the system. Target UUIDs:
   - Petroleum refining: `0aaf1e13-5d80-37f9-b7bb-81a6b8965c71`
   - Corn; whole plant: `11256034-2355-3add-ade9-59983025dded`
   - Portland cement: `62993671-574c-3fc5-b66a-6be3bb21ad3d`
   - Steel; billets: `ac54bc7d-5db5-3b4f-9175-5dd02f678312`
6. **Set the functional unit to `Amount: 1.0 kg`** of the reference product — *not* the process's
   native declared amount (e.g. petroleum's own reference is 0.2523 L, not 1 kg). The harness
   normalizes brightway to this same 1 kg basis.
7. **Calculate with TRACI 2.2.** Use the same TRACI 2.2 method file the pipeline uses (10 categories).
8. **Export to Excel** and confirm the sheets below are present.

> **Steel is direct-only.** Its bundle contains a single process with no upstream, so its `Impacts`
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
python validation/05_validate_uslci.py     # VALIDATION_MODE = "full_chain"
```

Expect every BW/OL ratio inside ±5% — in fact all 40 cells reproduce openLCA within 0.1% (every cell
rounds to 1.000). The harness overwrites
`validation/validation_full_chain_results.csv`; an empty `git diff` on it means your regenerated
exports reproduce the locked comparison. Rows flagged `!` are outside tolerance — if you get those,
re-check steps 2 (library mounted), 4 (electricity provider linked), and 6 (1 kg basis), which are
the three setup choices that most affect the full-chain numbers.
