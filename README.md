# fedefl-build-bw25

**Life-cycle assessment on the open US data stack — USLCI, FEDEFL and TRACI 2.2 loaded into
brightway25, in Python you can script.**

Bring a foreground inventory as a CSV, link it to USLCI background processes, and get TRACI 2.2
results. No ecoinvent licence, no GUI, and the engine has been checked against openLCA cell for cell.

---

## Why this exists

USLCI, FEDEFL and TRACI 2.2 are free and open, and brightway is a programmable LCA engine in Python.
What was missing is a reproducible path between them. The obvious route, `bw2io`'s `JSONLDImporter`,
has known bugs with USLCI's `isInput` field that silently misclassify exchanges.

This repo provides a clear path to results from USLCI within a brightway pipeline, with three key
features:

- A custom JSON-LD parser, with full control over exchange-direction detection.
- Every biosphere flow keyed by FEDEFL UUID, in the brightway database, the TRACI method and the
  USLCI data alike, so linking an emission is an identity lookup rather than name matching.
- A decoder for openLCA's pre-aggregated *library* packages, a format brightway cannot otherwise
  read, so a pre-solved background like the US Electricity Baseline is injected directly instead of
  being re-solved.

Multi-output allocation follows whatever each USLCI process declares, down to the per-exchange causal
case, which was a pain point in this project's development and may present the same issues for
similarly styled importers.

## Quick start from the command line

```bash
conda env create -f environment.yml && conda activate fedefl-build-bw25
pip install -e .
# put the USLCI JSON-LD zip from LCA Commons in source_data/, then:
python examples/full_pipeline.py --setup-only
```

That builds every database in about two and a half minutes. Then find a process by name and run it:

```bash
python general/04_run_lca.py --search "hdpe flake"
python general/04_run_lca.py --uuid 17664c37-72c0-4813-a4b9-93f962962c63 --database uslci-full
```

The search prints the second line for you, `--database` included when you need it.

You get ten TRACI categories in `lca_results.csv`, per-process contributions in
`lca_contributions.csv`, and an audit manifest recording which USLCI release, which electricity
vintage, and how much of the supply chain was cut.

**New here? [`docs/TUTORIAL.md`](docs/TUTORIAL.md) walks the whole path once**, from an empty machine
to your own product modelled against USLCI, with a spot-check of the result.

## Scripting it

Every pipeline step is a function, so a build plus a study fits in one file
([`examples/full_pipeline.py`](examples/full_pipeline.py)):

```python
from fedefl_bw25.setup_uslci import import_uslci
from fedefl_bw25.run import run_lca

import_uslci(full_db=True, overwrite=True)
run = run_lca(uuid="97970125-ad36-3919-8af8-69a053c5eefa", database="uslci-full")
run.score("Global warming")      # 0.511591  (fishmeal, per 1 kg)
```

Nothing in the package prints or prompts, and results come back as objects rather than files. That
makes the repetitive work cheap: run a list of processes into one comparison table, re-run a study
with a single amount changed, or rebuild a database and re-check a result, all from one script.

## Validation of pipeline results

On identical inputs this pipeline reproduces the numbers openLCA computes. That is a check on the
mechanics: the parser, allocation, provider linking, and the LCIA solve. Across nine test cases, all
**100 category × process cells reproduce openLCA within 0.1%**, every cell rounding to a ratio of
1.000. Two builds, because one build carries one electricity-baseline vintage:

| Build | Cases | Cells | Max deviation |
|---|---|---|---|
| 2025 baseline | petroleum refining, corn, Portland cement, steel billets | 40 | <0.0001% |
| 2026 baseline | steel billets, HDPE flake, PET flake, chlorine, hardboard, soy meal | 60 | 0.00077% |

Chlorine splits three ways and hardboard seven, so allocation is exercised on real grids. Economic
allocation cannot be tested against USLCI at all: all 29 processes declaring it use factors of
exactly 0.0 or 1.0.

Parity means the arithmetic is trustworthy. Whether the allocation choices, system boundary, cutoffs
and data vintage suit *your* study is a modelling judgment the engine cannot make for you. Method and
per-process results are in [`docs/VALIDATION_REPORT.md`](docs/VALIDATION_REPORT.md); the harness that
reproduces them is in [`validation/`](validation/).

## How this was built

Most of the implementation was written by Claude (Anthropic) under close human direction, and the
trust case deliberately does not rest on who typed it.

- **Human accountability.** A human expert made the method decisions, ran every openLCA reference
  session by hand, directed the debugging, and audited each script against a fixed
  procedure ([`VALIDATION_REPORT.md`](docs/VALIDATION_REPORT.md) appendix C). Where AI-generated work was accepted without proportionate
  review, that is tracked in the [devlog's](docs/DEVLOG.md) under-review ledger.
- **VALIDATE, do not FIT.** Discrepancies against openLCA were root-caused, never tuned away. No
  hardcoded per-dataset constants, no special-casing of the test processes. Petroleum's toxicity
  started at 2.0× openLCA and took three real bug fixes to reach 1.000; two comfortable-sounding
  explanations were disproven along the way, including one that wrongly blamed openLCA and had to be
  retracted. Both are still on the record.
- **The evidence chain is checkable without trusting any of the above:** pinned inputs, a
  reproducible harness, locked outputs. Re-run it from [`validation/`](validation/) and compare.

## Documentation

| File | Purpose |
|------|---------|
| [`TUTORIAL.md`](docs/TUTORIAL.md) | Start here. Getting your first results from scratch |
| [`HOWTO.md`](docs/HOWTO.md) | Task guides: functional units, foreground CSVs, choosing the background, scripting |
| [`TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) | Every error the pipeline raises, what it means, and the fix |
| [`ALLOCATION.md`](docs/ALLOCATION.md) | How multi-output processes are split, and what USLCI actually contains |
| [`SCHEMA_CROSSWALK.md`](docs/SCHEMA_CROSSWALK.md) | USLCI openLCA JSON-LD → the brightway schema, field by field |
| [`VALIDATION_REPORT.md`](docs/VALIDATION_REPORT.md) | The openLCA parity write-up, with the technical appendix |
| [`validation/`](validation/) | The harness, locked result CSVs, and the chronological validation log |
| [`DEVLOG.md`](docs/DEVLOG.md) | Design decisions, the bugs fixed to reach validation, and the release-plan record |
| [`ROADMAP.md`](docs/ROADMAP.md) | What is open, and what has been ruled out |

`setup/` builds the databases once per machine, `general/` runs studies, `validation/` reproduces
the parity claim, and `fedefl_bw25/` is the package the numbered scripts front. Pinned versions are
in [`environment.yml`](environment.yml); developer notes in [`CLAUDE.md`](CLAUDE.md).

## What's next

Parameterized foregrounds and fast scenario sweeps: declare parameters in the inventory CSV, vary
them from a params file, get a curve instead of a point. Open items in
[`docs/ROADMAP.md`](docs/ROADMAP.md).

## Author, citation, issues

Harrison Watson. Licensed [MIT](LICENSE) — free to use, modify and redistribute, with no warranty.
Cite it via [`CITATION.cff`](CITATION.cff), which GitHub renders as a "Cite this repository" button.

Parity results and bug reports are especially welcome, since the trust case here is built on outside
scrutiny: open a [GitHub issue](https://github.com/hotatejunior/fedefl-build-bw25/issues).
