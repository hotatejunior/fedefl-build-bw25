"""fedefl_bw25 — the importable core of the USLCI · FEDEFL · TRACI brightway pipeline.

These are the pure, side-effect-free modules: importing any of them builds nothing,
touches no brightway project, and reads no data. The numbered scripts in `setup/`,
`general/` and `validation/` are CLI front-ends over this package.

Nothing here imports brightway. `run_manifest`, `chart_units`, `allocation` and
`vintage_detect` operate on plain Python data supplied by the caller, which is why
they unit-test without a built database; `olca_library` decodes openLCA library
packages; `foreground_importer` parses and validates inventory CSVs.

Typical use::

    from fedefl_bw25 import config
    from fedefl_bw25.foreground_importer import load_foreground_csv

Submodules are not imported eagerly — `config` in particular is imported by every
script and should stay free of optional dependencies.
"""

__all__ = [
    "allocation",
    "chart_units",
    "config",
    "foreground_importer",
    "olca_library",
    "run_manifest",
    "vintage_detect",
]
