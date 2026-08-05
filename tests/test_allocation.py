"""Unit tests for setup/allocation.py — multi-output allocation + co-product
re-basis (ledger #1's code path), driven by synthetic openLCA-style JSON.

The synthetic causal fixture is deliberately built with per-exchange factors
that DIFFER row to row. The one real causal process in the locked bundles
(cellulosic ethanol) has a uniform grid — every exchange splits 0.4633/0.5367 —
so real data cannot distinguish a correct per-exchange implementation from a
flattened scalar. These fixtures can.
"""
import json
import zipfile
from pathlib import Path

import pytest

from fedefl_bw25.allocation import (MASS_FP_UUID, allocation_for, causal_coproducts,
                        coproduct_multipliers)

VOLUME_FP_UUID = "93a60a56-a3c8-22da-a746-0800200c9a66"  # arbitrary non-mass fp

FUEL = "flow-fuel"
WAX  = "flow-wax"
SLAG = "flow-slag"


def _exc(iid, flow_uuid, amount, *, unit="kg", is_input=False, is_ref=False,
         flow_type="PRODUCT_FLOW", fp=MASS_FP_UUID):
    return {
        "internalId": iid,
        "isInput": is_input,
        "isQuantitativeReference": is_ref,
        "amount": amount,
        "unit": {"name": unit},
        "flowProperty": {"@id": fp},
        "flow": {"@id": flow_uuid, "flowType": flow_type, "name": flow_uuid},
    }


def _identity_normalize(amount, unit, fp_uuid, flow_uuid):
    return amount, unit


def _mass_conv(*flows):
    """flow_conv table declaring each flow's reference property as mass (kg)."""
    return {f: {"ref_fp_uuid": MASS_FP_UUID, "ref_unit": "kg", "conversions": {}}
            for f in flows}


# ---------------------------------------------------------------------------
# single-output and structural cases
# ---------------------------------------------------------------------------

def test_single_output_is_unallocated():
    proc = {"name": "one product", "exchanges": [
        _exc(1, FUEL, 100.0, is_ref=True),
        _exc(2, "flow-crude", 120.0, is_input=True),
    ]}
    alloc, ref, per_output, method, causal = allocation_for(
        proc, "p1", _identity_normalize, _mass_conv(FUEL))
    assert (alloc, ref, per_output, method, causal) == (1.0, FUEL, {}, "single", None)


def test_waste_treatment_reference_on_input():
    # USLCI waste-treatment sinks put isQuantitativeReference on an INPUT;
    # with <=1 product output that is a "single" with no ref flow recorded here.
    proc = {"name": "landfill", "exchanges": [
        _exc(1, "flow-waste", 1.0, is_input=True, is_ref=True),
    ]}
    alloc, ref, per_output, method, causal = allocation_for(
        proc, "p2", _identity_normalize, {})
    assert (alloc, ref, method) == (1.0, None, "single")


def test_multi_output_without_ref_flag_raises():
    proc = {"name": "broken", "exchanges": [
        _exc(1, FUEL, 100.0),
        _exc(2, WAX, 50.0),
    ]}
    with pytest.raises(RuntimeError, match="no isQuantitativeReference flag"):
        allocation_for(proc, "p3", _identity_normalize, _mass_conv(FUEL, WAX))


# ---------------------------------------------------------------------------
# native (openLCA-precomputed) scalar allocation
# ---------------------------------------------------------------------------

def _economic_proc():
    return {
        "name": "refinery",
        "defaultAllocationMethod": "ECONOMIC_ALLOCATION",
        "exchanges": [
            _exc(1, FUEL, 100.0, is_ref=True),
            _exc(2, WAX, 50.0),
            _exc(3, "flow-crude", 160.0, is_input=True),
        ],
        "allocationFactors": [
            {"allocationType": "ECONOMIC_ALLOCATION", "product": {"@id": FUEL}, "value": 0.8},
            {"allocationType": "ECONOMIC_ALLOCATION", "product": {"@id": WAX},  "value": 0.2},
            # a PHYSICAL set is usually present too and must be ignored:
            {"allocationType": "PHYSICAL_ALLOCATION", "product": {"@id": FUEL}, "value": 0.6667},
            {"allocationType": "PHYSICAL_ALLOCATION", "product": {"@id": WAX},  "value": 0.3333},
        ],
    }


