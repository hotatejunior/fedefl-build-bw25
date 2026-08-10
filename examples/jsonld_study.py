#!/usr/bin/env python3
"""
jsonld_study.py — your own olca-schema JSON-LD, linked to USLCI, from a script.

The two-command CLI path is::

    python setup/03_import_uslci.py --full-db
    python setup/03c_import_jsonld.py source_data/my_study.zip --database my-study
    python general/04_run_lca.py --uuid <uuid> --database my-study

This file is the same thing as library calls, plus the part the CLI cannot do:
GENERATE the JSON per scenario and sweep. That is the point of a scripted study —
the inventory becomes a function of your parameters instead of a file you hand-edit
between runs.

    python examples/jsonld_study.py            # one import, one result
    python examples/jsonld_study.py --sweep    # a scenario per recycled-content value

Copy and edit the CONFIGURE block. `pip install -e .` first, and build the USLCI
background once (`python setup/03_import_uslci.py --full-db`).

On cost: each scenario below re-imports and re-solves, which is a few seconds. That
is fine for tens of scenarios and the wrong shape for thousands — with the
background pinned, a foreground result is linear in its exchange amounts, so a
large sweep wants the background unit scores computed once. See docs/ROADMAP.md.
"""
from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

from fedefl_bw25.config import ELECTRICITY_BASELINE_DB, REPO_ROOT, USLCI_FULL_DB
from fedefl_bw25.run import run_lca, write_results_csv
from fedefl_bw25.setup_uslci import import_jsonld

# =============================================================================
# CONFIGURE
# =============================================================================
DATABASE   = "my-study"                     # brightway database to write
BACKGROUND = [USLCI_FULL_DB, ELECTRICITY_BASELINE_DB]   # ordered; first match wins
SOURCE     = REPO_ROOT / "source_data" / "my_study.zip"
RESULTS    = REPO_ROOT / "lca_results_jsonld.csv"       # lca_results*.csv is gitignored
HEADLINE   = "Global warming"

# The background process this foreground draws on. Find yours with:
#   python general/04_run_lca.py --search "hdpe flake" --database uslci-full
HDPE_PROC = "17664c37-72c0-4813-a4b9-93f962962c63"
HDPE_FLOW = "c670f34f-b874-4928-89c0-a64ff78d7053"   # its reference product flow
CO2       = "d859bef1-3a4e-33a3-a86f-e6a1f617c53f"   # FEDEFL, has a TRACI CF
MASS_FP   = "93a60a56-a3c8-11da-a746-0800200b9a66"   # openLCA's Mass flow property

CRATE_PROC = "aa11bb22-cc33-dd44-ee55-ff6677889900"  # your own UUIDs — any v4 will do
CRATE_FLOW = "11223344-5566-7788-99aa-bbccddeeff00"


# =============================================================================
# GENERATE — the inventory as a function of its parameters
# =============================================================================
def write_jsonld(path, *, hdpe_kg=1.05, co2_g=250.0):
    """Write a minimal olca-schema zip: two directories, nothing else.

    Only `processes/` and `flows/` are read, so a generated dataset writes those
    two and skips everything else an openLCA export carries.

    The technosphere exchange MUST carry `defaultProvider` naming the background
    process — that is what links it to USLCI, and an exchange without one stops the
    import rather than becoming a silent cutoff.
    """
    crate = {
        "@type": "Process", "@id": CRATE_PROC,
        "name": "Recycled HDPE crate; at plant", "processType": "UNIT_PROCESS",
        "version": "01.00.000", "lastChange": "2026-08-10T12:00:00Z",
        "location": {"name": "United States of America (the)"},
        "exchanges": [
            {"internalId": 1, "isInput": False, "isQuantitativeReference": True,
             "flow": {"@id": CRATE_FLOW, "flowType": "PRODUCT_FLOW",
                      "name": "Recycled HDPE crate"},
             "flowProperty": {"@id": MASS_FP}, "unit": {"name": "kg"}, "amount": 1.0},
            {"internalId": 2, "isInput": True,
             "flow": {"@id": HDPE_FLOW, "flowType": "PRODUCT_FLOW",
                      "name": "Recycled postconsumer HDPE; flake"},
             "flowProperty": {"@id": MASS_FP}, "unit": {"name": "kg"},
             "amount": hdpe_kg,
             "defaultProvider": {"@id": HDPE_PROC}},          # <- the USLCI link
            {"internalId": 3, "isInput": False,
             "flow": {"@id": CO2, "flowType": "ELEMENTARY_FLOW", "name": "Carbon dioxide"},
             # Units are converted onto the flow's reference unit, so g is fine here.
             "flowProperty": {"@id": MASS_FP}, "unit": {"name": "g"}, "amount": co2_g},
        ],
    }
    crate_flow = {
        "@type": "Flow", "@id": CRATE_FLOW, "name": "Recycled HDPE crate",
        "flowType": "PRODUCT_FLOW",
        "flowProperties": [{"referenceFlowProperty": True, "conversionFactor": 1.0,
                            "flowProperty": {"@id": MASS_FP, "name": "Mass",
                                             "refUnit": "kg"}}],
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"processes/{CRATE_PROC}.json", json.dumps(crate, indent=2))
        z.writestr(f"flows/{CRATE_FLOW}.json", json.dumps(crate_flow, indent=2))
    return path


