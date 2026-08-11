# How-to guides

Task-shaped guides for running your own study. For *why* the engine behaves as it does, see
[`SCHEMA_CROSSWALK.md`](SCHEMA_CROSSWALK.md) (USLCI → brightway field mapping) and
[`ALLOCATION.md`](ALLOCATION.md) (multi-output handling).

Every number below was produced by the commands shown, on a `uslci-full` build of USLCI
v1.2026-06.0 with the 2026 electricity baseline.

- [1. Functional units — reading and changing the basis of a result](#1-functional-units)
- [2. Linking a foreground CSV to USLCI](#2-linking-a-foreground-csv-to-uslci)
- [3. Choosing what background a result is calculated against](#3-choosing-the-background)
- [4. Driving the pipeline from your own script](#4-driving-the-pipeline-from-your-own-script)

---

## 1. Functional units

**Every result is per one unit of the target's reference product, and that unit comes from the
data, not from you.** Misreading it is an easy mistake to make, so `general/04` states
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
impact. The guard is in `fedefl_bw25/chart_units.py`.

Magnitude is guarded too. Because every bar is a percentage *of the baseline*, a near-zero baseline
makes the chart a picture of its own denominator, so the comparison is refused rather than drawn:

```
Skipping scenario_comparison: baseline 'Alfalfa hay production' is 5.08e+05x smaller than the
largest scenario in at least one category, so '% of baseline' would be dominated by the choice of
denominator rather than by any difference in impact.
```

**The baseline is the first scenario in the results CSV**, so control it by controlling what you
write first.

### Building a comparison set

`general/04` overwrites the results CSV by default. Use `--append` to accumulate scenarios into one
file — that file is what the comparison chart needs. Get the UUIDs with `--search` (guide 2):

```bash
python general/04_run_lca.py --uuid <A> --append --output study.csv
python general/04_run_lca.py --uuid <B> --append --output study.csv
python general/06_visualize.py --results study.csv --output-dir charts/study
```

Re-running a scenario replaces its rows rather than duplicating them, so iterating on one case is
safe. The chart shows at most 8 scenarios and says how many it omitted.

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
| `provider_uuid` | Who supplies a technosphere input: a USLCI process UUID, or another foreground process's derived UUID. Empty on biosphere and production rows. Find one with `--search`, below. |
| `flow_name` | Human label, never used computationally |
| `amount` | Numeric, expressed in `unit` |
| `unit` | **Load-bearing** — see below |
| `is_ref` | `true` on the one production row per process |
| `location`, `comment` | Optional |

### Finding a process UUID

You know what a process is called; the CSV wants its UUID. Search by name, in any order, using as
many words as you need to narrow it:

```bash
python general/04_run_lca.py --search "nitrogen fertilizer"
```

```
3 match(es) for 'nitrogen fertilizer':

  dacaeae9-aeed-3366-912d-6a31de09eef9  subset,full  US  kg  Nitrogen fertilizer; production mix; at plant
  6b946d9a-1c41-3562-a74a-087d472a1031  full         US  kg  Nitrogen fertilizer mix; average production, at US regional storehouse; as N
  280bc9cc-af90-386e-acbf-c407ba0310d7  full         US  kg  Emissions; application of nitrogen fertilizer mix; at field
```

Words do not have to be adjacent in the name, which matters more than it sounds: searching
`hdpe flake` finds `Recycled postconsumer high-density polyethylene, HDPE, flake; at plant`, where
that pair of words never appears together.

The third column is which build holds the process. A row marked `full` only is not in the default
database, so a foreground CSV pointing at it needs `--database uslci-full`. That is the usual cause
of the `provider_uuid not found` error below.

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

[`TUTORIAL.md`](TUTORIAL.md) §6 builds a two-process inventory row by row and §7 reproduces its
2.584981 kg CO₂-eq by hand from the characterization factors. Start there if you have not written one
of these before; the rest of this guide is the reference you come back to.

Two things it covers that are easy to get wrong. A foreground-to-foreground link is made by UUID, and
that UUID is derived from the provider's `process_name`:

```bash
python -c "from fedefl_bw25.foreground_importer import fg_uuid; print(fg_uuid('Widget subassembly'))"
```

And the target process has to be named when the file holds more than one:

```bash
python general/04_run_lca.py --foreground my_inventory.csv --target-process "Widget assembly" --database uslci-full
```

### Pointing at a co-product

Multi-output USLCI processes with causal allocation get one activity per co-product, coded
`<process-uuid>__co__<flow-uuid>`. You can point a technosphere row straight at one; it behaves like
any other provider. They search like anything else, and the results mark them:

```bash
python general/04_run_lca.py --search "causal co-product"
```

```
30 match(es), showing 4 for 'causal co-product':

  d9cadd89-…__co__80e4ff02-…  subset,full  RNA  kg  Ethanol; denatured; forest residues, thermochem [causal co-product: Sulfur; thermochemical process]  [co-product]
  bf1b1b0c-…__co__94f0594c-…  full         US   kg  Mixed waste sorting; at material recovery facility, MRF [causal co-product: Post-consumer, glass, recovered and sorted…]  [co-product]
```

Add the material to narrow it. `--search "causal co-product glass"` returns four rows, all supplying
the same recovered-glass flow from a different sorting route: mixed waste, presorted, dual stream,
single stream. Which route your material came through is a modelling decision, not a lookup, and the
four carry different burdens. See [`ALLOCATION.md`](ALLOCATION.md) for why these activities exist.

### Common errors

Full list with fixes: [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md).

| Message | Cause |
|---|---|
| `provider_uuid '…' not found in <db> or the current foreground batch` | Run `--search` on the process name. If it comes back marked `full` only, add `--database uslci-full`. If it comes back with no matches, the UUID is wrong. For a foreground link, check the provider's `process_name` spelling exactly. |
| `Foreground CSV has N processes — specify one with --target-process` | More than one process; say which is the functional unit. |
| `unit '…' is incompatible with reference unit '…'` | Different flow property. Express the amount in a compatible unit. |
| `production exchange amount must be > 0` | A zero or negative production amount gives a singular matrix. |

---

## 2b. Bringing your own olca-schema JSON-LD

A CSV is the quick path. When your foreground already exists as openLCA JSON-LD — exported from
openLCA, or generated by your own script — import it directly instead. It goes through the same
parser USLCI does, so it gets multi-output allocation, causal co-products, avoided products and
waste-treatment linking, none of which the CSV path has.

### The zip

Two directories at the top level. Nothing else is read:

```
processes/*.json
flows/*.json
```

An openLCA export already has this shape (its other directories are ignored). If you generate the
JSON yourself, those two are all you have to write. Zip from *inside* the folder — a zip whose
entries start with `my_study/processes/` is one level too deep, and the importer says so.

### Every background link names its provider

A technosphere exchange that draws on USLCI must carry `defaultProvider` with the USLCI process
UUID. openLCA writes this whenever you pick a provider in the GUI; generating JSON yourself means
setting it explicitly:

```json
{
  "isInput": true,
  "flow": {"@id": "c670f34f-…", "flowType": "PRODUCT_FLOW"},
  "defaultProvider": {"@id": "17664c37-72c0-4813-a4b9-93f962962c63"},
  "unit": {"name": "kg"},
  "amount": 1.05
}
```

Find the UUID with `--search`, exactly as for a CSV foreground (§2). An exchange that resolves to
no provider **stops the build** rather than becoming a cutoff — a cut background link is a silently
low result, and the message names the exchange.

### Every exchange needs a flowType and a direction

`flowType` (`PRODUCT_FLOW`, `WASTE_FLOW` or `ELEMENTARY_FLOW`) is read off the exchange's own `flow`
reference, falling back to the flow's file in `flows/`. If neither has it, the exchange cannot be
classified and the import stops. Inputs also need `isInput: true` — without it a product input reads
as a co-product *output*, which both drops the link and turns a single-output process into a
multi-output one, putting every other exchange through an allocation factor you never asked for.

### Parameters are not evaluated

This pipeline has no parameter engine. The number that reaches the matrix is the literal `amount`;
`amountFormula` is carried as provenance and otherwise ignored.

**You do not need openLCA.** A formula alongside a numeric amount imports fine — write both:

```json
"amountFormula": "hdpe_per_crate * 1.0",
"amount": 1.05
```

If you generate the JSON, compute the value where the parameters already live and emit both. openLCA
happens to do the same on export, which is why a file that has been through it already works.

What stops the import is a formula with **no** numeric amount — either no `amount` key, or
`amount: 0` beside an expression, which is exactly how `lca_algebraic` stores a parametric exchange.
Such an exchange contributes nothing while looking, in the JSON, like a fully specified model.
`--allow-unevaluated-formulas` imports them as zero if that is genuinely what you want.

The remaining limitation is real: amounts are a **snapshot**. Re-parameterising upstream needs a
re-export to land here — so a sweep varies the numbers in the generator and re-imports, which is
what `examples/jsonld_study.py --sweep` does.

USLCI itself ships 725 formula-bearing exchanges in the bundle build and reproduces openLCA to
within 0.1%, because its exports carry both the formula and the evaluated amount.

### Running it

```bash
python setup/03_import_uslci.py --full-db          # the background, once
python setup/03c_import_jsonld.py source_data/my_study.zip --database my-study
python general/04_run_lca.py --search "" --database my-study
python general/04_run_lca.py --uuid <uuid> --database my-study
```

`--background` defaults to the full USLCI build if present (else the bundle build), plus the
electricity baseline. It is printed at import and stamped onto the database, because the choice
changes results. Override it with `--background uslci-subset,electricity-baseline`.

### When the zip holds many processes

A USLCI-expansion dataset — 50 unit processes across a few industries — imports as one database.
Nothing needs to change; what changes is how you find the thing you want to run.

The import tells you which processes are **study targets**: the ones nothing else in the dataset
consumes. The rest are intermediates that exist to be drawn on.

```
'uslci-expansion' holds 50 activities. 30 are consumed by nothing else in it —
the likely study targets:
  8476b295-…  Packaging: crate assembly; at plant [US]
  …
```

That is a heuristic, not a declaration — olca-schema has no "final product" field. An intermediate
nobody happens to consume yet appears here too, which is useful: it is often a link the author
meant to make and didn't.

From there, `--search` works across every built database, custom ones included:

```bash
python general/04_run_lca.py --search "crate assembly"
python general/04_run_lca.py --search "" --database uslci-expansion --limit 0   # list all
python general/04_run_lca.py --uuid 8476b295-… --database uslci-expansion
```

To score every target in one pass, script it — import once, solve many:

```python
build = import_jsonld("source_data/expansion.zip", db_name="uslci-expansion",
                      background=["uslci-full", "electricity-baseline"], overwrite=True)

for i, (code, name) in enumerate(build.named().items()):     # .named() -> the roots
    run = run_lca(uuid=code, database=build.database, scenario=name)
    write_results_csv(run, "expansion.csv", append=i > 0)
```

`build.roots` is the shortlist, `build.processes` is all of them, `build.named()` gives
`{code: name}` for either. Runnable version: `examples/jsonld_study.py --all`. Fifty processes,
thirty targets, about twenty seconds including the import — the database is built once and each
target is just another functional unit against it.

### Common errors

| Message | Cause |
|---|---|
| `no top-level 'processes/' directory` | The zip wraps a folder. Re-zip from inside it. |
| `N exchange(s) could not be classified` | No resolvable `flowType`. Put it on the exchange's `flow` reference or on the flow's file in `flows/`. |
| `product OUTPUTS that name a defaultProvider` | Missing `isInput: true`. Left alone it drops the link *and* makes the process multi-output, so allocation scales everything else down. |
| `parameterized exchange(s) have a formula but no evaluated amount` | Parameters were never evaluated. Re-export from openLCA after evaluating, or write a numeric `amount`. |
| `N exchange(s) carry NO defaultProvider` | A background link doesn't name its provider. Set it, or `--allow-unhinted-links` to cut them (exploratory only). |
| `names a provider that is in no background database` | The UUID is wrong, or that background isn't built. |
| `process UUID(s) … also exist in a background database` | Your process reuses a USLCI UUID and would shadow it. Give yours a fresh UUID. |
| `was built against a different revision of 'uslci-full'` | The background was rebuilt underneath you. Re-run `03c` to relink. |
| `has N activities, so there is no single target` | A multi-process build. Pick from `build.roots`, or iterate `build.named()`. |

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
> foreground CSV with no technosphere rows) is the only route today. Tracked in [`ROADMAP.md`](ROADMAP.md).

---

## 4. Driving the pipeline from your own script

The numbered scripts are CLI front-ends over an importable package. For anything
repetitive — a sweep, a batch, a study with ten scenarios — call the library instead and
skip the subprocess-per-run entirely.

```bash
pip install -e .          # once
```

### One run

`run_lca()` computes and returns; it writes nothing and prints nothing.

```python
from fedefl_bw25.run import run_lca

run = run_lca(uuid="97970125-ad36-3919-8af8-69a053c5eefa", database="uslci-full")

run.score("Global warming")   # 0.511591
run.functional_unit           # '1 kg'
run.as_dict()                 # {'Global warming': 0.5116, 'Acidification': ...}
run.build                     # which database, how many activities, which grid
run.notes                     # diagnostics the CLI would have printed
```

Pass `log=print` if you want the console chatter the script produces.

### A batch

Because results come back as objects, you accumulate in memory and write once:

```python
from fedefl_bw25.run import run_lca, write_results_csv

targets = {"fishmeal":   "97970125-ad36-3919-8af8-69a053c5eefa",
           "fertilizer": "dacaeae9-aeed-3366-912d-6a31de09eef9",
           "corrugated": "9c10be0f-e38e-4551-b8b5-eef65fa27dcc"}

runs = [run_lca(uuid=u, database="uslci-full", scenario=label,
                contributions=False, manifest=False, smoke_test=False)
        for label, u in targets.items()]

for i, r in enumerate(runs):
    write_results_csv(r, "study.csv", append=i > 0)
```

```
fishmeal     GWP=   0.51159  per 1 kg
fertilizer   GWP=   1.78831  per 1 kg
corrugated   GWP=   0.11846  per 1 kg
```

Then chart it exactly as the CLI would: `python general/06_visualize.py --results study.csv`.

Two arguments are worth knowing for loops. `smoke_test=False` skips the preflight
check, which writes and deletes a temporary database three times per call — insurance
worth paying once, not once per scenario. `contributions=False` skips per-process
attribution, which is the expensive part of a run and usually not what a sweep is for.

### A foreground study

```python
run = run_lca(foreground="my_inventory.csv", target_process="Widget assembly",
              database="uslci-full")
```

Same object back. Unit conversions and validation errors from guide 2 apply
identically — the CLI and the library are the same code path.

### Building databases from a script

`setup/03b` is callable too — useful when you rebuild across vintages:

```python
from fedefl_bw25.setup_baseline import inject_baseline

build = inject_baseline(vintage="2026", overwrite=True)
print(build.vintage, build.activity_count, build.library)
```

`overwrite=True` is what makes it scriptable: the CLI prompts before replacing an
existing database, and a library call must never block on stdin. Pass `confirm=`
a callable instead if you want your own gate.

> **Re-running `03b` alone leaves the USLCI databases stale.** Rewriting the
> baseline gives its activities new internal IDs, so the USLCI exchanges that
> pointed at them dangle and the next solve fails with a non-square technosphere
> matrix. Always follow `03b` with `03`. This is why the manifest reports an
> `inconsistent` electricity vintage when the two disagree.

`setup/03` is callable too, so a whole rebuild is one script:

```python
from fedefl_bw25.setup_baseline import inject_baseline
from fedefl_bw25.setup_uslci import import_uslci

inject_baseline(vintage="2026", overwrite=True)     # 03b first — 03 links against it
import_uslci(overwrite=True)                        # the bundle build
import_uslci(full_db=True, overwrite=True)          # and the whole database
```

`import_uslci()` returns a `UslciBuild` with the activity count, the link/match
totals, the source identity (zip names + SHA256) and the sidecar path — the same
diagnostics the script prints, as data you can assert on.

### The whole pipeline in one file

Every step is callable, so a bare machine to finished results is one script.
`examples/full_pipeline.py` is that script, ready to copy and edit; in outline:

```python
from fedefl_bw25.setup_biosphere import import_biosphere
from fedefl_bw25.setup_traci import import_traci
from fedefl_bw25.setup_baseline import inject_baseline
from fedefl_bw25.setup_uslci import import_uslci
from fedefl_bw25.run import run_lca, write_results_csv

import_biosphere(overwrite=True)                 # 01  FEDEFL flows
import_traci()                                   # 02  TRACI 2.2 methods
inject_baseline(vintage="2026", overwrite=True)  # 03b electricity background
import_uslci(full_db=True, overwrite=True)       # 03  the USLCI database

for label, uuid in targets.items():
    run = run_lca(uuid=uuid, database="uslci-full", scenario=label)
    write_results_csv(run, "study.csv", append=True)
```

Order matters and the chain is directional: `01` → `03b` → `03`. Rebuilding a step
renumbers brightway's internal ids, so everything downstream of it must be rebuilt
too or the next solve fails with a non-square technosphere matrix. Rebuild the
whole chain, not a piece of it.

Two steps behave differently on purpose. `setup/00`'s conversion table ships
committed and is not rebuilt here — it needs the full USLCI zip, and changing it is
a validation event; `describe_conversions()` reports what is on disk, and
`build_conversions()` + `write_conversion_table()` rebuild it when you mean to.
`import_traci()` has no exists-guard and simply rewrites its methods: they carry no
downstream ids, so there is nothing for a rebuild to invalidate.

Each returns a build object rather than printing — `BiosphereBuild.flow_count`,
`TraciBuild.total_matched`, `BaselineBuild.vintage`, `UslciBuild.totals` — so a
scripted build can assert on what it got. Pass `log=print` for the console output
the scripts produce.
