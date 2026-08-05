#!/usr/bin/env python3
"""
03_import_uslci.py
------------------
Imports per-process USLCI JSON-LD exports into brightway25.

Download individual processes from the LCA Commons:
  https://www.lcacommons.gov/lca-collaboration/National_Renewable_Energy_Laboratory/USLCI_Database_Public
Place the zip files in the same directory as this script.

Parses JSON-LD directly rather than using bw2io's JSONLDImporter,
which has known bugs with USLCI's isInput field.

Requires: setup/01_setup_biosphere_fedefl.py must have been run first.
"""

import os
import sys
import hashlib
import importlib.metadata
from datetime import datetime, timezone
from pathlib import Path
import zipfile
import json
import bw2data as bd

from fedefl_bw25.config import PROJECT_NAME, BIOSPHERE_DB, USLCI_DB as USLCI_BUNDLE_DB_NAME, \
    USLCI_FULL_DB as USLCI_FULL_DB_NAME, \
    ELECTRICITY_BASELINE_DB as EXTERNAL_PROVIDER_DB, REPO_ROOT
# EXTERNAL_PROVIDER_DB: the optional injected background DB consulted when a
# bundle exchange's provider isn't in the bundle itself (see
# setup/03b_import_electricity_baseline.py). Absent entirely if that script
# hasn't been run -- those flows stay cutoffs, same as before this fallback
# existed.

HERE = Path(__file__).parent

# Which build this run produces. Default: the per-process bundle set (the locked
# validation build). USLCI_FULL_DB=1 instead imports the ENTIRE USLCI database
# from the single full zip, so general/04 can run any of its ~1,341 processes
# without a rebuild. The two write to SEPARATE brightway databases and can coexist
# — see the USLCI_FULL_DB note in config.py for why the harness must stay pinned
# to the bundle build.
FULL_DB_MODE = os.environ.get("USLCI_FULL_DB", "").strip().lower() in ("1", "true", "yes", "on")
USLCI_DB_NAME = USLCI_FULL_DB_NAME if FULL_DB_MODE else USLCI_BUNDLE_DB_NAME

# Directory holding the per-target USLCI supply-chain bundle zips to import.
# Must be the SAME directory setup/03b_import_electricity_baseline.py scans (its
# DEFAULT_BUNDLE_DIR), so the electricity providers 03b discovers and injects
# line up with the bundles 03 actually imports. This is the canonical locked
# test-case set (petroleum / corn / cement / steel + their upstream).
# Defaults to source_data/ at the repo root; override with the SOURCE_DATA_DIR
# env var to point at your own checkout instead of editing this file.
BUNDLE_DIR = Path(os.environ.get("SOURCE_DATA_DIR", REPO_ROOT / "source_data"))
if not BUNDLE_DIR.is_dir():
    raise SystemExit(
        f"Bundle directory not found: {BUNDLE_DIR}\n"
        f"Place the USLCI bundle / openLCA library resources in {REPO_ROOT / 'source_data'}, "
        f"or set the SOURCE_DATA_DIR environment variable to your local checkout."
    )

# =============================================================================
# UNIT NORMALISATION
# =============================================================================
# Flow conversion table built by setup/00_build_flow_conversion_table.py.
# Maps flow UUID -> reference flow property + cross-property conversion factors.
_CONV_TABLE_PATH = HERE / "uslci_flow_conversions.json"
if not _CONV_TABLE_PATH.exists():
    raise SystemExit(
        "uslci_flow_conversions.json not found. "
        "Run setup/00_build_flow_conversion_table.py first."
    )
FLOW_CONV = json.loads(_CONV_TABLE_PATH.read_text())

# Verify the conversion table matches the USLCI zip it was built from -- but only
# when that zip is actually present. The full zip is a build-time source that is
# NOT tracked in git (see setup/00), so for most users it is absent here and the
# committed table is trusted as-is. When a rebuilder does have the zip in
# source_data/, compare by content hash (mtime is machine-specific and would
# false-alarm on every git checkout).
_meta = FLOW_CONV.get("_meta", {})
if _meta:
    _expected_zip = BUNDLE_DIR / _meta.get("source_zip", "")
    _expected_sha = _meta.get("source_zip_sha256")
    if _expected_zip.exists() and _expected_sha:
        _h = hashlib.sha256()
        with open(_expected_zip, "rb") as _f:
            for _chunk in iter(lambda: _f.read(1 << 20), b""):
                _h.update(_chunk)
        if _h.hexdigest() != _expected_sha:
            raise RuntimeError(
                f"uslci_flow_conversions.json was built from a different version of "
                f"{_meta['source_zip']} than the one now in {BUNDLE_DIR}.\n"
                f"  Table built at : {_meta.get('generated_at', '?')}\n"
                f"  Fix: rerun setup/00_build_flow_conversion_table.py --rebuild."
            )
else:
    print("WARNING: uslci_flow_conversions.json has no _meta block — cannot verify zip version match.")

# Within-property unit conversion factors.
# Maps unit name -> multiplier to reach the flow property's reference unit.
#   Mass   ref unit: kg    Volume ref unit: m3    Energy ref unit: MJ
#
# Lookup is case-insensitive (see _WITHIN_FP_LOWER below). Add explicit entries
# for abbreviation variants that differ beyond case (e.g. "mmbtu" vs "mm btu").
WITHIN_FP = {
    # Mass → kg
    "kg": 1.0,   "g": 1e-3,    "mg": 1e-6,   "lb": 0.45359237,
    "lb av": 0.45359237,        "sh tn": 907.18474,  "t": 1e3,  "ton": 907.18474,
    "short ton": 907.18474,     "metric ton": 1e3,   "tonne": 1e3,
    # Volume → m3
    "m3": 1.0,   "l": 1e-3,    "ml": 1e-6,
    "gal": 3.78541e-3,  "gal (us liq)": 3.78541e-3,  "gal (us fl)": 3.78541e-3,
    "gal (imp)": 4.54609e-3,
    "cu ft": 0.0283168,  "ft3": 0.0283168,
    # Energy → MJ
    "mj": 1.0,   "kj": 1e-3,   "gj": 1e3,    "kwh": 3.6,
    "btu": 1.05506e-3,
    "mmbtu": 1055.06,   "mm btu": 1055.06,   "mmBtu": 1055.06,
    # kcal is the INTERNATIONAL TABLE calorie (4.1868 J), matching the IT Btu
    # above (1055.06 J) and openLCA's own reference data. The thermochemical
    # calorie (4.184 J) would be 0.07% low — under the 0.1% replication gate,
    # so a wrong pick here would not be caught by the harness. Added for USLCI
    # v1.2026-06.0, which introduced kcal on coal/natural-gas combustion.
    "kcal": 4.1868e-3,
    # Transport (freight) → t*km
    "t*km": 1.0, "tkm": 1.0,   "t*mi": 1.60934,  "kg*km": 1e-3,
    # Transport (passenger) → p*km (person-kilometre; already reference unit)
    "p*km": 1.0,
    # Duration → h (USLCI service flows, e.g. chainsawing/skidding, are defined
    # and consumed as "1 h of <service>"; h is their reference unit)
    "h": 1.0,
    # Area → m2  ("ft2" = the international foot squared, 0.3048^2 exactly,
    # consistent with "ft" below; added for USLCI v1.2026-06.0)
    "m2": 1.0,   "ft2": 0.09290304,
    # Area × time → m2*a (land use). "ha*a" is hectare-years: 1 ha = 1e4 m2
    # exactly. Added for v1.2026-06.0's grazing/land-use exchanges.
    "m2*a": 1.0, "ha*a": 1e4,
    # Volume × time → m3*a (water use; already reference unit)
    "m3*a": 1.0,
    # Length → m
    "m": 1.0,    "ft": 0.3048,
    # Radioactivity → kBq
    "kbq": 1.0,  "bq": 1e-3,
    # Dimensionless / count
    "unit": 1.0, "item(s)": 1.0, "p": 1.0, "items": 1.0,
    # Currency — pass through at face value (no physical conversion)
    "usd": 1.0,  "$": 1.0,  "us$": 1.0,
}

