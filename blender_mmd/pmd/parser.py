"""PMD binary parser — reads PMD 1.0 files and outputs pmx.types.Model.

PMD is the predecessor format to PMX.  This parser converts everything into
the same dataclasses used by the PMX parser so the entire downstream pipeline
(armature, mesh, materials, physics, VMD) works unchanged.

Coordinate conversion (MMD left-handed Y-up → Blender right-handed Z-up):
    Position/Offset: (x, y, z) → (x, z, y)   [swap Y↔Z]
    Normal:          (x, y, z) → (x, z, y)
    Rotation (euler):(x, y, z) → (x, z, y)
"""

from __future__ import annotations

import logging
import math
import struct
from pathlib import Path
from typing import BinaryIO

from ..pmx.types import (
    Bone,
    BoneWeightBDEF1,
    BoneWeightBDEF2,
    DisplayFrame,
    DisplayItem,
    Header,
    IKLink,
    Joint,
    JointMode,
    Material,
    Model,
    Morph,
    MorphCategory,
    MorphType,
    RigidBody,
    RigidMode,
    RigidShape,
    Texture,
    Vertex,
    VertexMorphOffset,
    WeightType,
)

log = logging.getLogger("blender_mmd")


# ---------------------------------------------------------------------------
# Binary reader helper
# ---------------------------------------------------------------------------

class _Reader:
    """Low-level binary reader for PMD fixed-size fields."""

    __slots__ = ("_f",)

    def __init__(self, f: BinaryIO) -> None:
        self._f = f

    def read_bytes(self, n: int) -> bytes:
        data = self._f.read(n)
        if len(data) != n:
            raise EOFError(f"Expected {n} bytes, got {len(data)}")
        return data

    def read_int8(self) -> int:
        return struct.unpack("<b", self.read_bytes(1))[0]

    def read_uint8(self) -> int:
        return struct.unpack("<B", self.read_bytes(1))[0]

    def read_int16(self) -> int:
        return struct.unpack("<h", self.read_bytes(2))[0]

    def read_uint16(self) -> int:
        return struct.unpack("<H", self.read_bytes(2))[0]

    def read_int32(self) -> int:
        return struct.unpack("<i", self.read_bytes(4))[0]

    def read_uint32(self) -> int:
        return struct.unpack("<I", self.read_bytes(4))[0]

    def read_float(self) -> float:
        return struct.unpack("<f", self.read_bytes(4))[0]

    def read_vec2(self) -> tuple[float, float]:
        return struct.unpack("<2f", self.read_bytes(8))

    def read_vec3(self) -> tuple[float, float, float]:
        return struct.unpack("<3f", self.read_bytes(12))

    def read_vec4(self) -> tuple[float, float, float, float]:
        return struct.unpack("<4f", self.read_bytes(16))

    def read_str(self, size: int) -> str:
        """Read a fixed-size null-terminated CP932 string."""
        buf = self.read_bytes(size)
        # 0xFD is used as empty marker in some PMD files
        if buf[0:1] == b"\xfd":
            return ""
        return buf.split(b"\x00")[0].decode("cp932", errors="replace")

    def remaining(self) -> int:
        """Return bytes remaining in stream (for optional section detection)."""
        pos = self._f.tell()
        self._f.seek(0, 2)
        end = self._f.tell()
        self._f.seek(pos)
        return end - pos


# ---------------------------------------------------------------------------
# Coordinate conversion helpers
# ---------------------------------------------------------------------------

def _pos(x: float, y: float, z: float) -> tuple[float, float, float]:
    """MMD position → Blender position: (x, y, z) → (x, z, y)"""
    return (x, z, y)


def _rot(x: float, y: float, z: float) -> tuple[float, float, float]:
    """MMD euler rotation → Blender euler rotation: (x, y, z) → (x, z, y)"""
    return (x, z, y)


def _pos3(v: tuple[float, float, float]) -> tuple[float, float, float]:
    return _pos(*v)


def _rot3(v: tuple[float, float, float]) -> tuple[float, float, float]:
    return _rot(*v)