def test_native_economic_allocation():
    alloc, ref, per_output, method, causal = allocation_for(
        _economic_proc(), "p4", _identity_normalize, _mass_conv(FUEL, WAX))
    assert method == "native"
    assert ref == FUEL
    assert alloc == 0.8
    assert per_output == {FUEL: (100.0, 0.8), WAX: (50.0, 0.2)}
    assert causal is None


def test_native_lookup_ignores_per_exchange_entries():
    # A per-exchange CAUSAL-style breakdown listed under the same product must
    # not be picked up as the scalar factor.
    proc = _economic_proc()
    proc["allocationFactors"].insert(0, {
        "allocationType": "ECONOMIC_ALLOCATION", "product": {"@id": FUEL},
        "exchange": {"internalId": 3}, "value": 0.999,
    })
    alloc, *_ = allocation_for(proc, "p5", _identity_normalize, _mass_conv(FUEL, WAX))
    assert alloc == 0.8


# ---------------------------------------------------------------------------
# mass-fraction fallback (no usable native factors)
# ---------------------------------------------------------------------------

def test_mass_fallback_with_density_conversion():
    # Wax yield declared in m3; its flow_conv entry provides 900 kg/m3, so
    # 0.05 m3 -> 45 kg. Fractions: fuel 100/145, wax 45/145.
    proc = {
        "name": "no factors",
        "exchanges": [
            _exc(1, FUEL, 100.0, is_ref=True),
            _exc(2, WAX, 0.05, unit="m3", fp=VOLUME_FP_UUID),
        ],
    }
    flow_conv = _mass_conv(FUEL)
    flow_conv[WAX] = {"ref_fp_uuid": VOLUME_FP_UUID, "ref_unit": "m3",
                      "conversions": {MASS_FP_UUID: {"factor": 900.0}}}
    alloc, ref, per_output, method, causal = allocation_for(
        proc, "p6", _identity_normalize, flow_conv)
    assert method == "mass"
    assert alloc == pytest.approx(100.0 / 145.0)
    assert per_output[FUEL] == (100.0, pytest.approx(100.0 / 145.0))
    # native yield stays in the flow's own unit (m3); alloc is the mass share
    assert per_output[WAX] == (0.05, pytest.approx(45.0 / 145.0))


def test_mass_fallback_unconvertible_reference_raises():
    proc = {
        "name": "service ref",
        "exchanges": [
            _exc(1, FUEL, 1.0, unit="MJ", fp="fp-energy", is_ref=True),
            _exc(2, WAX, 50.0),
        ],
    }
    # FUEL has no mass conversion at all -> ref mass 0 -> hard error
    flow_conv = _mass_conv(WAX)
    flow_conv[FUEL] = {"ref_fp_uuid": "fp-energy", "ref_unit": "MJ", "conversions": {}}
    with pytest.raises(RuntimeError, match="Allocation failed"):
        allocation_for(proc, "p7", _identity_normalize, flow_conv)


# ---------------------------------------------------------------------------
# causal (per-exchange) allocation — rows that genuinely differ
# ---------------------------------------------------------------------------

def _causal_proc():
    return {
        "name": "biorefinery",
        "defaultAllocationMethod": "CAUSAL_ALLOCATION",
        "exchanges": [
            _exc(1, FUEL, 60.0, is_ref=True),
            _exc(2, WAX, 40.0),
            _exc(3, "flow-feedstock", 200.0, is_input=True),
            _exc(4, "flow-electricity", 10.0, is_input=True, unit="MJ", fp="fp-energy"),
            _exc(5, "flow-co2", 5.0, flow_type="ELEMENTARY_FLOW"),
        ],
        "allocationFactors": [
            # reference product's column — factors DIFFER per exchange
            {"allocationType": "CAUSAL_ALLOCATION", "product": {"@id": FUEL},
             "exchange": {"internalId": 3}, "value": 0.9},
            {"allocationType": "CAUSAL_ALLOCATION", "product": {"@id": FUEL},
             "exchange": {"internalId": 4}, "value": 0.5},
            {"allocationType": "CAUSAL_ALLOCATION", "product": {"@id": FUEL},
             "exchange": {"internalId": 5}, "value": 0.7},
            # co-product's column — present in the JSON but must be ignored
            {"allocationType": "CAUSAL_ALLOCATION", "product": {"@id": WAX},
             "exchange": {"internalId": 3}, "value": 0.1},
            {"allocationType": "CAUSAL_ALLOCATION", "product": {"@id": WAX},
             "exchange": {"internalId": 4}, "value": 0.5},
            {"allocationType": "CAUSAL_ALLOCATION", "product": {"@id": WAX},
             "exchange": {"internalId": 5}, "value": 0.3},
            # openLCA also ships scalar PHYSICAL/ECONOMIC sets — ignored for causal
            {"allocationType": "PHYSICAL_ALLOCATION", "product": {"@id": FUEL}, "value": 0.6},
        ],
    }


