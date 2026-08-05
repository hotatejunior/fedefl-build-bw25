# How-to guides

Task-shaped guides for running your own study. For *why* the engine behaves as it does, see
[`SCHEMA_CROSSWALK.md`](SCHEMA_CROSSWALK.md) (USLCI → brightway field mapping) and
[`ALLOCATION.md`](ALLOCATION.md) (multi-output handling).

Every number below was produced by the commands shown, on a `uslci-full` build of USLCI
v1.2026-06.0 with the 2026 electricity baseline.

- [1. Functional units — reading and changing the basis of a result](#1-functional-units)
- [2. Linking a foreground CSV to USLCI](#2-linking-a-foreground-csv-to-uslci)
- [3. Choosing what background a result is calculated against](#3-choosing-the-background)

---

## 1. Functional units

**Every result is per one unit of the target's reference product, and that unit comes from the
data, not from you.** It is the single most common way to misread a result, so `general/04` states
it in three places: the console target block, a `functional_unit` column in both output CSVs, and
the `target` block of `validation_manifest.json`.

```
=== Target: 'Petroleum refining; at refinery' ===
    Functional unit: 1 m3 (the process's reference unit — every score below is per 1 m3 of this product)
```

### The trap

A USLCI process's declared reference amount is often an arbitrary quantity rather than a sensible
basis. Petroleum refining is declared per **m³**, not per kg. A run of it returns ~673 kg CO₂-eq —
about 849× the locked validation figure of 0.79286, because the validation is kg-basis and
849 kg/m³ is the density. Neither number is wrong; they are answers to different questions. This
cost real debugging time before the functional unit was surfaced (DEVLOG, 2026-07-13).

**Check the functional unit before comparing any two numbers.**

### Changing the basis

There is no `--basis` flag, and that is deliberate — rescaling silently is how the trap above
happens. You change the basis by changing the question:

- **Scale afterwards.** Divide by the density or mass per unit yourself, and say so in your write-up.
  This is what the validation harness does: it re-bases to 1 kg at runtime using a pinned
  `DENSITY_KG_M3`, because the openLCA reference exports are kg-basis.
- **Wrap it in a foreground process** with the basis you want (guide 2). A foreground process whose
  production row is `1 kg` and which consumes `1/849 m3` of petroleum refining gives you a kg-basis
  result with the conversion recorded in your inventory rather than in your head.

### Comparisons are guarded

`general/06`'s scenario-comparison chart refuses to compare scenarios whose functional units differ,
naming each exclusion:

```
EXCLUDED from scenario_comparison: 'Printer; AltaLink B8045/55' (per 1 Item(s)) is not
comparable to the baseline 'Alfalfa hay production' (per 1 kg).
```

A percentage between a per-Item(s) and a per-kg result would be a unit artifact, not a difference in
impact. The guard is in `general/chart_units.py`.

> **Known gap.** The guard checks commensurable *units*, not commensurable *magnitudes*. If the
> baseline scenario has a near-zero score the percentages explode without warning. Pick your
> baseline deliberately. Tracked in RELEASE_PLAN Phase 6.

---

## 2. Linking a foreground CSV to USLCI

A foreground CSV is your own inventory: the processes you are studying, with their inputs pointing
into USLCI for everything upstream. One row per exchange, one file for all your processes.

### Columns

| Column | Meaning |
|---|---|
| `process_name` | Owner process. Its UUID is derived from this string, so **spelling is load-bearing** for foreground-to-foreground links. |
| `exchange_type` | `production`, `technosphere`, or `biosphere` |
| `flow_uuid` | FEDEFL UUID — **required and validated** on biosphere rows. On technosphere/production rows it is provenance only, not used. |
| `provider_uuid` | Who supplies a technosphere input: a USLCI process UUID, or another foreground process's derived UUID. Empty on biosphere and production rows. |
| `flow_name` | Human label, never used computationally |
| `amount` | Numeric, expressed in `unit` |
| `unit` | **Load-bearing** — see below |
| `is_ref` | `true` on the one production row per process |
| `location`, `comment` | Optional |

### Units are converted, not assumed

An amount is always applied against its counterpart's *reference* unit — the biosphere flow's, or
the technosphere provider's. Write `200 g` of a provider whose reference is kg and the importer
converts it and tells you:

```
WARNING: Row 7: converted 200.0 g → 0.2 kg (x0.001)
```

A unit from a different flow property, or one with no known conversion factor, is a hard error
rather than a passthrough:

```
Row 7: unit 'MJ' (energy) is incompatible with reference unit 'kg' (mass)
       (provider '97970125-ad36-3919-8af8-69a053c5eefa')
```

This follows the same rule as the USLCI importer: an unconverted unit is a wrong number wearing a
plausible one's clothes.

### A worked example

Two processes, where one feeds the other, with a USLCI background link and direct emissions:

```csv
process_name,exchange_type,flow_uuid,provider_uuid,flow_name,amount,unit,is_ref,location,comment
Widget subassembly,production,,,Widget subassembly,1,kg,true,US,ref product
Widget subassembly,technosphere,,dacaeae9-aeed-3366-912d-6a31de09eef9,Nitrogen fertilizer,0.4,kg,false,US,USLCI link
Widget subassembly,biosphere,4fa5e7ef-83a7-3ad6-ad4a-e0b3de171609,,Carbon dioxide,0.25,kg,false,US,direct
Widget assembly,production,,,Widget assembly,1,kg,true,US,ref product
Widget assembly,technosphere,,<uuid5 of "Widget subassembly">,Widget subassembly,0.5,kg,false,US,foreground link
Widget assembly,technosphere,,97970125-ad36-3919-8af8-69a053c5eefa,Fishmeal,0.2,kg,false,US,USLCI link
Widget assembly,biosphere,4fa5e7ef-83a7-3ad6-ad4a-e0b3de171609,,Carbon dioxide,1.5,kg,false,US,direct
Widget assembly,biosphere,be7b7ec1-c39a-376b-a50f-682256b29299,,Methane,0.02,kg,false,US,direct
```

Get the foreground-to-foreground UUID from the process name:

```bash
python -c "import sys; sys.path.insert(0,'general'); from foreground_importer import fg_uuid; print(fg_uuid('Widget subassembly'))"
```

Run it, naming which process is the functional unit:

```bash
python general/04_run_lca.py --foreground my_inventory.csv --target-process "Widget assembly" --database uslci-full
```

That returns **2.584981 kg CO₂-eq per 1 kg**, which is exactly
`1.5·CF_CO2 + 0.02·CF_CH4 + 0.5·(subassembly) + 0.2·(fishmeal)` — worth reproducing by hand once on
your own inventory, because it is the cheapest way to confirm you have linked what you think you
have.

### Pointing at a co-product

Multi-output USLCI processes with causal allocation get one activity per co-product, coded
`<process-uuid>__co__<flow-uuid>`. You can point a technosphere row straight at one; it behaves like
any other provider. Find them with:

```bash
python -c "import bw2data as bd; bd.projects.set_current('fedefl-build-bw25'); [print(a['code'],'|',a['name'][:60]) for a in bd.Database('uslci-full') if '__co__' in a['code']]"
```

See [`ALLOCATION.md`](ALLOCATION.md) for why those activities exist.

### Common errors

| Message | Cause |
|---|---|
| `provider_uuid '…' not found in <db> or the current foreground batch` | Wrong UUID, or the process is in the other USLCI build — try `--database uslci-full`. For a foreground link, check the provider's `process_name` spelling exactly. |
| `Foreground CSV has N processes — specify one with --target-process` | More than one process; say which is the functional unit. |
| `unit '…' is incompatible with reference unit '…'` | Different flow property. Express the amount in a compatible unit. |
| `production exchange amount must be > 0` | A zero or negative production amount gives a singular matrix. |

---

## 3. Choosing the background

A result is your foreground plus whatever background it links into. Two things control that.

### Which USLCI build

Two builds coexist. Both are stated in the startup banner, so a result is never ambiguous about what
it stood on:

```
  USLCI build      : 'uslci-full'  (1455 activities)   [set by --database]
  Composition      : whole USLCI database
  Built from       : National_Renewable_Energy_Laboratory-USLCI_Database_Public.zip (sha256 0c7ab40ac6da…)
  Electricity grid : 2026 baseline
```

| Build | What it is | Use it when |
|---|---|---|
| `uslci-subset` | The per-process bundles you downloaded — the bundle targets **plus every upstream process those bundles shipped** | Reproducing the locked validation, which was computed against exactly this set |
| `uslci-full` | The whole USLCI database from the single full zip | Running any process without re-importing |

Select it with `--database`, or set `USLCI_DATABASE` in `general/04`'s CONFIG block for IDE use.

> `uslci-subset` is not "the nine validated processes" — it is those nine plus their entire upstream,
> ~391 activities. A lookup can succeed on a process nobody deliberately imported.

### How much background your foreground pulls

There is no flag for this; it is a property of the inventory you write.

- **Full background** — your foreground has `technosphere` rows pointing into USLCI, so the solve
  pulls the whole upstream chain. This is the normal case.
- **Foreground only** — omit technosphere rows entirely. With just `production` and `biosphere` rows
  you characterize your own direct emissions and nothing else, which is useful for isolating whether
  a surprising result comes from your own inventory or from upstream.

Check what your result actually reached in `validation_manifest.json`:

```json
"completeness": {
  "supply_chain_activity_count": 383,
  "uslci_activity_count": 372,
  "external_background_activity_count": 11,
  "tech_unlinked_in_supply_chain": 736,
  "fully_linked": false
}
```

`fully_linked: false` is normal, not an error — it means some exchanges in your chain are cutoffs
with no producer in USLCI. The counts tell you how much.

> **Known gap.** For a *USLCI process* target there is no foreground-only switch. The harness has
> `--mode direct` for exactly this, but `general/04` has no equivalent, so the trick above (a
> foreground CSV with no technosphere rows) is the only route today. Tracked in RELEASE_PLAN Phase 6.