# Case-SENSITIVE entries, checked before the lowercase fallback: units whose
# meaning changes with case. "Mg" (megagram = tonne, used by the recycling/MRF
# sector) would otherwise collide with "mg" (milligram) in the case-insensitive
# lookup — a silent 1e9 error. A census of the full USLCI unit universe
# (30 strings) found Mg/mg to be the only case collision.
_WITHIN_FP_EXACT = {"Mg": 1e3}

# Case-insensitive lookup built from WITHIN_FP. All keys are lowercased at
# definition time so lookup just needs unit.lower(). Explicit lowercase entries
# in WITHIN_FP (e.g. "mmbtu") are already correct.
_WITHIN_FP_LOWER = {k.lower(): v for k, v in WITHIN_FP.items()}

# Accumulates unit strings not found in _WITHIN_FP_LOWER during import, mapped to
# the flow UUIDs seen carrying them (so the hard-stop message can say WHERE).
# Enforced before the DB is written — add missing units to WITHIN_FP and rerun.
UNKNOWN_UNITS: dict = {}   # unit string -> set of flow UUIDs

# Unknown-unit policy (ledger #7). An unrecognized unit string is passed through
# at face value in normalize() -- a silent wrong number ("a wrong number wearing a
# plausible one's clothes"). By DEFAULT the build HARD-STOPS if any unknown unit
# was encountered, checked before '{USLCI_DB}' is written. Set the
# ALLOW_UNIT_PASSTHROUGH=1 environment variable to permit passthrough with a loud
# warning instead (exploratory imports only — never for results you intend to use).
ALLOW_UNIT_PASSTHROUGH = os.environ.get(
    "ALLOW_UNIT_PASSTHROUGH", "").strip().lower() in ("1", "true", "yes", "on")


def normalize(amount: float, unit: str, fp_uuid: str, flow_uuid: str,
              cross_property: bool = True):
    """
    Convert exchange amount to the flow's reference flow property reference unit.

    Two-step process:
      1. Within-property: unit -> fp ref unit  (e.g. l -> m3, kg stays kg)
      2. Cross-property:  fp ref unit -> reference fp ref unit
                          (e.g. btu of diesel -> m3 using energy density)

    cross_property must be False for biosphere (elementary) flows — they are
    always expressed in their natural unit (kg, kBq, etc.) and must never be
    converted between flow properties via FLOW_CONV.

    Returns (normalized_amount, ref_unit_str).
    Falls back to (amount, unit) when data is missing so import never crashes.
    """
    within = _WITHIN_FP_EXACT.get(unit) if unit else None
    if within is None:
        within = _WITHIN_FP_LOWER.get(unit.lower() if unit else "")
    if within is None:
        UNKNOWN_UNITS.setdefault(unit, set()).add(flow_uuid)
        return amount, unit   # unrecognised unit — pass through

    if not cross_property:
        return amount * within, unit  # step 1 only (biosphere path)

    flow = FLOW_CONV.get(flow_uuid)
    if flow is None:
        return amount * within, unit  # step 1 only; flow not in table

    ref_fp_uuid = flow["ref_fp_uuid"]
    ref_unit    = flow["ref_unit"]

    amount_fp_ref = amount * within       # now in fp's own ref unit

    if fp_uuid == ref_fp_uuid:
        return amount_fp_ref, ref_unit    # already the reference fp

    conv = flow["conversions"].get(fp_uuid)
    if conv is None:
        return amount_fp_ref, ref_unit    # fp not in table — step 1 only

    # factor = (this fp ref unit) per (1 reference fp ref unit)
    # => reference fp amount = this fp amount / factor
    return amount_fp_ref / conv["factor"], ref_unit

bd.projects.set_current(PROJECT_NAME)
if not bd.projects.twofive:
    bd.projects.migrate_project_25()

if BIOSPHERE_DB not in bd.databases:
    raise RuntimeError("Run setup/01_setup_biosphere_fedefl.py first.")

# =============================================================================
# LOAD ALL PROCESSES AND FLOWS FROM ZIPS
# =============================================================================
# When the same process UUID appears in multiple exports (e.g. a corrected
# re-download), precedence is decided by the process's OWN embedded dataset
# version + lastChange timestamp -- NOT by file modification time (ledger #5).
# mtime does not survive copies/clones, so two users with identical bundles could
# otherwise build different databases; version/lastChange live inside the JSON and
# are identical on every machine. Zips are iterated in a deterministic filename
# order purely so the "equal version" tie-break is reproducible too.
def _parse_version(v):
    """'00.01.014' -> (0, 1, 14); non-numeric parts -> 0; missing -> () (lowest)."""
    if not v:
        return ()
    parts = []
    for part in str(v).split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return tuple(parts)

def _proc_precedence(data):
    """Sort key for choosing between two copies of the same process UUID. Higher =
    newer: primarily the dataset version, then the lastChange timestamp (ISO-8601
    Zulu strings sort chronologically)."""
    return (_parse_version(data.get("version")), data.get("lastChange") or "")

