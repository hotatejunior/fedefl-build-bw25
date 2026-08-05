"""Tests for fedefl_bw25.setup_traci — the CF-source hash pin and unit resolution.

The hash pin is what makes an upstream EPA change to the TRACI 2.2 CF files a
build failure rather than a quiet shift in every published number (ledger #4), so
it is worth a test that it actually stops. Unit resolution matters for the same
reason in miniature: two indicator units for one category means one of them is
wrong, and picking silently would label every score in that category incorrectly.
"""
import pytest

from fedefl_bw25 import setup_traci
from fedefl_bw25.setup_traci import (TRACI_BASE_CACHE_NAME, enforce_cf_hashes,
                                     file_sha256, _resolve_unit)
import pandas as pd

META = {"eutro_file": "eutro.xlsx"}


@pytest.fixture
def cached(tmp_path, monkeypatch):
    """Stand in for lciafmt's cache with files we control the contents of."""
    def write(name, text):
        p = tmp_path / name
        p.write_text(text)
        return p

    monkeypatch.setattr(setup_traci.lciafmt_cache, "get_path",
                        lambda name: str(tmp_path / name))
    return write


def test_matching_hashes_pass(cached, monkeypatch):
    base = cached(TRACI_BASE_CACHE_NAME, "base")
    eutro = cached("eutro.xlsx", "eutro")
    monkeypatch.setattr(setup_traci, "TRACI_BASE_SHA256", file_sha256(base))
    monkeypatch.setattr(setup_traci, "TRACI_EUTRO_SHA256", file_sha256(eutro))
    enforce_cf_hashes(META)      # no raise


def test_changed_cf_file_stops_the_build(cached, monkeypatch):
    base = cached(TRACI_BASE_CACHE_NAME, "base")
    cached("eutro.xlsx", "eutro")
    monkeypatch.setattr(setup_traci, "TRACI_BASE_SHA256", file_sha256(base))
    monkeypatch.setattr(setup_traci, "TRACI_EUTRO_SHA256", "0" * 64)
    with pytest.raises(RuntimeError, match="hash mismatch"):
        enforce_cf_hashes(META)


def test_missing_cf_file_stops_the_build(cached, monkeypatch):
    base = cached(TRACI_BASE_CACHE_NAME, "base")   # eutro never written
    monkeypatch.setattr(setup_traci, "TRACI_BASE_SHA256", file_sha256(base))
    with pytest.raises(RuntimeError, match="not found in the lciafmt cache"):
        enforce_cf_hashes(META)


def _group(units):
    return pd.DataFrame({"Indicator unit": units})


def test_single_indicator_unit_is_used():
    assert _resolve_unit("Global warming", _group(["kg CO2 eq"] * 3), {}, lambda *a: None) \
        == "kg CO2 eq"


def test_conflicting_units_raise_rather_than_pick():
    with pytest.raises(RuntimeError, match="Multiple indicator units"):
        _resolve_unit("Eutrophication (Marine)", _group(["kg N eq", "kg P eq"]),
                      {}, lambda *a: None)


def test_an_override_resolves_a_unit_conflict():
    unit = _resolve_unit("Eutrophication (Marine)", _group(["kg N eq", "kg P eq"]),
                         {"Eutrophication (Marine)": "kg N eq"}, lambda *a: None)
    assert unit == "kg N eq"
