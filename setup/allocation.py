"""Multi-output allocation logic for the USLCI import (module, not standalone).

Extracted verbatim from setup/03_import_uslci.py so the most intricate code in
the import — reference-product allocation plus co-product re-basis — can be
unit-tested with synthetic openLCA JSON (tests/test_allocation.py) without
executing the import script. No brightway dependency.

The two callables here are pure: `allocation_for` takes the caller's
`normalize` function and flow-conversion table as arguments instead of reading
module globals, and `coproduct_multipliers` is plain arithmetic on
`allocation_for`'s per-output data.
"""

# Mass flow-property UUID in the USLCI/FEDEFL flow-property tables.
MASS_FP_UUID = "93a60a56-a3c8-11da-a746-0800200b9a66"


def _output_products(proc, normalize, flow_conv):
    """List the process's PRODUCT_FLOW outputs as (flow_uuid, native_yield,
    mass_kg) plus the reference product's flow UUID (None if the reference is
    not among them, e.g. a reference-on-input waste-treatment process)."""
    output_products = []
    ref_flow_uuid = None
    for exc in proc.get("exchanges", []):
        if exc.get("isInput") or exc.get("flow", {}).get("flowType") != "PRODUCT_FLOW":
            continue
        flow_uuid_e = exc.get("flow", {}).get("@id")
        fp_uuid_e   = exc.get("flowProperty", {}).get("@id", "")
        amount_e    = exc.get("amount", 0.0)
        unit_e      = exc.get("unit", {}).get("name", "")
        # Native yield in the flow's own reference unit.
        norm_e, _ = normalize(amount_e, unit_e, fp_uuid_e, flow_uuid_e)
        # Convert to kg for the mass-fraction fallback denominator.
        # FLOW_CONV factor semantics: factor = (other fp units) per (ref fp unit).
        fc      = flow_conv.get(flow_uuid_e, {})
        ref_fp  = fc.get("ref_fp_uuid", "")
        convs   = fc.get("conversions", {})
        if ref_fp == MASS_FP_UUID:
            mass_kg = norm_e                                    # ref already kg
        elif MASS_FP_UUID in convs:
            mass_kg = norm_e * convs[MASS_FP_UUID]["factor"]    # e.g. m3 × (kg/m3)
        else:
            mass_kg = 0.0                                       # no mass conversion
        output_products.append((flow_uuid_e, norm_e, mass_kg))
        if exc.get("isQuantitativeReference"):
            ref_flow_uuid = flow_uuid_e
    return output_products, ref_flow_uuid


