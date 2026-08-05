"""run.py — the LCIA runner as a callable, not a script.

`run_lca()` computes a result and returns it; writing files is separate. That split
is the point: `general/04_run_lca.py` used to interleave solving with printing and
CSV writing across 750 lines of top-level statements, so nothing could call it and
nothing could test it. Here the computation returns an `LcaRun` and the caller
decides what to persist::

    from fedefl_bw25.run import run_lca, write_results_csv

    run = run_lca(uuid="0aaf1e13-5d80-37f9-b7bb-81a6b8965c71")
    print(run.score("Global warming"), run.functional_unit)
    write_results_csv(run, "study.csv", append=True)

Unlike the rest of the package this module DOES import brightway — it is the part
that drives it. The pure modules (`allocation`, `run_manifest`, `chart_units`,
`vintage_detect`, `foreground_importer`) stay brightway-free so they keep
unit-testing without a built database.

Nothing here prints. Diagnostics that the script shows the user are returned on
`LcaRun.notes`, so a library caller can inspect, log or ignore them, and a sweep
of 200 scenarios is not 200 pages of console output. Pass `log=print` for the
progress chatter the CLI wants.
"""
from __future__ import annotations

import atexit
import csv
import importlib.metadata
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import bw2data as bd
import bw2calc as bc

from fedefl_bw25 import run_manifest
from fedefl_bw25.config import (PROJECT_NAME, BIOSPHERE_DB, USLCI_DB, USLCI_FULL_DB,
                                ELECTRICITY_BASELINE_DB, METHOD_ROOT, REPO_ROOT)
from fedefl_bw25.foreground_importer import load_foreground_csv, fg_uuid

FOREGROUND_DB = "foreground"
_NOOP = lambda *a, **k: None          # noqa: E731  — default `log`


# =============================================================================
# RESULT OBJECT
# =============================================================================
@dataclass
class LcaRun:
    """Everything one run produced. Plain data — no brightway objects retained."""
    target: dict
    scenario: str
    functional_unit: str
    database: str
    results: list                      # [{scenario, method, score, unit, functional_unit}]
    contributions: list                # same, per contributing process (may be empty)
    electricity_vintage: dict
    manifest: dict | None
    notes: list = field(default_factory=list)
    build: dict = field(default_factory=dict)

    def score(self, method_name):
        """Score for one TRACI category by its short name ('Global warming')."""
        for r in self.results:
            if r["method"] == method_name:
                return r["score"]
        raise KeyError(f"{method_name!r} not among {[r['method'] for r in self.results]}")

    def as_dict(self):
        return {r["method"]: r["score"] for r in self.results}


# =============================================================================
# BUILD INSPECTION
# =============================================================================
def describe_build(database=None):
    """What a run against `database` would stand on. Safe to call before running.

    Returned rather than printed so the CLI banner and the manifest agree by
    construction instead of by two code paths saying the same thing.
    """
    database = database or USLCI_DB
    meta = bd.databases[database] if database in bd.databases else {}
    return {
        "database": database,
        "activity_count": meta.get("number"),
        "electricity_vintage": meta.get("electricity_vintage") or "unstamped",
        "composition": ("whole USLCI database" if database == USLCI_FULL_DB else
                        "per-process bundles: the bundle targets PLUS all upstream "
                        "processes those bundles shipped"),
        "source": run_manifest.describe_uslci_source(meta.get("uslci_source") or {}),
        "other_builds": {n: bd.databases[n].get("number")
                         for n in (USLCI_DB, USLCI_FULL_DB)
                         if n != database and n in bd.databases},
    }


def open_project(project=PROJECT_NAME):
    """Select the brightway project and check the setup chain has been run."""
    bd.projects.set_current(project)
    if not bd.projects.twofive:
        bd.projects.migrate_project_25()
    if BIOSPHERE_DB not in bd.databases:
        raise RuntimeError(
            f"'{BIOSPHERE_DB}' not found. Run setup/01_setup_biosphere_fedefl.py first.")
    methods = sorted(m for m in bd.methods if m[:2] == METHOD_ROOT)
    if not methods:
        raise RuntimeError("No TRACI 2.2 methods found. Run setup/02_setup_traci22.py first.")
    if len(methods) != 10:
        raise RuntimeError(f"Expected 10 TRACI 2.2 methods, found {len(methods)}. "
                           f"Re-run setup/02_setup_traci22.py.")
    return methods


