"""setup_biosphere.py — build the FEDEFL biosphere database as a callable step.

The library counterpart of `setup/01_setup_biosphere_fedefl.py`. Loads the Federal
Elementary Flow List into brightway as the biosphere database, replacing the
default ecoinvent `biosphere3` for EPA-based work. Every flow is keyed by its
FEDEFL UUID, which is what lets TRACI 2.2 (via lciafmt) and USLCI line up with it
without any name matching.

`import_biosphere()` is the script's body as a function: same checks, same order,
same numbers — progress goes to an optional `log` callable instead of `print`, and
rebuilding over an existing database takes `overwrite=`/`confirm=` rather than
telling the operator to go delete it by hand::

    from fedefl_bw25.setup_biosphere import import_biosphere
    build = import_biosphere(overwrite=True)
    print(build.flow_count, build.fedefl_version)

Rebuilding this database renumbers brightway's internal flow ids, so anything
already linked against it — `electricity-baseline`, both USLCI builds — must be
rebuilt afterwards or the technosphere goes non-square. It is also the base of the
locked validation, so treat a rebuild as a validation event.
"""
from __future__ import annotations

import importlib.metadata
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd
import bw2data as bd
import fedelemflowlist as fedefl

from fedefl_bw25.config import PROJECT_NAME, BIOSPHERE_DB

_NOOP = lambda *a, **k: None          # noqa: E731

# 90% of 332,133 (v1.3.0 baseline); update if FEDEFL grows significantly. A short
# return means a partial fetch, which would give a biosphere DB with incomplete
# UUID coverage — flows would silently fail to match rather than error.
FEDEFL_MIN_FLOWS = 298_919

# Fail fast if fedelemflowlist renames these — same risk as lciafmt in setup/02.
# Context and CAS No are read with .get() below and would silently default to an
# empty string if renamed, producing wrong categories for every flow without error.
REQUIRED_COLUMNS = {"Flow UUID", "Flowable", "Unit", "Context", "Class", "CAS No"}

# Identical duplicates are harmless (dedup silently); conflicting duplicates mean
# two different substances share a UUID — a FEDEFL data error that would silently
# corrupt whichever flow gets written last. These columns define "conflicting".
_IDENTITY_COLUMNS = ["Flowable", "Unit", "Context", "Class", "CAS No"]


class BiosphereExists(RuntimeError):
    """The database exists and neither overwrite nor confirm allowed a rebuild."""


@dataclass
class BiosphereBuild:
    """What one build produced."""
    database: str
    flow_count: int
    type_counts: dict
    units: list
    fedefl_version: str
    built_on: str
    notes: list = field(default_factory=list)


def pkg_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def parse_context(ctx) -> tuple:
    """'air/urban air close to ground' -> ('air', 'urban air close to ground')"""
    if pd.isna(ctx) or str(ctx).strip() == "":
        return ("unspecified",)
    return tuple(p.strip() for p in str(ctx).split("/") if p.strip())


def flow_type(row) -> str:
    """Classify as emission or natural resource based on context/class."""
    ctx = str(row.get("Context", "")).lower()
    cls = str(row.get("Class", "")).lower()
    if ctx.startswith("resource") or "resource" in cls:
        return "natural resource"
    return "emission"


def describe_existing(project=PROJECT_NAME) -> dict | None:
    """Provenance of the biosphere database already in `project`, or None.

    Sets the current project as a side effect, like every brightway read does.
    """
    bd.projects.set_current(project)
    if BIOSPHERE_DB not in bd.databases:
        return None
    meta = bd.databases[BIOSPHERE_DB]
    built_version = meta.get("fedefl_version", "unknown (pre-versioning)")
    installed = pkg_version("fedelemflowlist")
    return {
        "built_on": meta.get("fedefl_built_on", "unknown (pre-versioning)"),
        "built_version": built_version,
        "installed_version": installed,
        "stale": built_version != installed and built_version != "unknown (pre-versioning)",
    }