def allocation_for(proc, proc_uuid, normalize, flow_conv):
    """Compute one multi-output process's reference-product allocation, plus the
    per-output data needed to correctly re-basis consumers of its NON-reference
    co-products.

    A multi-output process is imported as ONE brightway activity: its production
    exchange is the *reference* product's native yield and its inputs/biosphere
    exchanges are scaled by the reference product's allocation factor. A consumer
    that draws a co-product must not inherit the reference product's basis (that
    was the co-product allocation bug -- see DEVLOG).

    `normalize` is the caller's unit-normalization function
    (amount, unit, fp_uuid, flow_uuid) -> (normalized_amount, ref_unit);
    `flow_conv` is the flow-conversion table keyed by flow UUID
    (setup/00's uslci_flow_conversions.json).

    Returns (alloc_factor, ref_flow_uuid, per_output, method, causal_factors):
      alloc_factor  -- scalar factor applied to every input/biosphere exchange
                       (reference product's own share; 1.0 for single-output).
                       For "causal" this is only a fallback for exchanges lacking
                       a causal factor -- see causal_factors.
      ref_flow_uuid -- reference product flow UUID (None for single-output or a
                       reference-on-input waste-treatment process).
      per_output    -- {flow_uuid: (native_yield, alloc)} for every PRODUCT_FLOW
                       output. `alloc` is computed by the SAME method used for
                       the reference product so co-product multipliers stay
                       internally consistent. Empty for single-output and causal.
      method        -- "single" | "native" | "mass" | "causal".
      causal_factors-- {exchange_internalId: factor} for the reference product,
                       for CAUSAL_ALLOCATION processes; None otherwise. Causal
                       allocation is per-exchange (each input/emission carries its
                       own factor per product), so it cannot be flattened to one
                       scalar without mis-allocating every exchange whose causal
                       factor differs from the mass fraction.

    Raises the same malformed/uncomputable errors the previous inline logic did.
    """
    output_products, ref_flow_uuid = _output_products(proc, normalize, flow_conv)

    if len(output_products) <= 1:
        return 1.0, ref_flow_uuid, {}, "single", None

    if ref_flow_uuid is None:
        raise RuntimeError(
            f"Multi-output process '{proc.get('name', proc_uuid)}' (proc UUID: {proc_uuid}) "
            f"has {len(output_products)} product outputs but no isQuantitativeReference flag. "
            f"Cannot determine which output is the reference product for allocation.\n"
            f"  Output flow UUIDs: {[u for u, _, _ in output_products]}"
        )

    # Prefer openLCA's own pre-computed factor for this process's declared
    # defaultAllocationMethod over a from-scratch mass fraction. USLCI does NOT
    # default to mass/physical allocation uniformly -- 9 of 18 multi-output
    # processes across the 4 test-case bundles use ECONOMIC_ or CAUSAL_
    # allocation (corn, soybeans, both steel co-product processes, several
    # paper/pulp mill processes). PHYSICAL/ECONOMIC give one factor per product
    # (a scalar applied to all inputs); CAUSAL gives a factor per *exchange* per
    # product -- handled separately below. Falls back to a from-scratch mass
    # fraction only when the process ships no usable native factor at all.
    default_method = proc.get("defaultAllocationMethod")
    total_mass = sum(m for _, _, m in output_products if m > 0)

    if default_method == "CAUSAL_ALLOCATION":
        # Per-exchange allocation: build the reference product's factor for each
        # input/emission exchange, keyed by its internalId. Flattening this to a
        # single scalar (native lookup returns nothing for causal, so the old
        # code fell back to the mass fraction) mis-allocates every exchange whose
        # causal factor differs from that fraction -- e.g. the cellulosic-ethanol
        # process over-attributed forest-residue feedstock to ethanol 1.83x.
        causal_factors = {}
        for af in proc.get("allocationFactors", []):
            if (af.get("allocationType") == "CAUSAL_ALLOCATION"
                    and af.get("product", {}).get("@id") == ref_flow_uuid
                    and "exchange" in af):
                iid = af["exchange"].get("internalId")
                if iid is not None:
                    causal_factors[iid] = af["value"]
        # Mass-fraction fallback for any *added* exchange that somehow lacks a
        # causal factor (in the observed data only the product outputs lack one,
        # and outputs are never added to the activity). per_output is left empty:
        # a causal co-product's burden is per-exchange, so the scalar co-product
        # multiplier cannot represent it -- the pre-pass skips causal here and
        # warns if any causal co-product is actually consumed.
        ref_mass = next(m for f, _, m in output_products if f == ref_flow_uuid)
        fallback = (ref_mass / total_mass) if total_mass else 1.0
        return fallback, ref_flow_uuid, {}, "causal", causal_factors

    def _native(flow_uuid):
        return next(
            (af["value"] for af in proc.get("allocationFactors", [])
             if af.get("allocationType") == default_method
             and af.get("product", {}).get("@id") == flow_uuid
             and "exchange" not in af),  # exclude per-exchange CAUSAL breakdowns
            None
        )

    ref_native = _native(ref_flow_uuid)

    per_output = {}
    if ref_native is not None:
        method = "native"
        alloc_factor = ref_native
        for flow_uuid, yld, _mass in output_products:
            per_output[flow_uuid] = (yld, _native(flow_uuid))
    else:
        method = "mass"
        ref_mass = next(m for f, _, m in output_products if f == ref_flow_uuid)
        if ref_mass == 0.0:
            _ref_exc = next(
                (e for e in proc.get("exchanges", [])
                 if e.get("isQuantitativeReference") and not e.get("isInput")),
                {}
            )
            _ref_unit = _ref_exc.get("unit", {}).get("name", "<unknown>")
            _ref_fp   = _ref_exc.get("flowProperty", {}).get("@id", "<unknown>")
            raise RuntimeError(
                f"Allocation failed for multi-output process '{proc.get('name', proc_uuid)}' "
                f"(proc UUID: {proc_uuid}):\n"
                f"  Ref product flow UUID : {ref_flow_uuid}\n"
                f"  Unit in JSON          : {_ref_unit}\n"
                f"  Flow property UUID    : {_ref_fp}\n"
                f"  FLOW_CONV entry exists: {ref_flow_uuid in flow_conv}\n"
                f"  Mass property UUID    : {MASS_FP_UUID}\n"
                f"  No usable '{default_method}' allocationFactors entry for this product, and "
                f"no mass conversion available for a from-scratch fallback either.\n"
                f"  Fix: add a mass conversion for this flow in setup/00_build_flow_conversion_table.py "
                f"and regenerate FLOW_CONV, or handle it as a service flow (not a mass-allocatable product)."
            )
        alloc_factor = ref_mass / total_mass
        for flow_uuid, yld, mass_kg in output_products:
            per_output[flow_uuid] = (yld, (mass_kg / total_mass) if total_mass else None)

    return alloc_factor, ref_flow_uuid, per_output, method, None


