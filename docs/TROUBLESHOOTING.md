# Troubleshooting

Every message below is one the pipeline actually raises, grouped by when you hit it. Most are
guards rather than failures: the pipeline stops on purpose rather than writing a number it cannot
stand behind.

If a message is not here, the text usually names its own fix — that is the convention the guards are
written to.

---

## Building the databases

| Message | What happened | Fix |
|---|---|---|
| `uslci_flow_conversions.json not found` | The conversion table is missing. It normally ships committed | `python setup/00_build_flow_conversion_table.py --rebuild`, which needs the full USLCI zip |
| `uslci_flow_conversions.json was built from a different version of …` | You downloaded a newer USLCI release than the committed table was built against. The table holds densities and energy contents that scale exchanges, so using it across releases would be a guess | Rebuild the table with `--rebuild`, then re-run setup. Takes about a minute |
| `No USLCI sources found in …` | `source_data/` has neither the full USLCI zip nor any bundle zips | Download the JSON-LD export from LCA Commons into `source_data/`, or point `SOURCE_DATA_DIR` at where it lives |
| `No process zip files found in …` | Bundle mode found nothing matching `<uuid>_<hash>.zip` | Keep the original LCA Commons filename. A renamed bundle is skipped with a warning naming it |
| `USLCI_FULL_DB is set but the full USLCI zip is not present` | Full-database mode, no full zip | Put it in `source_data/` under the name recorded in the conversion table's `_meta` |
| `fedelemflowlist returned only N flows (expected ≥298919)` | A partial or cached-broken FEDEFL fetch. Building on it would give silent gaps in UUID coverage | Reinstall `fedelemflowlist` and retry |
| `FEDEFL contains duplicate UUIDs with conflicting substance data` | Two different substances share a UUID upstream | Pin to the last known-good `fedelemflowlist`; the message links the issue tracker |
| `TRACI 2.2 CF source file hash mismatch — build STOPPED` | EPA changed a characterization-factor file. The locked validation was computed against the old one | Re-run the harness. If it still passes, update the pinned hash in `fedefl_bw25/setup_traci.py` |
| `Downloaded baseline failed hash check` | The electricity-baseline artifact on GitHub changed | Do not bypass. Verify the upstream release, then update the pinned `sha256` in `setup_baseline.py` |
| `N unrecognized unit string(s) encountered — build STOPPED before writing` | An exchange uses a unit with no conversion factor. Passing it through unconverted is a silently wrong number | Add the unit to `WITHIN_FP` with its factor, then rebuild. `--allow-unit-passthrough` overrides for exploration only |
| `N consumed causal co-product(s) have NO per-exchange factor column` | A causal co-product something actually draws has an empty factor grid; its activity would degrade to a flat mass split | The message lists the process/flow UUIDs. This is a data problem worth reporting upstream |
| `Aborted — database not modified` | You answered `n` to an overwrite prompt | Re-run with `--yes`, or `overwrite=True` from a script |

## Running a study

| Message | What happened | Fix |
|---|---|---|
| `'uslci-subset' not found` | You built only the full database, and the runner defaults to the bundle build | Add `--database uslci-full`. `--search` prints the right command with `--database` already filled in |
| `Process … not in any built USLCI database` | Wrong UUID, or the process is in a USLCI release newer than your build | Search by name: `--search "part of the name"`. The message says which builds exist |
| `No process matches '…'` | Search found nothing. Every word has to appear somewhere in the name | Use fewer or shorter words. The message suggests near-misses when it can |
| `No TRACI 2.2 methods found` / `Expected 10 …, found N` | `setup/02` did not complete | Re-run `python setup/02_setup_traci22.py` |
| `Preflight smoke test failed — biosphere or TRACI CFs are broken` | 1 kg of CO₂ did not score 1 kg CO₂-eq. Something is wrong beneath the run | Re-run `setup/01` then `setup/02`, then rebuild `03b` and `03` |
| `NonsquareTechnosphere: N activities and M products` | A database was rebuilt without the ones downstream of it. Brightway renumbers internal ids, so the exchanges pointing at them dangle | Rebuild the chain from the step you changed: `01` → `03b` → `03`. Never `03b` alone |
| **All ten scores are 0.0** | Not an error. The run explains which of the three causes applies: no exchanges at all, every input cut off, or flows matched to FEDEFL but uncharacterized by TRACI | Read the printed diagnosis before assuming a bug |

## Foreground CSVs

Validation collects every problem before raising, so one run tells you everything wrong with the
file rather than the first thing.

| Message | What happened | Fix |
|---|---|---|
| `CSV missing required columns: […]` | A header is missing or renamed | The contract is in [`HOWTO.md`](HOWTO.md) §2 |
| `could not be read as UTF-8` | Saved in a legacy encoding | In Excel: Save As → CSV UTF-8 |
| `Row N: provider_uuid '…' not found in … or the current foreground batch` | Wrong UUID, or the process is only in the other build, or a foreground-to-foreground name is misspelled | `--search` the name. If it shows `full` only, add `--database uslci-full`. Foreground UUIDs come from `fg_uuid("Process name")`, so spelling is load-bearing |
| `Row N: unit '…' (energy) is incompatible with reference unit '…' (mass)` | The amount is in a different physical quantity from the counterpart's reference unit | Express it in a compatible unit. Same-property units convert automatically and are reported |
| `Row N: production exchange amount must be > 0` | A zero or negative production amount gives a singular matrix | Fix the amount |
| `Process '…': expected exactly 1 is_ref=true row, found N` | Each process needs one reference, on its production row | Set `is_ref=true` on exactly one production row per process |
| `Foreground CSV has N processes — specify one with --target-process` | More than one process, so the functional unit is ambiguous | Name the one you are measuring |

## When a number looks wrong

Before assuming a bug, check three things in order.

**The functional unit.** Results are per one unit of the target's *declared* reference product, which
is often not the basis you would pick — petroleum refining is per m³, not per kg, an 849× difference.
The runner states it in the console header, in a `functional_unit` column, and in the manifest.

**Completeness.** `validation_manifest.json` reports how much of the solved supply chain was cut. A
result low by a suspicious margin is usually cutoffs rather than arithmetic.

**The electricity vintage.** A build carries one baseline vintage, printed at the top of every run.
Comparing a result against an openLCA export computed on the other vintage will not agree, and
neither number is wrong.

If all three check out and the number still looks wrong,
[open an issue](https://github.com/hotatejunior/fedefl-build-bw25/issues) with the manifest attached.