def _require_database(database):
    if database in bd.databases:
        return
    hint = ("Run 'USLCI_FULL_DB=1 python setup/03_import_uslci.py' to build it."
            if database == USLCI_FULL_DB else "Run setup/03_import_uslci.py first.")
    raise RuntimeError(
        f"'{database}' not found. {hint}\n  USLCI builds present: "
        f"{[n for n in (USLCI_DB, USLCI_FULL_DB) if n in bd.databases] or 'none'}")


# =============================================================================
# PREFLIGHT SMOKE TEST
# =============================================================================
def _flow_with_cf(bio_db, method, name_fragment, category_fragment):
    cf_ids = {k for k, _ in bd.Method(method).load()}
    return next((a for a in bio_db
                 if name_fragment.lower() in a["name"].lower()
                 and category_fragment.lower() in str(a.get("categories", "")).lower()
                 and a.id in cf_ids), None)


def _one_smoke(flow, method, lo, hi):
    TEST_DB, key = "_smoke_tmp", ("_smoke_tmp", "test")
    if TEST_DB in bd.databases:
        del bd.databases[TEST_DB]
    bd.Database(TEST_DB).write({key: {
        "name": "smoke", "unit": "unit", "location": "US",
        "exchanges": [
            {"input": key, "amount": 1.0, "type": "production"},
            {"input": (BIOSPHERE_DB, flow["code"]), "amount": 1.0, "unit": "kg",
             "type": "biosphere"},
        ]}})
    try:
        lca = bc.LCA({bd.get_activity(key): 1.0}, method)
        lca.lci(); lca.lcia()
        score = lca.score
    finally:
        if TEST_DB in bd.databases:
            del bd.databases[TEST_DB]
    return (lo <= score <= hi), score


def preflight(methods, log=_NOOP):
    """Characterize three known flows and check the answers are sane.

    Cheap insurance that the biosphere and CF setup is intact before a real solve —
    a broken FEDEFL/TRACI link otherwise surfaces as a plausible-looking number.
    """
    bio_db = bd.Database(BIOSPHERE_DB)
    checks = [
        ("warm",   "carbon dioxide", "air",   0.9,  1.1,  "CO2 → Global warming ≈ 1.0 kg CO2-eq"),
        ("acid",   "sulfur dioxide", "air",   1e-4, 1e4,  "SO2 → Acidification > 0"),
        ("marine", "nitrogen",       "water", 1e-6, 1e4,  "Nitrogen → Eutrophication (Marine) > 0"),
    ]
    log("=== Preflight smoke test ===")
    ok_all, notes = True, []
    for frag, flow_name, ctx, lo, hi, label in checks:
        method = next((m for m in methods if frag in m[2].lower()), None)
        if method is None:
            raise RuntimeError(f"No TRACI 2.2 method matching {frag!r}. "
                               f"Re-run setup/02_setup_traci22.py.")
        flow = _flow_with_cf(bio_db, method, flow_name, ctx)
        if flow is None:
            raise RuntimeError(f"No {flow_name}/{ctx} flow with a CF found. "
                               f"Re-run setup/02_setup_traci22.py.")
        ok, score = _one_smoke(flow, method, lo, hi)
        ok_all &= ok
        line = f"  [{'PASS' if ok else 'FAIL'}] {label}: {score:.6g} (expected {lo}–{hi})"
        notes.append(line); log(line)
    if not ok_all:
        raise RuntimeError(
            "Preflight smoke test failed — biosphere or TRACI CFs are broken.\n"
            "Fix: re-run setup/01_setup_biosphere_fedefl.py then setup/02_setup_traci22.py.")
    return notes


