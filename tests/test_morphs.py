"""Morph parsing tests — structure validation for vertex/bone/group/UV/material morphs."""

from __future__ import annotations

from blender_mmd.pmx.types import (
    MorphType,
    VertexMorphOffset,
    BoneMorphOffset,
    GroupMorphOffset,
    UVMorphOffset,
    MaterialMorphOffset,
)


class TestMorphsParsed:
    def test_morphs_exist(self, parsed_model):
        assert len(parsed_model.morphs) > 0

    def test_vertex_morphs_present(self, parsed_model):
        """Sample model should have vertex morphs (facial expressions)."""
        vertex_morphs = [
            m for m in parsed_model.morphs if m.morph_type == MorphType.VERTEX
        ]
        assert len(vertex_morphs) > 0

    def test_morph_names_non_empty(self, parsed_model):
        for morph in parsed_model.morphs:
            assert morph.name, "Morph Japanese name must be non-empty"

    def test_morph_types_valid(self, parsed_model):
        for morph in parsed_model.morphs:
            assert isinstance(morph.morph_type, MorphType)


class TestVertexMorphOffsets:
    def test_offsets_have_correct_type(self, parsed_model):
        for morph in parsed_model.morphs:
            if morph.morph_type == MorphType.VERTEX:
                for offset in morph.offsets:
                    assert isinstance(offset, VertexMorphOffset)

    def test_vertex_indices_in_range(self, parsed_model):
        n_verts = len(parsed_model.vertices)
        for morph in parsed_model.morphs:
            if morph.morph_type == MorphType.VERTEX:
                for offset in morph.offsets:
                    assert 0 <= offset.vertex_index < n_verts, (
                        f"Morph '{morph.name}' has out-of-range vertex index "
                        f"{offset.vertex_index} (n_verts={n_verts})"
                    )

    def test_offsets_are_3d(self, parsed_model):
        for morph in parsed_model.morphs:
            if morph.morph_type == MorphType.VERTEX:
                for offset in morph.offsets[:10]:
                    assert len(offset.offset) == 3

    def test_offsets_non_empty(self, parsed_model):
        """Vertex morphs should have at least one offset."""
        for morph in parsed_model.morphs:
            if morph.morph_type == MorphType.VERTEX:
                assert len(morph.offsets) > 0, (
                    f"Morph '{morph.name}' has no offsets"
                )


class TestBoneMorphOffsets:
    def test_offsets_have_correct_type(self, parsed_model):
        for morph in parsed_model.morphs:
            if morph.morph_type == MorphType.BONE:
                for offset in morph.offsets:
                    assert isinstance(offset, BoneMorphOffset)

    def test_bone_indices_in_range(self, parsed_model):
        n_bones = len(parsed_model.bones)
        for morph in parsed_model.morphs:
            if morph.morph_type == MorphType.BONE:
                for offset in morph.offsets:
                    assert 0 <= offset.bone_index < n_bones

    def test_quaternion_is_4d(self, parsed_model):
        for morph in parsed_model.morphs:
            if morph.morph_type == MorphType.BONE:
                for offset in morph.offsets[:10]:
                    assert len(offset.rotation) == 4


class TestGroupMorphOffsets:
    def test_offsets_have_correct_type(self, parsed_model):
        for morph in parsed_model.morphs:
            if morph.morph_type == MorphType.GROUP:
                for offset in morph.offsets:
                    assert isinstance(offset, GroupMorphOffset)

    def test_morph_indices_in_range(self, parsed_model):
        n_morphs = len(parsed_model.morphs)
        for morph in parsed_model.morphs:
            if morph.morph_type == MorphType.GROUP:
                for offset in morph.offsets:
                    assert 0 <= offset.morph_index < n_morphs


class TestMorphCoverage:
    def test_all_sample_files(self, pmx_files):
        """Parse all sample files and verify morph structure."""
        from blender_mmd.pmx.parser import parse
        for f in pmx_files:
            model = parse(f)
            for morph in model.morphs:
                assert morph.name
                assert isinstance(morph.morph_type, MorphType)
                # All morphs should have the correct offset type
                for offset in morph.offsets[:5]:
                    if morph.morph_type == MorphType.VERTEX:
                        assert isinstance(offset, VertexMorphOffset)
                    elif morph.morph_type == MorphType.BONE:
                        assert isinstance(offset, BoneMorphOffset)
                    elif morph.morph_type == MorphType.GROUP:
                        assert isinstance(offset, GroupMorphOffset)
                    elif morph.morph_type in (
                        MorphType.UV, MorphType.UV1, MorphType.UV2,
                        MorphType.UV3, MorphType.UV4,
                    ):
                        assert isinstance(offset, UVMorphOffset)
                    elif morph.morph_type == MorphType.MATERIAL:
                        assert isinstance(offset, MaterialMorphOffset)
