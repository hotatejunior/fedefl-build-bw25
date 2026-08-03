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

# Repo root — this file lives at the top level, so its parent directory IS the
# repo. Default output locations (lca_results.csv, charts/) anchor here so
# scripts produce the same files no matter which directory they are run from.
# Paths given explicitly on the command line still resolve against the CWD.
REPO_ROOT = Path(__file__).resolve().parent

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
METHOD_ROOT = ("TRACI", "2.2")
