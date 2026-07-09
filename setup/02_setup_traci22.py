#!/usr/bin/env python3
"""
02_setup_traci22.py
-------------------
Imports TRACI 2.2 characterization factors into brightway25 via lciafmt.

Creates one brightway Method per TRACI 2.2 impact category, with flows keyed
to the 'biosphere-fedefl' database by FEDEFL UUID (no name-matching needed
since lciafmt and fedelemflowlist share the same UUID space).

Requires:
  - lciafmt  : pip install git+https://github.com/USEPA/LCIAformatter.git
  - setup/01_setup_biosphere_fedefl.py must have been run first

Run once per machine / brightway project.
"""

import hashlib
import importlib.metadata
import os
import sys
from pathlib import Path
import pandas as pd
import bw2data as bd
import lciafmt
import lciafmt.cache as lciafmt_cache
import fedelemflowlist

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import PROJECT_NAME, BIOSPHERE_DB, METHOD_ROOT

# Populated only if a unit conflict is detected at runtime and operator intervention
# is required. e.g. UNIT_OVERRIDES = {"Eutrophication (Marine)": "kg N eq"}
UNIT_OVERRIDES = {}

# TRACI 2.2 eutrophication CFs are spatially differentiated; every other category
# has a single (non-located) CF. This selects which spatial variant to use for
# eutrophication only. See the location-filter block below for the full rationale.
#   ""      -> generic / non-located  (openLCA reference parity — DEFAULT)
#   "00000" -> US national average    (regionalized; DIVERGES from the openLCA reference)
# openLCA matches the (non-located) USLCI inventory flows to the generic CF, so ""
# reproduces its results. Switch to "00000" only for a deliberately US-regionalized
# study, and document the divergence — see DEVLOG "Eutrophication regionalization."
EUTRO_LOCATION = ""

bd.projects.set_current(PROJECT_NAME)
if not bd.projects.twofive:
    bd.projects.migrate_project_25()

if BIOSPHERE_DB not in bd.databases:
    raise RuntimeError(
        f"'{BIOSPHERE_DB}' not found. Run setup/01_setup_biosphere_fedefl.py first."
    )

# Known-good SHA256 of the two upstream TRACI 2.2 CF source files lciafmt fetches.
# These are ENFORCED, not just logged (ledger #4): a changed hash means EPA silently
# updated the source file, which invalidates the locked validation — the build stops
# so results are re-validated before use. Same guard pattern as setup/03b's baseline
# fetch. If an upstream change is intentional, re-run the validation harness and, once
# it still passes, update the pinned hash here.
#
# lciafmt caches the base TRACI file under a fixed internal name ("traci_2.1.xlsx",
# hardcoded in lciafmt/traci.py) that does NOT match method_meta['file']; the eutro
# file is cached under method_meta['eutro_file'].
TRACI_BASE_CACHE_NAME = "traci_2.1.xlsx"
TRACI_BASE_SHA256     = "a1f61b5f3dc6d11de2662335f0af48bec39882da89d5fcbc6bb7c35f1de142fc"
TRACI_EUTRO_SHA256    = "651f08ce6ab39b954fce698e88907ea96a637d53bd219ba1e8b3f3d5555eb82c"

def _file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def _enforce_cf_hashes(method_meta):
    """Verify the downloaded TRACI 2.2 CF source files against the pinned SHA256s and
    STOP the build on any mismatch or missing file. Call AFTER lciafmt.get_method()
    has populated the cache. Mirrors setup/03b's baseline hash guard."""
    expected = {
        TRACI_BASE_CACHE_NAME:        TRACI_BASE_SHA256,
        method_meta["eutro_file"]:    TRACI_EUTRO_SHA256,
    }
    for cache_name, want in expected.items():
        path = lciafmt_cache.get_path(cache_name)
        if not os.path.isfile(path):
            raise RuntimeError(
                f"Expected TRACI CF source '{cache_name}' not found in the lciafmt cache "
                f"({path}).\n  lciafmt may have changed how it downloads or names CF files. "
                f"Re-verify the CF provenance before trusting results."
            )
        got = _file_sha256(path)
        if got != want:
            raise RuntimeError(
                f"TRACI 2.2 CF source file hash mismatch — build STOPPED.\n"
                f"  file:     {cache_name}\n"
                f"  expected: {want}\n"
                f"  got:      {got}\n"
                f"  The upstream EPA CF file changed. The locked validation was computed against "
                f"the expected file, so results may no longer be valid. Re-run the validation "
                f"harness; if the new file is correct and still passes, update the pinned hash "
                f"(TRACI_BASE_SHA256 / TRACI_EUTRO_SHA256) at the top of this script."
            )
    print("  ✓ TRACI CF source files verified against pinned SHA256.")