# ---------------------------------------------------------------------------
# Section parsers
# ---------------------------------------------------------------------------

def _parse_header(r: _Reader) -> tuple[str, str]:
    """Parse PMD header. Returns (model_name, comment)."""
    magic = r.read_bytes(3)
    if magic != b"Pmd":
        raise ValueError(f"Not a PMD file: magic={magic!r}")

    version = r.read_float()
    if version != 1.0:
        raise ValueError(f"Unsupported PMD version: {version}")

    model_name = r.read_str(20)
    comment = r.read_str(256)
    return model_name, comment


def _parse_vertices(r: _Reader) -> list[Vertex]:
    count = r.read_uint32()
    log.debug("Parsing %d PMD vertices", count)
    vertices = []
    for _ in range(count):
        px, py, pz = r.read_vec3()
        nx, ny, nz = r.read_vec3()
        uv = r.read_vec2()
        bone1 = r.read_uint16()
        bone2 = r.read_uint16()
        weight_byte = r.read_uint8()  # 0-100
        edge_flag = r.read_uint8()  # 0 = edge on, 1 = edge off

        # Weight conversion
        w = weight_byte / 100.0
        if bone1 == bone2:
            bw = BoneWeightBDEF1(bone=bone1)
            wt = WeightType.BDEF1
        else:
            bw = BoneWeightBDEF2(bone1=bone1, bone2=bone2, weight=w)
            wt = WeightType.BDEF2

        # Edge flag: PMD 0=on → edge_scale=1.0; PMD 1=off → edge_scale=0.0
        edge_scale = 0.0 if edge_flag else 1.0

        vertices.append(Vertex(
            position=_pos(px, py, pz),
            normal=_pos(nx, ny, nz),
            uv=uv,
            additional_uvs=[],
            weight_type=wt,
            weight=bw,
            edge_scale=edge_scale,
        ))
    return vertices


