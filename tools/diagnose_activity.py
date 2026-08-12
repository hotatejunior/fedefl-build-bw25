#!/usr/bin/env python3
"""
diagnose_activity.py
--------------------
Localize a wrong LCIA number to the exchange that caused it.

    python tools/diagnose_activity.py --database my-study --search "widget"
    python tools/diagnose_activity.py --database my-study --uuid widget-0001

A result that is orders of magnitude too low has only a few possible causes, and
they are indistinguishable from the score alone. This walks one activity and
attributes its number, so the cause names itself:

  * the functional unit moved -- the production exchange is not the amount you
    think, which divides every result by whatever it actually is
  * biosphere flows did not match FEDEFL, so they were dropped at import
  * biosphere flows matched but TRACI does not characterize them, so they are
    carried and contribute nothing (a coverage limit, not a fault)
  * technosphere inputs did not link, so the supply chain behind them is absent
  * allocation scaled the process down, usually because it is unintentionally
    multi-output

`--against` additionally compares the built amounts to the source JSON-LD, which
is how a parameter evaluation that changed something it should not have gets
caught -- most importantly on the reference exchange, where a formula silently
redefines the functional unit.
"""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from collections import defaultdict

import bw2data as bd

sys.path.insert(0, __file__.rsplit("/", 2)[0])

from fedefl_bw25.config import BIOSPHERE_DB, PROJECT_NAME     # noqa: E402

GWP = "Global warming"


def find_activity(database, uuid=None, search=None):
    db = bd.Database(database)
    if uuid:
        matches = [a for a in db if a["code"] == uuid]
    else:
        needle = search.lower()
        matches = [a for a in db if needle in a["name"].lower()]
    if not matches:
        raise SystemExit(f"No activity in '{database}' matching "
                         f"{uuid or search!r}.")
    if len(matches) > 1 and not uuid:
        print(f"  {len(matches)} activities match {search!r}; using the first:")
        for a in matches[:8]:
            print(f"    {a['code']}  {a['name'][:60]}")
        print()
    return matches[0]


def gwp_factors():
    """Biosphere node id -> TRACI 2.2 global-warming CF."""
    method = next((m for m in bd.methods if GWP.lower() in " ".join(m).lower()), None)
    if method is None:
        raise SystemExit("No TRACI 2.2 'Global warming' method. Run setup/02.")
    return dict(bd.Method(method).load()), method


def source_amounts(zip_path):
    """proc uuid -> {internalId: (amount, formula, is_ref)} from the source JSON."""
    out = {}
    with zipfile.ZipFile(zip_path) as z:
        for n in z.namelist():
            if not (n.startswith("processes/") and n.endswith(".json")):
                continue
            d = json.loads(z.read(n))
            rows = {}
            for e in d.get("exchanges") or []:
                formula = e.get("amountFormula") or e.get("formula")
                rows[e.get("internalId")] = (e.get("amount"), formula,
                                             bool(e.get("isQuantitativeReference")))
            out[d.get("@id")] = rows
    return out


def source_processes(zip_path):
    """proc uuid -> the raw process dict, for shape questions the built DB cannot answer."""
    out = {}
    with zipfile.ZipFile(zip_path) as z:
        for n in z.namelist():
            if n.startswith("processes/") and n.endswith(".json"):
                d = json.loads(z.read(n))
                out[d.get("@id")] = d
    return out


