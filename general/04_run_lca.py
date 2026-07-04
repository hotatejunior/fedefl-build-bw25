#!/usr/bin/env python3
"""
04_run_lca.py
-------------
Runs LCIA across all 10 TRACI 2.2 categories for either:
  - A USLCI background process (specified by UUID), or
  - A foreground system loaded from a CSV inventory file.

Results are written to a CSV for downstream use by general/06_visualize.py.

Usage
-----
IDE / notebook: edit the CONFIG block below and run directly.

Terminal (from repo root):
  python general/04_run_lca.py [--uuid UUID] [--foreground CSV] [--target-process NAME] [--output CSV]

Requires: setup/01, setup/02, setup/03 to have been run on this machine.
"""

import argparse
import atexit
import csv
import sys
from pathlib import Path

import numpy as np
import bw2data as bd
import bw2calc as bc

from foreground_importer import load_foreground_csv, fg_uuid

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import PROJECT_NAME, BIOSPHERE_DB, USLCI_DB, METHOD_ROOT, REPO_ROOT

# =============================================================================
# CONFIG  — edit here for IDE / notebook use; CLI args override at runtime
# =============================================================================
PROCESS_UUID      = "0aaf1e13-5d80-37f9-b7bb-81a6b8965c71"  # petroleum refining, US
FOREGROUND_CSV    = None   # path to foreground inventory CSV, or None to skip
TARGET_PROCESS    = None   # foreground process_name to use as functional unit
                            # required when CSV has >1 process; ignored without CSV
OUTPUT_CSV        = REPO_ROOT / "lca_results.csv"        # CLI --output resolves against CWD instead
CONTRIBUTIONS_CSV = REPO_ROOT / "lca_contributions.csv"  # per-process scores; set None to skip
SCENARIO_LABEL    = None   # human label for this run; defaults to target process name

FOREGROUND_DB   = "foreground"

# =============================================================================
# CLI
# =============================================================================
parser = argparse.ArgumentParser(
    description="Run LCIA for a USLCI process or foreground system."
)
parser.add_argument("--uuid",              default=None, help="USLCI target process UUID")
parser.add_argument("--foreground",        default=None, help="Path to foreground inventory CSV")
parser.add_argument("--target-process",    default=None, dest="target_process",
                    help="Foreground process_name to use as functional unit")
parser.add_argument("--output",            default=None, help="Output CSV path (default: lca_results.csv at repo root)")
parser.add_argument("--contributions",     default=None, help="Contributions CSV path (default: lca_contributions.csv at repo root)")
parser.add_argument("--scenario",          default=None, help="Scenario label for output CSVs (default: target process name)")
parser.add_argument("--no-contributions",  action="store_true", dest="no_contributions",
                    help="Skip writing the per-process contributions CSV")
args = parser.parse_args()

if args.uuid:
    PROCESS_UUID = args.uuid
if args.foreground:
    FOREGROUND_CSV = args.foreground
if args.target_process:
    TARGET_PROCESS = args.target_process
if args.output:
    OUTPUT_CSV = args.output
if args.contributions:
    CONTRIBUTIONS_CSV = args.contributions
if args.scenario:
    SCENARIO_LABEL = args.scenario
if args.no_contributions:
    CONTRIBUTIONS_CSV = None

if args.foreground is not None and args.uuid is not None:
    print(f"NOTE: --foreground and --uuid both provided — --uuid '{PROCESS_UUID}' will be ignored.")

# =============================================================================
# BRIGHTWAY SETUP
# =============================================================================
bd.projects.set_current(PROJECT_NAME)
if not bd.projects.twofive:
    bd.projects.migrate_project_25()

if BIOSPHERE_DB not in bd.databases:
    raise RuntimeError(f"'{BIOSPHERE_DB}' not found. Run setup/01_setup_biosphere_fedefl.py first.")
if USLCI_DB not in bd.databases:
    raise RuntimeError(f"'{USLCI_DB}' not found. Run setup/03_import_uslci.py first.")

