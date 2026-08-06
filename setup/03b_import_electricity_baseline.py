#!/usr/bin/env python3
"""
03b_import_electricity_baseline.py
-----------------------------------
CLI front-end over `fedefl_bw25.setup_baseline` — injects the US Electricity
Baseline into brightway as aggregated background activities, so downstream LCIA
runs include electricity upstream instead of treating it as a cutoff.

All the logic lives in the package; this file parses arguments, prints, and owns
the overwrite prompt. To build from your own script instead::

    from fedefl_bw25.setup_baseline import inject_baseline
    build = inject_baseline(vintage="2026", overwrite=True)
    print(build.vintage, build.activity_count, build.library)

Requires: setup/01_setup_biosphere_fedefl.py must have been run first.
Run before setup/03_import_uslci.py so its flow-based relink fallback has
something to resolve against.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fedefl_bw25.setup_baseline import (DEFAULT_BUNDLE_DIR, VINTAGES, BaselineExists,
                                        inject_baseline)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--vintage", choices=sorted(VINTAGES), default=None,
                    help="electricity-baseline vintage to inject. Default: "
                         "auto-detected from the bundles' own grid references "
                         "(the 4 locked cases resolve to 2025; the HDPE/PET and "
                         "2026-drop bundles to 2026). One vintage per build; pass "
                         "this explicitly to override detection or to break a "
                         "mixed-bundle tie.")
    ap.add_argument("--library", type=Path, default=None,
                    help="explicit path to a baseline library zip, overriding the "
                         "vintage's auto-detect/fetch (still stamped with the "
                         "selected vintage)")
    ap.add_argument("--no-fetch", action="store_true",
                    help="don't download the baseline if it's missing locally")
    ap.add_argument("--bundle-dir", type=Path, default=DEFAULT_BUNDLE_DIR,
                    help="directory to glob USLCI bundle zips from")
    ap.add_argument("--yes", "-y", action="store_true",
                    help="overwrite an existing electricity-baseline database "
                         "without prompting (for scripted rebuilds)")
    args = ap.parse_args()

    def confirm(db_name):
        resp = input(f"\nDatabase '{db_name}' already exists. Delete and rebuild? [y/N] ")
        return resp.strip().lower() == "y"

    try:
        inject_baseline(
            vintage=args.vintage, library=args.library, fetch=not args.no_fetch,
            bundle_dir=args.bundle_dir, overwrite=args.yes,
            confirm=None if args.yes else confirm, log=print,
        )
    except BaselineExists:
        raise SystemExit("Aborted — database not modified.")
    except RuntimeError as e:
        raise SystemExit(str(e))


if __name__ == "__main__":
    main()
