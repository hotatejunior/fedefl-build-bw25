#!/usr/bin/env python3
"""
06_visualize.py
---------------
Generates general-use LCA charts from CSVs produced by general/04_run_lca.py.

No brightway dependency — pandas + matplotlib + seaborn only.

Charts are written under charts/general/:
  impact_profile.png            — absolute LCIA scores, log scale (first/only scenario)
  scenario_comparison.png       — % of baseline, per category, for the scenarios
                                  sharing the baseline's functional unit (others
                                  are excluded with a reason — see chart_units.py)
  normalized_profile.png        — scores / US per-capita reference (dimensionless)
  contribution_analysis_*.png   — % process contribution per category (one per scenario)
  foreground_background_*.png   — foreground vs supply-chain split (one per scenario)

For the validation-only charts (brightway vs openLCA parity), see
validation/06_visualize_validation.py.

Future (when mc_results.csv is available):
  monte_carlo_distributions.png — score distributions per category

Usage
-----
IDE / notebook: edit CONFIG block and run directly.
Terminal (from repo root):
  python general/06_visualize.py [--results CSV] [--contributions CSV]
                                  [--output-dir DIR] [--format png|svg]
                                  [--dpi N] [--top-n N]
"""

import argparse
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import seaborn as sns

from fedefl_bw25 import chart_units

from fedefl_bw25.config import REPO_ROOT

warnings.filterwarnings("ignore", category=UserWarning, module="matplotlib")

# =============================================================================
# CONFIG
# =============================================================================
RESULTS_CSV       = REPO_ROOT / "lca_results.csv"         # from general/04_run_lca.py
CONTRIBUTIONS_CSV = REPO_ROOT / "lca_contributions.csv"   # from general/04_run_lca.py
OUTPUT_DIR        = REPO_ROOT / "charts"                  # CLI paths resolve against CWD instead
OUTPUT_FORMAT     = "png"   # "png" or "svg"
DPI               = 300
TOP_N_PROCESSES   = 7       # contribution chart: show top N, lump rest as "Other"
# scenario_comparison guards. The legend and the "Set2" palette both stop being
# readable past a handful of series; and because every bar is a percentage OF the
# baseline, a near-zero baseline turns the chart into a picture of the
# denominator. Both were found rendering 43 scenarios at once (2026-08-05).
MAX_COMPARISON_SCENARIOS = 8
BASELINE_RATIO_LIMIT     = 1000   # refuse if any scenario exceeds baseline by this

# Short display labels for TRACI 2.2 categories.
# Keys must match the `method` column in lca_results.csv exactly.
CATEGORY_LABELS = {
    "Global warming":                         "GWP",
    "Acidification":                          "Acid.",
    "Eutrophication (Freshwater)":            "FW Eutro",
    "Eutrophication (Marine)":                "Mar. Eutro",
    "Freshwater ecotoxicity":                 "FW Ecotox",
    "Human health - cancer":                  "HH Cancer",
    "Human health - non-cancer":              "HH Non-cancer",
    "Human health - particulate matter":      "HH PM",
    "Ozone depletion":                        "ODP",
    "Smog formation":                         "Smog",
}

# Preferred category order for charts (top → bottom / left → right).
CATEGORY_ORDER = list(CATEGORY_LABELS.keys())

# Short display-name overrides for known scenarios/processes. Keys must match a
# `scenario`/`process_name` value exactly. Falls back to a truncated name for
# anything not listed here — this is a convenience table, not a required one.
PROCESS_LABELS = {
    "Petroleum refining; at refinery": "Petroleum",
    "Corn; whole plant; at field":     "Corn",
    "Portland cement; at plant":       "Cement",
    "Steel; billets; at plant":        "Billets",
}

# Scenarios to render the foreground-vs-background split for. Steel is excluded
# (near-pure foreground with avoided-burden credits — the split isn't illustrative).
FOREGROUND_BG_SCENARIOS = [
    "Petroleum refining; at refinery",
    "Corn; whole plant; at field",
    "Portland cement; at plant",
]

