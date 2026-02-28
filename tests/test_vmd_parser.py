"""VMD parser tests — parsing, structure validation, and coordinate conversion."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from blender_mmd.vmd.parser import parse, _read_text
from blender_mmd.vmd.types import BoneKeyframe, MorphKeyframe, VmdMotion

SAMPLES_DIR = Path(__file__).parent / "samples"
TEST_VMD = SAMPLES_DIR / "galaxias.vmd"


@pytest.fixture
def vmd_path() -> Path:
    assert TEST_VMD.exists(), f"Test VMD not found: {TEST_VMD}"
    return TEST_VMD


@pytest.fixture
def parsed_vmd(vmd_path) -> VmdMotion:
    return parse(vmd_path)


# ---------------------------------------------------------------------------
# Synthetic VMD creation helpers
# ---------------------------------------------------------------------------

def _make_vmd_bytes(
    model_name: str = "TestModel",
    bone_keyframes: list[tuple[str, int, tuple, tuple]] | None = None,
    morph_keyframes: list[tuple[str, int, float]] | None = None,
) -> bytes:
    """Build a minimal VMD binary for testing.

    bone_keyframes: list of (name, frame, (lx,ly,lz), (rx,ry,rz,rw))
    morph_keyframes: list of (name, frame, weight)
    """
    buf = bytearray()

    # Header: 30 bytes signature + 20 bytes model name
    sig = b"Vocaloid Motion Data 0002"
    buf.extend(sig.ljust(30, b"\x00"))
    name_bytes = model_name.encode("cp932")[:20]
    buf.extend(name_bytes.ljust(20, b"\x00"))

    # Bone keyframes
    bk = bone_keyframes or []
    buf.extend(struct.pack("<I", len(bk)))
    for name, frame, loc, rot in bk:
        name_b = name.encode("cp932")[:15]
        buf.extend(name_b.ljust(15, b"\x00"))
        buf.extend(struct.pack("<I3f4f", frame, *loc, *rot))
        buf.extend(b"\x00" * 64)  # interpolation

    # Morph keyframes
    mk = morph_keyframes or []
    buf.extend(struct.pack("<I", len(mk)))
    for name, frame, weight in mk:
        name_b = name.encode("cp932")[:15]
        buf.extend(name_b.ljust(15, b"\x00"))
        buf.extend(struct.pack("<If", frame, weight))

    # Camera keyframes (empty)
    buf.extend(struct.pack("<I", 0))

    return bytes(buf)


# ---------------------------------------------------------------------------
# Text decoding
# ---------------------------------------------------------------------------

class TestReadText:
    def test_basic_ascii(self):
        assert _read_text(b"Hello\x00\x00\x00") == "Hello"

    def test_null_terminated(self):
        assert _read_text(b"ABC\x00DEF") == "ABC"

    def test_japanese_cp932(self):
        text = "センター"
        encoded = text.encode("cp932")
        assert _read_text(encoded) == text

    def test_empty(self):
        assert _read_text(b"\x00\x00\x00") == ""


# ---------------------------------------------------------------------------
# Parse real VMD file
# ---------------------------------------------------------------------------

class TestParseRealVmd:
    def test_returns_vmd_motion(self, parsed_vmd):
        assert isinstance(parsed_vmd, VmdMotion)

    def test_model_name(self, parsed_vmd):
        assert parsed_vmd.model_name  # non-empty

    def test_has_bone_keyframes(self, parsed_vmd):
        assert len(parsed_vmd.bone_keyframes) > 0

    def test_bone_keyframe_structure(self, parsed_vmd):
        kf = parsed_vmd.bone_keyframes[0]
        assert isinstance(kf, BoneKeyframe)
        assert isinstance(kf.bone_name, str)
        assert len(kf.bone_name) > 0
        assert isinstance(kf.frame, int)
        assert kf.frame >= 0
        assert len(kf.location) == 3
        assert len(kf.rotation) == 4
        assert len(kf.interpolation) == 64

    def test_bone_names_are_strings(self, parsed_vmd):
        for kf in parsed_vmd.bone_keyframes[:100]:
            assert isinstance(kf.bone_name, str)
            assert len(kf.bone_name) > 0

    def test_quaternion_not_all_zero(self, parsed_vmd):
        """All-zero quaternions should have been fixed to identity."""
        for kf in parsed_vmd.bone_keyframes:
            r = kf.rotation
            assert not (r[0] == 0 and r[1] == 0 and r[2] == 0 and r[3] == 0)

    def test_known_bone_names_present(self, parsed_vmd):
        """galaxias.vmd should contain standard MMD bone names."""
        bone_names = {kf.bone_name for kf in parsed_vmd.bone_keyframes}
        # These should be in the test file
        assert "センター" in bone_names
        assert "上半身" in bone_names

    def test_multiple_frames_per_bone(self, parsed_vmd):
        """At least some bones should have multiple keyframes."""
        from collections import Counter
        counts = Counter(kf.bone_name for kf in parsed_vmd.bone_keyframes)
        max_count = max(counts.values())
        assert max_count > 1, "Expected at least one bone with multiple keyframes"


# ---------------------------------------------------------------------------
# Synthetic VMD tests
# ---------------------------------------------------------------------------

class TestParseSynthetic:
    def test_empty_motion(self, tmp_path):
        vmd_bytes = _make_vmd_bytes()
        path = tmp_path / "empty.vmd"
        path.write_bytes(vmd_bytes)
        vmd = parse(path)
        assert vmd.model_name == "TestModel"
        assert len(vmd.bone_keyframes) == 0
        assert len(vmd.morph_keyframes) == 0

    def test_single_bone_keyframe(self, tmp_path):
        vmd_bytes = _make_vmd_bytes(
            bone_keyframes=[
                ("センター", 0, (1.0, 2.0, 3.0), (0.0, 0.0, 0.0, 1.0)),
            ]
        )
        path = tmp_path / "one_bone.vmd"
        path.write_bytes(vmd_bytes)
        vmd = parse(path)
        assert len(vmd.bone_keyframes) == 1
        kf = vmd.bone_keyframes[0]
        assert kf.bone_name == "センター"
        assert kf.frame == 0
        assert kf.location == pytest.approx((1.0, 2.0, 3.0))
        assert kf.rotation == pytest.approx((0.0, 0.0, 0.0, 1.0))

    def test_multiple_bone_keyframes(self, tmp_path):
        vmd_bytes = _make_vmd_bytes(
            bone_keyframes=[
                ("センター", 0, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0)),
                ("センター", 30, (1.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0)),
                ("上半身", 0, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0)),
            ]
        )
        path = tmp_path / "multi.vmd"
        path.write_bytes(vmd_bytes)
        vmd = parse(path)
        assert len(vmd.bone_keyframes) == 3

    def test_morph_keyframes(self, tmp_path):
        vmd_bytes = _make_vmd_bytes(
            morph_keyframes=[
                ("あ", 0, 0.0),
                ("あ", 15, 1.0),
                ("あ", 30, 0.0),
            ]
        )
        path = tmp_path / "morphs.vmd"
        path.write_bytes(vmd_bytes)
        vmd = parse(path)
        assert len(vmd.morph_keyframes) == 3
        assert vmd.morph_keyframes[0].morph_name == "あ"
        assert vmd.morph_keyframes[0].weight == pytest.approx(0.0)
        assert vmd.morph_keyframes[1].weight == pytest.approx(1.0)

    def test_zero_quaternion_fixed(self, tmp_path):
        """All-zero quaternion should become identity (0,0,0,1)."""
        vmd_bytes = _make_vmd_bytes(
            bone_keyframes=[
                ("Test", 0, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 0.0)),
            ]
        )
        path = tmp_path / "zero_quat.vmd"
        path.write_bytes(vmd_bytes)
        vmd = parse(path)
        assert vmd.bone_keyframes[0].rotation == pytest.approx(
            (0.0, 0.0, 0.0, 1.0)
        )

    def test_model_name_decoded(self, tmp_path):
        vmd_bytes = _make_vmd_bytes(model_name="初音ミク")
        path = tmp_path / "jp_name.vmd"
        path.write_bytes(vmd_bytes)
        vmd = parse(path)
        assert vmd.model_name == "初音ミク"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_invalid_signature(self, tmp_path):
        path = tmp_path / "bad.vmd"
        path.write_bytes(b"Not a VMD file" + b"\x00" * 50)
        with pytest.raises(ValueError, match="Not a VMD"):
            parse(path)

    def test_too_small(self, tmp_path):
        path = tmp_path / "tiny.vmd"
        path.write_bytes(b"\x00" * 10)
        with pytest.raises(ValueError, match="too small"):
            parse(path)
