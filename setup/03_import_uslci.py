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
from pathlib import Path
import zipfile
import json
import bw2data as bd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import PROJECT_NAME, BIOSPHERE_DB, USLCI_DB as USLCI_DB_NAME, \
    ELECTRICITY_BASELINE_DB as EXTERNAL_PROVIDER_DB, REPO_ROOT
# EXTERNAL_PROVIDER_DB: the optional injected background DB consulted when a
# bundle exchange's provider isn't in the bundle itself (see
# setup/03b_import_electricity_baseline.py). Absent entirely if that script
# hasn't been run -- those flows stay cutoffs, same as before this fallback
# existed.

HERE = Path(__file__).parent

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
    "cu ft": 0.0283168,  "ft3": 0.0283168,
    # Energy → MJ
    "mj": 1.0,   "kj": 1e-3,   "gj": 1e3,    "kwh": 3.6,
    "btu": 1.05506e-3,
    "mmbtu": 1055.06,   "mm btu": 1055.06,   "mmBtu": 1055.06,
    # Transport → t*km
    "t*km": 1.0, "tkm": 1.0,   "t*mi": 1.60934,  "kg*km": 1e-3,
    # Area → m2
    "m2": 1.0,
    # Area × time → m2*a (land use; already reference unit)
    "m2*a": 1.0,
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

# Case-insensitive lookup built from WITHIN_FP. All keys are lowercased at
# definition time so lookup just needs unit.lower(). Explicit lowercase entries
# in WITHIN_FP (e.g. "mmbtu") are already correct.
_WITHIN_FP_LOWER = {k.lower(): v for k, v in WITHIN_FP.items()}

# Accumulates unit strings not found in _WITHIN_FP_LOWER during import.
# Reported after the main loop — add missing units to WITHIN_FP and rerun.
UNKNOWN_UNITS: set = set()


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
    within = _WITHIN_FP_LOWER.get(unit.lower() if unit else "")
    if within is None:
        UNKNOWN_UNITS.add(unit)
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
# Sort oldest-first by file modification time so newer zips overwrite older ones
# when the same process UUID appears in multiple exports (e.g. a corrected re-download).
zip_files = sorted(
    BUNDLE_DIR.glob("????????-????-????-????-????????????_*.zip"),
    key=lambda p: p.stat().st_mtime
)
if not zip_files:
    raise SystemExit(
        f"No process zip files found in {BUNDLE_DIR}. Download per-process exports "
        f"from LCA Commons and place them there (same dir 03b scans)."
    )

print(f"Found {len(zip_files)} process zip(s).")

all_processes = {}  # proc_uuid -> process dict
all_flows     = {}  # flow_uuid -> flow dict
overwritten_processes = set()  # UUIDs resolved by newer zip winning

for zpath in zip_files:
    with zipfile.ZipFile(zpath) as z:
        for name in z.namelist():
            if name.startswith("processes/") and name.endswith(".json"):
                data = json.loads(z.read(name))
                uid = data.get("@id")
                if uid:
                    if uid in all_processes:
                        overwritten_processes.add(uid)
                    all_processes[uid] = data
            elif name.startswith("flows/") and name.endswith(".json"):
                data = json.loads(z.read(name))
                uid = data.get("@id")
                if uid:
                    all_flows[uid] = data

print(f"  {len(all_processes)} unique processes, {len(all_flows)} unique flows.")
if overwritten_processes:
    print(f"  NOTE: {len(overwritten_processes)} process UUID(s) appeared in multiple zips "
          f"— kept data from the newest file (by modification time).")
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
if EXTERNAL_PROVIDER_DB in bd.databases:
    for act in bd.Database(EXTERNAL_PROVIDER_DB):
        external_provider_uuids.add(act["code"])
        external_uuid_to_name[act["code"]] = act["name"]
        ref_flow_uuid = act.get("reference_product_flow_uuid")
        if ref_flow_uuid:
            flow_to_external_process.setdefault(ref_flow_uuid, set()).add(act["code"])
    print(f"Loaded {len(external_provider_uuids)} external provider process(es) "
          f"({len(flow_to_external_process)} distinct reference flow(s)) "
          f"from '{EXTERNAL_PROVIDER_DB}'.")


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


