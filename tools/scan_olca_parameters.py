#!/usr/bin/env python3
"""
scan_olca_parameters.py
-----------------------
Read-only survey of parameter and formula usage in an olca-schema JSON-LD zip.

Why this exists
---------------
`fedefl_bw25` has no parameter engine: the number that reaches the matrix is the
literal `amount` field, and a formula beside it is discarded at import. Sizing the
work to change that needs to know which slice of openLCA's formula dialect a real
dataset actually uses -- openLCA supports 40+ functions, comparison and logical
operators, `div`/`mod`, and `^` as exponentiation, and implementing all of it is a
very different job from implementing `a * b`.

This script answers that question WITHOUT the dataset leaving the machine it is on.
It reports shapes and counts, not content: which operators and functions appear, how
formulas are distributed, whether global parameters are used, whether any name is
shadowed, and whether the dependent-parameter graph has cycles. Parameter names and
formula text are redacted unless you pass --show-names.

The output is meant to be pasted into a conversation, so read it first and confirm
you are happy with it.

Deliberate constraints
----------------------
* Standard library only. No brightway, no numpy, no install step, no project env.
  Runs anywhere with Python 3.8+.
* Read-only. The zip is opened for reading and nothing is ever written to it.
* Never raises on bad input if it can help it. A malformed JSON file is counted and
  named, not fatal -- a survey that dies on file 300 of 400 is useless.

Usage
-----
    python tools/scan_olca_parameters.py path/to/my_study.zip
    python tools/scan_olca_parameters.py my_study.zip --json > report.json
    python tools/scan_olca_parameters.py my_study.zip --show-names   # no redaction
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import zipfile
from collections import Counter, defaultdict


# =============================================================================
# openLCA formula dialect
# =============================================================================
# Sources: openLCA 2 manual, "Constants, operators and functions for formulas".
# Longest match first -- '<=' must be tried before '<', '&&' before '&'.
OPERATORS = (
    "<=", ">=", "==", "!=", "<>", "&&", "||",
    "<", ">", "=", "&", "|", "+", "-", "*", "/", "^",
)
# Infix operators spelled as words. These are why Python's `ast` cannot parse
# openLCA formulas at all: `a div b` is a SyntaxError, not an expression.
WORD_OPERATORS = {"div", "mod"}
CONSTANTS = {"pi", "e"}

# The subset a minimal evaluator would support. Everything else is a hard stop
# under the proposed design, so the counts against this set are the estimate.
CORE_OPERATORS = {"+", "-", "*", "/", "^"}

NUMBER_RE = re.compile(r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?")
IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")

# Keys whose string value is a formula expression. `amountFormula` is the v1
# spelling; `formula` appears on Parameter and on some hand-written exports.
FORMULA_KEYS = ("amountFormula", "formula")


class Lexed:
    """What one formula is made of. Everything the estimate depends on."""

    __slots__ = ("operators", "word_operators", "functions", "variables",
                 "separators", "unknown", "numbers", "error")

    def __init__(self):
        self.operators = set()
        self.word_operators = set()
        self.functions = set()
        self.variables = set()
        self.separators = set()      # ';' or ',' seen inside a call's arguments
        self.unknown = set()         # characters the dialect does not explain
        self.numbers = 0
        self.error = None

    def outside_core(self):
        """Constructs that a core-arithmetic evaluator would have to refuse."""
        return ((self.operators - CORE_OPERATORS)
                | self.word_operators
                | {f + "()" for f in self.functions})


def lex(formula):
    """Tokenize an openLCA formula. Never raises; unparseable bytes land in
    `unknown` so a dialect surprise is reported rather than swallowed.

    Not a parser -- it does not check that the expression is well-formed. It only
    identifies which constructs are present, which is what sizing the work needs.
    """
    out = Lexed()
    if not isinstance(formula, str):
        out.error = f"formula is {type(formula).__name__}, not a string"
        return out

    s = formula
    i, n = 0, len(s)
    depth = 0
    while i < n:
        ch = s[i]

        if ch.isspace():
            i += 1
            continue

        # Number. Tried before identifiers so `1e-3` lexes as one number and not
        # as `1` `e` `-` `3` -- `e` is also a constant, so order matters here.
        if ch.isdigit() or (ch == "." and i + 1 < n and s[i + 1].isdigit()):
            m = NUMBER_RE.match(s, i)
            if m:
                out.numbers += 1
                i = m.end()
                continue

        # Identifier: a function if followed by '(', else a word operator,
        # a constant, or a variable reference.
        if ch.isalpha() or ch == "_":
            m = IDENT_RE.match(s, i)
            name = m.group(0)
            j = m.end()
            k = j
            while k < n and s[k].isspace():
                k += 1
            if k < n and s[k] == "(":
                out.functions.add(name.lower())
            elif name.lower() in WORD_OPERATORS:
                out.word_operators.add(name.lower())
            elif name.lower() not in CONSTANTS:
                out.variables.add(name)
            i = j
            continue

        if ch == "(":
            depth += 1
            i += 1
            continue
        if ch == ")":
            depth = max(0, depth - 1)
            i += 1
            continue

        # Argument separators only mean something inside a call. openLCA's manual
        # documents ';' -- a ',' here would be a real finding, so both are recorded.
        if ch in ";," and depth > 0:
            out.separators.add(ch)
            i += 1
            continue

        for op in OPERATORS:
            if s.startswith(op, i):
                out.operators.add(op)
                i += len(op)
                break
        else:
            out.unknown.add(ch)
            i += 1

    return out


# =============================================================================
# Zip reading
# =============================================================================
def find_prefix(names):
    """The path prefix the package sits under ('' when correctly zipped).

    An openLCA export zipped one level too deep puts everything under
    `my_study/processes/...`. That is the most common packaging mistake, and there
    is no reason for this survey to fail on it.
    """
    for name in names:
        if name.endswith(".json") and "processes/" in name:
            return name[: name.index("processes/")]
    return ""


def read_folder(zf, prefix, folder):
    """Yield (entry_name, parsed_json) for one package folder.

    A file that will not parse is yielded as (name, None) so the caller can count
    it. One bad file must not end the survey.
    """
    want = f"{prefix}{folder}/"
    for name in zf.namelist():
        if not (name.startswith(want) and name.endswith(".json")):
            continue
        if name.endswith("/"):
            continue
        try:
            yield name, json.loads(zf.read(name))
        except (json.JSONDecodeError, UnicodeDecodeError):
            yield name, None


def walk_formulas(obj, path, out):
    """Every formula-valued key anywhere in a document, as (json_path, text).

    A generic sweep rather than a fixed list of locations: `costFormula` and
    allocation-factor formulas exist too, and a location nobody anticipated is
    exactly what this survey should surface. Array indices collapse to `[]` so the
    paths aggregate.
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            child = f"{path}.{k}" if path else k
            if isinstance(v, str) and (k in FORMULA_KEYS or k.endswith("Formula")):
                out.append((child, v))
            else:
                walk_formulas(v, child, out)
    elif isinstance(obj, list):
        for item in obj:
            walk_formulas(item, f"{path}[]", out)


