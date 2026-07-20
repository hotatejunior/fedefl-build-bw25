#!/usr/bin/env python3
"""
05_validate_uslci.py
--------------------
Validates LCIA scores computed by brightway against openLCA reference exports.

--mode direct
    Compares only the direct biosphere exchanges of each target process,
    isolating FEDEFL flow mapping and TRACI 2.2 CFs from technosphere logic.
    Reference: "Direct impact contributions" sheet in the kg-basis xlsx.
    BW scores are normalized to per-kg using the production exchange at runtime.

--mode full_chain (default)
    Compares full supply-chain LCIA scores.
    Reference: "Impacts" sheet in the standard openLCA xlsx. These exports are
    standardized to "Amount: 1.0 kg" of the reference product (check each
    xlsx's own "Calculation setup" sheet to confirm) -- NOT the process's
    native declared reference amount, which is usually a different, arbitrary
    quantity. BW scores are normalized to the same 1 kg basis at runtime.

Requires: setup/01, setup/02, setup/03b, setup/03 to have been run first (03b
provides the electricity baseline background that these full-chain reference
exports were computed with).
"""

import argparse
import os
import sys
from pathlib import Path
import pandas as pd
import bw2data as bd
import bw2calc as bc

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
from config import PROJECT_NAME, BIOSPHERE_DB, USLCI_DB as USLCI_DB_NAME, METHOD_ROOT, \
    REPO_ROOT

TEMP_DB_NAME  = "_direct_validation_temp"

# =============================================================================
# CONFIG
# =============================================================================
# Mode is a CLI flag so replicators don't edit source (ledger #8 / Phase 3.3).
_parser = argparse.ArgumentParser(
    description="Validate brightway LCIA scores against openLCA reference exports."
)
_parser.add_argument(
    "--mode", choices=("full_chain", "direct"), default="full_chain",
    help="full_chain (default): full supply-chain LCIA vs the standard openLCA "
         "export. direct: direct biosphere exchanges only vs the kg-basis export.",
)
VALIDATION_MODE = _parser.parse_args().mode

# Defaults to source_data/ at the repo root; override with the SOURCE_DATA_DIR
# env var to point at your own checkout instead of editing this file.
SOURCE_DATA = Path(os.environ.get("SOURCE_DATA_DIR", REPO_ROOT / "source_data"))
if not SOURCE_DATA.is_dir():
    raise SystemExit(
        f"Source-data directory not found: {SOURCE_DATA}\n"
        f"Place the openLCA reference exports in {REPO_ROOT / 'source_data'}, "
        f"or set the SOURCE_DATA_DIR environment variable to your local checkout."
    )

# direct mode: xlsx must have a "Direct impact contributions" sheet (kg-basis export)
TARGETS_DIRECT = {
    "0aaf1e13-5d80-37f9-b7bb-81a6b8965c71":
        HERE / "Petroleum_refining__at_refinery___US_kg_basis.xlsx",
}

# full_chain mode: xlsx must have an "Impacts" sheet (standard openLCA export).
# All 4 exports below were computed with the 2025 electricity baseline mounted
# and are on a "1.0 kg" basis (see module docstring) -- the 4 locked
# validation test cases (petroleum, corn, cement, steel).
# Petroleum, corn, and cement now point at the US_AVG_ELEC_SELECTION exports:
# the original exports left each process's direct "Electricity, AC, 120 V" input
# with no default provider (a product-system setup gap on the openLCA side), so
# openLCA under-counted electricity's entire upstream burden. Re-exported after
# manually linking the US average grid provider (7068192a). Steel is unchanged --
# it's a pure foreground process with no electricity input. See DEVLOG.
TARGETS_FULL = {
    "0aaf1e13-5d80-37f9-b7bb-81a6b8965c71":
        SOURCE_DATA /"Petroleum_refining__at_refinery___US__AVG_ELEC_SELECTION.xlsx",
    "11256034-2355-3add-ade9-59983025dded":
        SOURCE_DATA /"Corn__whole_plant__at_field___US_AVG_ELEC_SELECTION.xlsx",
    "62993671-574c-3fc5-b66a-6be3bb21ad3d":
        SOURCE_DATA /"Portland_cement__at_plant___US__US_AVG_ELEC_SELECTION.xlsx",
    "ac54bc7d-5db5-3b4f-9175-5dd02f678312":
        SOURCE_DATA /"Steel__billets__at_plant___RNA_results.xlsx",
}

