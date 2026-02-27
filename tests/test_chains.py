"""Chain detection tests against sample model (初音ミク.pmx).

Sample model stats: 45 RBs, 27 joints, 18 STATIC + 21 DYNAMIC + 6 DYNAMIC_BONE.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blender_mmd.chains import Chain, detect_chains
from blender_mmd.pmx import parse
from blender_mmd.pmx.types import RigidMode

SAMPLES_DIR = Path(__file__).parent / "samples"
MIKU_PMX = SAMPLES_DIR / "初音ミク.pmx"


@pytest.fixture(scope="module")
def miku_model():
    return parse(str(MIKU_PMX))


@pytest.fixture(scope="module")
def chains(miku_model):
    return detect_chains(miku_model)


class TestChainDetection:
    def test_chains_detected(self, chains):
        """At least one chain should be detected."""
        assert len(chains) > 0

    def test_chain_has_required_fields(self, chains):
        """Every chain has all required fields populated."""
        for chain in chains:
            assert isinstance(chain.name, str) and chain.name
            assert chain.group in ("hair", "skirt", "accessory", "other")
            assert chain.root_rigid_index >= 0
            assert len(chain.rigid_indices) > 0
            assert len(chain.joint_indices) > 0

    def test_root_is_static(self, chains, miku_model):
        """Chain root should always be a STATIC rigid body."""
        for chain in chains:
            root_rb = miku_model.rigid_bodies[chain.root_rigid_index]
            assert root_rb.mode == RigidMode.STATIC, (
                f"Chain '{chain.name}' root is {root_rb.mode}, expected STATIC"
            )

    def test_chain_bodies_are_dynamic(self, chains, miku_model):
        """All chain bodies (excluding root) should be DYNAMIC or DYNAMIC_BONE."""
        for chain in chains:
            for ri in chain.rigid_indices:
                rb = miku_model.rigid_bodies[ri]
                assert rb.mode in (RigidMode.DYNAMIC, RigidMode.DYNAMIC_BONE), (
                    f"Chain '{chain.name}' body {ri} is {rb.mode}"
                )

    def test_no_duplicate_bodies(self, chains):
        """No rigid body should appear in multiple chains."""
        all_indices = []
        for chain in chains:
            all_indices.extend(chain.rigid_indices)
        assert len(all_indices) == len(set(all_indices)), "Duplicate body across chains"

    def test_all_dynamic_bodies_accounted(self, chains, miku_model):
        """All non-STATIC bodies should be in some chain."""
        dynamic_count = sum(
            1 for rb in miku_model.rigid_bodies if rb.mode != RigidMode.STATIC
        )
        chain_body_count = sum(len(c.rigid_indices) for c in chains)
        assert chain_body_count == dynamic_count, (
            f"{chain_body_count} bodies in chains vs {dynamic_count} dynamic bodies"
        )

    def test_valid_groups(self, chains):
        """All groups are valid categories."""
        valid = {"hair", "skirt", "accessory", "other"}
        for chain in chains:
            assert chain.group in valid

    def test_bone_indices_populated(self, chains):
        """Chains with bone-linked bodies should have bone_indices."""
        for chain in chains:
            # Most chains should have bone indices
            if chain.root_bone_index >= 0:
                assert len(chain.bone_indices) > 0 or len(chain.rigid_indices) == 0


class TestChainSerialization:
    def test_json_round_trip(self, chains):
        """Chains can be serialized to JSON and back."""
        data = [
            {
                "name": c.name,
                "group": c.group,
                "root_rigid_index": c.root_rigid_index,
                "root_bone_index": c.root_bone_index,
                "rigid_indices": c.rigid_indices,
                "bone_indices": c.bone_indices,
                "joint_indices": c.joint_indices,
            }
            for c in chains
        ]
        json_str = json.dumps(data)
        restored = json.loads(json_str)

        assert len(restored) == len(chains)
        for orig, rest in zip(chains, restored):
            assert rest["name"] == orig.name
            assert rest["group"] == orig.group
            assert rest["rigid_indices"] == orig.rigid_indices