# Discover the process sources. Default: per-process bundle zips (<uuid>_<hash>.zip)
# in BUNDLE_DIR. In FULL_DB_MODE, import the ENTIRE USLCI database from the single
# full zip named in the conversion table's _meta, instead of per-process bundles.
# Everything downstream is unchanged — the parser already builds an activity for
# every process it loads, with no target scoping.
_full_db_zip = _meta.get("source_zip", "")
if FULL_DB_MODE:
    _full = BUNDLE_DIR / _full_db_zip
    if not _full_db_zip or not _full.exists():
        raise SystemExit(
            f"USLCI_FULL_DB is set but the full USLCI zip is not present in {BUNDLE_DIR} "
            f"(expected '{_full_db_zip or '<name recorded in the conversion table _meta>'}')."
        )
    zip_files = [_full]
    print(f"FULL-DB MODE: importing the entire USLCI database from {_full.name}")
    print(f"  target database: '{USLCI_DB_NAME}' "
          f"(the bundle build '{USLCI_BUNDLE_DB_NAME}' is left untouched)")
else:
    zip_files = sorted(
        BUNDLE_DIR.glob("????????-????-????-????-????????????_*.zip"),
        key=lambda p: p.name
    )

    # Guard against silently ignored bundles: a zip that LOOKS like a JSON-LD process
    # export (has openlca.json + processes/) but whose filename doesn't match the
    # required <uuid>_<hash>.zip pattern would otherwise vanish without a trace — the
    # operator thinks their process imported when it didn't. Renamed downloads are the
    # usual cause; keep the original LCA Commons filename. The full-DB zip (named in
    # the conversion table's _meta) is a build-time source, not a bundle — excluded.
    for _zp in sorted(BUNDLE_DIR.glob("*.zip")):
        if _zp in zip_files or _zp.name == _full_db_zip:
            continue
        try:
            with zipfile.ZipFile(_zp) as _z:
                _names = _z.namelist()
                if "openlca.json" in _names and any(n.startswith("processes/") for n in _names):
                    print(
                        f"WARNING: {_zp.name} looks like a process bundle but does NOT match the "
                        f"required '<uuid>_<hash>.zip' naming pattern — it will be IGNORED.\n"
                        f"  Restore the original LCA Commons filename (the process UUID + export hash) "
                        f"if you want it imported."
                    )
        except zipfile.BadZipFile:
            pass

    if not zip_files:
        raise SystemExit(
            f"No process zip files found in {BUNDLE_DIR}. Download per-process exports "
            f"from LCA Commons and place them there (same dir 03b scans). Bundle zips "
            f"must keep their original '<uuid>_<hash>.zip' filenames (see WARNINGs above, "
            f"if any, for zips that were skipped on naming grounds)."
        )

print(f"Found {len(zip_files)} process zip(s).")


def _zip_sha256(path):
    _h = hashlib.sha256()
    with open(path, "rb") as _f:
        for _chunk in iter(lambda: _f.read(1 << 20), b""):
            _h.update(_chunk)
    return _h.hexdigest()


# Content identity of the sources this build was made from -- stamped onto the
# database after the write (see the uslci_source block below). Bundle filenames
# additionally carry the USLCI release hash as their '_<hash>' suffix; it is
# recorded as-is, never translated into a version name, because the same suffix
# was observed on bundles whose process versions disagree.
_source_identity = {
    "mode": "full_db" if FULL_DB_MODE else "bundles",
    "zips": [{"name": _z.name, "sha256": _zip_sha256(_z)} for _z in zip_files],
}
if not FULL_DB_MODE:
    _hashes = sorted({_z.stem.rsplit("_", 1)[-1] for _z in zip_files
                      if "_" in _z.stem and len(_z.stem.rsplit("_", 1)[-1]) == 40})
    if _hashes:
        _source_identity["bundle_release_hashes"] = _hashes
print(f"  Source identity: {_source_identity['mode']}, "
      f"{len(_source_identity['zips'])} zip(s)")

all_processes = {}  # proc_uuid -> process dict
_proc_keys    = {}  # proc_uuid -> precedence key of the copy currently kept
all_flows     = {}  # flow_uuid -> flow dict
version_resolved = set()  # UUIDs where copies differed in version/lastChange

for zpath in zip_files:
    with zipfile.ZipFile(zpath) as z:
        for name in z.namelist():
            if name.startswith("processes/") and name.endswith(".json"):
                data = json.loads(z.read(name))
                uid = data.get("@id")
                if not uid:
                    continue
                key = _proc_precedence(data)
                if uid in all_processes:
                    if key == _proc_keys[uid]:
                        continue  # identical version — benign duplicate, keep first-seen
                    version_resolved.add(uid)
                    if key < _proc_keys[uid]:
                        continue  # incoming copy is older — keep the newer incumbent
                all_processes[uid] = data
                _proc_keys[uid]    = key
            elif name.startswith("flows/") and name.endswith(".json"):
                data = json.loads(z.read(name))
                uid = data.get("@id")
                if uid:
                    all_flows[uid] = data

print(f"  {len(all_processes)} unique processes, {len(all_flows)} unique flows.")
if version_resolved:
    print(f"  NOTE: {len(version_resolved)} process UUID(s) appeared in multiple zips with "
          f"differing versions — kept the highest dataset version / lastChange "
          f"(deterministic across machines).")
print()

# =============================================================================
# BUILD FLOW -> PROCESS MAP (for technosphere linking)
# Each reference flow UUID maps to the set of process UUIDs that supply it --
# a SET, not a single value, because multiple bundle-internal processes can
# legitimately share a reference flow (e.g. 4 regional crude-oil sourcing
# variants -- on/off-shore, domestic/import -- all supply generic "Crude oil";
# 6 natural-gas extraction methods all supply generic "Natural gas"; several
# regional transport processes all supply the same generic transport flow).
# Confirmed across the 4 test-case bundles: 7 such flows, 69 consuming
# exchanges total, and every one of those 69 carries a `defaultProvider` hint
# that correctly names one of the actual candidate producers -- so the data
# is fully disambiguated and resolution must use that hint, not a bare
# flow-UUID lookup (see _resolve_provider below).
#
# The reference exchange is usually an output (a normal product), but USLCI
# also has waste-treatment "sink" processes (landfilling, wastewater
# treatment, combustion, effluent release) whose function is defined by the
# waste they consume -- there isQuantitativeReference=true legitimately lands
# on an INPUT exchange (confirmed: every process in the current 4-bundle set
# has exactly one reference exchange, on one side or the other, never both/
# neither). Either way, that flow is what other processes link to via this
# map, so direction doesn't matter here -- only that isQuantitativeReference
# uniquely identifies it.
# =============================================================================
flow_to_process = {}
for proc_uuid, proc in all_processes.items():
    ref_flows = [
        exc.get("flow", {}).get("@id")
        for exc in proc.get("exchanges", [])
        if exc.get("isQuantitativeReference") and exc.get("flow", {}).get("@id")
    ]
    if len(ref_flows) > 1:
        raise RuntimeError(
            f"Process {proc_uuid} ({proc.get('name', '?')}) has {len(ref_flows)} "
            f"quantitative reference exchanges — expected exactly 1. "
            f"This indicates a malformed USLCI JSON-LD export."
        )
    for flow_uuid in ref_flows:
        flow_to_process.setdefault(flow_uuid, set()).add(proc_uuid)

