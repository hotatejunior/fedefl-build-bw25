# Tutorial — from nothing to a defensible number

One path, start to finish. You will build the databases, run a USLCI process, model your own
product against it, and check the answer by hand.

**Before you start:** conda or mamba, a terminal, and one file you download by hand (31 MB). You do
not need openLCA, an ecoinvent licence, or a LCA Commons account. The build itself takes about two and a
half minutes; the downloads decide everything else.

Task-shaped guides for specific jobs are in [`HOWTO.md`](HOWTO.md). This file assumes you have done
none of it before.

---

## 1. Install

```bash
git clone https://github.com/hotatejunior/fedefl-build-bw25.git
cd fedefl-build-bw25
conda env create -f environment.yml
conda activate fedefl-build-bw25
pip install -e .
```

`environment.yml` pins the versions the published validation was computed against, including the two
EPA packages that install from GitHub rather than PyPI. The editable install is what lets the
scripts find `fedefl_bw25`, and what lets you import the pipeline into scripts of your own later.

## 2. Get the data and build

Download the USLCI database as openLCA JSON-LD from the
[LCA Commons](https://www.lcacommons.gov/lca-collaboration/National_Renewable_Energy_Laboratory/USLCI_Database_Public)
(Export → JSON-LD) and put the zip in `source_data/` without renaming it. That is the only manual
download. The electricity baseline is fetched and hash-checked for you, and the elementary flows and
impact methods come from pip packages.

```bash
mkdir -p source_data
# ...move the downloaded zip into source_data/, then:
python examples/full_pipeline.py --setup-only
```

Each step says what it produced. On a 2023 MacBook Pro, with the impact-method files already
cached:

| Step | What it builds | Time |
|---|---|---|
| `01` | 332,133 FEDEFL elementary flows | 26s |
| `02` | 10 TRACI 2.2 methods, 200,699 characterization factors | 90s |
| `03b` | US electricity baseline, 17 background activities | 11s |
| `03` | USLCI, 1,455 activities | 10s |

The first run on a machine is slower: `02` downloads the EPA impact-method files, and `03b` fetches
a 167 MB baseline library and checks it against a pinned hash. Both are cached afterwards.

Watch for this line, which tells you which electricity grid your results will stand on:

```
Auto-detected electricity-baseline vintage: 2026 (no bundles present; read from
the full USLCI zip — 330 reference(s) vs 2025=0)
```

Three databases and ten impact methods now exist in a brightway project named
`fedefl-build-bw25`: the FEDEFL elementary flows, the US electricity baseline, USLCI itself, and the
TRACI 2.2 methods.

> **If the build stops with a hash mismatch on `uslci_flow_conversions.json`**, you downloaded a
> newer USLCI release than the conversion table shipped in this repo was built from. That guard is
> doing its job: the table holds substance densities and energy contents that scale exchanges, and
> using it against a different release would be a guess. Rebuild it with
> `python setup/00_build_flow_conversion_table.py --rebuild`, which takes about a minute, then re-run
> the setup.

## 3. Find your process

USLCI names are long, so search by whatever words you remember. They can appear in any order and do
not have to be adjacent:

```bash
python general/04_run_lca.py --search "petroleum refining"
```

```
1 match(es) for 'petroleum refining':

  0aaf1e13-5d80-37f9-b7bb-81a6b8965c71  subset,full  US   m3     Petroleum refining; at refinery

Run the first with:
  python general/04_run_lca.py --uuid 0aaf1e13-5d80-37f9-b7bb-81a6b8965c71
```

The third column tells you which build holds the process. Add words to narrow a broad search:
`diesel` returns 224 processes, `diesel boiler` returns 20.

## 4. Your first result

Run the command the search handed you. You get all ten TRACI 2.2 categories:

```
=== LCIA results — per 1 m3 of 'Petroleum refining; at refinery' ===
  Acidification                          0.982504  kg SO2 eq
  Eutrophication (Marine)                 0.289014  kg N eq
  Freshwater ecotoxicity                   2304.94  CTUeco
  Global warming                           668.532  kg CO2 eq
  ...  (ten categories in all)
```

Scores land in `lca_results.csv`, per-process contributions in `lca_contributions.csv`, and an audit
manifest in `validation_manifest.json`.

## 5. What the number is *per*

Look again at the header: **per 1 m3**. Not per kg.

A USLCI process declares its own reference unit, and it is often not the one you would pick. Divide
that 668.532 by diesel's density of 849 kg/m³ and you get 0.787 kg CO₂-eq per kg — the same process,
the same data, a number 849 times smaller. Neither is wrong. They answer different questions.

This is the most common way to misread a result, so the runner states the basis in three places: the
console header above, a `functional_unit` column in both CSVs, and the `target` block of the
manifest. There is deliberately no `--basis` flag, because rescaling silently is how the mistake
happens. To change the basis, either divide afterwards and say so in your write-up, or wrap the
process in a foreground of your own, which is section 6.

Check the functional unit before comparing any two numbers.

## 6. Your own product

A foreground inventory is a CSV, one row per exchange. Here is a two-stage product: a subassembly
that draws USLCI fertilizer and emits CO₂ directly, and an assembly that consumes the subassembly
plus USLCI fishmeal.

Foreground-to-foreground links are made by UUID, and a process's UUID is derived from its name:

```bash
python -c "from fedefl_bw25.foreground_importer import fg_uuid; print(fg_uuid('Widget subassembly'))"
```

Save this as `my_inventory.csv`, pasting that UUID into the marked cell:

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

Three row types. `production` declares what the process makes and sets its functional unit, one per
process, flagged `is_ref=true`. `technosphere` consumes something else, named by `provider_uuid` —
a USLCI process found with `--search`, or another of your own. `biosphere` is a direct emission,
named by its FEDEFL flow UUID.

Run it, naming which process is the functional unit:

```bash
python general/04_run_lca.py --foreground my_inventory.csv \
    --target-process "Widget assembly" --database uslci-full
```

```
Global warming    2.58498  kg CO2 eq   per 1 kg
```

The `unit` column is load-bearing. Write `200` and `g` instead of `0.2` and `kg` and you get the
same answer, with the conversion reported. Write a unit from a different physical quantity and the
run stops rather than guessing.

## 7. Check it by hand

Do this once, on your own inventory. It is the cheapest way to confirm you linked what you think you
linked.

The assembly's score is its own emissions, characterized, plus its inputs' scores:

| Term | Amount | × | Value | = |
|---|---|---|---|---|
| CO₂ direct | 1.5 kg | × | 1.0 kg CO₂-eq/kg | 1.500000 |
| CH₄ direct | 0.02 kg | × | 25.0 kg CO₂-eq/kg | 0.500000 |
| Subassembly | 0.5 kg | × | 0.965325 | 0.482663 |
| Fishmeal | 0.2 kg | × | 0.511591 | 0.102318 |
| | | | **Total** | **2.584981** |

The pipeline returned 2.584980824789767, agreeing to 1e-8. The residue is arithmetic ordering in the
matrix solve, not a modelling difference. Get the two component scores by running each supplier on
its own: `--target-process "Widget subassembly"` and `--search fishmeal`.

## 8. Compare scenarios

Vary something, accumulate the runs in one file, and chart them:

```bash
python general/04_run_lca.py --foreground my_inventory.csv \
    --target-process "Widget assembly" --scenario baseline --append --output study.csv
# edit an amount, then re-run with --scenario "less fertilizer"
python general/06_visualize.py --results study.csv --output-dir charts/study
```

Re-running a scenario label replaces its rows instead of duplicating them. The comparison chart
refuses to plot scenarios whose functional units differ, so a "1 m3" result cannot be read against a
"1 kg" one.

## 9. Keep the manifest

`validation_manifest.json` records what stood behind the result: which USLCI release, its content
hash, the electricity-baseline vintage, package versions, and how much of the solved supply chain
was cut. A reviewer will ask, and the run already answered.

## 10. Where next

[`HOWTO.md`](HOWTO.md) for task-shaped guides, [`ALLOCATION.md`](ALLOCATION.md) if your process has
co-products, [`SCHEMA_CROSSWALK.md`](SCHEMA_CROSSWALK.md) when a number looks wrong and you need to
know how USLCI became a matrix, and [`VALIDATION_REPORT.md`](VALIDATION_REPORT.md) for how far the
engine has been checked against openLCA.
