"""Pure-unit tests for general/chart_units.py — the functional-unit
commensurability guard on the scenario-comparison chart.

No pandas or matplotlib needed: partition_by_functional_unit() takes the ordered
scenario list and the scenario -> unit mapping as plain data, exactly what
general/06's `_fu_map` hands it.

The case that motivated the guard: petroleum is reported per 1 m3 while corn and
cement are per 1 kg, so an unguarded comparison put petroleum at 100% on all ten
TRACI categories purely from the ~849 kg/m3 density difference.
"""
import chart_units as cu

PETROLEUM = "Petroleum refining; at refinery"
CORN = "Corn; whole plant; at field"
CEMENT = "Portland cement; at plant"

MIXED_FU = {PETROLEUM: "1 m3", CORN: "1 kg", CEMENT: "1 kg"}
SAME_FU = {CORN: "1 kg", CEMENT: "1 kg"}


def test_same_unit_scenarios_all_compare():
    p = cu.partition_by_functional_unit([CORN, CEMENT], SAME_FU)
    assert p["comparable"] == [CORN, CEMENT]
    assert p["excluded"] == []
    assert p["baseline"] == CORN
    assert p["baseline_unit"] == "1 kg"
    assert p["verified"] is True


def test_same_unit_comparison_is_quiet():
    p = cu.partition_by_functional_unit([CORN, CEMENT], SAME_FU)
    assert cu.describe_exclusions(p) == []


def test_mismatched_units_are_excluded_from_the_baseline_group():
    # Baseline is petroleum (per 1 m3); the two per-1-kg scenarios are dropped.
    p = cu.partition_by_functional_unit([PETROLEUM, CORN, CEMENT], MIXED_FU)
    assert p["comparable"] == [PETROLEUM]
    assert [s for s, _ in p["excluded"]] == [CORN, CEMENT]
    assert p["baseline_unit"] == "1 m3"
    assert p["verified"] is True


def test_baseline_unit_defines_the_group_not_the_majority():
    # Corn first => the per-kg group wins and petroleum is the one dropped,
    # even though that is the reverse of the previous test's outcome.
    p = cu.partition_by_functional_unit([CORN, CEMENT, PETROLEUM], MIXED_FU)
    assert p["comparable"] == [CORN, CEMENT]
    assert p["excluded"] == [(PETROLEUM, "1 m3")]
    assert p["baseline"] == CORN


def test_exclusion_message_names_scenario_and_both_units():
    p = cu.partition_by_functional_unit([CORN, CEMENT, PETROLEUM], MIXED_FU)
    lines = cu.describe_exclusions(p)
    assert any(PETROLEUM in ln and "1 m3" in ln and "1 kg" in ln for ln in lines)
    assert any("unit artifact" in ln for ln in lines)


def test_missing_functional_unit_column_plots_all_but_reports_unverified():
    # Older CSVs predate the functional_unit column — keep plotting them.
    p = cu.partition_by_functional_unit([PETROLEUM, CORN], {})
    assert p["comparable"] == [PETROLEUM, CORN]
    assert p["excluded"] == []
    assert p["verified"] is False
    assert any("could not be verified" in ln for ln in cu.describe_exclusions(p))


def test_scenario_with_unrecorded_unit_is_excluded_and_labelled():
    partial = {CORN: "1 kg", CEMENT: "1 kg"}  # petroleum absent from the map
    p = cu.partition_by_functional_unit([CORN, CEMENT, PETROLEUM], partial)
    assert p["comparable"] == [CORN, CEMENT]
    assert p["excluded"] == [(PETROLEUM, None)]
    assert any(cu.UNIT_NOT_RECORDED in ln for ln in cu.describe_exclusions(p))


def test_subtitle_states_the_shared_basis():
    p = cu.partition_by_functional_unit([CORN, CEMENT], SAME_FU)
    assert cu.comparison_subtitle(p) == "All scenarios per 1 kg"


def test_no_subtitle_when_units_are_unverified():
    p = cu.partition_by_functional_unit([CORN, CEMENT], {})
    assert cu.comparison_subtitle(p) is None


def test_empty_scenario_list_is_handled():
    p = cu.partition_by_functional_unit([], MIXED_FU)
    assert p["comparable"] == []
    assert p["baseline"] is None
    assert p["verified"] is False
