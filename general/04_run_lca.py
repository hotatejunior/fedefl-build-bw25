#!/usr/bin/env python3
"""
04_run_lca.py
-------------
CLI front-end over `fedefl_bw25.run` — LCIA across all 10 TRACI 2.2 categories for
either a USLCI process (by UUID) or a foreground system loaded from a CSV.

All the logic lives in the package; this file parses arguments, prints, and writes
files. If you want to drive the pipeline from your own script, skip this and call
the library directly::

    from fedefl_bw25.run import run_lca, write_results_csv

    run = run_lca(uuid="0aaf1e13-5d80-37f9-b7bb-81a6b8965c71", database="uslci-full")
    print(run.score("Global warming"), run.functional_unit)
    write_results_csv(run, "study.csv", append=True)

That is also how you sweep: loop, collect `LcaRun` objects, write once.

Usage
-----
IDE / notebook: edit the CONFIG block below and run directly.

Terminal (from repo root):
  python general/04_run_lca.py [--uuid UUID] [--foreground CSV] [--target-process NAME] [--output CSV]

Requires: setup/01, setup/02, setup/03 to have been run, and `pip install -e .`.
"""

import argparse
from pathlib import Path

from fedefl_bw25 import run_manifest
from fedefl_bw25.config import USLCI_DB, USLCI_FULL_DB, REPO_ROOT

# fedefl_bw25.run pulls in bw2calc, which is slow to import and prints a solver
# warning on ARM. --search needs neither, so the import waits until after it.

# =============================================================================
# CONFIG  — edit here for IDE / notebook use; CLI args override at runtime
# =============================================================================
# These are the SHIPPED DEFAULTS — a neutral starting point, not a saved session.
# Edit freely for your own runs; just avoid committing a personal target back, so
# the repo keeps landing on the same known-good example for the next reader.
#
# Which USLCI build to run against. USLCI_DB is the per-process bundle set the
# locked validation cases were computed against (the default, matching
# --database's); USLCI_FULL_DB is the whole database from
# `USLCI_FULL_DB=1 setup/03_import_uslci.py`, needed to reach processes no
# bundle shipped. Overridden by --database.
USLCI_DATABASE    = USLCI_DB
# Petroleum refining, US — a locked validation case, so its result is a known
# quantity to check a fresh build against. Note its reference unit is m3, not kg
# (see docs/HOWTO.md §1).
PROCESS_UUID      = "0aaf1e13-5d80-37f9-b7bb-81a6b8965c71"
FOREGROUND_CSV    = None   # path to foreground inventory CSV, or None to skip
TARGET_PROCESS    = None   # foreground process_name to use as functional unit
                           # required when CSV has >1 process; ignored without CSV
OUTPUT_CSV        = REPO_ROOT / "lca_results.csv"        # CLI --output resolves against CWD instead
CONTRIBUTIONS_CSV = REPO_ROOT / "lca_contributions.csv"  # per-process scores; set None to skip
MANIFEST_JSON     = REPO_ROOT / run_manifest.MANIFEST_FILENAME  # per-run audit manifest; set None to skip
SCENARIO_LABEL    = None   # human label for this run; defaults to target process name
APPEND_RESULTS    = False  # True (or --append) accumulates scenarios in OUTPUT_CSV
                           # instead of overwriting — how you build a comparison set

# =============================================================================
# CLI
# =============================================================================
parser = argparse.ArgumentParser(
    description="Run LCIA for a USLCI process or foreground system."
)
parser.add_argument("--search",            default=None, metavar="TEXT",
                    help="find a process by name instead of UUID, then exit. Every "
                         "word must appear somewhere in the name, in any order: "
                         "'hdpe flake' finds 'Recycled postconsumer high-density "
                         "polyethylene, HDPE, flake; at plant'. Searches every built "
                         "USLCI database and names which one each hit is in. Pass an "
                         "empty string to list everything.")
parser.add_argument("--limit",             default=None, type=int,
                    help="how many search results to show (default 20; 0 for all)")
parser.add_argument("--uuid",              default=None, help="USLCI target process UUID")
parser.add_argument("--foreground",        default=None, help="Path to foreground inventory CSV")
parser.add_argument("--target-process",    default=None, dest="target_process",
                    help="Foreground process_name to use as functional unit")
parser.add_argument("--output",            default=None, help="Output CSV path (default: lca_results.csv at repo root)")
parser.add_argument("--contributions",     default=None, help="Contributions CSV path (default: lca_contributions.csv at repo root)")
parser.add_argument("--scenario",          default=None, help="Scenario label for output CSVs (default: target process name)")
parser.add_argument("--no-contributions",  action="store_true", dest="no_contributions",
                    help="Skip writing the per-process contributions CSV")
parser.add_argument("--manifest",          default=None,
                    help="Audit manifest JSON path (default: validation_manifest.json at repo root)")
parser.add_argument("--no-manifest",       action="store_true", dest="no_manifest",
                    help="Skip writing the per-run audit manifest")
