"""Electricity-baseline vintage detection for setup/03b.

Pure module (no brightway, no zipfile) so it unit-tests without a built DB or
the real bundles — same pattern as setup/allocation.py and general/run_manifest.py.

Why this exists: the US-average grid node is named identically across baseline
releases but carries a different UUID per vintage. A build injects exactly ONE
vintage, and picking the wrong one silently compares a bundle against a grid it
never referenced. The bundles themselves already say which vintage they want —
in their `defaultProvider` references — so 03b can read that instead of making
the operator know it.

**Presence is not the test.** Bundles routinely cite the *other* vintage's node
a handful of times: each of the four locked 2025 bundles references the 2026
UUID exactly once against ~80 references to its own. A detector that asked "does
this bundle mention the 2026 node?" would answer yes for every bundle in the
repo. The vintage cited by the most processes wins.
"""


def classify_bundle(external_providers, grid_uuids):
    """Which baseline vintage does ONE bundle's grid references point at?

    `external_providers` is `find_external_providers()` output — a dict of
    provider_uuid -> {..., "referenced_by": set_of_process_uuids}.
    `grid_uuids` maps vintage -> that release's US-average grid process UUID
    (owned by 03b's VINTAGES table, passed in so this module holds no copy).

    Returns {"vintage": str | None, "counts": {vintage: n}, "reason": str}.
    `vintage` is None when nothing can be concluded:
      - no grid node referenced at all (a foreground-only bundle such as steel
        billets, which has zero external providers), or
      - two vintages referenced equally often, which USLCI data has not
        produced — treated as undecidable rather than guessed.
    """
    counts = {
        vintage: len(external_providers.get(uuid, {}).get("referenced_by", ()))
        for vintage, uuid in grid_uuids.items()
    }
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

    if not ranked or ranked[0][1] == 0:
        return {"vintage": None, "counts": counts,
                "reason": "references no electricity-baseline grid node"}
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return {"vintage": None, "counts": counts,
                "reason": f"ties between {ranked[0][0]} and {ranked[1][0]} "
                          f"at {ranked[0][1]} reference(s) each"}
    return {"vintage": ranked[0][0], "counts": counts,
            "reason": f"{ranked[0][1]} reference(s) vs "
                      f"{', '.join(f'{v}={n}' for v, n in ranked[1:])}"}


def decide_vintage(bundle_verdicts, explicit=None, default=None):
    """Pick the one vintage this build should inject.

    `bundle_verdicts` maps a bundle label -> `classify_bundle()` result.
    `explicit` is the operator's --vintage, which always wins.
    `default` is the fallback when nothing can be detected.

    Returns {"vintage", "source", "groups", "undecided", "conflict"}:
      source   — "explicit" | "detected" | "default"
      groups   — {vintage: [bundle labels]} for bundles that voted
      undecided— [(label, reason)] for bundles that couldn't vote (harmless;
                 a vintage-agnostic bundle constrains nothing)
      conflict — True when bundles disagree. The caller must stop: one build
                 injects one vintage, so a mixed set has no correct answer and
                 silently picking either would mis-link the other group.
    """
    groups = {}
    undecided = []
    for label, verdict in bundle_verdicts.items():
        if verdict["vintage"] is None:
            undecided.append((label, verdict["reason"]))
        else:
            groups.setdefault(verdict["vintage"], []).append(label)

    for bundles in groups.values():
        bundles.sort()

    if explicit is not None:
        return {"vintage": explicit, "source": "explicit", "groups": groups,
                "undecided": undecided, "conflict": False}
    if len(groups) == 1:
        return {"vintage": next(iter(groups)), "source": "detected",
                "groups": groups, "undecided": undecided, "conflict": False}
    if len(groups) == 0:
        return {"vintage": default, "source": "default", "groups": groups,
                "undecided": undecided, "conflict": False}
    return {"vintage": None, "source": "detected", "groups": groups,
            "undecided": undecided, "conflict": True}


def format_conflict(decision):
    """Operator-facing explanation of a mixed bundle set, with the fix."""
    lines = [
        "Bundles in this directory require DIFFERENT electricity-baseline vintages,",
        "and a build injects exactly one. Pick one explicitly with --vintage:",
        "",
    ]
    for vintage in sorted(decision["groups"]):
        lines.append(f"  --vintage {vintage}")
        for label in decision["groups"][vintage]:
            lines.append(f"      {label}")
    if decision["undecided"]:
        lines.append("")
        lines.append("  vintage-agnostic (build with either):")
        for label, reason in sorted(decision["undecided"]):
            lines.append(f"      {label}  ({reason})")
    lines += [
        "",
        "Validate each group on its own build: run 03b + 03 for one vintage, run the",
        "harness, then rebuild for the other. The harness skips cases whose expected",
        "vintage doesn't match the build and writes a vintage-tagged CSV, so the two",
        "sets of locked results cannot overwrite each other.",
    ]
    return "\n".join(lines)