# =============================================================================
# FOREGROUND
# =============================================================================
def build_foreground(csv_path, database, log=_NOOP):
    """Write the transient FOREGROUND_DB from an inventory CSV.

    The database is rebuilt from the CSV on every run and removed afterwards, so
    the delete-before-write is not data loss. `atexit` is a backstop for an
    unhandled exception; `run_lca` also removes it on the normal path so a
    long-lived session (a sweep, a notebook) doesn't accumulate state.
    """
    bio_db = bd.Database(BIOSPHERE_DB)
    fg_processes = load_foreground_csv(Path(csv_path), bio_db, bd.Database(database))
    log(f"  {len(fg_processes)} foreground process(es) loaded.")
    fg_uuids = {p["uuid"] for p in fg_processes.values()}

    db_data = {}
    for name, proc in fg_processes.items():
        key = (FOREGROUND_DB, proc["uuid"])
        exchanges = []
        for exc in proc["exchanges"]:
            etype, amount, unit = exc["exchange_type"], exc["amount"], exc["unit"]
            if etype == "production":
                exchanges.append({"input": key, "amount": amount, "unit": unit,
                                  "type": "production"})
            elif etype == "biosphere":
                exchanges.append({"input": (BIOSPHERE_DB, exc["flow_uuid"]),
                                  "amount": amount, "unit": unit, "type": "biosphere"})
            elif etype == "technosphere":
                prov = exc["provider_uuid"]
                exchanges.append({
                    "input": (FOREGROUND_DB, prov) if prov in fg_uuids else (database, prov),
                    "amount": amount, "unit": unit, "type": "technosphere"})
        ref = next((e for e in proc["exchanges"] if e["is_ref"]), None)
        db_data[key] = {"name": name, "code": proc["uuid"],
                        "location": ref["location"] if ref else "US",
                        "unit": ref["unit"] if ref else "unit",
                        "exchanges": exchanges}

    if FOREGROUND_DB in bd.databases:
        del bd.databases[FOREGROUND_DB]
    bd.Database(FOREGROUND_DB).write(db_data)
    atexit.register(lambda: bd.databases.__delitem__(FOREGROUND_DB)
                    if FOREGROUND_DB in bd.databases else None)
    log(f"  Foreground database '{FOREGROUND_DB}' written.")
    return fg_processes


def drop_foreground():
    """Remove the transient foreground database if present."""
    if FOREGROUND_DB in bd.databases:
        del bd.databases[FOREGROUND_DB]


# =============================================================================
# TARGET RESOLUTION
# =============================================================================
def resolve_target(*, uuid=None, fg_processes=None, target_process=None, database=None):
    """The activity a run is for, with an error that says what to do on a miss."""
    database = database or USLCI_DB
    if fg_processes:
        names = list(fg_processes)
        if target_process is not None:
            tgt = fg_uuid(target_process)
            if tgt not in {p["uuid"] for p in fg_processes.values()}:
                raise RuntimeError(f"--target-process '{target_process}' not found in "
                                   f"foreground CSV. Available processes: {names}")
            return bd.get_activity((FOREGROUND_DB, tgt))
        if len(fg_processes) == 1:
            return bd.get_activity((FOREGROUND_DB,
                                    next(iter(fg_processes.values()))["uuid"]))
        raise RuntimeError(f"Foreground CSV has {len(fg_processes)} processes — specify "
                           f"one with --target-process NAME.\nAvailable: {names}")

    # bw2data's .get() RAISES UnknownObject on a miss and never returns None, so
    # catching it is the only way a useful message reaches the caller. Three things
    # are worth knowing: which build was searched, whether the code is actually a
    # UUID (a pasted bundle filename '<uuid>_<release-hash>' is the easy mistake),
    # and whether the other build has it.
    try:
        return bd.Database(database).get(uuid)
    except Exception as e:
        lines = [f"Process '{uuid}' not found in USLCI build '{database}' "
                 f"({bd.databases[database].get('number')} activities)."]
        bare = str(uuid).split("_", 1)[0]
        if bare != uuid and len(bare) == 36:
            here = False
            try:
                bd.Database(database).get(bare); here = True
            except Exception:
                pass
            lines.append(f"  That looks like a bundle FILENAME, not a process UUID. The "
                         f"text after the underscore is the USLCI release hash. Try: {bare}")
            if here:
                # The suffix is the whole problem; pointing elsewhere misdirects.
                raise RuntimeError("\n".join(lines)) from e
        elsewhere = []
        for other in (USLCI_DB, USLCI_FULL_DB):
            if other == database or other not in bd.databases:
                continue
            try:
                bd.Database(other).get(bare); elsewhere.append(other)
            except Exception:
                pass
        if elsewhere:
            lines.append(f"  It IS in: {', '.join(elsewhere)} — re-run with "
                         f"--database {elsewhere[0]} (or set USLCI_DATABASE in CONFIG).")
        elif database != USLCI_FULL_DB and USLCI_FULL_DB not in bd.databases:
            lines.append("  Not in any built USLCI database. Check the UUID, or build the "
                         "whole database with 'USLCI_FULL_DB=1 python "
                         "setup/03_import_uslci.py' — this build only has what its "
                         "bundles shipped.")
        else:
            src = run_manifest.describe_uslci_source(
                bd.databases[database].get("uslci_source") or {})
            lines.append(f"  Not in any built USLCI database, and this build already covers "
                         f"the whole USLCI it was made from:\n    {src}\n  USLCI ships "
                         f"quarterly, so a process added in a newer release will be missing "
                         f"here. Check the UUID on lcacommons.gov — if it exists in a later "
                         f"release, re-import from that export.")
        raise RuntimeError("\n".join(lines)) from e


