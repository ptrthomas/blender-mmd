"""Unit tests for translation tables + chunk translator."""

from __future__ import annotations

from blender_mmd.translations import (
    translate, normalize_lr, BONE_NAMES,
    translate_morph, MORPH_NAMES,
    MATERIAL_NAMES,
    translate_chunks, resolve_name, _looks_english,
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


class TestTranslateMorph:
    def test_known_morphs(self):
        assert translate_morph("まばたき") == "Blink"
        assert translate_morph("あ") == "A"
        assert translate_morph("笑い") == "Smile"
        assert translate_morph("ウィンク") == "Wink"

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


class TestLooksEnglish:
    def test_ascii_is_english(self):
        assert _looks_english("Hair") is True
        assert _looks_english("arm twist_L") is True

    def test_japanese_is_not_english(self):
        assert _looks_english("髪") is False
        assert _looks_english("ﾀｧ､・") is False

    def test_empty_is_not_english(self):
        assert _looks_english("") is False
        assert _looks_english("   ") is False

    def test_mixed(self):
        assert _looks_english("Hair123") is True
        # Any CJK/kana → not English (chunk translation handles better)
        assert _looks_english("髪Hair") is False
        assert _looks_english("Nipple凸") is False
        assert _looks_english("胸_L") is False
        assert _looks_english("髪の毛H") is False


class TestTranslateChunks:
    def test_side_prefix_with_ascii(self):
        result = translate_chunks("左HairA15")
        assert result is not None
        assert result.endswith(".L")
        assert "Hair" in result

    def test_side_prefix_with_japanese(self):
        result = translate_chunks("右スカート")
        assert result == "Skirt.R"

    def test_back_hair_numbered(self):
        result = translate_chunks("後髪3")
        assert result is not None
        assert "BackHair" in result
        assert "3" in result

    def test_upper_lash(self):
        result = translate_chunks("上まつげ2")
        assert result is not None
        assert "UpperLash" in result
        assert "2" in result

    def test_side_suffix(self):
        result = translate_chunks("スカート右")
        assert result is not None
        assert result.endswith(".R")
        assert "Skirt" in result

    def test_pure_ascii_returns_none(self):
        # No Japanese chunks to translate
        assert translate_chunks("HairA15") is None

    def test_empty_returns_none(self):
        assert translate_chunks("") is None

    def test_fullwidth_normalized(self):
        # ＩＫ should be normalized to IK
        result = translate_chunks("足ＩＫ")
        assert result is not None
        assert "Leg" in result
        assert "IK" in result

    def test_complex_name(self):
        result = translate_chunks("左胸補助")
        assert result is not None
        assert result.endswith(".L")
        assert "Chest" in result
        assert "Assist" in result


class TestResolveName:
    def test_chunks_preferred_over_name_e(self):
        # Chunks produce consistent CamelCase, preferred over lowercase name_e
        assert resolve_name("センター", "center", BONE_NAMES) == "Center"

    def test_chunks_used_when_no_name_e(self):
        # No name_e → chunks translate
        assert resolve_name("センター", "", BONE_NAMES) == "Center"

    def test_english_name_fallback(self):
        # Unknown Japanese, good English name → name_e used as fallback
        assert resolve_name("不明な骨", "SomeBone", BONE_NAMES) == "SomeBone"

    def test_garbage_english_filtered(self):
        # Japanese name_e that's not actually English
        result = resolve_name("不明な骨", "ﾀｧ､・", BONE_NAMES)
        # Should NOT use the garbage name_e
        assert result != "ﾀｧ､・"

    def test_chunk_fallback(self):
        # Not in table, no English, but has translatable chunks
        result = resolve_name("左スリーブ", "", BONE_NAMES)
        assert result.endswith(".L")
        assert "Sleeve" in result

    def test_japanese_fallback(self):
        # Completely unknown, no chunks match
        result = resolve_name("完全に不明", "", BONE_NAMES)
        assert result == "完全に不明"

    def test_nfkc_chunk_lookup(self):
        # Fullwidth ＩＫ normalized via chunks
        assert resolve_name("左足ＩＫ", "", BONE_NAMES) == "LegIK.L"

    def test_lr_normalization_on_english(self):
        # _L/_R in name_e should be converted to .L/.R
        result = resolve_name("何か", "arm_L", BONE_NAMES)
        assert result == "arm.L"

    def test_material_table(self):
        # Materials resolved via chunks (+ small override table)
        assert resolve_name("顔", "", MATERIAL_NAMES) == "Face"
        assert resolve_name("髪", "", MATERIAL_NAMES) == "Hair"
        assert resolve_name("スカート", "", MATERIAL_NAMES) == "Skirt"

    def test_material_override(self):
        # MATERIAL_NAMES overrides prevent bad chunk decomposition
        assert resolve_name("黒目", "", MATERIAL_NAMES) == "Iris"
        assert resolve_name("帽子", "", MATERIAL_NAMES) == "Hat"
