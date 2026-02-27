"""VMD binary parser — reads Vocaloid Motion Data files.

VMD is a sequential binary format with fixed-size records per section.
All multi-byte integers are little-endian.  String fields are CP932
(Shift-JIS) encoded, null-padded to a fixed width.

This parser reads bone keyframes, morph keyframes, and camera keyframes.
Light, shadow, and property (IK toggle) sections are skipped.
No coordinate conversion — raw MMD values are returned.
"""

from __future__ import annotations

import logging
import struct
from pathlib import Path

from .types import (
    BoneKeyframe,
    CameraKeyframe,
    MorphKeyframe,
    PropertyKeyframe,
    VmdMotion,
)

log = logging.getLogger("blender_mmd")

# VMD signature (first 30 bytes of the file)
_SIGNATURE = b"Vocaloid Motion Data 0002"


def _read_text(data: bytes, encoding: str = "cp932") -> str:
    """Decode a null-padded CP932 string field."""
    # Find null terminator
    end = data.find(b"\x00")
    if end >= 0:
        data = data[:end]
    try:
        return data.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        return data.decode(encoding, errors="replace")


def parse(filepath: str | Path) -> VmdMotion:
    """Parse a VMD file and return a VmdMotion object.

    Raises ValueError if the file is not a valid VMD file.
    """
    filepath = Path(filepath)
    buf = filepath.read_bytes()
    pos = 0

    # --- Header (50 bytes) ---
    if len(buf) < 50:
        raise ValueError(f"File too small for VMD header: {len(buf)} bytes")

    signature = buf[0:30]
    if not signature.startswith(_SIGNATURE):
        raise ValueError(
            f"Not a VMD file (bad signature): {signature[:25]!r}"
        )

    model_name = _read_text(buf[30:50])
    pos = 50

    log.info("Parsing VMD: %s (model: %s)", filepath.name, model_name)

    # --- Bone keyframes ---
    bone_keyframes, pos = _read_bone_keyframes(buf, pos)

    # --- Morph keyframes ---
    morph_keyframes, pos = _read_morph_keyframes(buf, pos)

    # --- Camera keyframes (optional) ---
    camera_keyframes, pos = _read_camera_keyframes(buf, pos)

    # --- Light keyframes (skip) ---
    pos = _skip_section(buf, pos, 28)  # 28 bytes per light keyframe

    # --- Shadow keyframes (skip) ---
    pos = _skip_section(buf, pos, 9)  # 9 bytes per shadow keyframe

    # --- Property keyframes (IK toggle) ---
    property_keyframes, pos = _read_property_keyframes(buf, pos)

    log.info(
        "VMD parsed: %d bone, %d morph, %d camera, %d property keyframes",
        len(bone_keyframes),
        len(morph_keyframes),
        len(camera_keyframes),
        len(property_keyframes),
    )

    return VmdMotion(
        model_name=model_name,
        bone_keyframes=bone_keyframes,
        morph_keyframes=morph_keyframes,
        camera_keyframes=camera_keyframes,
        property_keyframes=property_keyframes,
    )


def _read_bone_keyframes(
    buf: bytes, pos: int
) -> tuple[list[BoneKeyframe], int]:
    """Read the bone keyframe section."""
    if pos + 4 > len(buf):
        return [], pos

    (count,) = struct.unpack_from("<I", buf, pos)
    pos += 4

    # Each bone keyframe: 15 (name) + 4 (frame) + 12 (loc) + 16 (rot) + 64 (interp) = 111 bytes
    # Total per entry: 15 + 111 = but let's just read sequentially
    ENTRY_SIZE = 15 + 4 + 12 + 16 + 64  # = 111 total after name

    keyframes: list[BoneKeyframe] = []
    for _ in range(count):
        if pos + 15 + 4 + 12 + 16 + 64 > len(buf):
            log.warning("Truncated bone keyframe section at entry %d", len(keyframes))
            break

        bone_name = _read_text(buf[pos : pos + 15])
        pos += 15

        frame, lx, ly, lz, rx, ry, rz, rw = struct.unpack_from(
            "<I3f4f", buf, pos
        )
        pos += 4 + 12 + 16

        interp = buf[pos : pos + 64]
        pos += 64

        # Fix all-zero quaternion → identity
        if rx == 0.0 and ry == 0.0 and rz == 0.0 and rw == 0.0:
            rw = 1.0

        keyframes.append(
            BoneKeyframe(
                bone_name=bone_name,
                frame=frame,
                location=(lx, ly, lz),
                rotation=(rx, ry, rz, rw),
                interpolation=bytes(interp),
            )
        )

    return keyframes, pos