# US per-capita annual normalization references for TRACI 2.2.
# Source: Ryberg et al. (2014) / Bare (2011). Units must match TRACI output units.
# Set NORMALIZATION_REF = None to skip the normalized_profile chart.
NORMALIZATION_REF = {
    "Global warming":                     8040,      # kg CO2-eq / person / yr
    "Acidification":                        11.5,    # kg SO2-eq
    "Eutrophication (Freshwater)":           1.45,   # kg N-eq
    "Eutrophication (Marine)":               2.06,   # kg N-eq
    "Freshwater ecotoxicity":           3.93e6,      # CTUeco
    "Human health - cancer":             1.56e-5,    # CTUh
    "Human health - non-cancer":         2.27e-4,    # CTUh
    "Human health - particulate matter":    1.51,    # kg PM2.5-eq
    "Ozone depletion":                   7.47e-2,    # kg CFC-11-eq
    "Smog formation":                      233,      # kg O3-eq
}

# =============================================================================
# CLI
# =============================================================================
parser = argparse.ArgumentParser(description="Generate general-use LCA charts from 04's CSVs.")
parser.add_argument("--results",       default=None, help="Path to lca_results.csv")
parser.add_argument("--contributions", default=None, help="Path to lca_contributions.csv")
parser.add_argument("--output-dir",    default=None, dest="output_dir")
parser.add_argument("--format",        default=None, choices=["png", "svg"])
parser.add_argument("--dpi",           default=None, type=int)
parser.add_argument("--top-n",         default=None, type=int, dest="top_n")
args = parser.parse_args()

if args.results:       RESULTS_CSV       = args.results
if args.contributions: CONTRIBUTIONS_CSV = args.contributions
if args.output_dir:    OUTPUT_DIR        = args.output_dir
if args.format:        OUTPUT_FORMAT     = args.format
if args.dpi:            DPI               = args.dpi
if args.top_n:          TOP_N_PROCESSES   = args.top_n

out_dir = Path(OUTPUT_DIR)
out_dir.mkdir(parents=True, exist_ok=True)

GENERAL_SUBDIR = "general"

# =============================================================================
# HELPERS
# =============================================================================
sns.set_theme(style="whitegrid", palette="muted", font_scale=0.95)


def _short(cat):
    return CATEGORY_LABELS.get(cat, cat)


def _proc_short(name):
    """Short label for a scenario/process; falls back to a truncated name."""
    if name in PROCESS_LABELS:
        return PROCESS_LABELS[name]
    return name.split(";")[0][:16]


def _proc_legend(name, width=46):
    """Truncate verbose USLCI process names for a legend entry."""
    if name == "Other":
        return "Other"
    return name if len(name) <= width else name[:width - 1] + "…"


def _fu_map(df):
    """scenario → functional-unit string (e.g. '1 m3'). 04 emits a
    functional_unit column; CSVs from before it existed simply lack it."""
    if "functional_unit" not in df.columns:
        return {}
    sub = df.dropna(subset=["functional_unit"])
    return sub.groupby("scenario")["functional_unit"].first().to_dict()


def _scen_label(scenario, fu_map, short=False):
    """Scenario display label with its functional unit, when known."""
    base = _proc_short(scenario) if short else scenario
    fu = fu_map.get(scenario)
    return f"{base} (per {fu})" if fu else base


