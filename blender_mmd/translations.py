from __future__ import annotations

"""Japanese → English name translations for MMD objects.

Provides three layers:
1. Full-name lookup tables (BONE_NAMES, MORPH_NAMES, MATERIAL_NAMES)
2. Chunk-based fallback translation (translate_chunks)
3. Unified resolve_name() function for all categories

Uses Blender's `.L` / `.R` suffix convention for mirror operations.
"""

import re
import unicodedata

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
    # PMD-era abbreviated form (人差指 → 人指)
    "左人差指１": "IndexFinger1.L",
    "左人差指２": "IndexFinger2.L",
    "左人差指３": "IndexFinger3.L",
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
    # PMD-era abbreviated form
    "右人差指１": "IndexFinger1.R",
    "右人差指２": "IndexFinger2.R",
    "右人差指３": "IndexFinger3.R",
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
    "左ひざD1": "Knee_D1.L",
    "右ひざD1": "Knee_D1.R",
    "左ひざD2": "Knee_D2.L",
    "右ひざD2": "Knee_D2.R",
    "左ひざDIK": "Knee_D_IK.L",
    "右ひざDIK": "Knee_D_IK.R",
    "左足DS": "Leg_DS.L",
    "右足DS": "Leg_DS.R",
    "左足DS先": "Leg_DSTip.L",
    "右足DS先": "Leg_DSTip.R",

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

    # Skirt (fullwidth katakana — PMX style)
    "左スカート前": "SkirtFront.L",
    "右スカート前": "SkirtFront.R",
    "左スカート後": "SkirtBack.L",
    "右スカート後": "SkirtBack.R",

    # Skirt (halfwidth katakana — PMD style, e.g. Lat式ミク)
    "左ｽｶｰﾄ前": "SkirtFront.L",
    "右ｽｶｰﾄ前": "SkirtFront.R",
    "左ｽｶｰﾄ後": "SkirtBack.L",
    "右ｽｶｰﾄ後": "SkirtBack.R",

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
    "舌3": "Tongue3",
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
    "左人差指３先": "IndexFinger3Tip.L",
    "右人差指３先": "IndexFinger3Tip.R",
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

    # Additional common bones (from mining 3,912 files)
    "左胸": "Chest.L",
    "右胸": "Chest.R",
    "左胸先": "ChestTip.L",
    "右胸先": "ChestTip.R",
    "おっぱい": "Breast",
    "ネクタイ1": "Necktie1",
    "ネクタイ2": "Necktie2",
    "ネクタイ3": "Necktie3",
    "ネクタイ4": "Necktie4",
    "左袖": "Sleeve.L",
    "右袖": "Sleeve.R",
    "左袖先": "SleeveTip.L",
    "右袖先": "SleeveTip.R",
    "左スカート": "Skirt.L",
    "右スカート": "Skirt.R",
    "しっぽ1": "Tail1",
    "しっぽ2": "Tail2",
    "しっぽ3": "Tail3",
    "しっぽ先": "TailTip",
    "左もみあげ": "Sideburn.L",
    "右もみあげ": "Sideburn.R",
    "左おさげ": "Braid.L",
    "右おさげ": "Braid.R",
    "左ツインテ": "Twintail.L",
    "右ツインテ": "Twintail.R",
    "左耳": "Ear.L",
    "右耳": "Ear.R",

    # Kanji-form bones (not reachable via chunks)
    "顎": "Jaw",
    "背中": "Back",
}
# fmt: on


