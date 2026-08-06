"""Tests for fedefl_bw25.setup_uslci — the importer's decision points.

These all lived inside one 955-line function and were reachable only by building a
database. Several encode a bug that actually shipped: the Mg/mg case collision (a
silent 1e9 error), the parenthesised location that crashed brightway's geomapping,
and version-based precedence replacing mtime (ledger #5). None of them needs
brightway, so they test directly.
"""
import json
import zipfile

import pytest

from fedefl_bw25.setup_uslci import (ExternalProviders, ProviderResolver,
                                     UnitNormalizer, _parse_version, _proc_precedence,
                                     activity_location, build_jobs, discover_sources,
                                     index_reference_flows, load_sources,
                                     source_identity)

MASS_FP = "fp-mass"
VOL_FP  = "fp-vol"

# One flow measured in both volume (its reference) and mass — diesel's real shape.
CONV = {
    "diesel": {"name": "Diesel", "flow_type": "PRODUCT_FLOW",
               "ref_fp_uuid": VOL_FP, "ref_fp_name": "Volume", "ref_unit": "m3",
               "conversions": {MASS_FP: {"name": "Mass", "ref_unit": "kg",
                                         "factor": 849.0}}},
}


# -----------------------------------------------------------------------------
# UnitNormalizer
# -----------------------------------------------------------------------------
def test_within_property_conversion_reaches_the_reference_unit():
    n = UnitNormalizer({})
    assert n(1500, "g", MASS_FP, "any-flow", cross_property=False) == (1.5, "g")


def test_cross_property_conversion_uses_the_flows_own_density():
    # 849 kg of diesel is 1 m3 of it — the factor is per reference unit.
    n = UnitNormalizer(CONV)
    amount, unit = n(849.0, "kg", MASS_FP, "diesel")
    assert amount == pytest.approx(1.0)
    assert unit == "m3"


def test_an_amount_already_in_the_reference_property_only_converts_units():
    n = UnitNormalizer(CONV)
    assert n(1000, "l", VOL_FP, "diesel") == (1.0, "m3")


def test_biosphere_amounts_never_cross_flow_properties():
    # CO2 stays in kg; routing it through a density factor would be nonsense.
    n = UnitNormalizer(CONV)
    assert n(2.0, "kg", MASS_FP, "diesel", cross_property=False) == (2.0, "kg")


def test_megagram_is_not_milligram():
    # "Mg" (tonne) vs "mg" (milligram) is the one case collision in USLCI's unit
    # universe; a case-insensitive lookup alone is a silent 1e9 error.
    n = UnitNormalizer({})
    assert n(1, "Mg", MASS_FP, "f", cross_property=False) == (1e3, "Mg")
    assert n(1, "mg", MASS_FP, "f", cross_property=False) == (1e-6, "mg")


def test_unknown_units_pass_through_but_are_recorded():
    # Passing through is only safe because the build refuses to write afterwards.
    n = UnitNormalizer({})
    assert n(5.0, "furlong", MASS_FP, "flow-1") == (5.0, "furlong")
    assert n(2.0, "furlong", MASS_FP, "flow-2") == (2.0, "furlong")
    assert n.unknown == {"furlong": {"flow-1", "flow-2"}}


def test_unknown_unit_detail_names_the_flows_carrying_it():
    n = UnitNormalizer({})
    n(1.0, "furlong", MASS_FP, "flow-1")
    detail = n.unknown_detail({"flow-1": {"name": "Weird stuff"}})
    assert "furlong" in detail and "Weird stuff" in detail and "flow-1" in detail


def test_a_flow_missing_from_the_table_still_gets_its_unit_converted():
    n = UnitNormalizer({})
    assert n(1000, "g", MASS_FP, "not-in-table") == (1.0, "g")


# -----------------------------------------------------------------------------
# Process precedence (ledger #5 — deterministic across machines, unlike mtime)
# -----------------------------------------------------------------------------
def test_version_strings_sort_numerically_not_lexically():
    assert _parse_version("00.01.014") == (0, 1, 14)
    assert _parse_version("00.01.002") < _parse_version("00.01.014")
    assert _parse_version("") == ()          # missing sorts lowest
    assert _parse_version("1.x.3") == (1, 0, 3)


def test_last_change_breaks_a_version_tie():
    same = {"version": "01.00.000"}
    older = _proc_precedence({**same, "lastChange": "2025-01-01T00:00:00Z"})
    newer = _proc_precedence({**same, "lastChange": "2026-01-01T00:00:00Z"})
    assert newer > older