parser.add_argument("--append",            action="store_true", dest="append_results",
                    help="Append this run's scores to the results CSV instead of "
                         "overwriting it, accumulating scenarios for general/06's "
                         "scenario-comparison chart. Re-running the same scenario "
                         "label replaces its rows rather than duplicating them.")
parser.add_argument("--database",          default=None, choices=[USLCI_DB, USLCI_FULL_DB],
                    help=f"Which USLCI build to run against (default: {USLCI_DB}, the "
                         f"per-process bundle set the locked validation cases were computed "
                         f"against). '{USLCI_FULL_DB}' is the whole-database build from "
                         f"USLCI_FULL_DB=1 setup/03_import_uslci.py.")
args = parser.parse_args()

# --search answers "what is this process called?", which is the question a
# practitioner actually arrives with. It runs before anything else is resolved and
# exits, so it works on a build you haven't chosen a target in yet.
if args.search is not None:
    from fedefl_bw25.search import (DEFAULT_LIMIT, format_matches, load_processes,
                                    rank_entries)
    limit = DEFAULT_LIMIT if args.limit is None else args.limit
    try:
        processes = load_processes(args.database)
    except RuntimeError as e:
        raise SystemExit(str(e))
    matches, total = rank_entries(processes, args.search, limit=limit)
    print(format_matches(matches, total, args.search, all_processes=processes))
    raise SystemExit(0 if matches else 1)

from fedefl_bw25.run import (run_lca, write_results_csv,        # noqa: E402
                             write_contributions_csv, write_manifest_json)

database   = args.database or USLCI_DATABASE
db_source  = "--database" if args.database else "CONFIG USLCI_DATABASE"
uuid       = args.uuid or PROCESS_UUID
foreground = args.foreground or FOREGROUND_CSV
target     = args.target_process or TARGET_PROCESS
scenario   = args.scenario or SCENARIO_LABEL
out_path   = Path(args.output or OUTPUT_CSV)
contrib_path = (None if args.no_contributions
                else Path(args.contributions) if args.contributions
                else Path(CONTRIBUTIONS_CSV) if CONTRIBUTIONS_CSV else None)
manifest_path = (None if args.no_manifest
                 else Path(args.manifest) if args.manifest
                 else Path(MANIFEST_JSON) if MANIFEST_JSON else None)
append = args.append_results or APPEND_RESULTS

# =============================================================================
# BANNER — say what the run is standing on, before anything else prints
# =============================================================================
# The bundle build's name invites a wrong mental model: it is not "the 9 validated
# processes", it is those 9 plus every upstream process their bundles shipped, so
# a lookup can succeed on a process nobody deliberately imported. Naming the
# composition is what makes a result interpretable at a glance.
from fedefl_bw25.run import open_project, describe_build   # noqa: E402  (after arg parse)

open_project()
build = describe_build(database)
print("=" * 72)
print(f"  USLCI build      : '{build['database']}'  ({build['activity_count']} activities)"
      f"   [set by {db_source}]")
print(f"  Composition      : {build['composition']}")
print(f"  Built from       : {build['source']}")
print(f"  Electricity grid : {build['electricity_vintage']} baseline")
for other, count in build["other_builds"].items():
    print(f"  Also built       : '{other}' ({count} activities) — switch with --database")
print("=" * 72)

if foreground is not None and args.uuid is not None:
    print(f"NOTE: --foreground and --uuid both provided — --uuid '{uuid}' will be ignored.")

for p in (out_path, contrib_path):
    if p and p.exists():
        print(f"WARNING: '{p}' already exists and will be overwritten.")

# =============================================================================
# RUN
# =============================================================================
run = run_lca(
    uuid=uuid, foreground=foreground, target_process=target, database=database,
    scenario=scenario, contributions=contrib_path is not None,
    manifest=manifest_path is not None, log=print,
)

print(f"=== Target: '{run.target['name']}' ===")
print(f"    UUID:     {run.target['uuid']}")
print(f"    Scenario: {run.scenario}")
print(f"    Functional unit: {run.functional_unit} (the process's reference unit — "
      f"every score below is per {run.functional_unit} of this product)")
if run.electricity_vintage["status"] == "ok":
    print(f"    Electricity baseline: {run.electricity_vintage['vintage']} vintage "
          f"(background grid for this run)")
else:
    print(f"    Electricity baseline: {run.electricity_vintage['status'].upper()} — "
          f"{run.electricity_vintage['note']}")
print()

# =============================================================================
# WRITE
# =============================================================================
n_scen = write_results_csv(run, out_path, append=append)
if append:
    print(f"Results appended to: {out_path} ({n_scen} scenario(s) now in file)")
else:
    print(f"Results written to: {out_path}")

if contrib_path:
    n = write_contributions_csv(run, contrib_path)
    if n:
        print(f"Contributions written to: {contrib_path} ({n} rows)")

if manifest_path:
    written = write_manifest_json(run, manifest_path)
    if written:
        print(f"Audit manifest written to: {written}")
        if not run.manifest["completeness"]["fully_linked"]:
            print("  NOTE: this result is not fully linked — see completeness.notes "
                  "in the manifest.")