def test_causal_builds_per_exchange_table_for_reference_product():
    alloc, ref, per_output, method, causal = allocation_for(
        _causal_proc(), "p8", _identity_normalize, _mass_conv(FUEL, WAX))
    assert method == "causal"
    assert ref == FUEL
    # the reference product's column only, keyed by internalId
    assert causal == {3: 0.9, 4: 0.5, 5: 0.7}
    # no scalar co-product data: per-exchange burden can't be re-based scalar-ly
    assert per_output == {}
    # fallback for exchanges missing a causal entry = mass fraction 60/100
    assert alloc == pytest.approx(0.6)


def test_causal_would_be_misrepresented_by_any_scalar():
    # The point of the per-exchange table: no single scalar reproduces it.
    _alloc, _ref, _po, _method, causal = allocation_for(
        _causal_proc(), "p9", _identity_normalize, _mass_conv(FUEL, WAX))
    assert len(set(causal.values())) > 1


def test_causal_coproducts_extracts_each_coproducts_own_column():
    out = causal_coproducts(_causal_proc(), "p10", _identity_normalize,
                            _mass_conv(FUEL, WAX))
    # only the NON-reference product gets an entry
    assert set(out) == {WAX}
    wax = out[WAX]
    # the co-product's own column — NOT the reference product's
    assert wax["column"] == {3: 0.1, 4: 0.5, 5: 0.3}
    assert wax["yield"] == 40.0
    # fallback = wax's own mass fraction (40 / 100)
    assert wax["fallback"] == pytest.approx(0.4)
    # complementarity with the reference column: each exchange's factors sum to 1
    _a, _r, _po, _m, ref_col = allocation_for(
        _causal_proc(), "p10", _identity_normalize, _mass_conv(FUEL, WAX))
    for iid, v in wax["column"].items():
        assert ref_col[iid] + v == pytest.approx(1.0)


def test_causal_coproducts_empty_for_non_causal_processes():
    assert causal_coproducts(_economic_proc(), "p11", _identity_normalize,
                             _mass_conv(FUEL, WAX)) == {}
    single = {"name": "one", "exchanges": [_exc(1, FUEL, 100.0, is_ref=True)]}
    assert causal_coproducts(single, "p12", _identity_normalize,
                             _mass_conv(FUEL)) == {}


# ---------------------------------------------------------------------------
# co-product re-basis multipliers
# ---------------------------------------------------------------------------

def test_multiplier_analytic_example():
    # Supplier: 100 kg fuel (alpha 0.8), 50 kg wax (alpha 0.2).
    # A consumer asking for 1 kg wax must be charged wax's share of the burden:
    #   m = (ref_yield * tgt_alloc) / (tgt_yield * ref_alloc)
    #     = (100 * 0.2) / (50 * 0.8) = 0.5
    per_output = {FUEL: (100.0, 0.8), WAX: (50.0, 0.2)}
    mults, skipped = coproduct_multipliers(per_output, FUEL)
    assert skipped == []
    assert mults == {WAX: pytest.approx(0.5)}
    # Sanity: drawing 1 kg wax -> 0.5 ref units -> (0.5/100) * 0.8 = 0.004 of
    # total burden, which equals wax's own share per kg: 0.2 / 50 = 0.004.
    assert (0.5 / 100.0) * 0.8 == pytest.approx(0.2 / 50.0)