def _proc(uuid, exchanges=None, **kw):
    return {"@id": uuid, "name": f"proc {uuid}",
            "exchanges": exchanges if exchanges is not None else [_ref_out(f"flow-{uuid}")],
            **kw}


def _ref_out(flow_uuid, flow_type="PRODUCT_FLOW"):
    return {"isInput": False, "isQuantitativeReference": True,
            "flow": {"@id": flow_uuid, "name": "ref", "flowType": flow_type}}


def _zip(tmp_path, processes, name="a.zip"):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("openlca.json", "{}")
        for p in processes:
            z.writestr(f"processes/{p['@id']}.json", json.dumps(p))
    return path


def test_the_newer_copy_of_a_process_wins_across_zips(tmp_path):
    old = _zip(tmp_path, [_proc("A", version="00.01.002", name="old")], "1.zip")
    new = _zip(tmp_path, [_proc("A", version="00.01.014", name="new")], "2.zip")

    # Newest wins whichever order the zips are read in — that is the whole point.
    for order in ([old, new], [new, old]):
        processes, _flows, resolved = load_sources(order)
        assert processes["A"]["version"] == "00.01.014"
        assert resolved == {"A"}


def test_identical_duplicates_are_not_reported_as_a_version_conflict(tmp_path):
    a = _zip(tmp_path, [_proc("A", version="1.0.0")], "1.zip")
    b = _zip(tmp_path, [_proc("A", version="1.0.0")], "2.zip")
    _processes, _flows, resolved = load_sources([a, b])
    assert resolved == set()


# -----------------------------------------------------------------------------
# Source discovery
# -----------------------------------------------------------------------------
UUID_NAME = "0aaf1e13-5d80-37f9-b7bb-81a6b8965c71_" + "a" * 40 + ".zip"


def test_bundles_are_found_by_their_lca_commons_filename(tmp_path):
    _zip(tmp_path, [_proc("A")], UUID_NAME)
    found = discover_sources(bundle_dir=tmp_path, full_db=False, conv_meta={},
                             db_name="uslci-subset")
    assert [p.name for p in found] == [UUID_NAME]


def test_a_renamed_bundle_is_skipped_loudly(tmp_path):
    # Silently ignoring it is the failure mode: the operator thinks their process
    # imported when it didn't, and only finds out at run time.
    _zip(tmp_path, [_proc("A")], UUID_NAME)
    _zip(tmp_path, [_proc("B")], "petroleum.zip")
    messages = []
    found = discover_sources(bundle_dir=tmp_path, full_db=False, conv_meta={},
                             db_name="uslci-subset", log=messages.append)
    assert [p.name for p in found] == [UUID_NAME]
    assert any("petroleum.zip" in m and "IGNORED" in m for m in messages)


def test_no_bundles_at_all_is_an_error(tmp_path):
    with pytest.raises(RuntimeError, match="No process zip files found"):
        discover_sources(bundle_dir=tmp_path, full_db=False, conv_meta={},
                         db_name="uslci-subset")


def test_full_db_mode_reads_the_zip_named_in_the_conversion_table(tmp_path):
    _zip(tmp_path, [_proc("A")], "full.zip")
    found = discover_sources(bundle_dir=tmp_path, full_db=True,
                             conv_meta={"source_zip": "full.zip"}, db_name="uslci-full")
    assert [p.name for p in found] == ["full.zip"]


def test_full_db_mode_without_the_zip_is_an_error(tmp_path):
    with pytest.raises(RuntimeError, match="full USLCI zip is not present"):
        discover_sources(bundle_dir=tmp_path, full_db=True,
                         conv_meta={"source_zip": "full.zip"}, db_name="uslci-full")


def test_source_identity_records_content_hashes_and_release_suffixes(tmp_path):
    z = _zip(tmp_path, [_proc("A")], UUID_NAME)
    identity = source_identity([z], full_db=False)
    assert identity["mode"] == "bundles"
    assert identity["zips"][0]["name"] == UUID_NAME
    assert len(identity["zips"][0]["sha256"]) == 64
    assert identity["bundle_release_hashes"] == ["a" * 40]


# -----------------------------------------------------------------------------
# Reference-flow index
# -----------------------------------------------------------------------------
def test_several_processes_may_supply_one_reference_flow():
    # 4 regional crude-oil variants all supply generic "Crude oil" — legitimate,
    # which is why the index holds a set and resolution needs the provider hint.
    procs = {"A": _proc("A", [_ref_out("crude")]), "B": _proc("B", [_ref_out("crude")])}
    assert index_reference_flows(procs) == {"crude": {"A", "B"}}