# fmt: off
MORPH_NAMES: dict[str, str] = {
    # --- Eyebrows ---
    "真面目": "Serious",
    "困る": "Troubled",
    "にこり": "Cheerful",
    "怒り": "Angry",
    "上": "BrowUp",
    "下": "BrowDown",
    "前": "BrowForward",
    "平行": "BrowFlat",
    "左上": "BrowUp.L",
    "右上": "BrowUp.R",
    "左下": "BrowDown.L",
    "右下": "BrowDown.R",
    "眉頭": "BrowInner",
    "左眉頭": "BrowInner.L",
    "右眉頭": "BrowInner.R",
    "眉粗": "BrowCoarse",
    "左怒り": "Angry.L",
    "右怒り": "Angry.R",

    # --- Eyes (open/close) ---
    "まばたき": "Blink",
    "笑い": "Smile",
    "笑い目": "SmileEyes",
    "ウィンク": "Wink.L",
    "ウィンク２": "Wink2.L",
    "ウィンク右": "Wink.R",
    "ｳｨﾝｸ２右": "Wink2.R",
    # b-variants (bottom eyelid)
    "bまばたき": "BlinkBottom",
    "b笑い": "SmileBottom",
    "bウィンク": "WinkBottom.L",
    "bウィンク２": "Wink2Bottom.L",
    "bウィンク右": "WinkBottom.R",
    "bｳｨﾝｸ２右": "Wink2Bottom.R",

    # --- Eye shape ---
    "ｷﾘ?1": "SharpEyes",
    "ｷﾞｭｯ": "SquintTight",
    "なごみ": "Gentle",
    "はぅ": "Hau",
    "びっくり": "Surprised",
    "じと目": "HalfClosed",
    "たれ目": "DroopyEyes",
    "なぬ！": "What!",

    # --- Pupil ---
    "瞳小": "PupilSmall",
    "瞳縦": "PupilVertical",
    "近": "EyesClose",
    "近左": "EyeClose.L",
    "近右": "EyeClose.R",
    "離": "EyesFar",
    "離左": "EyeFar.L",
    "離右": "EyeFar.R",
    "短": "EyesShort",
    "カメラ目": "CameraEyes",
    "はちゅ目": "HachuEyes",
    "星目": "StarEyes",
    "はぁと": "HeartEyes",
    "恐ろしい子！": "Scary!",

    # --- Mouth (vowels) ---
    "あ": "A",
    "あ２": "A2",
    "い": "I",
    "い２": "I2",
    "う": "U",
    "え": "E",
    "お": "O",
    "ん": "N",

    # --- Mouth (shapes) ---
    "▲": "MouthTriangle",
    "∧": "MouthLambda",
    "□": "MouthSquare",
    "ワ": "MouthWa",
    "ω": "MouthOmega",
    "ω□": "MouthOmegaSquare",
    "にやり": "Smirk",
    "にっこり": "NiceSmile",
    "ぺろっ": "TongueOut",
    "口角上げ": "MouthCornersUp",
    "左口角上げ": "MouthCornerUp.L",
    "右口角上げ": "MouthCornerUp.R",
    "口角下げ": "MouthCornersDown",
    "左口角下げ": "MouthCornerDown.L",
    "右口角下げ": "MouthCornerDown.R",
    "口横広げ": "MouthWide",
    "左口横広げ": "MouthWide.L",
    "右口横広げ": "MouthWide.R",
    "もぐもぐ": "Chewing",
    "左もぐもぐ": "Chewing.L",
    "右もぐもぐ": "Chewing.R",

    # --- Teeth / tongue ---
    "歯無し上": "NoUpperTeeth",
    "歯無し下": "NoLowerTeeth",
    "舌無し": "NoTongue",

    # --- Tears / blush / effects ---
    "涙": "Tear",
    "涙長": "TearLong",
    "涙上": "TearUp",
    "涙下": "TearDown",
    "涙前": "TearForward",
    "涙近": "TearClose",
    "照れ": "Blush",
    "照れ2": "Blush2",
    "照れ3": "Blush3",
    "左汗": "Sweat.L",
    "右汗": "Sweat.R",
    "汗下": "SweatDown",

    # --- Eyelashes / highlights ---
    "下睫毛消": "HideLowerLashes",
    "HL消１": "HideHighlight1",

    # --- Face lines ---
    "鼻線消し": "HideNoseLine",
    "鼻線長": "NoseLineLong",

    # --- Symbols / decorations ---
    "左燈": "Light.L",
    "右燈": "Light.R",
    "左！": "Exclaim.L",
    "右！": "Exclaim.R",
    "左△△△": "Triangles.L",
    "右△△△": "Triangles.R",

    # --- Accessories ---
    "眼鏡": "Glasses",
    "マイクOFF": "MicOff",
    "HP消": "HideHP",

    # --- Additional morphs (from mining data) ---
    "左ウィンク": "Wink.L",
    "右ウィンク": "Wink.R",
    "しいたけ": "Shiitake",
    "白目": "Sclera",
    "白目左": "Sclera.L",
    "白目右": "Sclera.R",
    "口開け": "MouthOpen",
    "口閉じ": "MouthClose",
    "舌出し": "TongueOut2",
    "赤面": "Blush4",
    "青ざめ": "Pale",
    "ガーン": "Shocked",
    "猫目": "CatEyes",
    "瞳大": "PupilLarge",
    "あ2": "A2b",
    "い2": "I2b",
    "困る2": "Troubled2",
    "困る3": "Troubled3",
    "にこり2": "Cheerful2",
    "怒り2": "Angry2",
    "丸目": "RoundEyes",
    "じと目2": "HalfClosed2",
    "ジト目": "HalfClosed3",
    "口広": "MouthWideOpen",
    "口すぼめ": "MouthPucker",
    "いー": "Teeth",
    "△": "MouthTriangle2",
    "口角上": "MouthCornersUp2",
    "口角下": "MouthCornersDown2",
    "ウィンク2": "Wink2b.L",
    "ウィンク右2": "Wink2b.R",

    # --- Halfwidth kana variants (NFKC normalizes to fullwidth) ---
    "コッチミンナ": "LookHere",
    "キリッ": "SharpLook",
    "キリッ２": "SharpLook2",

    # --- Eyes (additional) ---
    "瞬き": "Blink",
    "瞬き右": "Blink.R",
    "瞬き左": "Blink.L",
    "笑右": "Smile.R",
    "笑左": "Smile.L",
    "笑": "Laugh",
    "怒": "Anger",
    "ウィンク２右": "Wink2.R",
    "なんで": "Why",
    "いやだ": "NoWay",
    "がーん": "GaanShock",
    "悲しい": "Sad",
    "悲しい目": "SadEyes",
    "困惑": "Puzzled",
    "凸目": "BulgingEyes",
    "デヘ目": "DazeEyes",
    "切長": "NarrowEyes",
    "奥目": "DeepEyes",
    "花目": "FlowerEyes",
    "ねこ目": "CatEyes2",
    "内寄せ": "CrossEyed",
    "内開き": "InnerOpen",
    "外開き": "OuterOpen",
    "目縮小": "EyeShrink",
    "瞳縦潰れ": "PupilVertCrush",
    "はちゅ目縦潰れ": "HachuVertCrush",
    "はちゅ目横潰れ": "HachuHorizCrush",
    "はちゅ目２": "HachuEyes2",
    "キッ": "Kii",
    "にやり２": "Smirk2",
    "縦長": "TallEyes",

    # --- Mouth (additional) ---
    "ア": "Aa",
    "オ": "Oo",
    "わ": "Wa",
    "へ": "He",
    "に～": "Ni",
    "に～右": "Ni.R",
    "に～左": "Ni.L",
    "三角": "Triangle",
    "口上げ": "MouthRaise",
    "口横縮小": "MouthNarrow",
    "ワE": "WaE",
    "太": "Thick",

    # --- Mouth (numbered variants) ---
    "あ２": "A2",
    "あ３": "A3",
    "あ４": "A4",
    "い３": "I3",
    "いB": "Ib",
    "う２": "U2",
    "うB": "Ub",
    "え２": "E2",
    "ぺろっE": "TongueOutE",
    "ぺろっ２": "TongueOut2",

    # --- Eyebrow variants ---
    "真面目左": "Serious.L",
    "真面目右": "Serious.R",
    "困る左": "Troubled.L",
    "困る右": "Troubled.R",
    "にこり左": "Cheerful.L",
    "にこり右": "Cheerful.R",
    "怒り左": "Angry.L",
    "怒り右": "Angry.R",

    # --- Teeth / fang ---
    "歯隠し": "HideTeeth",
    "前歯大": "BigFrontTeeth",
    "牙": "Fang",

    # --- Blush / color effects ---
    "照れ消": "BlushOff",
    "青褪め": "Pale2",
    "青ざめる": "Pale3",
    "ハイライト消し": "HideHighlight",
    "ハイライト下": "HighlightDown",
    "HL消": "HideHL",
    "暗さ": "Darkness",
    "エッジ": "EdgeMorph",
    "髮影消": "HairShadowOff",

    # --- Material/display morphs ---
    "輪郭線が消えて": "OutlineOff",
    "消えて": "Disappear",
    "全黒モード": "AllBlackMode",
    "全灰モード": "AllGrayMode",
    "全白モード": "AllWhiteMode",
    "spa消える": "SphereOff",
    "spa消": "SphereOff2",
    "toon白くなる": "ToonWhiten",
    "toon深まる": "ToonDeepen",
    "鼻筋": "NoseBridge",
    "動く": "Move",

    # --- Hand morphs ---
    "左手グー": "FistL",
    "右手グー": "FistR",
    "左手グー２": "Fist2L",
    "右手グー２": "Fist2R",
    "左指を広げて": "SpreadFingersL",
    "右指を広げて": "SpreadFingersR",

    # --- Jaw ---
    "顎開10": "JawOpen10",
    "顎開10デフォ": "JawOpen10Default",
    "下顎出し": "JawOut",

    # --- Idiomatic names (not decomposable into chunks) ---
    "切ない": "Bittersweet",
    "もっと困る": "MoreTroubled",
    "粗": "Coarse",
    "ヘ": "MouthHe",
    "ぷぅ": "Puu",
    "へへへ": "Hehehe",
    "フルート": "Flute",
}
# fmt: on


