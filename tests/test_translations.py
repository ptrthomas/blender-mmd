"""Unit tests for the bone name translation table."""

from __future__ import annotations

from blender_mmd.translations import translate, normalize_lr, BONE_NAMES


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