def _log_cf_provenance(method_meta):
    base_file  = method_meta.get("file", "unknown")
    eutro_file = method_meta.get("eutro_file")
    # lciafmt caches the base file under a fixed internal name, not method_meta['file']
    # (see TRACI_BASE_CACHE_NAME) — look it up there or the hash never prints.
    base_path  = lciafmt_cache.get_path(TRACI_BASE_CACHE_NAME)
    # Record these hashes after each run. A changed hash on a subsequent run means
    # EPA silently updated the source file — re-validate before using new results.
    print(f"\n--- TRACI CF provenance ---")
    print(f"  Method:    {method_meta.get('name')} ({method_meta.get('id')})")
    print(f"  Base file: {base_file}")
    print(f"  Base URL:  {method_meta.get('url')}")
    if os.path.isfile(base_path):
        print(f"  Base SHA256: {_file_sha256(base_path)}")
    else:
        print(f"  Base SHA256: <not yet cached — will download>")
    if eutro_file:
        eutro_path = lciafmt_cache.get_path(eutro_file)
        print(f"  Eutro file: {eutro_file}")
        print(f"  Eutro URL:  {method_meta.get('eutro_url')}")
        if os.path.isfile(eutro_path):
            print(f"  Eutro SHA256: {_file_sha256(eutro_path)}")
        else:
            print(f"  Eutro SHA256: <not yet cached — will download>")
    def _pkg_version(name):
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            return "unknown"
    print(f"  lciafmt version:         {_pkg_version('lciafmt')}")
    print(f"  fedelemflowlist version: {_pkg_version('fedelemflowlist')}")
    print(f"---------------------------\n")

# =============================================================================
# FETCH TRACI 2.2
# =============================================================================
print("Fetching TRACI 2.2 from lciafmt...")
method      = lciafmt.Method.TRACI2_2
method_meta = method.get_metadata()
traci_raw = lciafmt.get_method(method)   # downloads + caches the CF source files
_log_cf_provenance(method_meta)          # now the cached files exist → real hashes print
_enforce_cf_hashes(method_meta)          # STOP the build if either CF file changed (ledger #4)
print(f"  {len(traci_raw)} raw characterization factors retrieved.")

# map_flows() is required — it replaces raw source flow names with FEDEFL UUIDs.
# Without this step, Flow UUID contains source-format IDs that won't match
# the biosphere-fedefl database.
mapping_system = method_meta.get("mapping")
print(f"  Applying FEDEFL flow mappings (system: '{mapping_system}')...")

# First call: preserve unmapped rows so we can identify exactly which flows dropped.
traci_all = lciafmt.map_flows(traci_raw, system=mapping_system, preserve_unmapped=True)
traci     = lciafmt.map_flows(traci_raw, system=mapping_system, preserve_unmapped=False)

n_raw    = len(traci_raw)
n_mapped = len(traci)
dropped  = traci_all[traci_all["Flow UUID"].isna() | (traci_all["Flow UUID"] == "")]
n_dropped = len(dropped)

print(f"  {n_raw} raw factors → {n_mapped} mapped ({n_dropped} dropped at name→UUID step).")

# Full detail available for inspection: DROPPED_FLOWS_DETAIL (run with python -i to inspect)
DROPPED_FLOWS_DETAIL = (dropped.groupby(["Indicator", "Flowable"])
                               .size()
                               .reset_index(name="count")
                               .sort_values(["Indicator", "count"], ascending=[True, False]))
if n_dropped > 0:
    by_category = DROPPED_FLOWS_DETAIL.groupby("Indicator")["count"].sum()
    print(f"  Dropped flows by category (inspect DROPPED_FLOWS_DETAIL for full list):")
    for indicator, count in by_category.items():
        print(f"    {indicator}: {count} factor(s) dropped")

# TRACI 2.2 eutrophication uses spatially-explicit CFs (national + US-state +
# county-level), so each FEDEFL UUID gets many entries. Without filtering, bw2calc
# sums all spatial variants and inflates the characterization matrix massively.
#
# Location semantics (lciafmt output; cross-checked against the lcacommons TRACI
# 2.2 openLCA JSON, category files under lcia_categories/):
#   ""      = generic / non-located CF  (== the openLCA `location: null` factor)
#   "00000" = US national average       (== the openLCA US-country factor)
#   FIPS codes (e.g. "48001") = county-level; country names = international
#
# Non-eutrophication categories have ONLY Location=="" rows (no spatial variants) —
# which is exactly why they already validate 1:1 against openLCA. For eutrophication
# we mirror that: match the non-located USLCI inventory flows to the non-located
# (generic) CF, driven by EUTRO_LOCATION (default ""), for openLCA parity. The
# US-national variant ("00000") is a ~2.775x higher freshwater CF and a ~0.15x
# lower marine CF than generic — real TRACI values, but they DIVERGE from the
# openLCA reference (which uses generic). See DEVLOG "Eutrophication regionalization."
eutro_mask = traci["Indicator"].str.contains("Eutrophication")
traci = traci[
    (~eutro_mask & (traci["Location"] == "")) |
    ( eutro_mask & (traci["Location"] == EUTRO_LOCATION))
]
print(f"  {len(traci)} factors after location filter "
      f"(non-eutro: generic ''; eutro: '{EUTRO_LOCATION}').")
