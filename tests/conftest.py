"""Shared pytest setup.

Puts the repo root and the `general/` + `setup/` script dirs on sys.path so tests
can import the modules directly (`import foreground_importer`, `import olca_library`,
`from config import ...`) without the scripts being an installed package.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for _p in (ROOT, ROOT / "general", ROOT / "setup"):
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)
