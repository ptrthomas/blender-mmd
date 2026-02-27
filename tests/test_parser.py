"""PMX parser tests — structure validation and batch parsing."""

from __future__ import annotations

from blender_mmd.pmx.parser import parse
from blender_mmd.pmx.types import Model


class TestBatchParse:
    def test_parse_all_samples(self, pmx_files):
        """Every sample file parses without error."""
        for f in pmx_files:
            model = parse(f)
            assert isinstance(model, Model)
            assert len(model.vertices) > 0
            assert len(model.faces) > 0
            assert len(model.bones) > 0


class TestHeader:
    def test_version(self, parsed_model):
        assert parsed_model.header.version in (2.0, 2.1)

    def test_encoding(self, parsed_model):
        assert parsed_model.header.encoding in ("utf-16-le", "utf-8")

    def test_additional_uv_count(self, parsed_model):
        assert 0 <= parsed_model.header.additional_uv_count <= 4

    def test_index_sizes(self, parsed_model):
        h = parsed_model.header
        for size in (h.vertex_index_size, h.bone_index_size, h.texture_index_size,
                     h.material_index_size, h.morph_index_size, h.rigid_index_size):
            assert size in (1, 2, 4)


class TestVertices:
    def test_positions_are_3d(self, parsed_model):
        for v in parsed_model.vertices[:100]:
            assert len(v.position) == 3

    def test_normals_are_3d(self, parsed_model):
        for v in parsed_model.vertices[:100]:
            assert len(v.normal) == 3

    def test_uvs_are_2d(self, parsed_model):
        for v in parsed_model.vertices[:100]:
            assert len(v.uv) == 2


class TestFaces:
    def test_triangles(self, parsed_model):
        for face in parsed_model.faces:
            assert len(face) == 3

    def test_indices_in_range(self, parsed_model):
        n = len(parsed_model.vertices)
        for face in parsed_model.faces:
            for idx in face:
                assert 0 <= idx < n


class TestBones:
    def test_all_have_japanese_name(self, parsed_model):
        for bone in parsed_model.bones:
            assert bone.name

    def test_parent_indices_valid(self, parsed_model):
        n = len(parsed_model.bones)
        for bone in parsed_model.bones:
            assert bone.parent == -1 or 0 <= bone.parent < n


class TestMaterials:
    def test_face_count_sums_to_total(self, parsed_model):
        """Material face_counts should sum to total vertex indices."""
        total = sum(m.face_count for m in parsed_model.materials)
        assert total == len(parsed_model.faces) * 3

    def test_face_counts_positive(self, parsed_model):
        for mat in parsed_model.materials:
            assert mat.face_count > 0


class TestAllSections:
    def test_all_sections_parsed(self, parsed_model):
        """Verify all PMX sections produced data."""
        m = parsed_model
        assert len(m.vertices) > 0
        assert len(m.faces) > 0
        assert len(m.bones) > 0
        assert len(m.materials) > 0
        # These may be zero for some models, but our samples have them
        assert len(m.morphs) > 0
        assert len(m.rigid_bodies) > 0
        assert len(m.joints) > 0
