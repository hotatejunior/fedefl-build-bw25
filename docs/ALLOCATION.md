# Allocation — how this pipeline splits multi-output processes

For practitioners who need to know what the engine did to their numbers, and reviewers checking it.
The [short answer](#short-answer) is the rule; the rest is how to defend it. See also
[`SCHEMA_CROSSWALK.md`](SCHEMA_CROSSWALK.md) for how JSON-LD becomes exchanges and
[`VALIDATION_REPORT.md`](VALIDATION_REPORT.md) for the parity evidence.

---

## Short answer

A process that produces more than one product has to divide its burdens among them. This engine
**does not choose an allocation method.** It reads the method each USLCI process declares
(`defaultAllocationMethod`) and applies the factors that process ships, so that the result matches
what openLCA computes from the same file.

Four paths, in the order the engine tries them:

| Path | When | What is applied |
|---|---|---|
| **single** | one product output | nothing — α = 1.0 |
| **native** | the process ships a scalar factor for its declared method | that factor, α, on every input and elementary flow |
| **mass** | multi-output but no usable native factor | mass fraction computed from the outputs' yields |
| **causal** | declared `CAUSAL_ALLOCATION` | a **separate factor per exchange**, never flattened to a scalar |

The reference product's production exchange is **never** scaled by α — only the inputs and emissions
are. Scaling both would divide the burden out twice.

> **This is engine fidelity, not methodological endorsement.** Whether the declared method is
> *appropriate* for your study — whether physical allocation is defensible for that chemical plant,
> whether you should have used substitution instead — is your judgment call, not the engine's. See
> [What this means for you](#8-what-this-means-for-you).

Allocation was the hardest part of this import to get right, for three reasons. The two data models disagree about
shape, so the mismatch has to be resolved somewhere. The source data is not self-consistent: one
process can ship three competing sets of factors, and only the declared method says which governs.
And a wrong factor produces a plausible-looking number with no crash and no warning, which is how the
bugs in section 9 survived as long as they did.

---

## 1. The shape problem — N products, one column

This is the structural barrier people hit when moving openLCA JSON-LD into brightway.

brightway's technosphere matrix **A** must be square: one activity is one column, identified by
exactly one production exchange. openLCA instead treats a process as a *block* with N product outputs
(chlorine has 3, hardboard 7, the MRF sorting processes 8), keeping it intact in the file and
resolving multifunctionality at calculation time.

The difference is not whether allocation happens but when. brightway forces it to be materialized at
import, so this engine partitions while writing `db_data` and every activity it writes is
mono-functional by construction.

Two reshaping strategies, chosen by allocation type:

### (a) Collapse — scalar allocation (`native` / `mass`)

An N-product process becomes **one** activity: the reference product's.

- production exchange = the reference product's own native yield
- every input and elementary flow = scaled by α, the reference product's factor
- the N−1 co-products get **no column at all**

That is the textbook partitioning step: a multifunctional process is converted into a mono-functional
unit process carrying its share of the burden.

If the co-product has no column, how does anything consume it? Consumers link to the reference
activity, with their amount converted by a re-basis multiplier (section 6). Under scalar allocation
every co-product's column would be the same input vector rescaled, so an extra column would carry no
new information while making provider resolution ambiguous.

### (b) Explode — causal allocation

Here the collapse is invalid: under causal allocation each exchange is attributed differently
(section 5), so the columns are not rescalings of one another and no multiplier can convert between
them.

An N-product causal process becomes **1 + k activities** — the reference product's, plus a dedicated
activity per causal co-product coded `<proc_uuid>__co__<flow_uuid>`, each with one production exchange
and its own column of the factor grid. Consumers link straight to it, already in the co-product's own
units.

This is why the full-database build holds **1,455 activities for 1,425 source processes**: 30
dedicated causal co-product columns.

### The invariant, and how to check it

> **Every activity written by this engine has exactly one production exchange.**

The squareness guarantee is checkable rather than a claim you have to take:

```bash
python - <<'PY'
import bw2data as bd
from collections import Counter
bd.projects.set_current("fedefl-build-bw25")
for name in ("uslci-subset", "uslci-full"):
    db = bd.Database(name)
    counts = Counter(sum(1 for e in a.exchanges() if e["type"] == "production") for a in db)
    print(f"{name}: {len(db)} activities | production exchanges per activity -> {dict(counts)}")
PY
```

Current builds:

```
uslci-subset:  391 activities | production exchanges per activity -> {1: 391}
uslci-full:   1455 activities | production exchanges per activity -> {1: 1455}
```

No exceptions in either build. If this ever prints anything other than `{1: N}`, the matrix is
malformed and every downstream number is suspect.

---

## 2. What USLCI actually contains

Census of all **1,425 processes** in USLCI v1.2026-06.0:

| | Processes |
|---|---|
| Declare `PHYSICAL_ALLOCATION` | 721 |
| Declare `NO_ALLOCATION` | 429 |
| Declare `ECONOMIC_ALLOCATION` | 30 |
| Declare `CAUSAL_ALLOCATION` | 7 |
| **Actually multi-output** (>1 product output) | **66** |

Of those 66 multi-output processes — the only ones where allocation changes any number:

| Declared method | Multi-output | Degenerate (all factors 0 or 1) | Non-degenerate |
|---|---|---|---|
| `PHYSICAL_ALLOCATION` | 33 | 1 | **32** |
| `ECONOMIC_ALLOCATION` | 28 | **28** | **0** |
| `CAUSAL_ALLOCATION` | 5 | — (per-exchange, see section 5) | 5 |

Two findings a reviewer should know:

- **Most processes that declare a method never use it.** 721 declare physical allocation; 33 are
  multi-output. The declaration is boilerplate on single-output processes.
- **Economic allocation is degenerate throughout USLCI.** All 28 multi-output economic processes
  assign every factor as exactly 0.0 or 1.0 — one product absorbs 100% of the burden, co-products
  get zero. Not one process in the database has two economic factors strictly between 0 and 1.
  The engine implements economic allocation correctly, but **USLCI cannot test whether it does**;
  see [section 9](#9-how-we-know-this-is-right).

> A note on counting: 30 processes *declare* `ECONOMIC_ALLOCATION`, but only 28 are multi-output.
> Both numbers are correct and describe different sets.

---

## 3. Path `native` — a scalar factor per product

The common case. The process ships an `allocationFactors` entry whose `allocationType` matches its
declared `defaultAllocationMethod` and which has **no** `exchange` key (i.e. it is a per-product
scalar, not a per-exchange cell). That value becomes α.

### Worked example 1 — chlorine (scalar native)

`Chlorine; chlor-alkali electrolysis; at plant` (`a3e150d0-…`), declared `PHYSICAL_ALLOCATION`.

Product outputs:

| Product | Yield |
|---|---|
| Chlorine **(reference)** | 0.4418 kg |
| Sodium hydroxide | 0.5529 kg |
| Hydrogen | 0.01925 kg |

What the file actually ships in `allocationFactors`:

| Type | Rows | Shape |
|---|---|---|
| `CAUSAL_ALLOCATION` | 213 | per-exchange |
| `ECONOMIC_ALLOCATION` | 3 | scalar |
| `PHYSICAL_ALLOCATION` | 3 | scalar |

**Three competing factor sets in one process.** The engine takes the 3 scalar `PHYSICAL` rows,
because that is what the process declares, and ignores the other 216 rows. Result:

```
method = "native"    α(chlorine) = 0.4357244355
                     α(NaOH)     = 0.5452905911
                     α(H2)       = 0.0189849734
```

Every input and emission on the chlorine activity is scaled by **0.4357**. Sodium hydroxide and
hydrogen get no column; a consumer of either is re-based onto this one (section 6).

Note for the sceptical: here the physical factors happen to equal the mass fractions
(0.4418 / 1.01395 = 0.4357), so this case would validate identically under the mass fallback. It is a
clean *arithmetic* check but **not** a discriminating test of native-vs-mass selection.

---

## 4. Path `mass` — the fallback

If a multi-output process ships no usable scalar factor for its declared method, the engine computes
a mass fraction from the outputs' yields, converting each to kg via the flow-conversion table:

```
α(ref) = mass(ref) / Σ mass(all product outputs)
```

If a product has no mass conversion available, the import **stops with a diagnostic** naming the
flow, its unit, and its flow property — rather than silently treating it as massless. Guessing here
would produce exactly the invisible error described in section 0.

In the current full-database build this path is used **0 times**; every multi-output process ships a
usable native factor or is causal. It exists for robustness against future USLCI releases.

---

## 5. Path `causal` — a factor per exchange

Five processes declare `CAUSAL_ALLOCATION`, and they are the reason this document exists.

Causal allocation does not give each *product* a number. It gives each **(exchange, product) pair** a
number — a full grid. `Single stream recyclables sorting; at material recovery facility` ships 8
products against a grid of per-exchange factors, so one input may be attributed 36.6% to recovered
HDPE while another input is attributed 3.4% to the same product.

**Flattening this to one scalar is wrong, and we have the receipts.** An earlier version fell back to
the mass fraction for causal processes (the native lookup returns nothing for them, since all causal
rows carry an `exchange` key). That over-attributed forest-residue feedstock to cellulosic ethanol by
**1.83×**. The fix reads the reference product's own column of the grid and applies each factor to its
own exchange, keyed by `internalId`.

For how consumers of causal co-products are handled, see section 1(b) — the dedicated-activity
strategy exists specifically because causal columns are not rescalings of each other.

Any exchange missing a causal entry falls back to the mass fraction and is **counted and reported** at
import (30 such exchanges on the reference activities of the full build) — never silently absorbed.

---

## 6. Re-basis — consuming a scalar-allocated co-product

A process drawing a **non-reference** co-product of a scalar-allocated process must not inherit the
reference product's basis. That was a real bug (see `DEVLOG.md`). The engine converts a request in the
co-product's own units into the equivalent amount of the reference product carrying the co-product's
correctly-allocated share:

```
m(P, flow) = (ref_yield × target_alloc) / (target_yield × ref_alloc)
```

For pure mass allocation this reduces to a units/density conversion — the reference product's declared
yield may be in L or m³ while its allocation factor is mass-based. For economic allocation it does real
burden re-attribution. Both fall out of the same formula. The reference product itself gets no entry
(implicitly 1.0). Where a multiplier is uncomputable (zero or missing yield/allocation) the flow is
**skipped and reported**, not guessed.

The full-database build re-bases **125** co-product links this way.

---

## 7. Avoided products — a different thing that looks similar

Not allocation, but adjacent, frequently confused, and the source of the project's longest-running
error.

USLCI marks byproduct recovery with `isInput: true` **and** `isAvoidedProduct: true` — e.g. MSW
landfilling recovering landfill-gas electricity that displaces grid power. These are **credits**:
openLCA subtracts them. A brightway technosphere input with a positive amount is a positive
*consumption*, so the engine **flips the sign**.

Getting this wrong cost months. Because petroleum's toxicity result is ~99% grid electricity, a single
sign error on one landfill-gas exchange moved petroleum's ecotoxicity/cancer/non-cancer cells to ~1.03
and produced a confidently-published root-cause analysis that was **wrong** and later retracted. The
history is in `validation/VALIDATION_LOG.md` (2026-07-17 → 2026-07-21).

---

## 8. What this means for you

**The engine reproduces USLCI's declared allocation. It does not vouch for it.** Concretely:

- **Check what governed your result.** Every `general/04_run_lca.py` run writes
  `validation_manifest.json`, and the import prints an allocation summary — how many multi-output
  processes were hit, which path each took, how many co-product links were re-based. If your product
  system includes a multi-output process, look.
- **Economic allocation in USLCI is all-or-nothing.** If your supply chain passes through one of the
  28 degenerate economic processes, a co-product you might expect to carry burden carries exactly
  zero. That is USLCI's modelling choice, faithfully reproduced. If it is wrong for your study, it is
  wrong at the source, and you should say so in your write-up rather than expect the engine to fix it.
- **A declared method can disagree with the shipped factors.** Chlorine ships economic and causal rows
  it never uses. If you hand-check a factor against the JSON, make sure you are reading the rows for
  the *declared* method.
- **Causal co-products appear as extra activities.** Seeing `… [causal co-product: …]` in a
  contribution listing is expected, not a bug — see section 1(b).
- **Allocation is worth scrutinising before anything else here.** It is the least mechanical part
  of the pipeline, and the choice USLCI declares may not be the one your study wants.

---

## 9. How we know this is right

The engine reproduces openLCA on all 100 validated cells; the evidence is in
[`VALIDATION_REPORT.md`](VALIDATION_REPORT.md). What that covers for allocation specifically:

- **Scalar physical, real splits** — chlorine (3-way), hardboard (7-way, 0.8634 down to 0.0016),
  soybean (2-way, 0.8051 / 0.1949).
- **Causal consumption** — recycled HDPE and PET flake both draw MRF-sorting causal co-products
  through the dedicated-activity path.
- **Per-exchange causal arithmetic** — pinned by synthetic fixtures in `tests/test_allocation.py`,
  because no USLCI case exercises a non-uniform grid at full precision.
- **Squareness** — checkable directly, section 1.

Three gaps go with it. Economic-allocation arithmetic is untestable against USLCI, since the code
path only ever sees degenerate 0/1 factors. The mass fallback has zero uses in the full build, so it
is untested on real data. And parity is not correctness: both engines read the same USLCI file, so
agreement proves the implementations match and cannot catch an error both inherit from the source.

---

## 10. Where the code lives

| | |
|---|---|
| `fedefl_bw25/allocation.py` | All allocation logic. Pure functions, no brightway dependency: `allocation_for` (which path, what α), `causal_coproducts` (per-co-product grid columns), `coproduct_multipliers` (the re-basis formula) |
| `setup/03_import_uslci.py` | Calls the above in a pre-pass, then applies factors while building exchanges; builds the dedicated causal activities; handles `isAvoidedProduct` |
| `tests/test_allocation.py` | Unit tests on synthetic openLCA JSON — no import run required |
