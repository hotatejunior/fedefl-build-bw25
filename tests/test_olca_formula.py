"""Tests for fedefl_bw25.olca_formula — the openLCA expression subset.

The precedence cases are the point of this file. openLCA's `^` is exponentiation
where Python's is XOR, and `=` is equality where Python's is assignment, so a
formula handed to Python's `ast` does not fail loudly — it returns a different
number. Every one of those readings is pinned here.
"""
import math

import pytest

from fedefl_bw25.olca_formula import (FormulaError, UnsupportedConstruct,
                                      describe_support, dialect_histogram,
                                      evaluate, parse, referenced_names)

SCOPE = {"a": 2.0, "b": 3.0, "zero": 0.0, "mode": 1.0, "Rate": 4.0}


@pytest.mark.parametrize("src,want", [
    ("a * b", 6.0),
    ("a + b * 2", 8.0),                 # precedence, not left-to-right
    ("(a + b) * 2", 10.0),
    ("a - b - 1", -2.0),                # '-' is left-associative
    ("b / a", 1.5),
    ("1.5e2", 150.0),
    (".5", 0.5),
    ("-a", -2.0),
    ("+a", 2.0),
    ("- -a", 2.0),
])
def test_arithmetic(src, want):
    assert evaluate(src, SCOPE) == pytest.approx(want)


@pytest.mark.parametrize("src,want", [
    ("2 ^ 3", 8.0),                     # NOT 1, which is what Python's ^ gives
    ("2 ^ 3 ^ 2", 512.0),               # right-associative: 2^(3^2), not (2^3)^2
    ("-2 ^ 2", -4.0),                   # negation applies to the whole power
    ("2 ^ -3", 0.125),                  # a negative exponent needs no parentheses
])
def test_exponentiation_is_not_xor(src, want):
    assert evaluate(src, SCOPE) == pytest.approx(want)


@pytest.mark.parametrize("src,want", [
    ("if(mode = 1; a; b)", 2.0),
    ("if(mode = 2; a; b)", 3.0),
    ("if(a != b; 10; 20)", 10.0),
    ("if(a = b; 10; 20)", 20.0),
    ("if(mode == 1; a; b)", 2.0),       # '==' accepted as a spelling of '='
    ("if(a <> b; 10; 20)", 10.0),       # so is '<>' for '!='
    ("if(mode = 1; a, b)", 2.0),        # ',' accepted where openLCA writes ';'
])
def test_conditionals(src, want):
    assert evaluate(src, SCOPE) == pytest.approx(want)


def test_if_evaluates_only_the_taken_branch():
    """The divide-guard idiom — 90 formulas in the surveyed corpus — depends on it.

    An eager evaluator would raise on the guarded division even though the guard
    is exactly what makes it safe.
    """
    assert evaluate("if(zero != 0; a / zero; 0)", SCOPE) == 0.0


def test_constants():
    assert evaluate("pi", SCOPE) == pytest.approx(math.pi)
    assert evaluate("e", SCOPE) == pytest.approx(math.e)


def test_names_resolve_case_insensitively_when_exact_match_misses():
    assert evaluate("Rate * 2", SCOPE) == 8.0
    assert evaluate("rate * 2", SCOPE) == 8.0          # openLCA is case-insensitive


def test_ambiguous_case_is_a_hard_stop_not_a_coin_flip():
    with pytest.raises(FormulaError, match="differing only in case"):
        evaluate("rate", {"Rate": 1.0, "RATE": 2.0})


@pytest.mark.parametrize("src,want", [
    ("if(a < b; 10; 20)", 10.0),
    ("if(a > b; 10; 20)", 20.0),
    ("if(a <= 2; 10; 20)", 10.0),
    ("if(b >= 3; 10; 20)", 10.0),
])
def test_ordering_comparisons(src, want):
    """USLCI uses `>` exactly once, in C_storage_final of the mixed-MSW landfill
    process. One real formula in a shipped database is enough to support all four."""
    assert evaluate(src, SCOPE) == pytest.approx(want)


