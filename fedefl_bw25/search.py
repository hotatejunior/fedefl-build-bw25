"""search.py — find a USLCI process by name instead of by UUID.

Every runner entry point takes a 36-character UUID, and a practitioner has a name.
Closing that gap needs more than a substring test, because USLCI names are long
(median 58 characters, up to 206) and put the product first with qualifiers after
a semicolon:

    Recycled postconsumer high-density polyethylene, HDPE, flake; at plant

Searching that for "hdpe flake" as a substring finds nothing — the two words are
eight characters apart with a comma between them. So a query is split into tokens
and a process matches when it contains ALL of them, anywhere, case-insensitively.
"hdpe flake" then matches exactly one process out of 1,455.

Ranking matters as much as matching: "diesel" hits 224 processes, and the ones a
practitioner means are the diesel *products*, not the 200-odd transport processes
that happen to be diesel powered. Since USLCI names lead with the product and push
qualifiers after the first semicolon, a match in that head segment ranks above a
match anywhere else. That alone puts "Diesel; combusted in industrial boiler"
above "Transport, combination truck; short-haul; diesel powered".

Matching and ranking are pure — `rank_entries` takes plain tuples, so it tests
without a built database. Only `search_processes` touches brightway.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass

from fedefl_bw25.config import PROJECT_NAME, USLCI_DB, USLCI_FULL_DB

# Marks the dedicated activity built for one causal co-product (see docs/ALLOCATION.md).
COPRODUCT_MARKER = "__co__"

DEFAULT_LIMIT = 20


@dataclass(frozen=True)
class Process:
    """One searchable activity, and every build it appears in.

    A UUID identifies the same process in both USLCI builds, so the two are one
    result with two databases rather than two results — otherwise every hit is
    listed twice and "235 matches" overstates what is there by half.
    """
    code: str
    name: str
    location: str
    unit: str
    databases: tuple = ()

    @property
    def is_coproduct(self) -> bool:
        return COPRODUCT_MARKER in self.code


def merge_by_code(processes):
    """Collapse the same process found in several builds into one record.

    Keyed by (code, name), not code alone: if the two builds disagree about a
    UUID's name they were built from different dataset versions, and collapsing
    them would hide that. Order of first appearance is preserved.
    """
    merged = {}
    for p in processes:
        key = (p.code, p.name)
        if key in merged:
            existing = merged[key]
            merged[key] = Process(
                existing.code, existing.name, existing.location, existing.unit,
                existing.databases + tuple(d for d in p.databases
                                           if d not in existing.databases))
        else:
            merged[key] = p
    return list(merged.values())


def tokenize(query: str) -> list:
    """A query into lowercase tokens. Every token must be found, anywhere."""
    return query.lower().split()


def head_segment(name: str) -> str:
    """The product name — everything before the first ';'.

    1,421 of 1,455 USLCI names are semicolon-delimited, product first. A match in
    this segment is a match on what the process *makes*, rather than on how it is
    powered or where it ships from.
    """
    return name.lower().split(";")[0]


def match_rank(name: str, tokens: list):
    """Sort key for a matching name, or None if it doesn't match.

    Ordered by decreasing confidence that this is the process the reader meant:
    an exact name, then all tokens in the product segment, then a name that starts
    with the query, then the shortest name — brevity stands in for specificity,
    since USLCI qualifies with extra clauses rather than different words.
    """
    low = name.lower()
    if not all(t in low for t in tokens):
        return None
    joined = " ".join(tokens)
    head = head_segment(name)
    return (
        0 if low == joined else 1,
        0 if all(t in head for t in tokens) else 1,
        0 if low.startswith(joined) else 1,
        len(name),
        low,                      # deterministic tie-break
    )


def rank_entries(entries, query: str, limit=DEFAULT_LIMIT):
    """Matching entries, best first. Returns (matches, total_matched).

    `entries` is any iterable of objects with a `.name`. `total_matched` is the
    count before `limit` is applied, so a caller can say how many were withheld.
    An empty query matches everything, which makes `--search ""` a plain listing.
    """
    tokens = tokenize(query)
    scored = []
    for entry in entries:
        rank = match_rank(entry.name, tokens) if tokens else (1, 1, 1, len(entry.name),
                                                              entry.name.lower())
        if rank is not None:
            scored.append((rank, entry))
    scored.sort(key=lambda pair: pair[0])
    matches = [entry for _rank, entry in scored]
    return (matches[:limit] if limit else matches), len(matches)


def suggest(query: str, entries, n=5, cutoff=0.6):
    """Near-miss product names, for when nothing matched at all.

    Compares against the product segment rather than the whole name: a typo is in
    what the reader was trying to name, and matching it against a 206-character
    string dilutes the ratio past any useful cutoff.
    """
    heads = {head_segment(e.name): e.name for e in entries}
    close = difflib.get_close_matches(query.lower(), list(heads), n=n, cutoff=cutoff)
    return [heads[c] for c in close]


def load_processes(database=None, project=PROJECT_NAME):
    """Every activity in the built USLCI databases, as `Process` records.

    Defaults to searching every USLCI build present, not just one. A process
    missing from the bundle build but present in the full one is a routine
    situation, and the search result is the natural place to learn it — each row
    names the database it is in, which is the `--database` to run it against.
    """
    import bw2data as bd

    bd.projects.set_current(project)
    wanted = [database] if database else [USLCI_DB, USLCI_FULL_DB]
    processes = []
    for db_name in wanted:
        if db_name not in bd.databases:
            continue
        processes += [
            Process(code=act["code"], name=act["name"],
                    location=act.get("location") or "", unit=act.get("unit") or "",
                    databases=(db_name,))
            for act in bd.Database(db_name)
        ]
    if not processes:
        raise RuntimeError(
            f"No USLCI database found in project '{project}'. "
            f"Run setup/03_import_uslci.py first."
        )
    return merge_by_code(processes)


def search_processes(query, *, database=None, limit=DEFAULT_LIMIT, project=PROJECT_NAME):
    """Search the built USLCI databases by name. Returns (matches, total_matched)."""
    return rank_entries(load_processes(database, project), query, limit=limit)


def format_matches(matches, total, query, all_processes=None) -> str:
    """The search result as console text. Returns it; printing is the caller's job."""
    if not matches:
        lines = [f"No process matches {query!r}."]
        near = suggest(query, all_processes) if all_processes else []
        if near:
            lines.append("\nDid you mean:")
            lines += [f"  {n}" for n in near]
        else:
            lines.append("  Try fewer or shorter words — every word must appear "
                         "somewhere in the name.")
        return "\n".join(lines)

    shown = f"{total} match(es)" if total == len(matches) else \
            f"{total} match(es), showing {len(matches)}"
    lines = [f"{shown} for {query!r}:", ""]
    builds = {m: ",".join(d.replace("uslci-", "") for d in m.databases) for m in matches}
    build_width = max(len(b) for b in builds.values())
    for m in matches:
        mark = "  [co-product]" if m.is_coproduct else ""
        lines.append(f"  {m.code}  {builds[m]:<{build_width}}  {m.location:<4} "
                     f"{m.unit:<6} {m.name}{mark}")
    if total > len(matches):
        lines.append(f"\n  ... {total - len(matches)} more. Add words to narrow, "
                     f"or raise --limit.")
    lines += ["", "Run the first with:", f"  {run_command(matches[0])}"]
    return "\n".join(lines)


def run_command(process) -> str:
    """The exact command that runs this process.

    `--database` is omitted when the process is in the default build, so the line
    is the shortest thing that works rather than the most explicit.
    """
    cmd = f"python general/04_run_lca.py --uuid {process.code}"
    if USLCI_DB not in process.databases:
        cmd += f" --database {process.databases[0]}"
    return cmd
