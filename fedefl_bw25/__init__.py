"""fedefl_bw25 — the importable core of the USLCI · FEDEFL · TRACI brightway pipeline.

These are the pure, side-effect-free modules: importing any of them builds nothing,
touches no brightway project, and reads no data. The numbered scripts in `setup/`,
`general/` and `validation/` are CLI front-ends over this package.

Every module here except `run` is brightway-free: `run_manifest`, `chart_units`,
`allocation` and `vintage_detect` operate on plain Python data supplied by the
caller, which is why they unit-test without a built database; `olca_library`
decodes openLCA library packages; `foreground_importer` parses and validates
inventory CSVs. `run` is the exception and the point — it drives brightway, and
returns results rather than writing them, so a study can be scripted::

    from fedefl_bw25.run import run_lca, write_results_csv

    for label, uuid in targets.items():
        r = run_lca(uuid=uuid, database="uslci-full", scenario=label)
        print(label, r.score("Global warming"), r.functional_unit)
        write_results_csv(r, "study.csv", append=True)

Submodules are not imported eagerly — `config` in particular is imported by every
script and should stay free of optional dependencies.
"""

__all__ = [
    "allocation",
    "chart_units",
    "config",
    "foreground_importer",
    "olca_library",
    "run",
    "run_manifest",
    "vintage_detect",
]
