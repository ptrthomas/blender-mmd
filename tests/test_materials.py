"""Material helper tests — pure Python, no Blender required."""

from __future__ import annotations

import os

import pytest

from blender_mmd.materials import (
    build_material_indices,
    mix_diffuse_ambient,
    resolve_shared_toon,
    resolve_texture_path,
    roughness_from_shininess,
    shared_toon_filename,
)
from blender_mmd.pmx.parser import parse
from blender_mmd.pmx.types import Material


class TestRoughnessFromShininess:
    def test_known_values(self):
        # shininess=1 → roughness=1.0
        assert roughness_from_shininess(1) == pytest.approx(1.0)
        # shininess=50 → typical MMD value
        r = roughness_from_shininess(50)
        assert 0.0 < r < 1.0
        # Higher shininess → lower roughness
        assert roughness_from_shininess(100) < roughness_from_shininess(10)

    def test_zero_shininess(self):
        # Should not divide by zero; max(0, 1) = 1
        r = roughness_from_shininess(0)
        assert r == pytest.approx(1.0)

    def test_negative_shininess(self):
        r = roughness_from_shininess(-5)
        assert r == pytest.approx(1.0)

    def test_very_high_shininess(self):
        r = roughness_from_shininess(512)
        assert 0.0 < r < 0.2


class TestMixDiffuseAmbient:
    def test_basic_mixing(self):
        result = mix_diffuse_ambient((0.8, 0.6, 0.4), (0.2, 0.1, 0.0))
        assert result == pytest.approx((0.6, 0.4, 0.2))

    def test_clamping(self):
        result = mix_diffuse_ambient((1.0, 1.0, 1.0), (1.0, 1.0, 1.0))
        assert result == (1.0, 1.0, 1.0)

    def test_zero_inputs(self):
        result = mix_diffuse_ambient((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        assert result == (0.0, 0.0, 0.0)


class TestBuildMaterialIndices:
    def _mat(self, face_count: int) -> Material:
        return Material(
            name="test", name_e="", diffuse=(1, 1, 1, 1), specular=(0, 0, 0),
            shininess=50, ambient=(0.5, 0.5, 0.5), flags=0,
            edge_color=(0, 0, 0, 1), edge_size=1.0, texture_index=-1,
            sphere_texture_index=-1, sphere_mode=0, toon_sharing=0,
            toon_texture_index=-1, comment="", face_count=face_count,
        )

    def test_single_material(self):
        indices = build_material_indices([self._mat(12)])
        assert indices == [0, 0, 0, 0]

    def test_multiple_materials(self):
        indices = build_material_indices([self._mat(6), self._mat(9)])
        assert indices == [0, 0, 1, 1, 1]

    def test_empty(self):
        assert build_material_indices([]) == []


class TestResolveTexturePath:
    def test_backslash_normalization(self):
        result = resolve_texture_path("/models", "tex\\hair.png")
        assert "\\" not in result
        assert "hair.png" in result

    def test_relative_join(self):
        result = resolve_texture_path("/models/miku", "textures/body.png")
        expected = os.path.join("/models/miku", "textures/body.png")
        assert result == expected


class TestSharedToonFilename:
    def test_index_0(self):
        assert shared_toon_filename(0) == "toon01.bmp"

    def test_index_9(self):
        assert shared_toon_filename(9) == "toon10.bmp"

    def test_index_4(self):
        assert shared_toon_filename(4) == "toon05.bmp"


class TestResolveSharedToon:
    def test_bundled_fallback(self, tmp_path):
        """When toon isn't in PMX dir, falls back to bundled toons."""
        result = resolve_shared_toon(str(tmp_path), 0)
        assert result is not None
        assert result.endswith("toon01.bmp")

    def test_all_bundled_toons_exist(self):
        for i in range(10):
            result = resolve_shared_toon("/nonexistent", i)
            assert result is not None, f"toon{i+1:02d}.bmp not found"

    def test_pmx_dir_takes_priority(self, tmp_path):
        """Toon in PMX dir should be found before bundled."""
        local_toon = tmp_path / "toon01.bmp"
        local_toon.write_bytes(b"fake")
        result = resolve_shared_toon(str(tmp_path), 0)
        assert result == str(local_toon)


class TestMaterialFlags:
    def _mat(self, flags: int) -> Material:
        return Material(
            name="test", name_e="", diffuse=(1, 1, 1, 1), specular=(0, 0, 0),
            shininess=50, ambient=(0.5, 0.5, 0.5), flags=flags,
            edge_color=(0, 0, 0, 1), edge_size=1.0, texture_index=-1,
            sphere_texture_index=-1, sphere_mode=0, toon_sharing=0,
            toon_texture_index=-1, comment="", face_count=3,
        )

    def test_double_sided(self):
        assert self._mat(0x01).is_double_sided
        assert not self._mat(0x00).is_double_sided

    def test_drop_shadow(self):
        assert self._mat(0x02).enabled_drop_shadow
        assert not self._mat(0x00).enabled_drop_shadow

    def test_self_shadow_map(self):
        assert self._mat(0x04).enabled_self_shadow_map

    def test_self_shadow(self):
        assert self._mat(0x08).enabled_self_shadow

    def test_toon_edge(self):
        assert self._mat(0x10).enabled_toon_edge
        assert not self._mat(0x00).enabled_toon_edge

    def test_combined_flags(self):
        m = self._mat(0x1F)
        assert m.is_double_sided
        assert m.enabled_drop_shadow
        assert m.enabled_self_shadow_map
        assert m.enabled_self_shadow
        assert m.enabled_toon_edge


class TestSampleMaterials:
    """Validate material data from parsed sample PMX files."""

    def test_face_coverage(self, parsed_model):
        """Sum of material face_counts should equal total face indices."""
        total_faces = sum(m.face_count for m in parsed_model.materials)
        assert total_faces == len(parsed_model.faces) * 3

    def test_texture_indices_in_range(self, parsed_model):
        """All texture indices should be -1 or within texture table range."""
        n_textures = len(parsed_model.textures)
        for mat in parsed_model.materials:
            assert mat.texture_index == -1 or 0 <= mat.texture_index < n_textures
            assert mat.sphere_texture_index == -1 or 0 <= mat.sphere_texture_index < n_textures

    def test_sphere_mode_values(self, parsed_model):
        for mat in parsed_model.materials:
            assert mat.sphere_mode in (0, 1, 2, 3)

    def test_material_indices_length(self, parsed_model):
        indices = build_material_indices(parsed_model.materials)
        assert len(indices) == len(parsed_model.faces)

    def test_material_indices_values(self, parsed_model):
        indices = build_material_indices(parsed_model.materials)
        n_mats = len(parsed_model.materials)
        for idx in indices:
            assert 0 <= idx < n_mats
