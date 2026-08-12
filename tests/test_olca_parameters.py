"""Tests for fedefl_bw25.olca_parameters — resolving a scope to numbers.

The failure modes matter as much as the happy path: a cycle, an undefined name and
a valueless input parameter are three different authoring mistakes, and a build
that reports "could not resolve 26 parameters" tells the user none of them.
"""
import pytest

from fedefl_bw25.olca_parameters import (ParameterError, collect_parameters,
                                         is_dependent, process_scope, resolve)


def P(name, value=None, formula=None, is_input=None, scope="PROCESS_SCOPE"):
    return {"name": name, "value": value, "formula": formula,
            "is_input": is_input, "scope": scope}


def test_inputs_resolve_to_their_value():
    assert resolve([P("a", 2.0, is_input=True)])["a"] == 2.0


def test_dependents_resolve_in_dependency_order_not_declaration_order():
    """openLCA does not order the array, so a naive single pass gets this wrong."""
    scope = resolve([
        P("d", formula="c * 10"),
        P("c", formula="a + b"),
        P("a", 2.0, is_input=True),
        P("b", 3.0, is_input=True),
    ])
    assert scope["c"] == 5.0
    assert scope["d"] == 50.0


def test_a_formula_beats_a_stored_value_and_the_difference_is_recorded():
    """openLCA writes its last evaluation into `value`. Evaluating is the point, so
    the formula wins — but the stored number is kept as a check, which is what
    tools/verify_olca_formulas.py adjudicates against."""
    scope = resolve([P("a", 2.0, is_input=True), P("z", value=99.0, formula="a * 3")])
    assert scope["z"] == 6.0
    assert scope.checks == [("z", 6.0, 99.0)]


def test_an_input_parameter_carrying_a_formula_keeps_its_pinned_value():
    """isInputParameter means the user pinned it; the formula is then provenance."""
    scope = resolve([P("a", 7.0, formula="1 + 1", is_input=True)])
    assert scope["a"] == 7.0


def test_process_local_shadows_global():
    globals_ = resolve([P("rate", 1.0, is_input=True, scope="GLOBAL_SCOPE")])
    local = resolve([P("rate", 5.0, is_input=True), P("out", formula="rate * 2")],
                    globals_)
    assert local["out"] == 10.0
    assert globals_["rate"] == 1.0          # the outer scope is not mutated


def test_a_dependent_shadowing_a_global_does_not_read_the_global_it_replaces():
    """The local name must not resolve to the outer value while it is pending —
    that would silently compute from the wrong number instead of erroring."""
    globals_ = resolve([P("x", 100.0, is_input=True, scope="GLOBAL_SCOPE")])
    with pytest.raises(ParameterError, match="cycle"):
        resolve([P("x", formula="x + 1")], globals_)


def test_globals_are_visible_to_local_dependents():
    globals_ = resolve([P("g", 4.0, is_input=True, scope="GLOBAL_SCOPE")])
    assert resolve([P("out", formula="g * 3")], globals_)["out"] == 12.0


def test_a_cycle_is_reported_as_a_cycle():
    with pytest.raises(ParameterError, match="dependency cycle"):
        resolve([P("a", formula="b + 1"), P("b", formula="a + 1")])


def test_an_undefined_reference_names_what_is_missing():
    with pytest.raises(ParameterError, match="ghost"):
        resolve([P("a", formula="ghost * 2")])


def test_an_input_without_a_value_is_refused_rather_than_defaulted_to_zero():
    """Defaulting to 0 would hide a truncated export behind a plausible build."""
    with pytest.raises(ParameterError, match="no `value`"):
        resolve([P("a", is_input=True)])


def test_an_unevaluatable_formula_names_the_parameter():
    with pytest.raises(ParameterError, match="'a'"):
        resolve([P("a", formula="sqrt(4)")])


def test_collect_parameters_reads_a_standalone_global_document():
    doc = {"@type": "Parameter", "name": "g", "parameterScope": "GLOBAL_SCOPE",
           "isInputParameter": True, "value": 1.5}
    assert collect_parameters(doc) == [
        {"name": "g", "formula": None, "value": 1.5, "is_input": True,
         "scope": "GLOBAL_SCOPE"}]


def test_collect_parameters_reads_a_process_parameters_array():
    proc = {"@type": "Process", "name": "p", "exchanges": [],
            "parameters": [{"name": "a", "value": 1.0, "isInputParameter": True}]}
    assert [p["name"] for p in collect_parameters(proc)] == ["a"]


def test_collect_parameters_ignores_unnamed_entries():
    proc = {"parameters": [{"value": 1.0}, {"name": "  ", "value": 2.0},
                           {"name": "ok", "value": 3.0}]}
    assert [p["name"] for p in collect_parameters(proc)] == ["ok"]


@pytest.mark.parametrize("param,expected", [
    (P("a", formula="b + 1"), True),
    (P("a", formula="b + 1", is_input=False), True),
    (P("a", 1.0, formula="b + 1", is_input=True), False),   # pinned
    (P("a", 1.0, is_input=True), False),
    (P("a", 1.0), False),
])
def test_is_dependent(param, expected):
    assert is_dependent(param) is expected


def test_process_scope_names_the_process_in_its_error():
    proc = {"name": "widget line", "parameters": [{"name": "a", "formula": "ghost"}]}
    with pytest.raises(ParameterError, match="widget line"):
        process_scope(proc, resolve([]))
