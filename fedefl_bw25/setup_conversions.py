"""setup_conversions.py — build the USLCI flow-conversion table as a callable step.

The library counterpart of `setup/00_build_flow_conversion_table.py`, and the one
setup step that touches no brightway project at all: it parses the full USLCI
JSON-LD zip and emits a plain JSON table.

Background
----------
In openLCA's data model a "flow" (e.g. "Diesel; at refinery") can be measured in
several flow properties (Mass, Volume, Energy), and each flow file stores the
conversion factors between them — for diesel that encodes the substance-specific
density (849 kg/m3) and energy content (38,462 MJ/m3). Per-process LCA Commons
exports give the flow property and unit for every exchange but omit those factors,
so without this table brightway cannot correctly scale an exchange that crosses
flow properties (a process demanding diesel in kg from a supplier that outputs
litres).

Output schema
-------------
uslci_flow_conversions.json::

    {"<flow-uuid>": {"name": "Diesel; at refinery",
                     "flow_type": "PRODUCT_FLOW",
                     "ref_fp_uuid": "<uuid>", "ref_fp_name": "Volume",
                     "ref_unit": "m3",
                     "conversions": {"<fp-uuid>": {"name": "Mass",
                                                   "ref_unit": "kg",
                                                   "factor": 849.0}}},
     ...,
     "_meta": {...}}

Reading a factor: it is the amount in THIS flow property's reference unit per one
unit of the REFERENCE flow property's reference unit, so
`amount_in_ref_unit = amount_in_this_fp_ref_unit / factor`.

The committed table is the artifact every other script reads; the full zip is a
build-time source that is not tracked in git. Rebuilding is a rare power-user
action — the factors are physical properties that effectively never change between
USLCI releases, and `setup/03` pins the table's hash precisely so a change here
surfaces as a validation event::

    from fedefl_bw25.setup_conversions import build_conversions, write_conversion_table
    build = build_conversions()
    write_conversion_table(build)
"""
from __future__ import annotations

import hashlib
import json
import os
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fedefl_bw25.config import CONV_TABLE_PATH, REPO_ROOT

_NOOP = lambda *a, **k: None          # noqa: E731

# Defaults to source_data/ at the repo root; override with the SOURCE_DATA_DIR env
# var or the `bundle_dir` argument rather than editing this file.
DEFAULT_BUNDLE_DIR = Path(os.environ.get("SOURCE_DATA_DIR", REPO_ROOT / "source_data"))
FULL_DB_ZIP_NAME = "National_Renewable_Energy_Laboratory-USLCI_Database_Public.zip"
BUNDLE_GLOB = "????????-????-????-????-????????????_*.zip"
LCA_COMMONS_URL = ("https://www.lcacommons.gov/lca-collaboration/"
                   "National_Renewable_Energy_Laboratory/USLCI_Database_Public")


@dataclass
class ConversionTable:
    """What one rebuild produced. `table` already carries its own `_meta` entry."""
    table: dict
    flow_count: int
    multi_property_count: int
    source_zip: str
    source_zip_sha256: str
    source_zip_size: int
    supplement_count: int
    supplement_added: int


def file_sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_flow(data: dict) -> dict | None:
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


def build_table(zip_path, supplement_zips=None, log=_NOOP) -> tuple[dict, int]:
    """
    Build the conversion table from the full USLCI zip, then fill any gaps
    using flows found in the per-process export zips (supplement_zips).

    The full USLCI zip takes precedence; supplement flows are only added when
    the UUID is absent from the main table. Returns (table, supplement_added).
    """
    table = {}

    with zipfile.ZipFile(zip_path) as z:
        flow_files = [n for n in z.namelist()
                      if n.startswith("flows/") and n.endswith(".json")]
        log(f"Full USLCI zip: parsing {len(flow_files)} flow files...")
        for fname in flow_files:
            entry = parse_flow(json.loads(z.read(fname)))
            if entry:
                uid = entry.pop("uuid")
                table[uid] = entry

    added = 0
    if supplement_zips:
        for sup_path in supplement_zips:
            with zipfile.ZipFile(sup_path) as z:
                flow_files = [n for n in z.namelist()
                              if n.startswith("flows/") and n.endswith(".json")]
                for fname in flow_files:
                    entry = parse_flow(json.loads(z.read(fname)))
                    if entry:
                        uid = entry.pop("uuid")
                        if uid not in table:
                            table[uid] = entry
                            added += 1
        log(f"Per-process zips: {added} additional flows added.")

    return table, added


def build_conversions(*, source_zip=None, bundle_dir=None, log=_NOOP) -> ConversionTable:
    """Rebuild the conversion table from the full USLCI zip plus any bundle zips.

    `source_zip` defaults to the full USLCI export in `bundle_dir`, which itself
    defaults to source_data/. Nothing is written — pass the result to
    `write_conversion_table`.
    """
    bundle_dir = Path(bundle_dir) if bundle_dir else DEFAULT_BUNDLE_DIR
    zip_path = Path(source_zip) if source_zip else bundle_dir / FULL_DB_ZIP_NAME
    if not zip_path.exists():
        raise RuntimeError(
            f"Cannot rebuild: USLCI zip not found at {zip_path}\n"
            f"  Download the full JSON-LD export from LCA Commons and place it there:\n"
            f"    {LCA_COMMONS_URL}\n"
            f"  (Export -> JSON-LD; save as '{FULL_DB_ZIP_NAME}'.) "
            f"Or set SOURCE_DATA_DIR to its folder."
        )
    log(f"Rebuilding from USLCI zip: {zip_path}")

    supplement = sorted(bundle_dir.glob(BUNDLE_GLOB))
    if supplement:
        log(f"Found {len(supplement)} per-process zip(s) to supplement.")
    table, added = build_table(zip_path, supplement_zips=supplement, log=log)

    multi = sum(1 for v in table.values() if v["conversions"])
    log(f"  {len(table)} flows parsed, {multi} with cross-property conversions.")

    stat = zip_path.stat()
    sha = file_sha256(zip_path)
    table["_meta"] = {
        "source_zip":        zip_path.name,
        "source_zip_sha256": sha,
        "source_zip_size":   stat.st_size,
        "generated_at":      datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        # Off by one: the literal is evaluated before "_meta" is assigned, so len()
        # is already the flow count and the -1 undercounts by one. Carried over
        # verbatim so a rebuild reproduces the committed table byte-for-byte —
        # nothing reads this field (setup/03 pins source_zip_sha256, not this).
        "flow_count":        len(table) - 1,
    }
    return ConversionTable(
        table=table, flow_count=len(table) - 1, multi_property_count=multi,
        source_zip=zip_path.name, source_zip_sha256=sha, source_zip_size=stat.st_size,
        supplement_count=len(supplement), supplement_added=added,
    )


def write_conversion_table(build: ConversionTable, path=None) -> Path:
    """Write the table to `path` (default: the committed setup/ table)."""
    path = Path(path) if path else Path(CONV_TABLE_PATH)
    with open(path, "w") as f:
        json.dump(build.table, f, indent=2)
    return path


def describe_conversions(path=None) -> dict | None:
    """The `_meta` block of the table on disk, or None if it isn't there.

    The normal (non-rebuild) path: the committed table IS the artifact, so this
    reports its provenance without touching the usually-absent source zip.
    """
    path = Path(path) if path else Path(CONV_TABLE_PATH)
    if not path.exists():
        return None
    return json.loads(path.read_text()).get("_meta", {})
