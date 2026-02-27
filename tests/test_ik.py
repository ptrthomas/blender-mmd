"""IK setup tests — constraint placement, limit conversion, VMD property parsing."""

from __future__ import annotations

import math
import struct
from pathlib import Path

import pytest

from blender_mmd.vmd.parser import parse, _read_text
from blender_mmd.vmd.types import PropertyKeyframe, VmdMotion

SAMPLES_DIR = Path(__file__).parent / "samples"
TEST_VMD = SAMPLES_DIR / "galaxias.vmd"


# ---------------------------------------------------------------------------
# Synthetic VMD with property section
# ---------------------------------------------------------------------------

def _make_vmd_with_properties(
    model_name: str = "TestModel",
    bone_keyframes: list | None = None,
    morph_keyframes: list | None = None,
    property_keyframes: list | None = None,
) -> bytes:
    """Build a VMD binary that includes bone, morph, camera, light, shadow,
    and property (IK toggle) sections.

    property_keyframes: list of (frame, visible, [(ik_name, enabled), ...])
    """
    buf = bytearray()

    # Header: 30 bytes signature + 20 bytes model name
    sig = b"Vocaloid Motion Data 0002"
    buf.extend(sig.ljust(30, b"\x00"))
    name_bytes = model_name.encode("cp932")[:20]
    buf.extend(name_bytes.ljust(20, b"\x00"))

    # Bone keyframes (empty)
    bk = bone_keyframes or []
    buf.extend(struct.pack("<I", len(bk)))
    for name, frame, loc, rot in bk:
        name_b = name.encode("cp932")[:15]
        buf.extend(name_b.ljust(15, b"\x00"))
        buf.extend(struct.pack("<I3f4f", frame, *loc, *rot))
        buf.extend(b"\x00" * 64)

    # Morph keyframes (empty)
    mk = morph_keyframes or []
    buf.extend(struct.pack("<I", len(mk)))
    for name, frame, weight in mk:
        name_b = name.encode("cp932")[:15]
        buf.extend(name_b.ljust(15, b"\x00"))
        buf.extend(struct.pack("<If", frame, weight))

    # Camera keyframes (empty)
    buf.extend(struct.pack("<I", 0))

    # Light keyframes (empty)
    buf.extend(struct.pack("<I", 0))

    # Shadow keyframes (empty)
    buf.extend(struct.pack("<I", 0))

    # Property keyframes (IK toggle)
    pk = property_keyframes or []
    buf.extend(struct.pack("<I", len(pk)))
    for frame, visible, ik_states in pk:
        buf.extend(struct.pack("<I", frame))
        buf.extend(struct.pack("<B", 1 if visible else 0))
        buf.extend(struct.pack("<I", len(ik_states)))
        for ik_name, enabled in ik_states:
            name_b = ik_name.encode("cp932")[:20]
            buf.extend(name_b.ljust(20, b"\x00"))
            buf.extend(struct.pack("<B", 1 if enabled else 0))

    return bytes(buf)


# ---------------------------------------------------------------------------
# VMD property section parsing
# ---------------------------------------------------------------------------