def _save(fig, name):
    dest = out_dir / GENERAL_SUBDIR
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{name}.{OUTPUT_FORMAT}"
    fig.savefig(path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {path}")


def _sort_cats(df, col):
    """Return df sorted by CATEGORY_ORDER on column `col`."""
    order = {c: i for i, c in enumerate(CATEGORY_ORDER)}
    return (df.assign(_ord=df[col].map(lambda x: order.get(x, 999)))
              .sort_values("_ord")
              .drop(columns="_ord"))


# =============================================================================
# CHART 1: IMPACT PROFILE
# Absolute LCIA scores on a log scale, one bar per TRACI category.
# When multiple scenarios exist, plots the first one and advises using
# scenario_comparison for the rest.
# =============================================================================
def plot_impact_profile(df: pd.DataFrame) -> None:
    scenarios = df["scenario"].unique()
    if len(scenarios) > 1:
        print(f"  NOTE: impact_profile shows first scenario ('{scenarios[0]}') only. "
              f"Use scenario_comparison for multi-scenario view.")
    sub = _sort_cats(df[df["scenario"] == scenarios[0]].copy(), "method")
    sub["label"] = sub["method"].map(_short)
    fu_map = _fu_map(df)

    # A log axis cannot show negatives, so magnitudes are plotted — but the sign
    # must survive. A negative score is a CREDIT (avoided-product recovery), and
    # drawing it as an ordinary bar states the opposite of the result: processes
    # like 'Combustion of newspaper' are negative in all ten categories. Negative
    # bars are drawn in a distinct colour, hatched, labelled with a leading minus,
    # and called out in the axis label.
    neg = sub["score"] < 0
    n_neg = int(neg.sum())
    palette = sns.color_palette("Blues_d", len(sub))
    colors = ["#b1503f" if bad else palette[::-1][i] for i, bad in enumerate(neg)]
    fig, ax = plt.subplots(figsize=(9, 5))
    bars = ax.barh(sub["label"], sub["score"].abs(), color=colors, edgecolor="none")
    for bar, bad in zip(bars, neg):
        if bad:
            bar.set_hatch("//")
            bar.set_edgecolor("white")
    ax.set_xscale("log")
    fu = fu_map.get(scenarios[0])
    _base = (f"Impact score per {fu} (log scale, native units)" if fu
             else "Impact score (log scale, native units)")
    if n_neg:
        _base += f" — {n_neg} negative score(s) shown as magnitude, hatched"
    ax.set_xlabel(_base)
    ax.set_title(f"LCIA impact profile — {_scen_label(scenarios[0], fu_map)}",
                 fontsize=11, pad=10)
    ax.invert_yaxis()

    for bar, (_, row) in zip(bars, sub.iterrows()):
        # A leading minus on the unit label is the unmissable part: someone
        # reading a single bar sees "-kg CO2 eq" and knows it is a credit even
        # if they skipped the axis note.
        lbl = f"−{row['unit']}" if row["score"] < 0 else row["unit"]
        ax.text(
            bar.get_width() * 1.08,
            bar.get_y() + bar.get_height() / 2,
            lbl,
            va="center", fontsize=7,
            color="#b1503f" if row["score"] < 0 else "#666",
        )

    ax.tick_params(axis="y", labelsize=9)
    fig.tight_layout()
    _save(fig, "impact_profile")


# =============================================================================
# CHART 2: SCENARIO COMPARISON
# Each scenario's score as % of the first (baseline) scenario per category.
# Skipped if only one scenario is present.
#
# Only scenarios sharing the baseline's functional unit are plotted: a ratio
# between "1 m3" and "1 kg" scenarios is a unit artifact, not a result (see
# general/chart_units.py). Mismatched scenarios are dropped with a named reason.
# =============================================================================
def plot_scenario_comparison(df: pd.DataFrame) -> None:
    scenarios = df["scenario"].unique().tolist()
    if len(scenarios) < 2:
        print("  Skipping scenario_comparison: only one scenario in results CSV.")
        return

    fu_map = _fu_map(df)
    partition = chart_units.partition_by_functional_unit(scenarios, fu_map)
    for line in chart_units.describe_exclusions(partition):
        print(f"  {line}")

    scenarios = partition["comparable"]
    if len(scenarios) < 2:
        print("  Skipping scenario_comparison: fewer than 2 scenarios share the "
              "baseline's functional unit, so no meaningful comparison remains.")
        return

    # Cap the series count. Past a handful the legend eats the axes — at 43
    # scenarios it consumed ~85% of the figure and rendered the title behind
    # itself — and the palette cycles, so entries start sharing a colour and the
    # legend cannot disambiguate even where it fits.
    if len(scenarios) > MAX_COMPARISON_SCENARIOS:
        kept = scenarios[:MAX_COMPARISON_SCENARIOS]
        if partition["baseline"] not in kept:
            kept = [partition["baseline"]] + kept[:MAX_COMPARISON_SCENARIOS - 1]
        print(f"  NOTE: scenario_comparison shows {len(kept)} of {len(scenarios)} "
              f"comparable scenarios ({len(scenarios) - len(kept)} omitted — the "
              f"legend and colour palette cannot separate more). Pass a results CSV "
              f"with fewer scenarios to choose which.")
        scenarios = kept

    df = _sort_cats(df[df["scenario"].isin(scenarios)].copy(), "method")
    df["label"] = df["method"].map(_short)
    baseline = partition["baseline"]
    baseline_vals = df[df["scenario"] == baseline].set_index("method")["score"]

    # The baseline is the denominator of every number on this chart, so a
    # near-zero one makes the percentages meaningless rather than merely large:
    # an alphabetically-chosen baseline of 0.016 kg CO2-eq once put the y-axis at
    # 1e7 — fifty million percent — with nothing saying so. chart_units guards
    # incommensurable UNITS; this guards an incommensurable MAGNITUDE.
    _others = df[df["scenario"] != baseline]
    _ratios = []
    for _m, _ref in baseline_vals.items():
        if not _ref:
            continue
        _peak = _others[_others["method"] == _m]["score"].abs().max()
        if _peak and np.isfinite(_peak):
            _ratios.append(abs(_peak / _ref))
    if _ratios and max(_ratios) > BASELINE_RATIO_LIMIT:
        print(f"  Skipping scenario_comparison: baseline "
              f"'{_scen_label(baseline, fu_map)}' is {max(_ratios):.3g}x smaller than "
              f"the largest scenario in at least one category, so '% of baseline' "
              f"would be dominated by the choice of denominator rather than by any "
              f"difference in impact. Re-run with a baseline of comparable magnitude "
              f"(the first scenario in the results CSV is the baseline).")
        return

    def _pct(row):
        ref = baseline_vals.get(row["method"])
        return (row["score"] / ref * 100) if ref and ref != 0 else np.nan

    df["pct"] = df.apply(_pct, axis=1)

    cats = df["label"].unique().tolist()
    x = np.arange(len(cats))
    n = len(scenarios)
    width = 0.7 / n
    palette = sns.color_palette("Set2", n)

    fig, ax = plt.subplots(figsize=(12, 5))
    for i, scenario in enumerate(scenarios):
        sub = df[df["scenario"] == scenario]
        offsets = x + (i - n / 2 + 0.5) * width
        ax.bar(offsets, sub["pct"].values, width * 0.92,
               label=_scen_label(scenario, fu_map), color=palette[i],
               alpha=0.88, edgecolor="none")

    ax.axhline(100, color="#444", linewidth=0.9, linestyle="--")
    ax.set_xticks(x)
    ax.set_xticklabels(cats, rotation=30, ha="right", fontsize=9)
    ax.set_ylabel(f"% of baseline ('{_scen_label(baseline, fu_map)}')")
    subtitle = chart_units.comparison_subtitle(partition)
    ax.set_title("Scenario comparison — TRACI 2.2", fontsize=11,
                 pad=18 if subtitle else 10)
    if subtitle:
        ax.text(0.5, 1.02, subtitle, transform=ax.transAxes, ha="center",
                va="bottom", fontsize=9, color="#555")
    ax.legend(fontsize=9, framealpha=0.75)
    fig.tight_layout()
    _save(fig, "scenario_comparison")


# =============================================================================
# CHART 3: NORMALIZED PROFILE
# Each score divided by the US per-capita annual reference for that category,
# yielding dimensionless person-equivalents comparable across categories.
# Skipped if NORMALIZATION_REF is None.
# =============================================================================
def plot_normalized_profile(df: pd.DataFrame) -> None:
    if NORMALIZATION_REF is None:
        print("  Skipping normalized_profile: NORMALIZATION_REF is None.")
        return

    cats = [c for c in CATEGORY_ORDER if c in NORMALIZATION_REF]
    labels = [_short(c) for c in cats]
    fu_map = _fu_map(df)
    scenarios = df["scenario"].unique()
    x = np.arange(len(cats))
    n = len(scenarios)
    width = 0.7 / n
    palette = sns.color_palette("Set2", n)

    fig, ax = plt.subplots(figsize=(11, 5))
    for i, scenario in enumerate(scenarios):
        sub = df[df["scenario"] == scenario].set_index("method")
        vals = []
        for cat in cats:
            score = sub.loc[cat, "score"] if cat in sub.index else 0.0
            vals.append(score / NORMALIZATION_REF[cat])
        offsets = x + (i - n / 2 + 0.5) * width
        ax.bar(offsets, vals, width * 0.92,
               label=_scen_label(scenario, fu_map), color=palette[i],
               alpha=0.88, edgecolor="none")

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=9)
    ax.set_ylabel("Person-equivalents\n(score ÷ US per-capita annual reference)")
    ax.set_title("Normalized LCIA profile — TRACI 2.2 / US per-capita reference", fontsize=11, pad=10)
    if n > 1 or fu_map:
        ax.legend(fontsize=9, framealpha=0.75)
    fig.tight_layout()
    _save(fig, "normalized_profile")


