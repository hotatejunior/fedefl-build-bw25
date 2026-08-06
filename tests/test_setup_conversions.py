"""Tests for fedefl_bw25.setup_conversions — flow-property conversion factors.

This table is what lets an exchange cross flow properties (kg of a flow whose
supplier outputs m3), so a wrong factor here is a wrong number everywhere
downstream — and one that looks entirely plausible. The module is brightway-free
and parses zips, so it tests against synthetic USLCI-shaped ones.
"""
import json
import zipfile

import pytest

from fedefl_bw25.setup_conversions import (build_table, describe_conversions,
                                           parse_flow, write_conversion_table,
                                           ConversionTable)

MASS   = {"@id": "fp-mass", "name": "Mass", "refUnit": "kg"}
VOLUME = {"@id": "fp-vol", "name": "Volume", "refUnit": "m3"}
ENERGY = {"@id": "fp-nrg", "name": "Energy", "refUnit": "MJ"}


def _flow(uuid, fps, name="a flow", flow_type="PRODUCT_FLOW"):
    return {"@id": uuid, "name": name, "flowType": flow_type, "flowProperties": fps}


def _fp(prop, factor, is_ref=False):
    e = {"flowProperty": prop, "conversionFactor": factor}
    if is_ref:
        e["isRefFlowProperty"] = True
    return e


def _zip(tmp_path, flows, name="src.zip"):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as z:
        for f in flows:
            z.writestr(f"flows/{f['@id']}.json", json.dumps(f))
    return path


def test_reference_property_and_conversions_are_read():
    # Diesel's real shape: volume is the reference, mass and energy convert off it.
    entry = parse_flow(_flow("diesel", [_fp(VOLUME, 1.0, is_ref=True),
                                        _fp(MASS, 849.0),
                                        _fp(ENERGY, 38462.0)], name="Diesel"))
    assert entry["ref_fp_uuid"] == "fp-vol"
    assert entry["ref_unit"] == "m3"
    assert entry["conversions"]["fp-mass"] == {"name": "Mass", "ref_unit": "kg",
                                               "factor": 849.0}
    # The reference property is never listed as a conversion off itself.
    assert "fp-vol" not in entry["conversions"]


def test_reference_is_inferred_from_a_lone_unit_factor():
    # Some flow files omit isRefFlowProperty; exactly one factor of 1.0 identifies it.
    entry = parse_flow(_flow("f", [_fp(MASS, 1.0), _fp(VOLUME, 0.001)]))
    assert entry["ref_fp_uuid"] == "fp-mass"


def test_two_candidate_references_is_an_error_not_a_guess():
    # Picking either one silently would scale every crossing exchange by the wrong
    # factor, so ambiguity has to stop the build.
    with pytest.raises(RuntimeError, match="Ambiguous reference flow property"):
        parse_flow(_flow("f", [_fp(MASS, 1.0), _fp(VOLUME, 1.0)]))


def test_reference_factor_other_than_one_is_an_error():
    with pytest.raises(RuntimeError, match="expected 1.0"):
        parse_flow(_flow("f", [_fp(MASS, 2.0, is_ref=True), _fp(VOLUME, 0.001)]))


def test_flow_without_properties_is_skipped_not_defaulted():
    assert parse_flow(_flow("f", [])) is None
    assert parse_flow({"name": "no id"}) is None


def test_supplement_fills_gaps_but_never_overrides(tmp_path):
    # The full zip is authoritative; bundle zips only add flows it doesn't have.
    full = _zip(tmp_path, [_flow("shared", [_fp(MASS, 1.0, is_ref=True)])], "full.zip")
    sup  = _zip(tmp_path, [_flow("shared", [_fp(VOLUME, 1.0, is_ref=True)]),
                           _flow("extra",  [_fp(MASS, 1.0, is_ref=True)])], "sup.zip")

    table, added = build_table(full, supplement_zips=[sup])
    assert added == 1
    assert set(table) == {"shared", "extra"}
    assert table["shared"]["ref_fp_uuid"] == "fp-mass"   # full zip won


def test_describe_conversions_reads_meta_and_tolerates_absence(tmp_path):
    path = tmp_path / "table.json"
    assert describe_conversions(path) is None

    build = ConversionTable(table={"f": {}, "_meta": {"source_zip": "x.zip"}},
                            flow_count=1, multi_property_count=0, source_zip="x.zip",
                            source_zip_sha256="abc", source_zip_size=1,
                            supplement_count=0, supplement_added=0)
    write_conversion_table(build, path)
    assert describe_conversions(path)["source_zip"] == "x.zip"
