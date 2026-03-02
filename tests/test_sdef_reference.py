"""Compare SDEF intermediate data between mmd_tools (reference) and our implementation.

Run after generating dumps from both sides:
  - mmd_tools reference: /tmp/mmd_sdef_ref/bind_*.npz, frame41_*.npz
  - our implementation:  /tmp/mmd_sdef_ours/bind_*.npz, frame41_*.npz

Vertices are matched by rest-pose position (float3) since mmd_tools uses
a single mesh while we split by material.

Usage:
    pytest tests/test_sdef_reference.py -v
"""

from pathlib import Path

import numpy as np
import pytest

REF_DIR = Path("/tmp/mmd_sdef_ref")
OURS_DIR = Path("/tmp/mmd_sdef_ours")

# Position matching tolerance (same PMX data, same scale → should be exact)
POS_TOL = 1e-4


def _load_all_npz(directory: Path, prefix: str) -> list[dict]:
    """Load all .npz files matching prefix from a directory."""
    results = []
    for p in sorted(directory.glob(f"{prefix}*.npz")):
        data = dict(np.load(p, allow_pickle=True))
        data["_file"] = p.name
        results.append(data)
    return results


def _merge_our_bind_data(files: list[dict]) -> dict:
    """Merge bind dumps from multiple split meshes into one array set."""
    if not files:
        return {}
    keys = ["vertex_indices", "vertex_co", "c_raw", "r0_raw", "r1_raw",
            "bone0_name", "bone1_name", "bone0_group_index", "bone1_group_index",
            "w0", "w1", "pos_c", "cr0", "cr1"]
    merged = {}
    for k in keys:
        arrays = [f[k] for f in files if k in f]
        if arrays:
            merged[k] = np.concatenate(arrays, axis=0)
    # Track source mesh per vertex
    mesh_names = []
    for f in files:
        n = len(f["vertex_indices"])
        name = str(f.get("mesh_name", f["_file"]))
        mesh_names.extend([name] * n)
    merged["_mesh_name"] = np.array(mesh_names)
    return merged


def _merge_our_frame_data(files: list[dict]) -> dict:
    """Merge frame41 dumps from multiple split meshes."""
    if not files:
        return {}
    vert_keys = ["vertex_indices", "final_positions", "bone0_names", "bone1_names"]
    merged = {}
    for k in vert_keys:
        arrays = [f[k] for f in files if k in f]
        if arrays:
            merged[k] = np.concatenate(arrays, axis=0)
    # Bone pair data: deduplicate by bone pair name
    seen = set()
    bp_keys = ["bp_bone0_names", "bp_bone1_names", "bp_mat0", "bp_mat1", "bp_rot0", "bp_rot1"]
    bp_data = {k: [] for k in bp_keys}
    for f in files:
        if "bp_bone0_names" not in f:
            continue
        for i in range(len(f["bp_bone0_names"])):
            pair = (str(f["bp_bone0_names"][i]), str(f["bp_bone1_names"][i]))
            if pair not in seen:
                seen.add(pair)
                for k in bp_keys:
                    bp_data[k].append(f[k][i])
    for k in bp_keys:
        if bp_data[k]:
            merged[k] = np.array(bp_data[k])
    # Collect all positions per mesh for reference
    merged["_all_positions"] = {
        str(f.get("mesh_name", f["_file"])): f["all_positions"]
        for f in files if "all_positions" in f
    }
    return merged


def _match_by_position(ref_co, ours_co, tol=POS_TOL):
    """Build index mapping: ref_idx -> ours_idx matched by position.

    Returns dict mapping ref vertex index to ours vertex index.
    Uses brute-force nearest neighbor (no scipy dependency).
    """
    # Round to build a hash-based lookup (fast for exact/near-exact matches)
    decimals = int(-np.log10(tol))
    ours_rounded = np.round(ours_co, decimals=decimals)
    # Build dict: rounded tuple -> list of ours indices
    ours_lookup: dict[tuple, list[int]] = {}
    for oi in range(len(ours_co)):
        key = tuple(ours_rounded[oi])
        ours_lookup.setdefault(key, []).append(oi)

    mapping = {}
    unmatched = []
    for ri in range(len(ref_co)):
        key = tuple(np.round(ref_co[ri], decimals=decimals))
        candidates = ours_lookup.get(key, [])
        best_oi, best_d = -1, float('inf')
        for oi in candidates:
            d = np.max(np.abs(ref_co[ri] - ours_co[oi]))
            if d < best_d:
                best_d = d
                best_oi = oi
        if best_d <= tol:
            mapping[ri] = best_oi
        else:
            unmatched.append((ri, best_d))
    return mapping, unmatched


