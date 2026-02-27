"""Unit tests for bone and morph name translation tables."""

from __future__ import annotations

from blender_mmd.translations import (
    translate, normalize_lr, BONE_NAMES,
    translate_morph, MORPH_NAMES,
)


class TestTranslate:
    def test_known_names(self):
        assert translate("センター") == "Center"
        assert translate("頭") == "Head"
        assert translate("左腕") == "Arm.L"
        assert translate("右足") == "Leg.R"

    def test_unknown_returns_none(self):
        assert translate("存在しない骨") is None
        assert translate("") is None

    def test_standard_bones_present(self):
        required = [
            "センター", "上半身", "下半身", "首", "頭",
            "左肩", "左腕", "左ひじ", "左手首",
            "右肩", "右腕", "右ひじ", "右手首",
            "左足", "左ひざ", "左足首",
            "右足", "右ひざ", "右足首",
            "左目", "右目", "両目",
            "左足ＩＫ", "右足ＩＫ",
        ]
        for name_j in required:
            assert translate(name_j) is not None, f"Missing translation for {name_j}"

    def test_no_empty_values(self):
        for name_j, name_e in BONE_NAMES.items():
            assert name_j, "Empty Japanese key"
            assert name_e, f"Empty English value for {name_j}"

    def test_blender_lr_convention(self):
        """All L/R bones use Blender's .L/.R suffix, not _L/_R."""
        for name_j, name_e in BONE_NAMES.items():
            assert "_L" not in name_e and "_R" not in name_e or \
                   "_D." in name_e or "EX." in name_e or \
                   not name_e.endswith(("_L", "_R")), \
                f"{name_j} -> {name_e} uses _L/_R instead of .L/.R"


class TestTranslateMorph:
    def test_known_morphs(self):
        assert translate_morph("まばたき") == "Blink"
        assert translate_morph("あ") == "A"
        assert translate_morph("笑い") == "Smile"
        assert translate_morph("ウィンク") == "Wink.L"

    def test_unknown_returns_none(self):
        assert translate_morph("存在しないモーフ") is None
        assert translate_morph("") is None

    def test_standard_morphs_present(self):
        """Common MMD morphs every model should have translations for."""
        required = [
            # Vowels
            "あ", "い", "う", "え", "お",
            # Eyes
            "まばたき", "笑い", "ウィンク", "ウィンク右",
            # Eyebrows
            "真面目", "困る", "にこり", "怒り",
            # Effects
            "照れ",
        ]
        for name_j in required:
            assert translate_morph(name_j) is not None, (
                f"Missing morph translation for {name_j}"
            )

    def test_no_empty_values(self):
        for name_j, name_e in MORPH_NAMES.items():
            assert name_j, "Empty Japanese key in MORPH_NAMES"
            assert name_e, f"Empty English value for morph {name_j}"

    def test_lr_convention(self):
        """L/R morphs use Blender's .L/.R suffix."""
        for name_j, name_e in MORPH_NAMES.items():
            if name_e.endswith((".L", ".R")):
                # Good — uses Blender convention
                continue
            assert not name_e.endswith(("_L", "_R")), (
                f"Morph {name_j} -> {name_e} uses _L/_R instead of .L/.R"
            )

    def test_no_duplicates(self):
        """No two Japanese names map to the same English name."""
        seen: dict[str, str] = {}
        for name_j, name_e in MORPH_NAMES.items():
            if name_e in seen:
                assert False, (
                    f"Duplicate English morph name '{name_e}': "
                    f"'{seen[name_e]}' and '{name_j}'"
                )
            seen[name_e] = name_j


class TestNormalizeLR:
    def test_converts_underscore_to_dot(self):
        assert normalize_lr("Arm_L") == "Arm.L"
        assert normalize_lr("Leg_R") == "Leg.R"

    def test_preserves_dot_convention(self):
        assert normalize_lr("Arm.L") == "Arm.L"

    def test_no_change_for_center_bones(self):
        assert normalize_lr("Center") == "Center"
        assert normalize_lr("Head") == "Head"

    def test_only_converts_suffix(self):
        assert normalize_lr("L_Arm_L") == "L_Arm.L"
        assert normalize_lr("RULER") == "RULER"
