"""setup_baseline.py — inject the US Electricity Baseline as callable steps.

The library counterpart of `setup/03b_import_electricity_baseline.py`. The baseline
ships as an openLCA *library* (pre-solved sparse matrices) rather than JSON-LD, so
`setup/03`'s parser cannot read it; this module decodes it via `olca_library` and
mirrors how openLCA consumes a mounted library at calculation time — it does not
re-solve the library's internal network, it plugs each needed process's pre-solved
cumulative inventory in as one lumped background activity.

Discovery is source-driven, not a hardcoded UUID list: it scans the USLCI process
sources for technosphere exchanges whose flow has no producer inside the source but
which name a `defaultProvider` that IS a process in the library. "Sources" means the
per-process bundle zips *and* the full USLCI zip when present, because
`electricity-baseline` is a single database shared by both USLCI builds — injecting
the union is the only way one baseline serves both, and scoping discovery to
whichever build ran last would make the baseline order-dependent.

Vintage is decided by the bundles whenever any of them references a grid node:
they are the study data, the full zip is background. The full zip votes only when
no bundle does, which is the whole-database workflow — one download, no bundles.
That rule replaced an unconditional exclusion on 2026-08-05, whose stated reason
(the full zip "is a 2025-grid artifact") stopped being true when USLCI shipped
v1.2026-06.0: its 359 grid references are now all 2026, and excluding them left a
bundle-less build silently taking the 2025 default.

Like `run`, this module prints nothing and prompts for nothing. Progress goes to an
optional `log` callable, and overwriting an existing database requires either
`overwrite=True` or a `confirm` callback — so the CLI owns the prompt and a scripted
build never blocks on stdin::

    from fedefl_bw25.setup_baseline import inject_baseline
    build = inject_baseline(vintage="2026", overwrite=True)
    print(build.vintage, len(build.injected))
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import bw2data as bd

from fedefl_bw25.config import (PROJECT_NAME, BIOSPHERE_DB, CONV_TABLE_PATH,
                                ELECTRICITY_BASELINE_DB as INJECTED_DB_NAME, REPO_ROOT)
from fedefl_bw25.olca_library import read_library
from fedefl_bw25.vintage_detect import classify_bundle, decide_vintage, format_conflict

_NOOP = lambda *a, **k: None          # noqa: E731

# Defaults to source_data/ at the repo root; override with the SOURCE_DATA_DIR env
# var or the `bundle_dir` argument rather than editing this file.
DEFAULT_BUNDLE_DIR = Path(os.environ.get("SOURCE_DATA_DIR", REPO_ROOT / "source_data"))
BUNDLE_GLOB = "????????-????-????-????-????????????_*.zip"

# -----------------------------------------------------------------------------
# Per-vintage baseline library config.
# -----------------------------------------------------------------------------
# The pipeline injects exactly ONE electricity-baseline vintage per build. The
# US-average node is named identically across releases ("Electricity; at user;
# consumption mix - US - US") but carries a DIFFERENT UUID each vintage (7068192a
# in 2025-06, 75d4be66 in 2026-06), so injecting two at once would make name-based
# provider resolution ambiguous AND would flip the locked cases' stray 2026
# references -- see DEVLOG / the vintage-UUID note.
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
#   url / sha256 -- canonical release artifact and its whole-file hash (checked on
#                   download only). The host differs per vintage: 2025-06 is a file
#                   in the uslci-content GitHub repo, 2026-06 is served by the LCA
#                   Collaboration Server web service. Do not infer one from the
#                   other -- take each from the upstream release-downloads table.
#   grid_uuid    -- that release's US-average grid process. Same name every
#                   release, different UUID -- which is what makes it the vintage
#                   marker bundles are auto-classified by (see vintage_detect).
def _vintages(bundle_dir: Path) -> dict:
    return {
        "2025": {
            "hand_placed":  bundle_dir / "U.S._electricity_baseline_v1.2025-06.0_from_olca",
            "fetch_target": bundle_dir / "U.S._electricity_baseline_v1.2025-06.0.zip",
            "url": "https://raw.githubusercontent.com/FLCAC-admin/uslci-content/dev/"
                   "downloads/U.S._electricity_baseline_v1.2025-06.0.zip",
            "sha256": "9fec32a8b5f75560c93d6a34986ee5e1166cd3d1cd257616172a3e041cab2815",
            "grid_uuid": "7068192a-999c-39b6-bf66-234a294bdf92",
        },
        "2026": {
            "hand_placed":  bundle_dir / "U.S._electricity_baseline_v1.2026-06.0.zip",
            "fetch_target": bundle_dir / "U.S._electricity_baseline_v1.2026-06.0.zip",
            # NOT on GitHub: from 1.2026-06.0 the eLCI library is served by the LCA
            # Collaboration Server web service, not the uslci-content downloads/
            # folder (which still carries 2025-06 and older). The by-analogy GitHub
            # path 404s -- see the upstream release table, docs/release_info/
            # release-downloads.md, for which host holds which vintage.
            "url": "https://www.lcacommons.gov/lca-collaboration/ws/public/"
                   "libraries/U.S._electricity_baseline_v1.2026-06.0",
            "sha256": "fb545416220e6b3739496661f623081f6fd96de4b1c6508dd6353d88c2b33143",
            "grid_uuid": "75d4be66-12a7-30b3-bc57-fa724c941b0e",
        },
    }


VINTAGES = _vintages(DEFAULT_BUNDLE_DIR)
DEFAULT_VINTAGE = "2025"   # only when NO source names a grid node at all
GRID_UUIDS = {v: c["grid_uuid"] for v, c in VINTAGES.items()}


@dataclass
class BaselineBuild:
    """What one injection produced."""
    vintage: str
    vintage_source: str            # 'detected' (bundles) | 'detected-full-db'
                                   # | 'explicit' | 'default'
    library: str
    injected: list                 # provider UUIDs written
    unresolvable_bundle: list      # referenced by a bundle, absent from this library
    unresolvable_full_only: list   # referenced only by the full zip
    activity_count: int
    exchange_count: int
    dropped_count: int
    notes: list = field(default_factory=list)


class BaselineExists(RuntimeError):
    """The target database already exists and neither overwrite nor confirm allowed it."""


# =============================================================================
# DISCOVERY
# =============================================================================
def find_full_db_zip(bundle_dir: Path) -> Path | None:
    """The full USLCI zip in `bundle_dir`, or None if it isn't there.

    Resolved through the conversion table's `_meta.source_zip` — the same single
    source of truth `setup/03` reads in full-DB mode, so the two cannot drift onto
    different rules. Absence is normal: the bundle-only workflow never needs it.
    """
    try:
        meta = json.loads(Path(CONV_TABLE_PATH).read_text()).get("_meta", {})
    except (OSError, ValueError):
        return None
    name = meta.get("source_zip")
    if not name:
        return None
    path = Path(bundle_dir) / name
    return path if path.exists() else None


def find_external_providers(zip_path: Path) -> dict:
    """Technosphere exchanges in one source whose producer lies outside it.

    Returns {provider_uuid: {flow_uuid, flow_name, provider_name, referenced_by}}
    — the candidates to satisfy from the library.
    """
    with zipfile.ZipFile(zip_path) as z:
        proc_names = [n for n in z.namelist()
                      if n.startswith("processes/") and n.endswith(".json")]
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
                continue  # setup/03 already links this within the source
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
# BUILD
# =============================================================================
def build_activities(lib, provider_uuids, bio_uuids):
    """Brightway activity dicts for the given library processes.

    Returns (db_data, drop_log). `drop_log` maps provider_uuid -> dropped rows,
    covering both the library's own non-elementary cutoffs and elementary flows
    absent from biosphere-fedefl (openLCA's "non-FEDEFL" bucket, e.g. Steam) —
    surfaced for audit, never silently lost.
    """
    db_data, drop_log = {}, {}
    for provider_uuid in provider_uuids:
        proc = lib.processes[lib.process_col(provider_uuid)]
        ref = proc["secondary"]
        flows, dropped = lib.elementary_inventory(provider_uuid)

        exchanges = [{"input": (INJECTED_DB_NAME, provider_uuid), "amount": 1.0,
                      "unit": ref.get("unit", ""), "type": "production"}]
        for flow_uuid, amount in flows.items():
            if flow_uuid not in bio_uuids:
                dropped.append({"id": flow_uuid, "amount": amount,
                                "reason": "not in biosphere-fedefl (openLCA non-FEDEFL flow)"})
                continue
            exchanges.append({"input": (BIOSPHERE_DB, flow_uuid), "amount": amount,
                              "unit": "", "type": "biosphere"})
        if dropped:
            drop_log[provider_uuid] = dropped

        db_data[(INJECTED_DB_NAME, provider_uuid)] = {
            "name":     proc["primary"].get("name", provider_uuid),
            "code":     provider_uuid,
            "location": proc["primary"].get("type") or "GLO",
            "unit":     ref.get("unit", ""),
            "exchanges": exchanges,
            # Flow UUID this process supplies as its reference product -- lets
            # setup/03's flow-based relink fallback find this activity from a
            # bundle exchange's flow @id, the same way flow_to_process works for
            # source-internal processes.
            "reference_product_flow_uuid": ref.get("id"),
        }
    return db_data, drop_log


# =============================================================================
# FETCH
# =============================================================================
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_baseline(cfg: dict, log=_NOOP) -> Path:
    """Download the vintage's pinned library and verify its SHA256.

    Writes to a `.part` file first so an interrupted or corrupt fetch never leaves
    a half-written file where a reader would consume it; on a hash mismatch the
    download is deleted and the build aborts — upstream changed the artifact and a
    human should look before we ingest it.
    """
    target, url, expected = cfg["fetch_target"], cfg["url"], cfg["sha256"]
    target.parent.mkdir(parents=True, exist_ok=True)
    log(f"Fetching electricity baseline library from:\n  {url}")
    tmp = target.with_suffix(target.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=120) as resp, open(tmp, "wb") as out:
            shutil.copyfileobj(resp, out)
    except urllib.error.URLError as e:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"Download failed: {e}") from e

    got = _sha256(tmp)
    if got != expected:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            "Downloaded baseline failed hash check -- upstream artifact changed.\n"
            f"  expected {expected}\n  got      {got}\n"
            "Refusing to ingest. If this is an intentional upstream re-issue, verify "
            "the new file and update the vintage's sha256 in VINTAGES.")
    tmp.replace(target)
    log(f"  verified SHA256, saved {target.stat().st_size:,} bytes -> {target}")
    return target


def resolve_library(explicit, fetch, cfg, bundle_dir, log=_NOOP) -> Path:
    """Which library zip to read: explicit wins, then hand-placed, then fetched."""
    if explicit is not None:
        explicit = Path(explicit)
        if not explicit.exists():
            raise RuntimeError(f"Library not found: {explicit}")
        return explicit
    if cfg["hand_placed"].exists():
        return cfg["hand_placed"]
    if cfg["fetch_target"].exists():
        return cfg["fetch_target"]
    if not fetch:
        raise RuntimeError(
            f"Baseline library not found (looked for '{cfg['hand_placed'].name}' and "
            f"'{cfg['fetch_target'].name}' in {bundle_dir}).\nDrop the file there by "
            f"hand, or allow fetching to download it automatically.")
    return fetch_baseline(cfg, log=log)


# =============================================================================
# THE STEP
# =============================================================================
def scan_sources(bundle_dir, log=_NOOP):
    """Providers referenced across all sources, plus their vintage verdicts.

    Returns (external, verdicts, full_only, full_verdict). `full_only` is the set
    referenced by the full USLCI zip and by no bundle — reported separately because
    a full-only provider missing from a given vintage's library is expected, and
    must not read as damage to the bundle build.

    `full_verdict` is the full zip's own vintage reading, kept apart from the
    per-bundle `verdicts` because bundles decide the vintage whenever they can:
    they are the study data, the full zip is background. It is used only when no
    bundle votes, which is the whole-database workflow — one download, no bundles,
    and previously no evidence at all.

    Either source alone is enough. Requiring bundles made the minimum download
    nine files instead of one.
    """
    bundle_dir = Path(bundle_dir)
    bundles = sorted(bundle_dir.glob(BUNDLE_GLOB))
    full_zip = find_full_db_zip(bundle_dir)
    if not bundles and full_zip is None:
        raise RuntimeError(
            f"No USLCI sources found in {bundle_dir}. Place either the full USLCI "
            f"zip or per-process bundle zips there (see README, step 2)."
        )

    log(f"Scanning {len(bundles)} bundle(s) in {bundle_dir} for external providers...")
    external, verdicts = {}, {}
    for path in bundles:
        found = find_external_providers(path)
        verdicts[path.name] = classify_bundle(found, GRID_UUIDS)
        log(f"  {path.name}: {len(found)} external provider(s) referenced"
            f"  [grid vintage: {verdicts[path.name]['vintage'] or 'n/a'}]")
        for uid, info in found.items():
            external.setdefault(uid, {**info, "referenced_by": set()})["referenced_by"] |= info["referenced_by"]

    full_only, full_verdict = set(), None
    if full_zip is not None:
        found = find_external_providers(full_zip)
        full_only = set(found) - set(external)
        full_verdict = classify_bundle(found, GRID_UUIDS)
        voted = any(v["vintage"] for v in verdicts.values())
        log(f"  {full_zip.name}: {len(found)} external provider(s) referenced"
            f"  [{len(full_only)} not referenced by any bundle; grid vintage: "
            f"{full_verdict['vintage'] or 'n/a'}"
            f"{', outvoted by the bundles' if voted else ''}]")
        for uid, info in found.items():
            external.setdefault(uid, {**info, "referenced_by": set()})["referenced_by"] |= info["referenced_by"]
    else:
        log("  (full USLCI zip not present — injecting for the bundle build only; "
            "a USLCI_FULL_DB=1 import may leave grid links unresolved)")
    return external, verdicts, full_only, full_verdict


def inject_baseline(*, vintage=None, library=None, fetch=True, bundle_dir=None,
                    overwrite=False, confirm=None, project=PROJECT_NAME, log=_NOOP):
    """Scan, decode and write the electricity baseline. Returns a `BaselineBuild`.

    `overwrite=True` replaces an existing database outright. `confirm` is an
    optional callable taking the database name and returning a bool — the CLI
    passes an input() prompt so a human sees the build report first, while a
    scripted build never blocks on stdin. With neither, an existing database
    raises `BaselineExists`.
    """
    bundle_dir = Path(bundle_dir) if bundle_dir else DEFAULT_BUNDLE_DIR
    vintages = _vintages(bundle_dir)
    notes = []

    bd.projects.set_current(project)
    if not bd.projects.twofive:
        bd.projects.migrate_project_25()
    if BIOSPHERE_DB not in bd.databases:
        raise RuntimeError("Run setup/01_setup_biosphere_fedefl.py first.")

    external, verdicts, full_only, full_verdict = scan_sources(bundle_dir, log=log)

    decision = decide_vintage(verdicts, explicit=vintage, default=DEFAULT_VINTAGE)
    if decision["conflict"]:
        raise RuntimeError("\n" + format_conflict(decision))
    # No bundle voted, and none was asked for: read the full zip instead of taking
    # DEFAULT_VINTAGE on faith. The whole-database workflow has no bundles at all,
    # so the default was being applied to a build with real evidence sitting in it —
    # and it is wrong for the current USLCI release, whose 359 grid references are
    # all 2026 while DEFAULT_VINTAGE is 2025.
    if (decision["source"] == "default" and vintage is None
            and full_verdict and full_verdict["vintage"]):
        decision = {**decision, "vintage": full_verdict["vintage"],
                    "source": "detected-full-db"}
    chosen = decision["vintage"]
    cfg = vintages[chosen]
    if decision["source"] == "detected":
        log(f"\nAuto-detected electricity-baseline vintage: {chosen} "
            f"(from {sum(len(b) for b in decision['groups'].values())} bundle(s))")
    elif decision["source"] == "detected-full-db":
        log(f"\nAuto-detected electricity-baseline vintage: {chosen} "
            f"(no bundles present; read from the full USLCI zip — "
            f"{full_verdict['reason']})")
    elif decision["source"] == "default":
        log(f"\nNo source references a baseline grid node — falling back to the "
            f"default vintage {chosen}.")
    else:
        also = [v for v in decision["groups"] if v != chosen]
        log(f"\nElectricity-baseline vintage: {chosen} (set explicitly)")
        if also:
            note = (f"NOTE: bundle references also point at {', '.join(sorted(also))} — "
                    f"those bundles will not link to this build's grid.")
            notes.append(note); log("  " + note)

    lib_path = resolve_library(library, fetch, cfg, bundle_dir, log=log)
    log(f"Using library: {lib_path.name}")
    log(f"\n{len(external)} distinct external provider(s) referenced across all sources.")

    lib = read_library(lib_path)
    resolvable = [u for u in external if u in lib._proc_by_uuid]
    unresolvable = [u for u in external if u not in lib._proc_by_uuid]
    bundle_unres = [u for u in unresolvable if u not in full_only]
    full_unres = [u for u in unresolvable if u in full_only]

    if bundle_unres:
        note = (f"WARNING: {len(bundle_unres)} bundle-referenced provider(s) NOT found in "
                f"library '{lib.name}' -- these will remain cutoffs.")
        notes.append(note); log("\n" + note)
        for uid in bundle_unres:
            info = external[uid]
            log(f"  {uid}  {info['provider_name']!r}  (flow: {info['flow_name']!r}, "
                f"referenced by {len(info['referenced_by'])} process(es))")
    if full_unres:
        note = (f"NOTE: {len(full_unres)} provider(s) referenced only by the full USLCI zip "
                f"are absent from library '{lib.name}' (expected on a vintage the full zip "
                f"predates). The bundle build is unaffected; a USLCI_FULL_DB=1 import on "
                f"this vintage will cut those grid links.")
        notes.append(note); log("\n" + note)

    if not resolvable:
        raise RuntimeError("No referenced providers resolve against the library — "
                           "nothing to inject.")

    log(f"\nInjecting {len(resolvable)} process(es) from the library as background activities:")
    for uid in resolvable:
        info = external[uid]
        log(f"  {uid}  {info['provider_name']!r}  "
            f"(referenced by {len(info['referenced_by'])} process(es))")

    bio_uuids = {a["code"] for a in bd.Database(BIOSPHERE_DB)}
    db_data, drop_log = build_activities(lib, resolvable, bio_uuids)
    total_exch = sum(len(a["exchanges"]) - 1 for a in db_data.values())
    total_dropped = sum(len(v) for v in drop_log.values())
    log(f"\nBuilt {len(db_data)} activities, {total_exch} biosphere exchanges total, "
        f"{total_dropped} row(s) dropped (library-side cutoffs / non-FEDEFL flows).")
    if drop_log:
        sample = next(iter(drop_log))
        log(f"  Sample ({sample}): "
            f"{[d.get('id', d.get('reason')) for d in drop_log[sample][:3]]}")

    if INJECTED_DB_NAME in bd.databases:
        allowed = overwrite or (confirm(INJECTED_DB_NAME) if confirm else False)
        if not allowed:
            raise BaselineExists(
                f"Database '{INJECTED_DB_NAME}' already exists. Pass overwrite=True "
                f"(or --yes on the CLI) to replace it.")
        del bd.databases[INJECTED_DB_NAME]

    bd.Database(INJECTED_DB_NAME).write(db_data)

    # Stamp the vintage onto the database. setup/03 copies it onto the USLCI DB and
    # the harness asserts against it, so a case can never be silently diffed against
    # the wrong-vintage grid — a build is single-vintage, and mixing 2025 cases with
    # a 2026 grid is the footgun.
    bd.databases[INJECTED_DB_NAME]["electricity_vintage"] = chosen
    bd.databases[INJECTED_DB_NAME]["electricity_library"] = lib.name
    bd.databases.flush()
    log(f"\nDatabase '{INJECTED_DB_NAME}' written — {len(db_data)} processes "
        f"(electricity_vintage = {chosen}).")

    return BaselineBuild(
        vintage=chosen, vintage_source=decision["source"], library=lib.name,
        injected=resolvable, unresolvable_bundle=bundle_unres,
        unresolvable_full_only=full_unres, activity_count=len(db_data),
        exchange_count=total_exch, dropped_count=total_dropped, notes=notes)