# Mass flow-property UUID in the USLCI/FEDEFL flow-property tables.
_MASS_FP_UUID = "93a60a56-a3c8-11da-a746-0800200b9a66"


def _allocation_for(proc, proc_uuid):
    """Compute one multi-output process's reference-product allocation, plus the
    per-output data needed to correctly re-basis consumers of its NON-reference
    co-products.

    A multi-output process is imported as ONE brightway activity: its production
    exchange is the *reference* product's native yield and its inputs/biosphere
    exchanges are scaled by the reference product's allocation factor. A consumer
    that draws a co-product must not inherit the reference product's basis (that
    was the co-product allocation bug -- see DEVLOG).

    Returns (alloc_factor, ref_flow_uuid, per_output, method, causal_factors):
      alloc_factor  -- scalar factor applied to every input/biosphere exchange
                       (reference product's own share; 1.0 for single-output).
                       For "causal" this is only a fallback for exchanges lacking
                       a causal factor -- see causal_factors.
      ref_flow_uuid -- reference product flow UUID (None for single-output or a
                       reference-on-input waste-treatment process).
      per_output    -- {flow_uuid: (native_yield, alloc)} for every PRODUCT_FLOW
                       output. `alloc` is computed by the SAME method used for
                       the reference product so co-product multipliers stay
                       internally consistent. Empty for single-output and causal.
      method        -- "single" | "native" | "mass" | "causal".
      causal_factors-- {exchange_internalId: factor} for the reference product,
                       for CAUSAL_ALLOCATION processes; None otherwise. Causal
                       allocation is per-exchange (each input/emission carries its
                       own factor per product), so it cannot be flattened to one
                       scalar without mis-allocating every exchange whose causal
                       factor differs from the mass fraction.

    Raises the same malformed/uncomputable errors the previous inline logic did.
    """
    output_products = []  # (flow_uuid, native_yield, mass_kg)
    ref_flow_uuid = None
    for exc in proc.get("exchanges", []):
        if exc.get("isInput") or exc.get("flow", {}).get("flowType") != "PRODUCT_FLOW":
            continue
        flow_uuid_e = exc.get("flow", {}).get("@id")
        fp_uuid_e   = exc.get("flowProperty", {}).get("@id", "")
        amount_e    = exc.get("amount", 0.0)
        unit_e      = exc.get("unit", {}).get("name", "")
        # Native yield in the flow's own reference unit.
        norm_e, _ = normalize(amount_e, unit_e, fp_uuid_e, flow_uuid_e)
        # Convert to kg for the mass-fraction fallback denominator.
        # FLOW_CONV factor semantics: factor = (other fp units) per (ref fp unit).
        flow_conv = FLOW_CONV.get(flow_uuid_e, {})
        ref_fp    = flow_conv.get("ref_fp_uuid", "")
        convs     = flow_conv.get("conversions", {})
        if ref_fp == _MASS_FP_UUID:
            mass_kg = norm_e                                    # ref already kg
        elif _MASS_FP_UUID in convs:
            mass_kg = norm_e * convs[_MASS_FP_UUID]["factor"]   # e.g. m3 × (kg/m3)
        else:
            mass_kg = 0.0                                       # no mass conversion
        output_products.append((flow_uuid_e, norm_e, mass_kg))
        if exc.get("isQuantitativeReference"):
            ref_flow_uuid = flow_uuid_e

    if len(output_products) <= 1:
        return 1.0, ref_flow_uuid, {}, "single", None

    if ref_flow_uuid is None:
        raise RuntimeError(
            f"Multi-output process '{proc.get('name', proc_uuid)}' (proc UUID: {proc_uuid}) "
            f"has {len(output_products)} product outputs but no isQuantitativeReference flag. "
            f"Cannot determine which output is the reference product for allocation.\n"
            f"  Output flow UUIDs: {[u for u, _, _ in output_products]}"
        )

    # Prefer openLCA's own pre-computed factor for this process's declared
    # defaultAllocationMethod over a from-scratch mass fraction. USLCI does NOT
    # default to mass/physical allocation uniformly -- 9 of 18 multi-output
    # processes across the 4 test-case bundles use ECONOMIC_ or CAUSAL_
    # allocation (corn, soybeans, both steel co-product processes, several
    # paper/pulp mill processes). PHYSICAL/ECONOMIC give one factor per product
    # (a scalar applied to all inputs); CAUSAL gives a factor per *exchange* per
    # product -- handled separately below. Falls back to a from-scratch mass
    # fraction only when the process ships no usable native factor at all.
    default_method = proc.get("defaultAllocationMethod")
    total_mass = sum(m for _, _, m in output_products if m > 0)

    if default_method == "CAUSAL_ALLOCATION":
        # Per-exchange allocation: build the reference product's factor for each
        # input/emission exchange, keyed by its internalId. Flattening this to a
        # single scalar (native lookup returns nothing for causal, so the old
        # code fell back to the mass fraction) mis-allocates every exchange whose
        # causal factor differs from that fraction -- e.g. the cellulosic-ethanol
        # process over-attributed forest-residue feedstock to ethanol 1.83x.
        causal_factors = {}
        for af in proc.get("allocationFactors", []):
            if (af.get("allocationType") == "CAUSAL_ALLOCATION"
                    and af.get("product", {}).get("@id") == ref_flow_uuid
                    and "exchange" in af):
                iid = af["exchange"].get("internalId")
                if iid is not None:
                    causal_factors[iid] = af["value"]
        # Mass-fraction fallback for any *added* exchange that somehow lacks a
        # causal factor (in the observed data only the product outputs lack one,
        # and outputs are never added to the activity). per_output is left empty:
        # a causal co-product's burden is per-exchange, so the scalar co-product
        # multiplier cannot represent it -- the pre-pass skips causal here and
        # warns if any causal co-product is actually consumed.
        ref_mass = next(m for f, _, m in output_products if f == ref_flow_uuid)
        fallback = (ref_mass / total_mass) if total_mass else 1.0
        return fallback, ref_flow_uuid, {}, "causal", causal_factors

    def _native(flow_uuid):
        return next(
            (af["value"] for af in proc.get("allocationFactors", [])
             if af.get("allocationType") == default_method
             and af.get("product", {}).get("@id") == flow_uuid
             and "exchange" not in af),  # exclude per-exchange CAUSAL breakdowns
            None
        )

    ref_native = _native(ref_flow_uuid)

    per_output = {}
    if ref_native is not None:
        method = "native"
        alloc_factor = ref_native
        for flow_uuid, yld, _mass in output_products:
            per_output[flow_uuid] = (yld, _native(flow_uuid))
    else:
        method = "mass"
        ref_mass = next(m for f, _, m in output_products if f == ref_flow_uuid)
        if ref_mass == 0.0:
            _ref_exc = next(
                (e for e in proc.get("exchanges", [])
                 if e.get("isQuantitativeReference") and not e.get("isInput")),
                {}
            )
            _ref_unit = _ref_exc.get("unit", {}).get("name", "<unknown>")
            _ref_fp   = _ref_exc.get("flowProperty", {}).get("@id", "<unknown>")
            raise RuntimeError(
                f"Allocation failed for multi-output process '{proc.get('name', proc_uuid)}' "
                f"(proc UUID: {proc_uuid}):\n"
                f"  Ref product flow UUID : {ref_flow_uuid}\n"
                f"  Unit in JSON          : {_ref_unit}\n"
                f"  Flow property UUID    : {_ref_fp}\n"
                f"  FLOW_CONV entry exists: {ref_flow_uuid in FLOW_CONV}\n"
                f"  Mass property UUID    : {_MASS_FP_UUID}\n"
                f"  No usable '{default_method}' allocationFactors entry for this product, and "
                f"no mass conversion available for a from-scratch fallback either.\n"
                f"  Fix: add a mass conversion for this flow in setup/00_build_flow_conversion_table.py "
                f"and regenerate FLOW_CONV, or handle it as a service flow (not a mass-allocatable product)."
            )
        alloc_factor = ref_mass / total_mass
        for flow_uuid, yld, mass_kg in output_products:
            per_output[flow_uuid] = (yld, (mass_kg / total_mass) if total_mass else None)

    return alloc_factor, ref_flow_uuid, per_output, method, None


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
total_causal_coproduct_links = 0   # links consuming a causal process's co-product
causal_coproduct_examples = []

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
causal_coproduct_flows = set()   # non-ref product flows of causal processes (unsupported as inputs)
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
        # Causal co-products can't be scalar-re-based (their burden is per-exchange).
        # Record their flows so the build loop can warn if any is consumed as an input.
        for _e in _proc.get("exchanges", []):
            if (not _e.get("isInput") and not _e.get("isQuantitativeReference")
                    and _e.get("flow", {}).get("flowType") == "PRODUCT_FLOW"):
                causal_coproduct_flows.add(_e["flow"]["@id"])
    # Co-product multipliers: only for scalar-allocated processes (native/mass).
    # single-output and causal have empty per_output -> no multipliers.
    if _method in ("single", "causal") or _ref_flow is None:
        continue
    _ref_yield, _ref_alloc = _per_output[_ref_flow]
    for _flow_uuid, (_tgt_yield, _tgt_alloc) in _per_output.items():
        if _flow_uuid == _ref_flow:
            continue
        if not _ref_alloc or not _ref_yield or not _tgt_yield or _tgt_alloc is None:
            skipped_multipliers.append((_puuid, _flow_uuid))
            continue
        coproduct_multiplier[(_puuid, _flow_uuid)] = (
            (_ref_yield * _tgt_alloc) / (_tgt_yield * _ref_alloc)
        )