def test_multiplier_conservation_property():
    # For ANY yields/allocations: consuming a co-product's entire yield must
    # recover exactly its allocated burden share, and the shares of all
    # products must sum to 1 (nothing double-counted, nothing lost).
    per_output = {FUEL: (137.0, 0.55), WAX: (12.5, 0.30), SLAG: (940.0, 0.15)}
    mults, skipped = coproduct_multipliers(per_output, FUEL)
    assert skipped == []
    shares = {FUEL: 0.55}
    for flow, (yld, _alloc) in per_output.items():
        if flow == FUEL:
            continue
        ref_equivalent = yld * mults[flow]            # re-based request
        shares[flow] = (ref_equivalent / 137.0) * 0.55  # burden actually drawn
    assert shares[WAX] == pytest.approx(0.30)
    assert shares[SLAG] == pytest.approx(0.15)
    assert sum(shares.values()) == pytest.approx(1.0)


def test_multiplier_skips_uncomputable_entries():
    per_output = {
        FUEL: (100.0, 0.8),
        WAX:  (0.0, 0.2),    # zero yield -> uncomputable
        SLAG: (10.0, None),  # no allocation value -> uncomputable
    }
    mults, skipped = coproduct_multipliers(per_output, FUEL)
    assert mults == {}
    assert sorted(skipped) == sorted([WAX, SLAG])


# ---------------------------------------------------------------------------
# grounding against the one real causal process in the locked bundles
# ---------------------------------------------------------------------------

_BUNDLE = (Path(__file__).resolve().parent.parent / "source_data" /
           "0aaf1e13-5d80-37f9-b7bb-81a6b8965c71_00a040571f44a37517a3e8ebb1dfb54d8dd09c4a.zip")
_ETHANOL_UUID = "d9cadd89-4203-375a-903a-63197cd29c6a"


@pytest.mark.skipif(not _BUNDLE.exists(), reason="USLCI bundle zips not present")
def test_real_cellulosic_ethanol_grid():
    with zipfile.ZipFile(_BUNDLE) as z:
        proc = json.loads(z.read(f"processes/{_ETHANOL_UUID}.json"))
    # all three outputs are in kg, so a mass-only flow_conv suffices
    product_flows = [e["flow"]["@id"] for e in proc["exchanges"]
                     if not e.get("isInput")
                     and e["flow"].get("flowType") == "PRODUCT_FLOW"]
    alloc, ref, per_output, method, causal = allocation_for(
        proc, _ETHANOL_UUID, _identity_normalize, _mass_conv(*product_flows))
    assert method == "causal"
    assert per_output == {}
    # 29 exchanges carry a causal entry for the reference product (ethanol)
    assert len(causal) == 29
    # ethanol's causal factor is 0.4633 on every row except the all-zero
    # wastewater row — uniform, which is why real data can't test per-exchange
    # correctness and the synthetic fixtures above exist
    values = set(round(v, 4) for v in causal.values())
    assert values == {0.4633, 0.0}
    # mass-fraction fallback = ethanol's physical share, 21183 / 25023.52 kg
    assert alloc == pytest.approx(0.8465235906059579, rel=1e-9)


@pytest.mark.skipif(not _BUNDLE.exists(), reason="USLCI bundle zips not present")
def test_real_cellulosic_ethanol_coproduct_columns():
    with zipfile.ZipFile(_BUNDLE) as z:
        proc = json.loads(z.read(f"processes/{_ETHANOL_UUID}.json"))
    product_flows = [e["flow"]["@id"] for e in proc["exchanges"]
                     if not e.get("isInput")
                     and e["flow"].get("flowType") == "PRODUCT_FLOW"]
    out = causal_coproducts(proc, _ETHANOL_UUID, _identity_normalize,
                            _mass_conv(*product_flows))
    # two non-reference co-products: mixed alcohols and sulfur
    assert len(out) == 2
    by_fallback = sorted(out.values(), key=lambda d: d["fallback"])
    sulfur, mixed = by_fallback
    # mixed alcohols: complement of ethanol's uniform 0.4633 column
    assert len(mixed["column"]) == 29
    assert set(round(v, 4) for v in mixed["column"].values()) == {0.5367, 0.0}
    assert mixed["fallback"] == pytest.approx(0.15133762156563105, rel=1e-9)
    # sulfur: a real all-zero column — the plant charges sulfur nothing
    assert set(sulfur["column"].values()) == {0.0}
    assert sulfur["fallback"] == pytest.approx(0.002138787828411031, rel=1e-9)