print(f"  Columns: {list(traci.columns)}")
print(f"  Indicators: {sorted(traci['Indicator'].unique())}\n")

cf_col   = "Characterization Factor"
uuid_col = "Flow UUID"

# lciafmt is installed from GitHub master and may rename columns without notice.
# Fail fast here rather than silently writing empty methods downstream.
required_cols = {cf_col, uuid_col, "Indicator", "Location", "Flowable", "Indicator unit"}
missing_cols  = required_cols - set(traci.columns)
if missing_cols:
    raise RuntimeError(
        f"lciafmt output missing expected columns: {missing_cols}\n"
        f"  Actual columns: {list(traci.columns)}"
    )

# Index of all UUIDs in the FEDEFL biosphere database
bio_uuids = {act["code"] for act in bd.Database(BIOSPHERE_DB)}

# =============================================================================
# BUILD BRIGHTWAY METHODS — one per indicator
# =============================================================================
categories = sorted(traci["Indicator"].unique())
print(f"Importing {len(categories)} impact categories...")

total_matched = 0
total_unmatched = 0

for category in categories:
    group = traci[traci["Indicator"] == category].copy()
    method_key = METHOD_ROOT + (category,)

    # Clear any existing version
    m = bd.Method(method_key)
    if method_key in bd.methods:
        m.deregister()

    if "Indicator unit" in group.columns:
        units = group["Indicator unit"].dropna().unique()
        if len(units) > 1:
            if category in UNIT_OVERRIDES:
                unit = UNIT_OVERRIDES[category]
                print(f"  NOTE [{category}]: unit conflict resolved by override → '{unit}'")
            else:
                raise RuntimeError(
                    f"[{category}] Multiple indicator units found: {list(units)}\n"
                    f"  Set UNIT_OVERRIDES[\"{category}\"] at the top of the script to resolve."
                )
        else:
            unit = units[0] if len(units) else "unknown"
    else:
        unit = "unknown"
    m.register(unit=unit, description=f"TRACI 2.2 – {category}")

    cfs_dict = {}   # uuid -> cf; last value wins if duplicates exist
    duplicates = {}  # uuid -> count of extra occurrences
    unmatched = []
    for _, row in group.iterrows():
        uuid = row[uuid_col]
        cf   = row.get(cf_col, None)
        if pd.isna(uuid) or cf is None or pd.isna(cf):
            continue
        if uuid in bio_uuids:
            if uuid in cfs_dict:
                duplicates[uuid] = duplicates.get(uuid, 1) + 1
            cfs_dict[uuid] = float(cf)
        else:
            unmatched.append(row.get("Flowable", uuid))
    cfs = [((BIOSPHERE_DB, uuid), cf) for uuid, cf in cfs_dict.items()]
    if duplicates:
        print(f"  WARNING [{category}]: {len(duplicates)} UUID(s) had duplicate CF entries "
              f"— kept last value. Duplicates: {list(duplicates.keys())[:5]}"
              + (f" and {len(duplicates)-5} more" if len(duplicates) > 5 else ""))

    if not cfs:
        raise RuntimeError(
            f"[{category}] No CFs matched any biosphere UUID — method would be empty. "
            f"Check FEDEFL version alignment between lciafmt and setup/01_setup_biosphere_fedefl.py."
        )
    m.write(cfs)
    total_matched   += len(cfs)
    total_unmatched += len(unmatched)
    total = len(cfs) + len(unmatched)
    pct = 100 * len(unmatched) / total if total > 0 else 0
    match_msg = f"  ✓ {category}: {len(cfs)}/{total} flows matched"
    if unmatched:
        match_msg += f" ({pct:.1f}% unmatched: {', '.join(unmatched[:5])}"
        match_msg += ", ..." if len(unmatched) > 5 else ")"
        if len(unmatched) > 5:
            match_msg += f" and {len(unmatched)-5} more)"
    print(match_msg)

print(f"\nDone. {total_matched} total CFs written across {len(categories)} methods.")
if total_unmatched:
    print(f"  {total_unmatched} flows in TRACI 2.2 had no UUID match in '{BIOSPHERE_DB}'.")
    print("  This is expected for flows not in the FEDEFL preferred list.")

print(f"\nMethods are registered under {METHOD_ROOT}:")
for m in sorted(bd.methods):
    if m[:2] == METHOD_ROOT:
        print(f"  {m}")