# fmt: off
MATERIAL_NAMES: dict[str, str] = {
    "顔": "Face",
    "目": "Eye",
    "髪": "Hair",
    "体": "Body",
    "肌": "Skin",
    "白目": "Sclera",
    "舌": "Tongue",
    "歯": "Teeth",
    "口内": "Mouth",
    "瞳": "Pupil",
    "爪": "Nail",
    "パンツ": "Panties",
    "スカート": "Skirt",
    "ハイライト": "Highlight",
    "まつ毛": "Eyelash",
    "照れ": "Blush",
    "前髪": "Bangs",
    "涙": "Tears",
    "赤面": "Blush2",
    "表情": "Facial",
    "鼻線": "NoseLine",
    "身体": "Body2",
    "メガネ": "Glasses",
    "腕": "Arm",
    "レンズ": "Lens",
    "眉・まぶた": "BrowLash",
    "眉": "Brow",
    "しっぽ": "Tail",
    "猫耳": "CatEar",
    "ねこみみ": "CatEar2",
    "靴": "Shoes",
    "服": "Clothes",
    "リボン": "Ribbon",
    "ベルト": "Belt",
    "手袋": "Gloves",
    "帽子": "Hat",
    "胸": "Chest",
    "下着": "Underwear",
    "ネクタイ": "Necktie",
    "袖": "Sleeve",
    "裾": "Hem",
    "ボタン": "Button",
    "アクセサリ": "Accessory",
    "アクセサリー": "Accessory2",
    "ヘッドホン": "Headphone",
    "イヤリング": "Earring",
    "フリル": "Frill",
    "スパッツ": "Spats",
    "ブーツ": "Boots",
    "ヘッドセット": "Headset",
    "アームカバー": "ArmCover",
    "黒目": "Iris",
    "青ざめ": "Pale",
    "腹黒": "DarkBelly",
    "髪飾り": "HairOrnament",

    # Clothing / accessories
    "シャツ": "Shirt",
    "レース": "Lace",
    "ヘアピン": "Hairpin",
    "指輪": "Ring",
    "ハート": "Heart",
    "きらきら目": "SparkleEyes",
    "きらきら": "Sparkle",
    "ショーツ": "Shorts",
    "ビキニ": "Bikini",
    "マフラー": "Muffler",
    "スカーフ": "Scarf",
    "ぱんつ": "Panties2",
    "ツメ": "Nail2",
    "リンリボン": "RinRibbon",

    # Face / effect
    "髮影": "HairShadow",
    "髮": "Hair2",
    "口腔": "MouthCavity",
    "青ざめる": "Pale",
    "砲身": "GunBarrel",
    "頬タッチ": "CheekTouch",
    "紺": "NavyBlue",
    "目影": "EyeShadow",

    # Simplified Chinese (chunk-based — most compounds auto-resolve)
    "眼睛": "Eye",
    "眼白": "Sclera",
    "头发": "Hair",
    "脸": "Face",
    "皮肤": "Skin",
    "镜片": "Lens",
    "牙齿": "Teeth",
    "舌头": "Tongue",
    "胖次": "Panties",
    "本体": "Main",
    "新建": "New",
}
# fmt: on


