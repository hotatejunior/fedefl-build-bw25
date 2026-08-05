# Schema Crosswalk — USLCI openLCA JSON-LD → brightway25

How `setup/03_import_uslci.py` maps the openLCA JSON-LD process schema onto the brightway `db_data`
schema, field by field — and, more importantly, **how it resolves the places where the two data
models don't line up.** Read alongside [`DEVLOG.md`](DEVLOG.md) (design decisions) and
[`CLAUDE.md`](CLAUDE.md) (pipeline overview). Line references are to `setup/03_import_uslci.py`
unless noted.

---

## The shape mismatch

openLCA and brightway both describe "a process with exchanges," but they disagree on the two things
that matter most for building a linked database:

| | openLCA JSON-LD | brightway `db_data` |
|---|---|---|
| **How an exchange names its counterpart** | by **flow** (`flow.@id`), optionally with a `defaultProvider` hint | by **producing activity** — a resolved `(database, code)` tuple |
| **How it separates biosphere from technosphere** | one `flowType` field on the flow | two separate namespaces → the **A** (technosphere) and **B** (biosphere) matrices |
| **How many products a process may output** | many (with a `defaultAllocationMethod`) | one production exchange per activity |
| **What unit an amount is in** | any unit, against a `flowProperty` | the flow's single reference unit |

So the importer's job is not a field rename — it is to **synthesize** the two fields brightway needs
that don't exist in the source (`input` and `type`), normalize units, and split multi-output
processes into single-output activities. Those four operations are the whole engine; the field-level
crosswalk below is the easy part.

### Target schema

```python
db_data[(USLCI_DB, code)] = {          # one entry per activity
    "name":  str,
    "code":  str,
    "location": str,
    "unit":  str,                      # the reference product's normalized unit
    "exchanges": [
        {"input": (db, code), "amount": float, "unit": str,
         "type": "production" | "technosphere" | "biosphere"},
        ...
    ],
}
bd.Database(USLCI_DB).write(db_data)   # line 832
```

---

## Every exchange, by flow type

The whole crosswalk in one table: the seven kinds of exchange you'll meet, and how each is written on
both sides. This is the intuitive view — start here; the field-by-field detail follows. `α` = the
process's allocation factor (1.0 for single-output); `m` = the co-product re-basis multiplier.

| Flow — kind & direction | USLCI JSON-LD exchange | brightway exchange |
|---|---|---|
| **Reference product** — output | `isInput: false`, `flowType: PRODUCT_FLOW`, `isQuantitativeReference: true` | `type: "production"`, `input: (USLCI_DB, own code)`, amount = yield — **never** ×α |
| **Co-product** — non-ref output | `isInput: false`, `flowType: PRODUCT_FLOW`, `isQuantitativeReference: false` | **No exchange here.** It's an instruction to allocate: scalar → the reference activity's inputs are scaled by α; causal → a dedicated `{proc}__co__{flow}` activity is built (where this flow *is* the production exchange). Consumers link to whichever. |
| **Technosphere** — input | `isInput: true`, `flowType: PRODUCT_FLOW`, usually `defaultProvider: {…}` | `type: "technosphere"`, `input: (db, resolved producer)`, amount = normalized ×α ×m |
| **Elementary** — output (emission) | `isInput: false`, `flowType: ELEMENTARY_FLOW` | `type: "biosphere"`, `input: (BIOSPHERE_DB, flow.@id)`, amount = normalized ×α (within-property only) |
| **Elementary** — input (resource) | `isInput: true`, `flowType: ELEMENTARY_FLOW` | `type: "biosphere"`, `input: (BIOSPHERE_DB, flow.@id)`, amount = normalized ×α — **same shape as the row above** |
| **Waste** — output (to treatment) | `isInput: false`, `flowType: WASTE_FLOW`, `defaultProvider: {treatment}` | `type: "technosphere"`, `input: (db, treatment proc)`, amount = normalized ×α — positive, i.e. consuming the treatment service |
| ⚠ **Reference** — *input* (waste sink) | `isInput: true`, `flowType: WASTE_FLOW`, `isQuantitativeReference: true` | `type: "production"`, `input: (USLCI_DB, own code)`, amount = normalized. The activity's function is the waste it *consumes*. |

Two things this view makes obvious:

- **Direction lives in a different place on each side.** USLCI marks it on the *exchange* (`isInput`);
  brightway marks technosphere direction by `type`, and biosphere direction by the *flow* it points to
  — which is why **elementary input and output collapse to the same exchange shape** (rows 4–5),
  differing only in which FEDEFL node they reference (a `natural resource` node vs an `emission` node,
  set in `setup/01`). Both carry a positive amount; which FEDEFL node the exchange points at is what
  distinguishes a resource drawn from an emission released.