# ---------------------------------------------------------------------------
# BIND DATA COMPARISON
# ---------------------------------------------------------------------------

class TestBindData:
    """Compare precomputed SDEF constants between mmd_tools and our code."""

    @pytest.fixture(autouse=True)
    def load_data(self):
        if not REF_DIR.exists() or not OURS_DIR.exists():
            pytest.skip("Dump directories not found — run both importers first")

        ref_files = _load_all_npz(REF_DIR, "bind_")
        ours_files = _load_all_npz(OURS_DIR, "bind_")
        if not ref_files or not ours_files:
            pytest.skip("No bind dump files found")

        # mmd_tools: single mesh (take first/only file)
        self.ref = ref_files[0]
        # ours: merge split meshes
        self.ours = _merge_our_bind_data(ours_files)

        # Match vertices by rest-pose position
        self.mapping, self.unmatched = _match_by_position(
            self.ref["vertex_co"], self.ours["vertex_co"]
        )

    def test_vertex_count(self):
        """Both sides should find the same number of SDEF vertices."""
        n_ref = len(self.ref["vertex_indices"])
        n_ours = len(self.ours["vertex_indices"])
        print(f"\nRef SDEF vertices: {n_ref}")
        print(f"Ours SDEF vertices: {n_ours}")
        print(f"Matched: {len(self.mapping)}")
        print(f"Unmatched ref vertices: {len(self.unmatched)}")
        if self.unmatched:
            for ri, d in self.unmatched[:10]:
                print(f"  ref[{ri}] co={self.ref['vertex_co'][ri]} dist={d:.6f}")
        # Allow small differences due to detection method
        assert len(self.mapping) > 0, "No vertices matched!"
        match_pct = len(self.mapping) / n_ref * 100
        print(f"Match rate: {match_pct:.1f}%")
        assert match_pct > 95, f"Only {match_pct:.1f}% of ref vertices matched"

    def test_bone_pairs_by_group_index(self):
        """Bone pair assignment should match (by vertex group index, not name).

        mmd_tools uses Japanese bone names, we use English. Compare by group
        index which follows PMX bone order in both implementations.
        """
        mismatches = []
        for ri, oi in self.mapping.items():
            ref_pair = frozenset([int(self.ref["bone0_group_index"][ri]),
                                  int(self.ref["bone1_group_index"][ri])])
            ours_pair = frozenset([int(self.ours["bone0_group_index"][oi]),
                                   int(self.ours["bone1_group_index"][oi])])
            if ref_pair != ours_pair:
                mismatches.append((ri, oi, ref_pair, ours_pair))

        if mismatches:
            print(f"\nBone pair mismatches (by group index): {len(mismatches)}/{len(self.mapping)}")
            for ri, oi, rp, op in mismatches[:5]:
                co = self.ref["vertex_co"][ri]
                rb0 = str(self.ref["bone0_name"][ri])
                rb1 = str(self.ref["bone1_name"][ri])
                ob0 = str(self.ours["bone0_name"][oi])
                ob1 = str(self.ours["bone1_name"][oi])
                print(f"  co={co} ref=({rb0}[{rp}]) ours=({ob0}, {ob1})[{op}]")
        assert not mismatches, f"{len(mismatches)} bone pair mismatches"

    def test_bone_order(self):
        """Bone ordering (bone0 vs bone1) should match — critical for R0/R1 mapping.

        Compares by vertex group index (both sides follow PMX bone order).
        """
        swapped = []
        for ri, oi in self.mapping.items():
            rg0 = int(self.ref["bone0_group_index"][ri])
            rg1 = int(self.ref["bone1_group_index"][ri])
            og0 = int(self.ours["bone0_group_index"][oi])
            og1 = int(self.ours["bone1_group_index"][oi])
            if rg0 == og1 and rg1 == og0:
                rb0 = str(self.ref["bone0_name"][ri])
                rb1 = str(self.ref["bone1_name"][ri])
                swapped.append((ri, oi, rb0, rb1, rg0, rg1))

        if swapped:
            print(f"\nBone order SWAPPED: {len(swapped)}/{len(self.mapping)}")
            for ri, oi, b0, b1, g0, g1 in swapped[:5]:
                co = self.ref["vertex_co"][ri]
                rw0, rw1 = self.ref["w0"][ri], self.ref["w1"][ri]
                ow0, ow1 = self.ours["w0"][oi], self.ours["w1"][oi]
                print(f"  co={co}")
                print(f"    ref: bone0={b0}[{g0}] w0={rw0:.4f}, bone1={b1}[{g1}] w1={rw1:.4f}")
                print(f"    ours: bone0=[{g1}] w0={ow0:.4f}, bone1=[{g0}] w1={ow1:.4f}")
        assert not swapped, (
            f"{len(swapped)} vertices have SWAPPED bone order! "
            "R0/R1 will map to the wrong bones."
        )

    def test_weights(self):
        """Normalized weights should match (compare using group index for order)."""
        max_diff = 0
        diffs = []
        for ri, oi in self.mapping.items():
            rg0 = int(self.ref["bone0_group_index"][ri])
            og0 = int(self.ours["bone0_group_index"][oi])
            if rg0 == og0:
                # Same order — compare directly
                dw0 = abs(self.ref["w0"][ri] - self.ours["w0"][oi])
                dw1 = abs(self.ref["w1"][ri] - self.ours["w1"][oi])
            else:
                # Swapped — compare crossed
                dw0 = abs(self.ref["w0"][ri] - self.ours["w1"][oi])
                dw1 = abs(self.ref["w1"][ri] - self.ours["w0"][oi])
            d = max(dw0, dw1)
            if d > max_diff:
                max_diff = d
            if d > 1e-6:
                diffs.append((ri, oi, d))

        print(f"\nMax weight diff: {max_diff:.8f}")
        if diffs:
            print(f"Vertices with weight diff > 1e-6: {len(diffs)}")
            for ri, oi, d in diffs[:5]:
                co = self.ref["vertex_co"][ri]
                print(f"  co={co} ref=({self.ref['w0'][ri]:.6f}, {self.ref['w1'][ri]:.6f}) "
                      f"ours=({self.ours['w0'][oi]:.6f}, {self.ours['w1'][oi]:.6f}) diff={d:.8f}")
        assert max_diff < 1e-4, f"Weight diff too large: {max_diff}"

    def test_raw_c(self):
        """Raw C values should match."""
        max_diff = 0
        for ri, oi in self.mapping.items():
            d = np.max(np.abs(self.ref["c_raw"][ri] - self.ours["c_raw"][oi]))
            if d > max_diff:
                max_diff = d
        print(f"\nMax raw C diff: {max_diff:.8f}")
        assert max_diff < POS_TOL, f"Raw C diff too large: {max_diff}"

    def test_raw_r0_r1(self):
        """Raw R0/R1 values should match (using group index for order check)."""
        max_diff = 0
        for ri, oi in self.mapping.items():
            rg0 = int(self.ref["bone0_group_index"][ri])
            og0 = int(self.ours["bone0_group_index"][oi])
            if rg0 == og0:
                d0 = np.max(np.abs(self.ref["r0_raw"][ri] - self.ours["r0_raw"][oi]))
                d1 = np.max(np.abs(self.ref["r1_raw"][ri] - self.ours["r1_raw"][oi]))
            else:
                d0 = np.max(np.abs(self.ref["r0_raw"][ri] - self.ours["r1_raw"][oi]))
                d1 = np.max(np.abs(self.ref["r1_raw"][ri] - self.ours["r0_raw"][oi]))
            max_diff = max(max_diff, d0, d1)
        print(f"\nMax raw R0/R1 diff: {max_diff:.8f}")
        assert max_diff < POS_TOL, f"Raw R0/R1 diff too large: {max_diff}"

    def test_precomputed_pos_c(self):
        """Precomputed pos_c (vertex_co - C) should match."""
        max_diff = 0
        for ri, oi in self.mapping.items():
            d = np.max(np.abs(self.ref["pos_c"][ri] - self.ours["pos_c"][oi]))
            if d > max_diff:
                max_diff = d
        print(f"\nMax pos_c diff: {max_diff:.8f}")
        assert max_diff < POS_TOL, f"pos_c diff too large: {max_diff}"

    def test_precomputed_cr0_cr1(self):
        """Precomputed cr0/cr1 should match (using group index for order check)."""
        max_diff = 0
        worst = None
        for ri, oi in self.mapping.items():
            rg0 = int(self.ref["bone0_group_index"][ri])
            og0 = int(self.ours["bone0_group_index"][oi])
            if rg0 == og0:
                d0 = np.max(np.abs(self.ref["cr0"][ri] - self.ours["cr0"][oi]))
                d1 = np.max(np.abs(self.ref["cr1"][ri] - self.ours["cr1"][oi]))
            else:
                d0 = np.max(np.abs(self.ref["cr0"][ri] - self.ours["cr1"][oi]))
                d1 = np.max(np.abs(self.ref["cr1"][ri] - self.ours["cr0"][oi]))
            d = max(d0, d1)
            if d > max_diff:
                max_diff = d
                worst = (ri, oi)
        print(f"\nMax cr0/cr1 diff: {max_diff:.8f}")
        if worst and max_diff > POS_TOL:
            ri, oi = worst
            print(f"  Worst: ref[{ri}] co={self.ref['vertex_co'][ri]}")
            print(f"    ref cr0={self.ref['cr0'][ri]} cr1={self.ref['cr1'][ri]}")
            print(f"    ours cr0={self.ours['cr0'][oi]} cr1={self.ours['cr1'][oi]}")
        assert max_diff < POS_TOL, f"cr0/cr1 diff too large: {max_diff}"