# Density (kg/m3) for processes whose production exchange is in volume units.
# The production exchange amount is read from brightway at runtime; only the
# density needs to be configured here.
DENSITY_KG_M3 = {
    "0aaf1e13-5d80-37f9-b7bb-81a6b8965c71": 849.0,  # petroleum at refinery
}

# Plausible (min, max) kg-per-run bounds per process UUID.
# If the derived value falls outside this range the normalization is probably
# wrong — either the density is misconfigured or the production exchange was
# re-scaled in a USLCI update.
KG_PER_RUN_BOUNDS = {
    "0aaf1e13-5d80-37f9-b7bb-81a6b8965c71": (0.10, 0.50),  # petroleum ~0.2143 kg
}

# =============================================================================
# BRIGHTWAY SETUP
# =============================================================================
bd.projects.set_current(PROJECT_NAME)
if not bd.projects.twofive:
    bd.projects.migrate_project_25()

if BIOSPHERE_DB not in bd.databases:
    raise RuntimeError(f"'{BIOSPHERE_DB}' not found. Run setup/01_setup_biosphere_fedefl.py first.")
if USLCI_DB_NAME not in bd.databases:
    raise RuntimeError(f"'{USLCI_DB_NAME}' not found. Run setup/03_import_uslci.py first.")

traci_methods = {m[2]: m for m in bd.methods if m[:2] == METHOD_ROOT}
if not traci_methods:
    raise RuntimeError("No TRACI 2.2 methods found. Run setup/02_setup_traci22.py first.")

# =============================================================================
# NORMALIZATION
# =============================================================================
def _kg_per_native_unit(uuid, unit):
    """kg represented by 1 unit of a process's own declared unit (e.g. 1 m3 of
    petroleum's diesel output = density kg; 1 kg of anything already-mass = 1)."""
    unit = (unit or "kg").lower().strip()
    if unit == "kg":
        return 1.0
    elif unit in ("m3", "cubic meter", "cubic metres"):
        density = DENSITY_KG_M3.get(uuid)
        if density is None:
            raise RuntimeError(
                f"Process {uuid} is in '{unit}' but no density is configured in DENSITY_KG_M3."
            )
        return density
    else:
        raise RuntimeError(
            f"Process {uuid} is in unit '{unit}'. "
            f"Add a conversion to _kg_per_native_unit() to handle this unit."
        )


def _kg_per_run(act, uuid):
    """kg represented by this process's own declared production-exchange amount
    (i.e. its native reference amount, not necessarily 1 kg)."""
    prod_exc = next((e for e in act.exchanges() if e["type"] == "production"), None)
    if prod_exc is None:
        raise RuntimeError(f"Process {uuid} has no production exchange in '{USLCI_DB_NAME}'.")
    return prod_exc["amount"] * _kg_per_native_unit(uuid, prod_exc.get("unit"))