# ---------------------------------------------------------------------------
# Chunk-based translation
# ---------------------------------------------------------------------------

# fmt: off
NAME_CHUNKS: dict[str, str] = {
    # Side prefixes (handled specially — become .L/.R suffix)
    "左": ".L",
    "右": ".R",

    # Body parts
    "髪": "Hair",
    "前髪": "Bangs",
    "後髪": "BackHair",
    "横髪": "SideHair",
    "もみあげ": "Sideburn",
    "スカート": "Skirt",
    "スリーブ": "Sleeve",
    "袖": "Sleeve",
    "裾": "Hem",
    "服": "Clothes",
    "胸": "Chest",
    "腕": "Arm",
    "足": "Leg",
    "首": "Neck",
    "頭": "Head",
    "肩": "Shoulder",
    "ひじ": "Elbow",
    "手首": "Wrist",
    "ひざ": "Knee",
    "足首": "Ankle",
    "上半身": "UpperBody",
    "下半身": "LowerBody",
    "親指": "Thumb",
    "人差指": "IndexFinger",
    "人指": "IndexFinger",
    "中指": "MiddleFinger",
    "薬指": "RingFinger",
    "小指": "LittleFinger",
    "手": "Hand",
    "腰": "Waist",
    "つま先": "Toe",

    # Accessories
    "リボン": "Ribbon",
    "ツインテ": "Twintail",
    "ネクタイ": "Necktie",
    "メガネ": "Glasses",
    "ヘッドホン": "Headphone",
    "パーカー": "Hoodie",
    "ベルト": "Belt",
    "おさげ": "Braid",
    "しっぽ": "Tail",
    "おっぱい": "Breast",
    "スカ": "Skirt",  # common abbreviation of スカート

    # Physics/structural
    "錘": "Weight",
    "紐": "String",
    "補助": "Assist",
    "葉": "Leaf",
    "捩": "Twist",
    "操作": "Control",
    "ダミー": "Dummy",
    "重心": "CenterOfGravity",
    "体幹": "Torso",
    "皿": "Plate",
    "連": "Link",
    "前後": "FrontBack",

    # Descriptors (directional)
    "上": "Upper",
    "下": "Lower",
    "前": "Front",
    "後": "Back",
    "横": "Side",
    "先": "Tip",
    "根": "Root",
    "中": "Mid",
    "外": "Outer",
    "内": "Inner",
    "基": "Base",

    # Body / joint helpers
    "間": "Gap",
    "元": "Base",
    "両": "Both",
    "物": "Object",
    "連動": "Linked",
    "武器": "Weapon",
    "かかと": "Heel",
    "まゆ": "Brow",
    "唇": "Lip",
    "上唇": "UpperLip",
    "下唇": "LowerLip",
    "キューブ": "Cube",
    "眉頭": "BrowInner",

    # Adjectives / modifiers
    "低": "Low",
    "高": "High",
    "太": "Thick",
    "細": "Thin",
    "狭い": "Narrow",
    "広い": "Wide",
    "長": "Long",
    "短": "Short",
    "濃い": "Thick",
    "薄い": "Thin",
    "奥": "Deep",

    # Verb modifiers (common in morph/bone names)
    "上げ": "Raise",
    "下げ": "Lower",
    "広げ": "Widen",
    "寄せ": "Pull",
    "開き": "Open",
    "閉じ": "Close",
    "隠し": "Hide",
    "出し": "Out",

    # Expression / emotion chunks
    "赤面": "Blush",
    "睫毛": "Lash",
    "端": "Edge",
    "幅": "Width",
    "歪み": "Distort",
    "はぅ": "Hau",
    "悲しい": "Sad",
    "いやだ": "NoWay",
    "なんで": "Why",
    "星": "Star",
    "花": "Flower",
    "もっと": "More",

    # Material / misc
    "フルート": "Flute",
    "砲身": "GunBarrel",
    "艤装": "Rigging",
    "紺": "NavyBlue",

    # Face
    "舌": "Tongue",
    "口": "Mouth",
    "鼻": "Nose",
    "頬": "Cheek",
    "眉毛": "Eyebrow",
    "まつげ": "Eyelash",
    "上まつげ": "UpperLash",
    "下まつげ": "LowerLash",
    "口角": "MouthCorner",
    "瞳": "Pupil",
    "白目": "Sclera",
    "目": "Eye",
    "耳": "Ear",
    "顔": "Face",
    "歯": "Teeth",
    "上歯": "UpperTeeth",
    "下歯": "LowerTeeth",

    # Materials / descriptors
    "肌": "Skin",
    "体": "Body",
    "身体": "Body",
    "爪": "Nail",
    "靴": "Shoes",
    "照れ": "Blush",
    "涙": "Tear",
    "汗": "Sweat",
    "材質": "Material",
    "金属": "Metal",

    # Colors
    "黒": "Black",
    "白": "White",
    "赤": "Red",
    "青": "Blue",
    "緑": "Green",
    "黄": "Yellow",
    "紫": "Purple",
    "茶": "Brown",

    # Other common
    "全ての親": "ParentNode",
    "センター": "Center",
    "グルーブ": "Groove",
    "キャンセル": "Cancel",
    "両目": "Eyes",
    "飾り": "Ornament",
    "丸": "Round",
    "広": "Wide",
    "腹": "Belly",

    # Katakana loanwords
    "アーム": "Arm",
    "カバー": "Cover",
    "スパッツ": "Spats",
    "ブーツ": "Boots",
    "ヘッドセット": "Headset",

    # Traditional kanji variants (not NFKC-equivalent)
    "髮": "Hair",          # traditional form of 髪

    # Body / anatomy
    "顎": "Jaw",
    "鎖骨": "Clavicle",
    "背中": "Back",
    "尻": "Hip",
    "太もも": "Thigh",
    "乳": "Breast",
    "乳首": "Nipple",
    "膝": "Knee",          # kanji form (already have ひざ)
    "下腹部": "LowerAbdomen",
    "腹部": "Abdomen",

    # Bone modifiers / mechanics
    "戻": "Return",
    "付与": "Grant",
    "変形": "Deform",
    "補正": "Correct",
    "回転": "Rotate",
    "移動": "Move",
    "軸": "Axis",
    "親": "Parent",
    "子": "Child",
    "調整": "Adjust",
    "物理": "Physics",

    # Gaze / vision
    "視線": "Gaze",
    "瞬き": "Blink",

    # Accessories / clothing
    "スカーフ": "Scarf",
    "エリ": "Collar",
    "アホ毛": "Ahoge",
    "モミアゲ": "Sideburn",  # katakana form (already have もみあげ)
    "パーツ": "Parts",
    "シャツ": "Shirt",
    "レース": "Lace",
    "ヘアピン": "Hairpin",
    "ショーツ": "Shorts",
    "マフラー": "Muffler",
    "ビキニ": "Bikini",
    "パレオ": "Pareo",
    "サンダル": "Sandal",
    "指輪": "Ring",
    "ハート": "Heart",
    "ブレスレット": "Bracelet",
    "錨": "Anchor",

    # Katakana loanwords (effects / controls)
    "コントローラ": "Controller",
    "エッジ": "Edge",
    "リズム": "Rhythm",
    "マスター": "Master",
    "フォーカス": "Focus",
    "マイク": "Mic",
    "ハイライト": "Highlight",

    # Effect / display
    "影": "Shadow",
    "額": "Forehead",
    "消": "Off",
    "輪郭線": "Outline",
    "輪郭": "Contour",
    "線": "Line",
    "指": "Finger",
    "凸": "Convex",
    "凹": "Concave",
    "牙": "Fang",
    "縮小": "Shrink",
    "潰れ": "Crush",
    "縦": "Vertical",
    "モード": "Mode",

    # Simplified Chinese (chunks handle most compound words)
    "眼睛": "Eye",
    "头发": "Hair",
    "脸": "Face",
    "皮肤": "Skin",
    "镜片": "Lens",
    "牙齿": "Teeth",
    "舌头": "Tongue",
    "胖次": "Panties",
    "奶子": "Breast",
    "饰": "Ornament",
    "铁": "Metal",
    "内侧": "Inner",
    "甲": "Armor",
    "环": "Ring",
    "带": "Belt",
    "领": "Collar",

    # Simplified Chinese characters (for Chinese-origin models)
    # These decompose compound words like 衣饰铁 → Clothes+Ornament+Metal
    "衣": "Clothes",
    "裙": "Skirt",
    "鞋": "Shoe",
    "袜": "Sock",
    "饰": "Ornament",
    "铁": "Metal",
    "甲": "Armor",
    "环": "Ring",
    "带": "Belt",
    "领": "Collar",
    "脖": "Neck",
    "腿": "Leg",
    "头": "Head",
    "发": "Hair",
    "绿": "Green",
    "红": "Red",
    "光": "Light",
    "内侧": "Inner",

    # Suffixes/markers (NFKC will normalize fullwidth variants)
    "IK": "IK",
    "EX": "EX",
}
# fmt: on