# =============================================================================
# Parameter model
# =============================================================================
def collect_parameters(doc):
    """A document's parameters as [{name, formula, value, is_input, scope}].

    Handles both the process-level `parameters` array and a standalone global
    Parameter document, which have the same field names.
    """
    if doc.get("@type") == "Parameter" or ("parameterScope" in doc and "name" in doc
                                           and "exchanges" not in doc):
        raw = [doc]
    else:
        raw = doc.get("parameters") or []

    params = []
    for p in raw:
        if not isinstance(p, dict):
            continue
        params.append({
            "name": (p.get("name") or "").strip(),
            "formula": p.get("formula"),
            "value": p.get("value"),
            "is_input": p.get("isInputParameter"),
            "scope": p.get("parameterScope") or "",
        })
    return [p for p in params if p["name"]]


def find_cycles(edges):
    """Cycles in a name -> {referenced names} graph, as lists of names.

    Dependent parameters may reference other dependent parameters, so resolution
    is a topological walk and a cycle is a hard stop rather than a warning. Plain
    iterative DFS with a colour map; the graph is tiny.
    """
    WHITE, GREY, BLACK = 0, 1, 2
    color = defaultdict(int)
    cycles = []

    def visit(start):
        stack = [(start, iter(sorted(edges.get(start, ()))))]
        path = [start]
        color[start] = GREY
        while stack:
            node, it = stack[-1]
            advanced = False
            for nxt in it:
                if nxt not in edges:
                    continue                      # not a parameter; a leaf
                if color[nxt] == GREY:
                    cycles.append(path[path.index(nxt):] + [nxt])
                elif color[nxt] == WHITE:
                    color[nxt] = GREY
                    path.append(nxt)
                    stack.append((nxt, iter(sorted(edges.get(nxt, ())))))
                    advanced = True
                break
            if not advanced:
                stack.pop()
                color[node] = BLACK
                if path:
                    path.pop()

    for name in sorted(edges):
        if color[name] == WHITE:
            visit(name)
    return cycles


