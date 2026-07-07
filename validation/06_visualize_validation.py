#!/usr/bin/env python3
"""
06_visualize_validation.py
---------------------------
Generates validation-only LCA charts from CSVs produced by
validation/05_validate_uslci.py — brightway-vs-openLCA parity checks.

No brightway dependency — pandas + matplotlib + seaborn only.

Charts are written under charts/validation/:
  validation_ratio_*.png — BW/OL lollipop per validation mode (Chart A)
  validation_pct_*.png   — BW score as % of OL reference per mode (Chart B)

For the general-use charts (impact profile, contribution analysis, etc.), see
general/06_visualize.py.

Usage
-----
IDE / notebook: edit CONFIG block and run directly.
Terminal (from repo root):
  python validation/06_visualize_validation.py [--validation CSV]
                                                [--output-dir DIR]
                                                [--format png|svg] [--dpi N]
"""

import argparse
import sys
import warnings
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import seaborn as sns

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from config import REPO_ROOT

warnings.filterwarnings("ignore", category=UserWarning, module="matplotlib")

# =============================================================================
# CONFIG
# =============================================================================
VALIDATION_CSV = None   # override to a specific file; None = auto-detect in validation/
OUTPUT_DIR     = REPO_ROOT / "charts"   # CLI --output-dir resolves against CWD instead
OUTPUT_FORMAT  = "png"   # "png" or "svg"
DPI            = 300

# Short display labels for TRACI 2.2 categories.
# Keys must match the `category` column in validation_*_results.csv exactly.
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

# Short display labels for the validation test-case processes. Keys must match the
# `process` column in validation_*_results.csv exactly. Used to disambiguate the
# per-category rows in the validation chart (e.g. GWP_Petroleum vs GWP_Corn).
PROCESS_LABELS = {
    "Petroleum refining; at refinery": "Petroleum",
    "Corn; whole plant; at field":     "Corn",
    "Portland cement; at plant":       "Cement",
    "Steel; billets; at plant":        "Billets",
}

# Order the test-case processes appear within each category group.
PROCESS_ORDER = list(PROCESS_LABELS.keys())

_PASS_BAND = (0.95, 1.05)

# =============================================================================
# CLI
# =============================================================================
parser = argparse.ArgumentParser(description="Generate BW-vs-openLCA validation charts from 05's CSVs.")
parser.add_argument("--validation",    default=None, help="Path to validation_*_results.csv")
parser.add_argument("--output-dir",    default=None, dest="output_dir")
parser.add_argument("--format",        default=None, choices=["png", "svg"])
parser.add_argument("--dpi",           default=None, type=int)
args = parser.parse_args()

if args.validation:    VALIDATION_CSV = args.validation
if args.output_dir:    OUTPUT_DIR     = args.output_dir
if args.format:        OUTPUT_FORMAT  = args.format
if args.dpi:            DPI            = args.dpi

out_dir = Path(OUTPUT_DIR)
out_dir.mkdir(parents=True, exist_ok=True)

VALIDATION_SUBDIR = "validation"

# =============================================================================
# HELPERS
# =============================================================================
sns.set_theme(style="whitegrid", palette="muted", font_scale=0.95)


def _short(cat):
    return CATEGORY_LABELS.get(cat, cat)


def _proc_short(name):
    """Short label for a test-case process; falls back to a truncated name."""
    if name in PROCESS_LABELS:
        return PROCESS_LABELS[name]
    return name.split(";")[0][:16]


def _save(fig, name):
    dest = out_dir / VALIDATION_SUBDIR
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
# CHART A: VALIDATION RATIO
# Lollipop dot chart: BW/OL ratio per TRACI category.
# Green dot = within ±5%, orange = within ±20%, red = outside ±20%.
# Reference line at 1.0; shaded ±5% tolerance band.
# One output file per validation mode (direct / full_chain).
# =============================================================================
def plot_validation_ratio(df: pd.DataFrame) -> None:
    cat_ord  = {c: i for i, c in enumerate(CATEGORY_ORDER)}
    proc_ord = {p: i for i, p in enumerate(PROCESS_ORDER)}

    for mode in df["mode"].unique():
        sub = df[df["mode"] == mode].copy()
        multi_proc = sub["process"].nunique() > 1

        # Sort by category group, then by process within each group.
        sub["_c"] = sub["category"].map(lambda x: cat_ord.get(x, 999))
        sub["_p"] = sub["process"].map(lambda x: proc_ord.get(x, 999))
        sub = sub.sort_values(["_c", "_p"]).reset_index(drop=True)

        # Row label: "GWP_Petroleum" when several processes share a category,
        # else just the category short name.
        if multi_proc:
            sub["label"] = sub.apply(
                lambda r: f"{_short(r['category'])}_{_proc_short(r['process'])}", axis=1)
        else:
            sub["label"] = sub["category"].map(_short)

        n = len(sub)
        # ~0.34 in per row keeps labels clear of each other; floor for small sets.
        fig_h = max(5.0, 0.34 * n + 1.4)
        fig, ax = plt.subplots(figsize=(10, fig_h))

        ax.axvspan(_PASS_BAND[0], _PASS_BAND[1], alpha=0.10, color="green", zorder=0)
        ax.axvline(1.0, color="#333", linewidth=1.0, linestyle="-", zorder=1)

        # Faint alternating band per category group, so the eye can separate them.
        if multi_proc:
            for _, grp in sub.groupby("_c"):
                idx = grp.index
                if (cat_ord.get(grp["category"].iloc[0], 0)) % 2 == 0:
                    ax.axhspan(idx.min() - 0.5, idx.max() + 0.5,
                               color="#000000", alpha=0.035, zorder=0)

        for i, row in sub.iterrows():
            r = row["ratio_bw_ol"]
            if r is None or pd.isna(r):
                # Both engines report zero (e.g. petroleum has no direct FW-eutro
                # flows) — mark it explicitly instead of leaving a blank row.
                ax.scatter([1.0], [i], s=48, facecolors="none", edgecolors="#999",
                           linewidths=1.2, zorder=3)
                ax.annotate("n/a — BW and OL both zero", (1.0, i), fontsize=7.5,
                            color="#888", textcoords="offset points", xytext=(8, -2.5))
                continue
            color = ("#3cb464" if _PASS_BAND[0] <= r <= _PASS_BAND[1]
                     else "#e07b30" if 0.80 <= r <= 1.20
                     else "#cc3333")
            ax.plot([1.0, r], [i, i], color=color, linewidth=1.3, zorder=2, solid_capstyle="round")
            ax.scatter([r], [i], color=color, s=48, zorder=3)
            ax.annotate(f"{r:.3f}", (r, i), fontsize=7, color=color,
                        textcoords="offset points", xytext=(6, 5))

        ax.set_yticks(range(n))
        ax.set_yticklabels(sub["label"], fontsize=8.5)
        ax.set_ylim(n - 0.5, -0.5)   # invert, with a little padding
        ax.set_xlabel("brightway / openLCA ratio", fontsize=10)
        proc_note = "" if multi_proc else f" — {_proc_short(sub['process'].iloc[0])} only"
        ax.set_title(f"Pipeline validation — BW/OL ratio ({mode} mode{proc_note})",
                     fontsize=12, pad=12)
        ax.margins(y=0)

        band_patch = mpatches.Patch(color="green", alpha=0.25, label="±5% tolerance")
        ax.legend(handles=[band_patch], fontsize=9, loc="lower left",
                  bbox_to_anchor=(0, 1.005), frameon=False)
        fig.tight_layout()
        _save(fig, f"validation_ratio_{mode}")


