"""Tests for fedefl_bw25.setup_baseline — provider discovery and vintage config.

`find_external_providers` decides what gets injected, and a gap in it caused a real
defect: scanning only the per-process bundles left a full-database build with 11 of
the 17 grid providers it needed, and 42 transport processes silently scored zero.
It is pure enough to test against a synthetic USLCI-shaped zip, so it now is.
"""
import json
import zipfile

from fedefl_bw25.setup_baseline import (_vintages, find_external_providers,
                                        find_full_db_zip)


def _zip(tmp_path, processes, name="src.zip"):
    """Write a minimal USLCI-shaped JSON-LD zip."""
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as z:
        for p in processes:
            z.writestr(f"processes/{p['@id']}.json", json.dumps(p))
    return path


def _proc(uuid, exchanges):
    return {"@id": uuid, "name": f"proc {uuid}", "exchanges": exchanges}


def _ref_out(flow_uuid):
    return {"isInput": False, "isQuantitativeReference": True,
            "flow": {"@id": flow_uuid, "name": "ref", "flowType": "PRODUCT_FLOW"}}


def _input(flow_uuid, provider_uuid=None, flow_type="PRODUCT_FLOW"):
    e = {"isInput": True,
         "flow": {"@id": flow_uuid, "name": "in", "flowType": flow_type}}
    if provider_uuid:
        e["defaultProvider"] = {"@id": provider_uuid, "name": "the provider"}
    return e


def test_external_provider_is_found(tmp_path):
    z = _zip(tmp_path, [_proc("A", [_ref_out("fA"), _input("fGRID", "GRID-1")])])
    found = find_external_providers(z)
    assert set(found) == {"GRID-1"}
    assert found["GRID-1"]["provider_name"] == "the provider"
    assert found["GRID-1"]["referenced_by"] == {"A"}


def test_provider_produced_inside_the_source_is_not_external(tmp_path):
    # B produces fB and A consumes it — setup/03 links this internally, so it must
    # not be requested from the library.
    z = _zip(tmp_path, [_proc("A", [_ref_out("fA"), _input("fB", "B")]),
                        _proc("B", [_ref_out("fB")])])
    assert find_external_providers(z) == {}


def test_elementary_flows_are_not_providers(tmp_path):
    z = _zip(tmp_path, [_proc("A", [_ref_out("fA"),
                                    _input("fCO2", "X", flow_type="ELEMENTARY_FLOW")])])
    assert find_external_providers(z) == {}


def test_waste_flows_count_as_providers(tmp_path):
    # Waste sent for treatment is a technosphere link like any other.
    z = _zip(tmp_path, [_proc("A", [_ref_out("fA"),
                                    _input("fWASTE", "TREAT-1", flow_type="WASTE_FLOW")])])
    assert set(find_external_providers(z)) == {"TREAT-1"}


def test_input_without_a_default_provider_is_skipped(tmp_path):
    z = _zip(tmp_path, [_proc("A", [_ref_out("fA"), _input("fX")])])
    assert find_external_providers(z) == {}


def test_references_from_several_processes_accumulate(tmp_path):
    z = _zip(tmp_path, [_proc("A", [_ref_out("fA"), _input("fG", "GRID-1")]),
                        _proc("B", [_ref_out("fB"), _input("fG", "GRID-1")])])
    assert find_external_providers(z)["GRID-1"]["referenced_by"] == {"A", "B"}


def test_a_reference_flow_consumed_as_input_is_still_internal(tmp_path):
    # isQuantitativeReference on an INPUT (a waste-treatment sink) must not make
    # that flow count as "produced here" for other processes.
    z = _zip(tmp_path, [
        _proc("A", [{"isInput": True, "isQuantitativeReference": True,
                     "flow": {"@id": "fW", "name": "w", "flowType": "WASTE_FLOW"}}]),
        _proc("B", [_ref_out("fB"), _input("fW", "EXT-1", flow_type="WASTE_FLOW")]),
    ])
    assert set(find_external_providers(z)) == {"EXT-1"}


def test_full_db_zip_absent_is_none_not_an_error(tmp_path):
    # The bundle-only workflow never needs it; absence must be unremarkable.
    assert find_full_db_zip(tmp_path) is None


def test_vintage_paths_follow_the_given_bundle_dir(tmp_path):
    v = _vintages(tmp_path)
    assert set(v) == {"2025", "2026"}
    assert v["2026"]["fetch_target"].parent == tmp_path
    # The US-average grid UUID is the vintage marker bundles are classified by.
    assert v["2025"]["grid_uuid"] != v["2026"]["grid_uuid"]


# -----------------------------------------------------------------------------
# Who decides the vintage
# -----------------------------------------------------------------------------
# The whole-database workflow downloads one file and no bundles. Excluding the full
# zip from the vintage vote left that build taking DEFAULT_VINTAGE on faith — which
# went wrong the moment USLCI shipped a release whose grid references are all 2026.
def _grid_ref(vintage_uuid):
    return _input("fELEC", vintage_uuid)


def test_the_full_zip_decides_the_vintage_when_no_bundle_does():
    from fedefl_bw25.setup_baseline import GRID_UUIDS
    from fedefl_bw25.vintage_detect import classify_bundle, decide_vintage

    full = classify_bundle(
        {GRID_UUIDS["2026"]: {"referenced_by": {f"p{i}" for i in range(359)}}},
        GRID_UUIDS)
    assert full["vintage"] == "2026"

    # No bundles -> decide_vintage falls back to the default, which is what
    # inject_baseline then overrides with the full zip's reading.
    decision = decide_vintage({}, explicit=None, default="2025")
    assert decision["source"] == "default" and decision["vintage"] == "2025"


def test_bundles_outrank_the_full_zip_when_they_disagree():
    # Bundles are the study data; the full zip is background. A 2025 bundle set must
    # keep building 2025 even though the current full zip reads 2026.
    from fedefl_bw25.setup_baseline import GRID_UUIDS
    from fedefl_bw25.vintage_detect import classify_bundle, decide_vintage

    bundle = classify_bundle(
        {GRID_UUIDS["2025"]: {"referenced_by": {f"p{i}" for i in range(80)}},
         GRID_UUIDS["2026"]: {"referenced_by": {"stray"}}},
        GRID_UUIDS)
    assert bundle["vintage"] == "2025"
    decision = decide_vintage({"b.zip": bundle}, explicit=None, default="2025")
    assert decision["source"] == "detected" and decision["vintage"] == "2025"