# External providers (e.g. the electricity baseline library -- see
# setup/03b_import_electricity_baseline.py). Not electricity-specific: any bundle
# exchange whose named provider lives outside the bundle resolves against
# this if a matching injected database is present.
external_provider_uuids = set()
flow_to_external_process = {}
external_uuid_to_name = {}
# The electricity-baseline vintage 03b injected (e.g. "2025" / "2026"), copied
# onto uslci-subset below so the validation harness can assert a case is diffed
# against the matching-vintage grid. None if 03b predates the stamp.
electricity_vintage = None
if EXTERNAL_PROVIDER_DB in bd.databases:
    electricity_vintage = bd.databases[EXTERNAL_PROVIDER_DB].get("electricity_vintage")
    for act in bd.Database(EXTERNAL_PROVIDER_DB):
        external_provider_uuids.add(act["code"])
        external_uuid_to_name[act["code"]] = act["name"]
        ref_flow_uuid = act.get("reference_product_flow_uuid")
        if ref_flow_uuid:
            flow_to_external_process.setdefault(ref_flow_uuid, set()).add(act["code"])
    print(f"Loaded {len(external_provider_uuids)} external provider process(es) "
          f"({len(flow_to_external_process)} distinct reference flow(s)) "
          f"from '{EXTERNAL_PROVIDER_DB}'"
          + (f" [electricity_vintage = {electricity_vintage}]."
             if electricity_vintage else "."))


def _candidate_name(db_name, proc_uuid):
    """Name of a resolution candidate, for hint-name disambiguation."""
    if db_name == EXTERNAL_PROVIDER_DB:
        return external_uuid_to_name.get(proc_uuid)
    return (all_processes.get(proc_uuid) or {}).get("name")


def _resolve_provider(exc, flow_uuid):
    """Resolve a technosphere exchange to (db_name, proc_uuid), or (None, None)
    if unresolved. Always prefers the exchange's own `defaultProvider` UUID
    hint -- bundle-internal or external -- since that's the only thing that
    disambiguates when several processes share a reference flow. Falls back
    to flow-based resolution only when the hint is absent or doesn't match
    anything we have, and only when the flow maps to exactly one candidate
    across bundle + external combined; a flow with several candidates and no
    usable hint is a genuine ambiguity, left unlinked rather than guessed.
    Returns a third element: True if the fallback path hit a genuine ambiguity.
    """
    provider = exc.get("defaultProvider") or {}
    hinted_uuid = provider.get("@id")
    if hinted_uuid in all_processes:
        return USLCI_DB_NAME, hinted_uuid, False
    if hinted_uuid in external_provider_uuids:
        return EXTERNAL_PROVIDER_DB, hinted_uuid, False

    candidates = {(USLCI_DB_NAME, u) for u in flow_to_process.get(flow_uuid, set())}
    candidates |= {(EXTERNAL_PROVIDER_DB, u) for u in flow_to_external_process.get(flow_uuid, set())}
    if len(candidates) == 1:
        target_db, target_proc = next(iter(candidates))
        return target_db, target_proc, False

    if len(candidates) > 1:
        # Several producers share this flow and the hint UUID didn't resolve
        # (e.g. the electricity baseline: one flow "Electricity, AC, 120 V" is
        # produced by every regional consumption mix, and the exchange hints a
        # provider UUID that was renamed across library versions). The hint
        # still carries the intended provider's NAME, though -- if exactly one
        # candidate's name matches it, that's an unambiguous resolution, not a
        # guess. Mirrors the openLCA operator selecting the named mix by hand.
        hinted_name = provider.get("name")
        if hinted_name:
            by_name = [c for c in candidates if _candidate_name(*c) == hinted_name]
            if len(by_name) == 1:
                return (*by_name[0], False)
        return None, None, True

    return None, None, False


# Allocation logic lives in setup/allocation.py (extracted verbatim so it is
# unit-testable with synthetic openLCA JSON — see tests/test_allocation.py).
# The thin wrapper binds this script's normalize() and FLOW_CONV table.
from fedefl_bw25.allocation import allocation_for, causal_coproducts, coproduct_multipliers


def _allocation_for(proc, proc_uuid):
    return allocation_for(proc, proc_uuid, normalize=normalize, flow_conv=FLOW_CONV)


# =============================================================================
# BUILD BRIGHTWAY DATABASE
# =============================================================================
bio_uuids = {act["code"] for act in bd.Database(BIOSPHERE_DB)}

LOCATION_MAP = {
    "United States of America (the)": "US",
    "Northern America":               "RNA",
    "Global":                         "GLO",
    "Europe":                         "RER",
}

if USLCI_DB_NAME in bd.databases:
    response = input(f"Database '{USLCI_DB_NAME}' already exists. Delete and rebuild? [y/N] ")
    if response.strip().lower() != "y":
        raise SystemExit("Aborted — database not modified.")
    del bd.databases[USLCI_DB_NAME]

db_data = {}
total_bio_matched   = 0
total_bio_unmatched = 0
total_tech_linked    = 0
total_tech_external  = 0
total_tech_unlinked  = 0
total_tech_ambiguous = 0
ambiguous_examples   = []
total_causal_fallback = 0          # causal exchanges that fell back to the scalar
total_causal_coproduct_links = 0   # links redirected to a causal co-product activity
causal_coproduct_examples = []
total_waste_treatment_links = 0    # non-reference WASTE_FLOW outputs sent to a treatment provider
waste_treatment_examples = []
total_avoided_product_links = 0    # exchanges flagged isAvoidedProduct — credited (sign-flipped)
avoided_product_examples = []

