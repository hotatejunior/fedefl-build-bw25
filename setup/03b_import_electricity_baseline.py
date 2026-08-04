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

Discovery is source-driven, not a hardcoded UUID list: scans the USLCI process
sources for technosphere exchanges whose flow has no producer inside the
source, but which name a `defaultProvider` that IS a process in the library --
exactly the "this input comes from outside the source" case. Only those
providers get injected, so the set is whatever the build actually needs, not a
fixed per-dataset list.

"Sources" means the per-process bundle zips *and* the full USLCI zip, when
present. Scanning both is deliberate: `electricity-baseline` is a single
database shared by the bundle build and the full-database build
(USLCI_FULL_DB=1), so injecting the union is the only way one baseline can
serve both. Scoping discovery to whichever build was last imported would make
the baseline order-dependent -- exactly the defect this fixes, where 03b
injected the 11 providers the bundles referenced while 03's full-DB build
needed 17, leaving 42 electric-transport processes unable to resolve their grid
and scoring a silent zero.

The full zip contributes providers only, never a *vintage* verdict: it is a
2025-grid artifact, so letting it vote would pin auto-detection to 2025 and
break the 2026 build. Vintage is decided by the bundles alone, as before.

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
from vintage_detect import classify_bundle, decide_vintage, format_conflict

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
BUNDLE_GLOB = "????????-????-????-????-????????????_*.zip"
# The full USLCI zip is named in the conversion table's _meta, the same single
# source of truth setup/03 reads to find it in FULL_DB_MODE. Resolving it here
# from a second rule (a glob, a hardcoded filename) is how the two scripts would
# drift apart again.
_CONV_TABLE_PATH = HERE / "uslci_flow_conversions.json"


def find_full_db_zip(bundle_dir: Path) -> Path | None:
    """The full USLCI zip in `bundle_dir`, or None if it isn't there.

    Absence is normal and not an error: the bundle-only workflow never needs it,
    and a peer replicating the locked validation may not have downloaded it.
    """
    try:
        meta = json.loads(_CONV_TABLE_PATH.read_text()).get("_meta", {})
    except (OSError, ValueError):
        return None
    name = meta.get("source_zip")
    if not name:
        return None
    path = bundle_dir / name
    return path if path.exists() else None

# -----------------------------------------------------------------------------
# Per-vintage baseline library config.
# -----------------------------------------------------------------------------
# The pipeline injects exactly ONE electricity-baseline vintage per build. The
# US-average node is named identically across releases ("Electricity; at user;
# consumption mix - US - US") but carries a DIFFERENT UUID each vintage
# (7068192a in 2025-06, 75d4be66 in 2026-06), so injecting two at once would
# make name-based provider resolution ambiguous AND would flip the locked
# cases' stray 2026 references -- see DEVLOG / the vintage-UUID note.
#
# Match the vintage to the reference export you diff against: the 4 locked
# validation cases were exported against 2025-06; the newer HDPE/PET bundles
# hardcode the 2026-06 US-average provider UUID and must be built + validated
# against 2026-06.
#
# Per entry:
#   hand_placed  -- a copy dropped in source_data. May be an openLCA re-export
#                   whose *container* hash differs from the release artifact
#                   (byte-identical members, different zip), so it is NOT hash-
#                   checked; only what WE download is verified.
#   fetch_target -- where an auto-download lands.
#   url / sha256 -- canonical release artifact on the Federal LCA Commons GitHub
#                   and its whole-file hash (checked on download only).
#   grid_uuid    -- that release's US-average grid process ("Electricity; at
#                   user; consumption mix - US - US"). Same name every release,
#                   different UUID -- which is what makes it the vintage marker
#                   the bundles are auto-classified by (see vintage_detect).
VINTAGES = {
    "2025": {
        "hand_placed":  DEFAULT_BUNDLE_DIR / "U.S._electricity_baseline_v1.2025-06.0_from_olca",
        "fetch_target": DEFAULT_BUNDLE_DIR / "U.S._electricity_baseline_v1.2025-06.0.zip",
        "url": "https://raw.githubusercontent.com/FLCAC-admin/uslci-content/dev/"
               "downloads/U.S._electricity_baseline_v1.2025-06.0.zip",
        "sha256": "9fec32a8b5f75560c93d6a34986ee5e1166cd3d1cd257616172a3e041cab2815",
        "grid_uuid": "7068192a-999c-39b6-bf66-234a294bdf92",
    },
    "2026": {
        "hand_placed":  DEFAULT_BUNDLE_DIR / "U.S._electricity_baseline_v1.2026-06.0.zip",
        "fetch_target": DEFAULT_BUNDLE_DIR / "U.S._electricity_baseline_v1.2026-06.0.zip",
        "url": "https://raw.githubusercontent.com/FLCAC-admin/uslci-content/dev/"
               "downloads/U.S._electricity_baseline_v1.2026-06.0.zip",
        "sha256": "fb545416220e6b3739496661f623081f6fd96de4b1c6508dd6353d88c2b33143",
        "grid_uuid": "75d4be66-12a7-30b3-bc57-fa724c941b0e",
    },
}
DEFAULT_VINTAGE = "2025"   # only used when no bundle names a grid node at all
GRID_UUIDS = {v: c["grid_uuid"] for v, c in VINTAGES.items()}


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


