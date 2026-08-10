# Design sketch — custom olca-schema JSON-LD import

Status: **built** on branch `custom-jsonld-import`. User-facing instructions are in
[`HOWTO.md`](HOWTO.md) §2b; this file is the design record and the reasoning behind it.

Three things changed from the sketch as it was implemented:

- **Zip only.** Directory sources were dropped — generated JSON arrives as a zip, so the extra
  entry-reader had no caller.
- **The conversion-table supplement moved up.** It is small enough (`setup_conversions.parse_flow`
  already does the parsing) that deferring it would have left a silent cross-property gap in the
  first release rather than a later one.
- **A guard was added that the sketch missed.** Rebuilding a background renumbers brightway's
  internal ids, and a dependent custom database then fails with bw2calc's
  `Technosphere matrix is not square` — which names neither the stale database nor the fix. Found
  by rebuilding `uslci-full` under a working `my-study` during verification. `background_stamps` +
  `_check_background_freshness` turn it into a sentence that says re-import.

Bring your own olca-schema JSON-LD — not a USLCI export — and run TRACI results on it with
background providers resolved against an already-built USLCI database.

The parser already does this. `load_sources`, `index_reference_flows`, `plan_allocation` and
`build_activity` are a generic openLCA reader; nothing in them mentions USLCI. What is
USLCI-specific is the four gates around them, and this is a plan for widening each one without
moving the validated path.

---

## Contract for the author

A source is a **zip or a directory** containing exactly two things the parser reads:

```
processes/*.json
flows/*.json
```

Everything else openLCA writes — `openlca.json`, `flow_properties/`, `unit_groups/`,
`categories/`, `actors/`, `sources/` — is ignored. A generated dataset only needs the two.

**Every background link carries `defaultProvider`.** An exchange whose flow is not produced by a
process inside the same dataset, and which has no `defaultProvider.@id`, is a build error — not a
silent cutoff. openLCA writes the field whenever a provider is picked in the GUI, so this costs a
hand-authored dataset one key per background exchange and buys the guarantee that a missing link
is loud. Override with `--allow-unhinted-links` when exploring, following the
`ALLOW_UNIT_PASSTHROUGH` precedent.

The hint is matched by UUID against the background databases. Flow-UUID fallback into a background
is deliberately not supported: `import_uslci` does not stamp `reference_product_flow_uuid` on its
activities, so there is nothing to fall back to, and requiring the hint means never guessing.

---

## 1. Sources — zip or directory

`load_sources` reads `zipfile.ZipFile` directly. Split the entry enumeration out so both work:

```python
def read_entries(source):
    """Yield (name, bytes) for every JSON entry in a zip OR a directory.

    Directory sources are how a generated dataset arrives — writing a zip just to
    read it back is friction with no payoff.
    """
```

`discover_sources` keeps its USLCI filename gates and stays on the USLCI path. The custom path
takes an explicit `source` and skips discovery entirely; there is nothing to discover when the
user names the file.

## 2. Backgrounds — one external DB becomes an ordered list

The core change. `ExternalProviders` was already written not to be electricity-specific; it is
only single-DB because `EXTERNAL_PROVIDER_DB` is a module constant.

```python
@dataclass
class ExternalProviders:
    """Already-built brightway databases this import resolves links against.

    Ordered: the first database declaring a UUID wins, so a caller can put a
    project-specific override ahead of uslci-full.
    """
    databases: list = field(default_factory=list)
    db_of: dict = field(default_factory=dict)    # proc uuid -> db name  (was: uuids, a set)
    names: dict = field(default_factory=dict)    # proc uuid -> name
    flow_to_process: dict = field(default_factory=dict)  # flow uuid -> {(db, proc), ...}
    vintage: str | None = None

    @property
    def uuids(self):          # existing call sites keep working
        return self.db_of.keys()


def load_external_providers(databases=None, log=_NOOP) -> ExternalProviders:
    """Defaults to [ELECTRICITY_BASELINE_DB] — the USLCI path is unchanged."""
```

`ProviderResolver.resolve` loses its one hardcoded name:

```python
-        if hinted_uuid in self.external.uuids:
-            return EXTERNAL_PROVIDER_DB, hinted_uuid, False
+        if hinted_uuid in self.external.db_of:
+            return self.external.db_of[hinted_uuid], hinted_uuid, False
```

`candidates()` already yields `(db, proc)` pairs; it just stops synthesizing the db name.
`_technosphere_exchange` tallies `tech_external` on `target_db in external.databases` rather than
`== EXTERNAL_PROVIDER_DB`.

**Vintage rule.** One background carrying `electricity_vintage` stamps the build. Two carrying
*different* vintages is a hard-stop — a build is single-grid by design, and silently picking one
is exactly the parity failure the stamp exists to catch.

## 3. Conversion table — supplement, don't replace

Custom flows are absent from `uslci_flow_conversions.json`, so `flow_conv.get(uuid)` returns None
and cross-property conversion is skipped **silently**. That is ledger #7 in new clothes.

`setup_conversions.build_table` already has the mechanism — its `supplement_zips` argument fills
gaps from per-process exports, and `parse_flow` is generic. Reuse it: parse the custom source's
own `flows/` at import time and merge the entries the committed table lacks. Flows that only ever
appear in one property need no entry and cost nothing.

