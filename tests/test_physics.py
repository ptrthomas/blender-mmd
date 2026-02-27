"""Physics unit tests — collision collections, soft constraints, counts, spring values."""

from __future__ import annotations

from pathlib import Path

import pytest

from blender_mmd.pmx import parse
from blender_mmd.pmx.types import RigidBody, RigidMode, RigidShape
from blender_mmd.physics import build_collision_collections, is_locked_dof

SAMPLES_DIR = Path(__file__).parent / "samples"
MIKU_PMX = SAMPLES_DIR / "初音ミク.pmx"


# ---------------------------------------------------------------------------
# Collision collections
# ---------------------------------------------------------------------------

def _make_rigid(group: int = 0, mask: int = 0xFFFF) -> RigidBody:
    """Create a minimal RigidBody for testing collision collections."""
    return RigidBody(
        name="test", name_e="test",
        bone_index=-1,
        collision_group_number=group,
        collision_group_mask=mask,
        shape=RigidShape.SPHERE,
        size=(1.0, 0.0, 0.0),
        position=(0, 0, 0),
        rotation=(0, 0, 0),
        mass=1.0,
        linear_damping=0.0,
        angular_damping=0.0,
        bounce=0.0,
        friction=0.5,
        mode=RigidMode.STATIC,
    )


class TestCollisionCollections:
    def test_own_group_always_set(self):
        """Rigid body's own collision group should always be True."""
        for group in range(16):
            rigid = _make_rigid(group=group, mask=0xFFFF)
            cols = build_collision_collections(rigid)
            assert cols[group] is True

    def test_shared_layer_always_set(self):
        """Layer 0 (shared) is always set so all bodies can potentially collide.

        Actual non-collision is handled by GENERIC constraints with
        disable_collisions=True, not by collision layers.
        """
        rigid = _make_rigid(group=3, mask=0x0000)
        cols = build_collision_collections(rigid)
        assert cols[0] is True
        assert cols[3] is True
        assert sum(cols) == 2  # shared layer + own group

    def test_group_0_two_layers_overlap(self):
        """Group 0 body: shared layer and own group are the same layer."""
        rigid = _make_rigid(group=0, mask=0xFFFF)
        cols = build_collision_collections(rigid)
        assert cols[0] is True
        assert sum(cols) == 1  # layer 0 is both shared and own group

    def test_result_length_20(self):
        """Blender needs exactly 20 bools for collision_collections."""
        cols = build_collision_collections(_make_rigid())
        assert len(cols) == 20

    def test_group_15(self):
        """Edge case: highest PMX group (15)."""
        rigid = _make_rigid(group=15, mask=0x7FFF)
        cols = build_collision_collections(rigid)
        assert cols[0] is True   # shared layer
        assert cols[15] is True  # own group
        assert sum(cols) == 2


# ---------------------------------------------------------------------------
# Soft constraint detection
# ---------------------------------------------------------------------------

class TestSoftConstraints:
    def test_locked_dof(self):
        assert is_locked_dof(0.0, 0.0) is True
        assert is_locked_dof(1.5, 1.5) is True

    def test_unlocked_dof(self):
        assert is_locked_dof(-0.5, 0.5) is False
        assert is_locked_dof(0.0, 1.0) is False

    def test_near_locked(self):
        """Values within 1e-6 are considered locked."""
        assert is_locked_dof(1.0, 1.0 + 1e-7) is True
        assert is_locked_dof(1.0, 1.0 + 1e-5) is False


# ---------------------------------------------------------------------------
# Sample model counts
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def miku_model():
    return parse(str(MIKU_PMX))


class TestSampleCounts:
    def test_rigid_body_count(self, miku_model):
        assert len(miku_model.rigid_bodies) == 45

    def test_joint_count(self, miku_model):
        assert len(miku_model.joints) == 27

    def test_rigid_body_modes(self, miku_model):
        modes = {}
        for rb in miku_model.rigid_bodies:
            modes[rb.mode] = modes.get(rb.mode, 0) + 1
        assert modes[RigidMode.STATIC] == 18
        assert modes[RigidMode.DYNAMIC] == 21
        assert modes[RigidMode.DYNAMIC_BONE] == 6


class TestSpringValues:
    def test_spring_values_nonzero(self, miku_model):
        """Sample model should have non-zero spring rotation constants."""
        has_spring = any(
            any(v != 0 for v in j.spring_constant_rotate)
            for j in miku_model.joints
        )
        assert has_spring, "Expected at least one joint with non-zero spring_constant_rotate"

    def test_spring_count(self, miku_model):
        """19 of 27 joints have non-zero rotation spring constants."""
        count = sum(
            1 for j in miku_model.joints
            if any(v != 0 for v in j.spring_constant_rotate)
        )
        assert count == 19