def fetch_baseline(cfg: dict) -> Path:
    """Download the vintage's pinned baseline library and verify its SHA256.
    Writes to a `.part` temp file first so an interrupted or corrupt fetch never
    leaves a half-written file where the reader would try to consume it; on a
    hash mismatch, deletes the download and aborts -- upstream changed the
    artifact and a human should look before we ingest it.
    """
    target, url, expected = cfg["fetch_target"], cfg["url"], cfg["sha256"]
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"Fetching electricity baseline library from:\n  {url}")
    tmp = target.with_suffix(target.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=120) as resp, \
                open(tmp, "wb") as out:
            shutil.copyfileobj(resp, out)
    except urllib.error.URLError as e:
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"Download failed: {e}")

    got = _sha256(tmp)
    if got != expected:
        tmp.unlink(missing_ok=True)
        raise SystemExit(
            "Downloaded baseline failed hash check -- upstream artifact changed.\n"
            f"  expected {expected}\n"
            f"  got      {got}\n"
            "Refusing to ingest. If this is an intentional upstream re-issue, "
            "verify the new file and update the vintage's sha256 in VINTAGES."
        )
    tmp.replace(target)
    print(f"  verified SHA256, saved {target.stat().st_size:,} bytes -> {target}")
    return target


def resolve_library(explicit: Path | None, fetch: bool, cfg: dict) -> Path:
    """Pick the library zip to read for the selected vintage. An explicit
    --library always wins (and must exist). Otherwise prefer an existing
    hand-placed copy, then an existing fetched copy; if neither is present,
    fetch it (unless --no-fetch).
    """
    if explicit is not None:
        if not explicit.exists():
            raise SystemExit(f"Library not found: {explicit}")
        return explicit
    if cfg["hand_placed"].exists():
        return cfg["hand_placed"]
    if cfg["fetch_target"].exists():
        return cfg["fetch_target"]
    if not fetch:
        raise SystemExit(
            f"Baseline library not found (looked for '{cfg['hand_placed'].name}' "
            f"and '{cfg['fetch_target'].name}' in {DEFAULT_BUNDLE_DIR}).\n"
            "Drop the file there by hand, or omit --no-fetch to download it "
            "automatically."
        )
    return fetch_baseline(cfg)