# =============================================================================
# PRE-PASS: allocation factors + co-product re-basis multipliers
# =============================================================================
# Runs over ALL processes before the build loop because a consumer can be built
# before the co-product supplier it points at is visited. For each multi-output
# process it caches the reference product's allocation factor (what the single
# built activity's inputs are scaled by) and, per NON-reference output flow, a
# multiplier that converts a request expressed in the co-product's own units
# into the equivalent amount of the reference product delivering the co-product's
# correctly-allocated burden share:
#
#   m(P, flow) = (ref_yield * target_alloc) / (target_yield * ref_alloc)
#
# The reference product and single-output processes get no entry (implicit 1.0).
# For pure mass allocation this reduces to a units/density conversion (the
# reference product's declared yield can be in L/m3 while its allocation factor
# is mass-based); for economic/causal allocation it does real burden re-
# attribution. Both fall out of the same formula.
alloc_cache = {}                 # proc_uuid -> scalar alloc_factor (native/mass/fallback)
causal_cache = {}                # proc_uuid -> {exchange_internalId: ref-product factor}
coproduct_multiplier = {}        # (supplier_proc_uuid, output_flow_uuid) -> m
# Causal co-products can't be scalar-re-based (their burden is per-exchange), so
# each gets its OWN activity, built from its own column of the causal factor grid.
# Consumers are redirected to that activity at the link site — no multiplier.
causal_coproduct_info = {}       # (proc_uuid, co_flow_uuid) -> {"column", "fallback", "yield"}
causal_coproduct_key  = {}       # (proc_uuid, co_flow_uuid) -> (USLCI_DB_NAME, code)
causal_coproduct_consumed = set()  # (proc_uuid, co_flow_uuid) actually drawn by a consumer
skipped_multipliers = []         # (proc_uuid, flow_uuid) where m was uncomputable
n_multi_native = 0
n_multi_mass   = 0
n_multi_causal = 0
multi_examples = []

for _puuid, _proc in all_processes.items():
    _alloc_factor, _ref_flow, _per_output, _method, _causal = _allocation_for(_proc, _puuid)
    alloc_cache[_puuid] = _alloc_factor
    causal_cache[_puuid] = _causal
    if _method == "native":
        n_multi_native += 1
        if len(multi_examples) < 5:
            multi_examples.append(f"{_proc.get('name', _puuid)} ({_method}, ref α={_alloc_factor:.4g})")
    elif _method == "mass":
        n_multi_mass += 1
        if len(multi_examples) < 5:
            multi_examples.append(f"{_proc.get('name', _puuid)} ({_method}, ref α={_alloc_factor:.4g})")
    elif _method == "causal":
        n_multi_causal += 1
        if len(multi_examples) < 5:
            multi_examples.append(f"{_proc.get('name', _puuid)} (causal, {len(_causal)} per-exchange factors)")
        # Each causal co-product gets a dedicated activity built from its own
        # factor column. Key it now (pre-pass) so consumers built before the
        # supplier can already link to it.
        for _fuuid, _info in causal_coproducts(_proc, _puuid, normalize, FLOW_CONV).items():
            causal_coproduct_info[(_puuid, _fuuid)] = _info
            causal_coproduct_key[(_puuid, _fuuid)] = (USLCI_DB_NAME, f"{_puuid}__co__{_fuuid}")
    # Co-product multipliers: only for scalar-allocated processes (native/mass).
    # single-output and causal have empty per_output -> no multipliers.
    if _method in ("single", "causal") or _ref_flow is None:
        continue
    _mults, _skipped = coproduct_multipliers(_per_output, _ref_flow)
    for _flow_uuid, _m in _mults.items():
        coproduct_multiplier[(_puuid, _flow_uuid)] = _m
    skipped_multipliers.extend((_puuid, _f) for _f in _skipped)

# Per-process import diagnostics (RELEASE_PLAN 4.4 / ledger #9). The same
# match/link outcomes tallied into the globals below, kept PER process so
# general/04 can report completeness for a specific result's supply chain rather
# than the whole DB. Persisted to uslci_db_provenance.json after the write; this
# is purely additive and never touches db_data, so the LCIA harness is unaffected.
proc_provenance = {}

# Build jobs: every process's reference activity (co_flow None), plus one extra
# activity per causal co-product (co_flow set), built from the same JSON process
# but with the co-product's own per-exchange factor column and its own yield as
# the production exchange.
_build_jobs = [(_puuid, None) for _puuid in all_processes]
_build_jobs += sorted(causal_coproduct_info)  # (proc_uuid, co_flow_uuid), deterministic order