for proc_uuid, proc in all_processes.items():
    key = (USLCI_DB_NAME, proc_uuid)
    exchanges = []
    ref_unit = "unit"

    # Reference product's allocation factor(s), computed once in the pre-pass.
    # For native/mass allocation this is one scalar applied to every input/
    # biosphere exchange; for causal allocation each exchange has its own factor
    # (keyed by internalId), with alloc_factor as the fallback. Co-product
    # consumers are re-based separately, at their link site, via
    # coproduct_multiplier. 1.0 for single-output.
    alloc_factor    = alloc_cache[proc_uuid]
    causal_factors  = causal_cache.get(proc_uuid)

    for exc in proc.get("exchanges", []):
        flow_ref  = exc.get("flow", {})
        flow_uuid = flow_ref.get("@id")
        flow_type = flow_ref.get("flowType", "")
        is_input  = exc.get("isInput", False)
        is_ref    = exc.get("isQuantitativeReference", False)

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
                else:
                    total_bio_unmatched += 1

            elif flow_type in ("PRODUCT_FLOW", "WASTE_FLOW") and is_input:
                norm_amount, norm_unit = normalize(amount, unit, fp_uuid, flow_uuid)
                target_db, target_proc, was_ambiguous = _resolve_provider(exc, flow_uuid)
                if was_ambiguous:
                    # Several candidate producers (bundle-internal and/or
                    # external) supply this flow, and the exchange's own
                    # defaultProvider hint didn't match any of them -- non-
                    # fatal: leave unlinked/cutoff rather than guess.
                    total_tech_ambiguous += 1
                    hinted_uuid = (exc.get("defaultProvider") or {}).get("@id")
                    candidates = ({(USLCI_DB_NAME, u) for u in flow_to_process.get(flow_uuid, set())} |
                                  {(EXTERNAL_PROVIDER_DB, u) for u in flow_to_external_process.get(flow_uuid, set())})
                    ambiguous_examples.append(
                        f"{proc.get('name', proc_uuid)}: flow {flow_uuid} "
                        f"(hint {hinted_uuid!r} unresolved; {len(candidates)} candidates: {sorted(candidates)})"
                    )
                if target_proc:
                    # Safety net: a consumer drawing a CAUSAL process's non-
                    # reference co-product cannot be scalar-re-based (its burden
                    # is per-exchange), and no coproduct_multiplier is built for
                    # it. Does not occur in the current bundles (both causal
                    # co-products go unconsumed); warn loudly if that changes.
                    if flow_uuid in causal_coproduct_flows:
                        total_causal_coproduct_links += 1
                        if len(causal_coproduct_examples) < 5:
                            causal_coproduct_examples.append(
                                f"{proc.get('name', proc_uuid)} draws causal co-product flow {flow_uuid}")
                    # Re-basis co-product draws. When this exchange targets a
                    # NON-reference output of a multi-output supplier, the
                    # supplier's single brightway activity is built on its
                    # reference product's yield+allocation; convert the request
                    # into the equivalent reference-product amount that carries
                    # this co-product's own allocated burden. 1.0 (no-op) for
                    # reference products, single-output suppliers, and external
                    # (aggregated) providers.
                    m = coproduct_multiplier.get((target_proc, flow_uuid), 1.0)
                    exchanges.append({
                        "input":  (target_db, target_proc),
                        "amount": norm_amount * m,
                        "unit":   norm_unit,
                        "type":   "technosphere",
                    })
                    total_tech_linked += 1
                    if target_db == EXTERNAL_PROVIDER_DB:
                        total_tech_external += 1
                else:
                    total_tech_unlinked += 1

    if not any(e["type"] == "production" for e in exchanges):
        raise RuntimeError(
            f"Process '{proc.get('name', proc_uuid)}' (proc UUID: {proc_uuid}) "
            f"has no production exchange. It cannot be used as a supplier in LCA. "
            f"Check that exactly one exchange has isQuantitativeReference=true and isInput=false."
        )

    location = proc.get("location", {})
    raw_loc = location.get("name", "") if isinstance(location, dict) else ""
    location_name = LOCATION_MAP.get(raw_loc, raw_loc) or "GLO"

    db_data[key] = {
        "name":     proc.get("name", proc_uuid),
        "code":     proc_uuid,
        "location": location_name,
        "unit":     ref_unit,
        "exchanges": exchanges,
    }

