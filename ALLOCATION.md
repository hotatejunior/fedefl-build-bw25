# Allocation — how this pipeline splits multi-output processes

**Who this is for.** Practitioners who need to know what the engine did to their numbers, and
reviewers who need to check it. Read the [Short answer](#short-answer) if you just want the rule;
read on if you need to defend it.

Companion docs: [`SCHEMA_CROSSWALK.md`](SCHEMA_CROSSWALK.md) (how openLCA JSON-LD becomes brightway
exchanges), [`DEVLOG.md`](DEVLOG.md) (design decisions and the bugs behind each rule),
[`VALIDATION_REPORT.md`](VALIDATION_REPORT.md) (the parity evidence).

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
> [What this means for you](#9-what-this-means-for-you).

---

## 1. Why this is the hard part

Everything else in the import is a translation problem with one right answer: a unit is grams or it
isn't, a flow is elementary or it isn't. Allocation is different, for three reasons:

1. **The data models disagree about shape.** brightway needs one product per activity; openLCA lets a
   process emit any number. That mismatch has to be resolved at import — see Section 2.
2. **The source data is not self-consistent.** A single process can ship three competing sets of
   factors (see the [chlorine example](#worked-example-1--chlorine-scalar-native)); only the declared
   method says which one governs.
3. **The error is invisible.** A wrong allocation factor produces a plausible-looking number. There
   is no crash, no warning, nothing to notice — which is exactly how the bugs in
   [Section 10](#10-how-we-know-this-is-right) survived as long as they did.

---

## 2. The shape problem — N products, one column

This is the structural barrier people hit when moving openLCA JSON-LD into brightway, and it is worth
being precise about.

**brightway's requirement.** The technosphere matrix **A** must be square: one activity contributes
one column, and that column is identified by exactly one production exchange. Solving `A·s = f` means
inverting **A**, so there must be as many columns as product flows. An activity with two production
exchanges has no well-defined column and no well-defined scaling factor.

**What openLCA gives you.** A process is a *block*, not a column: one process, N product outputs
(chlorine has 3; hardboard has 7; the MRF sorting processes have 8). openLCA keeps the block intact
in the file and resolves multifunctionality **at calculation time**, building an allocated matrix
internally when you hit Calculate.

**So the difference is not whether allocation happens — it is when.** brightway forces it to be
*materialized at import*, in the stored data. openLCA defers it to solve time. This engine therefore
does the partitioning while writing `db_data`, and every activity it writes is mono-functional by
construction.

Two reshaping strategies, chosen by allocation type:

### (a) Collapse — scalar allocation (`native` / `mass`)

An N-product process becomes **one** activity: the reference product's.

- production exchange = the reference product's own native yield
- every input and elementary flow = scaled by α, the reference product's factor
- the N−1 co-products get **no column at all**

That is the textbook partitioning step: a multifunctional process is converted into a mono-functional
unit process carrying its share of the burden.

The obvious objection: *if the co-product has no column, how does anything consume it?* Answer —
consumers are linked to the **reference activity**, with their amount converted by a re-basis
multiplier (Section 7). The co-product is not a separate column; it is an alias for a scaled quantity
of the reference column. This works precisely because under scalar allocation every co-product's
column *would* be the same input vector rescaled — so an extra column would carry no new information,
while creating two activities producing overlapping flows and making provider resolution ambiguous.

### (b) Explode — causal allocation

Here the collapse is invalid, because under causal allocation the co-products' columns are **not**
rescalings of one another: each exchange is attributed differently (Section 6). No multiplier can
convert one into another.

So an N-product causal process becomes **1 + k activities**: the reference product's, plus a dedicated
activity per causal co-product, coded `<proc_uuid>__co__<flow_uuid>`. Each has exactly one production
exchange (the co-product's own yield) and its own allocated input set, taken from that co-product's
column of the factor grid. Consumers link straight to it, no multiplier needed — it is already in the
co-product's own units.

This is why the full-database build holds **1,371 activities for 1,341 source processes**: 30
dedicated causal co-product columns.

### The invariant, and how to check it

> **Every activity written by this engine has exactly one production exchange.**

That is the squareness guarantee, and it is directly checkable rather than a claim you have to trust:

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
uslci-subset:  375 activities | production exchanges per activity -> {1: 375}
uslci-full:   1371 activities | production exchanges per activity -> {1: 1371}
```

No exceptions in either build. If this ever prints anything other than `{1: N}`, the matrix is
malformed and every downstream number is suspect.

---

## 3. What USLCI actually contains

Census of all **1,341 processes** in the full USLCI database (reproduce with the snippet in
[Section 11](#11-where-the-code-lives)):

| | Processes |
|---|---|
| Declare `PHYSICAL_ALLOCATION` | 700 |
| Declare `NO_ALLOCATION` | 429 |
| Declare `ECONOMIC_ALLOCATION` | 29 |
| Declare `CAUSAL_ALLOCATION` | 7 |
| **Actually multi-output** (>1 product output) | **60** |

Of those 60 multi-output processes — the only ones where allocation changes any number:

| Declared method | Multi-output | Degenerate (all factors 0 or 1) | Non-degenerate |
|---|---|---|---|
| `PHYSICAL_ALLOCATION` | 28 | 1 | **27** |
| `ECONOMIC_ALLOCATION` | 27 | **27** | **0** |
| `CAUSAL_ALLOCATION` | 5 | — (per-exchange, see Section 6) | 5 |

Two findings a reviewer should know:

- **Most processes that declare a method never use it.** 700 declare physical allocation; 28 are
  multi-output. The declaration is boilerplate on single-output processes.
- **Economic allocation is degenerate throughout USLCI.** All 27 multi-output economic processes
  assign every factor as exactly 0.0 or 1.0 — one product absorbs 100% of the burden, co-products
  get zero. Not one process in the database has two economic factors strictly between 0 and 1.
  The engine implements economic allocation correctly, but **USLCI cannot test whether it does**;
  see [Section 10](#10-how-we-know-this-is-right).

> A note on counting: 29 processes *declare* `ECONOMIC_ALLOCATION`, but only 27 are multi-output.
> Both numbers are correct and describe different sets — they have been reported inconsistently in
> older notes.

---

## 4. Path `native` — a scalar factor per product

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
hydrogen get no column; a consumer of either is re-based onto this one (Section 7).

Note for the sceptical: here the physical factors happen to equal the mass fractions
(0.4418 / 1.01395 = 0.4357), so this case would validate identically under the mass fallback. It is a
clean *arithmetic* check but **not** a discriminating test of native-vs-mass selection.

---

## 5. Path `mass` — the fallback

If a multi-output process ships no usable scalar factor for its declared method, the engine computes
a mass fraction from the outputs' yields, converting each to kg via the flow-conversion table:

```
α(ref) = mass(ref) / Σ mass(all product outputs)
```

If a product has no mass conversion available, the import **stops with a diagnostic** naming the
flow, its unit, and its flow property — rather than silently treating it as massless. Guessing here
would produce exactly the invisible error described in Section 1.

In the current full-database build this path is used **0 times**; every multi-output process ships a
usable native factor or is causal. It exists for robustness against future USLCI releases.

---

## 6. Path `causal` — a factor per exchange

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

For how consumers of causal co-products are handled, see Section 2(b) — the dedicated-activity
strategy exists specifically because causal columns are not rescalings of each other.

Any exchange missing a causal entry falls back to the mass fraction and is **counted and reported** at
import (230 such exchanges in the full build) — never silently absorbed.

---

## 7. Re-basis — consuming a scalar-allocated co-product

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

The full-database build re-bases **119** co-product links this way.

---

## 8. Avoided products — a different thing that looks similar

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

## 9. What this means for you

**The engine reproduces USLCI's declared allocation. It does not vouch for it.** Concretely:

- **Check what governed your result.** Every `general/04_run_lca.py` run writes
  `validation_manifest.json`, and the import prints an allocation summary — how many multi-output
  processes were hit, which path each took, how many co-product links were re-based. If your product
  system includes a multi-output process, look.
- **Economic allocation in USLCI is all-or-nothing.** If your supply chain passes through one of the
  27 degenerate economic processes, a co-product you might expect to carry burden carries exactly
  zero. That is USLCI's modelling choice, faithfully reproduced. If it is wrong for your study, it is
  wrong at the source, and you should say so in your write-up rather than expect the engine to fix it.
- **A declared method can disagree with the shipped factors.** Chlorine ships economic and causal rows
  it never uses. If you hand-check a factor against the JSON, make sure you are reading the rows for
  the *declared* method.
- **Causal co-products appear as extra activities.** Seeing `… [causal co-product: …]` in a
  contribution listing is expected, not a bug — see Section 2(b).
- **Allocation is where you should push back hardest.** It is the least mechanical part of the
  pipeline and the part where a defensible study most often diverges from the database default.

---

## 10. How we know this is right

Validated against openLCA on identical inputs — the engine is verified, not the data:

| Build | Cases | Cells | Max deviation |
|---|---|---|---|
| 2025 | petroleum, corn, cement, steel | 40 | within 0.1% |
| 2026 | steel, HDPE flake, PET flake, chlorine, hardboard, soy meal | 60 | 0.00077% |

Allocation-specific coverage:

- **Scalar physical, real splits** — chlorine (3-way), hardboard (7-way, 0.8634 → 0.0016), soybean
  (2-way, 0.8051 / 0.1949).
- **Causal consumption** — recycled HDPE flake and recycled PET flake both consume MRF-sorting causal
  co-products through the dedicated-activity path.
- **Per-exchange causal arithmetic** — pinned by synthetic fixtures in `tests/test_allocation.py`,
  because no USLCI case exercises a non-uniform grid at full precision.
- **Squareness** — checkable directly, Section 2.

**Known gaps, stated plainly:**

- **Economic allocation arithmetic is untestable against USLCI** (Section 3). The code path is
  exercised only on degenerate 0/1 factors.
- **The mass fallback is untested on real data** — 0 uses in the full build.
- **Parity is not correctness.** Both engines read the same USLCI file and the same TRACI factors.
  Agreement proves the *implementation* matches; it cannot catch an error both engines inherit from
  the source.

---

## 11. Where the code lives

| | |
|---|---|
| `setup/allocation.py` | All allocation logic. Pure functions, no brightway dependency: `allocation_for` (which path, what α), `causal_coproducts` (per-co-product grid columns), `coproduct_multipliers` (the re-basis formula) |
| `setup/03_import_uslci.py` | Calls the above in a pre-pass, then applies factors while building exchanges; builds the dedicated causal activities; handles `isAvoidedProduct` |
| `tests/test_allocation.py` | Unit tests on synthetic openLCA JSON — no import run required |

Reproduce the Section 3 census:

```bash
python - <<'PY'
import zipfile, json
from collections import Counter
z = "source_data/National_Renewable_Energy_Laboratory-USLCI_Database_Public.zip"
multi, decl = [], Counter()
with zipfile.ZipFile(z) as zf:
    for n in zf.namelist():
        if not (n.startswith("processes/") and n.endswith(".json")):
            continue
        p = json.loads(zf.read(n))
        outs = [e for e in p.get("exchanges", [])
                if not e.get("isInput")
                and e.get("flow", {}).get("flowType") == "PRODUCT_FLOW"]
        if p.get("defaultAllocationMethod"):
            decl[p["defaultAllocationMethod"]] += 1
        if len(outs) > 1:
            multi.append(p.get("defaultAllocationMethod"))
print("declared:", dict(decl))
print("multi-output:", len(multi), dict(Counter(multi)))
PY
```
