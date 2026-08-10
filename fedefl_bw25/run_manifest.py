"""
run_manifest.py
---------------
Assembles the per-run audit manifest (`validation_manifest.json`) that
general/04_run_lca.py emits alongside the results CSV — the "audit any individual
result, not just the four locked test cases" artifact (RELEASE_PLAN Phase 4.4 /
over-delegation ledger #9).

The point of this module is the *per-result completeness* view. The setup scripts
already compute whole-database link/match counts, but only print them to stdout at
build time. `setup/03_import_uslci.py` now persists those counts *per process* to
`uslci_db_provenance.json`; here we cross that against THIS result's solved supply
chain so a user can see how complete their own number is — which cutoffs, if any,
fall inside the activities that actually contributed to it. "Actually contributed"
is load-bearing: the supply chain is the non-zero part of `bw2calc`'s solved supply
vector, not every column in `lca.dicts.activity` (see `select_supplied_keys`).

Everything in this module is pure (no `bw2data`/`bw2calc` import) and operates on
plain Python data, so it unit-tests without a built brightway database. The caller
(general/04) supplies the brightway-derived inputs: the solved-system activity
keys, database metadata, and the LCIA scores.

The provenance sidecar filename is duplicated as a local literal in
`setup/03_import_uslci.py` (the writer). Keep the two in sync; both anchor to
`config.REPO_ROOT`. Promote to `config.py` if a third script ever needs it.
"""
from __future__ import annotations

SCHEMA = "validation-manifest/2"   # /2 adds the electricity_vintage block

# Written by setup/03_import_uslci.py, read by general/04_run_lca.py. Anchored to
# the repo root on both sides (see setup/03's local copy of this literal).
DB_PROVENANCE_FILENAME = "uslci_db_provenance.json"
MANIFEST_FILENAME = "validation_manifest.json"


def db_provenance_filename(db_name, default_db_name):
    """Sidecar filename for a given USLCI build.

    The bundle build keeps the historical unsuffixed name, so existing runs,
    docs, and CI paths are unaffected. Any other build (currently the full-database
    build) gets its own suffixed file — the two builds coexist, and a sidecar
    describing one must never be read as if it described the other.
    """
    if db_name == default_db_name:
        return DB_PROVENANCE_FILENAME
    stem, _, ext = DB_PROVENANCE_FILENAME.rpartition(".")
    return f"{stem}.{db_name}.{ext}"


def describe_uslci_source(source):
    """One-line human description of which USLCI sources produced a build.

    `source` is the `uslci_source` stamp setup/03 writes onto the database:
    `{"mode": "full_db"|"bundles", "zips": [{"name", "sha256"}, ...]}`. Deliberately
    reports a content hash rather than a release version — the full USLCI zip has no
    intrinsic version field, and bundle filename hashes proved unreliable as content
    markers, so naming a release here would be a guess wearing a fact's clothes.

    An unstamped build (imported before this stamp existed) reports as unknown rather
    than being silently described as anything.
    """
    if not source or not source.get("zips"):
        return "unstamped build — re-run setup/03_import_uslci.py to record its source"
    zips = source["zips"]
    if source.get("mode") == "full_db":
        z = zips[0]
        return f"{z['name']} (sha256 {z['sha256'][:12]}…)"
    return (f"{len(zips)} bundle zip(s)"
            + (f", release hash {', '.join(h[:12] + '…' for h in source['bundle_release_hashes'])}"
               if source.get("bundle_release_hashes") else ""))


def select_supplied_keys(keyed_columns, supply):
    """Reduce a technosphere column index to what THIS result actually draws on.

    `lca.dicts.activity` indexes every column of the technosphere matrix — that is,
    every activity in the loaded databases, whether or not it is reachable from the
    functional unit. Summing completeness over it reports database-wide totals
    wearing a per-result audit's clothes.

    The distinction was invisible while the only build was the per-target bundle
    set (its ~391 activities were, by construction, close to the target's own
    chain). The full-database build makes it plain: a corrugated-product result
    draws on 383 activities out of 1,382 indexed.

    Parameters
    ----------
    keyed_columns : iterable of (key, column_index)
        From `lca.dicts.activity.items()`.
    supply : sequence of float
        The solved supply vector (`lca.supply_array`), indexed by column.

    Returns
    -------
    list
        The keys with non-zero supply, in column order. The test is exact
        inequality, not a tolerance: an unreachable column solves to exactly 0.0,
        while a real contributor is kept however small it is — and however
        negative, since an avoided-product credit supplies a negative amount.
    """
    return [key for key, col in sorted(keyed_columns, key=lambda kc: kc[1])
            if supply[col] != 0]