# =============================================================================
# PARSERS: openLCA REFERENCE EXPORTS
# =============================================================================
def parse_direct_impacts(xlsx_path, proc_uuid):
    """Return {category: score} from the 'Direct impact contributions' sheet (per kg)."""
    if not Path(xlsx_path).exists():
        raise FileNotFoundError(
            f"openLCA reference export not found: {xlsx_path}\n"
            f"  Export 'Direct impact contributions' (kg-basis) from openLCA and place it there."
        )
    df = pd.read_excel(xlsx_path, sheet_name="Direct impact contributions", header=None)
    proc_uuids = df.iloc[1, 4:]
    col = next((i + 4 for i, uid in enumerate(proc_uuids) if uid == proc_uuid), None)
    if col is None:
        raise ValueError(f"UUID {proc_uuid} not found in 'Direct impact contributions' sheet.")
    cats = df.iloc[5:, 2].tolist()
    vals = df.iloc[5:, col].tolist()
    return {
        str(cat): float(val) if pd.notna(val) else 0.0
        for cat, val in zip(cats, vals) if pd.notna(cat)
    }


def parse_full_chain_impacts(xlsx_path):
    """Return {category: score} from the 'Impacts' sheet (per production run)."""
    if not Path(xlsx_path).exists():
        raise FileNotFoundError(
            f"openLCA reference export not found: {xlsx_path}\n"
            f"  Export LCIA results (standard, not kg-basis) from openLCA and place it there."
        )
    df   = pd.read_excel(xlsx_path, sheet_name="Impacts", header=None)
    # Row 1: headers; rows 2+: data (col 2 = category name, col 4 = result)
    cats = df.iloc[2:, 2].tolist()
    vals = df.iloc[2:, 4].tolist()
    return {
        str(cat): float(val) if pd.notna(val) else 0.0
        for cat, val in zip(cats, vals) if pd.notna(cat)
    }

# =============================================================================
# LCA RUNNERS — both return {category: score}
# =============================================================================
def run_all_direct(source_act, methods):
    """
    Build a temp process with only source_act's direct biosphere exchanges
    (production amount = 1.0 so score is per process run), then sweep all
    methods via switch_method. Temp DB is created once, not per method.
    """
    temp_key = (TEMP_DB_NAME, source_act["code"])
    direct_exchanges = [{"input": temp_key, "amount": 1.0, "type": "production"}] + [
        {"input":  exc.input.key,
         "amount": exc["amount"],
         "unit":   exc.get("unit", ""),
         "type":   "biosphere"}
        for exc in source_act.exchanges()
        if exc["type"] == "biosphere"
    ]

    if TEMP_DB_NAME in bd.databases:
        del bd.databases[TEMP_DB_NAME]
    bd.Database(TEMP_DB_NAME).write({temp_key: {
        "name":      source_act["name"],
        "code":      source_act["code"],
        "unit":      source_act.get("unit", "unit"),
        "location":  source_act.get("location", "GLO"),
        "exchanges": direct_exchanges,
    }})

    sorted_methods = sorted(methods.items())
    temp_act = bd.get_activity(temp_key)
    lca = bc.LCA({temp_act: 1.0}, sorted_methods[0][1])
    lca.lci()
    scores = {}
    for category, method in sorted_methods:
        lca.switch_method(method)
        lca.lcia()
        scores[category] = lca.score
    return scores


def run_all_full_chain(act, methods):
    """Full supply-chain LCA. lci() once, switch_method for all categories."""
    sorted_methods = sorted(methods.items())
    lca = bc.LCA({act: 1.0}, sorted_methods[0][1])
    lca.lci()
    scores = {}
    for category, method in sorted_methods:
        lca.switch_method(method)
        lca.lcia()
        scores[category] = lca.score
    return scores

# =============================================================================
# COMPARE
# =============================================================================
targets = TARGETS_DIRECT if VALIDATION_MODE == "direct" else TARGETS_FULL
all_results = []

