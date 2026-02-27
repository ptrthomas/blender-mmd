from __future__ import annotations

"""Japanese → English bone name translations for common MMD bones.

Uses Blender's `.L` / `.R` suffix convention for mirror operations.
Grown over time by scripts/scan_translations.py.
"""

import re

# fmt: off
BONE_NAMES: dict[str, str] = {
    # Core body
    "操作中心": "ViewCenter",
    "全ての親": "ParentNode",
    "センター": "Center",
    "グルーブ": "Groove",
    "腰": "Waist",
    "上半身": "UpperBody",
    "上半身2": "UpperBody2",
    "上半身3": "UpperBody3",
    "下半身": "LowerBody",
    "首": "Neck",
    "頭": "Head",
    "あご": "Jaw",

    # Eyes and brows
    "両目": "Eyes",
    "左目": "Eye.L",
    "右目": "Eye.R",
    "左眉": "Brow.L",
    "右眉": "Brow.R",
    "左眉頭": "BrowInner.L",
    "右眉頭": "BrowInner.R",

    # Left arm
    "左肩": "Shoulder.L",
    "左腕": "Arm.L",
    "左腕捩": "ArmTwist.L",
    "左ひじ": "Elbow.L",
    "左手捩": "HandTwist.L",
    "左手首": "Wrist.L",

    # Left hand fingers
    "左親指０": "Thumb0.L",
    "左親指１": "Thumb1.L",
    "左親指２": "Thumb2.L",
    "左人指１": "IndexFinger1.L",
    "左人指２": "IndexFinger2.L",
    "左人指３": "IndexFinger3.L",
    "左中指１": "MiddleFinger1.L",
    "左中指２": "MiddleFinger2.L",
    "左中指３": "MiddleFinger3.L",
    "左薬指１": "RingFinger1.L",
    "左薬指２": "RingFinger2.L",
    "左薬指３": "RingFinger3.L",
    "左小指１": "LittleFinger1.L",
    "左小指２": "LittleFinger2.L",
    "左小指３": "LittleFinger3.L",

    # Right arm
    "右肩": "Shoulder.R",
    "右腕": "Arm.R",
    "右腕捩": "ArmTwist.R",
    "右ひじ": "Elbow.R",
    "右手捩": "HandTwist.R",
    "右手首": "Wrist.R",

    # Right hand fingers
    "右親指０": "Thumb0.R",
    "右親指１": "Thumb1.R",
    "右親指２": "Thumb2.R",
    "右人指１": "IndexFinger1.R",
    "右人指２": "IndexFinger2.R",
    "右人指３": "IndexFinger3.R",
    "右中指１": "MiddleFinger1.R",
    "右中指２": "MiddleFinger2.R",
    "右中指３": "MiddleFinger3.R",
    "右薬指１": "RingFinger1.R",
    "右薬指２": "RingFinger2.R",
    "右薬指３": "RingFinger3.R",
    "右小指１": "LittleFinger1.R",
    "右小指２": "LittleFinger2.R",
    "右小指３": "LittleFinger3.R",

    # Left leg
    "左足": "Leg.L",
    "左ひざ": "Knee.L",
    "左足首": "Ankle.L",
    "左つま先": "Toe.L",

    # Right leg
    "右足": "Leg.R",
    "右ひざ": "Knee.R",
    "右足首": "Ankle.R",
    "右つま先": "Toe.R",

    # IK
    "左足ＩＫ": "LegIK.L",
    "右足ＩＫ": "LegIK.R",
    "左つま先ＩＫ": "ToeIK.L",
    "右つま先ＩＫ": "ToeIK.R",

    # IK parents
    "左足IK親": "LegIKParent.L",
    "右足IK親": "LegIKParent.R",

    # D-bones (double / deformation)
    "左足D": "Leg_D.L",
    "右足D": "Leg_D.R",
    "左ひざD": "Knee_D.L",
    "右ひざD": "Knee_D.R",
    "左足首D": "Ankle_D.L",
    "右足首D": "Ankle_D.R",
    "左足先EX": "ToeTipEX.L",
    "右足先EX": "ToeTipEX.R",

    # Shoulder sub-bones
    "左肩P": "ShoulderP.L",
    "右肩P": "ShoulderP.R",
    "左肩C": "ShoulderC.L",
    "右肩C": "ShoulderC.R",

    # Hair
    "前髪1": "Bangs1",
    "前髪2": "Bangs2",
    "前髪3": "Bangs3",
    "左前髪": "SideBangs.L",
    "右前髪": "SideBangs.R",
    "左前髪1": "SideBangs1.L",
    "右前髪1": "SideBangs1.R",
    "左横": "SideHair.L",
    "右横": "SideHair.R",
    "後髪1": "BackHair1",
    "後髪2": "BackHair2",
    "後髪3": "BackHair3",

    # Accessories
    "ﾈｸﾀｲ1": "Necktie1",
    "ﾈｸﾀｲ2": "Necktie2",
    "ﾈｸﾀｲ3": "Necktie3",
    "ﾈｸﾀｲ4": "Necktie4",

    # Ribbon / accessories
    "左リボン": "Ribbon.L",
    "右リボン": "Ribbon.R",
    "左ダミー": "Dummy.L",
    "右ダミー": "Dummy.R",
    "左r": "HairR.L",
    "左r先": "HairRTip.L",
    "右r": "HairR.R",
    "右r先": "HairRTip.R",

    # Skirt
    "左スカート前": "SkirtFront.L",
    "右スカート前": "SkirtFront.R",
    "左スカート後": "SkirtBack.L",
    "右スカート後": "SkirtBack.R",

    # Glasses / accessories
    "眼鏡": "Glasses",

    # Twist sub-bones
    "左腕捩1": "ArmTwist1.L",
    "左腕捩2": "ArmTwist2.L",
    "左腕捩3": "ArmTwist3.L",
    "右腕捩1": "ArmTwist1.R",
    "右腕捩2": "ArmTwist2.R",
    "右腕捩3": "ArmTwist3.R",
    "左手捩1": "HandTwist1.L",
    "左手捩2": "HandTwist2.L",
    "左手捩3": "HandTwist3.L",
    "右手捩1": "HandTwist1.R",
    "右手捩2": "HandTwist2.R",
    "右手捩3": "HandTwist3.R",

    # Waist cancel
    "腰キャンセル左": "WaistCancel.L",
    "腰キャンセル右": "WaistCancel.R",

    # Face / mouth
    "舌0": "Tongue0",
    "舌1": "Tongue1",
    "舌2": "Tongue2",
    "上歯": "UpperTeeth",
    "下歯": "LowerTeeth",

    # Tip bones (先 = tip/end)
    "頭先": "HeadTip",
    "右目先": "EyeTip.R",
    "左目先": "EyeTip.L",
    "両目先": "EyesTip",
    "左手先": "WristTip.L",
    "右手先": "WristTip.R",
    "左親指２先": "Thumb2Tip.L",
    "右親指２先": "Thumb2Tip.R",
    "左人指３先": "IndexFinger3Tip.L",
    "右人指３先": "IndexFinger3Tip.R",
    "左中指３先": "MiddleFinger3Tip.L",
    "右中指３先": "MiddleFinger3Tip.R",
    "左薬指３先": "RingFinger3Tip.L",
    "右薬指３先": "RingFinger3Tip.R",
    "左小指３先": "LittleFinger3Tip.L",
    "右小指３先": "LittleFinger3Tip.R",
    "下半身先": "LowerBodyTip",
    "左つま先ＩＫ先": "ToeIKTip.L",
    "右つま先ＩＫ先": "ToeIKTip.R",
    "左足ＩＫ先": "LegIKTip.L",
    "右足ＩＫ先": "LegIKTip.R",
    "センター先": "CenterTip",
}
# fmt: on

# Pattern to convert MMD-style _L/_R suffixes to Blender .L/.R
_LR_PATTERN = re.compile(r"_([LR])$")


def normalize_lr(name: str) -> str:
    """Convert _L/_R suffix to Blender's .L/.R convention."""
    return _LR_PATTERN.sub(r".\1", name)


def translate(name_j: str) -> str | None:
    """Look up English name for a Japanese bone name. Returns None if not found."""
    return BONE_NAMES.get(name_j)