# Compile NFKC-normalized NAME_CHUNKS for matching
_NFKC_CHUNKS: dict[str, str] = {
    unicodedata.normalize("NFKC", k): v for k, v in NAME_CHUNKS.items()
}

# Sort by longest key first for greedy matching
_SORTED_CHUNKS: list[tuple[str, str]] = sorted(
    _NFKC_CHUNKS.items(), key=lambda kv: len(kv[0]), reverse=True
)

# Regex to split names into meaningful chunks:
# CJK ideographs, hiragana runs, katakana runs, ASCII+digit runs, individual symbols
_CHUNK_SPLIT = re.compile(
    r"[\u4E00-\u9FFF\u3400-\u4DBF]+"       # CJK ideographs (kanji)
    r"|[\u3040-\u309F]+"                     # hiragana
    r"|[\u30A0-\u30FF\uFF65-\uFF9F]+"        # katakana (full+half-width)
    r"|[A-Za-z0-9]+"                          # ASCII alphanumeric
    r"|[._\-]"                                # common separators
)

# Pattern to convert MMD-style _L/_R suffixes to Blender .L/.R
_LR_PATTERN = re.compile(r"_([LR])$")


def normalize_lr(name: str) -> str:
    """Convert _L/_R suffix to Blender's .L/.R convention."""
    return _LR_PATTERN.sub(r".\1", name)