# =============================================================================
# Survey
# =============================================================================
def scan(zip_path):
    """Everything the report prints, as a plain dict."""
    r = {
        "zip": str(zip_path),
        "folders": {},
        "unreadable_files": [],
        "processes": 0,
        "exchanges": 0,
        "exchange_formulas": 0,
        "exchange_formula_amount_nonzero": 0,
        "exchange_formula_amount_zero": 0,
        "exchange_formula_amount_missing": 0,
        "formula_locations": {},
        "operators": Counter(),
        "word_operators": Counter(),
        "functions": Counter(),
        "separators": Counter(),
        "unknown_chars": Counter(),
        "lex_errors": 0,
        "formulas_total": 0,
        "formulas_core_only": 0,
        "max_formula_len": 0,
        "global_parameters": 0,
        "global_input": 0,
        "global_dependent": 0,
        "process_parameters": 0,
        "process_input": 0,
        "process_dependent": 0,
        "scopes_seen": Counter(),
        "shadowed_names": [],
        "undefined_refs": [],
        "cycles": [],
        "multi_output_processes": 0,
        "multi_output_with_formula": 0,
        "processes_with_formulas": 0,
    }

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        prefix = find_prefix(names)
        r["nested_prefix"] = prefix or None
        top = set()
        for nm in names:
            rest = nm[len(prefix):]
            if "/" in rest:
                top.add(rest.split("/")[0])
        r["folders"] = sorted(top)

        # --- global parameters -------------------------------------------------
        global_names = {}
        for name, doc in read_folder(zf, prefix, "parameters"):
            if doc is None:
                r["unreadable_files"].append(name)
                continue
            for p in collect_parameters(doc):
                global_names[p["name"]] = p
                r["global_parameters"] += 1
                r["scopes_seen"][p["scope"] or "(unset)"] += 1
                if p["formula"]:
                    r["global_dependent"] += 1
                else:
                    r["global_input"] += 1

        # --- processes ---------------------------------------------------------
        global_edges = {}
        for nm, p in global_names.items():
            if p["formula"]:
                global_edges[nm] = lex(p["formula"]).variables

        all_undefined = Counter()
        for name, doc in read_folder(zf, prefix, "processes"):
            if doc is None:
                r["unreadable_files"].append(name)
                continue
            r["processes"] += 1

            local = {p["name"]: p for p in collect_parameters(doc)}
            for p in local.values():
                r["process_parameters"] += 1
                r["scopes_seen"][p["scope"] or "(unset)"] += 1
                if p["formula"]:
                    r["process_dependent"] += 1
                else:
                    r["process_input"] += 1
                if p["name"] in global_names:
                    r["shadowed_names"].append(p["name"])

            exchanges = doc.get("exchanges") or []
            r["exchanges"] += len(exchanges)

            # Multi-output detection: the allocation factors are derived from
            # exchange amounts BEFORE any evaluation would run, so a parameterized
            # product output is the one case where evaluation order changes results.
            product_outputs = [
                e for e in exchanges
                if isinstance(e, dict) and not e.get("isInput")
                and (e.get("flow") or {}).get("flowType") == "PRODUCT_FLOW"
            ]
            if len(product_outputs) > 1:
                r["multi_output_processes"] += 1
                if any(any(k in e for k in FORMULA_KEYS) for e in product_outputs):
                    r["multi_output_with_formula"] += 1

            for e in exchanges:
                if not isinstance(e, dict):
                    continue
                formula = next((e[k] for k in FORMULA_KEYS if k in e), None)
                if formula is None:
                    continue
                r["exchange_formulas"] += 1
                # Three buckets, not two. An explicit `amount: 0` and a missing
                # `amount` are the same falsy value but different mistakes: the
                # first is a modeller writing zero (or a tool storing a parametric
                # exchange as zero), the second is nothing having evaluated it at
                # all. Anything that later decides whether to stop a build needs
                # them apart, so don't merge them here either.
                if "amount" not in e:
                    r["exchange_formula_amount_missing"] += 1
                elif not e.get("amount"):
                    r["exchange_formula_amount_zero"] += 1
                else:
                    r["exchange_formula_amount_nonzero"] += 1

            # Every formula in the document, wherever it lives.
            found = []
            walk_formulas(doc, "", found)
            if found:
                r["processes_with_formulas"] += 1
            resolvable = set(local) | set(global_names) | CONSTANTS

            for path, text in found:
                r["formula_locations"][path] = r["formula_locations"].get(path, 0) + 1
                lx = lex(text)
                r["formulas_total"] += 1
                r["max_formula_len"] = max(r["max_formula_len"], len(str(text)))
                if lx.error:
                    r["lex_errors"] += 1
                    continue
                for o in lx.operators:
                    r["operators"][o] += 1
                for o in lx.word_operators:
                    r["word_operators"][o] += 1
                for f in lx.functions:
                    r["functions"][f] += 1
                for s in lx.separators:
                    r["separators"][s] += 1
                for c in lx.unknown:
                    r["unknown_chars"][c] += 1
                if not lx.outside_core():
                    r["formulas_core_only"] += 1
                for v in lx.variables:
                    if v not in resolvable:
                        all_undefined[v] += 1

            local_edges = dict(global_edges)
            for nm2, p in local.items():
                if p["formula"]:
                    local_edges[nm2] = lex(p["formula"]).variables
            for cyc in find_cycles(local_edges):
                if cyc not in r["cycles"]:
                    r["cycles"].append(cyc)

        r["undefined_refs"] = all_undefined.most_common()
        r["shadowed_names"] = sorted(set(r["shadowed_names"]))
    return r