def check_flows(flows) -> list:
    """Validate the fedelemflowlist frame; return the deduplicated frame's notes.

    Raises RuntimeError on anything that would produce a quietly wrong database:
    renamed columns, an empty or partial fetch, or duplicate UUIDs that disagree
    about what substance they describe.
    """
    missing_cols = REQUIRED_COLUMNS - set(flows.columns)
    if missing_cols:
        raise RuntimeError(
            f"fedelemflowlist output missing expected columns: {missing_cols}\n"
            f"  Actual columns: {list(flows.columns)}"
        )
    if flows.empty:
        raise RuntimeError(
            "fedelemflowlist returned zero flows. This likely indicates a package "
            "installation or caching failure — reinstall fedelemflowlist and retry."
        )
    if len(flows) < FEDEFL_MIN_FLOWS:
        raise RuntimeError(
            f"fedelemflowlist returned only {len(flows)} flows (expected "
            f"≥{FEDEFL_MIN_FLOWS}). Partial data would produce a biosphere DB with "
            f"incomplete UUID coverage. Reinstall fedelemflowlist and retry."
        )

    key_cols = [c for c in _IDENTITY_COLUMNS if c in flows.columns]
    dup_uuids = flows[flows.duplicated("Flow UUID", keep=False)]
    if dup_uuids.empty:
        return []

    conflicts = []
    for uuid, group in dup_uuids.groupby("Flow UUID"):
        if group[key_cols].drop_duplicates().shape[0] > 1:
            conflicts.append((uuid, group[key_cols].to_dict("records")))
    if conflicts:
        msg = f"  {len(conflicts)} UUID(s) map to conflicting flow definitions:\n"
        for uuid, rows in conflicts[:5]:
            msg += f"    {uuid}: {rows}\n"
        if len(conflicts) > 5:
            msg += f"    ... and {len(conflicts) - 5} more\n"
        raise RuntimeError(
            "FEDEFL contains duplicate UUIDs with conflicting substance data.\n"
            + msg
            + "\nRemediation:\n"
            + "  1. File a bug at https://github.com/USEPA/Federal-LCA-Commons-Elementary-Flow-List/issues\n"
            + "     Include the UUID(s) and conflicting rows printed above.\n"
            + "  2. Until fixed, pin to the last known-good version:\n"
            + "     pip install fedelemflowlist==<last-good-version>\n"
            + "     Check the package changelog to identify when the conflict was introduced.\n"
        )
    return [f"{dup_uuids['Flow UUID'].nunique()} UUID(s) had identical duplicate rows "
            f"— kept one each."]


def build_flow_data(flows) -> dict:
    """The deduplicated frame as a brightway database dict, keyed by FEDEFL UUID."""
    return {
        (BIOSPHERE_DB, row["Flow UUID"]): {
            "name":       row["Flowable"],
            "code":       row["Flow UUID"],
            "unit":       row["Unit"],
            "categories": parse_context(row.get("Context", "")),
            "type":       flow_type(row),
            "CAS number": str(row.get("CAS No", "")),
        }
        for _, row in flows.iterrows()
    }


def import_biosphere(*, overwrite=False, confirm=None, project=PROJECT_NAME,
                     log=_NOOP) -> BiosphereBuild:
    """Fetch FEDEFL and write it to brightway as the biosphere database.

    `overwrite=True` replaces an existing database outright. `confirm` is an
    optional callable taking the database name and returning a bool — the CLI
    passes one that prompts; a scripted build passes `overwrite=True` so it never
    blocks on stdin. Neither means `BiosphereExists` is raised and nothing is
    touched.
    """
    bd.projects.set_current(project)
    if not bd.projects.twofive:
        bd.projects.migrate_project_25()

    existing = describe_existing(project)
    if existing:
        log(f"'{BIOSPHERE_DB}' already exists.")
        log(f"  Built on:            {existing['built_on']}")
        log(f"  Built with:          fedelemflowlist {existing['built_version']}")
        log(f"  Currently installed: fedelemflowlist {existing['installed_version']}")
        if existing["stale"]:
            log("  *** VERSION MISMATCH — consider rebuilding if FEDEFL flows changed ***")
        else:
            log("  Version matches installed package — DB appears current.")
        if not (overwrite or (confirm(BIOSPHERE_DB) if confirm else False)):
            raise BiosphereExists(
                f"Database '{BIOSPHERE_DB}' already exists. Pass overwrite=True to "
                f"rebuild it.\n  Note that rebuilding renumbers brightway's internal "
                f"flow ids: 'electricity-baseline' and both USLCI databases must be "
                f"rebuilt afterwards."
            )
        del bd.databases[BIOSPHERE_DB]

    log("Fetching FEDEFL flows...")
    flows = fedefl.get_flows()
    log(f"  {len(flows)} total flows retrieved.")
    log(f"  Columns: {list(flows.columns)}")

    notes = check_flows(flows)
    for note in notes:
        log(f"  {note}")
    flows = flows.drop_duplicates("Flow UUID")

    db_data = build_flow_data(flows)

    type_counts = {}
    for v in db_data.values():
        type_counts[v["type"]] = type_counts.get(v["type"], 0) + 1
    # A shift in the natural-resource count on a later rebuild means FEDEFL
    # reclassified flows.
    log(f"  Flow types: {', '.join(f'{k}={v}' for k, v in sorted(type_counts.items()))}")

    # A new or renamed unit here surfaces as a silent unit-conversion failure in
    # setup/03, not here — which is why the set is reported on every build.
    units = sorted({v["unit"] for v in db_data.values()})
    log(f"  Unit strings ({len(units)}): {', '.join(units)}")

    fedefl_version = pkg_version("fedelemflowlist")
    built_on = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    log(f"Writing '{BIOSPHERE_DB}' ({len(db_data)} flows) to project '{project}'...")
    log(f"  fedelemflowlist version: {fedefl_version}")
    db = bd.Database(BIOSPHERE_DB)
    db.write(db_data)
    meta = bd.databases[BIOSPHERE_DB]
    meta["fedefl_version"] = fedefl_version
    meta["fedefl_built_on"] = built_on
    bd.databases[BIOSPHERE_DB] = meta
    log("Done.")

    return BiosphereBuild(
        database=BIOSPHERE_DB, flow_count=len(db_data), type_counts=type_counts,
        units=units, fedefl_version=fedefl_version, built_on=built_on, notes=notes,
    )