# =============================================================================
# IMPORT + RUN — the two CLI commands, as calls
# =============================================================================
def build_and_run(source, *, scenario, database=DATABASE, log=print):
    """Import the zip and score it. Returns the `LcaRun`.

    `overwrite=True` is what makes this scriptable: the CLI prompts before
    replacing a database, and a library call must never block on stdin.
    """
    build = import_jsonld(source, db_name=database, background=BACKGROUND,
                          overwrite=True)
    # The build names what it just wrote, so nothing has to re-read the JSON to
    # find the UUID. `.target` is the sole activity; use `.processes` when there
    # are several.
    run = run_lca(uuid=build.target, database=build.database, scenario=scenario,
                  contributions=False, smoke_test=False)
    log(f"  {scenario:<28} {run.score(HEADLINE):>12.6g} "
        f"{run.unit(HEADLINE)} / {run.functional_unit}")
    return run


# =============================================================================
# MANY PROCESSES — a USLCI-expansion dataset, scored in one pass
# =============================================================================
def run_all(source, *, database=DATABASE, out=RESULTS, log=print):
    """Import a multi-process zip and score every study target in it.

    IMPORT ONCE, SOLVE MANY. The database is built a single time; each target is
    then just another functional unit against it. Re-importing per target would be
    the same database N times over.

    `build.roots` is the shortlist: the processes nothing else in the dataset
    consumes. In a 50-process expansion most entries are intermediates that exist
    to be drawn on, and scoring those as if they were products is rarely what a
    study wants — `build.processes` is there when it is.
    """
    build = import_jsonld(source, db_name=database, background=BACKGROUND,
                          overwrite=True)
    targets = build.named()          # {code: name} for the roots
    log(f"\n{len(build.processes)} activities imported, "
        f"{len(targets)} study target(s):\n")

    runs = []
    for i, (code, name) in enumerate(sorted(targets.items(), key=lambda kv: kv[1])):
        run = run_lca(uuid=code, database=build.database, scenario=name,
                      contributions=False, manifest=False, smoke_test=False)
        write_results_csv(run, out, append=i > 0)
        runs.append(run)
        log(f"  {name[:46]:<46} {run.score(HEADLINE):>12.6g} "
            f"{run.unit(HEADLINE)}")

    log(f"\n{len(runs)} scenario(s) written to {out}")
    log(f"Chart them with: python general/06_visualize.py --results {out}")
    return runs


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sweep", action="store_true",
                    help="regenerate the inventory across a range of HDPE inputs "
                         "and write one scenario per value")
    ap.add_argument("--all", action="store_true",
                    help="import an existing multi-process zip and score every study "
                         "target in it — the shape for a USLCI-expansion dataset")
    ap.add_argument("--source", type=Path, default=None,
                    help=f"zip to import (default: {SOURCE.name}). With --all, point "
                         f"this at your own multi-process dataset.")
    ap.add_argument("--database", default=None,
                    help=f"database to write (default: {DATABASE})")
    args = ap.parse_args()

    database = args.database or DATABASE
    print(f"Study — {HEADLINE}, '{database}' on {', '.join(BACKGROUND)}:")

    if args.all:
        run_all(args.source or SOURCE, database=database)
        return

    if not args.sweep:
        write_jsonld(SOURCE)
        run = build_and_run(SOURCE, scenario="crate, 1.05 kg HDPE", database=database)
        write_results_csv(run, RESULTS)
        print(f"\nWritten to {RESULTS}")
        return

    runs = []
    for i, hdpe_kg in enumerate((0.90, 1.05, 1.20, 1.35)):
        write_jsonld(SOURCE, hdpe_kg=hdpe_kg)
        run = build_and_run(SOURCE, scenario=f"HDPE {hdpe_kg:.2f} kg", database=database)
        write_results_csv(run, RESULTS, append=i > 0)
        runs.append(run)

    print(f"\n{len(runs)} scenario(s) written to {RESULTS}")
    print(f"Chart them with: python general/06_visualize.py --results {RESULTS}")


if __name__ == "__main__":
    main()