- **A co-product output is not an exchange** (row 2) — it is an instruction to allocate. That one fact
  is why multi-output processes need a whole pre-pass instead of a per-exchange rule.

**Avoided-product variant** of the technosphere-input row: `isInput: true` **and `isAvoidedProduct:
true`** → identical to that row, but the amount is **sign-flipped negative** (a credit — e.g.
landfill-gas electricity displacing grid power). See §F.

---

## Field crosswalk

Three tables, one per nesting level of the source (process → exchange → `exchange.flow`). The
`→ brightway` column says where the value lands; *(drives)* means it steers a decision without being
stored verbatim; *dropped* means brightway carries no equivalent.

### 1 · Process level

| openLCA JSON-LD | → brightway | How it crosses |
|---|---|---|
| `@id` | activity `code` + `(USLCI_DB, code)` key | Verbatim (line 768). Causal co-products get a synthesized code `{@id}__co__{flow}` (line 529). |
| `name` | activity `name` | Copied (line 763); co-product activities decorated `… [causal co-product: <flow>]` (line 766). |
| `location.name` | activity `location` | Normalized via `LOCATION_MAP` ("United States of America (the)"→"US"); unmapped names pass through, empty→"GLO" (lines 446, 758). |
| `exchanges[]` | activity `exchanges` | The core loop — each exchange becomes one production / technosphere / biosphere exchange (tables 2–3). |
| `defaultAllocationMethod` | *(drives)* | Selects the allocation regime — native / mass / causal — in the pre-pass ([`setup/allocation.py`](setup/allocation.py), line 508). |
| `allocationFactors[]` | *(drives)* | Supplies per-exchange causal factor columns and scalar physical/economic factors ([`setup/allocation.py`](setup/allocation.py)). |
| `version` | *(dedup + sidecar)* | Precedence key when one `@id` appears in several bundles (line 241); also recorded in `uslci_db_provenance.json` (line 781). |
| `lastChange` | *(dedup + sidecar)* | Tiebreak after `version`; also in the sidecar. |
| `@type`, `processType`, `isInfrastructureProcess`, `lastInternalId` | *dropped* | Constant or irrelevant discriminators. |
| `description`, `category`, `processDocumentation`, `dqSystem`, `dqEntry`, `socialAspects`, `parameters` | *dropped* | Metadata brightway activities don't carry. (`category` is kept for **biosphere** nodes only, sourced from FEDEFL context in `setup/01`.) |

### 2 · Exchange level

| openLCA JSON-LD | → brightway | How it crosses |
|---|---|---|
| `amount` | exchange `amount` | Transformed, not copied: `normalize()` unit conversion → × allocation factor → (× co-product multiplier **or** sign-flip). See "The three amount modifiers" below (lines 597–735). |
| `unit.name` | *(drives)* + result `unit` | Within-property conversion input to `normalize()` (`l`→`m³`); the returned reference unit becomes the exchange `unit`, and for the reference exchange the activity `unit` (lines 598, 611). |
| `flowProperty.@id` | *(drives)* | Cross-property conversion key into `FLOW_CONV` (e.g. Btu-of-diesel→m³ via energy density) (lines 599, 610). |
| `isInput` | *(drives `type`)* | Primary direction classifier: `false` + reference → `production`; `true` → input side; combined with `flowType` for the bio/techno/waste split (lines 580, 652). |
| `isQuantitativeReference` | `type = "production"` | Marks the one production exchange (line 601); also builds the `flow → producer` map used for linking (line 345). |
| `defaultProvider.@id` | *(drives `input`)* | First-choice provider hint → resolves the link's `(db, code)` (lines 400–405). |
| `defaultProvider.name` | *(drives `input`)* | Fallback: used only when the `@id` doesn't resolve but the name uniquely matches one candidate (lines 421–425). |
| `isAvoidedProduct` | sign of `amount` | `true` → normalized amount negated (burden → credit) before linking (line 677). |
| `internalId` | *(drives)* | Key tying an exchange to its causal per-exchange allocation factor (line 625). |
| `amountFormula` | *dropped* ⚠ | Parameterized formulas are **not** evaluated — the resolved numeric `amount` is used as-is. A known limitation for parametric processes (see "Known limitations"). |
| `@type`, `description` | *dropped* | — |

### 3 · Flow reference (nested in `exchange.flow`)

| openLCA JSON-LD | → brightway | How it crosses |
|---|---|---|
| `@id` | *(drives `input`)* | **The universal key.** Biosphere → `(BIOSPHERE_DB, @id)` directly (line 641). Technosphere → looked up in the `flow → producer` map to find the supplying process (line 684). |
| `flowType` | *(drives `type`)* | The bio/techno discriminator: `ELEMENTARY_FLOW` → biosphere; `PRODUCT_FLOW` / `WASTE_FLOW` → technosphere (lines 633, 652). |
| `name` | *(diagnostics + co-product naming)* | Not a link key; used in warnings and co-product activity labels (line 765). |
| `refUnit` | *(informational)* | Not directly consumed — unit resolution goes through `flowProperty` + `unit` + `FLOW_CONV`, not this field. |
| `@type`, `category` | *dropped* | — |