def _read_morph_keyframes(
    buf: bytes, pos: int
) -> tuple[list[MorphKeyframe], int]:
    """Read the morph/shape key keyframe section."""
    if pos + 4 > len(buf):
        return [], pos

    (count,) = struct.unpack_from("<I", buf, pos)
    pos += 4

    keyframes: list[MorphKeyframe] = []
    for _ in range(count):
        if pos + 15 + 4 + 4 > len(buf):
            log.warning(
                "Truncated morph keyframe section at entry %d", len(keyframes)
            )
            break

        morph_name = _read_text(buf[pos : pos + 15])
        pos += 15

        frame, weight = struct.unpack_from("<If", buf, pos)
        pos += 8

        keyframes.append(
            MorphKeyframe(
                morph_name=morph_name,
                frame=frame,
                weight=weight,
            )
        )

    return keyframes, pos


def _read_camera_keyframes(
    buf: bytes, pos: int
) -> tuple[list[CameraKeyframe], int]:
    """Read the camera keyframe section (optional)."""
    if pos + 4 > len(buf):
        return [], pos

    (count,) = struct.unpack_from("<I", buf, pos)
    pos += 4

    keyframes: list[CameraKeyframe] = []
    for _ in range(count):
        if pos + 61 > len(buf):
            log.warning(
                "Truncated camera keyframe section at entry %d",
                len(keyframes),
            )
            break

        (
            frame,
            distance,
            lx, ly, lz,
            rx, ry, rz,
        ) = struct.unpack_from("<If3f3f", buf, pos)
        pos += 4 + 4 + 12 + 12

        interp = buf[pos : pos + 24]
        pos += 24

        fov, persp = struct.unpack_from("<Ib", buf, pos)
        pos += 5

        keyframes.append(
            CameraKeyframe(
                frame=frame,
                distance=distance,
                location=(lx, ly, lz),
                rotation=(rx, ry, rz),
                interpolation=bytes(interp),
                fov=fov,
                orthographic=bool(persp),
            )
        )

    return keyframes, pos


def _skip_section(buf: bytes, pos: int, entry_size: int) -> int:
    """Skip a fixed-size VMD section (light, shadow, etc.)."""
    if pos + 4 > len(buf):
        return pos
    (count,) = struct.unpack_from("<I", buf, pos)
    pos += 4
    pos += count * entry_size
    return min(pos, len(buf))


def _read_property_keyframes(
    buf: bytes, pos: int
) -> tuple[list[PropertyKeyframe], int]:
    """Read the property keyframe section (IK toggle + visibility).

    Format per entry:
        frame: uint32
        visible: uint8 (0 or 1)
        ik_count: uint32
        per IK state:
            name: 20 bytes (CP932, null-padded)
            enabled: uint8
    """
    if pos + 4 > len(buf):
        return [], pos

    (count,) = struct.unpack_from("<I", buf, pos)
    pos += 4

    keyframes: list[PropertyKeyframe] = []
    for _ in range(count):
        if pos + 5 > len(buf):
            log.warning(
                "Truncated property keyframe section at entry %d",
                len(keyframes),
            )
            break

        (frame,) = struct.unpack_from("<I", buf, pos)
        pos += 4
        (visible_byte,) = struct.unpack_from("<B", buf, pos)
        pos += 1

        if pos + 4 > len(buf):
            break
        (ik_count,) = struct.unpack_from("<I", buf, pos)
        pos += 4

        ik_states: list[tuple[str, bool]] = []
        for _ in range(ik_count):
            if pos + 21 > len(buf):
                break
            # IK bone name: 20 bytes CP932
            ik_name = _read_text(buf[pos : pos + 20])
            pos += 20
            (enabled_byte,) = struct.unpack_from("<B", buf, pos)
            pos += 1
            ik_states.append((ik_name, bool(enabled_byte)))

        keyframes.append(
            PropertyKeyframe(
                frame=frame,
                visible=bool(visible_byte),
                ik_states=ik_states,
            )
        )

    return keyframes, pos
