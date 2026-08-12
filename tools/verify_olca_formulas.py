#!/usr/bin/env python3
"""
verify_olca_formulas.py
-----------------------
Adjudicate this project's formula evaluator against openLCA's own arithmetic.

Why this exists
---------------
openLCA writes the last value it evaluated into every dependent parameter's
`value` field and every parameterized exchange's `amount` field. Those fields are
never needed to build -- the formula is authoritative once `fedefl_bw25` can
evaluate it -- which makes them something better: a few thousand cases where an
independent, mature implementation has already computed the answer.

This re-derives each one from its formula and compares. Passing does not prove the
evaluator correct in general; it proves that on THIS dataset it agrees with
openLCA everywhere openLCA left an answer, which is the claim a build needs.

    python tools/verify_olca_formulas.py path/to/study.zip

Deliberate constraints
----------------------
* Standard library plus `fedefl_bw25.olca_formula` / `.olca_parameters`, both of
  which are stdlib-only. No brightway, no project env, no install step -- run it
  from a checkout on whatever machine has the zip.
* Read-only. The zip is opened for reading and nothing is written to it.
* Parameter names, process names and formula text are REDACTED unless you pass
  --show-names, so the output can be pasted into a conversation. Read it first.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import zipfile
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fedefl_bw25.olca_formula import (            # noqa: E402
    FormulaError, UnsupportedConstruct, eval_ast, parse)
from fedefl_bw25.olca_parameters import (         # noqa: E402
    ParameterError, collect_parameters, resolve)

FORMULA_KEYS = ("amountFormula", "formula")

# Agreement threshold. openLCA stores doubles and the JSON round-trip is
# lossless, so a correct re-derivation should land far inside this -- it is set
# to catch a systematically different interpretation (wrong precedence, wrong
# associativity, `^` read as XOR), not to absorb float noise.
DEFAULT_TOLERANCE = 1e-9


def rel_error(computed, declared):
    """Relative difference, falling back to absolute when the reference is 0."""
    if declared == computed:
        return 0.0
    if declared == 0:
        return abs(computed)
    return abs(computed - declared) / abs(declared)


def redact(text, show):
    """Names and formula text are the dataset's content, not its shape."""
    if show:
        return text
    return f"<redacted:{len(str(text))}c>"


# =============================================================================
# VERIFICATION
# =============================================================================
def verify(zip_path, tolerance=DEFAULT_TOLERANCE):
    """Re-derive every stored value in the zip. Returns a report dict."""
    r = {
        "zip": str(zip_path),
        "tolerance": tolerance,
        "processes": 0,
        "param_checked": 0,
        "param_agreed": 0,
        "param_stored_zero": 0,     # openLCA left `value` at 0 (often unpopulated)
        "param_stored_zero_nonzero": 0,   # ...and we computed something else
        "param_worst": [],          # (rel, name, computed, declared)
        "exch_formulas": 0,
        "exch_checked": 0,          # had a stored amount to compare against
        "exch_agreed": 0,
        "exch_worst": [],
        "exch_no_amount": 0,        # `amount` key absent -- nothing to check
        "exch_no_amount_values": [],  # what they evaluate to now
        "exch_zero_amount": 0,      # stored amount is 0: checked, reported apart
        "exch_zero_disagreed": [],  # ...and we computed something else
        "ref_formulas": 0,          # REFERENCE exchanges carrying a formula
        "ref_changed": 0,           # ...where evaluating moves the functional unit
        "ref_examples": [],
        "refused": Counter(),       # construct -> count
        "refused_examples": {},
        "scope_errors": [],
        "bad_json": [],
    }

    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()

        # ---- global scope -------------------------------------------------
        global_params = []
        for name in names:
            if name.startswith("parameters/") and name.endswith(".json"):
                try:
                    global_params.extend(collect_parameters(json.loads(z.read(name))))
                except (ValueError, UnicodeDecodeError):
                    r["bad_json"].append(name)
        try:
            globals_ = resolve(global_params, where="the global parameter scope")
        except ParameterError as exc:
            r["scope_errors"].append(("<global>", str(exc)))
            globals_ = resolve([])
        r["global_params"] = len(globals_.values)

        _record_checks(r, globals_.checks, "param", tolerance)

        # ---- per process --------------------------------------------------
        for name in names:
            if not (name.startswith("processes/") and name.endswith(".json")):
                continue
            try:
                proc = json.loads(z.read(name))
            except (ValueError, UnicodeDecodeError):
                r["bad_json"].append(name)
                continue
            r["processes"] += 1
            label = proc.get("name") or proc.get("@id") or name

            try:
                scope = resolve(collect_parameters(proc), globals_,
                                where=f"process {label!r}")
            except ParameterError as exc:
                r["scope_errors"].append((label, str(exc)))
                continue
            _record_checks(r, scope.checks, "param", tolerance)

            for exc_dict in proc.get("exchanges") or []:
                if not isinstance(exc_dict, dict):
                    continue
                formula = next((exc_dict[k] for k in FORMULA_KEYS
                                if k in exc_dict and exc_dict[k]), None)
                if formula is None:
                    continue
                r["exch_formulas"] += 1
                is_ref = bool(exc_dict.get("isQuantitativeReference"))
                if is_ref:
                    # A formula on the REFERENCE exchange is a different kind of
                    # problem from a formula anywhere else: evaluating it changes
                    # the process's own production amount, which is the basis every
                    # result is normalized to. The scanner never flagged these --
                    # it only checked product outputs of MULTI-output processes --
                    # so a single-output process can carry one unnoticed.
                    r["ref_formulas"] += 1

                try:
                    computed = float(eval_ast(parse(formula), scope.values))
                except UnsupportedConstruct as exc:
                    _record_refusal(r, formula, exc, "unsupported")
                    continue
                except FormulaError as exc:
                    _record_refusal(r, formula, exc, "error")
                    continue

                # A MISSING `amount` is the only case with nothing to compare
                # against. A stored 0 is a real reference value and must be
                # checked like any other -- these are the exchanges the importer
                # currently hard-stops on, so "openLCA says 0 and so do we" is
                # precisely the claim that clears them.
                declared = exc_dict.get("amount")
                if declared is None:
                    r["exch_no_amount"] += 1
                    if len(r["exch_no_amount_values"]) < 25:
                        r["exch_no_amount_values"].append(
                            (label, formula, declared, computed))
                    continue

                declared = float(declared)
                if is_ref and rel_error(computed, declared) > tolerance:
                    r["ref_changed"] += 1
                    if len(r["ref_examples"]) < 15:
                        r["ref_examples"].append((label, declared, computed))
                r["exch_checked"] += 1
                if declared == 0:
                    r["exch_zero_amount"] += 1
                    if computed != 0 and len(r["exch_zero_disagreed"]) < 25:
                        r["exch_zero_disagreed"].append((label, formula, computed))
                err = rel_error(computed, declared)
                if err <= tolerance:
                    r["exch_agreed"] += 1
                r["exch_worst"].append((err, formula, computed, declared))

    for key in ("param_worst", "exch_worst"):
        r[key].sort(key=lambda t: -t[0])
        del r[key][20:]
    return r


