#!/usr/bin/env python3
"""
olca_library.py
---------------
Standalone reader for openLCA *library* packages (the matrix/.npz format that
Federal LCA Commons ships for pre-aggregated datasets such as the US Electricity
Baseline). General-purpose: it knows nothing about any specific dataset.

Why this exists
---------------
openLCA libraries are distributed as a zip of sparse matrices + a protobuf index,
NOT as JSON-LD. `setup/03_import_uslci.py` parses JSON-LD exchange lists and cannot read
this format. This module decodes the library so its pre-solved background can be
injected into brightway as aggregated activities (one column of M = one process's
full cradle-to-gate elementary-flow vector), mirroring how openLCA consumes a
library at calculation time.

Library layout (zip entries)
----------------------------
  A.npz        technosphere matrix            (n_proc x n_proc, sparse)
  B.npz        intervention/biosphere matrix  (n_flow x n_proc, sparse)
  INV.npy      A^-1 (scaling / Leontief inverse, n_proc x n_proc)
  M.npy        cumulative inventory = B @ A^-1 (n_flow x n_proc)  <-- the payload
  index_A.bin  protobuf: ordered process index (col -> process + ref product)
  index_B.bin  protobuf: ordered flow index    (row -> elementary flow + location)
  library.json {name, isRegionalized}
  meta.zip     JSON-LD descriptors (processes/flows/flow_properties/unit_groups)

protobuf schemas (reverse-engineered; proto3 omits zero-valued scalars)
-----------------------------------------------------------------------
  index_A: repeated Entry f1.  Entry { int32 col=1; Ref process=2; Ref refFlow=3 }
  index_B: repeated Entry f1.  Entry { int32 row=1; Ref flow=2; Ref location=3; int32 locIdx=4 }
  Ref    { string id=1; string name=2; string category=3; string typeOrActor=4; string unit=5 }

This module has NO brightway dependency (kept deliberately pure). Brightway
injection lives in a separate module that consumes `read_library()`.
"""

from __future__ import annotations

import io
import os
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import scipy.sparse as sp


# =============================================================================
# Minimal protobuf wire decoder (no external proto dependency)
# =============================================================================
def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = 0
    result = 0
    while True:
        b = buf[i]
        result |= (b & 0x7F) << shift
        i += 1
        if not b & 0x80:
            return result, i
        shift += 7


def _iter_fields(buf: bytes):
    """Yield (field_number, wire_type, value) for a protobuf message.
    value is int (varint), bytes (length-delimited), or raw bytes (fixed)."""
    i = 0
    n = len(buf)
    while i < n:
        tag, i = _read_varint(buf, i)
        fnum, wtype = tag >> 3, tag & 7
        if wtype == 0:
            v, i = _read_varint(buf, i)
            yield fnum, wtype, v
        elif wtype == 2:
            ln, i = _read_varint(buf, i)
            yield fnum, wtype, buf[i : i + ln]
            i += ln
        elif wtype == 5:
            yield fnum, wtype, buf[i : i + 4]
            i += 4
        elif wtype == 1:
            yield fnum, wtype, buf[i : i + 8]
            i += 8
        else:
            raise ValueError(f"unsupported wire type {wtype} at byte {i}")


def _decode_ref(buf: bytes) -> dict:
    """Decode a Ref sub-message {id, name, category, typeOrActor, unit}."""
    out = {}
    names = {1: "id", 2: "name", 3: "category", 4: "type", 5: "unit"}
    for fnum, wtype, val in _iter_fields(buf):
        if wtype == 2 and fnum in names:
            out[names[fnum]] = val.decode("utf-8")
    return out