def summarize_supply_chain_completeness(solved_keys, provenance_by_db,
                                        auditable_dbs, external_dbs):
    """Cross this result's solved supply chain against per-process import diagnostics.

    Parameters
    ----------
    solved_keys : iterable of (db_name, code)
        Every activity this result actually draws on (its supply chain), including
        the target activity itself. From `lca.dicts.activity` reduced to non-zero
        supply by `select_supplied_keys` — passing the unreduced index would make
        every count below a whole-database total.
    provenance_by_db : dict
        `{db_name: {proc_uuid: {"bio_unmatched": int, "tech_unlinked": int,
        "tech_ambiguous": int, ...}}}` — the "processes" block of each auditable
        database's provenance sidecar, keyed by the database it describes.
    auditable_dbs : iterable of str
        Databases imported exchange-by-exchange, so their cutoffs are countable.
        More than one once a custom JSON-LD database sits on a USLCI background:
        both are real joint-solved databases with their own sidecars, and calling
        the background "aggregated" to avoid reading two files would understate the
        audit by describing a joint solve as a pre-solved column.
    external_dbs : iterable of str
        Database names treated as aggregated (pre-solved) background — e.g. the
        electricity baseline. Their internal completeness is not per-exchange
        auditable here, so they are counted but not summed into cutoffs.

    Returns
    -------
    dict
        Per-result completeness. The cutoff counts are summed ONLY over auditable
        activities present in this supply chain, so they describe THIS result — not
        the whole database.
    """
    external  = set(external_dbs)
    auditable = [d for d in auditable_dbs]
    auditable_set = set(auditable)
    solved_keys = list(solved_keys)
    uslci_keys    = [k for k in solved_keys if k[0] in auditable_set]
    external_keys = [k for k in solved_keys if k[0] in external]
    other_keys    = [k for k in solved_keys
                     if k[0] not in auditable_set and k[0] not in external]

    bio_unmatched = tech_unlinked = tech_ambiguous = 0
    with_prov = 0
    missing_prov = []
    for db, code in uslci_keys:
        p = (provenance_by_db.get(db) or {}).get(code)
        if p is None:
            missing_prov.append(code)
            continue
        with_prov += 1
        bio_unmatched  += int(p.get("bio_unmatched", 0) or 0)
        tech_unlinked  += int(p.get("tech_unlinked", 0) or 0)
        tech_ambiguous += int(p.get("tech_ambiguous", 0) or 0)

    # "Fully linked" describes only the auditable part: no unaccounted activities
    # and no cutoffs among them. Use of aggregated background is a separate,
    # expected caveat (reported via uses_aggregated_background), NOT a failure —
    # folding it in here would flag every electricity-touching result.
    fully_linked = (not missing_prov
                    and bio_unmatched == 0
                    and tech_unlinked == 0
                    and tech_ambiguous == 0)

    return {
        "supply_chain_activity_count": len(solved_keys),
        "auditable_databases": auditable,
        "uslci_activity_count": len(uslci_keys),
        "external_background_activity_count": len(external_keys),
        "other_activity_count": len(other_keys),
        "uslci_processes_with_provenance": with_prov,
        "uslci_processes_missing_provenance": len(missing_prov),
        "missing_provenance_uuids": sorted(missing_prov),
        "bio_unmatched_in_supply_chain": bio_unmatched,
        "tech_unlinked_in_supply_chain": tech_unlinked,
        "tech_ambiguous_in_supply_chain": tech_ambiguous,
        "uses_aggregated_background": bool(external_keys),
        "fully_linked": fully_linked,
        "notes": _completeness_notes(missing_prov, external_keys,
                                     bio_unmatched, tech_unlinked, tech_ambiguous),
    }