# =============================================================================
# Report
# =============================================================================
class Redactor:
    """Stable placeholders unless --show-names. Shapes travel; identifiers don't.

    One mapping for the whole report, so the same parameter reads as the same
    placeholder everywhere and two different ones never collide. A per-list
    mapping would print the cycle a -> b -> a as three distinct names, which
    hides the one thing that makes it a cycle.
    """

    def __init__(self, show):
        self.show = show
        self._seen = {}

    def __call__(self, names):
        if self.show:
            return list(names)
        out = []
        for n in names:
            if n not in self._seen:
                self._seen[n] = f"<name{len(self._seen) + 1}>"
            out.append(self._seen[n])
        return out


def report(r, show_names=False):
    L = []
    add = L.append
    redact = Redactor(show_names)
    add("=" * 68)
    add("  olca-schema parameter/formula survey")
    add("=" * 68)
    add(f"  zip                : {r['zip']}")
    add(f"  top-level folders  : {', '.join(r['folders']) or '(none)'}")
    if r.get("nested_prefix"):
        add(f"  NOTE: package is nested under '{r['nested_prefix']}'")
    add(f"  processes          : {r['processes']}")
    add(f"  exchanges          : {r['exchanges']}")
    if r["unreadable_files"]:
        add(f"  UNREADABLE files   : {len(r['unreadable_files'])}")

    add("")
    add("  Parameters")
    add(f"    global           : {r['global_parameters']}"
        f"  ({r['global_input']} input, {r['global_dependent']} dependent)")
    add(f"    process-local    : {r['process_parameters']}"
        f"  ({r['process_input']} input, {r['process_dependent']} dependent)")
    if r["scopes_seen"]:
        add(f"    scopes seen      : "
            + ", ".join(f"{k}={v}" for k, v in sorted(r["scopes_seen"].items())))
    if r["shadowed_names"]:
        add(f"    SHADOWED (local overrides global): {len(r['shadowed_names'])}")
        add("      " + ", ".join(redact(r["shadowed_names"])))

    add("")
    add("  Formulas")
    add(f"    total            : {r['formulas_total']}"
        f"   (longest {r['max_formula_len']} chars)")
    add(f"    core-arithmetic only : {r['formulas_core_only']} / {r['formulas_total']}")
    add(f"    on exchanges     : {r['exchange_formulas']}")
    add(f"      with a non-zero amount    : {r['exchange_formula_amount_nonzero']}")
    add(f"      amount explicitly 0       : {r['exchange_formula_amount_zero']}")
    add(f"      no amount field at all    : {r['exchange_formula_amount_missing']}")
    if r["formula_locations"]:
        add("    locations        :")
        for path, count in sorted(r["formula_locations"].items(),
                                  key=lambda kv: -kv[1]):
            add(f"      {count:>6}  {path}")

    add("")
    add("  Dialect used")
    add(f"    operators        : "
        + (", ".join(f"{k}({v})" for k, v in r["operators"].most_common()) or "(none)"))
    add(f"    word operators   : "
        + (", ".join(f"{k}({v})" for k, v in r["word_operators"].most_common())
           or "(none)"))
    add(f"    functions        : "
        + (", ".join(f"{k}({v})" for k, v in r["functions"].most_common()) or "(none)"))
    add(f"    arg separators   : "
        + (", ".join(f"'{k}'({v})" for k, v in r["separators"].most_common())
           or "(none seen)"))
    if r["unknown_chars"]:
        add(f"    UNRECOGNIZED chars: "
            + ", ".join(f"'{k}'({v})" for k, v in r["unknown_chars"].most_common()))
    if r["lex_errors"]:
        add(f"    non-string formulas: {r['lex_errors']}")

    outside = (set(r["operators"]) - CORE_OPERATORS) | set(r["word_operators"]) \
        | set(r["functions"])
    add("")
    add("  Beyond core arithmetic (would hard-stop under the proposed subset)")
    add(f"    {', '.join(sorted(outside)) if outside else '(nothing — pure arithmetic)'}")

    add("")
    add("  Risks")
    add(f"    undefined references : {len(r['undefined_refs'])} distinct")
    if r["undefined_refs"]:
        shown = redact([n for n, _ in r["undefined_refs"][:10]])
        counts = [c for _, c in r["undefined_refs"][:10]]
        add("      " + ", ".join(f"{n}({c})" for n, c in zip(shown, counts)))
    add(f"    parameter cycles     : {len(r['cycles'])}")
    for cyc in r["cycles"][:5]:
        add("      " + " -> ".join(redact(cyc)))
    add(f"    multi-output processes : {r['multi_output_processes']}"
        f"  (with a parameterized product output: {r['multi_output_with_formula']})")

    add("")
    add("  This report contains no inventory data, no flow or process names,")
    add("  and no formula text." + ("" if show_names else " Identifiers are redacted."))
    add("=" * 68)
    return "\n".join(L)