# =============================================================================
# CHART 4: CONTRIBUTION ANALYSIS
# For each scenario: stacked horizontal bars per TRACI category, showing
# the top-N processes as a % of the category total. Remaining processes
# are lumped into "Other". One output file per scenario.
# Negative contributions (system expansion credits) are shown below zero.
# =============================================================================
def plot_contribution_analysis(df: pd.DataFrame, top_n: int = TOP_N_PROCESSES) -> None:
    fu_map = _fu_map(df)
    for scenario in df["scenario"].unique():
        sdf = df[df["scenario"] == scenario]

        # Rank processes GLOBALLY within this scenario (summed |contribution|
        # across all categories), keep the top_n, lump the rest into "Other".
        # Per-category top-N would union to a huge legend — this caps it at top_n+1.
        rank = (sdf.groupby("process_name")["contribution_score"]
                   .apply(lambda s: s.abs().sum())
                   .sort_values(ascending=False))
        keep = list(rank.head(top_n).index)

        methods_present = [m for m in CATEGORY_ORDER if m in sdf["method"].unique()]
        if not methods_present:
            continue
        cat_labels = [_short(m) for m in methods_present]

        procs = keep + ["Other"]
        pct = {p: [] for p in procs}
        for m in methods_present:
            g = sdf[sdf["method"] == m]
            total = g["contribution_score"].sum()
            kept_sum = 0.0
            for p in keep:
                v = g.loc[g["process_name"] == p, "contribution_score"].sum()
                pct[p].append(v / total * 100 if total else 0.0)
                kept_sum += v
            other = total - kept_sum
            pct["Other"].append(other / total * 100 if total else 0.0)

        tab = sns.color_palette("tab20", min(len(keep), 20))
        colors = {p: tab[i % 20] for i, p in enumerate(keep)}
        colors["Other"] = "#cccccc"

        fig, ax = plt.subplots(figsize=(13, 6))
        x = np.arange(len(cat_labels))
        pos_bottoms = np.zeros(len(cat_labels))
        neg_bottoms = np.zeros(len(cat_labels))
        handles = []
        for p in procs:
            vals = np.array(pct[p])
            if not np.any(vals != 0):
                continue
            pos = np.where(vals >= 0, vals, 0.0)
            neg = np.where(vals < 0,  vals, 0.0)
            ax.bar(x, pos, bottom=pos_bottoms, width=0.72,
                   color=colors[p], edgecolor="white", linewidth=0.3)
            ax.bar(x, neg, bottom=neg_bottoms, width=0.72,
                   color=colors[p], edgecolor="white", linewidth=0.3)
            pos_bottoms += pos
            neg_bottoms += neg
            handles.append(mpatches.Patch(color=colors[p], label=_proc_legend(p)))

        ax.axhline(100, color="#444", linewidth=0.8, linestyle="--")
        ax.axhline(0,   color="#444", linewidth=0.5)
        ax.set_xticks(x)
        ax.set_xticklabels(cat_labels, rotation=30, ha="right", fontsize=9)
        ax.set_ylabel("% contribution to impact-category total")
        ax.set_title(f"Process contribution — {_scen_label(scenario, fu_map, short=True)}",
                     fontsize=12, pad=10)
        # Legend outside the plot (right) so it never covers the bars.
        ax.legend(handles=handles, fontsize=8, loc="center left",
                  bbox_to_anchor=(1.01, 0.5), framealpha=0.9,
                  title=f"Top {top_n} contributors")
        fig.tight_layout()
        fname = "contribution_analysis_" + _proc_short(scenario).lower().replace(" ", "_")
        _save(fig, fname)


