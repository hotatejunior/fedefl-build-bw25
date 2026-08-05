#!/usr/bin/env python3
"""
02_setup_traci22.py
-------------------
CLI front-end over `fedefl_bw25.setup_traci` — imports TRACI 2.2 characterization
factors into brightway25 via lciafmt, one Method per impact category, with flows
keyed to the FEDEFL biosphere database by UUID (no name matching, since lciafmt and
fedelemflowlist share a UUID space).

All the logic lives in the package; this file parses arguments and prints. To
build from your own script instead::

    from fedefl_bw25.setup_traci import import_traci
    build = import_traci()
    print(build.total_matched, build.cf_counts)

Requires:
  - lciafmt  : pip install git+https://github.com/USEPA/LCIAformatter.git
  - setup/01_setup_biosphere_fedefl.py must have been run first

Run once per machine / brightway project. Each run rewrites the methods.
"""
from __future__ import annotations

import argparse

from fedefl_bw25.config import METHOD_ROOT, PROJECT_NAME
from fedefl_bw25.setup_traci import EUTRO_LOCATION, import_traci


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--eutro-location", default=EUTRO_LOCATION,
                    help="which spatial variant of the eutrophication CFs to use: "
                         "'' (generic / non-located — the default, and what openLCA "
                         "matches) or '00000' (US national average, which DIVERGES "
                         "from the openLCA reference). Every other category has only "
                         "non-located CFs.")
    ap.add_argument("--project", default=PROJECT_NAME,
                    help=f"brightway project to write methods into (default: {PROJECT_NAME})")
    args = ap.parse_args()

    try:
        build = import_traci(eutro_location=args.eutro_location,
                             project=args.project, log=print)
    except RuntimeError as e:
        raise SystemExit(str(e))

    print(f"\nMethods are registered under {METHOD_ROOT}:")
    for m in sorted(build.methods):
        print(f"  {m}")


if __name__ == "__main__":
    main()
