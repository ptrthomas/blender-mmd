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

# Full-name override tables. Most translation is handled by NAME_CHUNKS.
# These tables are only for names where chunk decomposition gives no result
# or a misleading result.

BONE_NAMES: dict[str, str] = {}


# fmt: off
MORPH_NAMES: dict[str, str] = {
    # Only names where chunks produce no result (single hiragana/katakana,
    # symbols, unique onomatopoeia). Everything else handled by NAME_CHUNKS.

    # Vowels (single hiragana — too short/ambiguous for chunks)
    "あ": "A", "い": "I", "う": "U", "え": "E", "お": "O", "ん": "N",
    "あ２": "A2", "あ２": "A2", "あ2": "A2b", "あ３": "A3", "あ４": "A4",
    "い２": "I2", "い2": "I2b", "い３": "I3", "いB": "Ib",
    "う２": "U2", "うB": "Ub", "え２": "E2",
    "いー": "Teeth",  # い + long vowel = teeth-showing expression
    "わ": "Wa", "へ": "He",

    # Single katakana
    "ア": "Aa", "オ": "Oo", "ヘ": "MouthHe", "ワ": "MouthWa", "ワE": "WaE",

    # Symbols
    "ω": "MouthOmega", "ω□": "MouthOmegaSquare",
    "∧": "MouthLambda", "□": "MouthSquare",
    "▲": "MouthTriangle", "△": "MouthTriangle2",

    # Unique onomatopoeia / expressions (no chunk decomposition possible)
    "がーん": "GaanShock", "ガーン": "Shocked",
    "しいたけ": "Shiitake", "なごみ": "Gentle", "なぬ！": "What!",
    "に～": "Ni", "はぁと": "HeartEyes", "ぷぅ": "Puu",
    "へへへ": "Hehehe",
    "キッ": "Kii", "キリッ": "SharpLook", "キリッ２": "SharpLook2",
    "コッチミンナ": "LookHere",
    "toon深まる": "ToonDeepen",

    # Halfwidth kana edge cases (no NFKC equivalent in chunks)
    "ｷﾘ?1": "SharpEyes", "ｷﾞｭｯ": "SquintTight",
}
# fmt: on


# fmt: off
MATERIAL_NAMES: dict[str, str] = {
    # Only compound words where chunk decomposition gives misleading results.
    # Everything else is handled by NAME_CHUNKS.
    "黒目": "Iris",          # chunks: BlackEye
    "帽子": "Hat",           # chunks: 帽Child
    "手袋": "Gloves",        # chunks: Hand袋
    "下着": "Underwear",     # chunks: Lower着
    "本体": "Main",          # chunks: 本Body
    "眼白": "Sclera",        # Chinese: chunks: 眼White
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

    # Accessories / clothing
    "リボン": "Ribbon",
    "ツインテ": "Twintail",
    "ネクタイ": "Necktie",
    "チェーン": "Chain",
    "タイ": "Tie",
    "メガネ": "Glasses",
    "ヘッドホン": "Headphone",
    "パーカー": "Hoodie",
    "ベルト": "Belt",
    "おさげ": "Braid",
    "しっぽ": "Tail",
    "おっぱい": "Breast",
    "スカ": "Skirt",  # common abbreviation of スカート
    "パンツ": "Panties",
    "ぱんつ": "Panties",
    "ボタン": "Button",
    "フリル": "Frill",
    "レンズ": "Lens",
    "イヤリング": "Earring",
    "アクセサリー": "Accessory",
    "アクセサリ": "Accessory",
    "きらきら": "Sparkle",
    "ツメ": "Nail",    # katakana form
    "まつ毛": "Eyelash",  # alternate of まつげ
    "タッチ": "Touch",
    "猫": "Cat",
    "ねこ": "Cat",
    "みみ": "Ear",     # hiragana form
    "表情": "Facial",
    "眉": "Brow",      # kanji form (also have まゆ)
    "まぶた": "Eyelid",
    "青ざめ": "Pale",
    "青ざめる": "Pale",
    "腔": "Cavity",
    "新建": "New",      # Chinese
    "着": "Wear",
    "水玉": "Polkadot",
    "襟": "Collar",
    "カフス": "Cuffs",
    "ブローチ": "Brooch",
    "底": "Bottom",
    "穴": "Hole",
    "背": "Back",

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

    # Expression / emotion chunks (shared by morphs and bones)
    "まばたき": "Blink",
    "笑い": "Smile",
    "怒り": "Angry",
    "困る": "Troubled",
    "にこり": "Cheerful",
    "にっこり": "NiceSmile",
    "にやり": "Smirk",
    "ウィンク": "Wink",
    "びっくり": "Surprised",
    "ぺろっ": "TongueOut",
    "もぐもぐ": "Chewing",
    "切ない": "Bittersweet",
    "真面目": "Serious",
    "困惑": "Puzzled",
    "平行": "Flat",
    "暗さ": "Darkness",
    "動く": "Move",
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
    "笑": "Smile",
    "怒": "Angry",
    "粗": "Coarse",
    "無し": "No",
    "大": "Big",
    "小": "Small",
    "近": "Close",
    "離": "Far",
    "筋": "Bridge",
    "灰": "Gray",
    "すぼめ": "Pucker",
    "開け": "Open",
    "カメラ": "Camera",
    "三角": "Triangle",
    "眼鏡": "Glasses",
    "あご": "Jaw",
    "中心": "Center",
    "鏡": "Mirror",

    # Material / misc
    "フルート": "Flute",
    "砲身": "GunBarrel",
    "艤装": "Rigging",
    "紺": "NavyBlue",
    "水着": "Swimsuit",

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

    # Simplified Chinese
    "眼睛": "Eye",
    "头发": "Hair",
    "脸": "Face",
    "皮肤": "Skin",
    "镜片": "Lens",
    "牙齿": "Teeth",
    "舌头": "Tongue",
    "胖次": "Panties",
    "奶子": "Breast",
    "内侧": "Inner",
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
    """Translate a Japanese bone name. Checks table then chunks."""
    result = BONE_NAMES.get(name_j) or translate_chunks(name_j)
    return result


def translate_morph(name_j: str) -> str | None:
    """Translate a Japanese morph name. Checks table then chunks."""
    result = MORPH_NAMES.get(name_j) or translate_chunks(name_j)
    return result


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
    2. Chunk-based translation of name_j (consistent CamelCase)
    3. Non-empty name_e (if it looks English — fallback for unknown chunks)
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

    # 2. Chunk-based translation (preferred — produces consistent CamelCase)
    chunked = translate_chunks(name_j)
    if chunked:
        return chunked

    # 3. Non-empty name_e that looks English (fallback for untranslatable chunks)
    if name_e and name_e.strip():
        cleaned = name_e.strip()
        if _looks_english(cleaned):
            return normalize_lr(cleaned)

    # 4. Fallback: Japanese name as-is
    return name_j


# Legacy wrapper (used by armature.py tests)
def resolve_morph_name(name_j: str, name_e: str) -> str:
    """Choose English display name for a morph. Uses resolve_name internally."""
    return resolve_name(name_j, name_e, MORPH_NAMES)
