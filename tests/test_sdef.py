"""Unit tests for SDEF math and MDD round-trip."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest

from blender_mmd.sdef import (
    SDEFMeshData,
    SDEFVertexData,
    read_mdd,
    write_mdd,
)


def _quat_to_matrix(q):
    """Convert quaternion (x, y, z, w) to 3x3 rotation matrix."""
    x, y, z, w = q
    return np.array([
        [1 - 2*(y*y + z*z), 2*(x*y - z*w),     2*(x*z + y*w)],
        [2*(x*y + z*w),     1 - 2*(x*x + z*z), 2*(y*z - x*w)],
        [2*(x*z - y*w),     2*(y*z + x*w),     1 - 2*(x*x + y*y)],
    ], dtype=np.float64)


# ---------------------------------------------------------------------------
# SDEF preprocessing math
# ---------------------------------------------------------------------------


class TestSDEFPreprocessing:
    """Verify precomputed constants match the SDEF algorithm."""

    def _precompute(self, C, R0, R1, vertex_co, w0):
        """Replicate _precompute_sdef_data math for a single vertex."""
        C = np.array(C, dtype=np.float32)
        R0 = np.array(R0, dtype=np.float32)
        R1 = np.array(R1, dtype=np.float32)
        vertex_co = np.array(vertex_co, dtype=np.float32)
        w1 = 1.0 - w0

        rw = R0 * w0 + R1 * w1
        r0 = C + R0 - rw
        r1 = C + R1 - rw
        pos_c = vertex_co - C
        cr0 = (C + r0) * 0.5
        cr1 = (C + r1) * 0.5

        return pos_c, cr0, cr1

    def test_equal_weights(self):
        """With w0=0.5, rw is midpoint of R0 and R1."""
        C = [0.0, 0.0, 0.0]
        R0 = [1.0, 0.0, 0.0]
        R1 = [-1.0, 0.0, 0.0]
        vertex_co = [0.5, 1.0, 0.0]

        pos_c, cr0, cr1 = self._precompute(C, R0, R1, vertex_co, 0.5)

        # pos_c = vertex_co - C
        np.testing.assert_allclose(pos_c, [0.5, 1.0, 0.0])

        # rw = R0*0.5 + R1*0.5 = [0, 0, 0]
        # r0 = C + R0 - rw = [1, 0, 0]
        # r1 = C + R1 - rw = [-1, 0, 0]
        # cr0 = (C + r0) / 2 = [0.5, 0, 0]
        # cr1 = (C + r1) / 2 = [-0.5, 0, 0]
        np.testing.assert_allclose(cr0, [0.5, 0.0, 0.0])
        np.testing.assert_allclose(cr1, [-0.5, 0.0, 0.0])

    def test_full_weight_bone0(self):
        """With w0=1.0, rw = R0, so r0 = C and r1 = C + R1 - R0."""
        C = [0.0, 0.0, 5.0]
        R0 = [1.0, 2.0, 3.0]
        R1 = [-1.0, -2.0, -3.0]
        vertex_co = [1.0, 1.0, 6.0]

        pos_c, cr0, cr1 = self._precompute(C, R0, R1, vertex_co, 1.0)

        np.testing.assert_allclose(pos_c, [1.0, 1.0, 1.0])

        # rw = R0*1.0 + R1*0.0 = R0 = [1, 2, 3]
        # r0 = C + R0 - rw = C = [0, 0, 5]
        # cr0 = (C + r0) / 2 = (C + C) / 2 = C = [0, 0, 5]
        np.testing.assert_allclose(cr0, [0.0, 0.0, 5.0])

    def test_full_weight_bone1(self):
        """With w0=0.0, rw = R1, so r1 = C and r0 = C + R0 - R1."""
        C = [0.0, 0.0, 5.0]
        R0 = [1.0, 2.0, 3.0]
        R1 = [-1.0, -2.0, -3.0]
        vertex_co = [1.0, 1.0, 6.0]

        pos_c, cr0, cr1 = self._precompute(C, R0, R1, vertex_co, 0.0)

        # rw = R1 = [-1, -2, -3]
        # r1 = C + R1 - rw = C = [0, 0, 5]
        # cr1 = (C + C) / 2 = C = [0, 0, 5]
        np.testing.assert_allclose(cr1, [0.0, 0.0, 5.0])

    def test_asymmetric_weights(self):
        """Non-equal weights shift rw toward the heavier bone."""
        C = [0.0, 0.0, 0.0]
        R0 = [2.0, 0.0, 0.0]
        R1 = [0.0, 2.0, 0.0]
        vertex_co = [1.0, 1.0, 0.0]
        w0 = 0.75

        pos_c, cr0, cr1 = self._precompute(C, R0, R1, vertex_co, w0)

        # rw = R0*0.75 + R1*0.25 = [1.5, 0.5, 0]
        rw = np.array([1.5, 0.5, 0.0])
        r0 = np.array(C) + np.array(R0) - rw  # [0.5, -0.5, 0]
        r1 = np.array(C) + np.array(R1) - rw  # [-1.5, 1.5, 0]

        expected_cr0 = (np.array(C) + r0) * 0.5
        expected_cr1 = (np.array(C) + r1) * 0.5

        np.testing.assert_allclose(cr0, expected_cr0, atol=1e-6)
        np.testing.assert_allclose(cr1, expected_cr1, atol=1e-6)


# ---------------------------------------------------------------------------
# SDEF deformation at identity
# ---------------------------------------------------------------------------


class TestSDEFIdentity:
    """When both bones are at identity (no movement), SDEF should
    produce the same position as the rest pose vertex."""

    def test_identity_deformation(self):
        """With identity bone matrices, SDEF output = rest vertex position."""
        # Set up a vertex at (1, 2, 3) with C at origin
        C = np.array([0.0, 0.0, 0.0], dtype=np.float32)
        R0 = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        R1 = np.array([-1.0, 0.0, 0.0], dtype=np.float32)
        vertex_co = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        w0, w1 = 0.5, 0.5

        # Precompute
        rw = R0 * w0 + R1 * w1
        r0 = C + R0 - rw
        r1 = C + R1 - rw
        pos_c = vertex_co - C
        cr0 = (C + r0) * 0.5
        cr1 = (C + r1) * 0.5

        # Identity rotation and matrix
        mat_rot = np.eye(3, dtype=np.float64)
        mat0 = np.eye(3, dtype=np.float64)
        mat1 = np.eye(3, dtype=np.float64)

        # SDEF formula: mat_rot @ pos_c + mat0 @ cr0 * w0 + mat1 @ cr1 * w1
        new_pos = (
            mat_rot @ pos_c.astype(np.float64)
            + (mat0 @ cr0.astype(np.float64)) * w0
            + (mat1 @ cr1.astype(np.float64)) * w1
        )

        # At identity, this should equal:
        # pos_c + cr0 * w0 + cr1 * w1
        # = (vertex_co - C) + (C + r0)/2 * w0 + (C + r1)/2 * w1
        # We verify it equals the original vertex position
        np.testing.assert_allclose(new_pos, vertex_co, atol=1e-6)

    def test_identity_with_offset_center(self):
        """Identity deformation with non-zero C."""
        C = np.array([5.0, 3.0, 1.0], dtype=np.float32)
        R0 = np.array([6.0, 3.0, 1.0], dtype=np.float32)
        R1 = np.array([4.0, 3.0, 1.0], dtype=np.float32)
        vertex_co = np.array([5.5, 4.0, 2.0], dtype=np.float32)
        w0, w1 = 0.6, 0.4

        rw = R0 * w0 + R1 * w1
        r0 = C + R0 - rw
        r1 = C + R1 - rw
        pos_c = vertex_co - C
        cr0 = (C + r0) * 0.5
        cr1 = (C + r1) * 0.5

        mat_rot = np.eye(3, dtype=np.float64)
        mat0 = np.eye(3, dtype=np.float64)
        mat1 = np.eye(3, dtype=np.float64)

        new_pos = (
            mat_rot @ pos_c.astype(np.float64)
            + (mat0 @ cr0.astype(np.float64)) * w0
            + (mat1 @ cr1.astype(np.float64)) * w1
        )

        np.testing.assert_allclose(new_pos, vertex_co, atol=1e-5)


# ---------------------------------------------------------------------------
# SDEF deformation with known rotation
# ---------------------------------------------------------------------------


class TestSDEFRotation:
    """Verify SDEF produces expected results with a known rotation."""

    def test_90deg_rotation_bone0_only(self):
        """With w0=1.0 and bone0 rotated 90° around Z,
        the vertex should follow bone0's rotation around C."""
        C = np.array([0.0, 0.0, 0.0], dtype=np.float32)
        R0 = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        R1 = np.array([-1.0, 0.0, 0.0], dtype=np.float32)
        vertex_co = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        w0, w1 = 1.0, 0.0

        rw = R0 * w0 + R1 * w1
        r0 = C + R0 - rw
        r1 = C + R1 - rw
        pos_c = vertex_co - C
        cr0 = (C + r0) * 0.5
        cr1 = (C + r1) * 0.5

        # 90° rotation around Z axis
        rot_90z = np.array([
            [0, -1, 0],
            [1,  0, 0],
            [0,  0, 1],
        ], dtype=np.float64)

        # With w0=1.0, mat_rot = rot0 (bone0's rotation)
        # mat0 = rot_90z, mat1 = identity (irrelevant since w1=0)
        mat_rot = rot_90z
        mat0 = rot_90z
        mat1 = np.eye(3, dtype=np.float64)

        new_pos = (
            mat_rot @ pos_c.astype(np.float64)
            + (mat0 @ cr0.astype(np.float64)) * w0
            + (mat1 @ cr1.astype(np.float64)) * w1
        )

        # pos_c = [1, 0, 0], rotated 90° Z → [0, 1, 0]
        # cr0 = C = [0, 0, 0] (since w0=1, r0=C), so mat0 @ cr0 = [0, 0, 0]
        # Result = [0, 1, 0]
        np.testing.assert_allclose(new_pos, [0.0, 1.0, 0.0], atol=1e-6)

    def test_equal_weight_blended_rotation(self):
        """With equal weights and opposing rotations, the blended
        rotation should be an average."""
        C = np.array([0.0, 0.0, 0.0], dtype=np.float32)
        R0 = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        R1 = np.array([-1.0, 0.0, 0.0], dtype=np.float32)
        vertex_co = np.array([0.0, 0.0, 1.0], dtype=np.float32)
        w0, w1 = 0.5, 0.5

        rw = R0 * w0 + R1 * w1
        r0 = C + R0 - rw
        r1 = C + R1 - rw
        pos_c = vertex_co - C
        cr0 = (C + r0) * 0.5
        cr1 = (C + r1) * 0.5

        # bone0: +30° around Z, bone1: -30° around Z
        angle0 = np.radians(30)
        angle1 = np.radians(-30)

        rot0 = np.array([
            [np.cos(angle0), -np.sin(angle0), 0],
            [np.sin(angle0),  np.cos(angle0), 0],
            [0, 0, 1],
        ], dtype=np.float64)

        rot1 = np.array([
            [np.cos(angle1), -np.sin(angle1), 0],
            [np.sin(angle1),  np.cos(angle1), 0],
            [0, 0, 1],
        ], dtype=np.float64)

        # Quaternion for rotation around Z: q = (0, 0, sin(θ/2), cos(θ/2))
        # in (x, y, z, w) format
        q0 = np.array([0, 0, np.sin(angle0 / 2), np.cos(angle0 / 2)])
        q1 = np.array([0, 0, np.sin(angle1 / 2), np.cos(angle1 / 2)])

        # Ensure shortest path
        if np.dot(q0, q1) < 0:
            q1 = -q1

        blended_q = q0 * w0 + q1 * w1
        blended_q /= np.linalg.norm(blended_q)
        mat_rot = _quat_to_matrix(blended_q)

        new_pos = (
            mat_rot @ pos_c.astype(np.float64)
            + (rot0 @ cr0.astype(np.float64)) * w0
            + (rot1 @ cr1.astype(np.float64)) * w1
        )

        # Symmetric ±30° blend = identity rotation for the pos_c part
        # pos_c = [0, 0, 1], identity rotation → [0, 0, 1]
        # cr0 = [0.5, 0, 0], cr1 = [-0.5, 0, 0]
        # rot0 @ cr0 * 0.5 + rot1 @ cr1 * 0.5
        # These corrections are small and symmetric, roughly canceling
        # The Z component should remain close to 1.0
        assert abs(new_pos[2] - 1.0) < 0.01

    def test_sdef_preserves_volume_vs_lbs(self):
        """SDEF should produce a position closer to the spherical arc
        than linear blend skinning (LBS) for a 90° bend.

        When R0=R1=C, the cr0/cr1 correction terms vanish (both equal C),
        isolating the pure rotation effect. SDEF rotates pos_c by a
        blended quaternion (NLERP), which stays on the unit sphere,
        while LBS linearly interpolates transformed positions, collapsing
        toward the center.
        """
        C = np.array([0.0, 0.0, 0.0], dtype=np.float32)
        # R0=R1=C eliminates correction terms, pure rotation test
        R0 = np.array([0.0, 0.0, 0.0], dtype=np.float32)
        R1 = np.array([0.0, 0.0, 0.0], dtype=np.float32)
        vertex_co = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        w0, w1 = 0.5, 0.5

        rw = R0 * w0 + R1 * w1
        r0 = C + R0 - rw
        r1 = C + R1 - rw
        pos_c = vertex_co - C
        cr0 = (C + r0) * 0.5
        cr1 = (C + r1) * 0.5

        # bone0: identity, bone1: 90° around Z
        rot0 = np.eye(3, dtype=np.float64)
        angle = np.radians(90)
        rot1 = np.array([
            [np.cos(angle), -np.sin(angle), 0],
            [np.sin(angle),  np.cos(angle), 0],
            [0, 0, 1],
        ], dtype=np.float64)

        # LBS result: linear blend of transformed positions
        lbs_pos = (rot0 @ vertex_co.astype(np.float64)) * w0 + (rot1 @ vertex_co.astype(np.float64)) * w1

        # SDEF result using NLERP
        q0 = np.array([0, 0, 0, 1], dtype=np.float64)
        q1 = np.array([0, 0, np.sin(angle / 2), np.cos(angle / 2)])
        if np.dot(q0, q1) < 0:
            q1 = -q1
        blended_q = q0 * w0 + q1 * w1
        blended_q /= np.linalg.norm(blended_q)
        mat_rot = _quat_to_matrix(blended_q)

        sdef_pos = (
            mat_rot @ pos_c.astype(np.float64)
            + (rot0 @ cr0.astype(np.float64)) * w0
            + (rot1 @ cr1.astype(np.float64)) * w1
        )

        # SDEF should maintain distance from C better than LBS
        sdef_dist = np.linalg.norm(sdef_pos)
        lbs_dist = np.linalg.norm(lbs_pos)
        original_dist = np.linalg.norm(vertex_co)

        sdef_error = abs(sdef_dist - original_dist)
        lbs_error = abs(lbs_dist - original_dist)
        assert sdef_error < lbs_error, (
            f"SDEF error {sdef_error:.4f} should be less than LBS error {lbs_error:.4f}"
        )


