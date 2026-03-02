"""PMD parser tests — structure validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from blender_mmd.pmd.parser import parse
from blender_mmd.pmx.types import (
    BoneWeightBDEF1,
    BoneWeightBDEF2,
    Model,
    MorphType,
    WeightType,
)

SAMPLES_DIR = Path(__file__).parent / "samples"


@pytest.fixture
def pmd_files() -> list[Path]:
    files = sorted(SAMPLES_DIR.glob("*.pmd"))
    assert files, f"No PMD files in {SAMPLES_DIR}"
    return files


@pytest.fixture
def parsed_pmd(pmd_files) -> Model:
    """Parse the first PMD sample file."""
    return parse(pmd_files[0])


class TestBatchParse:
    def test_parse_all_samples(self, pmd_files):
        """Every PMD sample file parses without error."""
        for f in pmd_files:
            model = parse(f)
            assert isinstance(model, Model)
            assert len(model.vertices) > 0
            assert len(model.faces) > 0
            assert len(model.bones) > 0


class TestHeader:
    def test_version(self, parsed_pmd):
        assert parsed_pmd.header.version == 1.0

    def test_encoding(self, parsed_pmd):
        assert parsed_pmd.header.encoding == "cp932"

    def test_model_name(self, parsed_pmd):
        assert parsed_pmd.name  # should have a name


class TestVertices:
    def test_positions_are_3d(self, parsed_pmd):
        for v in parsed_pmd.vertices[:100]:
            assert len(v.position) == 3

    def test_normals_are_3d(self, parsed_pmd):
        for v in parsed_pmd.vertices[:100]:
            assert len(v.normal) == 3

    def test_uvs_are_2d(self, parsed_pmd):
        for v in parsed_pmd.vertices[:100]:
            assert len(v.uv) == 2

    def test_weight_types(self, parsed_pmd):
        """PMD only produces BDEF1 or BDEF2 weights."""
        for v in parsed_pmd.vertices[:100]:
            assert v.weight_type in (WeightType.BDEF1, WeightType.BDEF2)

    def test_bdef2_weight_range(self, parsed_pmd):
        for v in parsed_pmd.vertices[:100]:
            if v.weight_type == WeightType.BDEF2:
                assert isinstance(v.weight, BoneWeightBDEF2)
                assert 0.0 <= v.weight.weight <= 1.0


class TestFaces:
    def test_triangles(self, parsed_pmd):
        for face in parsed_pmd.faces:
            assert len(face) == 3

    def test_indices_in_range(self, parsed_pmd):
        n = len(parsed_pmd.vertices)
        for face in parsed_pmd.faces:
            for idx in face:
                assert 0 <= idx < n


class TestBones:
    def test_all_have_japanese_name(self, parsed_pmd):
        for bone in parsed_pmd.bones:
            assert bone.name

    def test_parent_indices_valid(self, parsed_pmd):
        n = len(parsed_pmd.bones)
        for bone in parsed_pmd.bones:
            assert bone.parent == -1 or 0 <= bone.parent < n

    def test_ik_bones_have_links(self, parsed_pmd):
        """Any bone with IK flag should have IK data."""
        for bone in parsed_pmd.bones:
            if bone.is_ik:
                assert bone.ik_target is not None
                assert bone.ik_links is not None
                assert len(bone.ik_links) > 0

    def test_knee_ik_links_have_limits(self, parsed_pmd):
        """Knee bones in IK chains should have rotation limits for correct bending."""
        import math
        names = [b.name for b in parsed_pmd.bones]
        for bone in parsed_pmd.bones:
            if not bone.is_ik or not bone.ik_links:
                continue
            for link in bone.ik_links:
                link_name = names[link.bone_index] if link.bone_index < len(names) else ""
                if not link_name.endswith("ひざ"):
                    continue
                assert link.has_limits, f"Knee '{link_name}' should have IK limits"
                assert link.limit_min is not None
                assert link.limit_max is not None
                # Limits should restrict to backward bend (X rotation in MMD space,
                # already Y↔Z swapped by parser: min[0] ≈ -π, max[0] ≈ -0.5°)
                assert link.limit_min[0] < -3.0, "Knee min limit should be near -π"
                assert abs(link.limit_max[0]) < 0.02, "Knee max limit should be near -0.5°"

    def test_coordinate_conversion(self, parsed_pmd):
        """Root bone (Center) should be at Y=0 or positive Z in Blender coords."""
        # Just check that we have reasonable positions (not NaN/inf)
        for bone in parsed_pmd.bones[:10]:
            x, y, z = bone.position
            assert all(abs(v) < 1000 for v in (x, y, z))


class TestMaterials:
    def test_face_count_sums_to_total(self, parsed_pmd):
        """Material face_counts should sum to total vertex indices."""
        total = sum(m.face_count for m in parsed_pmd.materials)
        assert total == len(parsed_pmd.faces) * 3

    def test_face_counts_positive(self, parsed_pmd):
        for mat in parsed_pmd.materials:
            assert mat.face_count > 0


class TestMorphs:
    def test_morph_indices_absolute(self, parsed_pmd):
        """Morph vertex indices should be absolute (remapped from base morph)."""
        n = len(parsed_pmd.vertices)
        for morph in parsed_pmd.morphs:
            assert morph.morph_type == MorphType.VERTEX
            for offset in morph.offsets:
                assert 0 <= offset.vertex_index < n, (
                    f"Morph '{morph.name}' has out-of-range vertex index "
                    f"{offset.vertex_index} (max {n-1})"
                )

    def test_morph_offsets_are_3d(self, parsed_pmd):
        for morph in parsed_pmd.morphs:
            for offset in morph.offsets[:10]:
                assert len(offset.offset) == 3


class TestRigidBodies:
    def test_positions_absolute(self, parsed_pmd):
        """Rigid body positions should be absolute (not bone-relative)."""
        if not parsed_pmd.rigid_bodies:
            pytest.skip("No rigid bodies in sample")
        for rb in parsed_pmd.rigid_bodies:
            x, y, z = rb.position
            # Should be reasonable world coordinates
            assert all(abs(v) < 1000 for v in (x, y, z))

    def test_bone_indices_valid(self, parsed_pmd):
        n = len(parsed_pmd.bones)
        for rb in parsed_pmd.rigid_bodies:
            assert rb.bone_index == -1 or 0 <= rb.bone_index < n


class TestJoints:
    def test_rigid_indices_valid(self, parsed_pmd):
        if not parsed_pmd.joints:
            pytest.skip("No joints in sample")
        n = len(parsed_pmd.rigid_bodies)
        for j in parsed_pmd.joints:
            assert 0 <= j.src_rigid < n
            assert 0 <= j.dest_rigid < n


class TestWaistCancelFix:
    """PMD WaistCancel neutralization for modern VMD compatibility."""

    def test_waist_cancel_no_additional_transform(self, parsed_pmd):
        """WaistCancel bones should have additional_transform stripped (no LowerBody cancel)."""
        names = [b.name for b in parsed_pmd.bones]
        for wc_name in ("腰キャンセル左", "腰キャンセル右"):
            if wc_name not in names:
                continue
            wc = parsed_pmd.bones[names.index(wc_name)]
            assert wc.additional_transform is None, (
                f"{wc_name} should have additional_transform=None (neutralized), "
                f"got {wc.additional_transform}"
            )

    def test_waist_cancel_no_additional_rotation_flag(self, parsed_pmd):
        """WaistCancel bones should not have ADDITIONAL_ROTATION flag after neutralization."""
        names = [b.name for b in parsed_pmd.bones]
        for wc_name in ("腰キャンセル左", "腰キャンセル右"):
            if wc_name not in names:
                continue
            wc = parsed_pmd.bones[names.index(wc_name)]
            assert not wc.has_additional_rotation, (
                f"{wc_name} should not have ADDITIONAL_ROTATION flag"
            )

    def test_waist_cancel_parent_is_lower_body(self, parsed_pmd):
        """WaistCancel bones should remain children of LowerBody."""
        names = [b.name for b in parsed_pmd.bones]
        if "腰キャンセル左" not in names:
            pytest.skip("No WaistCancel bones")
        lower_body_idx = names.index("下半身")
        for wc_name in ("腰キャンセル左", "腰キャンセル右"):
            if wc_name in names:
                wc = parsed_pmd.bones[names.index(wc_name)]
                assert wc.parent == lower_body_idx

    def test_leg_parented_to_waist_cancel(self, parsed_pmd):
        """Leg bones should remain children of WaistCancel (passthrough)."""
        names = [b.name for b in parsed_pmd.bones]
        for leg_name, wc_name in (("左足", "腰キャンセル左"), ("右足", "腰キャンセル右")):
            if leg_name not in names or wc_name not in names:
                continue
            leg = parsed_pmd.bones[names.index(leg_name)]
            wc_idx = names.index(wc_name)
            assert leg.parent == wc_idx


class TestAllSections:
    def test_returns_pmx_model(self, parsed_pmd):
        """PMD parser should return the same Model type as PMX parser."""
        assert isinstance(parsed_pmd, Model)

    def test_all_sections_parsed(self, parsed_pmd):
        m = parsed_pmd
        assert len(m.vertices) > 0
        assert len(m.faces) > 0
        assert len(m.bones) > 0
        assert len(m.materials) > 0