# =============================================================================
# ZERO-RESULT DIAGNOSIS
# =============================================================================
def diagnose_all_zero(target_act, methods):
    """Why is every score 0.0? Answer from what this run can see.

    Naming one cause confidently sent real investigations the wrong way: the old
    message asserted a biosphere UUID mismatch, which was wrong for every process
    that actually tripped it.
    """
    exchanges = list(target_act.exchanges()) if hasattr(target_act, "exchanges") else []
    bio = [e for e in exchanges if e["type"] == "biosphere"]
    tech = [e for e in exchanges if e["type"] == "technosphere"]
    out = [f"WARNING: all {len(methods)} TRACI scores are 0.0 for this target.",
           f"  The target declares {len(bio)} biosphere and {len(tech)} technosphere exchange(s)."]
    if not bio and not tech:
        out.append("  It has no exchanges at all, so zero is the correct result "
                   "(USLCI ships such stubs, e.g. the 'Bridge; USLCI to USEEIO' processes).")
        return out
    if not bio:
        out.append("  It emits nothing directly, so its score comes entirely from upstream. "
                   "Zero means those technosphere inputs resolved to nothing — check "
                   "completeness.notes in the manifest for cutoffs.")
        return out
    # A flow can be matched to FEDEFL and still be uncharacterized: TRACI 2.2
    # characterizes PM2.5, not a generic "Particulate matter", so such flows ride
    # along in the inventory contributing exactly nothing. Distinct from an
    # unmatched flow and from a setup fault, and invisible without checking CFs.
    cf_ids = set()
    for m in methods:
        cf_ids |= {k for k, _ in bd.Method(m).load()}
    uncf = [e for e in bio if e.input.id not in cf_ids]
    if len(uncf) == len(bio):
        out.append(f"  All {len(bio)} of its biosphere flow(s) are matched to {BIOSPHERE_DB} "
                   f"but have NO TRACI 2.2 characterization factor, so they contribute "
                   f"nothing — the inventory is carried, not dropped:")
        out += [f"    - {e.input['name']} ({e['amount']:g})" for e in uncf[:4]]
        out.append("  This is a coverage limit of TRACI 2.2, not a build fault.")
    else:
        out.append("  Some of its flows are characterized, so zero is unexpected. If other "
                   "processes in this build score normally the inventory is likely cut; if "
                   "EVERY process scores zero, suspect the biosphere/CF setup and re-run "
                   "setup/01 then setup/02.")
    return out