### Reverse view — where each brightway field is born

| brightway field | Origin |
|---|---|
| activity `code` | process `@id` (or synthesized `__co__` code) |
| activity `name` | process `name` |
| activity `location` | `location.name` via `LOCATION_MAP` |
| activity `unit` | the reference exchange's **normalized** unit |
| exchange `input` | **synthesized** — provider resolution, or `(BIOSPHERE_DB, flow.@id)` |
| exchange `amount` | `amount` × unit conversion × allocation × multiplier × sign |
| exchange `unit` | `normalize()` return value |
| exchange `type` | **synthesized** — `isInput` + `flowType` + `isQuantitativeReference` |

The two **synthesized** fields, `input` and `type`, are the whole difficulty. Everything below is how
they — and the unit and allocation mismatches — get resolved.

---

## Solving the incompatibilities

### A. Exchange direction — reading `isInput` directly

brightway has no `isInput` field; direction is carried by exchange `type` and sign. openLCA states it
explicitly as a boolean, and **`bw2io`'s `JSONLDImporter` misclassifies it** — the documented reason
this pipeline parses JSON-LD by hand. The reference exchange (`isQuantitativeReference=true`, usually
an output) becomes the `production` exchange; everything else is an input to classify (line 580).
One subtlety: USLCI waste-treatment "sink" processes define their function by the waste they
*consume*, so `isQuantitativeReference` legitimately lands on an **input** there — handled at line
601, and it doesn't matter for downstream linking because the `flow → producer` map keys on the
reference flow either way.

### B. Biosphere vs. technosphere — FEDEFL UUID as the join key

`flowType` decides the matrix. For `ELEMENTARY_FLOW`, the flow's own `@id` **is** the biosphere code,
because `setup/01` loaded every FEDEFL flow keyed by its FEDEFL UUID. So linking an emission is a
single set-membership test against `bio_uuids` (line 444) — `(BIOSPHERE_DB, flow.@id)`, no name
matching, no fuzzy context resolution (line 641). An unmatched UUID becomes a counted cutoff rather
than a crash. This is why UUID-keying is a load-bearing design choice and not a stylistic one: it
turns biosphere linkage into an identity lookup.

### C. Provider resolution — synthesizing `input` for technosphere links

A brightway technosphere exchange must name the producing *activity*; a USLCI input names only a
**flow**, which several processes may produce (63 reference flows in the full public DB are shared,
one by 41 producers). `_resolve_provider` (lines 389–428) decides in priority order:

1. the exchange's own `defaultProvider.@id`, if it's a process we imported (bundle-internal or the
   injected electricity baseline);
2. else, if the flow maps to exactly one producer, that one;
3. else, if the `defaultProvider.name` matches exactly one candidate, that one (this mirrors an
   openLCA operator picking the named mix by hand — it recovers links where a grid node's UUID was
   renamed across baseline versions but the name is stable);
4. else genuinely ambiguous → left a cutoff rather than guessed, and reported.

The `flow → producer` map is built once from every process's reference exchange (lines 340–354), and
cross-database providers injected by `setup/03b` (the electricity baseline) join the same candidate
pool (lines 367–374), so a single resolver handles both intra-USLCI and USLCI→baseline links.

### D. Unit normalization — one reference unit per flow

brightway needs every amount of a flow in that flow's one reference unit; openLCA expresses amounts
in arbitrary units against a `flowProperty`. `normalize()` (line 161) is a two-step conversion:

1. **within-property** — `unit.name` → the property's reference unit (`l`→`m³`, `Btu`→`MJ`) via the
   `WITHIN_FP` table (line 97);
2. **cross-property** — the property's reference unit → the flow's reference-property unit (e.g.
   Btu-of-diesel → m³ via energy density) via `FLOW_CONV`, the substance-specific table
   `setup/00` builds.

Biosphere flows pass `cross_property=False` — CO₂ stays in kg, kBq stays in kBq (line 636). An
**unrecognized unit is a hard-stop before the database is written**, not a silent passthrough (lines
150–158), because a wrong unit is "a wrong number wearing a plausible one's clothes." One case-
sensitive trap is handled explicitly: `Mg` (megagram) vs `mg` (milligram), a 10⁹ error if collapsed
(line 139).

### E. Multi-output processes — splitting into single-output activities

