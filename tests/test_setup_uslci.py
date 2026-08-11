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

from fedefl_bw25.setup_uslci import (BuildTally, ExternalProviders, ProviderResolver,
                                     UnitNormalizer, _parse_version, _proc_precedence,
                                     activity_location, build_jobs,
                                     check_background_links, check_jsonld_layout,
                                     check_uuid_collisions, discover_sources,
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
    ext = ExternalProviders.single("electricity-baseline", uuids={"GRID"},
                                   flow_to_process={"elec": {"GRID"}},
                                   names={"GRID": "US average grid"}, vintage="2026")
    r = _resolver(external=ext)
    assert r.resolve({"defaultProvider": {"@id": "GRID"}}, "elec") == \
        ("electricity-baseline", "GRID", False)


def test_a_stale_hint_uuid_still_resolves_by_the_provider_name():
    # The grid node keeps its name but changes UUID between baseline vintages, so a
    # bundle's hardcoded hint UUID can miss while the name still identifies it.
    ext = ExternalProviders.single("electricity-baseline", uuids={"NEW"},
                                   flow_to_process={"elec": {"NEW", "OTHER"}},
                                   names={"NEW": "US average grid", "OTHER": "Texas grid"})
    r = _resolver(external=ext)
    exc = {"defaultProvider": {"@id": "RETIRED-UUID", "name": "US average grid"}}
    assert r.resolve(exc, "elec") == ("electricity-baseline", "NEW", False)


# -----------------------------------------------------------------------------
# Multi-background resolution — a custom JSON-LD import on a USLCI background
# -----------------------------------------------------------------------------
def _two_backgrounds():
    """A USLCI build and the electricity baseline, in precedence order."""
    return ExternalProviders(
        databases=["uslci-full", "electricity-baseline"],
        db_of={"CRUDE": "uslci-full", "GRID": "electricity-baseline"},
        names={"CRUDE": "Crude oil, at extraction", "GRID": "US average grid"},
        flow_to_process={"elec": {("electricity-baseline", "GRID")}},
    )


def test_a_hint_resolves_to_whichever_background_holds_it():
    # The whole point of the custom-import path: an author's defaultProvider naming
    # a USLCI process must land in the USLCI database, not the only background the
    # resolver used to know about.
    r = _resolver(external=_two_backgrounds())
    assert r.resolve({"defaultProvider": {"@id": "CRUDE"}}, "crude") == \
        ("uslci-full", "CRUDE", False)
    assert r.resolve({"defaultProvider": {"@id": "GRID"}}, "elec") == \
        ("electricity-baseline", "GRID", False)


def test_the_local_dataset_takes_precedence_over_a_background():
    # Documents the shadowing that check_uuid_collisions exists to refuse: with the
    # same UUID on both sides the local copy wins, silently.
    r = _resolver(all_processes={"CRUDE": {}}, external=_two_backgrounds())
    assert r.resolve({"defaultProvider": {"@id": "CRUDE"}}, "crude") == \
        ("uslci-subset", "CRUDE", False)


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


# -----------------------------------------------------------------------------
# Custom JSON-LD guards
# -----------------------------------------------------------------------------
# The custom-import path trades USLCI's tolerance for cutoffs for a hard contract:
# every background link names its provider, and no process reuses a background's
# UUID. Both failures are silent without these — a cut link lowers every result,
# and a reused UUID shadows the process it was meant to reference.

def _zip_with(tmp_path, names):
    path = tmp_path / "src.zip"
    with zipfile.ZipFile(path, "w") as z:
        for n in names:
            z.writestr(n, "{}")
    return path


def test_a_zip_missing_processes_names_the_two_directories(tmp_path):
    with pytest.raises(RuntimeError, match="processes/"):
        check_jsonld_layout(_zip_with(tmp_path, ["data/x.json"]))


def test_a_zip_nested_one_level_too_deep_says_how_to_rezip(tmp_path):
    # The likeliest authoring mistake: zipping the folder instead of its contents.
    path = _zip_with(tmp_path, ["my_study/processes/a.json", "my_study/flows/b.json"])
    with pytest.raises(RuntimeError, match="one level too deep"):
        check_jsonld_layout(path)


def test_a_correctly_shaped_zip_passes(tmp_path):
    check_jsonld_layout(_zip_with(tmp_path, ["processes/a.json", "flows/b.json"]))


def test_an_unhinted_link_stops_the_build_and_says_which_exchange():
    tally = BuildTally(tech_unhinted=1, unhinted_examples=["widget: 'steel' has no defaultProvider"])
    with pytest.raises(RuntimeError, match="no defaultProvider"):
        check_background_links(tally, "my-study", enforce=True)


def test_a_dangling_hint_is_reported_as_a_different_problem():
    tally = BuildTally(tech_dangling=1, dangling_examples=["widget: names provider 'GONE'"])
    with pytest.raises(RuntimeError, match="no background database"):
        check_background_links(tally, "my-study", enforce=True)


def test_the_uslci_path_reports_its_cutoffs_without_stopping():
    # A bundle build cuts its chain by construction; enforcing here would break it.
    tally = BuildTally(tech_unhinted=700, tech_dangling=70)
    check_background_links(tally, "uslci-subset", enforce=False)


def test_the_override_lets_an_exploratory_import_through():
    tally = BuildTally(tech_unhinted=1, unhinted_examples=["x"])
    check_background_links(tally, "my-study", enforce=False)


def test_a_uuid_shared_with_a_background_stops_a_custom_build():
    ext = ExternalProviders.single("uslci-full", uuids={"SHARED"})
    with pytest.raises(RuntimeError, match="shadow"):
        check_uuid_collisions({"SHARED": {"name": "my copy"}}, ext, "my-study",
                              enforce=True)


def test_the_same_collision_is_only_noted_on_the_uslci_path():
    # Shadowing an upstream process is the bundle build's intended precedence.
    ext = ExternalProviders.single("electricity-baseline", uuids={"SHARED"})
    said = []
    check_uuid_collisions({"SHARED": {"name": "bundle copy"}}, ext, "uslci-subset",
                          enforce=False, log=said.append)
    assert any("precedence" in s for s in said)


def test_backgrounds_disagreeing_on_vintage_is_refused():
    # Two grids in one build would make the vintage stamp a coin flip.
    from fedefl_bw25.setup_uslci import load_external_providers
    import fedefl_bw25.setup_uslci as su

    class _FakeDatabases(dict):
        def __init__(self, meta):
            super().__init__(meta)

    fake_meta = {"a": {"electricity_vintage": "2025"}, "b": {"electricity_vintage": "2026"}}

    class _FakeBd:
        databases = fake_meta
        @staticmethod
        def Database(name):
            return []

    original = su.bd
    su.bd = _FakeBd
    try:
        with pytest.raises(RuntimeError, match="disagree on the electricity baseline"):
            load_external_providers(["a", "b"])
    finally:
        su.bd = original


# -----------------------------------------------------------------------------
# Study targets in a multi-process dataset
# -----------------------------------------------------------------------------
# A 50-process expansion arrives as 50 equally-plausible UUIDs. The roots — what
# nothing else in the dataset consumes — are the shortlist a study picks from.

def _act(code, consumes=(), db="my-study"):
    exchanges = [{"input": (db, code), "amount": 1.0, "type": "production"}]
    exchanges += [{"input": (db, c), "amount": 1.0, "type": "technosphere"}
                  for c in consumes]
    return (db, code), {"name": f"proc {code}", "exchanges": exchanges}


def test_a_chain_has_one_root():
    from fedefl_bw25.setup_uslci import find_roots
    db_data = dict([_act("raw"), _act("mid", ["raw"]), _act("product", ["mid"])])
    assert find_roots(db_data, "my-study") == ["product"]


def test_every_industrys_end_product_is_a_root():
    from fedefl_bw25.setup_uslci import find_roots
    db_data = dict([_act("a_raw"), _act("a_end", ["a_raw"]),
                    _act("b_raw"), _act("b_end", ["b_raw"])])
    assert find_roots(db_data, "my-study") == ["a_end", "b_end"]


def test_a_link_into_the_background_does_not_make_it_a_root():
    # Consuming USLCI says nothing about whether THIS dataset consumes you.
    from fedefl_bw25.setup_uslci import find_roots
    key, act = _act("product")
    act["exchanges"].append({"input": ("uslci-full", "17664c37"), "amount": 1.0,
                             "type": "technosphere"})
    db_data = {key: act}
    assert find_roots(db_data, "my-study") == ["product"]


def test_a_production_exchange_does_not_consume_its_own_process():
    # Production exchanges point at the activity itself; counting them would make
    # every process "consumed" and the root list empty.
    from fedefl_bw25.setup_uslci import find_roots
    db_data = dict([_act("solo")])
    assert find_roots(db_data, "my-study") == ["solo"]


def test_the_build_result_refuses_to_guess_a_single_target():
    from fedefl_bw25.setup_uslci import UslciBuild
    build = UslciBuild(database="my-study", full_db=False, activity_count=2,
                       process_count=2, totals={}, source={}, electricity_vintage=None,
                       provenance_path="", processes={"a": "A", "b": "B"},
                       roots=["a", "b"])
    with pytest.raises(RuntimeError, match="no single target"):
        _ = build.target
    assert build.named() == {"a": "A", "b": "B"}


def test_a_single_process_build_has_a_target():
    from fedefl_bw25.setup_uslci import UslciBuild
    build = UslciBuild(database="my-study", full_db=False, activity_count=1,
                       process_count=1, totals={}, source={}, electricity_vintage=None,
                       provenance_path="", processes={"only": "Only"}, roots=["only"])
    assert build.target == "only"


# -----------------------------------------------------------------------------
# Exchange shape — the silent-drop failures
# -----------------------------------------------------------------------------
# A dropped exchange is the worst shape a defect can take here: the build reports
# success, the counts look plausible because they only count what matched, and the
# activity carries nothing but its production exchange. All three of these shipped
# as silent drops and are now refused.

def test_flow_type_falls_back_to_the_flows_file():
    # The likeliest authoring mistake: the flow's own file says what type it is, so
    # repeating it on every exchange looks redundant and gets left out.
    from fedefl_bw25.setup_uslci import resolve_flow_type
    all_flows = {"f1": {"flowType": "ELEMENTARY_FLOW"}}
    assert resolve_flow_type({"@id": "f1"}, "f1", all_flows) == "ELEMENTARY_FLOW"


def test_the_exchanges_own_flow_type_wins():
    from fedefl_bw25.setup_uslci import resolve_flow_type
    all_flows = {"f1": {"flowType": "ELEMENTARY_FLOW"}}
    assert resolve_flow_type({"@id": "f1", "flowType": "PRODUCT_FLOW"},
                             "f1", all_flows) == "PRODUCT_FLOW"


def test_an_unknown_flow_type_resolves_to_empty_not_a_guess():
    from fedefl_bw25.setup_uslci import resolve_flow_type
    assert resolve_flow_type({"@id": "f1"}, "f1", {}) == ""


def test_unclassifiable_exchanges_stop_a_custom_build():
    from fedefl_bw25.setup_uslci import check_untyped_exchanges
    tally = BuildTally(exc_untyped=2, untyped_examples=["widget: 'steel' flowType ''"])
    with pytest.raises(RuntimeError, match="could not be classified"):
        check_untyped_exchanges(tally, "my-study", enforce=True)


def test_the_uslci_path_does_not_stop_on_them():
    from fedefl_bw25.setup_uslci import check_untyped_exchanges
    check_untyped_exchanges(BuildTally(exc_untyped=2), "uslci-subset", enforce=False)


def test_a_formula_without_an_evaluated_amount_stops_a_custom_build():
    # brightway takes the literal amount; with none there the exchange is a no-op
    # that looks, in the JSON, like a fully specified model.
    from fedefl_bw25.setup_uslci import check_formula_exchanges
    tally = BuildTally(exc_formula=3, exc_formula_zero=2,
                       formula_examples=["widget: 'steel' = 'x*2' with amount 0.0"])
    with pytest.raises(RuntimeError, match="no numeric amount"):
        check_formula_exchanges(tally, "my-study", enforce=True)


def test_a_formula_with_an_evaluated_amount_is_fine():
    # USLCI ships 725 of these in the bundle build and reproduces openLCA to 0.1%.
    from fedefl_bw25.setup_uslci import check_formula_exchanges
    check_formula_exchanges(BuildTally(exc_formula=725, exc_formula_zero=0),
                            "uslci-subset", enforce=True)


def test_a_provider_on_a_product_output_stops_a_custom_build():
    # Missing isInput. Doubly damaging: the link is dropped AND the process becomes
    # multi-output, so allocation scales every other exchange down.
    from fedefl_bw25.setup_uslci import check_provider_on_output
    tally = BuildTally(exc_provider_on_output=1,
                       provider_on_output_examples=["widget: 'steel' is an OUTPUT"])
    with pytest.raises(RuntimeError, match="no provider"):
        check_provider_on_output(tally, "my-study", enforce=True)


def test_note_formula_only_flags_the_ones_with_no_amount():
    from fedefl_bw25.setup_uslci import note_formula
    tally = BuildTally()
    note_formula({"amountFormula": "a*2", "amount": 4.0}, tally, {}, "p", {}, "f", 4.0)
    assert (tally.exc_formula, tally.exc_formula_zero) == (1, 0)
    note_formula({"amountFormula": "a*2"}, tally, {}, "p", {}, "f", 0.0)
    assert (tally.exc_formula, tally.exc_formula_zero) == (2, 1)
    note_formula({"amount": 1.0}, tally, {}, "p", {}, "f", 1.0)
    assert tally.exc_formula == 2          # no formula -> not counted
