from __future__ import annotations

"""Japanese → English bone name translations for common MMD bones.

Used as fallback when PMX models have empty English names.
Grown over time by scripts/scan_translations.py.
"""

# fmt: off
BONE_NAMES: dict[str, str] = {
    # Core body
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

    # Eyes
    "両目": "Eyes",
    "左目": "Eye_L",
    "右目": "Eye_R",

    # Left arm
    "左肩": "Shoulder_L",
    "左腕": "Arm_L",
    "左腕捩": "ArmTwist_L",
    "左ひじ": "Elbow_L",
    "左手捩": "HandTwist_L",
    "左手首": "Wrist_L",

    # Left hand fingers
    "左親指０": "Thumb0_L",
    "左親指１": "Thumb1_L",
    "左親指２": "Thumb2_L",
    "左人指１": "IndexFinger1_L",
    "左人指２": "IndexFinger2_L",
    "左人指３": "IndexFinger3_L",
    "左中指１": "MiddleFinger1_L",
    "左中指２": "MiddleFinger2_L",
    "左中指３": "MiddleFinger3_L",
    "左薬指１": "RingFinger1_L",
    "左薬指２": "RingFinger2_L",
    "左薬指３": "RingFinger3_L",
    "左小指１": "LittleFinger1_L",
    "左小指２": "LittleFinger2_L",
    "左小指３": "LittleFinger3_L",

    # Right arm
    "右肩": "Shoulder_R",
    "右腕": "Arm_R",
    "右腕捩": "ArmTwist_R",
    "右ひじ": "Elbow_R",
    "右手捩": "HandTwist_R",
    "右手首": "Wrist_R",

    # Right hand fingers
    "右親指０": "Thumb0_R",
    "右親指１": "Thumb1_R",
    "右親指２": "Thumb2_R",
    "右人指１": "IndexFinger1_R",
    "右人指２": "IndexFinger2_R",
    "右人指３": "IndexFinger3_R",
    "右中指１": "MiddleFinger1_R",
    "右中指２": "MiddleFinger2_R",
    "右中指３": "MiddleFinger3_R",
    "右薬指１": "RingFinger1_R",
    "右薬指２": "RingFinger2_R",
    "右薬指３": "RingFinger3_R",
    "右小指１": "LittleFinger1_R",
    "右小指２": "LittleFinger2_R",
    "右小指３": "LittleFinger3_R",

    # Left leg
    "左足": "Leg_L",
    "左ひざ": "Knee_L",
    "左足首": "Ankle_L",
    "左つま先": "Toe_L",

    # Right leg
    "右足": "Leg_R",
    "右ひざ": "Knee_R",
    "右足首": "Ankle_R",
    "右つま先": "Toe_R",

    # IK
    "左足ＩＫ": "LegIK_L",
    "右足ＩＫ": "LegIK_R",
    "左つま先ＩＫ": "ToeIK_L",
    "右つま先ＩＫ": "ToeIK_R",

    # Additional / common variants
    "左足D": "Leg_D_L",
    "右足D": "Leg_D_R",
    "左ひざD": "Knee_D_L",
    "右ひざD": "Knee_D_R",
    "左足首D": "Ankle_D_L",
    "右足首D": "Ankle_D_R",
    "左足先EX": "ToeTipEX_L",
    "右足先EX": "ToeTipEX_R",
    "左肩P": "ShoulderP_L",
    "右肩P": "ShoulderP_R",
    "左肩C": "ShoulderC_L",
    "右肩C": "ShoulderC_R",
}
# fmt: on


def translate(name_j: str) -> str | None:
    """Look up English name for a Japanese bone name. Returns None if not found."""
    return BONE_NAMES.get(name_j)