def _record_checks(r, checks, prefix, tolerance):
    """Compare each computed parameter against the value openLCA stored.

    A stored 0 is bucketed separately rather than counted as a disagreement.
    openLCA does not reliably populate `value` for a DEPENDENT parameter on
    export -- in USLCI 243 of 306 are left at 0 while the exchange amounts
    computed from those same parameters are exact -- so scoring them as failures
    buries the real signal under a systematic export artefact. They are still
    counted and reported, because a stored 0 that should be 0 is genuine
    agreement and only the dataset can say which is which.
    """
    for name, computed, declared in checks:
        if declared == 0:
            r[f"{prefix}_stored_zero"] += 1
            if computed != 0:
                r[f"{prefix}_stored_zero_nonzero"] += 1
            continue
        r[f"{prefix}_checked"] += 1
        err = rel_error(computed, declared)
        if err <= tolerance:
            r[f"{prefix}_agreed"] += 1
        r[f"{prefix}_worst"].append((err, name, computed, declared))
        r[f"{prefix}_worst"].sort(key=lambda t: -t[0])
        del r[f"{prefix}_worst"][20:]


def _record_refusal(r, formula, exc, kind):
    first = str(exc).splitlines()[0]
    r["refused"][first] += 1
    r["refused_examples"].setdefault(first, formula)


