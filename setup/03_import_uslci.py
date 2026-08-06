#!/usr/bin/env python3
"""
03_import_uslci.py
------------------
CLI front-end over `fedefl_bw25.setup_uslci` — imports USLCI openLCA JSON-LD into
brightway25, parsing it directly rather than through `bw2io`'s JSONLDImporter,
which has known bugs with the `isInput` field.

Two builds, written to separate databases so they coexist:
  default      per-process bundle zips   -> 'uslci-subset'  (the validated set)
  --full-db    the single full USLCI zip -> 'uslci-full'     (every process)

All the logic lives in the package; this file parses arguments, prints, and owns
the overwrite prompt. To build from your own script instead::

    from fedefl_bw25.setup_uslci import import_uslci
    build = import_uslci(full_db=True, overwrite=True)
    print(build.database, build.activity_count, build.totals)

Download processes from the LCA Commons and place the zips in source_data/:
  https://www.lcacommons.gov/lca-collaboration/National_Renewable_Energy_Laboratory/USLCI_Database_Public

Requires: setup/01_setup_biosphere_fedefl.py must have been run first, and
setup/03b_import_electricity_baseline.py before this so the relink fallback has
something to resolve against.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from fedefl_bw25.setup_uslci import DEFAULT_BUNDLE_DIR, UslciExists, import_uslci


def _env_flag(name):
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--full-db", action="store_true", default=_env_flag("USLCI_FULL_DB"),
                    help="import the ENTIRE USLCI database from the single full zip "
                         "into 'uslci-full', instead of the per-process bundles into "
                         "'uslci-subset'. Also settable as USLCI_FULL_DB=1.")
    ap.add_argument("--bundle-dir", type=Path, default=DEFAULT_BUNDLE_DIR,
                    help="directory holding the USLCI zips (default: source_data/; "
                         "also settable as SOURCE_DATA_DIR)")
    ap.add_argument("--allow-unit-passthrough", action="store_true",
                    default=_env_flag("ALLOW_UNIT_PASSTHROUGH"),
                    help="permit unrecognized unit strings to pass through WITHOUT "
                         "conversion, with a loud warning, instead of hard-stopping. "
                         "Exploratory imports only — an unconverted unit is a wrong "
                         "number wearing a plausible one's clothes (ledger #7). Also "
                         "settable as ALLOW_UNIT_PASSTHROUGH=1.")
    ap.add_argument("--yes", "-y", action="store_true",
                    help="overwrite an existing database without prompting")
    args = ap.parse_args()

    def confirm(db_name):
        resp = input(f"Database '{db_name}' already exists. Delete and rebuild? [y/N] ")
        return resp.strip().lower() == "y"

    try:
        import_uslci(
            full_db=args.full_db, bundle_dir=args.bundle_dir,
            allow_unit_passthrough=args.allow_unit_passthrough,
            overwrite=args.yes, confirm=None if args.yes else confirm, log=print,
        )
    except UslciExists:
        raise SystemExit("Aborted — database not modified.")
    except RuntimeError as e:
        raise SystemExit(str(e))


if __name__ == "__main__":
    main()