def allocation_evidence(proc, act, database):
    """Compare each built biosphere amount to its evaluated source amount.

    The ratio between them IS the allocation factor -- allocation is applied to
    every non-reference exchange on the way in, and nothing downstream records
    what it was. A process that is multi-output by ACCIDENT (a product input
    missing `isInput: true` reads as a co-product) gets its whole inventory scaled
    by the reference product's share, which looks exactly like a correct build
    returning a wrong number.
    """
    from fedefl_bw25 import olca_formula, olca_parameters

    try:
        scope = olca_parameters.process_scope(proc, olca_parameters.resolve([]))
    except olca_parameters.ParameterError as exc:
        return None, f"source parameters do not resolve: {str(exc).splitlines()[0]}"

    src_by_flow = {}
    product_outputs = []
    for e in proc.get("exchanges") or []:
        flow = e.get("flow") or {}
        ftype = flow.get("flowType") or ""
        if not e.get("isInput") and ftype == "PRODUCT_FLOW":
            product_outputs.append(flow.get("name") or flow.get("@id"))
        if ftype != "ELEMENTARY_FLOW":
            continue
        formula = e.get("amountFormula") or e.get("formula")
        amount = e.get("amount")
        if formula:
            try:
                amount = float(olca_formula.evaluate(formula, scope.values))
            except olca_formula.FormulaError:
                continue
        if amount:
            src_by_flow[flow.get("@id")] = amount

    ratios = []
    for e in act.exchanges():
        if e["type"] != "biosphere":
            continue
        src = src_by_flow.get(e.input["code"])
        if src:
            ratios.append((e.input["name"], e["amount"], src, e["amount"] / src))
    return (ratios, product_outputs), None


