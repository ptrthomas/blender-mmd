"""Unit tests for MMD → Blender coordinate conversion.

MMD: left-handed Y-up (X-right, Y-up, Z-forward/towards camera)
Blender: right-handed Z-up (X-right, Y-forward/into screen, Z-up)

Conversion: (x, y, z) → (x, -z, y)
"""

from __future__ import annotations

from blender_mmd.pmx.parser import _pos, _rot


class TestPositionConversion:
    def test_identity(self):
        assert _pos(0, 0, 0) == (0, 0, 0)

    def test_x_unchanged(self):
        assert _pos(1.0, 0, 0) == (1.0, 0, 0)

    def test_y_becomes_z(self):
        # MMD Y (up) → Blender Z (up)
        assert _pos(0, 1.0, 0) == (0, 0, 1.0)

    def test_z_becomes_negative_y(self):
        # MMD Z (towards camera) → Blender -Y (towards camera)
        assert _pos(0, 0, 1.0) == (0, -1.0, 0)

    def test_full_vector(self):
        assert _pos(1.0, 2.0, 3.0) == (1.0, -3.0, 2.0)

    def test_negative_values(self):
        assert _pos(-1.0, -2.0, -3.0) == (-1.0, 3.0, -2.0)

    def test_model_center_above_origin(self):
        """MMD center bone at Y=8 should be at Blender Z=8 (above origin)."""
        x, y, z = _pos(0, 8.0, 0)
        assert z > 0


class TestRotationConversion:
    def test_identity(self):
        assert _rot(0, 0, 0) == (0, 0, 0)

    def test_full_vector(self):
        assert _rot(1.0, 2.0, 3.0) == (1.0, -3.0, 2.0)

    def test_same_as_position(self):
        """Rotation uses same axis remapping as position."""
        for vals in [(1, 2, 3), (-1, 0, 1), (0.5, -0.5, 0.5)]:
            assert _rot(*vals) == _pos(*vals)
