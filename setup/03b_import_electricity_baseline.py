#!/usr/bin/env python3
"""
03b_import_electricity_baseline.py
-----------------------------------
Injects the US Electricity Baseline into brightway as aggregated background
activities, so downstream LCIA runs include electricity upstream instead of
treating it as a cutoff.

The baseline ships as an openLCA *library* (pre-solved sparse matrices), not
JSON-LD, so setup/03_import_uslci.py's parser can't read it. This script uses
olca_library.py to decode it and mirrors how openLCA itself consumes a mounted
library at calc time: it doesn't re-solve the library's internal network, it
plugs in each needed process's pre-solved cumulative inventory (one M column)
as a single lumped background activity.

Discovery is bundle-driven, not a hardcoded UUID list: scans the USLCI bundle
zip(s) for technosphere exchanges whose flow has no producer inside the
bundle, but which name a `defaultProvider` that IS a process in the library --
exactly the "this input comes from outside the bundle" case. Only those
providers get injected, so the set is whatever a given bundle actually needs,
not a fixed per-dataset list.

Requires: setup/01_setup_biosphere_fedefl.py must have been run first.
Run before setup/03_import_uslci.py so its flow-based relink fallback has something
to resolve against.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import bw2data as bd

from olca_library import OlcaLibrary, read_library

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
from config import PROJECT_NAME, BIOSPHERE_DB, ELECTRICITY_BASELINE_DB as INJECTED_DB_NAME, \
    REPO_ROOT

# Defaults to source_data/ at the repo root; override with the SOURCE_DATA_DIR
# env var to point at your own checkout instead of editing this file.
# DEFAULT_LIBRARY_PATH derives from the same root so the two can't drift apart --
# the version-specific subfolder name is a data version pin, not a machine path,
# and stays hardcoded on purpose.
DEFAULT_BUNDLE_DIR = Path(os.environ.get("SOURCE_DATA_DIR", REPO_ROOT / "source_data"))
DEFAULT_LIBRARY_PATH = DEFAULT_BUNDLE_DIR / "U.S._electricity_baseline_v1.2025-06.0_from_olca"
BUNDLE_GLOB = "????????-????-????-????-????????????_*.zip"

# Canonical upstream copy of the baseline library, version-pinned in the
# filename and hosted on the Federal LCA Commons curation team's GitHub
# ("USLCI Support Content Downloads"). Fetched on demand so a fresh checkout
# doesn't have to source it by hand -- unlike the full USLCI JSON-LD bundles,
# the libraries DO have stable versioned GitHub URLs (see DEVLOG).
#
# BASELINE_SHA256 is the whole-file hash of the exact release artifact GitHub
# serves; a mismatch means upstream re-issued the file and we stop rather than
# silently ingest something different (same guard pattern setup/00 uses for the
# conversion table). NOTE: a locally re-exported copy -- e.g. one zipped
# straight out of openLCA, like DEFAULT_LIBRARY_PATH's `_from_olca` file -- has
# byte-identical *members* but a different *container* hash, so the check is
# only applied to what WE download, never to a file the user placed by hand.
BASELINE_URL = (
    "https://raw.githubusercontent.com/FLCAC-admin/uslci-content/dev/downloads/"
    "U.S._electricity_baseline_v1.2025-06.0.zip"
)
BASELINE_SHA256 = "9fec32a8b5f75560c93d6a34986ee5e1166cd3d1cd257616172a3e041cab2815"
# Where a fetched copy lands. Distinct filename from DEFAULT_LIBRARY_PATH so an
# auto-fetched artifact and a hand-exported one stay distinguishable by name.
FETCH_TARGET = DEFAULT_BUNDLE_DIR / "U.S._electricity_baseline_v1.2025-06.0.zip"


# =============================================================================
# DISCOVERY: which library processes does a given bundle actually need?
# =============================================================================
def find_external_providers(bundle_zip_path: Path) -> dict:
    """Scan one USLCI bundle for technosphere exchanges whose flow is not
    produced by any process in the bundle, but which name a `defaultProvider`
    outside the bundle. Returns {provider_uuid: {flow_uuid, flow_name,
    provider_name, referenced_by}} -- candidates to satisfy from the library.
    """
    with zipfile.ZipFile(bundle_zip_path) as z:
        proc_names = [
            n for n in z.namelist()
            if n.startswith("processes/") and n.endswith(".json")
        ]
        processes = [json.loads(z.read(n)) for n in proc_names]

    bundle_proc_uuids = {p.get("@id") for p in processes}
    produced_flows = {
        exc["flow"]["@id"]
        for p in processes
        for exc in p.get("exchanges", [])
        if exc.get("isQuantitativeReference") and not exc.get("isInput")
        and exc.get("flow", {}).get("@id")
    }

    external: dict[str, dict] = {}
    for proc in processes:
        for exc in proc.get("exchanges", []):
            if not exc.get("isInput"):
                continue
            flow = exc.get("flow", {})
            if flow.get("flowType") not in ("PRODUCT_FLOW", "WASTE_FLOW"):
                continue
            flow_uuid = flow.get("@id")
            if flow_uuid in produced_flows:
                continue  # 03 already links this within the bundle
            provider = exc.get("defaultProvider") or {}
            provider_uuid = provider.get("@id")
            if not provider_uuid or provider_uuid in bundle_proc_uuids:
                continue
            entry = external.setdefault(provider_uuid, {
                "flow_uuid": flow_uuid,
                "flow_name": flow.get("name", "?"),
                "provider_name": provider.get("name", "?"),
                "referenced_by": set(),
            })
            entry["referenced_by"].add(proc.get("@id"))
    return external


# =============================================================================
# INJECTION: library process -> brightway activity dict
# =============================================================================
def build_activities(lib: OlcaLibrary, provider_uuids: list[str], bio_uuids: set) -> tuple[dict, dict]:
    """Build brightway-ready activity dicts for the given library process
    UUIDs. Returns (db_data, drop_log): drop_log maps provider_uuid -> list of
    dropped-row dicts, covering both the library's own non-elementary cutoffs
    (WASTE_FLOW/PRODUCT_FLOW rows with no internal producer) and elementary
    flows absent from biosphere-fedefl (openLCA's "non-FEDEFL" bucket, e.g.
    Steam) -- surfaced for audit, never silently lost.
    """
    db_data = {}
    drop_log: dict[str, list] = {}

    for provider_uuid in provider_uuids:
        proc = lib.processes[lib.process_col(provider_uuid)]
        ref = proc["secondary"]
        flows, dropped = lib.elementary_inventory(provider_uuid)

        exchanges = [{
            "input": (INJECTED_DB_NAME, provider_uuid),
            "amount": 1.0,
            "unit": ref.get("unit", ""),
            "type": "production",
        }]

        for flow_uuid, amount in flows.items():
            if flow_uuid not in bio_uuids:
                dropped.append({
                    "id": flow_uuid, "amount": amount,
                    "reason": "not in biosphere-fedefl (openLCA non-FEDEFL flow)",
                })
                continue
            exchanges.append({
                "input": (BIOSPHERE_DB, flow_uuid),
                "amount": amount,
                "unit": "",
                "type": "biosphere",
            })

        if dropped:
            drop_log[provider_uuid] = dropped

        db_data[(INJECTED_DB_NAME, provider_uuid)] = {
            "name":     proc["primary"].get("name", provider_uuid),
            "code":     provider_uuid,
            "location": proc["primary"].get("type") or "GLO",
            "unit":     ref.get("unit", ""),
            "exchanges": exchanges,
            # Flow UUID this process supplies as its reference product -- lets
            # setup/03_import_uslci.py's flow-based relink fallback find this
            # activity from a bundle exchange's flow @id, the same way
            # flow_to_process works for bundle-internal processes.
            "reference_product_flow_uuid": ref.get("id"),
        }

    return db_data, drop_log


# =============================================================================
# FETCH: source the baseline library from its pinned upstream URL
# =============================================================================
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_baseline(target: Path) -> Path:
    """Download the pinned baseline library to `target` and verify its SHA256.
    Writes to a `.part` temp file first so an interrupted or corrupt fetch never
    leaves a half-written file where the reader would try to consume it; on a
    hash mismatch, deletes the download and aborts -- upstream changed the
    artifact and a human should look before we ingest it.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"Fetching electricity baseline library from:\n  {BASELINE_URL}")
    tmp = target.with_suffix(target.suffix + ".part")
    try:
        with urllib.request.urlopen(BASELINE_URL, timeout=120) as resp, \
                open(tmp, "wb") as out:
            shutil.copyfileobj(resp, out)
    except urllib.error.URLError as e:
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"Download failed: {e}")

    got = _sha256(tmp)
    if got != BASELINE_SHA256:
        tmp.unlink(missing_ok=True)
        raise SystemExit(
            "Downloaded baseline failed hash check -- upstream artifact changed.\n"
            f"  expected {BASELINE_SHA256}\n"
            f"  got      {got}\n"
            "Refusing to ingest. If this is an intentional upstream re-issue, "
            "verify the new file and update BASELINE_SHA256."
        )
    tmp.replace(target)
    print(f"  verified SHA256, saved {target.stat().st_size:,} bytes -> {target}")
    return target