def translate(name_j: str) -> str | None:
    """Look up English name for a Japanese bone name. Returns None if not found."""
    return BONE_NAMES.get(name_j)


def translate_morph(name_j: str) -> str | None:
    """Look up English name for a Japanese morph name. Returns None if not found."""
    return MORPH_NAMES.get(name_j)


def _looks_english(text: str) -> bool:
    """Check if text is pure English (no CJK/kana characters).

    Any CJK ideograph, hiragana, or katakana means it's not English.
    Mixed names like 'Nipple凸' or '胸_L' are rejected — chunk translation
    handles them better.
    """
    if not text:
        return False
    non_ws = [c for c in text if not c.isspace()]
    if not non_ws:
        return False
    for c in non_ws:
        cp = ord(c)
        # CJK ideographs, hiragana, katakana, halfwidth katakana
        if (0x3000 <= cp <= 0x9FFF or 0xF900 <= cp <= 0xFAFF
                or 0xFF65 <= cp <= 0xFF9F):
            return False
    # Must have at least one alphanumeric char (not just punctuation)
    return any(c.isalnum() for c in non_ws)


def translate_chunks(name_j: str) -> str | None:
    """Translate a Japanese name chunk-by-chunk.

    1. NFKC normalize (full-width → ASCII)
    2. Extract side prefix (左/右) → .L/.R suffix
    3. Greedy match against NAME_CHUNKS table (longest match first)
    4. Untranslated chunks pass through as-is
    5. Return None if no chunks were translated (fully ASCII already, etc.)

    Examples:
        右スリーブ１IK → Sleeve1IK.R
        左HairA15 → HairA15.L
        後髪3.右 → BackHair3.R
        上まつげ2 → UpperLash2
    """
    if not name_j:
        return None

    # NFKC normalize (e.g. ＩＫ → IK, １ → 1)
    norm = unicodedata.normalize("NFKC", name_j)

    # Extract side prefix/suffix
    side_suffix = ""
    # Check for side markers and remove them
    # Handle prefix (左xxx, 右xxx) and suffix (xxx.左, xxx 右, xxx左, etc.)
    working = norm
    if working.startswith("左"):
        side_suffix = ".L"
        working = working[1:]
    elif working.startswith("右"):
        side_suffix = ".R"
        working = working[1:]
    elif working.endswith((".左", "_左", " 左")):
        side_suffix = ".L"
        working = working[:-2]
    elif working.endswith((".右", "_右", " 右")):
        side_suffix = ".R"
        working = working[:-2]
    elif working.endswith("左"):
        side_suffix = ".L"
        working = working[:-1]
    elif working.endswith("右"):
        side_suffix = ".R"
        working = working[:-1]

    if not working:
        # Name was just 左 or 右
        return ("L" + side_suffix) if side_suffix else None

    # Greedy chunk translation
    result_parts: list[str] = []
    any_translated = bool(side_suffix)  # Side extraction counts as a translation
    pos = 0
    while pos < len(working):
        matched = False
        for chunk_jp, chunk_en in _SORTED_CHUNKS:
            if chunk_jp in (".L", ".R"):
                continue  # Skip side markers in chunk matching
            if working[pos:pos + len(chunk_jp)] == chunk_jp:
                result_parts.append(chunk_en)
                pos += len(chunk_jp)
                matched = True
                any_translated = True
                break
        if not matched:
            # Pass through the character as-is
            result_parts.append(working[pos])
            pos += 1

    if not any_translated:
        return None

    # Join parts — remove dots/underscores between CamelCase-joined chunks
    # but preserve explicit separators and numbers
    joined = "".join(result_parts)
    return joined + side_suffix