for uuid, xlsx_path in targets.items():
    try:
        act = bd.get_activity((USLCI_DB_NAME, uuid))
    except Exception:
        print(f"WARNING: {uuid} not in '{USLCI_DB_NAME}' — skipping.")
        continue

    try:
        if VALIDATION_MODE == "direct":
            ol_norm     = parse_direct_impacts(xlsx_path, uuid)
            bw_raw      = run_all_direct(act, traci_methods)
            kg          = _kg_per_run(act, uuid)
            bw_norm     = {cat: s / kg for cat, s in bw_raw.items()}
            bounds      = KG_PER_RUN_BOUNDS.get(uuid)
            if bounds and not (bounds[0] <= kg <= bounds[1]):
                print(f"  WARNING: derived kg_per_run={kg:.4f} is outside expected "
                      f"range {bounds} — check DENSITY_KG_M3 or the production exchange.")
            basis_label = f"per kg  (kg_per_run derived = {kg:.4f})"
        else:
            # openLCA full-chain exports are standardized to "Amount: 1.0 kg"
            # of the reference product (confirmed per-file via each xlsx's own
            # "Calculation setup" sheet) -- NOT the process's native declared
            # reference amount, which can be a different, arbitrary quantity
            # (e.g. petroleum's own amount is 0.2523 L, not 1 kg). Normalize
            # BW to the same 1 kg basis rather than multiplying by prod_amount.
            kg_per_unit = _kg_per_native_unit(uuid, act.get("unit"))
            ol_norm     = parse_full_chain_impacts(xlsx_path)
            bw_raw      = run_all_full_chain(act, traci_methods)
            bw_norm     = {cat: s / kg_per_unit for cat, s in bw_raw.items()}
            basis_label = f"per kg  (1 {act.get('unit', '?')} = {kg_per_unit:.6g} kg)"
    finally:
        if TEMP_DB_NAME in bd.databases:
            del bd.databases[TEMP_DB_NAME]

    unmatched = set(traci_methods.keys()) - set(ol_norm.keys())
    if unmatched:
        print(f"  WARNING: {len(unmatched)} BW category name(s) have no match in "
              f"the openLCA xlsx — those rows will show '-' instead of a ratio:")
        for cat in sorted(unmatched):
            print(f"    '{cat}'")

    n_bio = sum(1 for e in act.exchanges() if e["type"] == "biosphere")
    print(f"\n{'='*72}")
    print(f"Process : {act['name']}")
    print(f"Mode    : {VALIDATION_MODE}  |  Basis: {basis_label}  |  Direct bio exchanges: {n_bio}")
    print(f"\n  {'Impact category':<40} {'BW':>12} {'OL':>12} {'BW/OL':>8}")
    print(f"  {'-'*40} {'-'*12} {'-'*12} {'-'*8}")

    for category in sorted(traci_methods):
        bw_val = bw_norm.get(category, 0.0)
        ol_val = ol_norm.get(category, None)

        if ol_val is None or (ol_val == 0.0 and bw_val == 0.0):
            ratio_str = "    -"
        elif ol_val == 0.0:
            ratio_str = "  inf"
        else:
            ratio     = bw_val / ol_val
            flag      = " !" if abs(ratio - 1.0) > 0.05 else "   "
            ratio_str = f"{ratio:>7.3f}{flag}"

        ol_str = f"{ol_val:.3e}" if ol_val else "      0.0"
        print(f"  {category:<40} {bw_val:>12.3e} {ol_str:>12} {ratio_str}")

        all_results.append({
            "process":     act["name"],
            "mode":        VALIDATION_MODE,
            "category":    category,
            "bw_score":    bw_val,
            "ol_score":    ol_val,
            "ratio_bw_ol": bw_val / ol_val if ol_val else None,
        })

# =============================================================================
# SAVE
# =============================================================================
if not all_results:
    raise RuntimeError(
        f"No results produced. Check that TARGETS_{'DIRECT' if VALIDATION_MODE == 'direct' else 'FULL'} "
        f"contains at least one valid process UUID and that the xlsx path exists."
    )

out_path = HERE / f"validation_{VALIDATION_MODE}_results.csv"
pd.DataFrame(all_results).to_csv(out_path, index=False)
print(f"\nResults saved → {out_path.name}")
print("Rows marked '!' have BW/OL ratio outside [0.95, 1.05].")
