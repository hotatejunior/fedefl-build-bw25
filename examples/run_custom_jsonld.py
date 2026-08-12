#!/usr/bin/env python3
"""
Import a parameterized olca-schema zip and score every product in it.

    python examples/run_custom_jsonld.py my_study.zip my-study

Prerequisites, built once (see docs/HOWTO.md):

    python setup/01_setup_biosphere_fedefl.py
    python setup/02_setup_traci22.py
    python setup/03_import_uslci.py --full-db

`evaluate_formulas=True` is what makes this a parameterized run: exchange amounts
are computed from their formulas and the dataset's parameters, so editing a
parameter in the JSON and re-running this script moves the result. Without it the
literal `amount` in the file is used and the parameters are inert.
"""
import sys

from fedefl_bw25.run import run_lca, write_results_csv
from fedefl_bw25.setup_uslci import import_jsonld

ZIP = sys.argv[1] if len(sys.argv) > 1 else "source_data/my_study.zip"
DB = sys.argv[2] if len(sys.argv) > 2 else "my-study"
OUT = "results.csv"

# Drop "electricity-baseline" if you have not built it.
build = import_jsonld(ZIP, db_name=DB, background=["uslci-full", "electricity-baseline"],
                      evaluate_formulas=True, overwrite=True, log=print)

# build.roots is every product nothing else in the database consumes — the study
# targets, without hand-listing UUIDs. Use build.processes for all of them instead.
for i, code in enumerate(build.roots):
    run = run_lca(uuid=code, database=DB, smoke_test=(i == 0))
    write_results_csv(run, OUT, append=True)          # one row group per product
    print(f"  {build.processes[code]:.<60.60} {run.score('Global warming'):>12.6g}")

print(f"\n{len(build.roots)} product(s) scored across 10 TRACI categories -> {OUT}")
