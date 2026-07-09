"""Integration test: the cement full-chain cell reproduces the locked results.

The cheapest end-to-end guard from the release plan (Phase 4.1). It recomputes
Portland cement's full-chain TRACI scores in brightway and checks them against the
brightway column of validation/validation_full_chain_results.csv. Needs only the
built brightway project (not the ~130 MB openLCA exports), so it exercises the
parser + solve + LCIA without the reference xlsx.

Data-gated: skips unless the fedefl-build-bw25 project, the uslci-subset DB, the
TRACI methods, AND the locked CSV are all present.
"""
import csv
from pathlib import Path

import pytest

from config import PROJECT_NAME, USLCI_DB, METHOD_ROOT

bd = pytest.importorskip("bw2data")
bc = pytest.importorskip("bw2calc")

ROOT = Path(__file__).resolve().parents[1]
LOCKED = ROOT / "validation" / "validation_full_chain_results.csv"
CEMENT_UUID = "62993671-574c-3fc5-b66a-6be3bb21ad3d"
CEMENT_NAME = "Portland cement; at plant"


def _project_ready():
    if not LOCKED.exists():
        return False
    try:
        if PROJECT_NAME not in {p.name for p in bd.projects}:
            return False
        bd.projects.set_current(PROJECT_NAME)
    except Exception:
        return False
    return USLCI_DB in bd.databases and any(m[:2] == METHOD_ROOT for m in bd.methods)


pytestmark = pytest.mark.skipif(
    not _project_ready(),
    reason="brightway project/DB/methods or locked CSV not available (run the setup chain first)",
)


def _locked_cement_bw_scores():
    scores = {}
    with open(LOCKED, newline="") as f:
        for row in csv.DictReader(f):
            if row["process"] == CEMENT_NAME and row.get("bw_score"):
                scores[row["category"]] = float(row["bw_score"])
    return scores


def test_cement_full_chain_matches_locked_bw_scores():
    bd.projects.set_current(PROJECT_NAME)
    methods = {m[2]: m for m in bd.methods if m[:2] == METHOD_ROOT}
    act = bd.get_activity((USLCI_DB, CEMENT_UUID))

    # Cement's reference product is 1 kg (unit kg), so the harness's per-kg
    # normalization factor is 1.0 and the raw score equals the locked bw_score.
    assert (act.get("unit") or "kg").lower() == "kg"

    sorted_methods = sorted(methods.items())
    lca = bc.LCA({act: 1.0}, sorted_methods[0][1])
    lca.lci()
    computed = {}
    for category, method in sorted_methods:
        lca.switch_method(method)
        lca.lcia()
        computed[category] = lca.score

    locked = _locked_cement_bw_scores()
    assert locked, "no cement bw_score rows found in the locked CSV"

    for category, want in locked.items():
        assert category in computed, f"category {category!r} not computed"
        assert computed[category] == pytest.approx(want, rel=1e-9, abs=1e-30)