def causal_coproducts(proc, proc_uuid, normalize, flow_conv):
    """Per NON-reference product output of a CAUSAL_ALLOCATION process, the data
    needed to build a DEDICATED brightway activity for that co-product.

    Causal allocation is per-exchange, so a consumer of a causal co-product
    cannot be re-based through the reference product's activity by any scalar
    multiplier (see coproduct_multipliers). Instead the importer builds a second
    activity per co-product: production = the co-product's own native yield,
    every input/biosphere exchange scaled by the co-product's own column of the
    per-exchange factor grid. openLCA ships the full grid (all products'
    columns); allocation_for reads only the reference product's column, this
    reads the rest.

    Returns {} unless `proc` is a multi-output CAUSAL_ALLOCATION process with a
    reference product. Otherwise:

      {co_flow_uuid: {
          "column":   {exchange_internalId: factor}  -- this co-product's grid column
          "fallback": mass fraction of this co-product -- used, and counted by the
                      caller, for any exchange missing a causal entry (mirrors
                      allocation_for's reference-product fallback)
          "yield":    native yield in the flow's own reference unit
      }}
    """
    if proc.get("defaultAllocationMethod") != "CAUSAL_ALLOCATION":
        return {}
    output_products, ref_flow_uuid = _output_products(proc, normalize, flow_conv)
    if len(output_products) <= 1 or ref_flow_uuid is None:
        return {}

    total_mass = sum(m for _, _, m in output_products if m > 0)
    out = {}
    for flow_uuid, yld, mass_kg in output_products:
        if flow_uuid == ref_flow_uuid:
            continue
        column = {}
        for af in proc.get("allocationFactors", []):
            if (af.get("allocationType") == "CAUSAL_ALLOCATION"
                    and af.get("product", {}).get("@id") == flow_uuid
                    and "exchange" in af):
                iid = af["exchange"].get("internalId")
                if iid is not None:
                    column[iid] = af["value"]
        out[flow_uuid] = {
            "column":   column,
            "fallback": (mass_kg / total_mass) if total_mass else 1.0,
            "yield":    yld,
        }
    return out


def coproduct_multipliers(per_output, ref_flow_uuid):
    """Per non-reference output flow, the multiplier that converts a request
    expressed in the co-product's own units into the equivalent amount of the
    reference product delivering the co-product's correctly-allocated burden
    share:

        m(P, flow) = (ref_yield * target_alloc) / (target_yield * ref_alloc)

    The reference product gets no entry (implicit 1.0). For pure mass allocation
    this reduces to a units/density conversion (the reference product's declared
    yield can be in L/m3 while its allocation factor is mass-based); for
    economic allocation it does real burden re-attribution. Both fall out of the
    same formula.

    Returns ({flow_uuid: m}, skipped) where `skipped` lists flow UUIDs whose
    multiplier was uncomputable (zero/missing yield or allocation).
    """
    multipliers = {}
    skipped = []
    ref_yield, ref_alloc = per_output[ref_flow_uuid]
    for flow_uuid, (tgt_yield, tgt_alloc) in per_output.items():
        if flow_uuid == ref_flow_uuid:
            continue
        if not ref_alloc or not ref_yield or not tgt_yield or tgt_alloc is None:
            skipped.append(flow_uuid)
            continue
        multipliers[flow_uuid] = (ref_yield * tgt_alloc) / (tgt_yield * ref_alloc)
    return multipliers, skipped