def test_the_uslci_formula_that_needed_ordering_comparisons():
    """The actual formula, with its real parameter names. It guards a carbon
    balance: when storage would exceed the initial carbon content, cap it."""
    scope = {"C_storage_mixed_msw1": 0.05, "yield_CH4_mixed_msw1": 0.06,
             "density_CH4_standard1": 0.716, "CH4_to_C1": 0.749,
             "C_initial_content_mixed_msw1": 0.3}
    formula = ("if((C_storage_mixed_msw1 + 2 * yield_CH4_mixed_msw1 * "
               "(density_CH4_standard1 / 1000) * CH4_to_C1) / "
               "C_initial_content_mixed_msw1 > 1;C_initial_content_mixed_msw1 - "
               "(2 * yield_CH4_mixed_msw1 * (density_CH4_standard1 / 1000) * "
               "CH4_to_C1);C_storage_mixed_msw1)")
    # The guard is false here, so the stored branch is taken.
    assert evaluate(formula, scope) == pytest.approx(0.05)


def test_chained_comparison_is_a_parse_error_not_a_surprising_reading():
    """Non-associative on purpose: `a < b < c` means different things in different
    languages, so it should mean nothing here."""
    with pytest.raises(FormulaError):
        evaluate("a < b < 5", SCOPE)


@pytest.mark.parametrize("src,match", [
    ("a div b", "word operator"),       # the reason Python's ast cannot be used
    ("a mod b", "word operator"),
    ("sqrt(a)", "sqrt"),
    ("min(a; b)", "min"),
    ("a && b", "'&&'"),
    ("a || b", r"'\|\|'"),
])
def test_constructs_outside_the_subset_are_refused_by_name(src, match):
    with pytest.raises(UnsupportedConstruct, match=match):
        evaluate(src, SCOPE)


@pytest.mark.parametrize("src", [
    "a +", "a b", "(a", "if(a = 1; 2)", "if(a = 1; 2; 3; 4)", "a @ b", "",
])
def test_malformed_formulas_raise(src):
    with pytest.raises(FormulaError):
        evaluate(src, SCOPE)


def test_undefined_name_is_reported_by_name():
    with pytest.raises(FormulaError, match="undefined parameter 'ghost'"):
        evaluate("ghost * 2", SCOPE)


def test_division_by_zero_is_an_error_not_an_infinity():
    """An inf would propagate into the matrix and surface as a nonsense result
    somewhere else entirely."""
    with pytest.raises(FormulaError, match="division by zero"):
        evaluate("a / zero", SCOPE)


def test_error_points_at_the_offending_position():
    with pytest.raises(FormulaError) as exc:
        evaluate("a * ghost", SCOPE)
    assert "a * ghost" in str(exc.value)
    assert "^" in str(exc.value)


def test_referenced_names_skips_functions_and_constants():
    assert referenced_names("if(mode = 1; a * Rate; pi * b)") == {
        "mode", "a", "Rate", "b"}


def test_describe_support_partitions():
    ok, refused = describe_support(["a * b", "sqrt(a)", "a + 1"])
    assert ok == ["a * b", "a + 1"]
    assert list(refused) == ["sqrt(a)"]


def test_dialect_histogram_counts_tokens():
    hist = dialect_histogram(["a * b", "if(a = 1; b; 0)"])
    assert hist["*"] == 1
    assert hist["="] == 1
    assert hist["if()"] == 1


def test_parsing_is_separable_from_evaluation():
    """A sweep parses once and evaluates many times; that is the whole speed case."""
    ast = parse("a * b")
    from fedefl_bw25.olca_formula import eval_ast
    assert eval_ast(ast, {"a": 2.0, "b": 3.0}) == 6.0
    assert eval_ast(ast, {"a": 5.0, "b": 7.0}) == 35.0
