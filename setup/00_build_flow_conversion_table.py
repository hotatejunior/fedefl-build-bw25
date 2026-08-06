#!/usr/bin/env python3
"""
00_build_flow_conversion_table.py
----------------------------------
CLI front-end over `fedefl_bw25.setup_conversions` — parses the full USLCI
JSON-LD zip for the cross-flow-property conversion factors every other script
needs, and writes them to setup/uslci_flow_conversions.json.

The committed table IS the artifact; the full zip is a build-time source that is
not tracked in git. Without --rebuild this only reports the table's provenance.
Rebuilding is a rare power-user action, needed when moving to a newer USLCI
version — the factors are physical properties (densities, energy contents) that
effectively never change between releases.

All the logic lives in the package; this file parses arguments and prints. To
rebuild from your own script instead::

    from fedefl_bw25.setup_conversions import build_conversions, write_conversion_table
    build = build_conversions()
    write_conversion_table(build)

Download the full JSON-LD export from the LCA Commons and place it in source_data/:
  https://www.lcacommons.gov/lca-collaboration/National_Renewable_Energy_Laboratory/USLCI_Database_Public
"""
from __future__ import annotations

import argparse
from pathlib import Path

from fedefl_bw25.config import CONV_TABLE_PATH
from fedefl_bw25.setup_conversions import (DEFAULT_BUNDLE_DIR, build_conversions,
                                           describe_conversions, write_conversion_table)


def main():
    ap = argparse.ArgumentParser(
        description="Build/verify the USLCI flow-conversion table. The committed "
                    "table is canonical; --rebuild is a rare power-user action."
    )
    ap.add_argument("--rebuild", action="store_true",
                    help=f"Regenerate the table from the full USLCI zip in "
                         f"{DEFAULT_BUNDLE_DIR}. Only needed to move to a newer "
                         f"USLCI version.")
    ap.add_argument("--source-zip", type=Path, default=None,
                    help="explicit path to the full USLCI zip, overriding the "
                         "default lookup in the bundle directory")
    ap.add_argument("--bundle-dir", type=Path, default=DEFAULT_BUNDLE_DIR,
                    help="directory holding the USLCI zips (default: source_data/; "
                         "also settable as SOURCE_DATA_DIR)")
    ap.add_argument("--out", type=Path, default=Path(CONV_TABLE_PATH),
                    help="where to write the table (default: setup/uslci_flow_conversions.json)")
    args = ap.parse_args()

    # Normal path: validate presence and exit without touching the (untracked,
    # usually-absent) source zip.
    if not args.rebuild:
        meta = describe_conversions(args.out)
        if meta is None:
            raise SystemExit(
                f"{args.out.name} not found, and --rebuild was not given.\n"
                f"  This table normally ships committed in the repo. If it is genuinely\n"
                f"  missing, rebuild it from the full USLCI zip:\n"
                f"    python setup/00_build_flow_conversion_table.py --rebuild"
            )
        sha = meta.get("source_zip_sha256", "")
        print(f"Conversion table present: {args.out.name}")
        print(f"  Built {meta.get('generated_at', '?')} from {meta.get('source_zip', '?')}"
              f"{f' (sha256 {sha[:12]}...)' if sha else ''}.")
        print("  Nothing to do — pass --rebuild to regenerate from a newer USLCI zip.")
        return

    try:
        build = build_conversions(source_zip=args.source_zip,
                                  bundle_dir=args.bundle_dir, log=print)
    except RuntimeError as e:
        raise SystemExit(str(e))

    path = write_conversion_table(build, args.out)
    print(f"Written → {path.name}")
    print(f"  Source: {build.source_zip} ({build.source_zip_size / 1e6:.1f} MB, "
          f"sha256 {build.source_zip_sha256[:12]}...)")


if __name__ == "__main__":
    main()
