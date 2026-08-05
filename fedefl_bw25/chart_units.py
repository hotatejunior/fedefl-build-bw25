"""Functional-unit commensurability checks for general/06_visualize.py.

Pure module (no pandas, matplotlib, or brightway) so it unit-tests without a
built DB or a plotting backend — same pattern as general/run_manifest.py and
setup/allocation.py.

Why this exists: the scenario-comparison chart divides every scenario's score by
the baseline scenario's, so it only means anything when the scenarios share a
functional unit. Comparing "1 m3 of diesel" against "1 kg of cement" puts
petroleum at ~100% on every category and everything else near zero — a ~849x
density artifact, not an impact difference. The legend labelled the units
correctly and the chart was still misleading, which is the same failure mode
recorded in the devlog for 2026-07-13 (per-m3 petroleum scores first read
as wrong numbers).

Policy: the baseline scenario's functional unit defines the comparable group.
The baseline is the first scenario in the results CSV and everything is
normalized against it, so keeping it fixed and dropping the scenarios that don't
match is the one rule that never silently changes what the chart means.
"""

# Scenarios whose functional unit the results CSV never recorded. Older CSVs
# (written before 04 emitted the functional_unit column) have this for every
# scenario; a mixed CSV can have it for some.
UNIT_NOT_RECORDED = "unit not recorded"


def partition_by_functional_unit(scenarios, fu_map):
    """Split `scenarios` into those sharing the baseline's functional unit and
    those that don't.

    `scenarios` is an ordered sequence of scenario names (first = baseline).
    `fu_map` maps scenario -> functional-unit string, as general/06's `_fu_map`
    builds it; it is empty for CSVs predating the functional_unit column, and
    may be missing individual scenarios.

    Returns a dict:
      comparable    — scenarios to plot, baseline first (always includes baseline)
      excluded      — [(scenario, unit_or_None), ...] dropped as incommensurable
      baseline      — the baseline scenario name, or None if `scenarios` is empty
      baseline_unit — the baseline's functional unit, or None if not recorded
      verified      — True when there was functional-unit data to check against.
                      False means "could not check", not "checked and matched".
    """
    scenarios = list(scenarios)
    if not scenarios:
        return {"comparable": [], "excluded": [], "baseline": None,
                "baseline_unit": None, "verified": False}

    baseline = scenarios[0]

    # No functional-unit data at all: keep the pre-existing behaviour (plot
    # everything) rather than breaking older CSVs, but report it as unverified
    # so the caller can say so instead of implying the units were checked.
    if not fu_map:
        return {"comparable": scenarios, "excluded": [], "baseline": baseline,
                "baseline_unit": None, "verified": False}

    baseline_unit = fu_map.get(baseline)
    comparable, excluded = [], []
    for scenario in scenarios:
        if fu_map.get(scenario) == baseline_unit:
            comparable.append(scenario)
        else:
            excluded.append((scenario, fu_map.get(scenario)))

    return {"comparable": comparable, "excluded": excluded, "baseline": baseline,
            "baseline_unit": baseline_unit, "verified": True}


def describe_exclusions(partition):
    """Operator-facing lines explaining what was dropped and why.

    Returns [] when nothing was dropped and the units were verified — the quiet
    path for the ordinary same-unit case.
    """
    lines = []

    if not partition["verified"]:
        if partition["comparable"]:
            lines.append(
                "NOTE: results CSV has no functional_unit column, so scenario "
                "units could not be verified. Re-run general/04 to record them; "
                "comparing scenarios with different functional units is not "
                "meaningful."
            )
        return lines

    for scenario, unit in partition["excluded"]:
        shown = unit if unit else UNIT_NOT_RECORDED
        lines.append(
            f"EXCLUDED from scenario_comparison: '{scenario}' (per {shown}) "
            f"is not comparable to the baseline "
            f"'{partition['baseline']}' (per {_baseline_unit_label(partition)})."
        )

    if lines:
        lines.append(
            "Scenarios with different functional units cannot be compared as "
            "percentages — the ratio would be a unit artifact. Re-run with a "
            "common basis to include them."
        )
    return lines


def comparison_subtitle(partition):
    """Subtitle stating the basis every plotted scenario shares, or None when
    there is no verified common unit to state."""
    if not partition["verified"] or not partition["baseline_unit"]:
        return None
    return f"All scenarios per {partition['baseline_unit']}"


def _baseline_unit_label(partition):
    return partition["baseline_unit"] or UNIT_NOT_RECORDED