def report(act, database, src=None, procs=None):
    cfs, method = gwp_factors()
    exchanges = list(act.exchanges())
    prod = [e for e in exchanges if e["type"] == "production"]
    bio = [e for e in exchanges if e["type"] == "biosphere"]
    tech = [e for e in exchanges if e["type"] == "technosphere"]

    print("=" * 72)
    print(f"  {act['name']}")
    print(f"  {database} / {act['code']}")
    print("=" * 72)

    # ---- functional unit ------------------------------------------------
    # The raw exchange amounts are stated per the production amount, while
    # run_lca normalizes to one reference unit. Everything below is divided by
    # this so the direct figure and the total are on the same basis -- USLCI
    # routinely declares 0.001 m3, which makes the two differ by 1000x.
    ref_amount = prod[0]["amount"] if prod else 1.0

    print("\n  FUNCTIONAL UNIT (production exchange)")
    if not prod:
        print("    NONE — no production exchange. Every result will be meaningless.")
    for e in prod:
        print(f"    amount = {e['amount']!r}  unit = {e.get('unit')!r}")
        if e["amount"] != 1.0:
            print(f"    inventory below is stated per {e['amount']!r} {e.get('unit')}; "
                  f"scaled by 1/{e['amount']!r} to match the per-unit total.")
        if e["amount"] == 0:
            print("    !! ZERO — the process produces nothing, so nothing can be "
                  "normalized to it. This alone explains a nonsense result.")

    # ---- biosphere ------------------------------------------------------
    print(f"\n  BIOSPHERE — {len(bio)} exchange(s)")
    direct = 0.0
    uncharacterized = []
    for e in bio:
        cf = cfs.get(e.input.id)
        if cf is None:
            uncharacterized.append(e)
            continue
        contribution = e["amount"] * cf / (ref_amount or 1.0)
        direct += contribution
        if abs(contribution) > 0:
            print(f"    {e.input['name'][:42]:42} {e['amount']:>12.6g} "
                  f"x {cf:<8.4g} = {contribution:>12.6g}")
    if uncharacterized:
        print(f"    {len(uncharacterized)} flow(s) matched {BIOSPHERE_DB} but have NO "
              f"{GWP} CF — carried, contributing nothing:")
        for e in uncharacterized[:6]:
            print(f"      {e.input['name'][:50]} ({e['amount']:g})")
    print(f"    direct {GWP} = {direct:.6g}")

    # ---- technosphere ---------------------------------------------------
    print(f"\n  TECHNOSPHERE — {len(tech)} exchange(s)")
    by_db = defaultdict(int)
    for e in tech:
        by_db[e.input[0]] += 1
    for dbname, count in sorted(by_db.items()):
        tag = "  <- SELF" if dbname == database else ""
        print(f"    {count:>5} linked into '{dbname}'{tag}")
    if not tech:
        print("    none — this process has no inputs, so its score is its direct "
              "emissions only.")

    # ---- source comparison ----------------------------------------------
    if src is not None:
        rows = src.get(act["code"])
        if rows is None:
            print(f"\n  SOURCE JSON — no process {act['code']} in the zip.")
        else:
            print("\n  SOURCE JSON vs BUILT")
            formula_on_ref = [(i, a, f) for i, (a, f, r) in rows.items() if r and f]
            if formula_on_ref:
                print("    !! the REFERENCE exchange carries a formula. Evaluating it "
                      "redefines the functional unit:")
                for i, a, f in formula_on_ref:
                    print(f"       internalId {i}: stored {a!r}, formula {f!r}")
            else:
                print("    reference exchange carries no formula (functional unit "
                      "is not parameterized).")
            n_formula = sum(1 for a, f, r in rows.values() if f)
            print(f"    {n_formula} of {len(rows)} exchange(s) carry a formula.")

    if procs is not None:
        proc = procs.get(act["code"])
        if proc is not None:
            print("\n  ALLOCATION (built amount vs evaluated source amount)")
            evidence, err = allocation_evidence(proc, act, database)
            if err:
                print(f"    could not compare: {err}")
            else:
                ratios, product_outputs = evidence
                if len(product_outputs) > 1:
                    print(f"    !! {len(product_outputs)} PRODUCT OUTPUTS — this process "
                          f"is multi-output, so every non-reference")
                    print(f"       exchange is scaled by the reference product's "
                          f"allocation share:")
                    for name in product_outputs[:8]:
                        print(f"         {name}")
                    print(f"       If only one of these is a real co-product, the rest "
                          f"are inputs missing")
                    print(f"       `isInput: true` — which also explains unresolved "
                          f"providers, since a")
                    print(f"       mis-flagged input is never linked.")
                elif product_outputs:
                    print(f"    single product output — no allocation applied.")
                if not ratios:
                    print("    no biosphere exchange could be matched to the source.")
                else:
                    distinct = {round(r, 9) for _n, _b, _s, r in ratios}
                    for name, built, src, r in ratios[:8]:
                        print(f"    {name[:38]:38} built {built:>11.5g}  "
                              f"source {src:>11.5g}  x{r:<.6g}")
                    if len(distinct) == 1:
                        factor = next(iter(distinct))
                        if abs(factor - 1.0) < 1e-9:
                            print("    -> ratio is 1.0 throughout: no scaling was "
                                  "applied. Amounts are as authored.")
                        else:
                            print(f"    -> EVERY flow scaled by {factor:.6g}. That is an "
                                  f"allocation factor, and it is")
                            print(f"       dividing this process's whole inventory by "
                                  f"{1/factor:.6g}.")
                    else:
                        print(f"    -> {len(distinct)} distinct ratios — causal "
                              f"allocation (per-exchange factors), or a unit "
                              f"conversion on some flows.")

    print(f"\n  Method: {' / '.join(method)}")
    print("=" * 72)
    return direct


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[3])
    ap.add_argument("--database", required=True)
    ap.add_argument("--uuid")
    ap.add_argument("--search")
    ap.add_argument("--against", help="source olca-schema zip, to compare amounts")
    ap.add_argument("--project", default=PROJECT_NAME)
    args = ap.parse_args(argv)
    if not (args.uuid or args.search):
        ap.error("give --uuid or --search")

    bd.projects.set_current(args.project)
    if args.database not in bd.databases:
        raise SystemExit(f"No database '{args.database}'. Built: "
                         f"{sorted(bd.databases)}")

    src = source_amounts(args.against) if args.against else None
    procs = source_processes(args.against) if args.against else None
    act = find_activity(args.database, args.uuid, args.search)
    direct = report(act, args.database, src, procs)

    from fedefl_bw25.run import run_lca
    run = run_lca(uuid=act["code"], database=args.database, smoke_test=False,
                  contributions=False, manifest=False)
    total = run.score(GWP)
    print(f"\n  TOTAL {GWP} = {total:.6g}   ({run.functional_unit})")
    print(f"    direct      = {direct:.6g}")
    print(f"    supply chain= {total - direct:.6g}")
    if total and abs(direct / total) > 0.999:
        print("    -> essentially ALL of it is direct. The supply chain contributed "
              "nothing: inputs are cut, or there are none.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
