"""Morph parsing tests — structure validation for vertex/bone/group/UV/material morphs."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

# mesh.py imports bpy/mathutils which aren't available outside Blender.
# Mock them so we can import the pure-Python _flatten_group_morph helper.
sys.modules.setdefault("bpy", MagicMock())
sys.modules.setdefault("mathutils", MagicMock())

from blender_mmd.mesh import _flatten_group_morph
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


class TestMorphNameResolution:
    def test_table_wins_over_english(self):
        """Table lookup takes priority over PMX name_e (matches bone behavior)."""
        from blender_mmd.translations import resolve_morph_name
        # "あ" is in MORPH_NAMES → "A", so table wins
        assert resolve_morph_name("あ", "A_from_pmx") == "A"

    def test_english_used_when_not_in_table(self):
        """If not in table, use name_e when it looks English."""
        from blender_mmd.translations import resolve_morph_name
        assert resolve_morph_name("不明モーフ", "CustomMorph") == "CustomMorph"

    def test_resolve_uses_translation_table(self):
        """If no name_e, fall back to translation table."""
        from blender_mmd.translations import resolve_morph_name
        assert resolve_morph_name("まばたき", "") == "Blink"

    def test_resolve_falls_back_to_japanese(self):
        """If no name_e and no translation, use Japanese name."""
        from blender_mmd.translations import resolve_morph_name
        assert resolve_morph_name("カスタムモーフ", "") == "カスタムモーフ"


class TestGroupMorphFlattening:
    """Test group morph → composite vertex shape key flattening.

    Uses the sample model's TestGroup morph [18]:
      -> [15] MATERIAL "TestMat" factor=1.0
      -> [16] UV "TestUV" factor=1.0
      -> [17] BONE "TestBone" factor=1.0
      -> [0] VERTEX "あ" factor=1.0
    """

    def test_sample_has_group_morph(self, parsed_model):
        """Confirm the sample fixture has at least one GROUP morph."""
        group_morphs = [
            m for m in parsed_model.morphs if m.morph_type == MorphType.GROUP
        ]
        assert len(group_morphs) > 0

    def test_flatten_finds_vertex_child(self, parsed_model):
        """Flattening TestGroup should produce non-empty vertex_deltas from 'あ'."""
        # _flatten_group_morph imported at module level

        # Find TestGroup
        group_idx = None
        for i, m in enumerate(parsed_model.morphs):
            if m.morph_type == MorphType.GROUP and m.name == "TestGroup":
                group_idx = i
                break
        assert group_idx is not None, "TestGroup not found in sample model"

        n_verts = len(parsed_model.vertices)
        vertex_deltas: dict[int, list[float]] = {}
        visited: set[int] = {group_idx}
        skipped = _flatten_group_morph(
            parsed_model, group_idx, 1.0, vertex_deltas, visited, n_verts, scale=1.0
        )
        assert len(vertex_deltas) > 0, "Should have vertex deltas from child 'あ'"

    def test_flatten_skips_non_vertex(self, parsed_model):
        """Flattening TestGroup should skip 3 non-vertex children (MAT+UV+BONE)."""
        # _flatten_group_morph imported at module level

        group_idx = None
        for i, m in enumerate(parsed_model.morphs):
            if m.morph_type == MorphType.GROUP and m.name == "TestGroup":
                group_idx = i
                break
        assert group_idx is not None

        n_verts = len(parsed_model.vertices)
        vertex_deltas: dict[int, list[float]] = {}
        visited: set[int] = {group_idx}
        skipped = _flatten_group_morph(
            parsed_model, group_idx, 1.0, vertex_deltas, visited, n_verts, scale=1.0
        )
        assert skipped == 3, f"Expected 3 skipped (MAT+UV+BONE), got {skipped}"

    def test_flatten_cycle_detection(self):
        """A group morph referencing itself should not infinite-loop."""
        # _flatten_group_morph imported at module level
        from blender_mmd.pmx.types import Morph, MorphCategory

        # Synthetic model with a single group morph pointing at itself
        self_ref = Morph(
            name="SelfRef", name_e="", category=MorphCategory.OTHER,
            morph_type=MorphType.GROUP,
            offsets=[GroupMorphOffset(morph_index=0, factor=1.0)],
        )

        class FakeModel:
            morphs = [self_ref]
            vertices = []

        vertex_deltas: dict[int, list[float]] = {}
        visited: set[int] = {0}
        # Should return immediately without recursion (0 is already in visited)
        skipped = _flatten_group_morph(
            FakeModel(), 0, 1.0, vertex_deltas, visited, 0, scale=1.0
        )
        assert skipped == 0
        assert len(vertex_deltas) == 0


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