# =============================================================================
# MAIN
# =============================================================================
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--vintage", choices=sorted(VINTAGES), default=None,
                     help="electricity-baseline vintage to inject. Default: "
                          "auto-detected from the bundles' own grid references "
                          "(the 4 locked cases resolve to 2025; the HDPE/PET and "
                          "2026-drop bundles to 2026). One vintage per build; pass "
                          "this explicitly to override detection or to break a "
                          "mixed-bundle tie.")
    ap.add_argument("--library", type=Path, default=None,
                     help="explicit path to a baseline library zip, overriding the "
                          "vintage's auto-detect/fetch (still stamped with the "
                          "selected vintage)")
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

    bundle_zips = sorted(args.bundle_dir.glob(BUNDLE_GLOB))
    if not bundle_zips:
        raise SystemExit(f"No bundle zips found in {args.bundle_dir}")

    # Scan first: the same pass that finds providers to inject also tells us
    # which baseline vintage the bundles were built against.
    print(f"Scanning {len(bundle_zips)} bundle(s) in {args.bundle_dir} for external providers...")
    external: dict[str, dict] = {}
    verdicts: dict[str, dict] = {}
    for bpath in bundle_zips:
        found = find_external_providers(bpath)
        verdicts[bpath.name] = classify_bundle(found, GRID_UUIDS)
        print(f"  {bpath.name}: {len(found)} external provider(s) referenced"
              f"  [grid vintage: {verdicts[bpath.name]['vintage'] or 'n/a'}]")
        for uid, info in found.items():
            entry = external.setdefault(uid, {**info, "referenced_by": set()})
            entry["referenced_by"] |= info["referenced_by"]

    # Then the full USLCI zip, if present, so one baseline serves the full-database
    # build too (see module docstring). Providers only -- no classify_bundle call,
    # so it cannot vote on vintage.
    full_only: set[str] = set()
    full_zip = find_full_db_zip(args.bundle_dir)
    if full_zip is not None:
        found = find_external_providers(full_zip)
        full_only = set(found) - set(external)
        print(f"  {full_zip.name}: {len(found)} external provider(s) referenced"
              f"  [{len(full_only)} not referenced by any bundle; vintage vote: excluded]")
        for uid, info in found.items():
            entry = external.setdefault(uid, {**info, "referenced_by": set()})
            entry["referenced_by"] |= info["referenced_by"]
    else:
        print("  (full USLCI zip not present — injecting for the bundle build only; "
              "a USLCI_FULL_DB=1 import may leave grid links unresolved)")

    decision = decide_vintage(verdicts, explicit=args.vintage, default=DEFAULT_VINTAGE)
    if decision["conflict"]:
        raise SystemExit("\n" + format_conflict(decision))

    vintage = decision["vintage"]
    cfg = VINTAGES[vintage]
    if decision["source"] == "detected":
        print(f"\nAuto-detected electricity-baseline vintage: {vintage} "
              f"(from {sum(len(b) for b in decision['groups'].values())} bundle(s); "
              f"override with --vintage)")
    elif decision["source"] == "default":
        print(f"\nNo bundle references a baseline grid node — falling back to "
              f"the default vintage {vintage}.")
    else:
        detected = [v for v in decision["groups"] if v != vintage]
        print(f"\nElectricity-baseline vintage: {vintage} (set explicitly)")
        if detected:
            print(f"  NOTE: bundle references also point at {', '.join(sorted(detected))} — "
                  f"those bundles will not link to this build's grid.")

    library = resolve_library(args.library, fetch=not args.no_fetch, cfg=cfg)
    print(f"Using library: {library.name}")

    print(f"\n{len(external)} distinct external provider(s) referenced across all sources.")

    lib = read_library(library)
    resolvable = [uid for uid in external if uid in lib._proc_by_uuid]
    unresolvable = [uid for uid in external if uid not in lib._proc_by_uuid]

    # Split the report by who needs the provider. A full-DB-only provider missing
    # from a 2026 library is expected -- the full zip is a 2025 artifact, so its
    # grid UUIDs simply don't exist in a 2026 build -- and must not read as damage
    # to the bundle build, which is what the operator is usually looking at.
    if unresolvable:
        bundle_unres = [u for u in unresolvable if u not in full_only]
        full_unres   = [u for u in unresolvable if u in full_only]
        if bundle_unres:
            print(f"\nWARNING: {len(bundle_unres)} bundle-referenced provider(s) NOT found in "
                  f"library '{lib.name}' -- these will remain cutoffs:")
            for uid in bundle_unres:
                info = external[uid]
                print(f"  {uid}  {info['provider_name']!r}  (flow: {info['flow_name']!r}, "
                      f"referenced by {len(info['referenced_by'])} process(es))")
        if full_unres:
            print(f"\nNOTE: {len(full_unres)} provider(s) referenced only by the full USLCI zip "
                  f"are absent from library '{lib.name}' (expected on a vintage the full zip "
                  f"predates). The bundle build is unaffected; a USLCI_FULL_DB=1 import on this "
                  f"vintage will cut those grid links.")

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

    # Stamp the injected vintage onto the database metadata. setup/03 copies this
    # onto 'uslci-subset' and the validation harness asserts against it, so a
    # case can never be silently diffed against the wrong-vintage grid (a build
    # is single-vintage; mixing 2025 cases with a 2026 grid is the footgun).
    bd.databases[INJECTED_DB_NAME]["electricity_vintage"] = vintage
    bd.databases[INJECTED_DB_NAME]["electricity_library"] = lib.name
    bd.databases.flush()

    print(f"\nDatabase '{INJECTED_DB_NAME}' written — {len(db_data)} processes "
          f"(electricity_vintage = {vintage}).")


if __name__ == "__main__":
    main()