# =============================================================================
# CHART 4b: FOREGROUND vs BACKGROUND SPLIT
# 100%-stacked bars, one per TRACI category, split into two segments:
#   Foreground = the reference process node's own direct emissions
#   Background = everything upstream (the supply chain)
# One output file per scenario in FOREGROUND_BG_SCENARIOS.
# =============================================================================
def plot_foreground_background(df: pd.DataFrame) -> None:
    fu_map = _fu_map(df)
    scenarios = [s for s in FOREGROUND_BG_SCENARIOS if s in df["scenario"].unique()]
    if not scenarios:
        scenarios = list(df["scenario"].unique())

    for scenario in scenarios:
        sub = df[df["scenario"] == scenario]
        methods_present = [m for m in CATEGORY_ORDER if m in sub["method"].unique()]
        if not methods_present:
            continue
        cat_labels = [_short(m) for m in methods_present]

        fg_pct, bg_pct = [], []
        for m in methods_present:
            g = sub[sub["method"] == m]
            total = g["contribution_score"].sum()
            fg = g.loc[g["process_name"] == scenario, "contribution_score"].sum()
            bg = total - fg
            if total == 0:
                fg_pct.append(0.0); bg_pct.append(0.0)
            else:
                fg_pct.append(fg / total * 100)
                bg_pct.append(bg / total * 100)

        fig, ax = plt.subplots(figsize=(11, 5))
        x = np.arange(len(cat_labels))
        ax.bar(x, fg_pct, color="#2c7fb8", label="Foreground (this process, direct)",
               edgecolor="white", linewidth=0.4)
        ax.bar(x, bg_pct, bottom=fg_pct, color="#bcbddc", label="Background (supply chain)",
               edgecolor="white", linewidth=0.4)

        ax.axhline(100, color="#444", linewidth=0.8, linestyle="--")
        ax.set_xticks(x)
        ax.set_xticklabels(cat_labels, rotation=30, ha="right", fontsize=9)
        ax.set_ylabel("% of impact-category total")
        ax.set_ylim(0, 105)
        ax.set_title(f"Foreground vs background — {_scen_label(scenario, fu_map, short=True)}",
                     fontsize=12, pad=10)
        ax.legend(fontsize=9, loc="lower right", framealpha=0.9)
        fig.tight_layout()
        fname = "foreground_background_" + _proc_short(scenario).lower().replace(" ", "_")
        _save(fig, fname)


