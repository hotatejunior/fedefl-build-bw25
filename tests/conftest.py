"""Shared pytest setup.

The library modules live in the `fedefl_bw25` package and are imported normally
(`from fedefl_bw25 import run_manifest`), so no sys.path manipulation is needed —
install the repo once with `pip install -e .`.

The numbered CLI scripts in `setup/`, `general/` and `validation/` remain
unimportable: their filenames start with digits and they execute their whole body
on import. Tests therefore exercise the package, and anything a test cannot reach
is a signal that the logic belongs in the package rather than in a script.
"""