## 4. Entry point

```python
def import_jsonld(source, *, db_name, background=(), allow_unit_passthrough=False,
                  allow_unhinted_links=False, overwrite=False, confirm=None,
                  project=PROJECT_NAME, log=_NOOP) -> JsonldBuild:
    """Import an olca-schema JSON-LD zip or directory into `db_name`.

    `background` is an ordered list of already-built brightway database names that
    technosphere links resolve against, e.g. ["uslci-full", "electricity-baseline"].
    """
```

`import_uslci` keeps its signature and becomes a preset over the same core — its `db_name` from
config, its `background` fixed to `[ELECTRICITY_BASELINE_DB]`, its sources from
`discover_sources`. The validated path must stay byte-identical.

**Persistent vs transient is one argument.** `db_name="my-study"` gives a reusable database with
its own provenance sidecar, searchable and runnable by UUID. `db_name=FOREGROUND_DB` with a drop
afterwards gives the CSV foreground's throwaway semantics. Same code.

Routing JSON through this parser rather than extending `foreground_importer` is what earns the
feature: the CSV path is flat, and a JSON foreground gets multi-output allocation, causal
co-products, avoided products and waste-treatment linking on day one.

## 5. Guards

Two new, alongside `check_unknown_units` and `check_consumed_causal_columns`:

```python
def check_unhinted_links(tally, db_name, allow_unhinted):
    """Hard-stop when a technosphere exchange resolved to nothing and carried no
    defaultProvider. A cut background link is a silently low result."""

def check_uuid_collisions(all_processes, external, db_name):
    """Hard-stop when a custom process UUID also exists in a background database.

    resolve() checks in-dataset processes BEFORE backgrounds, so a reused UUID
    silently shadows the background process it was meant to reference."""
```

## 6. Downstream — where a custom database is currently assumed away

- **`04_run_lca.py:99`** — `--database` has `choices=[USLCI_DB, USLCI_FULL_DB]`, which rejects a
  custom name outright. Validate against `bd.databases` instead.
- **`run.py:535`** — completeness is summarized with `[ELECTRICITY_BASELINE_DB]` as the only
  external list, so for a custom build **every USLCI link is counted as a cutoff** and the
  manifest reports `fully_linked: false` on a result that is fully linked. This is the one
  downstream bug that misreports rather than crashing.

  Fix at the source: stamp the backgrounds onto the database at write time —

  ```python
  bd.databases[db_name]["background_databases"] = list(background)
  ```

  and have `_assemble_manifest` read them back rather than assuming.

- **Completeness spans two sidecars.** A custom build's supply chain contains custom activities
  *and* USLCI activities, each with their own provenance file, and USLCI is a real joint solve —
  calling it "aggregated background" to dodge the problem would be false. So:

  ```python
  -def summarize_supply_chain_completeness(solved_keys, provenance_processes, uslci_db, external_dbs)
  +def summarize_supply_chain_completeness(solved_keys, provenance_by_db, auditable_dbs, external_dbs)
  ```

  with `run.py` loading a sidecar per auditable database. This is the largest ripple in the plan.

- **`run.py:125` `_require_database`** and `resolve_target`'s miss messages name USLCI builds
  unconditionally. Keep that advice when the name *is* a USLCI build; say something accurate
  otherwise.

- Cosmetic: `db_provenance_filename` gives a custom build `uslci_db_provenance.my-study.json`.
  Wrong-sounding but harmless. Renaming the whole scheme is a separate, breaking change.

## Verification

The generalization had to leave the validated path where it was, so that was checked directly
rather than argued:

- **Both USLCI databases rebuilt and their provenance sidecars diffed against the pre-change
  builds.** `uslci-subset`: all 391 per-process diagnostic blocks identical, all six totals
  identical. `uslci-full`: all 1,455 identical. (The one field that moved, `project`, was a stale
  artifact — the old sidecar had been written under the `fedefl-bw25-tutorial-check` project.)
- **Replication gate: PASS**, 60/60 cells within ±0.1%, largest deviation 7.7e-06. The three
  skipped cases are the 2025-vintage exports the vintage guard skips against a 2026 build, which
  is pre-existing. `--mode direct` cannot run against a 2026 build at all — its only case is
  2025-vintage — also pre-existing.
- **The custom path checked against arithmetic, not just "it ran".** A crate of 1.05 kg USLCI HDPE
  flake plus 250 g of its own CO2 scored 0.795706 kg CO2 eq; HDPE flake alone scores 0.519720, and
  0.519720 × 1.05 + 0.25 = 0.795706. That confirms the background link resolved through the hint
  *and* that the `g`→`kg` conversion ran on the direct emission.
- **The completeness fix checked in both directions.** After: the crate's chain reports 387
  auditable activities (HDPE's 386 + itself) and inherits its exact cutoff counts (113 unmatched,
  776 unlinked), with `other_activity_count: 0`. Under the pre-fix single-database assumption the
  same chain reported the USLCI activities as `other` and **zero** cutoffs — i.e. `fully_linked` on
  a result carrying 776 of them.

193 unit tests pass, up from 184.

## Known cosmetic issue

`db_provenance_filename` gives a custom build `uslci_db_provenance.my-study.json`. Wrong-sounding
but harmless, and renaming the scheme would break paths documented elsewhere. Left alone
deliberately.
