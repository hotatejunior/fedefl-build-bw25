"""Integration test: the cement full-chain cell reproduces the locked results.

The cheapest end-to-end guard from the release plan (Phase 4.1). It recomputes
Portland cement's full-chain TRACI scores in brightway and checks them against the
brightway column of validation/validation_full_chain_results.csv. Needs only the
built brightway project (not the ~130 MB openLCA exports), so it exercises the
parser + solve + LCIA without the reference xlsx.

Data-gated: skips unless the fedefl-build-bw25 project, the uslci-subset DB, the
TRACI methods, AND the locked CSV are all present.

Also vintage-gated. Cement is one of the four cases locked against the 2025
electricity baseline, so its scores are only comparable on a 2025 build. A build
injects exactly one vintage (see setup/vintage_detect.py), so on a 2026 build
cement draws a different grid and the locked numbers legitimately don't match —
that is the vintage guard working, not a regression. Mirrors the EXPECTED_VINTAGE
check in validation/05_validate_uslci.py: skip rather than fail. A build with no
stamp predates the guard, so it is allowed through like the harness allows it.
"""
import csv
from pathlib import Path

import pytest

from fedefl_bw25.config import PROJECT_NAME, USLCI_DB, METHOD_ROOT

bd = pytest.importorskip("bw2data")
bc = pytest.importorskip("bw2calc")

ROOT = Path(__file__).resolve().parents[1]
LOCKED = ROOT / "validation" / "validation_full_chain_results.csv"
CEMENT_UUID = "62993671-574c-3fc5-b66a-6be3bb21ad3d"
CEMENT_NAME = "Portland cement; at plant"
CEMENT_VINTAGE = "2025"   # the baseline the locked cement export was computed against


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


def _build_vintage():
    """This build's electricity_vintage stamp, or None if it predates the stamp."""
    try:
        return bd.databases[USLCI_DB].get("electricity_vintage")
    except Exception:
        return None


_ready = _project_ready()
_vintage = _build_vintage() if _ready else None

pytestmark = [
    pytest.mark.skipif(
        not _ready,
        reason="brightway project/DB/methods or locked CSV not available (run the setup chain first)",
    ),
    pytest.mark.skipif(
        _ready and _vintage is not None and _vintage != CEMENT_VINTAGE,
        reason=f"build is electricity_vintage={_vintage}, but the locked cement scores are "
               f"{CEMENT_VINTAGE}-baseline. Rebuild with 'setup/03b --vintage {CEMENT_VINTAGE}' "
               f"+ setup/03 to run this check.",
    ),
]


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
