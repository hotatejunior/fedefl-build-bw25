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
        methods=[["TRACI", "2.2", "Global warming"]],
        packages={"bw2calc": "2.5.0"},
        provenance_source={"available": True},
        solved_system={"activity_count": 1},
        completeness=s,
        results=[{"method": "Global warming", "score": 1.0, "unit": "kg CO2 eq"}],
    )
    assert m["schema"] == rm.SCHEMA
    assert set(m) == {
        "schema", "generated", "target", "project", "databases", "methods",
        "packages", "provenance_source", "solved_system", "completeness", "results",
    }
    assert m["completeness"]["fully_linked"] is True
