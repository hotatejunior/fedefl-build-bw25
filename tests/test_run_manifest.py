"""Pure-unit tests for general/run_manifest.py — the per-run audit manifest logic.

No brightway needed: summarize_supply_chain_completeness() takes the solved-system
activity keys and the provenance dict as plain data, so we pass small fakes and
exercise the per-result completeness crossing directly. This mirrors how
general/04 calls it (solved keys from lca.dicts.activity, provenance from
uslci_db_provenance.json).
"""
import run_manifest as rm

USLCI = "uslci"
ELEC = "electricity-baseline"

# proc "A" is clean; "B" has cutoffs; "C" exists but is only referenced when we
# want a missing-provenance case (we simply omit it from the dict there).
PROV = {
    "A": {"name": "clean proc", "bio_unmatched": 0, "tech_unlinked": 0, "tech_ambiguous": 0},
    "B": {"name": "leaky proc", "bio_unmatched": 2, "tech_unlinked": 1, "tech_ambiguous": 3},
}


def _summ(solved_keys, prov=PROV, external=(ELEC,)):
    return rm.summarize_supply_chain_completeness(solved_keys, prov, USLCI, external)


def test_clean_single_process_is_fully_linked():
    s = _summ([(USLCI, "A")])
    assert s["fully_linked"] is True
    assert s["uslci_activity_count"] == 1
    assert s["uslci_processes_with_provenance"] == 1
    assert s["bio_unmatched_in_supply_chain"] == 0
    assert s["tech_unlinked_in_supply_chain"] == 0
    assert s["uses_aggregated_background"] is False


def test_cutoffs_are_summed_over_supply_chain():
    s = _summ([(USLCI, "A"), (USLCI, "B")])
    assert s["fully_linked"] is False
    assert s["bio_unmatched_in_supply_chain"] == 2
    assert s["tech_unlinked_in_supply_chain"] == 1
    assert s["tech_ambiguous_in_supply_chain"] == 3
    assert s["uslci_processes_with_provenance"] == 2


def test_aggregated_background_counted_not_penalized():
    # A clean USLCI process that draws the electricity baseline is still
    # "fully_linked" (no USLCI cutoffs); background is flagged separately.
    s = _summ([(USLCI, "A"), (ELEC, "grid-mix")])
    assert s["fully_linked"] is True
    assert s["uses_aggregated_background"] is True
    assert s["external_background_activity_count"] == 1
    assert s["uslci_activity_count"] == 1
    assert any("aggregated background" in n for n in s["notes"])


def test_missing_provenance_breaks_full_linkage_and_is_reported():
    # "C" is in the supply chain but absent from the sidecar (stale sidecar).
    s = _summ([(USLCI, "A"), (USLCI, "C")])
    assert s["fully_linked"] is False
    assert s["uslci_processes_missing_provenance"] == 1
    assert s["missing_provenance_uuids"] == ["C"]
    # Its (unknown) cutoffs are NOT silently counted as zero-clean.
    assert s["uslci_processes_with_provenance"] == 1
    assert any("stale" in n or "no entry" in n for n in s["notes"])


def test_foreground_activity_bucketed_as_other():
    s = _summ([("foreground", "fg-target"), (USLCI, "A")])
    assert s["other_activity_count"] == 1
    assert s["uslci_activity_count"] == 1
    assert s["supply_chain_activity_count"] == 2


def test_clean_result_has_reassuring_note():
    s = _summ([(USLCI, "A")])
    assert any("No cutoffs detected" in n for n in s["notes"])


def test_build_manifest_shape_is_stable():
    s = _summ([(USLCI, "A")])
    m = rm.build_manifest(
        generated="2026-07-09T00:00:00Z",
        target={"uuid": "A", "name": "clean proc"},
        project="fedefl-build-bw25",
        databases={USLCI: {"activity_count": 1}},
        electricity_vintage={"vintage": "2025", "status": "ok"},
        methods=[["TRACI", "2.2", "Global warming"]],
        packages={"bw2calc": "2.5.0"},
        provenance_source={"available": True},
        solved_system={"activity_count": 1},
        completeness=s,
        results=[{"method": "Global warming", "score": 1.0, "unit": "kg CO2 eq"}],
    )
    assert m["schema"] == rm.SCHEMA
    assert set(m) == {
        "schema", "generated", "target", "project", "databases", "electricity_vintage",
        "methods", "packages", "provenance_source", "solved_system", "completeness",
        "results",
    }
    assert m["completeness"]["fully_linked"] is True


# =============================================================================
# electricity-vintage attestation
#
# A build injects exactly one baseline vintage and it materially moves any result
# with grid electricity upstream — recomputing the locked cases against the wrong
# vintage shifts them ~10% (corn 0.887-1.025, cement 0.929-1.052), which reads as
# a bad number rather than a bad build. The manifest has to say which grid it was.
# =============================================================================
USLCI_DB = "uslci-subset"
ELEC_DB = "electricity-baseline"


def _vint(uslci=..., baseline=...):
    dbs = {}
    if uslci is not ...:
        dbs[USLCI_DB] = {"electricity_vintage": uslci}
    if baseline is not ...:
        dbs[ELEC_DB] = {"electricity_vintage": baseline}
    return rm.summarize_electricity_vintage(dbs, USLCI_DB, ELEC_DB)


def test_matching_stamps_report_the_vintage():
    v = _vint(uslci="2026", baseline="2026")
    assert v == {"vintage": "2026", "status": "ok"}


def test_unstamped_build_is_reported_as_unattestable_not_guessed():
    v = _vint(uslci=None, baseline=None)
    assert v["vintage"] is None and v["status"] == "unstamped"
    assert "cannot be attested" in v["note"]


def test_missing_databases_are_treated_as_unstamped():
    assert rm.summarize_electricity_vintage({}, USLCI_DB, ELEC_DB)["status"] == "unstamped"


def test_disagreeing_stamps_are_flagged_inconsistent():
    # 03b re-run at a new vintage without re-running 03: the injected grid is
    # 2026 while everything reading the stamp still believes 2025.
    v = _vint(uslci="2025", baseline="2026")
    assert v["status"] == "inconsistent"
    assert v["vintage"] is None, "must not pick a side when the build disagrees"
    assert v["uslci_db_vintage"] == "2025" and v["baseline_db_vintage"] == "2026"
    assert "setup/03" in v["note"]


def test_one_sided_stamp_still_reports_the_known_vintage():
    assert _vint(uslci="2025", baseline=None)["vintage"] == "2025"
    assert _vint(uslci=None, baseline="2026")["vintage"] == "2026"


def test_manifest_carries_the_vintage_block():
    m = rm.build_manifest(
        generated="2026-07-23T00:00:00Z", target={}, project="p", databases={},
        electricity_vintage={"vintage": "2026", "status": "ok"},
        methods=[], packages={}, provenance_source=None,
        solved_system={}, completeness={}, results=[])
    assert m["electricity_vintage"] == {"vintage": "2026", "status": "ok"}
    assert m["schema"] == "validation-manifest/2"