def _decode_index(raw: bytes, expected_rows: int, kind: str) -> list[dict]:
    """Decode index_A or index_B into an ordered list keyed by matrix position.

    Returns a list where element[k] describes matrix position k. The entry's
    explicit position field is honored (proto3 omits it when 0); we assert it
    matches enumeration order so a silent reorder can never corrupt the mapping.
    """
    entries = [v for fnum, wt, v in _iter_fields(raw) if fnum == 1 and wt == 2]
    if len(entries) != expected_rows:
        raise ValueError(
            f"{kind}: decoded {len(entries)} entries but matrix has {expected_rows} "
            f"rows/cols — index/matrix mismatch."
        )
    result: list[dict] = []
    for enum_pos, ent in enumerate(entries):
        pos = 0
        primary = {}      # process (index_A) or flow (index_B)
        secondary = {}    # ref product (index_A) or location (index_B)
        for fnum, wtype, val in _iter_fields(ent):
            if fnum == 1 and wtype == 0:
                pos = val
            elif fnum == 2 and wtype == 2:
                primary = _decode_ref(val)
            elif fnum == 3 and wtype == 2:
                secondary = _decode_ref(val)
        if pos != enum_pos:
            raise ValueError(
                f"{kind}: entry order {enum_pos} != stored position {pos} — "
                f"matrix alignment cannot be trusted."
            )
        result.append({"pos": enum_pos, "primary": primary, "secondary": secondary})
    return result


# =============================================================================
# Library container
# =============================================================================
@dataclass
class OlcaLibrary:
    name: str
    is_regionalized: bool
    A: sp.csc_matrix                 # technosphere (n_proc x n_proc)
    B: sp.csc_matrix                 # biosphere   (n_flow x n_proc)
    M: np.ndarray                    # cumulative inventory B @ A^-1 (n_flow x n_proc)
    INV: np.ndarray                  # A^-1 (n_proc x n_proc)
    processes: list[dict]            # col -> {pos, primary=process ref, secondary=ref-product ref}
    flows: list[dict]                # row -> {pos, primary=flow ref, secondary=location ref}
    _proc_by_uuid: dict = field(default_factory=dict)

    @property
    def n_proc(self) -> int:
        return self.A.shape[0]

    @property
    def n_flow(self) -> int:
        return self.B.shape[0]

    def process_col(self, process_uuid: str) -> int:
        """Matrix column index for a process UUID (raises if absent)."""
        if process_uuid not in self._proc_by_uuid:
            raise KeyError(f"process {process_uuid} not in library '{self.name}'")
        return self._proc_by_uuid[process_uuid]

    def cumulative_inventory(self, process_uuid: str) -> dict:
        """Aggregated cradle-to-gate inventory for a process, as
        {flow_uuid: amount} summed over locations (regionalized rows collapsed
        to national flows). Zero entries dropped."""
        col = self.process_col(process_uuid)
        vec = self.M[:, col]
        out: dict[str, float] = {}
        for row, amount in zip(np.nonzero(vec)[0], vec[np.nonzero(vec)[0]]):
            fid = self.flows[row]["primary"].get("id")
            if fid is None:
                continue
            out[fid] = out.get(fid, 0.0) + float(amount)
        return {k: v for k, v in out.items() if v != 0.0}

    def elementary_inventory(self, process_uuid: str) -> tuple[dict, list]:
        """Cumulative inventory restricted to ELEMENTARY_FLOW rows (the rows a
        biosphere exchange can represent). Library M rows also include
        PRODUCT_FLOW / WASTE_FLOW entries -- commodities and wastes the library
        itself has no internal producer/consumer for, i.e. the library's OWN
        cutoffs, not elementary flows. Those are excluded here and returned in
        `dropped` (one dict per row, with `amount`) so a caller can log them
        rather than silently lose them.

        Returns (flows, dropped) where flows = {flow_uuid: amount}, summed over
        locations same as `cumulative_inventory`. No FEDEFL knowledge here --
        that check belongs to the (brightway-dependent) caller.
        """
        col = self.process_col(process_uuid)
        vec = self.M[:, col]
        flows: dict[str, float] = {}
        dropped: list[dict] = []
        for row, amount in zip(np.nonzero(vec)[0], vec[np.nonzero(vec)[0]]):
            amount = float(amount)
            if amount == 0.0:
                continue
            f = self.flows[row]["primary"]
            fid = f.get("id")
            if fid is None:
                continue
            if f.get("type") != "ELEMENTARY_FLOW":
                dropped.append({**f, "amount": amount})
                continue
            flows[fid] = flows.get(fid, 0.0) + amount
        return {k: v for k, v in flows.items() if v != 0.0}, dropped