# ---------------------------------------------------------------------------
# MDD write / read round-trip
# ---------------------------------------------------------------------------


class TestMDDRoundTrip:
    """MDD file write and read back."""

    def test_single_frame(self, tmp_path):
        """Single frame round-trip."""
        positions = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], dtype=np.float32)
        path = tmp_path / "test.mdd"

        write_mdd(path, [positions])
        fc, vc, frames = read_mdd(path)

        assert fc == 1
        assert vc == 2
        assert len(frames) == 1
        np.testing.assert_allclose(frames[0], positions, atol=1e-6)

    def test_multiple_frames(self, tmp_path):
        """Multiple frames round-trip."""
        frame0 = np.array([[0, 0, 0], [1, 1, 1]], dtype=np.float32)
        frame1 = np.array([[2, 2, 2], [3, 3, 3]], dtype=np.float32)
        frame2 = np.array([[4, 4, 4], [5, 5, 5]], dtype=np.float32)
        path = tmp_path / "multi.mdd"

        write_mdd(path, [frame0, frame1, frame2])
        fc, vc, frames = read_mdd(path)

        assert fc == 3
        assert vc == 2
        np.testing.assert_allclose(frames[0], frame0)
        np.testing.assert_allclose(frames[1], frame1)
        np.testing.assert_allclose(frames[2], frame2)

    def test_large_vertex_count(self, tmp_path):
        """Realistic vertex count (5000 verts, 10 frames)."""
        n_verts = 5000
        n_frames = 10
        rng = np.random.default_rng(42)
        all_frames = [rng.standard_normal((n_verts, 3)).astype(np.float32) for _ in range(n_frames)]
        path = tmp_path / "large.mdd"

        write_mdd(path, all_frames)
        fc, vc, frames = read_mdd(path)

        assert fc == n_frames
        assert vc == n_verts
        for i in range(n_frames):
            np.testing.assert_allclose(frames[i], all_frames[i], atol=1e-5)

    def test_file_size(self, tmp_path):
        """Verify MDD file is exactly the expected size."""
        n_verts = 100
        n_frames = 5
        frames = [np.zeros((n_verts, 3), dtype=np.float32) for _ in range(n_frames)]
        path = tmp_path / "size.mdd"

        write_mdd(path, frames)

        expected_size = 8 + n_frames * n_verts * 3 * 4
        assert path.stat().st_size == expected_size

    def test_big_endian_header(self, tmp_path):
        """Verify header is written in big-endian."""
        import struct
        frames = [np.zeros((3, 3), dtype=np.float32)]
        path = tmp_path / "endian.mdd"

        write_mdd(path, frames)

        with open(path, "rb") as f:
            raw = f.read(8)
        fc, vc = struct.unpack(">ii", raw)
        assert fc == 1
        assert vc == 3

    def test_negative_values(self, tmp_path):
        """Negative vertex positions survive round-trip."""
        positions = np.array([[-1.5, -2.5, -3.5], [0.0, 0.0, 0.0]], dtype=np.float32)
        path = tmp_path / "neg.mdd"

        write_mdd(path, [positions])
        _, _, frames = read_mdd(path)

        np.testing.assert_allclose(frames[0], positions, atol=1e-6)

    def test_empty_frames_raises(self):
        """Writing zero frames should raise ValueError."""
        with pytest.raises(ValueError, match="No frames"):
            write_mdd("/tmp/nope.mdd", [])

    def test_creates_parent_directories(self, tmp_path):
        """write_mdd creates intermediate directories."""
        path = tmp_path / "a" / "b" / "c" / "test.mdd"
        frames = [np.zeros((2, 3), dtype=np.float32)]

        write_mdd(path, frames)

        assert path.exists()
