"""Tests for fedefl_bw25.search — finding a process by name rather than UUID.

The matching rule earns its complexity on one case: "hdpe flake" appears nowhere
in "Recycled postconsumer high-density polyethylene, HDPE, flake; at plant" as a
substring, so a naive search returns nothing for the query a practitioner would
actually type. Ranking earns its keep on another: "diesel" hits 224 processes, and
the diesel *products* have to come before the 200-odd things that are merely
diesel powered.

Matching and ranking take plain records, so none of this needs a built database.
"""
import pytest

from fedefl_bw25.search import (Process, format_matches, head_segment, match_rank,
                                merge_by_code, rank_entries, run_command, suggest,
                                tokenize)


def _p(name, code="c", location="US", unit="kg", databases=("uslci-subset",)):
    return Process(code=code, name=name, location=location, unit=unit,
                   databases=databases)


# Names taken verbatim from the built database.
HDPE_FLAKE = "Recycled postconsumer high-density polyethylene, HDPE, flake; at plant"
HDPE_PELLET = "Recycled postconsumer high-density polyethylene, HDPE, pellet; at plant"
DIESEL_BOILER = "Diesel; combusted in industrial boiler"
TRUCK = "Transport, combination truck; short-haul; diesel powered"
CORN_FIELD = "Corn; at field"


# -----------------------------------------------------------------------------
# Matching
# -----------------------------------------------------------------------------
def test_tokens_may_be_separated_by_anything_in_the_real_name():
    # The whole reason substring search is not enough: the words are eight
    # characters apart with a comma between them.
    assert "hdpe flake" not in HDPE_FLAKE.lower()
    assert match_rank(HDPE_FLAKE, tokenize("hdpe flake")) is not None


def test_token_order_does_not_matter():
    assert match_rank(HDPE_FLAKE, tokenize("flake hdpe")) is not None


def test_every_token_must_appear():
    assert match_rank(HDPE_PELLET, tokenize("hdpe flake")) is None


def test_matching_is_case_insensitive():
    assert match_rank(HDPE_FLAKE, tokenize("HDPE FLAKE")) is not None


def test_a_non_matching_name_scores_none_rather_than_last():
    assert match_rank(CORN_FIELD, tokenize("diesel")) is None


# -----------------------------------------------------------------------------
# Ranking
# -----------------------------------------------------------------------------
def test_a_product_outranks_something_merely_powered_by_it():
    # USLCI puts the product before the first ';', so a hit there is a hit on what
    # the process makes. Without this, 200 transport processes bury the fuel.
    matches, _total = rank_entries([_p(TRUCK), _p(DIESEL_BOILER)], "diesel")
    assert matches[0].name == DIESEL_BOILER


def test_an_exact_name_comes_first():
    matches, _total = rank_entries([_p(CORN_FIELD), _p("Corn")], "corn")
    assert matches[0].name == "Corn"


def test_shorter_names_rank_above_longer_ones_at_equal_specificity():
    matches, _total = rank_entries(
        [_p("Corn stover; carted"), _p("Corn; at field")], "corn")
    assert matches[0].name == "Corn; at field"


def test_ranking_is_deterministic_for_equally_good_matches():
    entries = [_p("Corn B; at field"), _p("Corn A; at field")]
    first, _ = rank_entries(entries, "corn")
    second, _ = rank_entries(list(reversed(entries)), "corn")
    assert [m.name for m in first] == [m.name for m in second]


def test_head_segment_is_the_product_name():
    assert head_segment(DIESEL_BOILER) == "diesel"
    assert head_segment("No semicolon here") == "no semicolon here"


# -----------------------------------------------------------------------------
# Limits and totals
# -----------------------------------------------------------------------------
def test_total_counts_all_matches_not_just_the_shown_ones():
    entries = [_p(f"Diesel {i}; at plant") for i in range(30)]
    matches, total = rank_entries(entries, "diesel", limit=5)
    assert len(matches) == 5 and total == 30


def test_limit_zero_returns_everything():
    entries = [_p(f"Diesel {i}") for i in range(30)]
    matches, total = rank_entries(entries, "diesel", limit=0)
    assert len(matches) == 30 == total


def test_an_empty_query_lists_everything():
    entries = [_p("Corn"), _p("Diesel")]
    matches, total = rank_entries(entries, "", limit=0)
    assert total == 2


# -----------------------------------------------------------------------------
# One process, several builds
# -----------------------------------------------------------------------------
def test_the_same_process_in_two_builds_is_one_result():
    # Listing it twice would double every count and every row.
    merged = merge_by_code([_p(CORN_FIELD, code="u1", databases=("uslci-subset",)),
                            _p(CORN_FIELD, code="u1", databases=("uslci-full",))])
    assert len(merged) == 1
    assert merged[0].databases == ("uslci-subset", "uslci-full")


def test_a_uuid_whose_name_differs_between_builds_stays_two_results():
    # Different names mean the builds parsed different dataset versions — real
    # information, and collapsing them would hide it.
    merged = merge_by_code([_p("Corn; at field", code="u1", databases=("uslci-subset",)),
                            _p("Corn; at field, revised", code="u1",
                               databases=("uslci-full",))])
    assert len(merged) == 2


def test_the_suggested_command_omits_the_database_when_it_is_the_default():
    in_default = _p(CORN_FIELD, code="u1", databases=("uslci-subset", "uslci-full"))
    assert run_command(in_default) == "python general/04_run_lca.py --uuid u1"


def test_the_suggested_command_names_a_non_default_build():
    full_only = _p(CORN_FIELD, code="u1", databases=("uslci-full",))
    assert "--database uslci-full" in run_command(full_only)


# -----------------------------------------------------------------------------
# Nothing found
# -----------------------------------------------------------------------------
def test_a_typo_gets_a_suggestion():
    near = suggest("portland cemant", [_p("Portland cement; at plant"), _p(CORN_FIELD)])
    assert near == ["Portland cement; at plant"]


def test_suggestions_compare_against_the_product_name_only():
    # Against the full 206-character name a typo's ratio falls under any useful
    # cutoff, so the reader gets nothing at exactly the moment they need help.
    long_name = "Corn; whole plant; at field; " + "qualifier, " * 20
    assert suggest("corn whol plant", [_p(long_name)]) == []
    assert suggest("cornn", [_p(long_name)]) == [long_name]


def test_no_matches_reports_the_query_rather_than_an_empty_table():
    out = format_matches([], 0, "zzzz", all_processes=[_p(CORN_FIELD)])
    assert "No process matches 'zzzz'" in out


def test_output_marks_causal_coproduct_activities():
    co = _p("Sorting [causal co-product: HDPE]", code="abc__co__def")
    assert co.is_coproduct
    assert "[co-product]" in format_matches([co], 1, "hdpe")


def test_output_hands_over_the_next_command():
    out = format_matches([_p(CORN_FIELD, code="u1")], 1, "corn")
    assert "--uuid u1" in out