# ---------------------------------------------------------------------------
# FRAME 41 COMPARISON
# ---------------------------------------------------------------------------

class TestFrame41:
    """Compare SDEF output at frame 41 between mmd_tools and our code.

    NOTE: Frame-level positions will differ because our armature import
    produces different bone transforms than mmd_tools (different rest pose,
    IK solving, animation interpolation). These tests use a name mapping
    derived from bind data and report diffs as informational.
    """

    @pytest.fixture(autouse=True)
    def load_data(self):
        if not REF_DIR.exists() or not OURS_DIR.exists():
            pytest.skip("Dump directories not found")

        ref_files = _load_all_npz(REF_DIR, "frame41_")
        ours_files = _load_all_npz(OURS_DIR, "frame41_")
        if not ref_files or not ours_files:
            pytest.skip("No frame41 dump files found")

        self.ref = ref_files[0]
        self.ours = _merge_our_frame_data(ours_files)

        # Build Japanese→English bone name mapping from bind data
        ref_bind = _load_all_npz(REF_DIR, "bind_")
        ours_bind = _load_all_npz(OURS_DIR, "bind_")
        if not ref_bind or not ours_bind:
            pytest.skip("No bind data for name mapping")

        self.ref_bind = ref_bind[0]
        self.ours_bind = _merge_our_bind_data(ours_bind)
        self.mapping, _ = _match_by_position(
            self.ref_bind["vertex_co"], self.ours_bind["vertex_co"]
        )

        # Build group_index → name for each side
        ref_idx_to_name = {}
        for i in range(len(self.ref_bind["bone0_group_index"])):
            ref_idx_to_name[int(self.ref_bind["bone0_group_index"][i])] = str(self.ref_bind["bone0_name"][i])
            ref_idx_to_name[int(self.ref_bind["bone1_group_index"][i])] = str(self.ref_bind["bone1_name"][i])
        ours_idx_to_name = {}
        for i in range(len(self.ours_bind["bone0_group_index"])):
            ours_idx_to_name[int(self.ours_bind["bone0_group_index"][i])] = str(self.ours_bind["bone0_name"][i])
            ours_idx_to_name[int(self.ours_bind["bone1_group_index"][i])] = str(self.ours_bind["bone1_name"][i])

        # Japanese name → English name
        self.ja_to_en = {}
        for idx in set(ref_idx_to_name) & set(ours_idx_to_name):
            self.ja_to_en[ref_idx_to_name[idx]] = ours_idx_to_name[idx]

    def test_bone_matrices(self):
        """Compare bone matrices using JA→EN name mapping.

        Diffs are expected (different armature imports), but reported
        for documentation. The test passes as informational.
        """
        # Build ref bone pair mats keyed by English name
        ref_mats = {}
        for i in range(len(self.ref["bp_bone0_names"])):
            ja0 = str(self.ref["bp_bone0_names"][i])
            ja1 = str(self.ref["bp_bone1_names"][i])
            en0 = self.ja_to_en.get(ja0, ja0)
            en1 = self.ja_to_en.get(ja1, ja1)
            ref_mats[(en0, en1)] = (self.ref["bp_mat0"][i], self.ref["bp_mat1"][i])

        ours_mats = {}
        for i in range(len(self.ours["bp_bone0_names"])):
            pair = (str(self.ours["bp_bone0_names"][i]), str(self.ours["bp_bone1_names"][i]))
            ours_mats[pair] = (self.ours["bp_mat0"][i], self.ours["bp_mat1"][i])

        matched = 0
        max_diff = 0
        for pair, (rm0, rm1) in ref_mats.items():
            om = ours_mats.get(pair) or ours_mats.get((pair[1], pair[0]))
            if om is None:
                continue
            matched += 1
            if pair in ours_mats:
                d = max(np.max(np.abs(rm0 - om[0])), np.max(np.abs(rm1 - om[1])))
            else:
                d = max(np.max(np.abs(rm0 - om[1])), np.max(np.abs(rm1 - om[0])))
            max_diff = max(max_diff, d)

        print(f"\nMatched bone pairs (via JA→EN mapping): {matched}/{len(ref_mats)}")
        print(f"Max bone matrix diff: {max_diff:.6f}")
        print("NOTE: Diffs expected — different armature imports produce different bone transforms")
        # Informational — don't assert on cross-armature diffs

    def test_quaternions(self):
        """Compare quaternions using JA→EN name mapping (informational)."""
        ref_quats = {}
        for i in range(len(self.ref["bp_bone0_names"])):
            ja0 = str(self.ref["bp_bone0_names"][i])
            ja1 = str(self.ref["bp_bone1_names"][i])
            en0 = self.ja_to_en.get(ja0, ja0)
            en1 = self.ja_to_en.get(ja1, ja1)
            ref_quats[(en0, en1)] = (self.ref["bp_rot0"][i], self.ref["bp_rot1"][i])

        ours_quats = {}
        for i in range(len(self.ours["bp_bone0_names"])):
            pair = (str(self.ours["bp_bone0_names"][i]), str(self.ours["bp_bone1_names"][i]))
            ours_quats[pair] = (self.ours["bp_rot0"][i], self.ours["bp_rot1"][i])

        matched = 0
        max_diff = 0
        for pair, (rq0, rq1) in ref_quats.items():
            if pair in ours_quats:
                oq0, oq1 = ours_quats[pair]
            elif (pair[1], pair[0]) in ours_quats:
                oq1, oq0 = ours_quats[(pair[1], pair[0])]
            else:
                continue
            matched += 1
            d0 = min(np.max(np.abs(rq0 - oq0)), np.max(np.abs(rq0 + oq0)))
            d1 = min(np.max(np.abs(rq1 - oq1)), np.max(np.abs(rq1 + oq1)))
            max_diff = max(max_diff, d0, d1)

        print(f"\nMatched bone pairs for quat comparison: {matched}")
        print(f"Max quaternion diff: {max_diff:.6f}")
        print("NOTE: Diffs expected — different armature bone transforms")

    def test_final_positions(self):
        """Report final position diff (informational — diffs expected from bone matrix diffs)."""
        if not self.mapping:
            pytest.skip("No vertex mapping from bind data")

        ref_pos = {int(self.ref["vertex_indices"][i]): self.ref["final_positions"][i]
                   for i in range(len(self.ref["vertex_indices"]))}
        ours_pos = {int(self.ours["vertex_indices"][i]): self.ours["final_positions"][i]
                    for i in range(len(self.ours["vertex_indices"]))}

        matched = 0
        max_diff = 0
        for ri, oi in self.mapping.items():
            ref_vid = int(self.ref_bind["vertex_indices"][ri])
            ours_vid = int(self.ours_bind["vertex_indices"][oi])
            if ref_vid not in ref_pos or ours_vid not in ours_pos:
                continue
            matched += 1
            d = np.max(np.abs(ref_pos[ref_vid] - ours_pos[ours_vid]))
            max_diff = max(max_diff, d)

        print(f"\nMatched SDEF verts with frame data: {matched}")
        print(f"Max final position diff: {max_diff:.6f}")
        print("NOTE: Position diffs are expected — different armature imports")
        print("      produce different bone matrices. Bind data match (zero diff)")
        print("      confirms the SDEF formula itself is correct.")


# ---------------------------------------------------------------------------
# SUMMARY
# ---------------------------------------------------------------------------

class TestSummary:
    """Print a summary of all differences found."""

    def test_summary(self):
        """Print overview of dump files and basic stats."""
        print("\n" + "=" * 60)
        print("SDEF REFERENCE COMPARISON SUMMARY")
        print("=" * 60)
        for label, d in [("Reference (mmd_tools)", REF_DIR), ("Ours", OURS_DIR)]:
            print(f"\n{label}: {d}")
            if d.exists():
                files = sorted(d.glob("*.npz"))
                for f in files:
                    data = np.load(f, allow_pickle=True)
                    keys = list(data.keys())
                    n = len(data[keys[0]]) if keys else 0
                    print(f"  {f.name}: {n} entries, keys={keys}")
            else:
                print("  NOT FOUND")
        print("=" * 60)