bd.Database(USLCI_DB_NAME).write(db_data)

print(f"Database '{USLCI_DB_NAME}' written — {len(db_data)} processes.")
print(f"  Biosphere exchanges: {total_bio_matched} matched, {total_bio_unmatched} unmatched")
print(f"  Technosphere exchanges: {total_tech_linked} linked "
      f"({total_tech_external} via '{EXTERNAL_PROVIDER_DB}'), {total_tech_unlinked} unlinked")

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
if total_causal_coproduct_links:
    print(f"\n  WARNING: {total_causal_coproduct_links} technosphere link(s) consume a CAUSAL "
          f"process's NON-reference co-product, which cannot be correctly re-based by the scalar "
          f"co-product multiplier (its burden is per-exchange). These are UNDER-supported — the "
          f"consumer gets the reference product's basis. Handle explicitly if this matters:")
    for ex in causal_coproduct_examples[:5]:
        print(f"    {ex}")
if skipped_multipliers:
    print(f"\n  WARNING: {len(skipped_multipliers)} co-product output(s) could not be re-based "
          f"(missing yield or allocation factor) -- consumers of these fall back to the reference "
          f"product's basis (pre-fix behavior). Examples:")
    for puuid, fuuid in skipped_multipliers[:5]:
        print(f"    process {puuid}, flow {fuuid}")
if UNKNOWN_UNITS:
    print(f"\n  WARNING: {len(UNKNOWN_UNITS)} unrecognized unit string(s) encountered during import.")
    print(f"  These exchanges were passed through without unit conversion — amounts may be wrong.")
    print(f"  Add the following to WITHIN_FP and rerun:")
    for u in sorted(UNKNOWN_UNITS):
        print(f"    \"{u}\": <conversion_factor>")

# =============================================================================
# SUMMARY
# =============================================================================
print(f"\nProcesses in '{USLCI_DB_NAME}':")
for act in bd.Database(USLCI_DB_NAME):
    n_bio  = sum(1 for e in act.exchanges() if e["type"] == "biosphere")
    n_tech = sum(1 for e in act.exchanges() if e["type"] == "technosphere")
    print(f"  {act['name'][:70]}")
    print(f"    {n_bio} biosphere, {n_tech} technosphere exchanges")