# =============================================================================
# CHART 5: MONTE CARLO DISTRIBUTIONS (stub — future)
# Expected input CSV columns: scenario, method, iteration, score
# Produces: violin or box plots per category, optionally faceted by scenario.
# Run general/04_run_lca.py with --mc-iterations N (not yet implemented) to
# generate mc_results.csv.
# =============================================================================
def plot_monte_carlo_distributions(df: pd.DataFrame) -> None:
    raise NotImplementedError(
        "Monte Carlo plotting not yet implemented.\n"
        "Expected CSV columns: scenario, method, iteration, score\n"
        "Planned chart: violin plots per TRACI category, faceted by scenario."
    )


# =============================================================================
# MAIN — auto-detect available inputs and dispatch to chart functions
# =============================================================================
def main() -> None:
    ran_any = False
    results_scenarios = None

    # --- LCA results (04 output) ---
    results_path = Path(RESULTS_CSV)
    if results_path.exists():
        print(f"\nLoading: {results_path}")
        results_df = pd.read_csv(results_path)
        if "scenario" in results_df.columns:
            results_scenarios = set(results_df["scenario"].unique())
        required = {"scenario", "method", "score", "unit"}
        missing = required - set(results_df.columns)
        if missing:
            print(f"  WARNING: results CSV missing columns {missing} — skipping results charts.")
        else:
            plot_impact_profile(results_df)
            plot_scenario_comparison(results_df)
            plot_normalized_profile(results_df)
            ran_any = True
    else:
        print(f"No results CSV at '{results_path}' — skipping impact / scenario / normalized charts.")

    # --- Contributions (04 output) ---
    contrib_path = Path(CONTRIBUTIONS_CSV)
    if contrib_path.exists():
        print(f"\nLoading: {contrib_path}")
        contrib_df = pd.read_csv(contrib_path)
        required = {"scenario", "method", "process_name", "contribution_score"}
        missing = required - set(contrib_df.columns)
        # Refuse a contributions file that describes different scenarios than the
        # results file. CONTRIBUTIONS_CSV defaults to a fixed repo-root path, so
        # omitting --contributions used to silently pair whatever ran last with
        # the current results — and it fired automatically, because a zero-score
        # run writes no contributions file at all. The charts landed in the right
        # directory under the wrong process's name and looked like success.
        _contrib_scen = (set(contrib_df["scenario"].unique())
                         if "scenario" in contrib_df.columns else set())
        _shared = _contrib_scen & (results_scenarios or set())
        if missing:
            print(f"  WARNING: contributions CSV missing columns {missing} — skipping contribution chart.")
        elif results_scenarios is not None and not _shared:
            print(f"  WARNING: '{contrib_path.name}' describes "
                  f"{sorted(_contrib_scen)[:2]}{'…' if len(_contrib_scen) > 2 else ''} "
                  f"but the results CSV describes "
                  f"{sorted(results_scenarios)[:2]}{'…' if len(results_scenarios) > 2 else ''} "
                  f"— no scenario in common, so this is a different run. Skipping the "
                  f"contribution charts; pass --contributions to name the matching file.")
        else:
            plot_contribution_analysis(contrib_df)
            plot_foreground_background(contrib_df)
            ran_any = True
    else:
        print(f"No contributions CSV at '{contrib_path}' — skipping contribution chart.")

    if not ran_any:
        print("\nNo usable input CSVs found. Run general/04_run_lca.py first.")
    else:
        print(f"\nAll charts written to: {out_dir.resolve()}")


if __name__ == "__main__":
    main()
