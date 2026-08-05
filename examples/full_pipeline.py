#!/usr/bin/env python3
"""
full_pipeline.py — the whole pipeline in one file.

Everything the numbered scripts do, as library calls: build the conversion table,
the FEDEFL biosphere, the TRACI methods, the electricity baseline and the USLCI
database, then run a study across several processes and write one results CSV.

    python examples/full_pipeline.py            # build what's missing, run the study
    python examples/full_pipeline.py --rebuild  # rebuild everything from scratch
    python examples/full_pipeline.py --study    # skip setup, just run the study

Setup is idempotent here: each step raises its own `…Exists` when the database is
already there, and this file treats that as "nothing to do" unless --rebuild is
passed. That is the whole reason the setup steps take `overwrite=`/`confirm=`
instead of prompting — a scripted build never blocks on stdin.

Copy this file and edit the CONFIGURE block; it is meant to be a starting point
for your own study, not a fixed program. `pip install -e .` first.

One ordering constraint the code below encodes: the electricity baseline must be
injected BEFORE the USLCI import, because the importer's relink fallback resolves
grid references against it. Rebuilding an earlier step invalidates the later ones —
brightway renumbers internal ids, and a half-rebuilt chain goes non-square — so
--rebuild always redoes the whole chain rather than a piece of it.
"""
from __future__ import annotations

import argparse

from fedefl_bw25.config import PROJECT_NAME, USLCI_DB, USLCI_FULL_DB, REPO_ROOT
from fedefl_bw25.run import run_lca, write_results_csv
from fedefl_bw25.setup_baseline import BaselineExists, inject_baseline
from fedefl_bw25.setup_biosphere import BiosphereExists, import_biosphere
from fedefl_bw25.setup_conversions import describe_conversions
from fedefl_bw25.setup_traci import import_traci
from fedefl_bw25.setup_uslci import UslciExists, import_uslci

# =============================================================================
# CONFIGURE
# =============================================================================
PROJECT = PROJECT_NAME    # brightway project to build into

# Electricity-baseline vintage. One per build — match it to the study, and to any
# openLCA export you mean to compare against. None auto-detects from the bundles.
VINTAGE = "2026"

# True imports the entire USLCI database (~1,455 activities, into 'uslci-full');
# False imports only the per-process bundle zips (into 'uslci-subset').
FULL_DB = True
DATABASE = USLCI_FULL_DB if FULL_DB else USLCI_DB

# The study: label -> USLCI process UUID. Labels become the scenario column.
TARGETS = {
    "petroleum refining": "0aaf1e13-5d80-37f9-b7bb-81a6b8965c71",
    "corn, at field":     "11256034-2355-3add-ade9-59983025dded",
    "portland cement":    "62993671-574c-3fc5-b66a-6be3bb21ad3d",
    "steel billets":      "ac54bc7d-5db5-3b4f-9175-5dd02f678312",
}

# A foreground CSV instead of / alongside UUID targets — see HOWTO.md §2.
# Set to a path and give TARGET_PROCESS the row it should measure.
FOREGROUND_CSV = None
TARGET_PROCESS = None

RESULTS_CSV = REPO_ROOT / "lca_results_pipeline.csv"   # lca_results*.csv is gitignored
HEADLINE = "Global warming"   # category echoed to the console per target


# =============================================================================
# SETUP — steps 00, 01, 02, 03b, 03
# =============================================================================
def setup(*, rebuild=False, vintage=VINTAGE, full_db=FULL_DB, project=PROJECT,
          log=print):
    """Build every database the study needs. Returns what each step produced.

    Steps already built are skipped unless `rebuild=True`. The conversion table is
    never rebuilt here — it ships committed, needs the full USLCI zip, and changing
    it is a validation event (see fedefl_bw25/setup_conversions.py).
    """
    built = {}

    meta = describe_conversions()
    if meta is None:
        raise SystemExit(
            "No flow-conversion table. Run "
            "`python setup/00_build_flow_conversion_table.py --rebuild` first "
            "(needs the full USLCI zip in source_data/)."
        )
    log(f"[00] conversion table: {meta.get('flow_count', '?')} flows, "
        f"built {meta.get('generated_at', '?')} from {meta.get('source_zip', '?')}")

    try:
        built["biosphere"] = import_biosphere(overwrite=rebuild, project=project)
        log(f"[01] biosphere: {built['biosphere'].flow_count} flows "
            f"(fedelemflowlist {built['biosphere'].fedefl_version})")
    except BiosphereExists:
        log("[01] biosphere: already built — skipping")

    # TRACI has no exists-guard: methods carry no downstream ids, so rewriting them
    # invalidates nothing. Registering 10 methods is seconds; the download is cached.
    built["traci"] = import_traci(project=project)
    log(f"[02] TRACI 2.2: {len(built['traci'].methods)} methods, "
        f"{built['traci'].total_matched} CFs")

    try:
        built["baseline"] = inject_baseline(vintage=vintage, overwrite=rebuild,
                                            project=project)
        log(f"[03b] electricity baseline: {built['baseline'].activity_count} "
            f"activities, {built['baseline'].vintage} vintage "
            f"({built['baseline'].vintage_source})")
    except BaselineExists:
        log("[03b] electricity baseline: already built — skipping")

    try:
        built["uslci"] = import_uslci(full_db=full_db, overwrite=rebuild,
                                      project=project)
        log(f"[03] USLCI: '{built['uslci'].database}' — "
            f"{built['uslci'].activity_count} activities, "
            f"{built['uslci'].totals['tech_unlinked']} unlinked exchanges")
    except UslciExists:
        log("[03] USLCI: already built — skipping")

    return built


# =============================================================================
# STUDY — step 04, once per target, into one CSV
# =============================================================================
def study(*, targets=TARGETS, database=DATABASE, out=RESULTS_CSV, project=PROJECT,
          log=print):
    """Run every target and accumulate the scores in one CSV. Returns the runs."""
    runs = []
    for i, (label, uuid) in enumerate(targets.items()):
        run = run_lca(uuid=uuid, database=database, scenario=label, project=project)
        # append=False on the first target so a re-run replaces the file rather
        # than growing it; every later target appends into the same table.
        write_results_csv(run, out, append=i > 0)
        runs.append(run)
        log(f"  {label:<22} {run.score(HEADLINE):>14.6g} "
            f"{run.unit(HEADLINE)} / {run.functional_unit}")

    if FOREGROUND_CSV:
        run = run_lca(foreground=FOREGROUND_CSV, target_process=TARGET_PROCESS,
                      database=database, scenario="foreground", project=project)
        write_results_csv(run, out, append=True)
        runs.append(run)
        log(f"  {'foreground':<22} {run.score(HEADLINE):>14.6g} "
            f"{run.unit(HEADLINE)} / {run.functional_unit}")

    log(f"\n{len(runs)} scenario(s) written to {out}")
    log(f"Chart them with: python general/06_visualize.py --results {out}")
    return runs


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rebuild", action="store_true",
                    help="rebuild every database even if it already exists")
    ap.add_argument("--setup-only", action="store_true", help="build, don't run the study")
    ap.add_argument("--study", action="store_true", help="run the study, skip setup")
    args = ap.parse_args()

    if not args.study:
        print("=" * 72)
        setup(rebuild=args.rebuild)
        print("=" * 72)
    if not args.setup_only:
        print(f"\nStudy — {HEADLINE}, from '{DATABASE}':")
        study()


if __name__ == "__main__":
    main()
