#!/usr/bin/env python3
"""
00_build_flow_conversion_table.py
----------------------------------
Parses the full USLCI JSON-LD zip to extract cross-flow-property conversion
factors for every flow and writes them to uslci_flow_conversions.json.

Background
----------
In openLCA's data model, a "flow" (e.g. "Diesel; at refinery") can be measured
in multiple flow properties (Mass, Volume, Energy). Each flow file stores the
conversion factors between those properties — for diesel this encodes the
substance-specific density (849 kg/m³) and energy content (38,462 MJ/m³).

Per-process LCA Commons exports include the flow property and unit for every
exchange but omit these conversion factors. Without them, brightway cannot
correctly scale exchanges that cross flow properties (e.g. a process that
demands diesel in kg while the supplier expresses output in litres).

Output schema
-------------
uslci_flow_conversions.json:
{
  "<flow-uuid>": {
    "name": "Diesel; at refinery",
    "flow_type": "PRODUCT_FLOW",
    "ref_fp_uuid": "<uuid>",
    "ref_fp_name": "Volume",
    "ref_unit": "m3",
    "conversions": {
      "<fp-uuid>": {
        "name": "Mass",
        "ref_unit": "kg",
        "factor": 849.0          # 1 m3 of diesel = 849 kg
      },
      ...
    }
  },
  ...
}

Reading the factor
------------------
factor = (amount in THIS flow property's ref unit)
         per (1 unit of the REFERENCE flow property's ref unit)

To normalise an exchange amount to the reference flow property:
  amount_in_ref_unit = amount_in_this_fp_ref_unit / factor

Run once per machine whenever the USLCI zip is updated.
"""

import argparse
import hashlib
import json
import os
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import REPO_ROOT

HERE     = Path(__file__).parent
OUT_PATH = HERE / "uslci_flow_conversions.json"

# The full USLCI JSON-LD zip is a *build-time* source: it is parsed only to
# (re)generate uslci_flow_conversions.json, which is committed to the repo and is
# what every other script actually reads. The conversion factors are flow
# physical properties (densities, energy contents) that effectively never change
# between USLCI releases, so rebuilding is a rare power-user action and the zip is
# NOT tracked in git. Place it in source_data/ (or set SOURCE_DATA_DIR) only when
# you actually need to rebuild against a newer USLCI version.
SOURCE_DATA_DIR = Path(os.environ.get("SOURCE_DATA_DIR", REPO_ROOT / "source_data"))
ZIP_NAME        = "National_Renewable_Energy_Laboratory-USLCI_Database_Public.zip"
ZIP_PATH        = SOURCE_DATA_DIR / ZIP_NAME
LCA_COMMONS_URL = "https://www.lcacommons.gov/lca-collaboration/National_Renewable_Energy_Laboratory/USLCI_Database_Public"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_flow(data: dict) -> dict | None:
    """Parse a single flow JSON dict into a conversion table entry, or None if unusable."""
    uuid = data.get("@id")
    if not uuid:
        return None
    fps = data.get("flowProperties", [])
    if not fps:
        return None

    ref_entry = next((fp for fp in fps if fp.get("isRefFlowProperty")), None)
    if ref_entry is None:
        candidates = [fp for fp in fps if fp.get("conversionFactor") == 1.0]
        if len(candidates) > 1:
            names = [fp.get("flowProperty", {}).get("name", "<unknown>") for fp in candidates]
            raise RuntimeError(
                f"Ambiguous reference flow property for flow '{data.get('name', '')}' "
                f"(UUID: {uuid}): multiple flow properties have conversionFactor=1.0.\n"
                f"  Candidates: {names}\n"
                f"  Fix: ensure exactly one flowProperty has isRefFlowProperty=true in the source data."
            )
        ref_entry = candidates[0] if candidates else None
    if ref_entry is None:
        return None

    ref_factor = ref_entry.get("conversionFactor")
    if ref_factor != 1.0:
        raise RuntimeError(
            f"Reference flow property for flow '{data.get('name', '')}' (UUID: {uuid}) "
            f"has conversionFactor={ref_factor}, expected 1.0.\n"
            f"  This indicates inverted or inconsistent factor semantics in the source data."
        )

    ref_fp = ref_entry.get("flowProperty")
    if not ref_fp or "@id" not in ref_fp:
        raise RuntimeError(
            f"Reference flow property entry for flow '{data.get('name', '')}' (UUID: {uuid}) "
            f"is missing 'flowProperty' or '@id'. Malformed flow file."
        )
    ref_fp_uuid = ref_fp["@id"]

    conversions = {}
    for fp_entry in fps:
        fp      = fp_entry["flowProperty"]
        fp_uuid = fp["@id"]
        if fp_uuid == ref_fp_uuid:
            continue
        conversions[fp_uuid] = {
            "name":     fp.get("name", ""),
            "ref_unit": fp.get("refUnit", ""),
            "factor":   fp_entry.get("conversionFactor", 1.0),
        }

    return {
        "uuid":        uuid,
        "name":        data.get("name", ""),
        "flow_type":   data.get("flowType", ""),
        "ref_fp_uuid": ref_fp_uuid,
        "ref_fp_name": ref_fp.get("name", ""),
        "ref_unit":    ref_fp.get("refUnit", ""),
        "conversions": conversions,
    }