def resolve_name(name_j: str, name_e: str, table: dict[str, str]) -> str:
    """Unified name resolution for any MMD object type.

    Priority:
    1. Full-name table lookup (after NFKC normalization)
    2. Non-empty name_e (if it looks English — mostly ASCII)
    3. Chunk-based translation of name_j
    4. name_j as-is (fallback)
    """
    if not name_j:
        return name_e if name_e else ""

    # 1. Full-name table lookup (try raw first, then NFKC)
    result = table.get(name_j)
    if result:
        return result
    nfkc = unicodedata.normalize("NFKC", name_j)
    if nfkc != name_j:
        result = table.get(nfkc)
        if result:
            return result

    # 2. Non-empty name_e that looks English
    if name_e and name_e.strip():
        cleaned = name_e.strip()
        if _looks_english(cleaned):
            return normalize_lr(cleaned)

    # 3. Chunk-based translation
    chunked = translate_chunks(name_j)
    if chunked:
        return chunked

    # 4. Fallback: Japanese name as-is
    return name_j


# Legacy wrapper (used by armature.py tests)
def resolve_morph_name(name_j: str, name_e: str) -> str:
    """Choose English display name for a morph. Uses resolve_name internally."""
    return resolve_name(name_j, name_e, MORPH_NAMES)
