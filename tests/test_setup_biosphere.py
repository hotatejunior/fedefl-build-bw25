"""Tests for fedefl_bw25.setup_biosphere — the FEDEFL input guards.

Everything downstream is keyed by FEDEFL UUID, so a duplicate UUID describing two
different substances would silently corrupt whichever flow got written last, and a
short or renamed fetch would produce a biosphere DB whose gaps show up much later
as flows that quietly fail to match. `check_flows` is where those stop; it takes a
plain DataFrame, so it tests without brightway.
"""
import pandas as pd
import pytest

from fedefl_bw25 import setup_biosphere
from fedefl_bw25.setup_biosphere import (build_flow_data, check_flows, flow_type,
                                         parse_context)

COLUMNS = ["Flow UUID", "Flowable", "Unit", "Context", "Class", "CAS No"]


@pytest.fixture(autouse=True)
def small_minimum(monkeypatch):
    """Test against a 3-flow floor rather than FEDEFL's ~300k.

    The guard's job is to reject a short fetch, and that logic is the same at any
    threshold — padding every frame out to the real minimum would only make the
    suite slow.
    """
    monkeypatch.setattr(setup_biosphere, "FEDEFL_MIN_FLOWS", 3)


def _frame(rows):
    return pd.DataFrame(rows, columns=COLUMNS)


def _row(uuid, **kw):
    base = {"Flow UUID": uuid, "Flowable": "carbon dioxide", "Unit": "kg",
            "Context": "air/urban air close to ground", "Class": "Chemicals",
            "CAS No": "124-38-9"}
    base.update(kw)
    return base


def test_clean_frame_passes_with_no_notes():
    rows = [_row("a"), _row("b", Flowable="methane"), _row("c", Flowable="nitrous oxide")]
    assert check_flows(_frame(rows)) == []


def test_renamed_column_is_an_error():
    # .get() on Context would silently default to "" and mis-categorize every flow.
    df = _frame([_row("a")]).rename(columns={"Context": "Compartment"})
    with pytest.raises(RuntimeError, match="missing expected columns"):
        check_flows(df)


def test_empty_fetch_is_an_error():
    with pytest.raises(RuntimeError, match="zero flows"):
        check_flows(_frame([]))


def test_partial_fetch_is_an_error():
    with pytest.raises(RuntimeError, match="Partial data"):
        check_flows(_frame([_row("a")]))


def test_identical_duplicate_uuids_are_deduplicated_with_a_note():
    rows = [_row("a"), _row("a"), _row("b"), _row("c")]
    notes = check_flows(_frame(rows))
    assert notes and "1 UUID(s) had identical duplicate rows" in notes[0]


def test_conflicting_duplicate_uuids_are_an_error():
    # Two different substances sharing a UUID: whichever wrote last would win, and
    # every result keyed to that UUID would silently be the wrong substance.
    rows = [_row("a"), _row("a", Flowable="methane", **{"CAS No": "74-82-8"}),
            _row("b"), _row("c")]
    with pytest.raises(RuntimeError, match="conflicting substance data"):
        check_flows(_frame(rows))


def test_context_becomes_a_category_tuple():
    assert parse_context("air/urban air close to ground") == ("air", "urban air close to ground")
    assert parse_context("") == ("unspecified",)
    assert parse_context(float("nan")) == ("unspecified",)


def test_resources_are_classified_apart_from_emissions():
    assert flow_type({"Context": "resource/ground", "Class": "Chemicals"}) == "natural resource"
    assert flow_type({"Context": "air", "Class": "Land Resource"}) == "natural resource"
    assert flow_type({"Context": "air", "Class": "Chemicals"}) == "emission"


def test_flow_data_is_keyed_by_uuid_and_carries_the_code():
    data = build_flow_data(_frame([_row("uuid-1")]))
    key = next(iter(data))
    assert key[1] == "uuid-1"
    assert data[key]["code"] == "uuid-1"
    assert data[key]["categories"] == ("air", "urban air close to ground")
    assert data[key]["type"] == "emission"
    assert data[key]["CAS number"] == "124-38-9"
