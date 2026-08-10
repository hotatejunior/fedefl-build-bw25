"""
config.py
---------
Shared brightway project/database/method identifiers used across setup/,
general/, and validation/ scripts. Previously each of these was duplicated
verbatim (with drifting local names — e.g. USLCI_DB vs USLCI_DB_NAME) across
6+ files; this is the single source of truth now.

Scripts import this from a subdirectory via:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import PROJECT_NAME, BIOSPHERE_DB, USLCI_DB, METHOD_ROOT
"""

from pathlib import Path

# Repo root — this file now lives inside the `fedefl_bw25` package, so the repo is
# its GRANDparent. Default output locations (lca_results.csv, charts/) anchor here
# so scripts produce the same files no matter which directory they are run from.
# Paths given explicitly on the command line still resolve against the CWD.
#
# This holds for a source checkout and for `pip install -e .`, which is the
# supported way to use the package. A non-editable install into site-packages has
# no repo to anchor to, so the fallback is the current working directory —
# otherwise defaults would silently write into site-packages. The marker is
# `setup/`, which exists in a checkout and never in an installed wheel.
_PKG_PARENT = Path(__file__).resolve().parent.parent
REPO_ROOT = _PKG_PARENT if (_PKG_PARENT / "setup").is_dir() else Path.cwd()

# The flow conversion table setup/00 builds and setup/03 + 03b both read. Defined
# once here because the two scripts previously each derived it from their own
# __file__, which is how the pair would drift apart if either ever moved.
CONV_TABLE_PATH = REPO_ROOT / "setup" / "uslci_flow_conversions.json"

PROJECT_NAME = "fedefl-build-bw25"
BIOSPHERE_DB = "biosphere-fedefl"
USLCI_DB = "uslci-subset"
# The whole-database build produced by `USLCI_FULL_DB=1 setup/03_import_uslci.py`
# (~1,341 processes from the single full USLCI zip, vs. the per-process bundles
# that make up USLCI_DB). Deliberately a SEPARATE database so the two builds
# coexist: the validation harness stays pinned to USLCI_DB — the set the locked
# reference exports were computed against — while general/04 can be pointed at
# either. Switching modes must never silently repoint the harness.
USLCI_FULL_DB = "uslci-full"
ELECTRICITY_BASELINE_DB = "electricity-baseline"
# The transient database `run.run_lca` writes a foreground CSV into and drops after.
# Here rather than in run.py so `search` can exclude it without importing run.py,
# which pulls in bw2calc — slow, and noisy on ARM.
FOREGROUND_DB = "foreground"
METHOD_ROOT = ("TRACI", "2.2")

# Databases that hold something other than runnable unit processes: elementary
# flows, pre-solved background columns, and the transient foreground. Search skips
# these by default so a bare --search sees study targets and nothing else; naming
# one explicitly with --database still works.
NON_TARGET_DBS = frozenset({BIOSPHERE_DB, ELECTRICITY_BASELINE_DB, FOREGROUND_DB})