traci_methods = sorted(m for m in bd.methods if m[:2] == METHOD_ROOT)
if not traci_methods:
    raise RuntimeError("No TRACI 2.2 methods found. Run setup/02_setup_traci22.py first.")
if len(traci_methods) != 10:
    raise RuntimeError(
        f"Expected 10 TRACI 2.2 methods, found {len(traci_methods)}. "
        f"Re-run setup/02_setup_traci22.py."
    )

# =============================================================================
# PREFLIGHT SMOKE TEST
# =============================================================================
def _flow_with_cf(bio_db, method, name_fragment, category_fragment):
    """Find a biosphere flow matching name/category that has a non-zero CF."""
    cf_ids = {k for k, _ in bd.Method(method).load()}
    return next(
        (a for a in bio_db
         if name_fragment.lower() in a["name"].lower()
         and category_fragment.lower() in str(a.get("categories", "")).lower()
         and a.id in cf_ids),
        None
    )

def _run_smoke(bio_db, flow, method, expected_min, expected_max, label):
    TEST_DB = "_smoke_tmp"
    key = (TEST_DB, "test")
    if TEST_DB in bd.databases:
        del bd.databases[TEST_DB]
    bd.Database(TEST_DB).write({
        key: {
            "name": "smoke", "unit": "unit", "location": "US",
            "exchanges": [
                {"input": key, "amount": 1.0, "type": "production"},
                {"input": (BIOSPHERE_DB, flow["code"]), "amount": 1.0,
                 "unit": "kg", "type": "biosphere"},
            ],
        }
    })
    try:
        act = bd.get_activity(key)
        lca = bc.LCA({act: 1.0}, method)
        lca.lci()
        lca.lcia()
        score = lca.score
    finally:
        if TEST_DB in bd.databases:
            del bd.databases[TEST_DB]
    ok = expected_min <= score <= expected_max
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {score:.6g} (expected {expected_min}–{expected_max})")
    return ok

print("=== Preflight smoke test ===")
bio_db = bd.Database(BIOSPHERE_DB)
smoke_ok = True

gwp_method    = next((m for m in traci_methods if "warm"   in m[2].lower()), None)
acid_method   = next((m for m in traci_methods if "acid"   in m[2].lower()), None)
marine_method = next((m for m in traci_methods if "marine" in m[2].lower()), None)
if not gwp_method:
    raise RuntimeError("Could not find a 'Global warming' method under TRACI 2.2. Re-run setup/02_setup_traci22.py.")
if not acid_method:
    raise RuntimeError("Could not find an 'Acidification' method under TRACI 2.2. Re-run setup/02_setup_traci22.py.")
if not marine_method:
    raise RuntimeError("Could not find an 'Eutrophication (Marine)' method under TRACI 2.2. Re-run setup/02_setup_traci22.py.")

co2 = _flow_with_cf(bio_db, gwp_method, "carbon dioxide", "air")
if co2 is None:
    raise RuntimeError("No CO2/air flow with a GWP CF found. Re-run setup/02_setup_traci22.py.")
smoke_ok &= _run_smoke(
    bio_db, co2, gwp_method,
    expected_min=0.9, expected_max=1.1,
    label="CO2 → Global warming ≈ 1.0 kg CO2-eq",
)

so2 = _flow_with_cf(bio_db, acid_method, "sulfur dioxide", "air")
if so2 is None:
    raise RuntimeError("No SO2/air flow with an Acidification CF found. Re-run setup/02_setup_traci22.py.")
smoke_ok &= _run_smoke(
    bio_db, so2, acid_method,
    expected_min=1e-4, expected_max=1e4,
    label="SO2 → Acidification > 0",
)

nitrogen = _flow_with_cf(bio_db, marine_method, "nitrogen", "water")
if nitrogen is None:
    raise RuntimeError("No nitrogen/water flow with a Marine Eutrophication CF found. Re-run setup/02_setup_traci22.py.")
smoke_ok &= _run_smoke(
    bio_db, nitrogen, marine_method,
    expected_min=1e-6, expected_max=1e4,
    label="Nitrogen → Eutrophication (Marine) > 0",
)

