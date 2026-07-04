#!/usr/bin/env python3
"""
foreground_importer.py
----------------------
Loads and validates a foreground inventory CSV into a structure that
general/04_run_lca.py can use to build brightway activities.

CSV schema (one row per exchange, one file for all foreground processes):

    process_name   : owner process (human label; UUID auto-derived via uuid5)
    exchange_type  : "production" / "technosphere" / "biosphere"
    flow_uuid      : FEDEFL UUID (biosphere) — validated against biosphere-fedefl.
                     For technosphere and production rows this column is for
                     openLCA provenance traceability only; it is NOT validated
                     and NOT used in brightway computation.
    provider_uuid  : USLCI process UUID (background technosphere); uuid5 of
                     producing process name (foreground-to-foreground);
                     empty for biosphere and production rows
    flow_name      : human label only — not used computationally
    amount         : numeric
    unit           : unit string
    is_ref         : "true" on the production exchange, "false" elsewhere
    location       : optional; defaults to "US"
    comment        : optional; ignored at import
"""

import csv
import uuid
from collections import defaultdict
from pathlib import Path

# Stable project-specific namespace for foreground process UUIDs.
FOREGROUND_NS = uuid.UUID("7d3e2a91-4f8c-5b6d-9e0f-1a2b3c4d5e6f")

REQUIRED_COLS = {
    "process_name", "exchange_type", "flow_uuid", "provider_uuid",
    "flow_name", "amount", "unit", "is_ref",
}
VALID_EXCHANGE_TYPES = {"production", "technosphere", "biosphere"}

# Maps unit string (lowercase) to a flow-property group name.
# Used to catch unit/flow-property mismatches on biosphere exchanges
# (e.g. specifying MJ for a flow whose reference unit is kg).
_FLOW_PROPERTY_BY_UNIT = {
    # Mass
    "kg": "mass", "g": "mass", "mg": "mass", "lb": "mass",
    "lb av": "mass", "sh tn": "mass", "t": "mass", "ton": "mass",
    "short ton": "mass", "metric ton": "mass", "tonne": "mass",
    # Volume
    "m3": "volume", "l": "volume", "ml": "volume",
    "gal": "volume", "gal (us liq)": "volume", "gal (us fl)": "volume",
    "cu ft": "volume", "ft3": "volume",
    # Energy
    "mj": "energy", "kj": "energy", "gj": "energy", "kwh": "energy",
    "btu": "energy", "mmbtu": "energy", "mm btu": "energy",
    # Transport
    "t*km": "transport", "tkm": "transport", "t*mi": "transport", "kg*km": "transport",
    # Area
    "m2": "area",
    # Area-time
    "m2*a": "area_time",
    # Volume-time
    "m3*a": "volume_time",
    # Length
    "m": "length", "ft": "length",
    # Radioactivity
    "kbq": "radioactivity", "bq": "radioactivity",
    # Dimensionless / count
    "unit": "count", "item(s)": "count", "p": "count", "items": "count",
    # Currency
    "usd": "currency", "$": "currency", "us$": "currency",
}


def fg_uuid(process_name: str) -> str:
    """Deterministic UUID for a foreground process from its name slug."""
    return str(uuid.uuid5(FOREGROUND_NS, process_name.strip().lower()))