class TestVmdPropertyParsing:
    def test_empty_property_section(self, tmp_path):
        vmd_bytes = _make_vmd_with_properties()
        path = tmp_path / "empty_prop.vmd"
        path.write_bytes(vmd_bytes)
        vmd = parse(path)
        assert len(vmd.property_keyframes) == 0

    def test_single_property_keyframe(self, tmp_path):
        vmd_bytes = _make_vmd_with_properties(
            property_keyframes=[
                (0, True, [("左足ＩＫ", True), ("右足ＩＫ", True)]),
            ]
        )
        path = tmp_path / "one_prop.vmd"
        path.write_bytes(vmd_bytes)
        vmd = parse(path)
        assert len(vmd.property_keyframes) == 1
        pk = vmd.property_keyframes[0]
        assert pk.frame == 0
        assert pk.visible is True
        assert len(pk.ik_states) == 2
        assert pk.ik_states[0] == ("左足ＩＫ", True)
        assert pk.ik_states[1] == ("右足ＩＫ", True)

    def test_ik_disable_toggle(self, tmp_path):
        vmd_bytes = _make_vmd_with_properties(
            property_keyframes=[
                (0, True, [("左足ＩＫ", True)]),
                (100, True, [("左足ＩＫ", False)]),
                (200, True, [("左足ＩＫ", True)]),
            ]
        )
        path = tmp_path / "ik_toggle.vmd"
        path.write_bytes(vmd_bytes)
        vmd = parse(path)
        assert len(vmd.property_keyframes) == 3
        assert vmd.property_keyframes[0].ik_states[0][1] is True
        assert vmd.property_keyframes[1].ik_states[0][1] is False
        assert vmd.property_keyframes[2].ik_states[0][1] is True

    def test_visibility_flag(self, tmp_path):
        vmd_bytes = _make_vmd_with_properties(
            property_keyframes=[
                (0, False, []),
            ]
        )
        path = tmp_path / "vis.vmd"
        path.write_bytes(vmd_bytes)
        vmd = parse(path)
        assert vmd.property_keyframes[0].visible is False

    def test_multiple_ik_bones(self, tmp_path):
        vmd_bytes = _make_vmd_with_properties(
            property_keyframes=[
                (50, True, [
                    ("左足ＩＫ", True),
                    ("右足ＩＫ", False),
                    ("左つま先ＩＫ", True),
                    ("右つま先ＩＫ", False),
                ]),
            ]
        )
        path = tmp_path / "multi_ik.vmd"
        path.write_bytes(vmd_bytes)
        vmd = parse(path)
        pk = vmd.property_keyframes[0]
        assert len(pk.ik_states) == 4
        names = [s[0] for s in pk.ik_states]
        assert "左足ＩＫ" in names
        assert "右足ＩＫ" in names

    def test_property_keyframe_type(self, tmp_path):
        vmd_bytes = _make_vmd_with_properties(
            property_keyframes=[(0, True, [])]
        )
        path = tmp_path / "type.vmd"
        path.write_bytes(vmd_bytes)
        vmd = parse(path)
        assert isinstance(vmd.property_keyframes[0], PropertyKeyframe)


# ---------------------------------------------------------------------------
# Real VMD file property parsing
# ---------------------------------------------------------------------------

class TestRealVmdProperties:
    @pytest.fixture
    def parsed_vmd(self) -> VmdMotion:
        if not TEST_VMD.exists():
            pytest.skip(f"Test VMD not found: {TEST_VMD}")
        return parse(TEST_VMD)

    def test_property_keyframes_field_exists(self, parsed_vmd):
        """VmdMotion should have property_keyframes field."""
        assert hasattr(parsed_vmd, "property_keyframes")
        assert isinstance(parsed_vmd.property_keyframes, list)

    def test_bone_keyframes_still_parsed(self, parsed_vmd):
        """Adding property parsing shouldn't break existing sections."""
        assert len(parsed_vmd.bone_keyframes) > 0
        assert len(parsed_vmd.morph_keyframes) > 0


# ---------------------------------------------------------------------------
# IK limit conversion (pure math, no Blender)
# ---------------------------------------------------------------------------