# =============================================================================
# THE RUNNER
# =============================================================================
def run_lca(*, uuid=None, foreground=None, target_process=None, database=None,
            scenario=None, contributions=True, manifest=True, smoke_test=True,
            project=PROJECT_NAME, log=_NOOP):
    """Run LCIA across all 10 TRACI 2.2 categories and return an `LcaRun`.

    Exactly one of `uuid` (a USLCI process) or `foreground` (an inventory CSV) is
    the target; if both are given the foreground wins, matching the CLI.

    Writes nothing. Use `write_results_csv` / `write_contributions_csv` /
    `write_manifest_json` to persist, so a parameter sweep can hold results in
    memory and write once.
    """
    database = database or USLCI_DB
    notes = []
    methods = open_project(project)
    _require_database(database)
    build = describe_build(database)

    if smoke_test:
        notes += preflight(methods, log=log)

    fg_processes = {}
    if foreground is not None:
        log(f"=== Loading foreground CSV: {foreground} ===")
        fg_processes = build_foreground(foreground, database, log=log)
        if uuid is not None:
            notes.append(f"NOTE: foreground and uuid both given — uuid '{uuid}' ignored.")

    try:
        target_act = resolve_target(uuid=uuid, fg_processes=fg_processes,
                                    target_process=target_process, database=database)
        scenario_label = scenario if scenario is not None else target_act["name"]
        # The demand is always 1 unit of the target's OWN reference unit, which is
        # not necessarily mass (petroleum at refinery is m3). State it wherever a
        # score appears so a per-m3 number is never read as per-kg.
        functional_unit = f"1 {target_act.get('unit') or 'unit'}"

        db_stamps = {n: {"electricity_vintage": bd.databases[n].get("electricity_vintage")}
                     for n in (database, ELECTRICITY_BASELINE_DB) if n in bd.databases}
        electricity_vintage = run_manifest.summarize_electricity_vintage(
            db_stamps, database, ELECTRICITY_BASELINE_DB)

        target = {"uuid": target_act["code"], "name": target_act["name"],
                  "unit": target_act.get("unit"), "functional_unit": functional_unit,
                  "location": target_act.get("location"), "scenario": scenario_label,
                  "source": "foreground" if foreground is not None else "uslci"}

        method_units = {m: bd.Method(m).metadata.get("unit", "?") for m in methods}
        lca = bc.LCA({target_act: 1.0}, methods[0])
        lca.lci()

        act_by_col = {}
        if contributions:
            for key, col in lca.dicts.activity.items():
                try:
                    a = bd.get_activity(key)
                    act_by_col[col] = {"name": a["name"], "code": a["code"], "db": a.key[0]}
                except Exception:
                    act_by_col[col] = {"name": str(key), "code": str(key), "db": "unknown"}

        results, contrib_rows = [], []
        log(f"=== LCIA results — per {functional_unit} of '{target_act['name']}' ===")
        for method in methods:
            lca.switch_method(method)
            lca.lcia()
            unit = method_units[method]
            results.append({"scenario": scenario_label, "method": method[2],
                            "score": lca.score, "unit": unit,
                            "functional_unit": functional_unit})
            log(f"  {method[2]:45s}  {lca.score:.6g}  {unit}")
            if contributions:
                per_proc = np.asarray(lca.characterized_inventory.sum(axis=0)).flatten()
                for col, amount in enumerate(per_proc):
                    if abs(amount) < 1e-30:
                        continue
                    info = act_by_col.get(col, {"name": "unknown", "code": "?", "db": "unknown"})
                    contrib_rows.append({
                        "scenario": scenario_label, "method": method[2],
                        "process_name": info["name"], "process_uuid": info["code"],
                        "db": info["db"], "contribution_score": float(amount),
                        "unit": unit, "functional_unit": functional_unit})

        if all(r["score"] == 0.0 for r in results):
            zero_notes = diagnose_all_zero(target_act, methods)
            notes += zero_notes
            for line in zero_notes:
                log(line)

        manifest_doc = None
        if manifest:
            manifest_doc, mnotes = _assemble_manifest(
                lca=lca, methods=methods, method_units=method_units, results=results,
                target=target, database=database, electricity_vintage=electricity_vintage,
                foreground=foreground)
            notes += mnotes
            for line in mnotes:
                log(line)
    finally:
        if foreground is not None:
            drop_foreground()

    return LcaRun(target=target, scenario=scenario_label, functional_unit=functional_unit,
                  database=database, results=results, contributions=contrib_rows,
                  electricity_vintage=electricity_vintage, manifest=manifest_doc,
                  notes=notes, build=build)