brightway wants one production exchange per activity; a USLCI process can output several products
under a `defaultAllocationMethod`. Resolved in a **pre-pass** (lines 508–537) that runs before the
build loop, because a consumer can be built before the supplier it points at is visited. Logic lives
in [`setup/allocation.py`](setup/allocation.py) (extracted so it unit-tests on synthetic JSON). Three
regimes:

- **native / mass (scalar).** One factor scales every input and biosphere exchange of the single
  built (reference) activity. A *consumer* drawing a non-reference co-product is re-based at the link
  site by a multiplier that converts a request in the co-product's units into the equivalent
  reference-product amount carrying that co-product's allocated burden:
  `m = (ref_yield · target_alloc) / (target_yield · ref_alloc)` (line 729).
- **causal (per-exchange).** Burdens are assigned per *exchange*, so no scalar can re-base them. Each
  causal co-product gets its **own dedicated activity** (code `{proc}__co__{flow}`), built from its
  own column of the factor grid (line 529); consumers link straight to it, no multiplier (lines
  700–718). A *consumed* causal co-product with an empty factor column hard-stops the build (line
  794) — it would otherwise silently degrade to a flat mass split, the exact error causal handling
  exists to prevent.

### F. Two openLCA modeling conventions, normalized

- **Waste as an output flow.** openLCA models disposal as the generator **outputting** a `WASTE_FLOW`
  whose `defaultProvider` is a treatment process (whose own reference is that waste as an input). The
  importer links this output exactly like a positive input, so the disposal burden (e.g. landfill
  methane) actually lands (lines 652–663). Without it, every waste-to-treatment link was silently
  dropped.
- **Avoided products as credits.** `isAvoidedProduct=true` marks byproduct energy/material recovery
  that *displaces* production elsewhere (e.g. landfill-gas electricity displacing grid power). openLCA
  subtracts these; a positive brightway input amount would add them as a burden, so the normalized
  amount is sign-flipped to a credit (line 677). This was the final petroleum-parity fix.

---

## Two worked examples

**A technosphere input** — hydrogen consumed by petroleum refining:

```
source exchange:  isInput=true, amount=0.00459, unit="kg",
                  flow.@id = f842f7d2-…  (flowType PRODUCT_FLOW),
                  defaultProvider.@id = 4eaec911-…  ("Hydrogen; liquid, synthesis gas; at plant")
    ↓  isInput=true + PRODUCT_FLOW            → technosphere link (needs a producer)   [B, C]
    ↓  normalize(0.00459, "kg", Mass)         → 0.00459 (kg is already reference)      [D]
    ↓  × reference-product allocation factor  → petroleum is multi-output              [E]
    ↓  _resolve_provider: defaultProvider.@id present & imported → link it             [C]
brightway exchange:  {"input": (USLCI_DB, "4eaec911-…"), "amount": 0.00459·α,
                      "unit": "kg", "type": "technosphere"}
```

**A biosphere exchange** — a cyclohexane air emission from the same process:

```
source exchange:  isInput=false, amount=2.8e-07, unit="kg",
                  flow.@id = 12b39b80-…  (flowType ELEMENTARY_FLOW)
    ↓  ELEMENTARY_FLOW                        → biosphere exchange                     [B]
    ↓  normalize(…, cross_property=False)     → 2.8e-07 kg (never cross-converted)     [D]
    ↓  12b39b80-… ∈ bio_uuids?                → yes, matched                           [B]
brightway exchange:  {"input": ("biosphere-fedefl", "12b39b80-…"), "amount": 2.8e-07·α,
                      "unit": "kg", "type": "biosphere"}
```

---

## Known limitations

- **`amountFormula` is not evaluated — parametric processes are frozen at their shipped
  configuration.** Exchange amounts use the resolved numeric `amount`. Measured on USLCI
  v1.2026-06.0 (2026-08-05): 105 processes carry parameters and 264 carry at least one exchange
  formula, 18.5% of the database. This costs **no accuracy at the shipped parameter values** — four
  validated cases (petroleum, chlorine, HDPE, PET) carry formulas and still reproduce openLCA at
  1.000, which is itself proof that the stored `amount` equals openLCA's evaluation, and all 29
  formula exchanges with a zero stored amount evaluate to exactly 0 at their shipped values. What is
  lost is the ability to **change** a parameter: the wastewater-treatment models ship with switches
  like `disinfect` and `filter_include` set to 0, and an openLCA user would flip them to model a
  different treatment train. Here they are fixed, and nothing surfaces that a knob exists. See
  RELEASE_PLAN Phase 6 for the parametric-modelling roadmap.
- **Process `category` is dropped** for USLCI activities (biosphere nodes keep FEDEFL context as
  `categories`).
- **Genuinely ambiguous links become cutoffs.** When a flow has several producers and no usable
  provider hint, the exchange is left unlinked rather than guessed — correct, but it means coverage
  depends on which bundles are present (see the per-process vs. whole-DB note in the README roadmap).
