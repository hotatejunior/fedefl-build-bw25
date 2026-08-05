"""setup_uslci.py — build the USLCI brightway database as a callable step.

The library counterpart of `setup/03_import_uslci.py`. Parses USLCI openLCA
JSON-LD directly rather than through `bw2io`'s JSONLDImporter, which has known
bugs with the `isInput` field that silently misclassify exchanges.

    from fedefl_bw25.setup_uslci import import_uslci
    build = import_uslci(full_db=True, overwrite=True)
    print(build.database, build.activity_count, build.totals["tech_ambiguous"])

`import_uslci()` orchestrates; the work is in the stages below, each importable
and inspectable on its own:

    load_conversion_table    the flow-property factors, checked against their zip
    UnitNormalizer           exchange amount -> the flow's reference unit
    discover_sources         which zips this build reads (bundles, or the full DB)
    load_sources             zips -> processes + flows, newest copy wins
    index_reference_flows    which process supplies which reference flow
    load_external_providers  what 03b injected, and at which grid vintage
    ProviderResolver         a technosphere exchange -> the activity supplying it
    plan_allocation          allocation factors + co-product re-basis, all processes
    build_activities         parsed data -> brightway activity dicts

This builds the database the locked validation is computed against, so treat any
behavioural change as a validation event: rebuild both databases and re-run the
replication gate, not just the unit tests.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import bw2data as bd

from fedefl_bw25.allocation import allocation_for, causal_coproducts, coproduct_multipliers
from fedefl_bw25.config import (PROJECT_NAME, BIOSPHERE_DB, CONV_TABLE_PATH,
                                USLCI_DB as USLCI_BUNDLE_DB_NAME,
                                USLCI_FULL_DB as USLCI_FULL_DB_NAME,
                                ELECTRICITY_BASELINE_DB as EXTERNAL_PROVIDER_DB,
                                REPO_ROOT)

_NOOP = lambda *a, **k: None          # noqa: E731

# Defaults to source_data/ at the repo root; override with SOURCE_DATA_DIR or the
# `bundle_dir` argument. Must be the SAME directory setup_baseline scans, so the
# electricity providers it injects line up with the bundles imported here.
DEFAULT_BUNDLE_DIR = Path(os.environ.get("SOURCE_DATA_DIR", REPO_ROOT / "source_data"))

BUNDLE_GLOB = "????????-????-????-????-????????????_*.zip"

# How many of each diagnostic to keep as worked examples for the console summary.
MAX_EXAMPLES = 5



class UslciExists(RuntimeError):
    """The target database exists and neither overwrite nor confirm allowed it."""


@dataclass
class UslciBuild:
    """What one import produced."""
    database: str
    full_db: bool
    activity_count: int
    process_count: int
    totals: dict
    source: dict
    electricity_vintage: str | None
    provenance_path: str
    notes: list = field(default_factory=list)


WITHIN_FP = {
    # Mass → kg
    "kg": 1.0,   "g": 1e-3,    "mg": 1e-6,   "lb": 0.45359237,
    "lb av": 0.45359237,        "sh tn": 907.18474,  "t": 1e3,  "ton": 907.18474,
    "short ton": 907.18474,     "metric ton": 1e3,   "tonne": 1e3,
    # Volume → m3
    "m3": 1.0,   "l": 1e-3,    "ml": 1e-6,
    "gal": 3.78541e-3,  "gal (us liq)": 3.78541e-3,  "gal (us fl)": 3.78541e-3,
    "gal (imp)": 4.54609e-3,
    "cu ft": 0.0283168,  "ft3": 0.0283168,
    # Energy → MJ
    "mj": 1.0,   "kj": 1e-3,   "gj": 1e3,    "kwh": 3.6,
    "btu": 1.05506e-3,
    "mmbtu": 1055.06,   "mm btu": 1055.06,   "mmBtu": 1055.06,
    # kcal is the INTERNATIONAL TABLE calorie (4.1868 J), matching the IT Btu
    # above (1055.06 J) and openLCA's own reference data. The thermochemical
    # calorie (4.184 J) would be 0.07% low — under the 0.1% replication gate,
    # so a wrong pick here would not be caught by the harness. Added for USLCI
    # v1.2026-06.0, which introduced kcal on coal/natural-gas combustion.
    "kcal": 4.1868e-3,
    # Transport (freight) → t*km
    "t*km": 1.0, "tkm": 1.0,   "t*mi": 1.60934,  "kg*km": 1e-3,
    # Transport (passenger) → p*km (person-kilometre; already reference unit)
    "p*km": 1.0,
    # Duration → h (USLCI service flows, e.g. chainsawing/skidding, are defined
    # and consumed as "1 h of <service>"; h is their reference unit)
    "h": 1.0,
    # Area → m2  ("ft2" = the international foot squared, 0.3048^2 exactly,
    # consistent with "ft" below; added for USLCI v1.2026-06.0)
    "m2": 1.0,   "ft2": 0.09290304,
    # Area × time → m2*a (land use). "ha*a" is hectare-years: 1 ha = 1e4 m2
    # exactly. Added for v1.2026-06.0's grazing/land-use exchanges.
    "m2*a": 1.0, "ha*a": 1e4,
    # Volume × time → m3*a (water use; already reference unit)
    "m3*a": 1.0,
    # Length → m
    "m": 1.0,    "ft": 0.3048,
    # Radioactivity → kBq
    "kbq": 1.0,  "bq": 1e-3,
    # Dimensionless / count
    "unit": 1.0, "item(s)": 1.0, "p": 1.0, "items": 1.0,
    # Currency — pass through at face value (no physical conversion)
    "usd": 1.0,  "$": 1.0,  "us$": 1.0,
}

# Case-SENSITIVE entries, checked before the lowercase fallback: units whose
# meaning changes with case. "Mg" (megagram = tonne, used by the recycling/MRF
# sector) would otherwise collide with "mg" (milligram) in the case-insensitive
# lookup — a silent 1e9 error. A census of the full USLCI unit universe
# (30 strings) found Mg/mg to be the only case collision.
_WITHIN_FP_EXACT = {"Mg": 1e3}

# Case-insensitive lookup built from WITHIN_FP. All keys are lowercased at
# definition time so lookup just needs unit.lower(). Explicit lowercase entries
# in WITHIN_FP (e.g. "mmbtu") are already correct.
_WITHIN_FP_LOWER = {k.lower(): v for k, v in WITHIN_FP.items()}

LOCATION_MAP = {
    "United States of America (the)": "US",
    "Northern America":               "RNA",
    "Global":                         "GLO",
    "Europe":                         "RER",
}


# =============================================================================
# UNIT NORMALISATION
# =============================================================================
class UnitNormalizer:
    """Convert an exchange amount to its flow's reference flow-property ref unit.

    Stateful on purpose: unrecognized unit strings are accumulated as they are met
    (mapped to the flow UUIDs seen carrying them, so the hard-stop message can say
    WHERE), and checked once before the database is written. Passing an unknown unit
    through at face value is a silent wrong number — "a wrong number wearing a
    plausible one's clothes" (ledger #7) — so the build refuses by default.

    Callable, because that is the interface `allocation` expects.
    """

    def __init__(self, flow_conv):
        self.flow_conv = flow_conv
        self.unknown: dict = {}   # unit string -> set of flow UUIDs

    def __call__(self, amount: float, unit: str, fp_uuid: str, flow_uuid: str,
                 cross_property: bool = True):
        """
        Convert exchange amount to the flow's reference flow property reference unit.

        Two-step process:
          1. Within-property: unit -> fp ref unit  (e.g. l -> m3, kg stays kg)
          2. Cross-property:  fp ref unit -> reference fp ref unit
                              (e.g. btu of diesel -> m3 using energy density)

        cross_property must be False for biosphere (elementary) flows — they are
        always expressed in their natural unit (kg, kBq, etc.) and must never be
        converted between flow properties via the conversion table.

        Returns (normalized_amount, ref_unit_str).
        Falls back to (amount, unit) when data is missing so import never crashes.
        """
        within = _WITHIN_FP_EXACT.get(unit) if unit else None
        if within is None:
            within = _WITHIN_FP_LOWER.get(unit.lower() if unit else "")
        if within is None:
            self.unknown.setdefault(unit, set()).add(flow_uuid)
            return amount, unit   # unrecognised unit — pass through

        if not cross_property:
            return amount * within, unit  # step 1 only (biosphere path)

        flow = self.flow_conv.get(flow_uuid)
        if flow is None:
            return amount * within, unit  # step 1 only; flow not in table

        ref_fp_uuid = flow["ref_fp_uuid"]
        ref_unit    = flow["ref_unit"]

        amount_fp_ref = amount * within       # now in fp's own ref unit

        if fp_uuid == ref_fp_uuid:
            return amount_fp_ref, ref_unit    # already the reference fp

        conv = flow["conversions"].get(fp_uuid)
        if conv is None:
            return amount_fp_ref, ref_unit    # fp not in table — step 1 only

        # factor = (this fp ref unit) per (1 reference fp ref unit)
        # => reference fp amount = this fp amount / factor
        return amount_fp_ref / conv["factor"], ref_unit

    def unknown_detail(self, all_flows) -> str:
        """One line per unknown unit, with up to 3 example flows so the operator can
        see WHERE the unit occurs and judge whether it touches their target."""
        lines = []
        for u in sorted(self.unknown):
            flows = sorted(self.unknown[u])
            examples = ", ".join(
                f"'{all_flows.get(f, {}).get('name', '?')}' ({f})" for f in flows[:3]
            )
            more = f" (+{len(flows) - 3} more flows)" if len(flows) > 3 else ""
            lines.append(f'    "{u}" — e.g. {examples}{more}')
        return "\n".join(lines)


# =============================================================================
# SOURCES
# =============================================================================
def file_sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_conversion_table(bundle_dir, log=_NOOP) -> dict:
    """The flow-conversion table from setup/00, verified against its source zip.

    Maps flow UUID -> reference flow property + cross-property conversion factors.
    The zip check only runs when that zip is actually present: the full USLCI zip is
    a build-time source that is NOT tracked in git, so for most users it is absent
    here and the committed table is trusted as-is. When a rebuilder does have it in
    source_data/, compare by content hash — mtime is machine-specific and would
    false-alarm on every git checkout.
    """
    path = Path(CONV_TABLE_PATH)
    if not path.exists():
        raise RuntimeError(
            "uslci_flow_conversions.json not found. "
            "Run setup/00_build_flow_conversion_table.py first."
        )
    flow_conv = json.loads(path.read_text())

    meta = flow_conv.get("_meta", {})
    if meta:
        expected_zip = Path(bundle_dir) / meta.get("source_zip", "")
        expected_sha = meta.get("source_zip_sha256")
        if expected_zip.exists() and expected_sha:
            if file_sha256(expected_zip) != expected_sha:
                raise RuntimeError(
                    f"uslci_flow_conversions.json was built from a different version of "
                    f"{meta['source_zip']} than the one now in {bundle_dir}.\n"
                    f"  Table built at : {meta.get('generated_at', '?')}\n"
                    f"  Fix: rerun setup/00_build_flow_conversion_table.py --rebuild."
                )
    else:
        log("WARNING: uslci_flow_conversions.json has no _meta block — cannot verify zip version match.")
    return flow_conv


def discover_sources(*, bundle_dir, full_db, conv_meta, db_name, log=_NOOP) -> list:
    """Which zips this build reads.

    Default: the per-process bundle zips (`<uuid>_<hash>.zip`) in `bundle_dir`.
    In full-DB mode: the single full USLCI zip named in the conversion table's
    `_meta`. Everything downstream is unchanged — the parser builds an activity for
    every process it loads, with no target scoping.
    """
    bundle_dir = Path(bundle_dir)
    full_db_zip = conv_meta.get("source_zip", "")

    if full_db:
        full = bundle_dir / full_db_zip
        if not full_db_zip or not full.exists():
            raise RuntimeError(
                f"USLCI_FULL_DB is set but the full USLCI zip is not present in {bundle_dir} "
                f"(expected '{full_db_zip or '<name recorded in the conversion table _meta>'}')."
            )
        log(f"FULL-DB MODE: importing the entire USLCI database from {full.name}")
        log(f"  target database: '{db_name}' "
            f"(the bundle build '{USLCI_BUNDLE_DB_NAME}' is left untouched)")
        return [full]

    zip_files = sorted(bundle_dir.glob(BUNDLE_GLOB), key=lambda p: p.name)

    # Guard against silently ignored bundles: a zip that LOOKS like a JSON-LD process
    # export (has openlca.json + processes/) but whose filename doesn't match the
    # required <uuid>_<hash>.zip pattern would otherwise vanish without a trace — the
    # operator thinks their process imported when it didn't. Renamed downloads are the
    # usual cause; keep the original LCA Commons filename. The full-DB zip (named in
    # the conversion table's _meta) is a build-time source, not a bundle — excluded.
    for zp in sorted(bundle_dir.glob("*.zip")):
        if zp in zip_files or zp.name == full_db_zip:
            continue
        try:
            with zipfile.ZipFile(zp) as z:
                names = z.namelist()
                if "openlca.json" in names and any(n.startswith("processes/") for n in names):
                    log(
                        f"WARNING: {zp.name} looks like a process bundle but does NOT match the "
                        f"required '<uuid>_<hash>.zip' naming pattern — it will be IGNORED.\n"
                        f"  Restore the original LCA Commons filename (the process UUID + export hash) "
                        f"if you want it imported."
                    )
        except zipfile.BadZipFile:
            pass

    if not zip_files:
        raise RuntimeError(
            f"No process zip files found in {bundle_dir}. Download per-process exports "
            f"from LCA Commons and place them there (same dir 03b scans). Bundle zips "
            f"must keep their original '<uuid>_<hash>.zip' filenames (see WARNINGs above, "
            f"if any, for zips that were skipped on naming grounds)."
        )
    return zip_files


def source_identity(zip_files, full_db) -> dict:
    """Content identity of the sources this build was made from.

    Stamped onto the database after the write. Bundle filenames additionally carry
    the USLCI release hash as their `_<hash>` suffix; it is recorded as-is, never
    translated into a version name, because the same suffix was observed on bundles
    whose process versions disagree.
    """
    identity = {
        "mode": "full_db" if full_db else "bundles",
        "zips": [{"name": z.name, "sha256": file_sha256(z)} for z in zip_files],
    }
    if not full_db:
        hashes = sorted({z.stem.rsplit("_", 1)[-1] for z in zip_files
                         if "_" in z.stem and len(z.stem.rsplit("_", 1)[-1]) == 40})
        if hashes:
            identity["bundle_release_hashes"] = hashes
    return identity


def _parse_version(v):
    """'00.01.014' -> (0, 1, 14); non-numeric parts -> 0; missing -> () (lowest)."""
    if not v:
        return ()
    parts = []
    for part in str(v).split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _proc_precedence(data):
    """Sort key for choosing between two copies of the same process UUID. Higher =
    newer: primarily the dataset version, then the lastChange timestamp (ISO-8601
    Zulu strings sort chronologically)."""
    return (_parse_version(data.get("version")), data.get("lastChange") or "")


def load_sources(zip_files, log=_NOOP):
    """Parse the zips into (processes, flows, uuids_resolved_by_version).

    When the same process UUID appears in multiple exports (e.g. a corrected
    re-download), precedence is decided by the process's OWN embedded dataset
    version + lastChange timestamp -- NOT by file modification time (ledger #5).
    mtime does not survive copies/clones, so two users with identical bundles could
    otherwise build different databases; version/lastChange live inside the JSON and
    are identical on every machine. Zips are iterated in a deterministic filename
    order purely so the "equal version" tie-break is reproducible too.
    """
    all_processes = {}  # proc_uuid -> process dict
    proc_keys     = {}  # proc_uuid -> precedence key of the copy currently kept
    all_flows     = {}  # flow_uuid -> flow dict
    version_resolved = set()  # UUIDs where copies differed in version/lastChange

    for zpath in zip_files:
        with zipfile.ZipFile(zpath) as z:
            for name in z.namelist():
                if name.startswith("processes/") and name.endswith(".json"):
                    data = json.loads(z.read(name))
                    uid = data.get("@id")
                    if not uid:
                        continue
                    key = _proc_precedence(data)
                    if uid in all_processes:
                        if key == proc_keys[uid]:
                            continue  # identical version — benign duplicate, keep first-seen
                        version_resolved.add(uid)
                        if key < proc_keys[uid]:
                            continue  # incoming copy is older — keep the newer incumbent
                    all_processes[uid] = data
                    proc_keys[uid]     = key
                elif name.startswith("flows/") and name.endswith(".json"):
                    data = json.loads(z.read(name))
                    uid = data.get("@id")
                    if uid:
                        all_flows[uid] = data

    log(f"  {len(all_processes)} unique processes, {len(all_flows)} unique flows.")
    if version_resolved:
        log(f"  NOTE: {len(version_resolved)} process UUID(s) appeared in multiple zips with "
            f"differing versions — kept the highest dataset version / lastChange "
            f"(deterministic across machines).")
    return all_processes, all_flows, version_resolved


# =============================================================================
# LINKING
# =============================================================================
def index_reference_flows(all_processes) -> dict:
    """Each reference flow UUID -> the set of process UUIDs that supply it.

    A SET, not a single value, because multiple bundle-internal processes can
    legitimately share a reference flow (e.g. 4 regional crude-oil sourcing
    variants -- on/off-shore, domestic/import -- all supply generic "Crude oil";
    6 natural-gas extraction methods all supply generic "Natural gas"; several
    regional transport processes all supply the same generic transport flow).
    Confirmed across the 4 test-case bundles: 7 such flows, 69 consuming
    exchanges total, and every one of those 69 carries a `defaultProvider` hint
    that correctly names one of the actual candidate producers -- so the data
    is fully disambiguated and resolution must use that hint, not a bare
    flow-UUID lookup (see ProviderResolver).

    The reference exchange is usually an output (a normal product), but USLCI
    also has waste-treatment "sink" processes (landfilling, wastewater
    treatment, combustion, effluent release) whose function is defined by the
    waste they consume -- there isQuantitativeReference=true legitimately lands
    on an INPUT exchange (confirmed: every process in the current 4-bundle set
    has exactly one reference exchange, on one side or the other, never both/
    neither). Either way, that flow is what other processes link to via this
    map, so direction doesn't matter here -- only that isQuantitativeReference
    uniquely identifies it.
    """
    flow_to_process = {}
    for proc_uuid, proc in all_processes.items():
        ref_flows = [
            exc.get("flow", {}).get("@id")
            for exc in proc.get("exchanges", [])
            if exc.get("isQuantitativeReference") and exc.get("flow", {}).get("@id")
        ]
        if len(ref_flows) > 1:
            raise RuntimeError(
                f"Process {proc_uuid} ({proc.get('name', '?')}) has {len(ref_flows)} "
                f"quantitative reference exchanges — expected exactly 1. "
                f"This indicates a malformed USLCI JSON-LD export."
            )
        for flow_uuid in ref_flows:
            flow_to_process.setdefault(flow_uuid, set()).add(proc_uuid)
    return flow_to_process


@dataclass
class ExternalProviders:
    """Aggregated background activities injected by setup/03b, if any.

    Not electricity-specific: any bundle exchange whose named provider lives
    outside the bundle resolves against this if a matching injected database is
    present. `vintage` is the electricity-baseline vintage 03b stamped (e.g.
    "2025"), copied onto the USLCI database so the validation harness can assert a
    case is diffed against the matching-vintage grid. None if 03b predates the stamp.
    """
    uuids: set = field(default_factory=set)
    flow_to_process: dict = field(default_factory=dict)
    names: dict = field(default_factory=dict)
    vintage: str | None = None


def load_external_providers(log=_NOOP) -> ExternalProviders:
    external = ExternalProviders()
    if EXTERNAL_PROVIDER_DB not in bd.databases:
        return external

    external.vintage = bd.databases[EXTERNAL_PROVIDER_DB].get("electricity_vintage")
    for act in bd.Database(EXTERNAL_PROVIDER_DB):
        external.uuids.add(act["code"])
        external.names[act["code"]] = act["name"]
        ref_flow_uuid = act.get("reference_product_flow_uuid")
        if ref_flow_uuid:
            external.flow_to_process.setdefault(ref_flow_uuid, set()).add(act["code"])
    log(f"Loaded {len(external.uuids)} external provider process(es) "
        f"({len(external.flow_to_process)} distinct reference flow(s)) "
        f"from '{EXTERNAL_PROVIDER_DB}'"
        + (f" [electricity_vintage = {external.vintage}]."
           if external.vintage else "."))
    return external


class ProviderResolver:
    """Resolve a technosphere exchange to the activity that supplies it."""

    def __init__(self, db_name, all_processes, flow_to_process, external):
        self.db_name = db_name
        self.all_processes = all_processes
        self.flow_to_process = flow_to_process
        self.external = external

    def candidates(self, flow_uuid) -> set:
        """Every (database, process UUID) that declares this flow as its reference."""
        return ({(self.db_name, u) for u in self.flow_to_process.get(flow_uuid, set())} |
                {(EXTERNAL_PROVIDER_DB, u)
                 for u in self.external.flow_to_process.get(flow_uuid, set())})

    def _candidate_name(self, db_name, proc_uuid):
        """Name of a resolution candidate, for hint-name disambiguation."""
        if db_name == EXTERNAL_PROVIDER_DB:
            return self.external.names.get(proc_uuid)
        return (self.all_processes.get(proc_uuid) or {}).get("name")

    def resolve(self, exc, flow_uuid):
        """Resolve a technosphere exchange to (db_name, proc_uuid), or (None, None)
        if unresolved. Always prefers the exchange's own `defaultProvider` UUID
        hint -- bundle-internal or external -- since that's the only thing that
        disambiguates when several processes share a reference flow. Falls back
        to flow-based resolution only when the hint is absent or doesn't match
        anything we have, and only when the flow maps to exactly one candidate
        across bundle + external combined; a flow with several candidates and no
        usable hint is a genuine ambiguity, left unlinked rather than guessed.
        Returns a third element: True if the fallback path hit a genuine ambiguity.
        """
        provider = exc.get("defaultProvider") or {}
        hinted_uuid = provider.get("@id")
        if hinted_uuid in self.all_processes:
            return self.db_name, hinted_uuid, False
        if hinted_uuid in self.external.uuids:
            return EXTERNAL_PROVIDER_DB, hinted_uuid, False

        candidates = self.candidates(flow_uuid)
        if len(candidates) == 1:
            target_db, target_proc = next(iter(candidates))
            return target_db, target_proc, False

        if len(candidates) > 1:
            # Several producers share this flow and the hint UUID didn't resolve
            # (e.g. the electricity baseline: one flow "Electricity, AC, 120 V" is
            # produced by every regional consumption mix, and the exchange hints a
            # provider UUID that was renamed across library versions). The hint
            # still carries the intended provider's NAME, though -- if exactly one
            # candidate's name matches it, that's an unambiguous resolution, not a
            # guess. Mirrors the openLCA operator selecting the named mix by hand.
            hinted_name = provider.get("name")
            if hinted_name:
                by_name = [c for c in candidates if self._candidate_name(*c) == hinted_name]
                if len(by_name) == 1:
                    return (*by_name[0], False)
            return None, None, True

        return None, None, False


# =============================================================================
# ALLOCATION PRE-PASS
# =============================================================================
@dataclass
class AllocationPlan:
    """Allocation factors and co-product re-basis, computed for every process.

    Built before the activities because a consumer can be built before the
    co-product supplier it points at is visited.
    """
    alloc: dict = field(default_factory=dict)          # proc_uuid -> scalar alloc factor
    causal: dict = field(default_factory=dict)         # proc_uuid -> {internalId: factor}
    multiplier: dict = field(default_factory=dict)     # (supplier, out_flow) -> m
    coproduct_info: dict = field(default_factory=dict)  # (proc, co_flow) -> {column, fallback, yield}
    coproduct_key: dict = field(default_factory=dict)   # (proc, co_flow) -> (db, code)
    skipped_multipliers: list = field(default_factory=list)
    n_native: int = 0
    n_mass: int = 0
    n_causal: int = 0
    examples: list = field(default_factory=list)

    @property
    def n_multi(self) -> int:
        return self.n_native + self.n_mass + self.n_causal


def plan_allocation(all_processes, db_name, normalize, flow_conv) -> AllocationPlan:
    """Allocation factors + co-product re-basis multipliers, for all processes.

    For each multi-output process this caches the reference product's allocation
    factor (what the single built activity's inputs are scaled by) and, per
    NON-reference output flow, a multiplier that converts a request expressed in the
    co-product's own units into the equivalent amount of the reference product
    delivering the co-product's correctly-allocated burden share:

      m(P, flow) = (ref_yield * target_alloc) / (target_yield * ref_alloc)

    The reference product and single-output processes get no entry (implicit 1.0).
    For pure mass allocation this reduces to a units/density conversion (the
    reference product's declared yield can be in L/m3 while its allocation factor
    is mass-based); for economic/causal allocation it does real burden re-
    attribution. Both fall out of the same formula.

    Causal co-products can't be scalar-re-based (their burden is per-exchange), so
    each gets its OWN activity, built from its own column of the causal factor grid.
    Consumers are redirected to that activity at the link site — no multiplier.
    """
    plan = AllocationPlan()

    for puuid, proc in all_processes.items():
        alloc_factor, ref_flow, per_output, method, causal = allocation_for(
            proc, puuid, normalize=normalize, flow_conv=flow_conv)
        plan.alloc[puuid] = alloc_factor
        plan.causal[puuid] = causal
        if method == "native":
            plan.n_native += 1
            if len(plan.examples) < MAX_EXAMPLES:
                plan.examples.append(f"{proc.get('name', puuid)} ({method}, ref α={alloc_factor:.4g})")
        elif method == "mass":
            plan.n_mass += 1
            if len(plan.examples) < MAX_EXAMPLES:
                plan.examples.append(f"{proc.get('name', puuid)} ({method}, ref α={alloc_factor:.4g})")
        elif method == "causal":
            plan.n_causal += 1
            if len(plan.examples) < MAX_EXAMPLES:
                plan.examples.append(f"{proc.get('name', puuid)} (causal, {len(causal)} per-exchange factors)")
            # Each causal co-product gets a dedicated activity built from its own
            # factor column. Key it now (pre-pass) so consumers built before the
            # supplier can already link to it.
            for fuuid, info in causal_coproducts(proc, puuid, normalize, flow_conv).items():
                plan.coproduct_info[(puuid, fuuid)] = info
                plan.coproduct_key[(puuid, fuuid)] = (db_name, f"{puuid}__co__{fuuid}")
        # Co-product multipliers: only for scalar-allocated processes (native/mass).
        # single-output and causal have empty per_output -> no multipliers.
        if method in ("single", "causal") or ref_flow is None:
            continue
        mults, skipped = coproduct_multipliers(per_output, ref_flow)
        for flow_uuid, m in mults.items():
            plan.multiplier[(puuid, flow_uuid)] = m
        plan.skipped_multipliers.extend((puuid, f) for f in skipped)

    return plan


# =============================================================================
# BUILD
# =============================================================================
@dataclass
class BuildTally:
    """Match/link outcomes across the whole build, plus a few worked examples."""
    bio_matched: int = 0
    bio_unmatched: int = 0
    tech_linked: int = 0
    tech_external: int = 0
    tech_unlinked: int = 0
    tech_ambiguous: int = 0
    causal_fallback: int = 0            # causal exchanges that fell back to the scalar
    causal_coproduct_links: int = 0     # links redirected to a causal co-product activity
    waste_treatment_links: int = 0      # non-reference WASTE_FLOW outputs sent to treatment
    avoided_product_links: int = 0      # exchanges flagged isAvoidedProduct — credited
    ambiguous_examples: list = field(default_factory=list)
    causal_coproduct_examples: list = field(default_factory=list)
    waste_treatment_examples: list = field(default_factory=list)
    avoided_product_examples: list = field(default_factory=list)
    causal_coproduct_consumed: set = field(default_factory=set)

    def totals(self) -> dict:
        """The six counts recorded in the provenance sidecar."""
        return {
            "bio_matched":    self.bio_matched,   "bio_unmatched":  self.bio_unmatched,
            "tech_linked":    self.tech_linked,   "tech_external":  self.tech_external,
            "tech_unlinked":  self.tech_unlinked, "tech_ambiguous": self.tech_ambiguous,
        }


@dataclass
class BuildContext:
    """Everything the per-activity build reads. Assembled once by import_uslci."""
    db_name: str
    all_processes: dict
    all_flows: dict
    bio_uuids: set
    normalize: UnitNormalizer
    resolver: ProviderResolver
    plan: AllocationPlan
    external_names: dict


def _new_pdiag() -> dict:
    return {"bio_matched": 0, "bio_unmatched": 0, "tech_linked": 0,
            "tech_external": 0, "tech_unlinked": 0, "tech_ambiguous": 0}


def build_jobs(all_processes, plan) -> list:
    """Every process's reference activity (co_flow None), plus one extra activity per
    causal co-product (co_flow set), built from the same JSON process but with the
    co-product's own per-exchange factor column and its own yield as the production
    exchange. Sorted so the order is deterministic."""
    jobs = [(puuid, None) for puuid in all_processes]
    jobs += sorted(plan.coproduct_info)
    return jobs


def activity_location(proc) -> str:
    """The process's location as a brightway-safe code.

    brightway's geomapping eval()s any location string containing "(" (it treats it
    as a possibly re-tupleized regionalization key — see bw2data
    retupleize_geo_strings), so an unmapped country name like "Congo (the Democratic
    Republic of the)" crashes .write() with a SyntaxError. LOCATION_MAP already
    normalizes the common cases (e.g. the USA name) to codes; for anything it didn't,
    drop the parenthetical qualifier so the raw name is eval-safe. General on purpose
    — handles any future paren-bearing country.
    """
    location = proc.get("location", {})
    raw_loc = location.get("name", "") if isinstance(location, dict) else ""
    location_name = LOCATION_MAP.get(raw_loc, raw_loc) or "GLO"
    if "(" in location_name:
        location_name = location_name.split(" (")[0].strip() or "GLO"
    return location_name


def _technosphere_exchange(ctx, tally, pdiag, *, proc, proc_uuid, exc, flow_ref,
                           flow_uuid, is_input, amount, unit, fp_uuid):
    """One technosphere link, or None if it resolved to nothing.

    Two directions resolve the same way:
      - a consumed INPUT (product or waste feedstock), and
      - a WASTE_FLOW OUTPUT sent to a treatment provider. openLCA models disposal as
        the generator OUTPUTTING a waste flow whose defaultProvider is the treatment
        process (whose own reference is that waste flow as an input). The waste
        amount is a positive consumption of the treatment service, so it links
        exactly like an input. Without this, every waste-to-treatment output was
        silently dropped and the treatment burden (e.g. landfill methane) was omitted
        entirely.

    NON-reference PRODUCT_FLOW outputs are co-products, handled by allocation /
    the co-product multiplier — deliberately NOT linked here.
    """
    norm_amount, norm_unit = ctx.normalize(amount, unit, fp_uuid, flow_uuid)

    # Avoided products are CREDITS, not burdens. USLCI marks byproduct
    # energy/material recovery this way (isInput=true + isAvoidedProduct
    # =true): e.g. MSW landfilling / combustion recovering landfill-gas
    # electricity, which displaces grid power. openLCA subtracts these;
    # a brightway technosphere input with a positive stored amount is a
    # positive consumption (burden), so flip the sign to turn the
    # avoided consumption into the credit openLCA computes. Without this,
    # the landfill-gas electricity credit was imported as a burden and
    # sign-flipped petroleum's grid-dominated toxicity result.
    if exc.get("isAvoidedProduct", False):
        norm_amount = -norm_amount
        tally.avoided_product_links += 1
        if len(tally.avoided_product_examples) < MAX_EXAMPLES:
            tally.avoided_product_examples.append(
                f"{proc.get('name', proc_uuid)} avoids "
                f"{flow_ref.get('name', flow_uuid)} ({norm_amount:.4g} {norm_unit})")

    target_db, target_proc, was_ambiguous = ctx.resolver.resolve(exc, flow_uuid)
    if was_ambiguous:
        # Several candidate producers (bundle-internal and/or external) supply this
        # flow, and the exchange's own defaultProvider hint didn't match any of them
        # -- non-fatal: leave unlinked/cutoff rather than guess.
        tally.tech_ambiguous += 1
        pdiag["tech_ambiguous"] += 1
        hinted_uuid = (exc.get("defaultProvider") or {}).get("@id")
        candidates = ctx.resolver.candidates(flow_uuid)
        tally.ambiguous_examples.append(
            f"{proc.get('name', proc_uuid)}: flow {flow_uuid} "
            f"(hint {hinted_uuid!r} unresolved; {len(candidates)} candidates: {sorted(candidates)})"
        )

    if not target_proc:
        tally.tech_unlinked += 1
        pdiag["tech_unlinked"] += 1
        return None

    co_key = ctx.plan.coproduct_key.get((target_proc, flow_uuid))
    if co_key is not None:
        # This exchange draws a CAUSAL process's non-reference co-product. Its burden
        # is per-exchange, so no scalar multiplier can re-base it through the
        # reference activity — link instead to the co-product's dedicated activity
        # (built from its own factor column), which is natively in the co-product's
        # own units. No multiplier.
        tally.causal_coproduct_consumed.add((target_proc, flow_uuid))
        tally.causal_coproduct_links += 1
        if len(tally.causal_coproduct_examples) < MAX_EXAMPLES:
            tally.causal_coproduct_examples.append(
                f"{proc.get('name', proc_uuid)} draws causal co-product flow {flow_uuid}")
        exchange = {"input": co_key, "amount": norm_amount, "unit": norm_unit,
                    "type": "technosphere"}
    else:
        # Re-basis scalar co-product draws. When this exchange targets a NON-reference
        # output of a multi-output supplier, the supplier's single brightway activity
        # is built on its reference product's yield+allocation; convert the request
        # into the equivalent reference-product amount that carries this co-product's
        # own allocated burden. 1.0 (no-op) for reference products, single-output
        # suppliers, and external (aggregated) providers.
        m = ctx.plan.multiplier.get((target_proc, flow_uuid), 1.0)
        exchange = {"input": (target_db, target_proc), "amount": norm_amount * m,
                    "unit": norm_unit, "type": "technosphere"}

    tally.tech_linked += 1
    pdiag["tech_linked"] += 1
    if target_db == EXTERNAL_PROVIDER_DB:
        tally.tech_external += 1
        pdiag["tech_external"] += 1
    if not is_input:  # a waste-treatment output link
        tally.waste_treatment_links += 1
        if len(tally.waste_treatment_examples) < MAX_EXAMPLES:
            tally.waste_treatment_examples.append(
                f"{proc.get('name', proc_uuid)} sends {flow_ref.get('name', flow_uuid)} "
                f"to {ctx.external_names.get(target_proc) or (ctx.all_processes.get(target_proc) or {}).get('name', target_proc)}")
    return exchange


def build_activity(ctx, tally, proc_uuid, co_flow):
    """One brightway activity. Returns (key, activity_dict, per-process diagnostics).

    `co_flow` None builds the process's reference activity; set, it builds the
    dedicated activity for that causal co-product.
    """
    proc = ctx.all_processes[proc_uuid]
    exchanges = []
    ref_unit = "unit"
    pdiag = _new_pdiag()

    # Allocation factor(s), computed once in the pre-pass. For native/mass
    # allocation this is one scalar applied to every input/biosphere exchange;
    # for causal allocation each exchange has its own factor (keyed by
    # internalId), with alloc_factor as the fallback. Scalar co-product
    # consumers are re-based separately, at their link site, via the
    # multiplier. 1.0 for single-output. A causal co-product job uses
    # the CO-PRODUCT's own factor column and mass-fraction fallback.
    if co_flow is None:
        key = (ctx.db_name, proc_uuid)
        alloc_factor   = ctx.plan.alloc[proc_uuid]
        causal_factors = ctx.plan.causal.get(proc_uuid)
    else:
        key = ctx.plan.coproduct_key[(proc_uuid, co_flow)]
        alloc_factor   = ctx.plan.coproduct_info[(proc_uuid, co_flow)]["fallback"]
        causal_factors = ctx.plan.coproduct_info[(proc_uuid, co_flow)]["column"]

    for exc in proc.get("exchanges", []):
        flow_ref  = exc.get("flow", {})
        flow_uuid = flow_ref.get("@id")
        flow_type = flow_ref.get("flowType", "")
        is_input  = exc.get("isInput", False)
        # Production exchange: the quantitative reference for a normal job, the
        # co-product's own output exchange for a causal co-product job (there
        # the actual reference product falls through to the else branch and is
        # dropped like any other non-reference product output).
        if co_flow is None:
            is_ref = exc.get("isQuantitativeReference", False)
        else:
            is_ref = (not is_input and flow_uuid == co_flow
                      and flow_type == "PRODUCT_FLOW")

        if flow_uuid is None:
            raise RuntimeError(
                f"Malformed exchange in process '{proc.get('name', proc_uuid)}' "
                f"(proc UUID: {proc_uuid}): exchange has no flow @id.\n"
                f"  Exchange: {exc}"
            )
        amount    = exc.get("amount", 0.0)
        unit      = exc.get("unit", {}).get("name", "")
        fp_uuid   = exc.get("flowProperty", {}).get("@id", "")

        if is_ref:
            # Production exchange: never scaled by allocation factor. Usually
            # an output (a normal product), but USLCI waste-treatment "sink"
            # processes (landfilling, wastewater treatment, combustion,
            # effluent release) define their function by the waste they
            # consume, so isQuantitativeReference legitimately lands on an
            # input exchange there. Direction doesn't matter for building the
            # activity's own production exchange -- index_reference_flows above
            # already resolves consumers to this process either way.
            norm_amount, norm_unit = ctx.normalize(amount, unit, fp_uuid, flow_uuid)
            ref_unit = norm_unit
            exchanges.append({
                "input":  key,
                "amount": norm_amount,
                "unit":   norm_unit,
                "type":   "production",
            })
            continue

        # Apply the allocation factor for multi-output processes. Native/mass
        # use one scalar; causal uses this exchange's own factor (by
        # internalId), falling back to the scalar only if an added exchange
        # unexpectedly lacks one. Single-output: alloc_factor = 1.0.
        if causal_factors is not None:
            factor = causal_factors.get(exc.get("internalId"))
            if factor is None:
                factor = alloc_factor
                tally.causal_fallback += 1
        else:
            factor = alloc_factor
        amount = amount * factor

        if flow_type == "ELEMENTARY_FLOW":
            # Biosphere: only within-property unit conversion (step 1).
            # Never cross-property — CO2 stays in kg, kBq stays in kBq, etc.
            bio_amount, bio_unit = ctx.normalize(
                amount, unit, fp_uuid, flow_uuid, cross_property=False
            )
            if flow_uuid in ctx.bio_uuids:
                exchanges.append({
                    "input":  (BIOSPHERE_DB, flow_uuid),
                    "amount": bio_amount,
                    "unit":   bio_unit,
                    "type":   "biosphere",
                })
                tally.bio_matched += 1
                pdiag["bio_matched"] += 1
            else:
                tally.bio_unmatched += 1
                pdiag["bio_unmatched"] += 1

        elif (is_input and flow_type in ("PRODUCT_FLOW", "WASTE_FLOW")) \
                or (not is_input and flow_type == "WASTE_FLOW"):
            exchange = _technosphere_exchange(
                ctx, tally, pdiag, proc=proc, proc_uuid=proc_uuid, exc=exc,
                flow_ref=flow_ref, flow_uuid=flow_uuid, is_input=is_input,
                amount=amount, unit=unit, fp_uuid=fp_uuid)
            if exchange is not None:
                exchanges.append(exchange)

    if not any(e["type"] == "production" for e in exchanges):
        raise RuntimeError(
            f"Process '{proc.get('name', proc_uuid)}' (proc UUID: {proc_uuid}) "
            f"has no production exchange. It cannot be used as a supplier in LCA. "
            f"Check that exactly one exchange has isQuantitativeReference=true and isInput=false."
        )

    if co_flow is None:
        act_name = proc.get("name", proc_uuid)
    else:
        co_flow_name = (ctx.all_flows.get(co_flow) or {}).get("name", co_flow)
        act_name = f"{proc.get('name', proc_uuid)} [causal co-product: {co_flow_name}]"

    activity = {
        "name":     act_name,
        "code":     key[1],
        "location": activity_location(proc),
        "unit":     ref_unit,
        "exchanges": exchanges,
    }
    return key, activity, pdiag


def build_activities(ctx):
    """Every build job -> (db_data, per-process provenance, tally).

    Per-process import diagnostics (RELEASE_PLAN 4.4 / ledger #9) are the same
    match/link outcomes tallied globally, kept PER process so general/04 can report
    completeness for a specific result's supply chain rather than the whole DB.
    Keyed by activity code so the manifest can look any solved activity up directly
    (main jobs: code == proc_uuid).
    """
    db_data = {}
    proc_provenance = {}
    tally = BuildTally()

    for proc_uuid, co_flow in build_jobs(ctx.all_processes, ctx.plan):
        key, activity, pdiag = build_activity(ctx, tally, proc_uuid, co_flow)
        db_data[key] = activity
        proc = ctx.all_processes[proc_uuid]
        proc_provenance[key[1]] = {
            "name":       activity["name"],
            "version":    proc.get("version"),
            "lastChange": proc.get("lastChange"),
            **({"coproduct_of": proc_uuid, "coproduct_flow": co_flow}
               if co_flow is not None else {}),
            **pdiag,
        }

    return db_data, proc_provenance, tally


# =============================================================================
# PRE-WRITE GUARDS
# =============================================================================
def check_consumed_causal_columns(plan, tally, db_name) -> None:
    """Hard-stop if a CONSUMED causal co-product has no factor column at all — its
    activity would then be built entirely on the mass-fraction fallback, i.e. a
    flattened scalar wearing per-exchange clothes. Unconsumed co-products may pass
    (their activities are inert); partial holes use the counted fallback, same as
    the reference-product side."""
    empty = [(p, f) for (p, f) in tally.causal_coproduct_consumed
             if not plan.coproduct_info[(p, f)]["column"]]
    if empty:
        raise RuntimeError(
            f"{len(empty)} consumed causal co-product(s) have NO per-exchange "
            f"factor column in their process JSON — build STOPPED before writing "
            f"'{db_name}'.\n"
            f"  Their activities would silently degrade to a flat mass-fraction split, "
            f"which is exactly the mis-allocation causal handling exists to prevent.\n"
            f"  Affected (process UUID, co-product flow UUID):\n"
            + "\n".join(f"    {p}  {f}" for p, f in empty)
        )


def check_unknown_units(normalize, all_flows, db_name, allow_passthrough) -> None:
    """Hard-stop on unrecognized units BEFORE writing (ledger #7). An unconverted unit
    means silently wrong exchange amounts; refuse to build a database that contains
    them unless the operator has explicitly opted into passthrough."""
    if not normalize.unknown or allow_passthrough:
        return
    raise RuntimeError(
        f"{len(normalize.unknown)} unrecognized unit string(s) encountered — build STOPPED before "
        f"writing '{db_name}'.\n"
        f"  These exchanges would be passed through WITHOUT unit conversion, i.e. silently wrong "
        f"amounts. Add each unit to WITHIN_FP (with its factor to the flow-property reference unit) "
        f"and rerun, or set the ALLOW_UNIT_PASSTHROUGH=1 environment variable to import anyway "
        f"with a warning (NOT recommended for study use):\n"
        + normalize.unknown_detail(all_flows)
    )


# =============================================================================
# PROVENANCE SIDECAR
# =============================================================================
def provenance_path(db_name) -> Path:
    """Where general/04's per-run audit manifest reads this build's diagnostics from.

    Filename mirrors run_manifest.db_provenance_filename(); kept as a local literal
    to avoid setup/ importing from general/. The bundle build keeps the historical
    unsuffixed name; the full-database build gets its own file so the two builds'
    diagnostics can coexist without either being read as if it described the other.
    """
    return REPO_ROOT / (
        "uslci_db_provenance.json" if db_name == USLCI_BUNDLE_DB_NAME
        else f"uslci_db_provenance.{db_name}.json"
    )


def _pkg_versions(names) -> dict:
    out = {}
    for n in names:
        try:
            out[n] = importlib.metadata.version(n)
        except importlib.metadata.PackageNotFoundError:
            out[n] = None
    return out


def write_db_provenance(*, db_name, project, activity_count, proc_provenance, tally,
                        source) -> tuple:
    """Persist per-process import diagnostics. Returns (path, document)."""
    document = {
        "schema":               "uslci-db-provenance/1",
        "generated":            datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "project":              project,
        "database":             db_name,
        "db_activity_count":    activity_count,
        "external_provider_db": EXTERNAL_PROVIDER_DB,
        "uslci_source":         source,
        "packages":             _pkg_versions(["bw2data", "bw2io", "bw2calc",
                                               "fedelemflowlist", "lciafmt"]),
        "totals":               tally.totals(),
        "processes":            proc_provenance,
    }
    path = provenance_path(db_name)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")
    return path, document


# =============================================================================
# REPORTING
# =============================================================================
def report_build(log, *, db_name, tally, plan, normalize, all_flows,
                 allow_unit_passthrough) -> None:
    """The console summary. Notes first, then each diagnostic that has anything to say."""
    if tally.bio_unmatched:
        log("\n  Note: unmatched biosphere flows are elementary flows whose UUIDs are not")
        log("  in biosphere-fedefl. They will be excluded from LCIA scoring.")
    if tally.tech_unlinked:
        log("\n  Note: unlinked technosphere exchanges reference processes not included")
        log("  in the downloaded zips. Add more process exports to extend coverage.")
    if tally.tech_ambiguous:
        log(f"\n  WARNING: {tally.tech_ambiguous} technosphere exchange(s) had an unresolvable "
            f"hinted provider AND multiple candidate providers in '{EXTERNAL_PROVIDER_DB}' -- "
            f"left unlinked rather than guessed. Examples:")
        for ex in tally.ambiguous_examples[:MAX_EXAMPLES]:
            log(f"    {ex}")
    if plan.n_multi:
        log(f"\n  Allocation: {plan.n_multi} multi-output process(es) "
            f"({plan.n_native} native factors, {plan.n_mass} mass fraction, {plan.n_causal} causal "
            f"per-exchange). {len(plan.multiplier)} co-product link(s) re-based off the "
            f"reference product's basis. Examples:")
        for ex in plan.examples[:MAX_EXAMPLES]:
            log(f"    {ex}")
    if tally.causal_fallback:
        log(f"\n  Note: {tally.causal_fallback} exchange(s) in causal-allocation process(es) lacked a "
            f"per-exchange factor and used the mass-fraction fallback.")
    if plan.coproduct_info:
        log(f"\n  Causal co-products: {len(plan.coproduct_info)} dedicated activit(ies) built "
            f"from per-exchange factor columns; {tally.causal_coproduct_links} consuming link(s) "
            f"redirected to them ({len(tally.causal_coproduct_consumed)} distinct co-product(s) consumed)."
            + ("" if not tally.causal_coproduct_examples else " Examples:"))
        for ex in tally.causal_coproduct_examples[:MAX_EXAMPLES]:
            log(f"    {ex}")
    if tally.waste_treatment_links:
        log(f"\n  Waste treatment: {tally.waste_treatment_links} WASTE_FLOW output(s) linked to a "
            f"treatment provider (disposal burden, e.g. landfill methane, now charged to the "
            f"generating process)." + ("" if not tally.waste_treatment_examples else " Examples:"))
        for ex in tally.waste_treatment_examples[:MAX_EXAMPLES]:
            log(f"    {ex}")
    if tally.avoided_product_links:
        log(f"\n  Avoided products: {tally.avoided_product_links} exchange(s) flagged "
            f"isAvoidedProduct credited (sign-flipped), matching openLCA (e.g. landfill-gas / "
            f"combustion electricity displacing grid power)." +
            ("" if not tally.avoided_product_examples else " Examples:"))
        for ex in tally.avoided_product_examples[:MAX_EXAMPLES]:
            log(f"    {ex}")
    if plan.skipped_multipliers:
        log(f"\n  WARNING: {len(plan.skipped_multipliers)} co-product output(s) could not be re-based "
            f"(missing yield or allocation factor) -- consumers of these fall back to the reference "
            f"product's basis (pre-fix behavior). Examples:")
        for puuid, fuuid in plan.skipped_multipliers[:MAX_EXAMPLES]:
            log(f"    process {puuid}, flow {fuuid}")
    if normalize.unknown:
        # Only reachable when passthrough is allowed (otherwise the build raised above).
        log(f"\n  WARNING: {len(normalize.unknown)} unrecognized unit string(s) encountered during import.")
        log(f"  ALLOW_UNIT_PASSTHROUGH is set, so these were passed through WITHOUT unit conversion")
        log(f"  — amounts may be silently wrong. Add the following to WITHIN_FP and rerun without the flag:")
        log(normalize.unknown_detail(all_flows))


# =============================================================================
# THE IMPORTER
# =============================================================================
def import_uslci(*, full_db=False, bundle_dir=None, allow_unit_passthrough=False,
                 overwrite=False, confirm=None, project=PROJECT_NAME, log=_NOOP):
    """Parse the USLCI sources into brightway. Returns a `UslciBuild`.

    `full_db=False` (the default) imports the per-process bundle zips into
    'uslci-subset' — the locked validation build. `full_db=True` imports the ENTIRE
    USLCI database from the single full zip into 'uslci-full', so general/04 can run
    any of its processes without a rebuild. The two are SEPARATE brightway databases
    and coexist; see the USLCI_FULL_DB note in config.py for why the harness must
    stay pinned to the bundle build.
    """
    db_name = USLCI_FULL_DB_NAME if full_db else USLCI_BUNDLE_DB_NAME

    bundle_dir = Path(bundle_dir) if bundle_dir else DEFAULT_BUNDLE_DIR
    if not bundle_dir.is_dir():
        raise RuntimeError(
            f"Bundle directory not found: {bundle_dir}\n"
            f"Place the USLCI bundle / openLCA library resources in {REPO_ROOT / 'source_data'}, "
            f"or set the SOURCE_DATA_DIR environment variable to your local checkout."
        )

    flow_conv = load_conversion_table(bundle_dir, log=log)
    normalize = UnitNormalizer(flow_conv)

    bd.projects.set_current(project)
    if not bd.projects.twofive:
        bd.projects.migrate_project_25()

    if BIOSPHERE_DB not in bd.databases:
        raise RuntimeError("Run setup/01_setup_biosphere_fedefl.py first.")

    zip_files = discover_sources(bundle_dir=bundle_dir, full_db=full_db,
                                 conv_meta=flow_conv.get("_meta", {}),
                                 db_name=db_name, log=log)
    log(f"Found {len(zip_files)} process zip(s).")

    source = source_identity(zip_files, full_db)
    log(f"  Source identity: {source['mode']}, {len(source['zips'])} zip(s)")

    all_processes, all_flows, _resolved = load_sources(zip_files, log=log)
    log()

    flow_to_process = index_reference_flows(all_processes)
    external = load_external_providers(log=log)

    plan = plan_allocation(all_processes, db_name, normalize, flow_conv)

    if db_name in bd.databases:
        # The CLI passes a confirm() that prompts; a scripted build passes
        # overwrite=True. A library call must never block on stdin.
        if not (overwrite or (confirm(db_name) if confirm else False)):
            raise UslciExists(
                f"Database '{db_name}' already exists. Pass overwrite=True "
                f"(or --yes on the CLI) to replace it.")
        del bd.databases[db_name]

    ctx = BuildContext(
        db_name=db_name, all_processes=all_processes, all_flows=all_flows,
        bio_uuids={act["code"] for act in bd.Database(BIOSPHERE_DB)},
        normalize=normalize,
        resolver=ProviderResolver(db_name, all_processes, flow_to_process, external),
        plan=plan, external_names=external.names,
    )
    db_data, proc_provenance, tally = build_activities(ctx)

    check_consumed_causal_columns(plan, tally, db_name)
    check_unknown_units(normalize, all_flows, db_name, allow_unit_passthrough)

    bd.Database(db_name).write(db_data)

    # Carry the electricity-baseline vintage stamp (from 03b) onto this database, so
    # the validation harness can assert each case is diffed against the grid vintage
    # its openLCA reference export used. A build is single-vintage by design.
    if external.vintage is not None:
        bd.databases[db_name]["electricity_vintage"] = external.vintage
        bd.databases.flush()

    # Stamp WHICH USLCI the build came from. Without this nothing at run time can
    # distinguish "that process doesn't exist" from "that process is in a newer
    # USLCI release than this build" -- the two look identical to a caller, and the
    # second is common because USLCI ships quarterly (a fishmeal process absent from
    # v1.2026-03.0 but present in v1.2026-06.0 is what prompted this).
    #
    # The identifier is the source zips' name + SHA256, deliberately NOT a release
    # version string: the full zip carries no intrinsic version field (its
    # openlca.json names only the electricity-library dependency), and the release
    # hash in bundle filenames proved unreliable as a content marker. A content hash
    # is the honest identity -- it says exactly which bytes produced this database
    # even when it cannot say what upstream calls them.
    bd.databases[db_name]["uslci_source"] = source
    bd.databases.flush()

    log(f"Database '{db_name}' written — {len(db_data)} processes"
        + (f" [electricity_vintage = {external.vintage}]." if external.vintage else "."))
    log(f"  Biosphere exchanges: {tally.bio_matched} matched, {tally.bio_unmatched} unmatched")
    log(f"  Technosphere exchanges: {tally.tech_linked} linked "
        f"({tally.tech_external} via '{EXTERNAL_PROVIDER_DB}'), {tally.tech_unlinked} unlinked")

    prov_path, _document = write_db_provenance(
        db_name=db_name, project=project, activity_count=len(db_data),
        proc_provenance=proc_provenance, tally=tally, source=source)
    log(f"  Provenance sidecar: {prov_path} ({len(proc_provenance)} processes)")

    report_build(log, db_name=db_name, tally=tally, plan=plan, normalize=normalize,
                 all_flows=all_flows, allow_unit_passthrough=allow_unit_passthrough)

    log(f"\nProcesses in '{db_name}':")
    for act in bd.Database(db_name):
        n_bio  = sum(1 for e in act.exchanges() if e["type"] == "biosphere")
        n_tech = sum(1 for e in act.exchanges() if e["type"] == "technosphere")
        log(f"  {act['name'][:70]}")
        log(f"    {n_bio} biosphere, {n_tech} technosphere exchanges")

    return UslciBuild(
        database=db_name,
        full_db=full_db,
        activity_count=len(db_data),
        process_count=len(proc_provenance),
        totals=tally.totals(),
        source=source,
        electricity_vintage=external.vintage,
        provenance_path=str(prov_path),
        notes=[],
    )
