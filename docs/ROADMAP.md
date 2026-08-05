# Roadmap

Open work only. Everything already done is in [`DEVLOG.md`](DEVLOG.md), which carries the dated
release-plan record this file was split out of on 2026-08-05.

Where the engine stands: nine test cases reproduce openLCA on all 100 category × process cells
within 0.1%, both USLCI builds import, and every pipeline step is callable from a script.

---

## Now

**Documentation rewrite.** In progress. The docs grew to 43,000 words across 12 files, which is
about eight words per line of code. Target is a 40% cut, a practitioner-first tutorial, and a
troubleshooting guide built from the error strings the pipeline actually emits.

**Share with 2–3 trusted peers.** Outstanding since the `v0.1.0-beta` tag on 2026-07-23, and the
reason the docs work is happening first. The ask is specific: try to break the replication, try a
study-shaped foreground CSV, say where you stopped trusting it. Feedback goes into
`validation/VALIDATION_LOG.md` as a public record of external review.

## Next — parametric foreground

The priority is parameterising a foreground and sweeping ranges quickly. USLCI-side parameters stay
pinned, and that pin is what makes the sweeps fast: with the background fixed, a foreground result is
linear in its exchange amounts, so each background activity's unit score is computed once and every
scenario after that is a dot product. Allow USLCI parameters in and each combination needs a real
solve at roughly ten seconds each.

1. Parameter declarations and formula-valued amounts in the foreground CSV. An `ast` evaluator
   restricted to arithmetic, a `parameter` exchange type, and every declared value recorded in the
   manifest.
2. A params file plus `--sweep`, so varying a value never means editing the inventory.
3. Precompute background unit scores and evaluate combinations as arithmetic. Do this when a sweep
   feels slow, not before.

Pinned: overriding a shipped USLCI process parameter, and propagating a change to an activity inside
a supply chain. Revisit only if a study needs it. Details and the measured shape of USLCI's 1,247
process parameters are in the devlog.

## Engine

**Hard-stop on ambiguous links** in `setup/03`, with an opt-in override, following the
`ALLOW_UNIT_PASSTHROUGH` pattern. An ambiguous link means the resolver had candidates and declined to
choose, which is a build defect rather than a property of the data. Both builds pass clean today, so
this can land without breaking anything.

**Run-time guard in `general/04`** for a target whose solved chain contains ambiguous links. Covers a
database built with the override above, or built before it existed.

**Explain a zero instead of printing it bare.** When a result is 0.0 across all ten categories and
the target has non-zero inputs, say that every input is a cutoff. On the current full build this
fires on 10 of 1,455 processes; the other 84 zeros are correct, and 82 of those have no exchanges at
all. Not a stop — those 10 are right answers, badly presented.

**Uncertainty is dropped on import.** 5,740 exchanges carry distributions that the parser discards
without recording it. No validated case depends on one, so this is a capability gap rather than a
wrong number, but it is currently undocumented as well as unimplemented.

**Foreground-only mode in `general/04`.** `--mode {direct,full_chain}` exists only in
`validation/05`, so a practitioner cannot compute foreground-only impacts through the general runner.
Either expose the flag or state that the harness is the only place it belongs.

**Magnitude guard for the comparison chart.** `chart_units.py` refuses to compare incommensurable
*units*; nothing guards an incommensurable *magnitude*. A near-zero baseline once produced a y-axis
reading fifty million percent, with no warning.

## Validation

**Direct-mode reference exports for cement and corn.** Petroleum is the only process with a
background whose foreground/background split is verified directly. The split is lopsided and
case-specific — petroleum's own emissions are under 0.01% of its full-chain result, cement's run to
76% — so a disagreement in cement could not currently be attributed to foreground versus solve.
Needs kg-basis openLCA exports with a `Direct impact contributions` sheet.

**Decide whether full-database mode is supported or stays opt-in.** Today it is an env var and a
flag, absent from the README's feature list, with the vintage forced by whichever full zip is
present.

---

## Not planned

**Economic-allocation arithmetic cannot be validated against USLCI.** All 29 processes declaring
`ECONOMIC_ALLOCATION` use factors of exactly 0.0 or 1.0, so no process exercises a real economic
split. The locked corn case already covers the degenerate path. This is a scope limitation to state,
not a gap to close.

**Under-reporting**, meaning a non-zero result that is too low because part of its inventory was cut.
That is endemic to LCA rather than a defect here, and the per-result completeness block in the run
manifest is the right instrument for it.
