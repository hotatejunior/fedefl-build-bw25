"""setup_traci.py — register TRACI 2.2 characterization factors as a callable step.

The library counterpart of `setup/02_setup_traci22.py`. Creates one brightway
Method per TRACI 2.2 impact category, with flows keyed to the FEDEFL biosphere
database by UUID — no name matching needed, since lciafmt and fedelemflowlist
share a UUID space.

`import_traci()` is the script's body as a function: same checks, same order, same
numbers — progress goes to an optional `log` callable instead of `print`, and what
used to be edit-the-file constants (`EUTRO_LOCATION`, `UNIT_OVERRIDES`) are now
arguments::

    from fedefl_bw25.setup_traci import import_traci
    build = import_traci()
    print(build.total_matched, build.cf_counts)

Unlike the other setup steps this one always rebuilds: it deregisters and rewrites
each method rather than guarding on existence. Methods carry no downstream ids, so
there is nothing for a rebuild to invalidate.

Requires lciafmt (`pip install git+https://github.com/USEPA/LCIAformatter.git`) and
a biosphere database built by `setup_biosphere` first.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import os
from dataclasses import dataclass, field

import pandas as pd
import bw2data as bd
import lciafmt
import lciafmt.cache as lciafmt_cache

from fedefl_bw25.config import PROJECT_NAME, BIOSPHERE_DB, METHOD_ROOT

_NOOP = lambda *a, **k: None          # noqa: E731

# TRACI 2.2 eutrophication CFs are spatially differentiated; every other category
# has a single (non-located) CF. This selects which spatial variant to use for
# eutrophication only. See the location-filter block in import_traci for the full
# rationale.
#   ""      -> generic / non-located  (openLCA reference parity — DEFAULT)
#   "00000" -> US national average    (regionalized; DIVERGES from the openLCA reference)
# openLCA matches the (non-located) USLCI inventory flows to the generic CF, so ""
# reproduces its results. Switch to "00000" only for a deliberately US-regionalized
# study, and document the divergence — see DEVLOG "Eutrophication regionalization."
EUTRO_LOCATION = ""

# Known-good SHA256 of the two upstream TRACI 2.2 CF source files lciafmt fetches.
# These are ENFORCED, not just logged (ledger #4): a changed hash means EPA silently
# updated the source file, which invalidates the locked validation — the build stops
# so results are re-validated before use. Same guard pattern as setup_baseline's
# fetch. If an upstream change is intentional, re-run the validation harness and,
# once it still passes, update the pinned hash here.
#
# lciafmt caches the base TRACI file under a fixed internal name ("traci_2.1.xlsx",
# hardcoded in lciafmt/traci.py) that does NOT match method_meta['file']; the eutro
# file is cached under method_meta['eutro_file'].
TRACI_BASE_CACHE_NAME = "traci_2.1.xlsx"
TRACI_BASE_SHA256     = "a1f61b5f3dc6d11de2662335f0af48bec39882da89d5fcbc6bb7c35f1de142fc"
TRACI_EUTRO_SHA256    = "651f08ce6ab39b954fce698e88907ea96a637d53bd219ba1e8b3f3d5555eb82c"

# lciafmt is installed from GitHub master and may rename columns without notice.
# Fail fast rather than silently writing empty methods downstream.
CF_COLUMN   = "Characterization Factor"
UUID_COLUMN = "Flow UUID"
REQUIRED_COLUMNS = {CF_COLUMN, UUID_COLUMN, "Indicator", "Location", "Flowable",
                    "Indicator unit"}


@dataclass
class TraciBuild:
    """What one import produced."""
    methods: list                  # registered brightway method keys
    cf_counts: dict                # category -> CFs written
    units: dict                    # category -> indicator unit
    total_matched: int
    total_unmatched: int
    dropped_count: int
    dropped_detail: object         # DataFrame: Indicator x Flowable x count
    eutro_location: str
    provenance: dict
    notes: list = field(default_factory=list)


def pkg_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def file_sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def cf_provenance(method_meta) -> dict:
    """Where the CF source files came from and what they hash to.

    Call AFTER `lciafmt.get_method()` has populated the cache, or the hashes read
    as not-yet-cached. lciafmt caches the base file under a fixed internal name,
    not `method_meta['file']` (see TRACI_BASE_CACHE_NAME) — looking it up under the
    wrong name is why the hash used to print as missing.
    """
    base_path = lciafmt_cache.get_path(TRACI_BASE_CACHE_NAME)
    prov = {
        "method": f"{method_meta.get('name')} ({method_meta.get('id')})",
        "base_file": method_meta.get("file", "unknown"),
        "base_url": method_meta.get("url"),
        "base_sha256": file_sha256(base_path) if os.path.isfile(base_path) else None,
        "lciafmt_version": pkg_version("lciafmt"),
        "fedelemflowlist_version": pkg_version("fedelemflowlist"),
    }
    eutro_file = method_meta.get("eutro_file")
    if eutro_file:
        eutro_path = lciafmt_cache.get_path(eutro_file)
        prov["eutro_file"] = eutro_file
        prov["eutro_url"] = method_meta.get("eutro_url")
        prov["eutro_sha256"] = (file_sha256(eutro_path)
                                if os.path.isfile(eutro_path) else None)
    return prov


def enforce_cf_hashes(method_meta, log=_NOOP) -> None:
    """Verify the downloaded TRACI 2.2 CF source files against the pinned SHA256s and
    STOP the build on any mismatch or missing file."""
    expected = {
        TRACI_BASE_CACHE_NAME:     TRACI_BASE_SHA256,
        method_meta["eutro_file"]: TRACI_EUTRO_SHA256,
    }
    for cache_name, want in expected.items():
        path = lciafmt_cache.get_path(cache_name)
        if not os.path.isfile(path):
            raise RuntimeError(
                f"Expected TRACI CF source '{cache_name}' not found in the lciafmt cache "
                f"({path}).\n  lciafmt may have changed how it downloads or names CF files. "
                f"Re-verify the CF provenance before trusting results."
            )
        got = file_sha256(path)
        if got != want:
            raise RuntimeError(
                f"TRACI 2.2 CF source file hash mismatch — build STOPPED.\n"
                f"  file:     {cache_name}\n"
                f"  expected: {want}\n"
                f"  got:      {got}\n"
                f"  The upstream EPA CF file changed. The locked validation was computed against "
                f"the expected file, so results may no longer be valid. Re-run the validation "
                f"harness; if the new file is correct and still passes, update the pinned hash "
                f"(TRACI_BASE_SHA256 / TRACI_EUTRO_SHA256) in fedefl_bw25/setup_traci.py."
            )
    log("  ✓ TRACI CF source files verified against pinned SHA256.")


def import_traci(*, eutro_location=EUTRO_LOCATION, unit_overrides=None,
                 project=PROJECT_NAME, log=_NOOP) -> TraciBuild:
    """Fetch TRACI 2.2 via lciafmt and register one brightway Method per category.

    `unit_overrides` maps a category name to the indicator unit to use when lciafmt
    reports more than one for it (e.g. `{"Eutrophication (Marine)": "kg N eq"}`);
    without an entry, a unit conflict is an error rather than a coin flip.
    """
    unit_overrides = unit_overrides or {}

    bd.projects.set_current(project)
    if not bd.projects.twofive:
        bd.projects.migrate_project_25()

    if BIOSPHERE_DB not in bd.databases:
        raise RuntimeError(
            f"'{BIOSPHERE_DB}' not found. Run setup/01_setup_biosphere_fedefl.py first."
        )

    log("Fetching TRACI 2.2 from lciafmt...")
    method      = lciafmt.Method.TRACI2_2
    method_meta = method.get_metadata()
    traci_raw   = lciafmt.get_method(method)   # downloads + caches the CF source files

    provenance = cf_provenance(method_meta)    # cached files now exist -> real hashes
    log("\n--- TRACI CF provenance ---")
    for key, value in provenance.items():
        log(f"  {key}: {value}")
    log("---------------------------\n")
    enforce_cf_hashes(method_meta, log=log)    # STOP if either CF file changed (ledger #4)
    log(f"  {len(traci_raw)} raw characterization factors retrieved.")

    # map_flows() is required — it replaces raw source flow names with FEDEFL UUIDs.
    # Without this step, Flow UUID contains source-format IDs that won't match the
    # biosphere database.
    mapping_system = method_meta.get("mapping")
    log(f"  Applying FEDEFL flow mappings (system: '{mapping_system}')...")

    # First call preserves unmapped rows so we can identify exactly which dropped.
    traci_all = lciafmt.map_flows(traci_raw, system=mapping_system, preserve_unmapped=True)
    traci     = lciafmt.map_flows(traci_raw, system=mapping_system, preserve_unmapped=False)

    dropped = traci_all[traci_all[UUID_COLUMN].isna() | (traci_all[UUID_COLUMN] == "")]
    log(f"  {len(traci_raw)} raw factors → {len(traci)} mapped "
        f"({len(dropped)} dropped at name→UUID step).")

    dropped_detail = (dropped.groupby(["Indicator", "Flowable"])
                             .size()
                             .reset_index(name="count")
                             .sort_values(["Indicator", "count"], ascending=[True, False]))
    if len(dropped):
        log("  Dropped flows by category (see build.dropped_detail for the full list):")
        for indicator, count in dropped_detail.groupby("Indicator")["count"].sum().items():
            log(f"    {indicator}: {count} factor(s) dropped")

    # TRACI 2.2 eutrophication uses spatially-explicit CFs (national + US-state +
    # county-level), so each FEDEFL UUID gets many entries. Without filtering,
    # bw2calc sums all spatial variants and inflates the characterization matrix
    # massively.
    #
    # Location semantics (lciafmt output; cross-checked against the lcacommons TRACI
    # 2.2 openLCA JSON, category files under lcia_categories/):
    #   ""      = generic / non-located CF  (== the openLCA `location: null` factor)
    #   "00000" = US national average       (== the openLCA US-country factor)
    #   FIPS codes (e.g. "48001") = county-level; country names = international
    #
    # Non-eutrophication categories have ONLY Location=="" rows (no spatial variants)
    # — which is exactly why they already validate 1:1 against openLCA. For
    # eutrophication we mirror that: match the non-located USLCI inventory flows to
    # the non-located (generic) CF, driven by eutro_location (default ""), for
    # openLCA parity. The US-national variant ("00000") is a ~2.775x higher
    # freshwater CF and a ~0.15x lower marine CF than generic — real TRACI values,
    # but they DIVERGE from the openLCA reference (which uses generic). See DEVLOG
    # "Eutrophication regionalization."
    eutro_mask = traci["Indicator"].str.contains("Eutrophication")
    traci = traci[
        (~eutro_mask & (traci["Location"] == "")) |
        ( eutro_mask & (traci["Location"] == eutro_location))
    ]
    log(f"  {len(traci)} factors after location filter "
        f"(non-eutro: generic ''; eutro: '{eutro_location}').")
    log(f"  Indicators: {sorted(traci['Indicator'].unique())}\n")

    missing_cols = REQUIRED_COLUMNS - set(traci.columns)
    if missing_cols:
        raise RuntimeError(
            f"lciafmt output missing expected columns: {missing_cols}\n"
            f"  Actual columns: {list(traci.columns)}"
        )

    # Index of all UUIDs in the FEDEFL biosphere database
    bio_uuids = {act["code"] for act in bd.Database(BIOSPHERE_DB)}

    categories = sorted(traci["Indicator"].unique())
    log(f"Importing {len(categories)} impact categories...")

    methods, cf_counts, units, notes = [], {}, {}, []
    total_matched = total_unmatched = 0

    for category in categories:
        group = traci[traci["Indicator"] == category].copy()
        method_key = METHOD_ROOT + (category,)

        # Clear any existing version
        m = bd.Method(method_key)
        if method_key in bd.methods:
            m.deregister()

        unit = _resolve_unit(category, group, unit_overrides, log)
        m.register(unit=unit, description=f"TRACI 2.2 – {category}")

        cfs_dict = {}    # uuid -> cf; last value wins if duplicates exist
        duplicates = {}  # uuid -> count of extra occurrences
        unmatched = []
        for _, row in group.iterrows():
            uuid = row[UUID_COLUMN]
            cf   = row.get(CF_COLUMN, None)
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
            note = (f"[{category}]: {len(duplicates)} UUID(s) had duplicate CF entries "
                    f"— kept last value. Duplicates: {list(duplicates.keys())[:5]}"
                    + (f" and {len(duplicates) - 5} more" if len(duplicates) > 5 else ""))
            notes.append(note)
            log(f"  WARNING {note}")

        if not cfs:
            raise RuntimeError(
                f"[{category}] No CFs matched any biosphere UUID — method would be empty. "
                f"Check FEDEFL version alignment between lciafmt and "
                f"setup/01_setup_biosphere_fedefl.py."
            )
        m.write(cfs)
        methods.append(method_key)
        cf_counts[category] = len(cfs)
        units[category] = unit
        total_matched   += len(cfs)
        total_unmatched += len(unmatched)

        total = len(cfs) + len(unmatched)
        pct = 100 * len(unmatched) / total if total > 0 else 0
        match_msg = f"  ✓ {category}: {len(cfs)}/{total} flows matched"
        if unmatched:
            match_msg += f" ({pct:.1f}% unmatched: {', '.join(unmatched[:5])}"
            match_msg += ", ..." if len(unmatched) > 5 else ")"
            if len(unmatched) > 5:
                match_msg += f" and {len(unmatched) - 5} more)"
        log(match_msg)

    log(f"\nDone. {total_matched} total CFs written across {len(categories)} methods.")
    if total_unmatched:
        log(f"  {total_unmatched} flows in TRACI 2.2 had no UUID match in '{BIOSPHERE_DB}'.")
        log("  This is expected for flows not in the FEDEFL preferred list.")

    return TraciBuild(
        methods=methods, cf_counts=cf_counts, units=units,
        total_matched=total_matched, total_unmatched=total_unmatched,
        dropped_count=len(dropped), dropped_detail=dropped_detail,
        eutro_location=eutro_location, provenance=provenance, notes=notes,
    )


def _resolve_unit(category, group, unit_overrides, log) -> str:
    if "Indicator unit" not in group.columns:
        return "unknown"
    found = group["Indicator unit"].dropna().unique()
    if len(found) > 1:
        if category not in unit_overrides:
            raise RuntimeError(
                f"[{category}] Multiple indicator units found: {list(found)}\n"
                f"  Pass unit_overrides={{'{category}': ...}} to resolve."
            )
        unit = unit_overrides[category]
        log(f"  NOTE [{category}]: unit conflict resolved by override → '{unit}'")
        return unit
    return found[0] if len(found) else "unknown"