def _assemble_manifest(*, lca, methods, method_units, results, target, database,
                       electricity_vintage, foreground):
    """Provenance + per-result completeness. Pure assembly lives in run_manifest."""
    notes = []
    # Reduce the technosphere column index to what this result actually draws on.
    # lca.dicts.activity spans every activity in the loaded databases, reachable or
    # not, so summing completeness over it reports database-wide totals as if they
    # were this result's.
    supplied = run_manifest.select_supplied_keys(lca.dicts.activity.items(), lca.supply_array)
    solved_keys = []
    for k in supplied:
        try:
            a = bd.get_activity(k)
            solved_keys.append((a.key[0], a["code"]))
        except Exception:
            solved_keys.append(("unknown", str(k)))

    prov_filename = run_manifest.db_provenance_filename(database, USLCI_DB)
    prov_path = Path(REPO_ROOT) / prov_filename
    provenance_processes = {}
    prov_source = {"available": False, "path": str(prov_path)}
    if prov_path.exists():
        try:
            doc = json.loads(prov_path.read_text(encoding="utf-8"))
            sidecar_db = doc.get("database")
            if sidecar_db is not None and sidecar_db != database:
                # The filename already separates the builds, so this only fires if a
                # sidecar was renamed or hand-edited. Reading one build's diagnostics
                # against another would silently misreport completeness.
                notes.append(f"WARNING: {prov_filename} describes database '{sidecar_db}' "
                             f"but this run targets '{database}' — ignoring it; "
                             f"completeness will be limited.")
                prov_source["mismatched_database"] = sidecar_db
            else:
                provenance_processes = doc.get("processes", {})
                sidecar_count = doc.get("db_activity_count")
                live_count = bd.databases[database].get("number")
                prov_source = {"available": True, "path": str(prov_path),
                               "database": sidecar_db, "generated": doc.get("generated"),
                               "db_activity_count": sidecar_count,
                               "live_db_activity_count": live_count,
                               "matches_live_db": sidecar_count == live_count}
                if sidecar_count != live_count:
                    notes.append(f"WARNING: {prov_filename} records {sidecar_count} activities "
                                 f"but '{database}' currently has {live_count} — the sidecar "
                                 f"looks stale, so completeness may be inaccurate. Re-run "
                                 f"setup/03_import_uslci.py.")
        except (ValueError, OSError) as e:
            notes.append(f"WARNING: could not read {prov_path}: {e} — completeness limited.")
    else:
        notes.append(f"NOTE: {prov_filename} not found at repo root — completeness section "
                     f"will be limited. Re-run setup/03_import_uslci.py to generate it.")

    completeness = run_manifest.summarize_supply_chain_completeness(
        solved_keys, provenance_processes, database, [ELECTRICITY_BASELINE_DB])

    dbs = {}
    for name in (BIOSPHERE_DB, database, ELECTRICITY_BASELINE_DB, FOREGROUND_DB):
        if name not in bd.databases:
            continue
        meta = bd.databases[name]
        dbs[name] = {"activity_count": meta.get("number"), "modified": meta.get("modified")}
        if meta.get("electricity_vintage") is not None:
            dbs[name]["electricity_vintage"] = meta.get("electricity_vintage")
        if meta.get("uslci_source") is not None:
            dbs[name]["uslci_source"] = meta.get("uslci_source")

    doc = run_manifest.build_manifest(
        generated=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        target=target, project=PROJECT_NAME, databases=dbs,
        electricity_vintage=electricity_vintage,
        methods=[[m[0], m[1], m[2], method_units[m]] for m in methods],
        packages=_pkg_versions(["bw2data", "bw2calc", "bw2io", "fedelemflowlist",
                                "lciafmt", "numpy", "pandas"]),
        provenance_source=prov_source,
        solved_system={"activity_count": len(solved_keys),
                       "biosphere_flow_count": len(lca.dicts.biosphere)},
        completeness=completeness, results=results)
    return doc, notes


def _pkg_versions(names):
    out = {}
    for n in names:
        try:
            out[n] = importlib.metadata.version(n)
        except importlib.metadata.PackageNotFoundError:
            out[n] = None
    return out


# =============================================================================
# WRITERS
# =============================================================================
RESULT_FIELDS = ["scenario", "method", "score", "unit", "functional_unit"]
CONTRIB_FIELDS = ["scenario", "method", "process_name", "process_uuid", "db",
                  "contribution_score", "unit", "functional_unit"]


def write_results_csv(run, path, append=False):
    """Write the 10 category scores. `append=True` accumulates scenarios.

    Re-running the same scenario label REPLACES its rows rather than duplicating
    them: a sweep is iterative, and silent duplicates would double-count in any
    chart that groups by scenario. Returns the number of scenarios in the file.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    kept = []
    if append and path.exists():
        with open(path, newline="", encoding="utf-8") as f:
            kept = [r for r in csv.DictReader(f) if r.get("scenario") != run.scenario]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=RESULT_FIELDS)
        w.writeheader(); w.writerows(kept); w.writerows(run.results)
    return len({r["scenario"] for r in kept} | {run.scenario})


def write_contributions_csv(run, path):
    if not run.contributions:
        return 0
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CONTRIB_FIELDS)
        w.writeheader(); w.writerows(run.contributions)
    return len(run.contributions)


def _json_default(o):
    # LCIA scores are numpy floats and a DB 'modified' may be a datetime; neither
    # is JSON-serializable by default.
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, (datetime,)):
        return o.isoformat()
    return str(o)


def write_manifest_json(run, path):
    if run.manifest is None:
        return None
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(run.manifest, indent=2, default=_json_default),
                    encoding="utf-8")
    return path