def _completeness_notes(missing_prov, external_keys,
                        bio_unmatched, tech_unlinked, tech_ambiguous):
    """Human-readable caveats stating what the counts do and don't attest."""
    notes = []
    if missing_prov:
        notes.append(
            f"{len(missing_prov)} USLCI activity(ies) in the supply chain have no entry in "
            f"{DB_PROVENANCE_FILENAME} — the sidecar is stale or was built from a different "
            f"import, so their cutoffs are NOT included in the counts below. Re-run "
            f"setup/03_import_uslci.py to refresh it."
        )
    if external_keys:
        notes.append(
            f"{len(external_keys)} activity(ies) come from aggregated background (e.g. the "
            f"electricity baseline), injected as pre-solved columns; their internal completeness "
            f"is not per-exchange auditable here (the aggregated-vs-joint-solve trade-off)."
        )
    if bio_unmatched or tech_unlinked or tech_ambiguous:
        notes.append(
            f"Within this supply chain's USLCI processes: {bio_unmatched} biosphere exchange(s) "
            f"unmatched (excluded from LCIA), {tech_unlinked} technosphere exchange(s) unlinked "
            f"(cutoff), {tech_ambiguous} left unlinked as ambiguous. Non-zero counts mean the "
            f"result omits part of its inventory."
        )
    if not notes:
        notes.append(
            "No cutoffs detected in this result's USLCI supply chain, and no aggregated "
            "background used."
        )
    return notes


def summarize_electricity_vintage(databases, uslci_db, baseline_db):
    """Which electricity-baseline vintage produced this result, and is the build
    self-consistent?

    A build injects exactly ONE baseline vintage (setup/03b), which stamps it onto
    the baseline DB; setup/03 copies the stamp onto the USLCI DB. The choice is
    load-bearing for any result with grid electricity in its chain — the same
    process computed against a different vintage moves by ~10% on the locked cases
    — so it belongs in the manifest rather than only in the build log.

    Two failure modes are worth naming rather than hiding:
      * `status="inconsistent"` — the two DBs carry DIFFERENT stamps, which means
        03b was re-run without re-running 03. The injected grid is the baseline
        DB's vintage while every consumer still links per the older build, and
        anything reading only the USLCI stamp (the harness's guard included) will
        believe the wrong vintage. Rebuild both.
      * `status="unstamped"` — a build predating the stamp. Not wrong, just
        unattributable; the vintage cannot be reported for this result.

    `databases` is general/04's per-DB metadata dict. Returns a dict, never raises:
    a manifest that can't attest the vintage should say so, not fail the run.
    """
    uslci = (databases.get(uslci_db) or {}).get("electricity_vintage")
    baseline = (databases.get(baseline_db) or {}).get("electricity_vintage")

    if uslci is None and baseline is None:
        return {"vintage": None, "status": "unstamped",
                "note": "This build predates the electricity-vintage stamp, so the "
                        "baseline vintage behind this result cannot be attested. "
                        "Rebuild with setup/03b + setup/03 to record it."}
    if uslci is not None and baseline is not None and uslci != baseline:
        return {"vintage": None, "status": "inconsistent",
                "uslci_db_vintage": uslci, "baseline_db_vintage": baseline,
                "note": f"INCONSISTENT BUILD: '{baseline_db}' was injected at vintage "
                        f"{baseline} but '{uslci_db}' is stamped {uslci}. setup/03b was "
                        f"re-run without re-running setup/03. Results in this state use "
                        f"the {baseline} grid while everything reading the stamp believes "
                        f"{uslci}. Re-run setup/03 before trusting this result."}
    return {"vintage": uslci if uslci is not None else baseline, "status": "ok"}


def build_manifest(*, generated, target, project, databases, electricity_vintage,
                   methods, packages, provenance_source, solved_system,
                   completeness, results):
    """Fold the run's provenance + completeness summary into the final manifest dict.

    Thin by design — all the derived logic lives in
    summarize_supply_chain_completeness(); this just fixes the schema/shape so it is
    stable and testable. Every argument is plain data assembled by general/04.
    """
    return {
        "schema": SCHEMA,
        "generated": generated,
        "target": target,
        "project": project,
        "databases": databases,
        "electricity_vintage": electricity_vintage,
        "methods": methods,
        "packages": packages,
        "provenance_source": provenance_source,
        "solved_system": solved_system,
        "completeness": completeness,
        "results": results,
    }
