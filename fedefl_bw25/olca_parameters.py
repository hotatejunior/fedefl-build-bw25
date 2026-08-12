"""
olca_parameters
---------------
Resolve an olca-schema parameter scope to concrete numbers.

Where parameters live
---------------------
Two places, with one shadowing rule:

    parameters/*.json          standalone Parameter documents, GLOBAL_SCOPE
    processes/*.json           a `parameters` array on each process, PROCESS_SCOPE

A process-local name shadows a global of the same name. openLCA allows this and
real models use it -- a global default overridden for one process -- so it is
normal, not a warning.

Two kinds of parameter
----------------------
    input      `isInputParameter: true`, carries a literal `value`. A leaf.
    dependent  carries a `formula` over other parameter names. Must be evaluated,
               in dependency order.

Dependents may reference other dependents, so resolution is a topological walk.
The surveyed corpus (napa-lci alpha, 2026-08-12) has 2 globals and 6,240
process-local parameters, of which 2,668 are dependent, with zero cycles and zero
undefined references -- but all three failure modes are hard stops here rather
than assumptions, because the cost of being wrong is a silently different number.

The free reference values
-------------------------
openLCA writes the last evaluated result into a dependent's `value` field. That
field is never used to produce a build -- the formula is authoritative, which is
the entire point of evaluating -- but it is retained as a CHECK: 2,668 cases where
an independent implementation already computed the answer. `resolve` returns them
so `tools/verify_olca_formulas.py` can adjudicate this module against openLCA.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .olca_formula import FormulaError, eval_ast, parse, referenced_names


class ParameterError(RuntimeError):
    """A parameter scope could not be resolved: a cycle, or an undefined name."""


# =============================================================================
# READING
# =============================================================================
def collect_parameters(doc) -> list:
    """A document's parameters as [{name, formula, value, is_input, scope}].

    Handles both shapes with one function: a standalone global Parameter document
    and the `parameters` array on a process use identical field names, so the only
    difference is whether the fields are at the top level or one deep.
    """
    if doc.get("@type") == "Parameter" or (
            "parameterScope" in doc and "name" in doc and "exchanges" not in doc):
        raw = [doc]
    else:
        raw = doc.get("parameters") or []

    params = []
    for p in raw:
        if not isinstance(p, dict):
            continue
        name = (p.get("name") or "").strip()
        if not name:
            continue
        params.append({
            "name": name,
            "formula": p.get("formula") or None,
            "value": p.get("value"),
            "is_input": p.get("isInputParameter"),
            "scope": p.get("parameterScope") or "",
        })
    return params


def is_dependent(p) -> bool:
    """A parameter that must be evaluated rather than read.

    `isInputParameter` is the declared intent, but it is optional in the schema
    and hand-written exports omit it. The presence of a formula is the reliable
    signal, so a parameter with a formula is dependent whatever the flag says --
    with one exception: an INPUT parameter that also carries a formula is openLCA
    recording provenance for a value the user has since pinned, and the pin wins.
    """
    if p["formula"] is None:
        return False
    return not p["is_input"]


# =============================================================================
# RESOLUTION
# =============================================================================
@dataclass
class ScopeResult:
    """Resolved names, plus every case where openLCA had already computed one."""

    values: dict = field(default_factory=dict)
    # (name, computed, declared) for dependents that carried a stored `value`.
    checks: list = field(default_factory=list)

    def __contains__(self, name):
        return name in self.values

    def __getitem__(self, name):
        return self.values[name]


def resolve(params, outer=None, *, where="") -> ScopeResult:
    """Resolve `params` to numbers, layered over an already-resolved `outer` scope.

    Iterative fixed point rather than an explicit topological sort: each pass
    resolves every dependent whose references are all known, and a pass that makes
    no progress means the remainder is stuck. The graph is tiny (roughly 60 names
    per process) so the O(n^2) worst case is irrelevant, and the payoff is that
    the stuck set is exactly what the error needs to report.
    """
    values = dict(outer.values) if isinstance(outer, ScopeResult) else dict(outer or {})
    result = ScopeResult(values=values)

    pending = {}          # name -> (ast, referenced names, declared value)
    for p in params:
        name = p["name"]
        if is_dependent(p):
            try:
                ast = parse(p["formula"])
                refs = referenced_names(p["formula"])
            except FormulaError as exc:
                raise ParameterError(
                    f"parameter {name!r}{_at(where)} has a formula this build "
                    f"cannot evaluate:\n  {exc}") from None
            pending[name] = (ast, refs, p["value"])
        else:
            # An input parameter with no value is openLCA's way of saying "the
            # default is zero"; treating a missing value as 0.0 silently would
            # hide a truncated export, so it is refused.
            if p["value"] is None:
                raise ParameterError(
                    f"input parameter {name!r}{_at(where)} has no `value` and no "
                    f"`formula` -- nothing to resolve it to.")
            values[name] = float(p["value"])

    # Local names shadow the outer scope, so a dependent that is about to be
    # computed must not be readable from `outer` in the meantime.
    for name in pending:
        values.pop(name, None)

    while pending:
        ready = [n for n, (_ast, refs, _v) in pending.items()
                 if all(r in values or _ci_hit(r, values) for r in refs)]
        if not ready:
            raise ParameterError(_stuck_message(pending, values, where))

        for name in sorted(ready):                     # deterministic order
            ast, _refs, declared = pending.pop(name)
            try:
                computed = float(eval_ast(ast, values))
            except FormulaError as exc:
                raise ParameterError(
                    f"parameter {name!r}{_at(where)} failed to evaluate:\n"
                    f"  {exc}") from None
            values[name] = computed
            if declared is not None:
                result.checks.append((name, computed, float(declared)))

    return result


def _ci_hit(name, values) -> bool:
    """Case-insensitive availability, matching the evaluator's own fallback."""
    if name in values:
        return True
    return sum(1 for k in values if k.lower() == name.lower()) == 1