for proc_uuid, co_flow in _build_jobs:
    proc = all_processes[proc_uuid]
    exchanges = []
    ref_unit = "unit"
    pdiag = {"bio_matched": 0, "bio_unmatched": 0, "tech_linked": 0,
             "tech_external": 0, "tech_unlinked": 0, "tech_ambiguous": 0}

    # Allocation factor(s), computed once in the pre-pass. For native/mass
    # allocation this is one scalar applied to every input/biosphere exchange;
    # for causal allocation each exchange has its own factor (keyed by
    # internalId), with alloc_factor as the fallback. Scalar co-product
    # consumers are re-based separately, at their link site, via
    # coproduct_multiplier. 1.0 for single-output. A causal co-product job uses
    # the CO-PRODUCT's own factor column and mass-fraction fallback.
    if co_flow is None:
        key = (USLCI_DB_NAME, proc_uuid)
        alloc_factor    = alloc_cache[proc_uuid]
        causal_factors  = causal_cache.get(proc_uuid)
    else:
        key = causal_coproduct_key[(proc_uuid, co_flow)]
        alloc_factor    = causal_coproduct_info[(proc_uuid, co_flow)]["fallback"]
        causal_factors  = causal_coproduct_info[(proc_uuid, co_flow)]["column"]

    for exc in proc.get("exchanges", []):
        flow_ref  = exc.get("flow", {})
        flow_uuid = flow_ref.get("@id")
        flow_type = flow_ref.get("flowType", "")
        is_input  = exc.get("isInput", False)
        # Production exchange: the quantitative reference for a normal job, the
        # co-product's own output exchange for a causal co-product job (there
        # the actual reference product falls through to the else branch and is
        # dropped like any other non-reference product output).
        if co_flow is None:
            is_ref = exc.get("isQuantitativeReference", False)
        else:
            is_ref = (not is_input and flow_uuid == co_flow
                      and flow_type == "PRODUCT_FLOW")

        if flow_uuid is None:
            raise RuntimeError(
                f"Malformed exchange in process '{proc.get('name', proc_uuid)}' "
                f"(proc UUID: {proc_uuid}): exchange has no flow @id.\n"
                f"  Exchange: {exc}"
            )
        amount    = exc.get("amount", 0.0)
        unit      = exc.get("unit", {}).get("name", "")
        fp_uuid   = exc.get("flowProperty", {}).get("@id", "")

        if is_ref:
            # Production exchange: never scaled by allocation factor. Usually
            # an output (a normal product), but USLCI waste-treatment "sink"
            # processes (landfilling, wastewater treatment, combustion,
            # effluent release) define their function by the waste they
            # consume, so isQuantitativeReference legitimately lands on an
            # input exchange there. Direction doesn't matter for building the
            # activity's own production exchange -- flow_to_process above
            # already resolves consumers to this process either way.
            norm_amount, norm_unit = normalize(amount, unit, fp_uuid, flow_uuid)
            ref_unit = norm_unit
            exchanges.append({
                "input":  key,
                "amount": norm_amount,
                "unit":   norm_unit,
                "type":   "production",
            })

        else:
            # Apply the allocation factor for multi-output processes. Native/mass
            # use one scalar; causal uses this exchange's own factor (by
            # internalId), falling back to the scalar only if an added exchange
            # unexpectedly lacks one. Single-output: alloc_factor = 1.0.
            if causal_factors is not None:
                factor = causal_factors.get(exc.get("internalId"))
                if factor is None:
                    factor = alloc_factor
                    total_causal_fallback += 1
            else:
                factor = alloc_factor
            amount = amount * factor

            if flow_type == "ELEMENTARY_FLOW":
                # Biosphere: only within-property unit conversion (step 1).
                # Never cross-property — CO2 stays in kg, kBq stays in kBq, etc.
                bio_amount, bio_unit = normalize(
                    amount, unit, fp_uuid, flow_uuid, cross_property=False
                )
                if flow_uuid in bio_uuids:
                    exchanges.append({
                        "input":  (BIOSPHERE_DB, flow_uuid),
                        "amount": bio_amount,
                        "unit":   bio_unit,
                        "type":   "biosphere",
                    })
                    total_bio_matched += 1
                    pdiag["bio_matched"] += 1
                else:
                    total_bio_unmatched += 1
                    pdiag["bio_unmatched"] += 1

            elif (is_input and flow_type in ("PRODUCT_FLOW", "WASTE_FLOW")) \
                    or (not is_input and flow_type == "WASTE_FLOW"):
                # Technosphere link. Two directions resolve the same way:
                #   - a consumed INPUT (product or waste feedstock), and
                #   - a WASTE_FLOW OUTPUT sent to a treatment provider. openLCA
                #     models disposal as the generator OUTPUTTING a waste flow
                #     whose defaultProvider is the treatment process (whose own
                #     reference is that waste flow as an input). The waste amount
                #     is a positive consumption of the treatment service, so it
                #     links exactly like an input. Without this, every waste-to-
                #     treatment output was silently dropped and the treatment
                #     burden (e.g. landfill methane) was omitted entirely.
                # NON-reference PRODUCT_FLOW outputs are co-products, handled by
                # allocation / coproduct_multiplier — deliberately NOT linked here.
                norm_amount, norm_unit = normalize(amount, unit, fp_uuid, flow_uuid)

                # Avoided products are CREDITS, not burdens. USLCI marks byproduct
                # energy/material recovery this way (isInput=true + isAvoidedProduct
                # =true): e.g. MSW landfilling / combustion recovering landfill-gas
                # electricity, which displaces grid power. openLCA subtracts these;
                # a brightway technosphere input with a positive stored amount is a
                # positive consumption (burden), so flip the sign to turn the
                # avoided consumption into the credit openLCA computes. Without this,
                # the landfill-gas electricity credit was imported as a burden and
                # sign-flipped petroleum's grid-dominated toxicity result.
                if exc.get("isAvoidedProduct", False):
                    norm_amount = -norm_amount
                    total_avoided_product_links += 1
                    if len(avoided_product_examples) < 5:
                        avoided_product_examples.append(
                            f"{proc.get('name', proc_uuid)} avoids "
                            f"{flow_ref.get('name', flow_uuid)} ({norm_amount:.4g} {norm_unit})")
                target_db, target_proc, was_ambiguous = _resolve_provider(exc, flow_uuid)
                if was_ambiguous:
                    # Several candidate producers (bundle-internal and/or
                    # external) supply this flow, and the exchange's own
                    # defaultProvider hint didn't match any of them -- non-
                    # fatal: leave unlinked/cutoff rather than guess.
                    total_tech_ambiguous += 1
                    pdiag["tech_ambiguous"] += 1
                    hinted_uuid = (exc.get("defaultProvider") or {}).get("@id")
                    candidates = ({(USLCI_DB_NAME, u) for u in flow_to_process.get(flow_uuid, set())} |
                                  {(EXTERNAL_PROVIDER_DB, u) for u in flow_to_external_process.get(flow_uuid, set())})
                    ambiguous_examples.append(
                        f"{proc.get('name', proc_uuid)}: flow {flow_uuid} "
                        f"(hint {hinted_uuid!r} unresolved; {len(candidates)} candidates: {sorted(candidates)})"
                    )
                if target_proc:
                    co_key = causal_coproduct_key.get((target_proc, flow_uuid))
                    if co_key is not None:
                        # This exchange draws a CAUSAL process's non-reference
                        # co-product. Its burden is per-exchange, so no scalar
                        # multiplier can re-base it through the reference
                        # activity — link instead to the co-product's dedicated
                        # activity (built from its own factor column), which is
                        # natively in the co-product's own units. No multiplier.
                        causal_coproduct_consumed.add((target_proc, flow_uuid))
                        total_causal_coproduct_links += 1
                        if len(causal_coproduct_examples) < 5:
                            causal_coproduct_examples.append(
                                f"{proc.get('name', proc_uuid)} draws causal co-product flow {flow_uuid}")
                        exchanges.append({
                            "input":  co_key,
                            "amount": norm_amount,
                            "unit":   norm_unit,
                            "type":   "technosphere",
                        })
                    else:
                        # Re-basis scalar co-product draws. When this exchange
                        # targets a NON-reference output of a multi-output
                        # supplier, the supplier's single brightway activity is
                        # built on its reference product's yield+allocation;
                        # convert the request into the equivalent reference-
                        # product amount that carries this co-product's own
                        # allocated burden. 1.0 (no-op) for reference products,
                        # single-output suppliers, and external (aggregated)
                        # providers.
                        m = coproduct_multiplier.get((target_proc, flow_uuid), 1.0)
                        exchanges.append({
                            "input":  (target_db, target_proc),
                            "amount": norm_amount * m,
                            "unit":   norm_unit,
                            "type":   "technosphere",
                        })
                    total_tech_linked += 1
                    pdiag["tech_linked"] += 1
                    if target_db == EXTERNAL_PROVIDER_DB:
                        total_tech_external += 1
                        pdiag["tech_external"] += 1
                    if not is_input:  # a waste-treatment output link
                        total_waste_treatment_links += 1
                        if len(waste_treatment_examples) < 5:
                            waste_treatment_examples.append(
                                f"{proc.get('name', proc_uuid)} sends {flow_ref.get('name', flow_uuid)} "
                                f"to {external_uuid_to_name.get(target_proc) or (all_processes.get(target_proc) or {}).get('name', target_proc)}")
                else:
                    total_tech_unlinked += 1
                    pdiag["tech_unlinked"] += 1

    if not any(e["type"] == "production" for e in exchanges):
        raise RuntimeError(
            f"Process '{proc.get('name', proc_uuid)}' (proc UUID: {proc_uuid}) "
            f"has no production exchange. It cannot be used as a supplier in LCA. "
            f"Check that exactly one exchange has isQuantitativeReference=true and isInput=false."
        )

    location = proc.get("location", {})
    raw_loc = location.get("name", "") if isinstance(location, dict) else ""
    location_name = LOCATION_MAP.get(raw_loc, raw_loc) or "GLO"
    # brightway's geomapping eval()s any location string containing "(" (it treats
    # it as a possibly re-tupleized regionalization key — see bw2data
    # retupleize_geo_strings), so an unmapped country name like
    # "Congo (the Democratic Republic of the)" crashes .write() with a SyntaxError.
    # LOCATION_MAP already normalizes the common cases (e.g. the USA name) to codes;
    # for anything it didn't, drop the parenthetical qualifier so the raw name is
    # eval-safe. General on purpose — handles any future paren-bearing country.
    if "(" in location_name:
        location_name = location_name.split(" (")[0].strip() or "GLO"

    if co_flow is None:
        act_name = proc.get("name", proc_uuid)
    else:
        _co_flow_name = (all_flows.get(co_flow) or {}).get("name", co_flow)
        act_name = f"{proc.get('name', proc_uuid)} [causal co-product: {_co_flow_name}]"

    db_data[key] = {
        "name":     act_name,
        "code":     key[1],
        "location": location_name,
        "unit":     ref_unit,
        "exchanges": exchanges,
    }

    # Keyed by activity code so general/04's manifest can look any solved
    # activity up directly (main jobs: code == proc_uuid, unchanged).
    proc_provenance[key[1]] = {
        "name":       act_name,
        "version":    proc.get("version"),
        "lastChange": proc.get("lastChange"),
        **({"coproduct_of": proc_uuid, "coproduct_flow": co_flow}
           if co_flow is not None else {}),
        **pdiag,
    }