# =============================================================================
# Reader
# =============================================================================
def read_library(zip_path: str | Path) -> OlcaLibrary:
    zip_path = Path(zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        required = {"A.npz", "B.npz", "M.npy", "index_A.bin", "index_B.bin"}
        missing = required - names
        if missing:
            raise ValueError(
                f"{zip_path.name} is not a complete openLCA library — missing {missing}"
            )
        A = sp.load_npz(io.BytesIO(zf.read("A.npz"))).tocsc()
        B = sp.load_npz(io.BytesIO(zf.read("B.npz"))).tocsc()
        M = np.load(io.BytesIO(zf.read("M.npy")), allow_pickle=True)
        INV = (
            np.load(io.BytesIO(zf.read("INV.npy")), allow_pickle=True)
            if "INV.npy" in names
            else None
        )
        lib_meta = {}
        if "library.json" in names:
            import json

            lib_meta = json.loads(zf.read("library.json"))
        procs = _decode_index(zf.read("index_A.bin"), A.shape[0], "index_A")
        flows = _decode_index(zf.read("index_B.bin"), B.shape[0], "index_B")

    proc_by_uuid = {}
    for p in procs:
        uid = p["primary"].get("id")
        if uid is not None:
            if uid in proc_by_uuid:
                raise ValueError(f"duplicate process UUID in index_A: {uid}")
            proc_by_uuid[uid] = p["pos"]

    return OlcaLibrary(
        name=lib_meta.get("name", zip_path.stem),
        is_regionalized=bool(lib_meta.get("isRegionalized", False)),
        A=A, B=B, M=M, INV=INV,
        processes=procs, flows=flows, _proc_by_uuid=proc_by_uuid,
    )


# =============================================================================
# Non-circular self-checks (validate the DECODE, not any LCA result)
# =============================================================================
def self_check(lib: OlcaLibrary) -> None:
    print(f"Library: {lib.name!r}  regionalized={lib.is_regionalized}")
    print(f"  A {lib.A.shape}  B {lib.B.shape}  M {lib.M.shape}")
    print(f"  processes decoded: {len(lib.processes)}  flows decoded: {len(lib.flows)}")

    # 1. Shapes agree (index lengths already asserted == matrix dims in _decode_index)
    assert lib.M.shape == (lib.n_flow, lib.n_proc), "M shape != (n_flow, n_proc)"

    # 2. Index round-trip: every process UUID resolves back to its own column.
    for p in lib.processes:
        uid = p["primary"].get("id")
        if uid and lib.process_col(uid) != p["pos"]:
            raise AssertionError(f"round-trip failed for {uid}")
    print("  [ok] index round-trip: all process UUIDs map to their own column")

    # 3. M == B @ A^-1 internal consistency (confirms M really is cumulative inv).
    if lib.INV is not None:
        # check a handful of columns to keep it cheap
        cols = np.linspace(0, lib.n_proc - 1, min(8, lib.n_proc)).astype(int)
        max_rel = 0.0
        for c in cols:
            recon = lib.B @ lib.INV[:, c]
            denom = np.abs(lib.M[:, c]).max() or 1.0
            max_rel = max(max_rel, float(np.abs(recon - lib.M[:, c]).max() / denom))
        print(f"  [ok] M == B*A^-1 internal consistency: max rel err {max_rel:.2e}")

    # 4. Flow refs look like elementary flows with units.
    elem = sum(1 for f in lib.flows if f["primary"].get("type") == "ELEMENTARY_FLOW")
    print(f"  [info] flows tagged ELEMENTARY_FLOW: {elem}/{len(lib.flows)}")

    # 5. Spot-report a known electricity column if present (descriptive only).
    print("  [info] sample processes:")
    for p in lib.processes[:3]:
        pr, rf = p["primary"], p["secondary"]
        print(f"     col{p['pos']:>3}  {pr.get('name','?')[:46]:46} -> "
              f"{rf.get('name','?')[:28]} [{rf.get('unit','?')}]")


if __name__ == "__main__":
    import argparse

    _default_root = os.environ.get(
        "SOURCE_DATA_DIR", str(Path(__file__).resolve().parent.parent / "source_data")
    )
    ap = argparse.ArgumentParser(description="Read/verify an openLCA library zip.")
    ap.add_argument(
        "library",
        nargs="?",
        default=_default_root + "/U.S._electricity_baseline_v1.2025-06.0_from_olca",
        help="path to an openLCA library zip (default: $SOURCE_DATA_DIR/U.S._electricity_baseline_v1.2025-06.0_from_olca)",
    )
    args = ap.parse_args()
    lib = read_library(args.library)
    self_check(lib)
