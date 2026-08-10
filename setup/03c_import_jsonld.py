#!/usr/bin/env python3
"""
03c_import_jsonld.py
--------------------
CLI front-end over `fedefl_bw25.setup_uslci.import_jsonld` — imports YOUR OWN
olca-schema JSON-LD into brightway, with technosphere links resolved against an
already-built USLCI database.

The zip needs two directories at its top level, and nothing else is read:

    processes/*.json
    flows/*.json

Every link into the background must name its provider on the exchange's
`defaultProvider`. An exchange that resolves to nothing stops the build instead of
becoming a silent cutoff — a cut background link is a silently LOW result.

Typical use, from the repo root::

    # the background, built once
    python setup/03_import_uslci.py --full-db

    # your dataset
    python setup/03c_import_jsonld.py source_data/my_study.zip --database my-study

    # results, exactly as for any USLCI process
    python general/04_run_lca.py --search "widget" --database my-study

All the logic lives in the package; this file parses arguments, prints, and owns
the overwrite prompt. To build from your own script instead::

    from fedefl_bw25.setup_uslci import import_jsonld
    build = import_jsonld("source_data/my_study.zip", db_name="my-study",
                          background=["uslci-full"], overwrite=True)
    print(build.database, build.activity_count, build.totals)

Requires: setup/01, setup/02, and a USLCI background built by setup/03.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import bw2data as bd

from fedefl_bw25.config import (ELECTRICITY_BASELINE_DB, PROJECT_NAME, USLCI_DB,
                                USLCI_FULL_DB)
from fedefl_bw25.setup_uslci import DEFAULT_BUNDLE_DIR, UslciExists, import_jsonld


def _env_flag(name):
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def default_background():
    """The USLCI build to link against, plus the electricity baseline if present.

    Prefers the full build: it is the one that can actually supply an arbitrary
    provider UUID, where the bundle build only holds what its bundles shipped. The
    choice changes results, so the caller PRINTS what this returned rather than
    letting it be silent, and the build stamps it onto the database either way.
    """
    bd.projects.set_current(PROJECT_NAME)
    picked = [n for n in (USLCI_FULL_DB, USLCI_DB) if n in bd.databases][:1]
    if ELECTRICITY_BASELINE_DB in bd.databases:
        picked.append(ELECTRICITY_BASELINE_DB)
    return picked


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", type=Path,
                    help=f"path to your olca-schema JSON-LD zip, e.g. "
                         f"{DEFAULT_BUNDLE_DIR / 'my_study.zip'}")
    ap.add_argument("--database", required=True,
                    help="brightway database name to write, e.g. 'my-study'. Must not "
                         "be an existing USLCI build.")
    ap.add_argument("--background", default=None,
                    help="comma-separated built databases to resolve links against, "
                         "in precedence order (default: the full USLCI build if "
                         "present, else the bundle build, plus the electricity "
                         "baseline). This choice changes results and is stamped onto "
                         "the database.")
    ap.add_argument("--allow-unhinted-links", action="store_true",
                    default=_env_flag("ALLOW_UNHINTED_LINKS"),
                    help="import anyway when exchanges fail to resolve to a provider, "
                         "cutting them, instead of hard-stopping. Exploratory use "
                         "only — a cut background link silently lowers every result. "
                         "Also settable as ALLOW_UNHINTED_LINKS=1.")
    ap.add_argument("--allow-unit-passthrough", action="store_true",
                    default=_env_flag("ALLOW_UNIT_PASSTHROUGH"),
                    help="permit unrecognized unit strings to pass through WITHOUT "
                         "conversion, with a loud warning, instead of hard-stopping. "
                         "Also settable as ALLOW_UNIT_PASSTHROUGH=1.")
    ap.add_argument("--yes", "-y", action="store_true",
                    help="overwrite an existing database without prompting")
    args = ap.parse_args()

    if args.database in (USLCI_DB, USLCI_FULL_DB):
        raise SystemExit(
            f"Refusing to write '{args.database}' — that is a USLCI build name, and "
            f"overwriting it with a custom import would leave the validation harness "
            f"pointed at a database that is not USLCI. Pick another name.")

    background = ([b.strip() for b in args.background.split(",") if b.strip()]
                  if args.background else default_background())
    if not background:
        raise SystemExit(
            "No background database found to link against. Build one first:\n"
            "  python setup/03_import_uslci.py --full-db")

    print("=" * 72)
    print(f"  Source      : {args.source}")
    print(f"  Database    : '{args.database}'")
    print(f"  Background  : {', '.join(background)}"
          f"{'' if args.background else '   [auto-detected — set with --background]'}")
    print("=" * 72)

    def confirm(db_name):
        resp = input(f"Database '{db_name}' already exists. Delete and rebuild? [y/N] ")
        return resp.strip().lower() == "y"

    try:
        build = import_jsonld(
            args.source, db_name=args.database, background=background,
            allow_unit_passthrough=args.allow_unit_passthrough,
            allow_unhinted_links=args.allow_unhinted_links,
            overwrite=args.yes, confirm=None if args.yes else confirm, log=print,
        )
    except UslciExists:
        raise SystemExit("Aborted — database not modified.")
    except RuntimeError as e:
        raise SystemExit(str(e))

    print("\nDone.", end=" ")
    if len(build.processes) == 1:
        print("Run it with:")
        print(f"  python general/04_run_lca.py --uuid {build.target} "
              f"--database {build.database}")
    else:
        # Naming a real root beats "--search ''" — for a 50-process dataset that
        # dumps every intermediate and buries the thing you meant to run.
        first = build.roots[0] if build.roots else next(iter(build.processes))
        print(f"Run one of the {len(build.roots)} study target(s), e.g. "
              f"'{build.processes[first][:48]}':")
        print(f"  python general/04_run_lca.py --uuid {first} --database {build.database}")
        print(f"\nOr find one by name:")
        print(f"  python general/04_run_lca.py --search \"crate\" --database {build.database}")
        print(f"\nOr run them all — see examples/jsonld_study.py --all")


if __name__ == "__main__":
    main()
