"""Pure-unit tests for setup/vintage_detect.py — electricity-baseline vintage
auto-detection.

No bundles or brightway needed: classify_bundle() takes find_external_providers()
output as plain data, so the fixtures below are small hand-built dicts shaped
like the real thing.

The counts in the fixtures are the measured ones. The four locked 2025 bundles
each reference the 2025 grid node ~79-80 times AND the 2026 node exactly once;
the 2026-drop bundles reference only the 2026 node, 80-92 times. That stray
single reference is why detection is by dominance, not presence — a
presence-based test would classify every bundle in the repo as 2026.
"""
import vintage_detect as vd

G25 = "7068192a-999c-39b6-bf66-234a294bdf92"
G26 = "75d4be66-12a7-30b3-bc57-fa724c941b0e"
GRIDS = {"2025": G25, "2026": G26}


def _ext(**counts):
    """Build a find_external_providers()-shaped dict from {uuid: n_referencers}."""
    return {uuid: {"provider_name": "Electricity; at user; consumption mix - US - US",
                   "referenced_by": {f"proc{i}" for i in range(n)}}
            for uuid, n in counts.items() if n}


# Measured shapes
PETROLEUM = _ext(**{G25: 79, G26: 1})     # locked 2025 bundle, one stray 2026 ref
CHLORINE  = _ext(**{G26: 80})             # 2026 drop
HARDBOARD = _ext(**{G26: 92})
STEEL     = _ext()                        # foreground only, no external providers


def test_locked_bundle_with_stray_other_vintage_ref_is_2025():
    # The whole point: 79 vs 1 must resolve to 2025, not "mentions 2026".
    assert vd.classify_bundle(PETROLEUM, GRIDS)["vintage"] == "2025"


def test_2026_drop_bundle_is_2026():
    assert vd.classify_bundle(CHLORINE, GRIDS)["vintage"] == "2026"


def test_bundle_with_no_grid_reference_is_undecided():
    v = vd.classify_bundle(STEEL, GRIDS)
    assert v["vintage"] is None
    assert "no electricity-baseline grid node" in v["reason"]


def test_exact_tie_is_undecided_rather_than_guessed():
    v = vd.classify_bundle(_ext(**{G25: 5, G26: 5}), GRIDS)
    assert v["vintage"] is None
    assert "ties" in v["reason"]


def test_counts_are_reported_for_both_vintages():
    assert vd.classify_bundle(PETROLEUM, GRIDS)["counts"] == {"2025": 79, "2026": 1}


def test_unanimous_bundles_detect_that_vintage():
    d = vd.decide_vintage({"chlorine": vd.classify_bundle(CHLORINE, GRIDS),
                           "hardboard": vd.classify_bundle(HARDBOARD, GRIDS)})
    assert d["vintage"] == "2026" and d["source"] == "detected"
    assert d["conflict"] is False


def test_agnostic_bundle_does_not_block_detection():
    # Steel votes for nothing, so it must not turn a clean 2026 set into a conflict.
    d = vd.decide_vintage({"chlorine": vd.classify_bundle(CHLORINE, GRIDS),
                           "steel": vd.classify_bundle(STEEL, GRIDS)})
    assert d["vintage"] == "2026" and d["conflict"] is False
    assert [lbl for lbl, _ in d["undecided"]] == ["steel"]


def test_mixed_bundles_conflict_rather_than_pick_one():
    d = vd.decide_vintage({"petroleum": vd.classify_bundle(PETROLEUM, GRIDS),
                           "chlorine": vd.classify_bundle(CHLORINE, GRIDS)})
    assert d["conflict"] is True
    assert d["vintage"] is None
    assert d["groups"] == {"2025": ["petroleum"], "2026": ["chlorine"]}


def test_explicit_vintage_overrides_detection_and_conflict():
    d = vd.decide_vintage({"petroleum": vd.classify_bundle(PETROLEUM, GRIDS),
                           "chlorine": vd.classify_bundle(CHLORINE, GRIDS)},
                          explicit="2025")
    assert d["vintage"] == "2025" and d["source"] == "explicit"
    assert d["conflict"] is False


def test_no_grid_references_anywhere_falls_back_to_default():
    d = vd.decide_vintage({"steel": vd.classify_bundle(STEEL, GRIDS)}, default="2025")
    assert d["vintage"] == "2025" and d["source"] == "default"
    assert d["conflict"] is False


def test_conflict_message_names_both_groups_and_the_flag():
    d = vd.decide_vintage({"petroleum": vd.classify_bundle(PETROLEUM, GRIDS),
                           "chlorine": vd.classify_bundle(CHLORINE, GRIDS),
                           "steel": vd.classify_bundle(STEEL, GRIDS)})
    msg = vd.format_conflict(d)
    assert "--vintage 2025" in msg and "--vintage 2026" in msg
    assert "petroleum" in msg and "chlorine" in msg
    assert "steel" in msg and "vintage-agnostic" in msg