def test_a_waste_treatment_sink_indexes_its_input_reference():
    sink = {"@id": "T", "name": "landfill", "exchanges": [
        {"isInput": True, "isQuantitativeReference": True,
         "flow": {"@id": "msw", "flowType": "WASTE_FLOW"}}]}
    assert index_reference_flows({"T": sink}) == {"msw": {"T"}}


def test_two_reference_exchanges_is_a_malformed_export():
    bad = _proc("A", [_ref_out("f1"), _ref_out("f2")])
    with pytest.raises(RuntimeError, match="expected exactly 1"):
        index_reference_flows({"A": bad})


# -----------------------------------------------------------------------------
# ProviderResolver
# -----------------------------------------------------------------------------
def _resolver(all_processes=None, flow_to_process=None, external=None):
    all_processes = all_processes if all_processes is not None else {}
    return ProviderResolver("uslci-subset", all_processes,
                            flow_to_process if flow_to_process is not None else {},
                            external or ExternalProviders())


def test_the_default_provider_hint_wins_over_the_flow_index():
    # Two producers of "crude"; the hint is the only thing that disambiguates.
    r = _resolver(all_processes={"A": {}, "B": {}}, flow_to_process={"crude": {"A", "B"}})
    exc = {"defaultProvider": {"@id": "B"}}
    assert r.resolve(exc, "crude") == ("uslci-subset", "B", False)


def test_a_sole_producer_resolves_without_any_hint():
    r = _resolver(flow_to_process={"crude": {"A"}})
    assert r.resolve({}, "crude") == ("uslci-subset", "A", False)


def test_an_external_provider_resolves_to_the_baseline_database():
    ext = ExternalProviders(uuids={"GRID"}, flow_to_process={"elec": {"GRID"}},
                            names={"GRID": "US average grid"}, vintage="2026")
    r = _resolver(external=ext)
    assert r.resolve({"defaultProvider": {"@id": "GRID"}}, "elec") == \
        ("electricity-baseline", "GRID", False)


def test_a_stale_hint_uuid_still_resolves_by_the_provider_name():
    # The grid node keeps its name but changes UUID between baseline vintages, so a
    # bundle's hardcoded hint UUID can miss while the name still identifies it.
    ext = ExternalProviders(uuids={"NEW"}, flow_to_process={"elec": {"NEW", "OTHER"}},
                            names={"NEW": "US average grid", "OTHER": "Texas grid"})
    r = _resolver(external=ext)
    exc = {"defaultProvider": {"@id": "RETIRED-UUID", "name": "US average grid"}}
    assert r.resolve(exc, "elec") == ("electricity-baseline", "NEW", False)


def test_several_candidates_and_no_usable_hint_is_left_unlinked():
    # Guessing here would silently attribute the wrong supply chain.
    r = _resolver(flow_to_process={"crude": {"A", "B"}})
    assert r.resolve({"defaultProvider": {"@id": "GONE"}}, "crude") == (None, None, True)


def test_an_unknown_flow_is_unlinked_but_not_ambiguous():
    # A genuine cutoff — reported separately from an ambiguity, which is a build fault.
    assert _resolver().resolve({}, "nothing-supplies-this") == (None, None, False)


# -----------------------------------------------------------------------------
# Location
# -----------------------------------------------------------------------------
def test_known_locations_map_to_codes():
    assert activity_location({"location": {"name": "United States of America (the)"}}) == "US"
    assert activity_location({"location": {"name": "Northern America"}}) == "RNA"


def test_a_parenthesised_country_is_made_eval_safe():
    # brightway's geomapping eval()s any location containing "(", so an unmapped
    # country name like this one crashed .write() with a SyntaxError.
    got = activity_location({"location": {"name": "Congo (the Democratic Republic of the)"}})
    assert got == "Congo" and "(" not in got


def test_a_missing_location_defaults_to_global():
    assert activity_location({}) == "GLO"
    assert activity_location({"location": None}) == "GLO"
    assert activity_location({"location": {"name": ""}}) == "GLO"


# -----------------------------------------------------------------------------
# Build jobs
# -----------------------------------------------------------------------------
def test_causal_coproducts_get_their_own_job_after_the_reference_ones():
    from fedefl_bw25.setup_uslci import AllocationPlan
    plan = AllocationPlan(coproduct_info={("A", "co2"): {}, ("A", "co1"): {}})
    jobs = build_jobs({"A": {}, "B": {}}, plan)
    assert jobs[:2] == [("A", None), ("B", None)]
    assert jobs[2:] == [("A", "co1"), ("A", "co2")]   # sorted -> deterministic
