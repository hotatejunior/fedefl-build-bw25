#!/usr/bin/env python3
"""
01_setup_biosphere_fedefl.py
----------------------------
CLI front-end over `fedefl_bw25.setup_biosphere` — creates a brightway biosphere
database from the Federal Elementary Flow List (FEDEFL), replacing the default
ecoinvent biosphere3 for EPA-based LCA work. All flows use FEDEFL UUIDs, which
align with TRACI 2.2 (via lciafmt) and USLCI.

All the logic lives in the package; this file parses arguments, prints, and owns
the overwrite prompt. To build from your own script instead::

    from fedefl_bw25.setup_biosphere import import_biosphere
    build = import_biosphere(overwrite=True)
    print(build.flow_count, build.fedefl_version)

Run once per machine / brightway project. Rebuilding renumbers brightway's
internal flow ids, so setup/03b and setup/03 must be re-run afterwards.
"""
from __future__ import annotations

import argparse

from fedefl_bw25.config import BIOSPHERE_DB, PROJECT_NAME
from fedefl_bw25.setup_biosphere import BiosphereExists, import_biosphere


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default=PROJECT_NAME,
                    help=f"brightway project to build into (default: {PROJECT_NAME})")
    ap.add_argument("--yes", "-y", action="store_true",
                    help="rebuild over an existing biosphere database without prompting")
    args = ap.parse_args()

    def confirm(db_name):
        print(f"\nRebuilding renumbers brightway's internal flow ids — "
              f"'electricity-baseline' and both USLCI databases will need rebuilding too.")
        resp = input(f"Delete and rebuild '{db_name}'? [y/N] ")
        return resp.strip().lower() == "y"

    try:
        build = import_biosphere(overwrite=args.yes,
                                 confirm=None if args.yes else confirm,
                                 project=args.project, log=print)
    except BiosphereExists:
        raise SystemExit("Aborted — database not modified.")
    except RuntimeError as e:
        raise SystemExit(str(e))

    import bw2data as bd
    print("\nSample flows:")
    for act in list(bd.Database(BIOSPHERE_DB))[:3]:
        print(f"  {act['name']} | {act['categories']} | {act['unit']} | {act['code']}")


if __name__ == "__main__":
    main()
