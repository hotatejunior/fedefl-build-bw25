"""Tests for fedefl_bw25.run — the parts that need no built database.

The point of extracting general/04 into `run_lca()` was that its behaviour became
reachable at all. The result object and the CSV writers are pure once an `LcaRun`
exists, so they test directly; the solve itself still needs a brightway project and
is covered by tests/test_validation_cement.py.
"""
import csv

import pytest

from fedefl_bw25.run import (LcaRun, write_results_csv, write_contributions_csv,
                             RESULT_FIELDS)


def _run(scenario="A", gwp=1.0):
    return LcaRun(
        target={"uuid": "u", "name": scenario},
        scenario=scenario,
        functional_unit="1 kg",
        database="uslci-subset",
        results=[
            {"scenario": scenario, "method": "Global warming", "score": gwp,
             "unit": "kg CO2 eq", "functional_unit": "1 kg"},
            {"scenario": scenario, "method": "Acidification", "score": 0.5,
             "unit": "kg SO2 eq", "functional_unit": "1 kg"},
        ],
        contributions=[
            {"scenario": scenario, "method": "Global warming", "process_name": "p",
             "process_uuid": "x", "db": "uslci-subset", "contribution_score": gwp,
             "unit": "kg CO2 eq", "functional_unit": "1 kg"},
        ],
        electricity_vintage={"status": "ok", "vintage": "2025"},
        manifest=None,
    )


def test_score_looks_up_by_category_name():
    assert _run(gwp=2.5).score("Global warming") == 2.5


def test_score_on_unknown_category_names_what_is_available():
    with pytest.raises(KeyError) as e:
        _run().score("Nonexistent")
    assert "Global warming" in str(e.value)


def test_as_dict_maps_every_category():
    assert _run(gwp=3.0).as_dict() == {"Global warming": 3.0, "Acidification": 0.5}


def test_write_results_overwrites_by_default(tmp_path):
    p = tmp_path / "r.csv"
    write_results_csv(_run("A"), p)
    n = write_results_csv(_run("B"), p)
    rows = list(csv.DictReader(open(p)))
    assert n == 1 and {r["scenario"] for r in rows} == {"B"}
    assert list(rows[0]) == RESULT_FIELDS


def test_append_accumulates_scenarios(tmp_path):
    p = tmp_path / "r.csv"
    write_results_csv(_run("A"), p)
    n = write_results_csv(_run("B"), p, append=True)
    rows = list(csv.DictReader(open(p)))
    assert n == 2 and {r["scenario"] for r in rows} == {"A", "B"} and len(rows) == 4


def test_appending_the_same_scenario_replaces_rather_than_duplicates(tmp_path):
    # A sweep is iterative; silent duplicates would double-count in any chart
    # that groups by scenario.
    p = tmp_path / "r.csv"
    write_results_csv(_run("A", gwp=1.0), p)
    write_results_csv(_run("B"), p, append=True)
    n = write_results_csv(_run("A", gwp=9.0), p, append=True)
    rows = list(csv.DictReader(open(p)))
    assert n == 2 and len(rows) == 4
    a_gwp = [r["score"] for r in rows
             if r["scenario"] == "A" and r["method"] == "Global warming"]
    assert a_gwp == ["9.0"]


def test_append_to_a_missing_file_just_creates_it(tmp_path):
    p = tmp_path / "nested" / "r.csv"
    assert write_results_csv(_run("A"), p, append=True) == 1
    assert p.exists()


def test_contributions_writer_reports_row_count(tmp_path):
    p = tmp_path / "c.csv"
    assert write_contributions_csv(_run(), p) == 1


def test_contributions_writer_skips_when_empty(tmp_path):
    r = _run()
    r.contributions = []
    p = tmp_path / "c.csv"
    assert write_contributions_csv(r, p) == 0
    assert not p.exists()