def build_table(zip_path: Path, supplement_zips: list[Path] | None = None) -> dict:
    """
    Build the conversion table from the full USLCI zip, then fill any gaps
    using flows found in the per-process export zips (supplement_zips).

    The full USLCI zip takes precedence; supplement flows are only added when
    the UUID is absent from the main table.
    """
    table = {}

    with zipfile.ZipFile(zip_path) as z:
        flow_files = [n for n in z.namelist()
                      if n.startswith("flows/") and n.endswith(".json")]
        print(f"Full USLCI zip: parsing {len(flow_files)} flow files...")
        for fname in flow_files:
            entry = _parse_flow(json.loads(z.read(fname)))
            if entry:
                uid = entry.pop("uuid")
                table[uid] = entry

    if supplement_zips:
        added = 0
        for sup_path in supplement_zips:
            with zipfile.ZipFile(sup_path) as z:
                flow_files = [n for n in z.namelist()
                              if n.startswith("flows/") and n.endswith(".json")]
                for fname in flow_files:
                    entry = _parse_flow(json.loads(z.read(fname)))
                    if entry:
                        uid = entry.pop("uuid")
                        if uid not in table:
                            table[uid] = entry
                            added += 1
        print(f"Per-process zips: {added} additional flows added.")

    return table


def main():
    ap = argparse.ArgumentParser(
        description="Build/verify the USLCI flow-conversion table. The committed "
                    "table is canonical; --rebuild is a rare power-user action."
    )
    ap.add_argument(
        "--rebuild", action="store_true",
        help=f"Regenerate the table from the full USLCI zip in {SOURCE_DATA_DIR}. "
             f"Only needed to move to a newer USLCI version.",
    )
    args = ap.parse_args()

    # Normal path: the committed table IS the artifact. Validate presence and exit
    # without touching the (untracked, usually-absent) source zip.
    if not args.rebuild:
        if not OUT_PATH.exists():
            raise SystemExit(
                f"{OUT_PATH.name} not found, and --rebuild was not given.\n"
                f"  This table normally ships committed in the repo. If it is genuinely\n"
                f"  missing, rebuild it from the full USLCI zip:\n"
                f"    python setup/00_build_flow_conversion_table.py --rebuild\n"
                f"  (requires '{ZIP_NAME}' in {SOURCE_DATA_DIR})"
            )
        meta = json.loads(OUT_PATH.read_text()).get("_meta", {})
        sha  = meta.get("source_zip_sha256", "")
        print(f"Conversion table present: {OUT_PATH.name}")
        print(f"  Built {meta.get('generated_at', '?')} from {meta.get('source_zip', '?')}"
              f"{f' (sha256 {sha[:12]}...)' if sha else ''}.")
        print("  Nothing to do — pass --rebuild to regenerate from a newer USLCI zip.")
        return

    # --rebuild: the full zip must be present in source_data/.
    if not ZIP_PATH.exists():
        raise SystemExit(
            f"Cannot rebuild: USLCI zip not found at {ZIP_PATH}\n"
            f"  Download the full JSON-LD export from LCA Commons and place it there:\n"
            f"    {LCA_COMMONS_URL}\n"
            f"  (Export -> JSON-LD; save as '{ZIP_NAME}'.) Or set SOURCE_DATA_DIR to its folder."
        )
    print(f"Rebuilding from USLCI zip: {ZIP_PATH}")

    supplement = sorted(SOURCE_DATA_DIR.glob("????????-????-????-????-????????????_*.zip"))
    if supplement:
        print(f"Found {len(supplement)} per-process zip(s) to supplement.")
    table = build_table(ZIP_PATH, supplement_zips=supplement)

    multi = sum(1 for v in table.values() if v["conversions"])
    print(f"  {len(table)} flows parsed, {multi} with cross-property conversions.")

    stat = ZIP_PATH.stat()
    table["_meta"] = {
        "source_zip":        ZIP_NAME,
        "source_zip_sha256": _sha256(ZIP_PATH),
        "source_zip_size":   stat.st_size,
        "generated_at":      datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "flow_count":        len(table) - 1,  # exclude _meta itself
    }

    with open(OUT_PATH, "w") as f:
        json.dump(table, f, indent=2)
    print(f"Written → {OUT_PATH.name}")
    print(f"  Source: {ZIP_NAME} ({stat.st_size / 1e6:.1f} MB, sha256 {table['_meta']['source_zip_sha256'][:12]}...)")



if __name__ == "__main__":
    main()