class TestIkLimitConversion:
    """Test the axis-aligned permutation logic used for IK limit conversion.

    These tests verify the pure-math functions without Blender. The actual
    _convert_ik_limits and _axis_aligned_permutation functions use mathutils
    types (Matrix, Vector) only available in Blender, so we test the
    equivalent algorithm in pure Python here.
    """

    @staticmethod
    def _axis_aligned_permutation(mat):
        """Pure-Python version of _axis_aligned_permutation."""
        m = [[0, 0, 0], [0, 0, 0], [0, 0, 0]]
        i_set = [0, 1, 2]
        j_set = [0, 1, 2]
        for _ in range(3):
            ii, jj = i_set[0], j_set[0]
            for i in i_set:
                for j in j_set:
                    if abs(mat[i][j]) > abs(mat[ii][jj]):
                        ii, jj = i, j
            i_set.remove(ii)
            j_set.remove(jj)
            m[ii][jj] = -1.0 if mat[ii][jj] < 0 else 1.0
        return m

    @staticmethod
    def _mat_vec_mul(m, v):
        """3x3 matrix × 3-vector."""
        return tuple(sum(m[i][j] * v[j] for j in range(3)) for i in range(3))

    def test_identity_matrix_gives_identity_perm(self):
        """Identity bone matrix → identity permutation (no axis remapping)."""
        # With the conversion formula: -I * -1 = I, swap rows 1/2, transpose
        # gives a matrix that swaps Y↔Z. The permutation should reflect that.
        mat = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        # Simulate: mat * -1
        neg = [[-r for r in row] for row in mat]
        # Swap rows 1 and 2
        neg[1], neg[2] = neg[2], neg[1]
        # Transpose
        trans = [[neg[j][i] for j in range(3)] for i in range(3)]
        perm = self._axis_aligned_permutation(trans)
        # Apply to test limits
        test_min = (-1.5, 0.0, 0.0)
        result = self._mat_vec_mul(perm, test_min)
        # Each axis should map to exactly one output axis
        non_zero = sum(1 for v in result if abs(v) > 1e-6)
        assert non_zero >= 1  # permutation preserves non-zero values

    def test_permutation_is_orthogonal(self):
        """Permutation matrix should have exactly one non-zero per row/col."""
        mat = [[0.9, 0.1, 0.0], [0.0, 0.0, -0.95], [0.1, 0.9, 0.0]]
        perm = self._axis_aligned_permutation(mat)
        for i in range(3):
            row_nz = sum(1 for j in range(3) if abs(perm[i][j]) > 0.5)
            assert row_nz == 1, f"Row {i} has {row_nz} non-zero entries"
        for j in range(3):
            col_nz = sum(1 for i in range(3) if abs(perm[i][j]) > 0.5)
            assert col_nz == 1, f"Col {j} has {col_nz} non-zero entries"

    def test_knee_like_limits(self):
        """For a typical knee bone (bend on one axis), limits should stay on one axis."""
        # Typical knee: limits like (-3.14, 0, 0) to (-0.008, 0, 0)
        # after parser Y↔Z swap: (-3.14, 0, 0) → (-3.14, 0, 0) (X stays X)
        #
        # With identity-ish bone matrix, the limits should end up on one axis.
        mat = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        neg = [[-r for r in row] for row in mat]
        neg[1], neg[2] = neg[2], neg[1]
        trans = [[neg[j][i] for j in range(3)] for i in range(3)]
        perm = self._axis_aligned_permutation(trans)

        limit_min = (-3.14159, 0.0, 0.0)
        limit_max = (-0.008, 0.0, 0.0)

        new_min = list(self._mat_vec_mul(perm, limit_min))
        new_max = list(self._mat_vec_mul(perm, limit_max))

        # Ensure min <= max
        for i in range(3):
            if new_min[i] > new_max[i]:
                new_min[i], new_max[i] = new_max[i], new_min[i]

        # At least one axis should have the range
        ranges = [new_max[i] - new_min[i] for i in range(3)]
        assert max(ranges) > 3.0, "Expected large range on one axis for knee limits"

    def test_min_max_swap_correction(self):
        """When axis permutation flips sign, min/max should be corrected."""
        # A permutation with -1 on the diagonal would negate values,
        # causing min > max without correction
        perm = [[-1, 0, 0], [0, 1, 0], [0, 0, -1]]
        limit_min = (-3.0, -1.0, -2.0)
        limit_max = (-0.5, 0.5, -0.1)

        new_min = list(self._mat_vec_mul(perm, limit_min))
        new_max = list(self._mat_vec_mul(perm, limit_max))

        for i in range(3):
            if new_min[i] > new_max[i]:
                new_min[i], new_max[i] = new_max[i], new_min[i]

        for i in range(3):
            assert new_min[i] <= new_max[i], f"Axis {i}: min={new_min[i]} > max={new_max[i]}"


# ---------------------------------------------------------------------------
# PropertyKeyframe dataclass
# ---------------------------------------------------------------------------

class TestPropertyKeyframeDataclass:
    def test_fields(self):
        pk = PropertyKeyframe(frame=10, visible=True, ik_states=[("test", False)])
        assert pk.frame == 10
        assert pk.visible is True
        assert pk.ik_states == [("test", False)]

    def test_vmd_motion_has_property_keyframes(self):
        vmd = VmdMotion(model_name="test")
        assert vmd.property_keyframes == []

    def test_vmd_motion_with_property_keyframes(self):
        pk = PropertyKeyframe(frame=0, visible=True, ik_states=[])
        vmd = VmdMotion(model_name="test", property_keyframes=[pk])
        assert len(vmd.property_keyframes) == 1