if not smoke_ok:
    raise RuntimeError(
        "Preflight smoke test failed — biosphere or TRACI CFs are broken.\n"
        "Fix: re-run setup/01_setup_biosphere_fedefl.py then setup/02_setup_traci22.py."
    )
print()

# =============================================================================
# FOREGROUND CSV: LOAD + BUILD BRIGHTWAY DB
# =============================================================================
fg_processes = {}

if FOREGROUND_CSV is not None:
    csv_path = Path(FOREGROUND_CSV)
    print(f"=== Loading foreground CSV: {csv_path} ===")
    fg_processes = load_foreground_csv(csv_path, bio_db, bd.Database(USLCI_DB))
    print(f"  {len(fg_processes)} foreground process(es) loaded.")

    fg_uuid_set = {p["uuid"] for p in fg_processes.values()}

    db_data = {}
    for process_name, proc in fg_processes.items():
        proc_uuid = proc["uuid"]
        key = (FOREGROUND_DB, proc_uuid)
        exchanges = []
        for exc in proc["exchanges"]:
            etype        = exc["exchange_type"]
            flow_uuid    = exc["flow_uuid"]
            prov_uuid    = exc["provider_uuid"]
            amount       = exc["amount"]
            unit         = exc["unit"]

            if etype == "production":
                exchanges.append({
                    "input":  key,
                    "amount": amount,
                    "unit":   unit,
                    "type":   "production",
                })
            elif etype == "biosphere":
                exchanges.append({
                    "input":  (BIOSPHERE_DB, flow_uuid),
                    "amount": amount,
                    "unit":   unit,
                    "type":   "biosphere",
                })
            elif etype == "technosphere":
                if prov_uuid in fg_uuid_set:
                    input_key = (FOREGROUND_DB, prov_uuid)
                else:
                    input_key = (USLCI_DB, prov_uuid)
                exchanges.append({
                    "input":  input_key,
                    "amount": amount,
                    "unit":   unit,
                    "type":   "technosphere",
                })

        ref_exc = next((e for e in proc["exchanges"] if e["is_ref"]), None)
        db_data[key] = {
            "name":      process_name,
            "code":      proc_uuid,
            "location":  ref_exc["location"] if ref_exc else "US",
            "unit":      ref_exc["unit"] if ref_exc else "unit",
            "exchanges": exchanges,
        }

    # FOREGROUND_DB is intentionally transient — rebuilt from CSV on every run
    # and deleted at script exit. The delete-before-write here is not data loss.
    if FOREGROUND_DB in bd.databases:
        del bd.databases[FOREGROUND_DB]
    bd.Database(FOREGROUND_DB).write(db_data)
    # Safety net: guarantee cleanup even if the script exits via an unhandled exception.
    atexit.register(lambda: bd.databases.__delitem__(FOREGROUND_DB)
                    if FOREGROUND_DB in bd.databases else None)
    print(f"  Foreground database '{FOREGROUND_DB}' written.")
    print()

# =============================================================================
# RESOLVE TARGET ACTIVITY
# =============================================================================
if FOREGROUND_CSV is not None:
    if TARGET_PROCESS is not None:
        target_uuid = fg_uuid(TARGET_PROCESS)
        if target_uuid not in {p["uuid"] for p in fg_processes.values()}:
            raise RuntimeError(
                f"--target-process '{TARGET_PROCESS}' not found in foreground CSV. "
                f"Available processes: {list(fg_processes.keys())}"
            )
        target_act = bd.get_activity((FOREGROUND_DB, target_uuid))
    elif len(fg_processes) == 1:
        only_uuid = next(iter(fg_processes.values()))["uuid"]
        target_act = bd.get_activity((FOREGROUND_DB, only_uuid))
    else:
        raise RuntimeError(
            f"Foreground CSV has {len(fg_processes)} processes — specify one with "
            f"--target-process NAME.\nAvailable: {list(fg_processes.keys())}"
        )
