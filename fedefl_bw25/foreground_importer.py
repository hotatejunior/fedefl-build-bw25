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
    amount         : numeric, expressed in `unit` (converted on load — see below)
    unit           : unit string. LOAD-BEARING: the amount is converted onto the
                     counterpart's reference unit — the biosphere flow's, or the
                     technosphere provider's — so "200 g" of a kg-based provider
                     imports as 0.2 kg, and each conversion is reported. A unit
                     from a different flow property (MJ for a kg flow), or one
                     with no known conversion factor, is a hard error rather
                     than a passthrough. On a production row the unit defines
                     the process's own reference basis and is not converted.
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


def convert_to_ref_unit(amount, unit, ref_unit):
    """Convert `amount` from `unit` into `ref_unit`.

    Returns (converted_amount, error, note). Exactly one of error/note may be set;
    both None means the units already matched and nothing was done.

    An amount is only ever used against its counterpart's REFERENCE unit — the
    biosphere flow's, or the technosphere provider's — so any other unit must be
    converted or refused. Refusing (rather than passing through) follows ledger
    #7: an unconverted unit is a wrong number wearing a plausible one's clothes.
    """
    u, r = (unit or "").strip().lower(), (ref_unit or "").strip().lower()
    if not r or u == r:
        return amount, None, None
    fu, fr = _FLOW_PROPERTY_BY_UNIT.get(u), _FLOW_PROPERTY_BY_UNIT.get(r)
    if fu is not None and fr is not None and fu != fr:
        return amount, (f"unit '{unit}' ({fu}) is incompatible with reference "
                        f"unit '{ref_unit}' ({fr})"), None
    cu, cr = _UNIT_TO_REF.get(u), _UNIT_TO_REF.get(r)
    if cu is None or cr is None:
        unknown = unit if cu is None else ref_unit
        return amount, (f"unit '{unknown}' has no conversion factor, so '{unit}' "
                        f"cannot be converted to the reference unit '{ref_unit}'; "
                        f"express the amount in '{ref_unit}'"), None
    factor = cu / cr
    return (amount * factor if amount is not None else None), None, (
        f"converted {amount} {unit} → {amount * factor:g} {ref_unit} "
        f"(x{factor:g})" if amount is not None else None)

# Factor from each unit to its flow property's reference unit (kg / m3 / MJ /
# t*km / m2 / m2*a / h / kBq / count). Mirrors setup/03's WITHIN_FP so a
# foreground CSV converts exactly the way an imported USLCI exchange does.
#
# This table is what makes the unit column load-bearing rather than decorative.
# Before it existed, an amount was used verbatim against the provider's or
# flow's reference unit, so "200 g" of an input whose reference is kg was read
# as 200 kg -- a silent 1000x error, and the more likely user mistake precisely
# because a flow-property check passes it (g and kg are both mass). The same
# principle as ledger #7: an unconverted unit is a wrong number wearing a
# plausible one's clothes.
_UNIT_TO_REF = {
    # Mass → kg
    "kg": 1.0, "g": 1e-3, "mg": 1e-6, "lb": 0.45359237, "lb av": 0.45359237,
    "sh tn": 907.18474, "t": 1e3, "ton": 907.18474, "short ton": 907.18474,
    "metric ton": 1e3, "tonne": 1e3,
    # Volume → m3
    "m3": 1.0, "l": 1e-3, "ml": 1e-6, "gal": 3.78541e-3,
    "gal (us liq)": 3.78541e-3, "gal (us fl)": 3.78541e-3,
    "cu ft": 0.0283168, "ft3": 0.0283168,
    # Energy → MJ
    "mj": 1.0, "kj": 1e-3, "gj": 1e3, "kwh": 3.6, "btu": 1.05506e-3,
    "mmbtu": 1055.06, "mm btu": 1055.06, "kcal": 4.1868e-3,
    # Transport → t*km
    "t*km": 1.0, "tkm": 1.0, "t*mi": 1.60934, "kg*km": 1e-3,
    # Area → m2 ; area-time → m2*a
    "m2": 1.0, "ft2": 0.09290304, "m2*a": 1.0, "ha*a": 1e4,
    # Duration → h ; radioactivity → kBq
    "h": 1.0, "kbq": 1.0, "bq": 1e-3,
    # Dimensionless
    "unit": 1.0, "item(s)": 1.0, "items": 1.0, "p": 1.0,
}

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
    # Unit as well as code: a technosphere amount is applied against the
    # provider's reference unit, so converting to it requires knowing it.
    uslci_proc_units = {act["code"]: act.get("unit", "") for act in uslci_db}
    uslci_proc_codes = set(uslci_proc_units)

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

    # Reference unit of each foreground process, taken from its own production
    # row, so a foreground-to-foreground link converts on the same rule as a
    # link into USLCI. Keyed by UUID because that is what provider_uuid carries.
    fg_ref_units = {}
    for _name, _rows in groups.items():
        for _lineno, _r in _rows:
            if _r["exchange_type"].strip().lower() == "production":
                fg_ref_units[fg_uuids[_name]] = _r["unit"].strip()
                break

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
                    # Convert onto the flow's reference unit. A compatibility
                    # check alone is not enough: g and kg are both mass, so a
                    # property check passes "1500 g" for a kg flow and the amount
                    # was then used as 1500 kg.
                    ref_unit = bio_codes[flow_uuid_val]
                    amount, err, note = convert_to_ref_unit(amount, unit, ref_unit)
                    if err:
                        errors.append(f"Row {lineno}: {err} (flow '{flow_uuid_val}')")
                    elif note:
                        warnings.append(f"Row {lineno}: {note}")
                        unit = ref_unit
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
                elif prov_uuid in uslci_proc_codes or prov_uuid in fg_uuid_set:
                    # Valid provider (USLCI background, or another foreground
                    # process). The amount is applied against that provider's
                    # reference unit, so convert onto it — this column was
                    # previously decorative, making "200 g" of a kg-based
                    # provider a silent 1000x error.
                    ref_unit = (uslci_proc_units.get(prov_uuid)
                                if prov_uuid in uslci_proc_codes
                                else fg_ref_units.get(prov_uuid, ""))
                    amount, err, note = convert_to_ref_unit(amount, unit, ref_unit)
                    if err:
                        errors.append(
                            f"Row {lineno}: {err} (provider '{prov_uuid}')")
                    elif note:
                        warnings.append(f"Row {lineno}: {note}")
                        unit = ref_unit
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