# =============================================================================
# CHART B: VALIDATION % BARS
# Horizontal bars: BW score as % of the openLCA reference value.
# Reference line at 100%; dotted line at 95%.
# One output file per validation mode.
# =============================================================================
def plot_validation_pct(df: pd.DataFrame) -> None:
    def _color(r):
        if r is None or pd.isna(r): return "#aaaaaa"
        if r >= 0.95: return "#3cb464"
        if r >= 0.80: return "#e07b30"
        return "#cc3333"

    for mode in df["mode"].unique():
        sub = _sort_cats(df[df["mode"] == mode].copy(), "category").reset_index(drop=True)
        multi_proc = sub["process"].nunique() > 1
        # Bars are positioned numerically (not by label) — with several processes
        # per category, duplicate category labels would overplot on shared rows.
        if multi_proc:
            sub["label"] = sub.apply(
                lambda r: f"{_short(r['category'])}_{_proc_short(r['process'])}", axis=1)
        else:
            sub["label"] = sub["category"].map(_short)
        sub["pct"]   = sub["ratio_bw_ol"].clip(upper=2.0) * 100
        sub["color"] = sub["ratio_bw_ol"].map(_color)

        n = len(sub)
        fig, ax = plt.subplots(figsize=(9, max(5.0, 0.3 * n + 1.2)))
        ax.barh(range(n), sub["pct"].fillna(0), color=sub["color"], alpha=0.85, edgecolor="none")
        ax.set_yticks(range(n))
        ax.set_yticklabels(sub["label"], fontsize=9)
        for y, (pct, r) in enumerate(zip(sub["pct"], sub["ratio_bw_ol"])):
            if pd.isna(r):
                ax.annotate("n/a — BW and OL both zero", (2, y), fontsize=7.5,
                            color="#888", va="center")
            else:
                ax.annotate(f"{pct:.1f}%", (pct, y), fontsize=7.5, color="#444",
                            va="center", textcoords="offset points", xytext=(4, 0))
        ax.axvline(100, color="#444", linewidth=0.9, linestyle="--")
        ax.axvline(95,  color="#3cb464", linewidth=0.7, linestyle=":")
        ax.set_xlabel("BW score as % of openLCA reference")
        proc_note = "" if multi_proc else f" — {_proc_short(sub['process'].iloc[0])} only"
        ax.set_title(f"Pipeline validation — % of reference ({mode} mode{proc_note})",
                     fontsize=11, pad=10)
        ax.invert_yaxis()
        ax.tick_params(axis="y", labelsize=9)
        fig.tight_layout()
        _save(fig, f"validation_pct_{mode}")


# =============================================================================
# MAIN — auto-detect available validation CSV(s) and dispatch to chart functions
# =============================================================================
def main() -> None:
    ran_any = False

    if VALIDATION_CSV:
        val_paths = [Path(VALIDATION_CSV)]
    else:
        # 05 writes its CSVs next to itself in validation/, not the CWD.
        val_paths = sorted(HERE.glob("validation_*_results.csv"))
        if val_paths:
            print(f"\nAuto-detected validation CSV(s): {[str(p) for p in val_paths]}")

    required_val = {"process", "mode", "category", "bw_score", "ol_score", "ratio_bw_ol"}
    for vp in val_paths:
        if not vp.exists():
            print(f"  Validation CSV not found: {vp}")
            continue
        print(f"\nLoading: {vp}")
        val_df = pd.read_csv(vp)
        missing = required_val - set(val_df.columns)
        if missing:
            print(f"  WARNING: validation CSV missing columns {missing} — skipping.")
            continue
        plot_validation_ratio(val_df)
        plot_validation_pct(val_df)
        ran_any = True

    if not ran_any:
        print("\nNo usable validation CSVs found. Run validation/05_validate_uslci.py first.")
    else:
        print(f"\nAll charts written to: {out_dir.resolve()}")


if __name__ == "__main__":
    main()