# =============================================================================
# REPORT
# =============================================================================
def render(r, show_names=False):
    out = []
    add = out.append

    add("=" * 68)
    add("  openLCA formula verification")
    add("=" * 68)
    add(f"  zip                : {r['zip']}")
    add(f"  processes          : {r['processes']}")
    add(f"  global parameters  : {r.get('global_params', 0)}")
    add(f"  agreement tolerance: {r['tolerance']:g} relative")
    add("")

    add("  Dependent parameters (vs openLCA's stored `value`)")
    add(f"    checked          : {r['param_checked']}")
    add(f"    agreed           : {r['param_agreed']}")
    _disagreement_block(add, r, "param", show_names)
    if r["param_stored_zero"]:
        add(f"    stored 0         : {r['param_stored_zero']}  (excluded — openLCA "
            f"often leaves a dependent's `value` unpopulated)")
        add(f"        of those, {r['param_stored_zero_nonzero']} evaluate non-zero. "
            f"The exchange figures below are the real check on these:")
        add(f"        they are computed FROM these parameters, so an error here "
            f"cannot leave them exact.")
    add("")

    add("  Exchange amounts (vs openLCA's stored `amount`)")
    add(f"    formulas found   : {r['exch_formulas']}")
    add(f"    checked          : {r['exch_checked']}")
    add(f"    agreed           : {r['exch_agreed']}")
    _disagreement_block(add, r, "exch", show_names)
    add(f"    of which stored 0: {r['exch_zero_amount']}  "
        f"(the ones the importer currently hard-stops on)")
    if r["exch_zero_disagreed"]:
        add(f"        {len(r['exch_zero_disagreed'])} of those are NOT zero when "
            f"evaluated -- openLCA's stored 0 is stale:")
        for label, formula, computed in r["exch_zero_disagreed"][:10]:
            add(f"        {redact(label, show_names)}: "
                f"stored 0 -> evaluates to {computed!r}")
    add(f"    no `amount` field: {r['exch_no_amount']}  "
        f"(nothing to compare; these are what evaluation ADDS)")
    if r["ref_formulas"]:
        add("")
        add(f"  !! REFERENCE exchanges carrying a formula: {r['ref_formulas']}")
        add(f"     Evaluating one changes the process's own production amount — the "
            f"basis every")
        add(f"     result is normalized to. {r['ref_changed']} of them evaluate to "
            f"something OTHER")
        add(f"     than the stored amount, so those functional units MOVED:")
        for label, declared, computed in r["ref_examples"][:10]:
            factor = (computed / declared) if declared else float("inf")
            add(f"       {redact(label, show_names)}: {declared!r} -> {computed!r} "
                f"({factor:.4g}x — results scale by 1/{factor:.4g})")
    for label, formula, declared, computed in r["exch_no_amount_values"][:10]:
        add(f"        {redact(label, show_names)}: "
            f"stored {declared!r} -> evaluates to {computed!r}")
    add("")

    total_checked = r["param_checked"] + r["exch_checked"]
    total_agreed = r["param_agreed"] + r["exch_agreed"]
    add("  Constructs refused")
    if not r["refused"]:
        add("    (none -- every formula parsed and evaluated)")
    else:
        for reason, count in r["refused"].most_common(15):
            add(f"    {count:5}  {reason}")
            add(f"           e.g. {redact(r['refused_examples'][reason], show_names)}")
    add("")

    if r["scope_errors"]:
        add(f"  Scopes that failed to resolve : {len(r['scope_errors'])}")
        for label, msg in r["scope_errors"][:5]:
            add(f"    {redact(label, show_names)}: {msg.splitlines()[0]}")
        add("")
    if r["bad_json"]:
        add(f"  Unreadable JSON files : {len(r['bad_json'])}")
        add("")

    add("-" * 68)
    if total_checked and total_agreed == total_checked and not r["refused"]:
        add(f"  PASS  {total_agreed}/{total_checked} stored values re-derived exactly.")
    elif total_checked:
        add(f"  {total_agreed}/{total_checked} agreed "
            f"({total_checked - total_agreed} disagreements, "
            f"{sum(r['refused'].values())} refused).")
    else:
        add("  Nothing to check -- no stored values found.")
    add("-" * 68)
    if not show_names:
        add("  Names and formula text are redacted. Pass --show-names to see them.")
    return "\n".join(out)


def _disagreement_block(add, r, prefix, show_names):
    worst = r[f"{prefix}_worst"]
    disagreed = [w for w in worst if w[0] > r["tolerance"]]
    if not disagreed:
        if worst:
            add(f"    worst rel. error : {worst[0][0]:.3g}")
        return
    add(f"    DISAGREED        : {len(disagreed)} shown (worst first)")
    for err, label, computed, declared in disagreed[:10]:
        add(f"        rel {err:>10.3g}  computed {computed!r}  "
            f"openLCA {declared!r}   {redact(label, show_names)}")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Verify the formula evaluator against openLCA's stored values.")
    ap.add_argument("zip", help="an olca-schema JSON-LD zip")
    ap.add_argument("--show-names", action="store_true",
                    help="do not redact parameter/process names or formula text")
    ap.add_argument("--json", action="store_true",
                    help="emit the raw report as JSON")
    ap.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE,
                    help=f"relative agreement threshold (default {DEFAULT_TOLERANCE:g})")
    args = ap.parse_args(argv)

    if not os.path.exists(args.zip):
        ap.error(f"no such file: {args.zip}")

    try:
        report = verify(args.zip, tolerance=args.tolerance)
    except zipfile.BadZipFile:
        ap.error(f"{args.zip} is not a readable zip")

    if args.json:
        report["refused"] = dict(report["refused"])
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render(report, show_names=args.show_names))

    disagreed = ((report["param_checked"] - report["param_agreed"])
                 + (report["exch_checked"] - report["exch_agreed"]))
    return 1 if (disagreed or report["refused"] or report["scope_errors"]) else 0


if __name__ == "__main__":
    sys.exit(main())