def load_foreground_csv(csv_path, bio_db, uslci_db):
    """
    Parse and validate a foreground inventory CSV.

    Parameters
    ----------
    csv_path : str or Path
    bio_db   : brightway Database object for "biosphere-fedefl"
    uslci_db : brightway Database object for "uslci-subset"

    Returns
    -------
    dict  {process_name: {"uuid": str, "exchanges": [row_dict, ...]}}

    Raises
    ------
    FileNotFoundError  if csv_path does not exist
    ValueError         if any required columns are missing
    ValueError         if any validation errors are found (all collected before raising)
    """
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Foreground CSV not found: {csv_path}")

    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = set(reader.fieldnames or [])
            missing_cols = REQUIRED_COLS - fieldnames
            if missing_cols:
                raise ValueError(f"CSV missing required columns: {sorted(missing_cols)}")
            rows = list(reader)
    except UnicodeDecodeError:
        raise ValueError(
            f"Foreground CSV '{path}' could not be read as UTF-8. "
            "Re-save the file as UTF-8 (in Excel: Save As → CSV UTF-8 (with BOM))."
        )

    if not rows:
        raise ValueError(f"Foreground CSV '{path}' contains no data rows (header only).")

    # Build lookup sets from brightway DBs once (avoids repeated iteration).
    bio_codes       = {act["code"]: act.get("unit", "") for act in bio_db}
    uslci_proc_codes = {act["code"] for act in uslci_db}

    # Group rows by process_name; collect lineno for error messages.
    groups = defaultdict(list)
    errors   = []
    warnings = []
    for i, row in enumerate(rows, start=2):  # row 1 = header
        pname = row["process_name"].strip()
        if not pname:
            errors.append(f"Row {i}: process_name is empty")
            continue
        groups[pname].append((i, row))

    # Pre-build foreground UUID set so cross-process links can be validated
    # before processing each group individually.
    fg_uuids    = {name: fg_uuid(name) for name in groups}
    fg_uuid_set = set(fg_uuids.values())

    processes = {}
    for process_name, group_rows in groups.items():
        proc_uuid = fg_uuids[process_name]

        ref_rows = [(i, r) for i, r in group_rows
                    if r["is_ref"].strip().lower() == "true"]
        ref_count = len(ref_rows)
        if ref_count != 1:
            errors.append(
                f"Process '{process_name}': expected exactly 1 is_ref=true row, "
                f"found {ref_count}"
            )
        elif ref_rows[0][1]["exchange_type"].strip().lower() != "production":
            errors.append(
                f"Process '{process_name}': is_ref=true must be on the production "
                f"exchange, but found it on a "
                f"'{ref_rows[0][1]['exchange_type'].strip()}' row (line {ref_rows[0][0]})"
            )

        parsed_exchanges = []
        for lineno, row in group_rows:
            etype         = row["exchange_type"].strip().lower()
            flow_uuid_val = row["flow_uuid"].strip()
            prov_uuid     = row["provider_uuid"].strip()
            amount_raw    = row["amount"].strip()
            unit          = row["unit"].strip()
            is_ref        = row["is_ref"].strip().lower() == "true"
            location      = row.get("location", "").strip() or "US"
            comment       = row.get("comment", "").strip()

            if etype not in VALID_EXCHANGE_TYPES:
                errors.append(
                    f"Row {lineno}: invalid exchange_type '{etype}' "
                    f"— must be one of {sorted(VALID_EXCHANGE_TYPES)}"
                )
                continue

            try:
                amount = float(amount_raw)
            except ValueError:
                errors.append(f"Row {lineno}: amount '{amount_raw}' is not a valid number")
                amount = None

            if amount == 0.0 and etype != "production":
                warnings.append(
                    f"Row {lineno}: amount is 0 for a {etype} exchange "
                    f"('{row['flow_name'].strip()}') — likely a data entry error"
                )

            if etype == "biosphere":
                if flow_uuid_val not in bio_codes:
                    errors.append(
                        f"Row {lineno}: biosphere flow_uuid '{flow_uuid_val}' "
                        f"not found in biosphere-fedefl"
                    )
                else:
                    # Unit flow-property compatibility check.
                    ref_unit = bio_codes[flow_uuid_val]
                    csv_fp   = _FLOW_PROPERTY_BY_UNIT.get(unit.lower())
                    ref_fp   = _FLOW_PROPERTY_BY_UNIT.get(ref_unit.lower())
                    if csv_fp is not None and ref_fp is not None and csv_fp != ref_fp:
                        errors.append(
                            f"Row {lineno}: unit '{unit}' ({csv_fp}) is incompatible "
                            f"with flow '{flow_uuid_val}' whose reference unit is "
                            f"'{ref_unit}' ({ref_fp})"
                        )
                    elif csv_fp is None or ref_fp is None:
                        unknown = unit if csv_fp is None else ref_unit
                        warnings.append(
                            f"Row {lineno}: unit '{unknown}' is not in the unit "
                            f"compatibility table — flow-property check skipped"
                        )
                if prov_uuid:
                    errors.append(
                        f"Row {lineno}: biosphere exchange must have empty provider_uuid "
                        f"(got '{prov_uuid}')"
                    )

            elif etype == "technosphere":
                if not prov_uuid:
                    errors.append(
                        f"Row {lineno}: technosphere exchange requires a provider_uuid"
                    )
                elif prov_uuid == proc_uuid:
                    errors.append(
                        f"Row {lineno}: technosphere exchange points back to its own "
                        f"process (provider_uuid == process UUID '{proc_uuid}') "
                        f"— circular dependency"
                    )
                elif prov_uuid in uslci_proc_codes:
                    pass  # valid background provider
                elif prov_uuid in fg_uuid_set:
                    pass  # valid foreground-to-foreground link
                else:
                    errors.append(
                        f"Row {lineno}: provider_uuid '{prov_uuid}' not found in "
                        f"uslci-subset or the current foreground batch. "
                        f"If this is a foreground-to-foreground link, check that the "
                        f"provider process_name spelling matches exactly — UUIDs are "
                        f"derived from the lowercased name."
                    )

            elif etype == "production":
                if prov_uuid:
                    errors.append(
                        f"Row {lineno}: production exchange must have empty provider_uuid "
                        f"(got '{prov_uuid}')"
                    )
                if amount is not None and amount <= 0:
                    errors.append(
                        f"Row {lineno}: production exchange amount must be > 0 "
                        f"(got {amount}) — this will cause a singular matrix in brightway"
                    )

            parsed_exchanges.append({
                "exchange_type": etype,
                "flow_uuid":     flow_uuid_val,
                "provider_uuid": prov_uuid,
                "flow_name":     row["flow_name"].strip(),
                "amount":        amount,
                "unit":          unit,
                "is_ref":        is_ref,
                "location":      location,
                "comment":       comment,
            })

        processes[process_name] = {
            "uuid":      proc_uuid,
            "exchanges": parsed_exchanges,
        }

    if errors:
        n = len(errors)
        raise ValueError(
            f"Foreground CSV validation failed — {n} error(s):\n"
            + "\n".join(f"  [{i + 1}] {e}" for i, e in enumerate(errors))
        )

    if warnings:
        for w in warnings:
            print(f"  WARNING: {w}")

    n_bio  = sum(e["exchange_type"] == "biosphere"    for p in processes.values() for e in p["exchanges"])
    n_tech = sum(e["exchange_type"] == "technosphere" for p in processes.values() for e in p["exchanges"])
    n_prod = sum(e["exchange_type"] == "production"   for p in processes.values() for e in p["exchanges"])
    n_ref  = sum(e["is_ref"]                          for p in processes.values() for e in p["exchanges"])
    print(
        f"  Parsed {len(processes)} process(es): {n_bio + n_tech + n_prod} exchanges "
        f"({n_bio} biosphere, {n_tech} technosphere, {n_prod} production, {n_ref} ref)"
    )

    return processes