def clean_path(raw):
    """A pasted path, made usable.

    Terminal paste adds artifacts that are not the operator's mistake: drag-and-drop
    wraps the path in quotes and backslash-escapes spaces, "Copy as Pathname" can
    bring trailing whitespace, and a quoted `~` never reaches the shell's expansion.

    Repairs are tried in order and the FIRST candidate that exists wins, so a real
    filename that genuinely contains a quote or a backslash is never mangled by a
    repair it did not need.
    """
    candidates = []
    p = raw.strip()
    candidates.append(p)
    for q in ('"', "'"):
        if len(p) >= 2 and p.startswith(q) and p.endswith(q):
            p = p[1:-1].strip()
    candidates.append(p)
    candidates.append(os.path.expanduser(p))
    candidates.append(os.path.expanduser(p.replace("\\ ", " ")))
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return candidates[0]


def self_test():
    """Scan a synthetic package built in memory. Proves the tool runs.

    Exists so that "the scanner is broken" and "my path is wrong" are separable
    without a round trip -- the whole point of a script someone runs on a machine
    where they cannot ask for help.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("parameters/g.json", json.dumps({
            "@type": "Parameter", "name": "rate", "value": 0.5,
            "isInputParameter": True, "parameterScope": "GLOBAL_SCOPE"}))
        z.writestr("processes/p.json", json.dumps({
            "@type": "Process", "@id": "p", "name": "demo",
            "parameters": [{"name": "loss", "formula": "rate * 2",
                            "isInputParameter": False}],
            "exchanges": [
                {"flow": {"@id": "f1", "flowType": "PRODUCT_FLOW"},
                 "unit": {"name": "kg"}, "isInput": False,
                 "isQuantitativeReference": True, "amount": 1.0},
                {"flow": {"@id": "f2", "flowType": "PRODUCT_FLOW"},
                 "unit": {"name": "kg"}, "isInput": True, "amount": 1.05,
                 "amountFormula": "loss * if(rate > 0; 1; 2)"}]}))
    buf.seek(0)
    r = scan(buf)
    r["zip"] = "(built-in self test)"
    print(report(r, show_names=True))
    ok = (r["processes"] == 1 and r["global_parameters"] == 1
          and r["formulas_total"] == 2 and "if" in r["functions"]
          and ";" in r["separators"] and ">" in r["operators"])
    print("\nself-test: " + ("PASS — the tool works; any failure is the path."
                             if ok else "FAIL — report this output."))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("zip", nargs="?", help="path to an olca-schema JSON-LD zip")
    ap.add_argument("--show-names", action="store_true",
                    help="print parameter names instead of redacting them")
    ap.add_argument("--json", action="store_true",
                    help="emit the raw survey as JSON instead of the report")
    ap.add_argument("--self-test", action="store_true",
                    help="scan a built-in synthetic package to prove the tool runs")
    args = ap.parse_args()

    if args.self_test:
        raise SystemExit(self_test())
    if not args.zip:
        ap.error("a zip path is required (or pass --self-test)")

    path = clean_path(args.zip)
    if os.path.isdir(path):
        raise SystemExit(
            f"That is a directory, not a zip: {path}\n"
            f"  Point this at the .zip file itself. If the package is already "
            f"unpacked, re-zip it from INSIDE the folder holding 'processes':\n"
            f"      cd {path} && zip -r ../survey_me.zip processes flows parameters")

    try:
        r = scan(path)
    except (FileNotFoundError, NotADirectoryError):
        # NotADirectoryError (errno 20) is the same user mistake as errno 2: it
        # just means the dead path component happens to exist as a file. Both are
        # overwhelmingly "the script name and the zip path were pasted together",
        # which is invisible on a long line, so say so rather than only naming it.
        hint = ""
        if ".py" in args.zip:
            hint = ("\n  The path contains '.py', so the script name and the zip "
                    "path look pasted together. They need a space between them:\n"
                    "      python3 tools/scan_olca_parameters.py <space> /path/to/study.zip")
        raise SystemExit(f"Cannot open: {path}{hint}")
    except IsADirectoryError:
        raise SystemExit(f"That is a directory, not a zip: {path}")
    except zipfile.BadZipFile:
        raise SystemExit(f"Not a readable zip file: {path}")
    except PermissionError:
        raise SystemExit(f"No permission to read: {path}")

    if args.json:
        printable = {k: (dict(v) if isinstance(v, Counter) else v)
                     for k, v in r.items()}
        json.dump(printable, sys.stdout, indent=2, default=list)
        sys.stdout.write("\n")
    else:
        print(report(r, show_names=args.show_names))


if __name__ == "__main__":
    main()