def _parse_faces(r: _Reader) -> list[tuple[int, int, int]]:
    index_count = r.read_uint32()
    log.debug("Parsing %d PMD face indices (%d triangles)", index_count, index_count // 3)
    faces = []
    for _ in range(index_count // 3):
        f1 = r.read_uint16()
        f2 = r.read_uint16()
        f3 = r.read_uint16()
        # Reverse winding order (MMD → Blender)
        faces.append((f3, f2, f1))
    return faces


def _parse_materials(r: _Reader) -> list[dict]:
    """Parse PMD materials into intermediate dicts (converted to Material later)."""
    count = r.read_uint32()
    log.debug("Parsing %d PMD materials", count)
    materials = []
    for _ in range(count):
        diffuse = r.read_vec4()  # RGBA
        shininess = r.read_float()
        specular = r.read_vec3()
        ambient = r.read_vec3()
        toon_index = r.read_uint8()
        edge_flag = r.read_uint8()
        face_count = r.read_uint32()  # vertex index count (not triangle count)
        texture_path = r.read_str(20)

        materials.append({
            "diffuse": diffuse,
            "shininess": shininess,
            "specular": specular,
            "ambient": ambient,
            "toon_index": toon_index,
            "edge_flag": edge_flag,
            "face_count": face_count,
            "texture_path": texture_path,
        })
    return materials


def _parse_bones(r: _Reader) -> list[dict]:
    """Parse PMD bones into intermediate dicts."""
    count = r.read_uint16()
    log.debug("Parsing %d PMD bones", count)
    bones = []
    for _ in range(count):
        name = r.read_str(20)
        parent = r.read_uint16()  # 0xFFFF = no parent
        tail_bone = r.read_uint16()
        bone_type = r.read_uint8()
        ik_bone = r.read_uint16()  # or signed short for type 9
        px, py, pz = r.read_vec3()

        # 0xFFFF → -1 for no parent
        if parent == 0xFFFF:
            parent = -1

        bones.append({
            "name": name,
            "parent": parent,
            "tail_bone": tail_bone,
            "type": bone_type,
            "ik_bone": ik_bone,
            "position": (px, py, pz),
        })
    return bones


def _parse_iks(r: _Reader) -> list[dict]:
    """Parse PMD IK chains."""
    count = r.read_uint16()
    log.debug("Parsing %d PMD IK chains", count)
    iks = []
    for _ in range(count):
        bone_index = r.read_uint16()
        target_bone = r.read_uint16()
        chain_length = r.read_uint8()
        iterations = r.read_uint16()
        control_weight = r.read_float()
        child_bones = [r.read_uint16() for _ in range(chain_length)]

        iks.append({
            "bone": bone_index,
            "target": target_bone,
            "iterations": iterations,
            "control_weight": control_weight,
            "child_bones": child_bones,
        })
    return iks


def _parse_morphs(r: _Reader) -> list[dict]:
    """Parse PMD morphs (base + relative)."""
    count = r.read_uint16()
    log.debug("Parsing %d PMD morphs", count)
    morphs = []
    for _ in range(count):
        name = r.read_str(20)
        data_count = r.read_uint32()
        morph_type = r.read_uint8()  # 0=base, 1=eyebrow, 2=eye, 3=mouth, 4=other
        data = []
        for _ in range(data_count):
            index = r.read_uint32()
            ox, oy, oz = r.read_vec3()
            data.append({"index": index, "offset": (ox, oy, oz)})

        morphs.append({
            "name": name,
            "type": morph_type,
            "data": data,
        })
    return morphs


def _parse_display_frames(r: _Reader) -> None:
    """Parse and skip PMD display frame data."""
    # Facial morph display list
    facial_count = r.read_uint8()
    for _ in range(facial_count):
        r.read_uint16()  # morph index

    # Bone display frame names
    frame_count = r.read_uint8()
    for _ in range(frame_count):
        r.read_str(50)  # frame name

    # Bone display list
    bone_display_count = r.read_uint32()
    for _ in range(bone_display_count):
        r.read_uint16()  # bone index
        r.read_uint8()   # frame index


def _parse_english(r: _Reader, bone_count: int, morph_count: int,
                   frame_count: int) -> tuple[str, str, list[str], list[str]]:
    """Parse optional English extension. Returns (name_e, comment_e, bone_names_e, morph_names_e)."""
    name_e = r.read_str(20)
    comment_e = r.read_str(256)

    bone_names_e = [r.read_str(20) for _ in range(bone_count)]
    # morph_count - 1 because base morph (type 0) has no English name
    morph_names_e = [r.read_str(20) for _ in range(morph_count - 1)] if morph_count > 0 else []
    # frame names
    for _ in range(frame_count):
        r.read_str(50)

    return name_e, comment_e, bone_names_e, morph_names_e


def _parse_toon_textures(r: _Reader) -> list[str]:
    """Parse 10 custom toon texture filenames."""
    return [r.read_str(100) for _ in range(10)]


def _parse_rigid_bodies(r: _Reader) -> list[dict]:
    """Parse PMD rigid bodies."""
    count = r.read_uint32()
    log.debug("Parsing %d PMD rigid bodies", count)
    bodies = []
    for _ in range(count):
        name = r.read_str(20)
        bone = r.read_uint16()
        collision_group_number = r.read_uint8()
        collision_group_mask = r.read_uint16()
        shape = r.read_uint8()
        sx, sy, sz = r.read_vec3()
        px, py, pz = r.read_vec3()
        rx, ry, rz = r.read_vec3()
        mass = r.read_float()
        linear_damping = r.read_float()
        angular_damping = r.read_float()
        bounce = r.read_float()
        friction = r.read_float()
        mode = r.read_uint8()

        if bone == 0xFFFF:
            bone = -1

        bodies.append({
            "name": name,
            "bone": bone,
            "collision_group_number": collision_group_number,
            "collision_group_mask": collision_group_mask,
            "shape": shape,
            "size": (sx, sy, sz),
            "position": (px, py, pz),
            "rotation": (rx, ry, rz),
            "mass": mass,
            "linear_damping": linear_damping,
            "angular_damping": angular_damping,
            "bounce": bounce,
            "friction": friction,
            "mode": mode,
        })
    return bodies


def _parse_joints(r: _Reader) -> list[dict]:
    """Parse PMD joints."""
    count = r.read_uint32()
    log.debug("Parsing %d PMD joints", count)
    joints = []
    for _ in range(count):
        name = r.read_str(20)
        src_rigid = r.read_uint32()
        dest_rigid = r.read_uint32()
        px, py, pz = r.read_vec3()
        rx, ry, rz = r.read_vec3()
        move_lo = r.read_vec3()
        move_hi = r.read_vec3()
        rot_lo = r.read_vec3()
        rot_hi = r.read_vec3()
        spring_move = r.read_vec3()
        spring_rot = r.read_vec3()

        joints.append({
            "name": name,
            "src_rigid": src_rigid,
            "dest_rigid": dest_rigid,
            "position": (px, py, pz),
            "rotation": (rx, ry, rz),
            "move_lo": move_lo,
            "move_hi": move_hi,
            "rot_lo": rot_lo,
            "rot_hi": rot_hi,
            "spring_move": spring_move,
            "spring_rot": spring_rot,
        })
    return joints


# ---------------------------------------------------------------------------
# PMD → PMX conversion
# ---------------------------------------------------------------------------

# PMD bone type → PMX flags mapping
# Flags: 0x0002=rotatable, 0x0004=movable, 0x0008=visible, 0x0010=controllable,
#         0x0020=is_ik, 0x0100=additional_rotation, 0x0400=fixed_axis
_ROTATABLE = 0x0002
_MOVABLE = 0x0004
_VISIBLE = 0x0008
_CONTROLLABLE = 0x0010
_IS_IK = 0x0020
_ADDITIONAL_ROTATION = 0x0100
_FIXED_AXIS = 0x0400
_TAIL_IS_BONE = 0x0001

_BONE_TYPE_FLAGS: dict[int, int] = {
    0: _ROTATABLE | _VISIBLE | _CONTROLLABLE,                    # Rotation only
    1: _ROTATABLE | _MOVABLE | _VISIBLE | _CONTROLLABLE,         # Rotation + Translation
    2: _ROTATABLE | _MOVABLE | _VISIBLE | _CONTROLLABLE | _IS_IK,  # IK
    3: _ROTATABLE | _VISIBLE | _CONTROLLABLE,                    # Unknown
    4: _ROTATABLE | _VISIBLE | _CONTROLLABLE,                    # IK effected
    5: _ROTATABLE | _VISIBLE | _CONTROLLABLE | _ADDITIONAL_ROTATION,  # Rotate influenced
    6: _ROTATABLE,                                                # IK connection (hidden)
    7: _ROTATABLE,                                                # Hidden
    8: _ROTATABLE | _VISIBLE | _CONTROLLABLE | _FIXED_AXIS,      # Twist
    9: _ROTATABLE | _ADDITIONAL_ROTATION,                         # Rotation movement (hidden)
}


def _convert_bones(pmd_bones: list[dict], pmd_iks: list[dict]) -> list[Bone]:
    """Convert PMD bones + IK data to PMX Bone list."""
    # Pre-identify knee bones for IK limit detection
    knee_bones: set[int] = set()
    for i, b in enumerate(pmd_bones):
        if b["name"].endswith("ひざ"):
            knee_bones.add(i)

    bones: list[Bone] = []
    for i, b in enumerate(pmd_bones):
        bone_type = b["type"]
        flags = _BONE_TYPE_FLAGS.get(bone_type, _ROTATABLE | _VISIBLE)
        transform_order = 0

        # Position in Blender coords
        pos = _pos(*b["position"])

        # Display connection
        tail = b["tail_bone"]
        if bone_type not in (8, 9) and tail > 0 and tail < len(pmd_bones):
            display_connection: int | tuple[float, float, float] = tail
            flags |= _TAIL_IS_BONE
        else:
            display_connection = (0.0, 0.0, 0.0)

        # Additional transform
        additional_transform = None
        if bone_type == 5:
            # Rotate influenced: inherit rotation from ik_bone
            additional_transform = (b["ik_bone"], 1.0)
            transform_order = 2
        elif bone_type == 9:
            # Rotation movement: inherit from tail_bone at ik_bone/100.0 factor
            additional_transform = (b["tail_bone"], b["ik_bone"] / 100.0)

        # Fixed axis for twist bones
        fixed_axis = None
        if bone_type == 8:
            tail_idx = b["tail_bone"]
            if 0 < tail_idx < len(pmd_bones):
                tp = pmd_bones[tail_idx]["position"]
                bp = b["position"]
                dx, dy, dz = tp[0] - bp[0], tp[1] - bp[1], tp[2] - bp[2]
                length = math.sqrt(dx * dx + dy * dy + dz * dz)
                if length > 1e-8:
                    fixed_axis = _pos(dx / length, dy / length, dz / length)
                else:
                    fixed_axis = (1.0, 0.0, 0.0)
            else:
                fixed_axis = (1.0, 0.0, 0.0)

        # IK type gets special transform_order
        if bone_type == 2:
            transform_order = 1

        bones.append(Bone(
            name=b["name"],
            name_e="",
            position=pos,
            parent=b["parent"],
            transform_order=transform_order,
            flags=flags,
            display_connection=display_connection,
            additional_transform=additional_transform,
            fixed_axis=fixed_axis,
            local_axis_x=None,
            local_axis_z=None,
            external_parent=None,
            ik_target=None,
            ik_loop_count=None,
            ik_limit_angle=None,
            ik_links=None,
        ))

    # Merge IK data into bones
    for ik in pmd_iks:
        bone_idx = ik["bone"]
        if bone_idx >= len(bones):
            continue

        bone = bones[bone_idx]
        # Set IK flag (may already be set for type 2)
        bone.flags |= _IS_IK

        bone.ik_target = ik["target"]
        bone.ik_loop_count = ik["iterations"]
        bone.ik_limit_angle = ik["control_weight"] * 4.0

        links = []
        for child_idx in ik["child_bones"]:
            if child_idx >= len(pmd_bones):
                continue

            has_limits = child_idx in knee_bones
            limit_min = None
            limit_max = None
            if has_limits:
                # Knee: only bend backward (negative X in MMD space)
                limit_min = _rot(math.radians(-180.0), 0.0, 0.0)
                limit_max = _rot(math.radians(-0.5), 0.0, 0.0)

            links.append(IKLink(
                bone_index=child_idx,
                has_limits=has_limits,
                limit_min=limit_min,
                limit_max=limit_max,
            ))

        bone.ik_links = links

    return bones


def _fix_waist_cancel(bones: list[Bone]) -> None:
    """Neutralize PMD-era WaistCancel bones that cancel LowerBody rotation.

    PMD models have WaistCancel bones with ``additional_transform = (LowerBody, -1.0)``
    which cancels LowerBody's rotation from the leg chain.  Modern PMX models (e.g. YYB)
    have WaistCancel that cancels a separate *Waist* bone (腰) instead — one that typically
    carries no VMD animation, making the cancellation a no-op.

    Modern VMDs are authored assuming legs inherit LowerBody's lean.  When a PMD model's
    WaistCancel actively cancels LowerBody, the legs lose that lean and IK targets become
    unreachable (0.47 unit error vs 0.00008 on a correctly structured PMX model).

    The fix: strip the ``additional_transform`` and the ``ADDITIONAL_ROTATION`` flag from
    WaistCancel bones that target LowerBody, converting them to passive passthrough bones.
    The leg parent chain is preserved (Leg → WaistCancel → LowerBody) so any VMD that
    targets WaistCancel directly still has a bone to land on.
    """
    lower_body_idx: int | None = None
    for i, b in enumerate(bones):
        if b.name == "下半身":
            lower_body_idx = i
            break

    if lower_body_idx is None:
        return

    fixed = 0
    for b in bones:
        if b.name not in ("腰キャンセル左", "腰キャンセル右"):
            continue
        if b.additional_transform is None:
            continue
        target_idx, _factor = b.additional_transform
        if target_idx != lower_body_idx:
            continue

        b.additional_transform = None
        b.flags &= ~_ADDITIONAL_ROTATION
        fixed += 1

    if fixed:
        log.info("PMD: Neutralized %d WaistCancel bone(s) that canceled LowerBody", fixed)


def _convert_morphs(pmd_morphs: list[dict]) -> list[Morph]:
    """Convert PMD morphs (base + relative) to PMX vertex morphs with absolute indices."""
    if not pmd_morphs:
        return []

    # Find base morph (type 0)
    base_morph = None
    for m in pmd_morphs:
        if m["type"] == 0:
            base_morph = m
            break

    if base_morph is None:
        log.warning("PMD: No base morph found, skipping morph conversion")
        return []

    # Build vertex index map from base morph
    vertex_map = [d["index"] for d in base_morph["data"]]

    # PMD morph type → PMX MorphCategory
    _TYPE_MAP = {
        1: MorphCategory.EYEBROW,
        2: MorphCategory.EYE,
        3: MorphCategory.MOUTH,
        4: MorphCategory.OTHER,
    }

    morphs: list[Morph] = []
    for m in pmd_morphs:
        if m["type"] == 0:
            continue  # Skip base morph

        offsets = []
        for d in m["data"]:
            # Remap relative index through base morph to absolute vertex index
            rel_idx = d["index"]
            if rel_idx < len(vertex_map):
                abs_idx = vertex_map[rel_idx]
                ox, oy, oz = d["offset"]
                offsets.append(VertexMorphOffset(
                    vertex_index=abs_idx,
                    offset=_pos(ox, oy, oz),
                ))

        category = _TYPE_MAP.get(m["type"], MorphCategory.OTHER)

        morphs.append(Morph(
            name=m["name"],
            name_e="",
            category=category,
            morph_type=MorphType.VERTEX,
            offsets=offsets,
        ))

    return morphs


def _convert_materials(
    pmd_materials: list[dict],
    toon_textures: list[str],
) -> tuple[list[Material], list[Texture]]:
    """Convert PMD materials to PMX Materials and Textures.

    Returns (materials, textures) where textures are deduplicated.
    """
    textures: list[Texture] = []
    tex_path_to_idx: dict[str, int] = {}

    def _get_or_add_tex(path: str) -> int:
        if not path:
            return -1
        if path in tex_path_to_idx:
            return tex_path_to_idx[path]
        idx = len(textures)
        textures.append(Texture(path=path))
        tex_path_to_idx[path] = idx
        return idx

    materials: list[Material] = []
    for i, m in enumerate(pmd_materials):
        # Parse texture path: "diffuse.bmp*sphere.sph"
        tex_path = m["texture_path"]
        diffuse_tex = ""
        sphere_tex = ""
        sphere_mode = 0

        if tex_path:
            parts = tex_path.split("*")
            for part in parts:
                part = part.strip()
                if not part:
                    continue
                low = part.lower()
                if low.endswith(".spa"):
                    sphere_tex = part
                    sphere_mode = 2  # add
                elif low.endswith(".sph"):
                    sphere_tex = part
                    sphere_mode = 1  # multiply
                else:
                    diffuse_tex = part

        texture_index = _get_or_add_tex(diffuse_tex)
        sphere_texture_index = _get_or_add_tex(sphere_tex)

        # Toon texture — PMD uses shared toon index (0-9 maps to toon01-10)
        toon_index = m["toon_index"]
        toon_texture_index = toon_index  # shared toon (0-indexed)
        toon_sharing = 1  # always shared in PMD

        # Flags
        flags = 0
        alpha = m["diffuse"][3]
        # Double sided if alpha < 1 (semi-transparent materials)
        if alpha < 1.0:
            flags |= 0x01  # double-sided
        # Drop shadow, self shadow map, self shadow — on by default
        flags |= 0x02  # drop shadow
        flags |= 0x04  # self shadow map
        flags |= 0x08  # self shadow
        # Edge flag
        if m["edge_flag"]:
            flags |= 0x10  # toon edge

        # Edge color/size from defaults
        edge_color = (0.0, 0.0, 0.0, 1.0)
        edge_size = 1.0

        materials.append(Material(
            name=f"Mat{i:02d}",
            name_e="",
            diffuse=m["diffuse"],
            specular=m["specular"],
            shininess=m["shininess"],
            ambient=m["ambient"],
            flags=flags,
            edge_color=edge_color,
            edge_size=edge_size,
            texture_index=texture_index,
            sphere_texture_index=sphere_texture_index,
            sphere_mode=sphere_mode,
            toon_sharing=toon_sharing,
            toon_texture_index=toon_texture_index,
            comment="",
            face_count=m["face_count"],
        ))

    return materials, textures


def _convert_rigid_bodies(
    pmd_bodies: list[dict],
    pmd_bones: list[dict],
) -> list[RigidBody]:
    """Convert PMD rigid bodies to PMX format.

    Key difference: PMD rigid body position is offset from bone; PMX is absolute.
    """
    bodies: list[RigidBody] = []
    for rb in pmd_bodies:
        bone_idx = rb["bone"]

        # Convert position from bone-relative to absolute
        px, py, pz = rb["position"]
        if 0 <= bone_idx < len(pmd_bones):
            bx, by, bz = pmd_bones[bone_idx]["position"]
            px += bx
            py += by
            pz += bz
        # else: bone -1 → position is already absolute (from world origin)

        bodies.append(RigidBody(
            name=rb["name"],
            name_e="",
            bone_index=bone_idx,
            collision_group_number=rb["collision_group_number"],
            collision_group_mask=rb["collision_group_mask"],
            shape=RigidShape(rb["shape"]),
            size=rb["size"],  # shape dimensions, no coord swap
            position=_pos(px, py, pz),
            rotation=_rot(*rb["rotation"]),
            mass=rb["mass"],
            linear_damping=rb["linear_damping"],
            angular_damping=rb["angular_damping"],
            bounce=rb["bounce"],
            friction=rb["friction"],
            mode=RigidMode(rb["mode"]),
        ))
    return bodies


def _convert_joints(pmd_joints: list[dict]) -> list[Joint]:
    """Convert PMD joints to PMX format."""
    joints: list[Joint] = []
    for j in pmd_joints:
        joints.append(Joint(
            name=j["name"],
            name_e="",
            mode=JointMode.SPRING_6DOF,
            src_rigid=j["src_rigid"],
            dest_rigid=j["dest_rigid"],
            position=_pos(*j["position"]),
            rotation=_rot(*j["rotation"]),
            limit_move_lower=_pos3(j["move_lo"]),
            limit_move_upper=_pos3(j["move_hi"]),
            limit_rotate_lower=_rot3(j["rot_lo"]),
            limit_rotate_upper=_rot3(j["rot_hi"]),
            spring_constant_move=_pos3(j["spring_move"]),
            spring_constant_rotate=_rot3(j["spring_rot"]),
        ))
    return joints


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse(filepath: str | Path) -> Model:
    """Parse a PMD file and return a Model with all data in Blender coordinates."""
    filepath = Path(filepath)
    log.info("Parsing PMD: %s", filepath.name)

    with open(filepath, "rb") as f:
        r = _Reader(f)

        # Header
        model_name, comment = _parse_header(r)

        # Core geometry
        vertices = _parse_vertices(r)
        faces = _parse_faces(r)
        pmd_materials = _parse_materials(r)
        pmd_bones = _parse_bones(r)
        pmd_iks = _parse_iks(r)
        pmd_morphs = _parse_morphs(r)

        # Display frames (parsed and discarded)
        facial_count = r.read_uint8()
        for _ in range(facial_count):
            r.read_uint16()
        frame_count = r.read_uint8()
        for _ in range(frame_count):
            r.read_str(50)
        bone_display_count = r.read_uint32()
        for _ in range(bone_display_count):
            r.read_uint16()
            r.read_uint8()

        # Optional extended sections
        name_e = ""
        comment_e = ""
        bone_names_e: list[str] = []
        morph_names_e: list[str] = []
        toon_textures: list[str] = []

        if r.remaining() > 0:
            english_flag = r.read_uint8()
            if english_flag:
                name_e, comment_e, bone_names_e, morph_names_e = _parse_english(
                    r, len(pmd_bones),
                    len(pmd_morphs),  # total count; _parse_english subtracts 1 for base
                    frame_count,
                )

        if r.remaining() >= 1000:  # 10 × 100 bytes
            toon_textures = _parse_toon_textures(r)

        # Physics (optional)
        pmd_bodies: list[dict] = []
        pmd_joints: list[dict] = []
        if r.remaining() > 4:
            try:
                pmd_bodies = _parse_rigid_bodies(r)
            except (EOFError, struct.error):
                log.debug("PMD: No rigid body section or truncated data")
        if r.remaining() > 4:
            try:
                pmd_joints = _parse_joints(r)
            except (EOFError, struct.error):
                log.debug("PMD: No joint section or truncated data")

    # --- Convert to PMX types ---

    # Apply English names to bones
    for i, name in enumerate(bone_names_e):
        if i < len(pmd_bones) and name:
            pmd_bones[i]["name_e"] = name

    # Apply English names to non-base morphs
    non_base_idx = 0
    for m in pmd_morphs:
        if m["type"] == 0:
            continue
        if non_base_idx < len(morph_names_e):
            m["name_e"] = morph_names_e[non_base_idx]
        non_base_idx += 1

    # Convert bones (includes IK merging)
    bones = _convert_bones(pmd_bones, pmd_iks)

    # Neutralize PMD-era WaistCancel that cancels LowerBody (breaks modern VMDs)
    _fix_waist_cancel(bones)

    # Set English names on bones
    for i, bone in enumerate(bones):
        if i < len(pmd_bones):
            bone.name_e = pmd_bones[i].get("name_e", "")

    # Convert morphs
    morphs = _convert_morphs(pmd_morphs)

    # Set English names on morphs
    non_base_idx = 0
    for morph in morphs:
        m_dict = None
        for m in pmd_morphs:
            if m["type"] == 0:
                continue
            if m["name"] == morph.name:
                m_dict = m
                break
        if m_dict:
            morph.name_e = m_dict.get("name_e", "")

    # Convert materials and textures
    pmx_materials, textures = _convert_materials(pmd_materials, toon_textures)

    # Convert rigid bodies (bone-relative → absolute position)
    rigid_bodies = _convert_rigid_bodies(pmd_bodies, pmd_bones)

    # Convert joints
    joints = _convert_joints(pmd_joints)

    # Build PMX Header (synthetic — PMD has no configurable index sizes)
    header = Header(
        version=1.0,
        encoding="cp932",
        additional_uv_count=0,
        vertex_index_size=2,
        texture_index_size=2,
        material_index_size=1,
        bone_index_size=2,
        morph_index_size=2,
        rigid_index_size=2,
    )

    model = Model(
        header=header,
        name=model_name,
        name_e=name_e,
        comment=comment,
        comment_e=comment_e,
    )
    model.vertices = vertices
    model.faces = faces
    model.textures = textures
    model.materials = pmx_materials
    model.bones = bones
    model.morphs = morphs
    model.display_frames = []
    model.rigid_bodies = rigid_bodies
    model.joints = joints

    log.info(
        "Parsed PMD: %d verts, %d faces, %d bones, %d materials, "
        "%d morphs, %d rigid bodies, %d joints",
        len(model.vertices), len(model.faces), len(model.bones),
        len(model.materials), len(model.morphs),
        len(model.rigid_bodies), len(model.joints),
    )

    return model
