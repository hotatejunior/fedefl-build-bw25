"""fedefl_bw25 — the importable core of the USLCI · FEDEFL · TRACI brightway pipeline.

The numbered scripts in `setup/`, `general/` and `validation/` are CLI front-ends
over this package. Every step of the pipeline is also a function, so the whole
thing fits in one script rather than five invocations — see
`examples/full_pipeline.py`::

    from fedefl_bw25.setup_baseline import inject_baseline
    from fedefl_bw25.setup_uslci import import_uslci
    from fedefl_bw25.run import run_lca

    inject_baseline(vintage="2026", overwrite=True)   # must precede the import
    import_uslci(full_db=True, overwrite=True)
    run_lca(uuid=..., database="uslci-full").score("Global warming")

Two conventions make that work. Nothing here prints — progress goes to an optional
`log` callable — and nothing prompts: replacing an existing database takes
`overwrite=True` or a `confirm` callback, so a scripted build never blocks on
stdin. Results are returned rather than written, so a sweep can hold many runs in
memory and write once.

Modules divide by what they touch. `allocation`, `chart_units`, `run_manifest`,
`vintage_detect`, `olca_library`, `foreground_importer` and `setup_conversions`
are brightway-free: they operate on plain data supplied by the caller, which is
why they unit-test without a built database. `run`, `setup_biosphere`,
`setup_traci`, `setup_baseline` and `setup_uslci` drive brightway.

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
    "setup_baseline",
    "setup_biosphere",
    "setup_conversions",
    "setup_traci",
    "setup_uslci",
    "vintage_detect",
]