def _at(where) -> str:
    return f" in {where}" if where else ""


def _stuck_message(pending, values, where) -> str:
    """Distinguish a cycle from an undefined reference, and name the culprits.

    These are different authoring mistakes with different fixes, and "could not
    resolve 26 parameters" tells the user neither.
    """
    unresolved = set(pending)
    undefined = {}
    for name, (_ast, refs, _v) in pending.items():
        missing = {r for r in refs
                   if r not in values and not _ci_hit(r, values)
                   and r not in unresolved}
        if missing:
            undefined[name] = sorted(missing)

    lines = []
    if undefined:
        lines.append(
            f"{len(undefined)} parameter(s){_at(where)} reference names that are "
            f"not defined anywhere in scope:")
        for name in sorted(undefined)[:10]:
            lines.append(f"    {name} -> needs {', '.join(undefined[name])}")
        lines.append(
            "  A global parameter is only in scope if parameters/ was loaded; "
            "check the export includes it.")

    cyclic = sorted(unresolved - set(undefined))
    if cyclic:
        lines.append(
            f"{len(cyclic)} parameter(s){_at(where)} form a dependency cycle -- "
            f"each waits on another in this set, so none can be computed:")
        lines.append("    " + ", ".join(cyclic[:10])
                     + (" ..." if len(cyclic) > 10 else ""))

    return "\n".join(lines)


# =============================================================================
# WHOLE-FILE SCOPES
# =============================================================================
def global_scope(param_docs, *, log=None) -> ScopeResult:
    """Resolve every standalone Parameter document into one scope."""
    params = []
    for doc in param_docs:
        params.extend(collect_parameters(doc))
    scope = resolve(params, where="the global parameter scope")
    if log:
        log(f"  {len(scope.values)} global parameter(s) resolved.")
    return scope


def process_scope(proc, globals_) -> ScopeResult:
    """Resolve one process's local parameters over the resolved global scope."""
    name = proc.get("name") or proc.get("@id") or "<unnamed process>"
    return resolve(collect_parameters(proc), globals_, where=f"process {name!r}")