# Hard-stop if a CONSUMED causal co-product has no factor column at all — its
# activity would then be built entirely on the mass-fraction fallback, i.e. a
# flattened scalar wearing per-exchange clothes. Unconsumed co-products may pass
# (their activities are inert); partial holes use the counted fallback, same as
# the reference-product side.
_empty_consumed = [(p, f) for (p, f) in causal_coproduct_consumed
                   if not causal_coproduct_info[(p, f)]["column"]]
if _empty_consumed:
    raise RuntimeError(
        f"{len(_empty_consumed)} consumed causal co-product(s) have NO per-exchange "
        f"factor column in their process JSON — build STOPPED before writing "
        f"'{USLCI_DB_NAME}'.\n"
        f"  Their activities would silently degrade to a flat mass-fraction split, "
        f"which is exactly the mis-allocation causal handling exists to prevent.\n"
        f"  Affected (process UUID, co-product flow UUID):\n"
        + "\n".join(f"    {p}  {f}" for p, f in _empty_consumed)
    )

def _unknown_units_detail():
    """One line per unknown unit, with up to 3 example flows so the operator can
    see WHERE the unit occurs and judge whether it touches their target."""
    lines = []
    for u in sorted(UNKNOWN_UNITS):
        flows = sorted(UNKNOWN_UNITS[u])
        examples = ", ".join(
            f"'{all_flows.get(f, {}).get('name', '?')}' ({f})" for f in flows[:3]
        )
        more = f" (+{len(flows) - 3} more flows)" if len(flows) > 3 else ""
        lines.append(f'    "{u}" — e.g. {examples}{more}')
    return "\n".join(lines)

# Hard-stop on unrecognized units BEFORE writing (ledger #7). An unconverted unit
# means silently wrong exchange amounts; refuse to build a database that contains
# them unless the operator has explicitly opted into passthrough.
if UNKNOWN_UNITS and not ALLOW_UNIT_PASSTHROUGH:
    raise RuntimeError(
        f"{len(UNKNOWN_UNITS)} unrecognized unit string(s) encountered — build STOPPED before "
        f"writing '{USLCI_DB_NAME}'.\n"
        f"  These exchanges would be passed through WITHOUT unit conversion, i.e. silently wrong "
        f"amounts. Add each unit to WITHIN_FP (with its factor to the flow-property reference unit) "
        f"and rerun, or set the ALLOW_UNIT_PASSTHROUGH=1 environment variable to import anyway "
        f"with a warning (NOT recommended for study use):\n"
        + _unknown_units_detail()
    )

bd.Database(USLCI_DB_NAME).write(db_data)

# Carry the electricity-baseline vintage stamp (from 03b) onto this database, so
# the validation harness can assert each case is diffed against the grid vintage
# its openLCA reference export used. A build is single-vintage by design.
if electricity_vintage is not None:
    bd.databases[USLCI_DB_NAME]["electricity_vintage"] = electricity_vintage
    bd.databases.flush()

# Stamp WHICH USLCI the build came from. Without this nothing at run time can
# distinguish "that process doesn't exist" from "that process is in a newer
# USLCI release than this build" -- the two look identical to a caller, and the
# second is common because USLCI ships quarterly (a fishmeal process absent from
# v1.2026-03.0 but present in v1.2026-06.0 is what prompted this).
#
# The identifier is the source zips' name + SHA256, deliberately NOT a release
# version string: the full zip carries no intrinsic version field (its
# openlca.json names only the electricity-library dependency), and the release
# hash in bundle filenames proved unreliable as a content marker. A content hash
# is the honest identity -- it says exactly which bytes produced this database
# even when it cannot say what upstream calls them.
bd.databases[USLCI_DB_NAME]["uslci_source"] = _source_identity
bd.databases.flush()

