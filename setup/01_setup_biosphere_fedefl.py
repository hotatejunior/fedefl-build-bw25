#!/usr/bin/env python3
"""
01_setup_biosphere_fedefl.py
----------------------------
Creates a brightway biosphere database from the Federal Elementary Flow List (FEDEFL).

This replaces the default ecoinvent biosphere3 database for EPA-based LCA work.
All flows use FEDEFL UUIDs, which align with TRACI 2.2 (via lciafmt) and USLCI.

Run once per machine / brightway project.
"""

import importlib.metadata
import sys
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import bw2data as bd
import fedelemflowlist as fedefl

from fedefl_bw25.config import PROJECT_NAME, BIOSPHERE_DB


def _pkg_version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"

bd.projects.set_current(PROJECT_NAME)
if not bd.projects.twofive:
    bd.projects.migrate_project_25()

# Guard: don't overwrite an existing database silently
if BIOSPHERE_DB in bd.databases:
    meta = bd.databases[BIOSPHERE_DB]
    built_on      = meta.get("fedefl_built_on",      "unknown (pre-versioning)")
    built_version = meta.get("fedefl_version",        "unknown (pre-versioning)")
    current_version = _pkg_version("fedelemflowlist")
    stale = (built_version != current_version and built_version != "unknown (pre-versioning)")
    print(f"'{BIOSPHERE_DB}' already exists.")
    print(f"  Built on:         {built_on}")
    print(f"  Built with:       fedelemflowlist {built_version}")
    print(f"  Currently installed: fedelemflowlist {current_version}")
    if stale:
        print(f"  *** VERSION MISMATCH — consider rebuilding if FEDEFL flows changed ***")
    else:
        print(f"  Version matches installed package — DB appears current.")
    print(f"To rebuild, first run:")
    print(f"  import bw2data as bd; bd.projects.set_current('{PROJECT_NAME}')")
    print(f"  del bd.databases['{BIOSPHERE_DB}']")
    raise SystemExit

# =============================================================================
# FETCH FLOWS
# =============================================================================
print("Fetching FEDEFL flows...")
flows = fedefl.get_flows()
print(f"  {len(flows)} total flows retrieved.")
print(f"  Columns: {list(flows.columns)}")

# Fail fast if fedelemflowlist renames expected columns — same risk as lciafmt in 02.
# Context and CAS No use .get() in the loop and would silently default to empty string
# if renamed, producing wrong categories/metadata for every flow without any error.
required_cols = {"Flow UUID", "Flowable", "Unit", "Context", "Class", "CAS No"}
missing_cols  = required_cols - set(flows.columns)
if missing_cols:
    raise RuntimeError(
        f"fedelemflowlist output missing expected columns: {missing_cols}\n"
        f"  Actual columns: {list(flows.columns)}"
    )

if flows.empty:
    raise RuntimeError(
        "fedelemflowlist returned zero flows. This likely indicates a package "
        "installation or caching failure — reinstall fedelemflowlist and retry."
    )
FEDEFL_MIN_FLOWS = 298_919  # 90% of 332,133 (v1.3.0 baseline); update if FEDEFL grows significantly
if len(flows) < FEDEFL_MIN_FLOWS:
    raise RuntimeError(
        f"fedelemflowlist returned only {len(flows)} flows (expected ≥{FEDEFL_MIN_FLOWS}). "
        f"Partial data would produce a biosphere DB with incomplete UUID coverage. "
        f"Reinstall fedelemflowlist and retry."
    )