def resolve_library(explicit: Path | None, fetch: bool) -> Path:
    """Pick the library zip to read. An explicit --library always wins (and must
    exist). Otherwise prefer an existing hand-placed default, then an existing
    fetched copy; if neither is present, fetch it (unless --no-fetch).
    """
    if explicit is not None:
        if not explicit.exists():
            raise SystemExit(f"Library not found: {explicit}")
        return explicit
    if DEFAULT_LIBRARY_PATH.exists():
        return DEFAULT_LIBRARY_PATH
    if FETCH_TARGET.exists():
        return FETCH_TARGET
    if not fetch:
        raise SystemExit(
            f"Baseline library not found (looked for '{DEFAULT_LIBRARY_PATH.name}' "
            f"and '{FETCH_TARGET.name}' in {DEFAULT_BUNDLE_DIR}).\n"
            "Drop the file there by hand, or omit --no-fetch to download it "
            "automatically."
        )
    return fetch_baseline(FETCH_TARGET)


# =============================================================================
# MAIN
# =============================================================================
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--library", type=Path, default=None,
                     help="path to the openLCA electricity baseline library zip "
                          "(default: auto-detect in source_data, else fetch)")
    ap.add_argument("--no-fetch", action="store_true",
                     help="don't download the baseline if it's missing locally")
    ap.add_argument("--bundle-dir", type=Path, default=DEFAULT_BUNDLE_DIR,
                     help="directory to glob USLCI bundle zips from")
    args = ap.parse_args()

    bd.projects.set_current(PROJECT_NAME)
    if not bd.projects.twofive:
        bd.projects.migrate_project_25()

    if BIOSPHERE_DB not in bd.databases:
        raise RuntimeError("Run setup/01_setup_biosphere_fedefl.py first.")

    library = resolve_library(args.library, fetch=not args.no_fetch)

    bundle_zips = sorted(args.bundle_dir.glob(BUNDLE_GLOB))
    if not bundle_zips:
        raise SystemExit(f"No bundle zips found in {args.bundle_dir}")

    print(f"Scanning {len(bundle_zips)} bundle(s) in {args.bundle_dir} for external providers...")
    external: dict[str, dict] = {}
    for bpath in bundle_zips:
        found = find_external_providers(bpath)
        print(f"  {bpath.name}: {len(found)} external provider(s) referenced")
        for uid, info in found.items():
            entry = external.setdefault(uid, {**info, "referenced_by": set()})
            entry["referenced_by"] |= info["referenced_by"]

    print(f"\n{len(external)} distinct external provider(s) referenced across all bundles.")

    lib = read_library(library)
    resolvable = [uid for uid in external if uid in lib._proc_by_uuid]
    unresolvable = [uid for uid in external if uid not in lib._proc_by_uuid]

    if unresolvable:
        print(f"\nWARNING: {len(unresolvable)} referenced provider(s) NOT found in library "
              f"'{lib.name}' -- these will remain cutoffs:")
        for uid in unresolvable:
            info = external[uid]
            print(f"  {uid}  {info['provider_name']!r}  (flow: {info['flow_name']!r}, "
                  f"referenced by {len(info['referenced_by'])} process(es))")

    if not resolvable:
        raise SystemExit("No referenced providers resolve against the library — nothing to inject.")

    print(f"\nInjecting {len(resolvable)} process(es) from the library as background activities:")
    for uid in resolvable:
        info = external[uid]
        print(f"  {uid}  {info['provider_name']!r}  (referenced by {len(info['referenced_by'])} process(es))")

    bio_uuids = {act["code"] for act in bd.Database(BIOSPHERE_DB)}
    db_data, drop_log = build_activities(lib, resolvable, bio_uuids)

    total_exch = sum(len(a["exchanges"]) - 1 for a in db_data.values())
    total_dropped = sum(len(v) for v in drop_log.values())
    print(f"\nBuilt {len(db_data)} activities, {total_exch} biosphere exchanges total, "
          f"{total_dropped} row(s) dropped (library-side cutoffs / non-FEDEFL flows).")
    if drop_log:
        sample_uid = next(iter(drop_log))
        print(f"  Sample ({sample_uid}): {[d.get('id', d.get('reason')) for d in drop_log[sample_uid][:3]]}")

    if INJECTED_DB_NAME in bd.databases:
        response = input(f"\nDatabase '{INJECTED_DB_NAME}' already exists. Delete and rebuild? [y/N] ")
        if response.strip().lower() != "y":
            raise SystemExit("Aborted — database not modified.")
        del bd.databases[INJECTED_DB_NAME]

    bd.Database(INJECTED_DB_NAME).write(db_data)
    print(f"\nDatabase '{INJECTED_DB_NAME}' written — {len(db_data)} processes.")


if __name__ == "__main__":
    main()