print(f"Database '{USLCI_DB_NAME}' written — {len(db_data)} processes"
      + (f" [electricity_vintage = {electricity_vintage}]." if electricity_vintage else "."))
print(f"  Biosphere exchanges: {total_bio_matched} matched, {total_bio_unmatched} unmatched")
print(f"  Technosphere exchanges: {total_tech_linked} linked "
      f"({total_tech_external} via '{EXTERNAL_PROVIDER_DB}'), {total_tech_unlinked} unlinked")

# Persist per-process import diagnostics for general/04's per-run audit manifest
# (RELEASE_PLAN 4.4 / ledger #9). Filename mirrors run_manifest.db_provenance_filename()
# in general/; kept as a local literal to avoid setup/ importing from general/.
# The bundle build keeps the historical unsuffixed name; the full-database build
# gets its own file so the two builds' diagnostics can coexist without either
# being read as if it described the other.
_DB_PROVENANCE_PATH = REPO_ROOT / (
    "uslci_db_provenance.json" if USLCI_DB_NAME == USLCI_BUNDLE_DB_NAME
    else f"uslci_db_provenance.{USLCI_DB_NAME}.json"
)

def _pkg_versions(names):
    out = {}
    for _n in names:
        try:
            out[_n] = importlib.metadata.version(_n)
        except importlib.metadata.PackageNotFoundError:
            out[_n] = None
    return out

_db_provenance = {
    "schema":               "uslci-db-provenance/1",
    "generated":            datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    "project":              PROJECT_NAME,
    "database":             USLCI_DB_NAME,
    "db_activity_count":    len(db_data),
    "external_provider_db": EXTERNAL_PROVIDER_DB,
    "uslci_source":         _source_identity,
    "packages":             _pkg_versions(["bw2data", "bw2io", "bw2calc",
                                           "fedelemflowlist", "lciafmt"]),
    "totals": {
        "bio_matched":    total_bio_matched,   "bio_unmatched":  total_bio_unmatched,
        "tech_linked":    total_tech_linked,   "tech_external":  total_tech_external,
        "tech_unlinked":  total_tech_unlinked, "tech_ambiguous": total_tech_ambiguous,
    },
    "processes": proc_provenance,
}
_DB_PROVENANCE_PATH.write_text(
    json.dumps(_db_provenance, indent=2, ensure_ascii=False), encoding="utf-8"
)
print(f"  Provenance sidecar: {_DB_PROVENANCE_PATH} ({len(proc_provenance)} processes)")

if total_bio_unmatched:
    print("\n  Note: unmatched biosphere flows are elementary flows whose UUIDs are not")
    print("  in biosphere-fedefl. They will be excluded from LCIA scoring.")
if total_tech_unlinked:
    print("\n  Note: unlinked technosphere exchanges reference processes not included")
    print("  in the downloaded zips. Add more process exports to extend coverage.")
if total_tech_ambiguous:
    print(f"\n  WARNING: {total_tech_ambiguous} technosphere exchange(s) had an unresolvable "
          f"hinted provider AND multiple candidate providers in '{EXTERNAL_PROVIDER_DB}' -- "
          f"left unlinked rather than guessed. Examples:")
    for ex in ambiguous_examples[:5]:
        print(f"    {ex}")
if n_multi_native or n_multi_mass or n_multi_causal:
    print(f"\n  Allocation: {n_multi_native + n_multi_mass + n_multi_causal} multi-output process(es) "
          f"({n_multi_native} native factors, {n_multi_mass} mass fraction, {n_multi_causal} causal "
          f"per-exchange). {len(coproduct_multiplier)} co-product link(s) re-based off the "
          f"reference product's basis. Examples:")
    for ex in multi_examples[:5]:
        print(f"    {ex}")
if total_causal_fallback:
    print(f"\n  Note: {total_causal_fallback} exchange(s) in causal-allocation process(es) lacked a "
          f"per-exchange factor and used the mass-fraction fallback.")
if causal_coproduct_info:
    print(f"\n  Causal co-products: {len(causal_coproduct_info)} dedicated activit(ies) built "
          f"from per-exchange factor columns; {total_causal_coproduct_links} consuming link(s) "
          f"redirected to them ({len(causal_coproduct_consumed)} distinct co-product(s) consumed)."
          + ("" if not causal_coproduct_examples else " Examples:"))
    for ex in causal_coproduct_examples[:5]:
        print(f"    {ex}")
if total_waste_treatment_links:
    print(f"\n  Waste treatment: {total_waste_treatment_links} WASTE_FLOW output(s) linked to a "
          f"treatment provider (disposal burden, e.g. landfill methane, now charged to the "
          f"generating process)." + ("" if not waste_treatment_examples else " Examples:"))
    for ex in waste_treatment_examples[:5]:
        print(f"    {ex}")
if total_avoided_product_links:
    print(f"\n  Avoided products: {total_avoided_product_links} exchange(s) flagged "
          f"isAvoidedProduct credited (sign-flipped), matching openLCA (e.g. landfill-gas / "
          f"combustion electricity displacing grid power)." +
          ("" if not avoided_product_examples else " Examples:"))
    for ex in avoided_product_examples[:5]:
        print(f"    {ex}")
if skipped_multipliers:
    print(f"\n  WARNING: {len(skipped_multipliers)} co-product output(s) could not be re-based "
          f"(missing yield or allocation factor) -- consumers of these fall back to the reference "
          f"product's basis (pre-fix behavior). Examples:")
    for puuid, fuuid in skipped_multipliers[:5]:
        print(f"    process {puuid}, flow {fuuid}")
if UNKNOWN_UNITS:
    # Only reachable when ALLOW_UNIT_PASSTHROUGH=1 (otherwise the build raised above).
    print(f"\n  WARNING: {len(UNKNOWN_UNITS)} unrecognized unit string(s) encountered during import.")
    print(f"  ALLOW_UNIT_PASSTHROUGH is set, so these were passed through WITHOUT unit conversion")
    print(f"  — amounts may be silently wrong. Add the following to WITHIN_FP and rerun without the flag:")
    print(_unknown_units_detail())

# =============================================================================
# SUMMARY
# =============================================================================
print(f"\nProcesses in '{USLCI_DB_NAME}':")
for act in bd.Database(USLCI_DB_NAME):
    n_bio  = sum(1 for e in act.exchanges() if e["type"] == "biosphere")
    n_tech = sum(1 for e in act.exchanges() if e["type"] == "technosphere")
    print(f"  {act['name'][:70]}")
    print(f"    {n_bio} biosphere, {n_tech} technosphere exchanges")