# Check for duplicate UUIDs before building the database.
# Identical duplicates are harmless (dedup silently); conflicting duplicates mean two
# different substances share a UUID — a FEDEFL data error that would silently corrupt
# whichever flow gets written last.
_key_cols = ["Flowable", "Unit", "Context", "Class", "CAS No"]
_key_cols_present = [c for c in _key_cols if c in flows.columns]
dup_uuids = flows[flows.duplicated("Flow UUID", keep=False)]
if not dup_uuids.empty:
    conflicts = []
    for uuid, group in dup_uuids.groupby("Flow UUID"):
        if group[_key_cols_present].drop_duplicates().shape[0] > 1:
            conflicts.append((uuid, group[_key_cols_present].to_dict("records")))
    if conflicts:
        msg = f"  {len(conflicts)} UUID(s) map to conflicting flow definitions:\n"
        for uuid, rows in conflicts[:5]:
            msg += f"    {uuid}: {rows}\n"
        if len(conflicts) > 5:
            msg += f"    ... and {len(conflicts) - 5} more\n"
        raise RuntimeError(
            "FEDEFL contains duplicate UUIDs with conflicting substance data.\n"
            + msg
            + "\nRemediation:\n"
            + "  1. File a bug at https://github.com/USEPA/Federal-LCA-Commons-Elementary-Flow-List/issues\n"
            + "     Include the UUID(s) and conflicting rows printed above.\n"
            + "  2. Until fixed, pin to the last known-good version:\n"
            + "     pip install fedelemflowlist==<last-good-version>\n"
            + "     Check the package changelog to identify when the conflict was introduced.\n"
        )
    n_dup = dup_uuids["Flow UUID"].nunique()
    flows = flows.drop_duplicates("Flow UUID")
    print(f"  {n_dup} UUID(s) had identical duplicate rows — kept one each.")
print()


def parse_context(ctx):
    """'air/urban air close to ground' → ('air', 'urban air close to ground')"""
    if pd.isna(ctx) or str(ctx).strip() == "":
        return ("unspecified",)
    return tuple(p.strip() for p in str(ctx).split("/") if p.strip())


def flow_type(row):
    """Classify as emission or natural resource based on context/class."""
    ctx = str(row.get("Context", "")).lower()
    cls = str(row.get("Class", "")).lower()
    if ctx.startswith("resource") or "resource" in cls:
        return "natural resource"
    return "emission"


# =============================================================================
# BUILD DATABASE
# =============================================================================
db_data = {}
for _, row in flows.iterrows():
    uuid = row["Flow UUID"]
    db_data[(BIOSPHERE_DB, uuid)] = {
        "name":       row["Flowable"],
        "code":       uuid,
        "unit":       row["Unit"],
        "categories": parse_context(row.get("Context", "")),
        "type":       flow_type(row),
        "CAS number": str(row.get("CAS No", "")),
    }

type_counts = {}
for v in db_data.values():
    type_counts[v["type"]] = type_counts.get(v["type"], 0) + 1
# Note these counts after first run — a shift in natural resource count on a later
# rebuild indicates FEDEFL reclassified flows. Future improvement: persist counts
# in db metadata and auto-compare in the overwrite guard.
print(f"  Flow types: {', '.join(f'{k}={v}' for k, v in sorted(type_counts.items()))}")

# Note the unit strings after first run — a new or renamed unit here will cause a
# silent unit conversion failure in setup/03_import_uslci.py, not here.
unique_units = sorted({v["unit"] for v in db_data.values()})
print(f"  Unit strings ({len(unique_units)}): {', '.join(unique_units)}")

fedefl_version = _pkg_version("fedelemflowlist")
print(f"Writing '{BIOSPHERE_DB}' ({len(db_data)} flows) to project '{PROJECT_NAME}'...")
print(f"  fedelemflowlist version: {fedefl_version}")
db = bd.Database(BIOSPHERE_DB)
db.write(db_data)
meta = bd.databases[BIOSPHERE_DB]
meta["fedefl_version"]  = fedefl_version
meta["fedefl_built_on"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
bd.databases[BIOSPHERE_DB] = meta
print("Done.\n")

# Quick sanity check
sample = list(bd.Database(BIOSPHERE_DB))[:3]
print("Sample flows:")
for act in sample:
    print(f"  {act['name']} | {act['categories']} | {act['unit']} | {act['code']}")
