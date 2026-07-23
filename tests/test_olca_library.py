"""Decode self-checks for setup/olca_library.py against the real US electricity
baseline library. These are non-circular: they validate that the matrix package
DECODES correctly (index round-trip, M == B·A^-1, shapes), not any LCA result.

Data-gated: skips if the baseline library isn't in source_data/.
"""
from pathlib import Path

import pytest

import olca_library

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "source_data" / "U.S._electricity_baseline_v1.2025-06.0_from_olca"

pytestmark = pytest.mark.skipif(
    not BASELINE.exists(),
    reason=f"baseline library not present at {BASELINE}",
)


@pytest.fixture(scope="module")
def lib():
    return olca_library.read_library(BASELINE)


def test_decoded_dimensions(lib):
    # Canonical 2025 baseline: A 771x771, B 14818x771 (pinned in VALIDATION_LOG.md).
    assert lib.n_proc == 771
    assert lib.n_flow == 14818
    assert lib.M.shape == (lib.n_flow, lib.n_proc)
    assert len(lib.processes) == lib.n_proc
    assert len(lib.flows) == lib.n_flow


def test_self_check_passes(lib):
    # self_check() asserts index round-trip and M == B·A^-1 internally.
    olca_library.self_check(lib)


def test_process_col_round_trip(lib):
    for p in lib.processes[:25]:
        uid = p["primary"].get("id")
        if uid:
            assert lib.process_col(uid) == p["pos"]


def test_cumulative_inventory_nonempty_for_first_process(lib):
    uid = next(p["primary"]["id"] for p in lib.processes if p["primary"].get("id"))
    inv = lib.cumulative_inventory(uid)
    assert isinstance(inv, dict)
    # A pre-solved aggregated column should carry a nonzero cumulative inventory.
    assert len(inv) > 0
    assert all(v != 0.0 for v in inv.values())


def test_unknown_process_uuid_raises(lib):
    with pytest.raises(KeyError):
        lib.process_col("00000000-0000-0000-0000-000000000000")