else:
    target_act = bd.Database(USLCI_DB).get(PROCESS_UUID)
    if target_act is None:
        raise RuntimeError(
            f"Process UUID '{PROCESS_UUID}' not found in '{USLCI_DB}'. "
            f"Check PROCESS_UUID or re-run setup/03_import_uslci.py."
        )

scenario_label = SCENARIO_LABEL if SCENARIO_LABEL is not None else target_act["name"]

print(f"=== Target: '{target_act['name']}' ===")
print(f"    UUID:     {target_act['code']}")
print(f"    Scenario: {scenario_label}")
print()

# Validate output paths before spending time on LCA.
out_path = Path(OUTPUT_CSV)
try:
    out_path.parent.mkdir(parents=True, exist_ok=True)
except OSError as e:
    raise RuntimeError(f"Cannot create output directory for '{out_path}': {e}")
if out_path.exists():
    print(f"WARNING: '{out_path}' already exists and will be overwritten.")

contrib_path = Path(CONTRIBUTIONS_CSV) if CONTRIBUTIONS_CSV else None
if contrib_path:
    try:
        contrib_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise RuntimeError(f"Cannot create output directory for '{contrib_path}': {e}")
    if contrib_path.exists():
        print(f"WARNING: '{contrib_path}' already exists and will be overwritten.")

# =============================================================================
# LCA ACROSS ALL 10 TRACI 2.2 CATEGORIES
# =============================================================================
print("=== LCIA results ===")
results       = []
contributions = []

method_units = {m: bd.Method(m).metadata.get("unit", "?") for m in traci_methods}

lca = bc.LCA({target_act: 1.0}, traci_methods[0])
lca.lci()

# Build activity name lookup once — db lookups are expensive inside a loop.
act_by_col = {}
if contrib_path:
    for key, col_idx in lca.dicts.activity.items():
        try:
            act_obj = bd.get_activity(key)
            act_by_col[col_idx] = {
                "name": act_obj["name"],
                "code": act_obj["code"],
                "db":   act_obj.key[0],
            }
        except Exception:
            act_by_col[col_idx] = {"name": str(key), "code": str(key), "db": "unknown"}

for method in traci_methods:
    lca.switch_method(method)
    lca.lcia()
    score = lca.score
    unit  = method_units[method]
    results.append({
        "scenario": scenario_label,
        "method":   method[2],
        "score":    score,
        "unit":     unit,
    })
    print(f"  {method[2]:45s}  {score:.6g}  {unit}")

    if contrib_path:
        per_proc = np.asarray(lca.characterized_inventory.sum(axis=0)).flatten()
        for col_idx, contrib in enumerate(per_proc):
            if abs(contrib) < 1e-30:
                continue
            info = act_by_col.get(col_idx, {"name": "unknown", "code": "?", "db": "unknown"})
            contributions.append({
                "scenario":           scenario_label,
                "method":             method[2],
                "process_name":       info["name"],
                "process_uuid":       info["code"],
                "db":                 info["db"],
                "contribution_score": float(contrib),
                "unit":               unit,
            })

if all(r["score"] == 0.0 for r in results):
    print(
        "WARNING: all 10 TRACI scores are 0.0 — this almost certainly indicates a "
        "biosphere UUID mismatch. Re-run setup/01_setup_biosphere_fedefl.py then "
        "setup/02_setup_traci22.py."
    )
print()

# =============================================================================
# WRITE OUTPUT CSV
# =============================================================================
with open(out_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=["scenario", "method", "score", "unit"])
    writer.writeheader()
    writer.writerows(results)
print(f"Results written to: {out_path}")

if contrib_path and contributions:
    with open(contrib_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["scenario", "method", "process_name", "process_uuid",
                        "db", "contribution_score", "unit"],
        )
        writer.writeheader()
        writer.writerows(contributions)
    print(f"Contributions written to: {contrib_path} ({len(contributions)} rows)")

# =============================================================================
# CLEANUP
# =============================================================================
if FOREGROUND_CSV is not None and FOREGROUND_DB in bd.databases:
    del bd.databases[FOREGROUND_DB]
