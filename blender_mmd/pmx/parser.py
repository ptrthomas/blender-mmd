"""PMX binary parser — clean rewrite targeting PMX 2.0/2.1.

All coordinates are converted to Blender space (Z-up, right-handed) at parse time.
Downstream code never deals with MMD coordinates.

Coordinate conversion (MMD left-handed Y-up → Blender right-handed Z-up):
    Position/Offset: (x, y, z) → (x, z, y)   [swap Y↔Z]
    Normal:          (x, y, z) → (x, z, y)
    Rotation (euler):(x, y, z) → (x, z, y)    [TODO: negate for physics milestone]
"""

from __future__ import annotations

import logging
import struct
from pathlib import Path
from typing import BinaryIO

from .types import (
    Bone,
    BoneMorphOffset,
    BoneWeight,
    BoneWeightBDEF1,
    BoneWeightBDEF2,
    BoneWeightBDEF4,
    BoneWeightQDEF,
    BoneWeightSDEF,
    DisplayFrame,
    DisplayItem,
    GroupMorphOffset,
    Header,
    IKLink,
    Joint,
    JointMode,
    Material,
    MaterialMorphOffset,
    Model,
    Morph,
    MorphCategory,
    MorphType,
    RigidBody,
    RigidMode,
    RigidShape,
    Texture,
    UVMorphOffset,
    Vertex,
    VertexMorphOffset,
    WeightType,
)

log = logging.getLogger("blender_mmd")


# ---------------------------------------------------------------------------
# Binary reader helper
# ---------------------------------------------------------------------------

class _Reader:
    """Low-level binary reader with PMX-aware index reading."""

    __slots__ = ("_f", "_header")

    def __init__(self, f: BinaryIO) -> None:
        self._f = f
        self._header: Header | None = None

    def set_header(self, header: Header) -> None:
        self._header = header

    # -- primitive reads --

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

    # -- text --

    def read_text(self) -> str:
        length = self.read_int32()
        if length == 0:
            return ""
        data = self.read_bytes(length)
        assert self._header is not None
        return data.decode(self._header.encoding, errors="replace")

    # -- variable-size signed index --

    def _read_signed_index(self, size: int) -> int:
        if size == 1:
            return self.read_int8()
        elif size == 2:
            return self.read_int16()
        else:
            return self.read_int32()

    # -- variable-size unsigned index --

    def _read_unsigned_index(self, size: int) -> int:
        if size == 1:
            return self.read_uint8()
        elif size == 2:
            return self.read_uint16()
        else:
            return self.read_uint32()

    # -- typed index reads --

    def read_vertex_index(self) -> int:
        assert self._header is not None
        return self._read_unsigned_index(self._header.vertex_index_size)

    def read_bone_index(self) -> int:
        assert self._header is not None
        return self._read_signed_index(self._header.bone_index_size)

    def read_texture_index(self) -> int:
        assert self._header is not None
        return self._read_signed_index(self._header.texture_index_size)

    def read_material_index(self) -> int:
        assert self._header is not None
        return self._read_signed_index(self._header.material_index_size)

    def read_morph_index(self) -> int:
        assert self._header is not None
        return self._read_signed_index(self._header.morph_index_size)

    def read_rigid_index(self) -> int:
        assert self._header is not None
        return self._read_signed_index(self._header.rigid_index_size)


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

def _parse_header(r: _Reader) -> Header:
    magic = r.read_bytes(4)
    if magic != b"PMX ":
        raise ValueError(f"Not a PMX file: magic={magic!r}")

    version = r.read_float()
    if version not in (2.0, 2.1):
        raise ValueError(f"Unsupported PMX version: {version}")

    globals_count = r.read_uint8()
    globals_data = r.read_bytes(globals_count)

    encoding_idx = globals_data[0]
    encoding = "utf-8" if encoding_idx == 1 else "utf-16-le"

    header = Header(
        version=version,
        encoding=encoding,
        additional_uv_count=globals_data[1],
        vertex_index_size=globals_data[2],
        texture_index_size=globals_data[3],
        material_index_size=globals_data[4],
        bone_index_size=globals_data[5],
        morph_index_size=globals_data[6],
        rigid_index_size=globals_data[7],
    )
    return header


def _parse_vertex(r: _Reader, additional_uv_count: int) -> Vertex:
    px, py, pz = r.read_vec3()
    nx, ny, nz = r.read_vec3()
    uv = r.read_vec2()

    additional_uvs = []
    for _ in range(additional_uv_count):
        additional_uvs.append(r.read_vec4())

    weight_type_val = r.read_uint8()
    weight_type = WeightType(weight_type_val)

    weight: BoneWeight
    if weight_type == WeightType.BDEF1:
        weight = BoneWeightBDEF1(bone=r.read_bone_index())
    elif weight_type == WeightType.BDEF2:
        weight = BoneWeightBDEF2(
            bone1=r.read_bone_index(),
            bone2=r.read_bone_index(),
            weight=r.read_float(),
        )
    elif weight_type == WeightType.BDEF4:
        bones = (r.read_bone_index(), r.read_bone_index(),
                 r.read_bone_index(), r.read_bone_index())
        weights = r.read_vec4()
        weight = BoneWeightBDEF4(bones=bones, weights=weights)
    elif weight_type == WeightType.SDEF:
        b1 = r.read_bone_index()
        b2 = r.read_bone_index()
        w = r.read_float()
        c = r.read_vec3()
        r0 = r.read_vec3()
        r1 = r.read_vec3()
        weight = BoneWeightSDEF(
            bone1=b1, bone2=b2, weight=w,
            c=_pos3(c), r0=_pos3(r0), r1=_pos3(r1),
        )
    elif weight_type == WeightType.QDEF:
        bones = (r.read_bone_index(), r.read_bone_index(),
                 r.read_bone_index(), r.read_bone_index())
        weights = r.read_vec4()
        weight = BoneWeightQDEF(bones=bones, weights=weights)
    else:
        raise ValueError(f"Unknown weight type: {weight_type_val}")

    edge_scale = r.read_float()

    return Vertex(
        position=_pos(px, py, pz),
        normal=_pos(nx, ny, nz),
        uv=uv,
        additional_uvs=additional_uvs,
        weight_type=weight_type,
        weight=weight,
        edge_scale=edge_scale,
    )


def _parse_vertices(r: _Reader, header: Header) -> list[Vertex]:
    count = r.read_int32()
    log.debug("Parsing %d vertices", count)
    return [_parse_vertex(r, header.additional_uv_count) for _ in range(count)]


def _parse_faces(r: _Reader) -> list[tuple[int, int, int]]:
    index_count = r.read_int32()
    log.debug("Parsing %d face indices (%d triangles)", index_count, index_count // 3)
    faces = []
    for _ in range(index_count // 3):
        f1 = r.read_vertex_index()
        f2 = r.read_vertex_index()
        f3 = r.read_vertex_index()
        # Reverse winding order (MMD → Blender)
        faces.append((f3, f2, f1))
    return faces


def _parse_textures(r: _Reader) -> list[Texture]:
    count = r.read_int32()
    log.debug("Parsing %d textures", count)
    return [Texture(path=r.read_text()) for _ in range(count)]


def _parse_material(r: _Reader) -> Material:
    name = r.read_text()
    name_e = r.read_text()
    diffuse = r.read_vec4()
    specular = r.read_vec3()
    shininess = r.read_float()
    ambient = r.read_vec3()
    flags = r.read_uint8()
    edge_color = r.read_vec4()
    edge_size = r.read_float()
    texture_index = r.read_texture_index()
    sphere_texture_index = r.read_texture_index()
    sphere_mode = r.read_uint8()
    toon_sharing = r.read_uint8()
    if toon_sharing == 0:
        toon_texture_index = r.read_texture_index()
    else:
        toon_texture_index = r.read_uint8()
    comment = r.read_text()
    face_count = r.read_int32()

    return Material(
        name=name, name_e=name_e,
        diffuse=diffuse, specular=specular, shininess=shininess,
        ambient=ambient, flags=flags,
        edge_color=edge_color, edge_size=edge_size,
        texture_index=texture_index,
        sphere_texture_index=sphere_texture_index,
        sphere_mode=sphere_mode,
        toon_sharing=toon_sharing,
        toon_texture_index=toon_texture_index,
        comment=comment, face_count=face_count,
    )


def _parse_materials(r: _Reader) -> list[Material]:
    count = r.read_int32()
    log.debug("Parsing %d materials", count)
    return [_parse_material(r) for _ in range(count)]


def _parse_bone(r: _Reader) -> Bone:
    name = r.read_text()
    name_e = r.read_text()
    px, py, pz = r.read_vec3()
    parent = r.read_bone_index()
    transform_order = r.read_int32()
    flags = r.read_uint16()

    # Display connection
    if flags & 0x0001:
        display_connection: int | tuple[float, float, float] = r.read_bone_index()
    else:
        ox, oy, oz = r.read_vec3()
        display_connection = _pos(ox, oy, oz)

    # Additional transform (inherit rotation/location)
    additional_transform = None
    if flags & 0x0100 or flags & 0x0200:
        bone_idx = r.read_bone_index()
        factor = r.read_float()
        additional_transform = (bone_idx, factor)

    # Fixed axis
    fixed_axis = None
    if flags & 0x0400:
        ax, ay, az = r.read_vec3()
        fixed_axis = _pos(ax, ay, az)

    # Local axis
    local_axis_x = None
    local_axis_z = None
    if flags & 0x0800:
        lx_x, lx_y, lx_z = r.read_vec3()
        lz_x, lz_y, lz_z = r.read_vec3()
        local_axis_x = _pos(lx_x, lx_y, lx_z)
        local_axis_z = _pos(lz_x, lz_y, lz_z)

    # External parent
    external_parent = None
    if flags & 0x2000:
        external_parent = r.read_int32()

    # IK
    ik_target = None
    ik_loop_count = None
    ik_limit_angle = None
    ik_links = None
    if flags & 0x0020:
        ik_target = r.read_bone_index()
        ik_loop_count = r.read_int32()
        ik_limit_angle = r.read_float()
        link_count = r.read_int32()
        ik_links = []
        for _ in range(link_count):
            link_bone = r.read_bone_index()
            has_limits = r.read_uint8() != 0
            limit_min = None
            limit_max = None
            if has_limits:
                mn_x, mn_y, mn_z = r.read_vec3()
                mx_x, mx_y, mx_z = r.read_vec3()
                limit_min = _rot(mn_x, mn_y, mn_z)
                limit_max = _rot(mx_x, mx_y, mx_z)
            ik_links.append(IKLink(
                bone_index=link_bone,
                has_limits=has_limits,
                limit_min=limit_min,
                limit_max=limit_max,
            ))

    return Bone(
        name=name, name_e=name_e,
        position=_pos(px, py, pz),
        parent=parent,
        transform_order=transform_order,
        flags=flags,
        display_connection=display_connection,
        additional_transform=additional_transform,
        fixed_axis=fixed_axis,
        local_axis_x=local_axis_x,
        local_axis_z=local_axis_z,
        external_parent=external_parent,
        ik_target=ik_target,
        ik_loop_count=ik_loop_count,
        ik_limit_angle=ik_limit_angle,
        ik_links=ik_links,
    )


def _parse_bones(r: _Reader) -> list[Bone]:
    count = r.read_int32()
    log.debug("Parsing %d bones", count)
    return [_parse_bone(r) for _ in range(count)]


def _parse_morph(r: _Reader) -> Morph:
    name = r.read_text()
    name_e = r.read_text()
    category = MorphCategory(r.read_uint8())
    morph_type_val = r.read_uint8()
    morph_type = MorphType(morph_type_val)
    offset_count = r.read_int32()

    offsets = []
    for _ in range(offset_count):
        if morph_type == MorphType.GROUP:
            offsets.append(GroupMorphOffset(
                morph_index=r.read_morph_index(),
                factor=r.read_float(),
            ))
        elif morph_type == MorphType.VERTEX:
            vi = r.read_vertex_index()
            ox, oy, oz = r.read_vec3()
            offsets.append(VertexMorphOffset(
                vertex_index=vi, offset=_pos(ox, oy, oz),
            ))
        elif morph_type == MorphType.BONE:
            bi = r.read_bone_index()
            lx, ly, lz = r.read_vec3()
            qx, qy, qz, qw = r.read_vec4()
            # Fix zero quaternion → identity
            if qx == 0 and qy == 0 and qz == 0 and qw == 0:
                qx, qy, qz, qw = 0.0, 0.0, 0.0, 1.0
            offsets.append(BoneMorphOffset(
                bone_index=bi,
                location=_pos(lx, ly, lz),
                rotation=(qx, qz, -qy, qw),  # quaternion coord conversion
            ))
        elif morph_type in (
            MorphType.UV, MorphType.UV1, MorphType.UV2,
            MorphType.UV3, MorphType.UV4,
        ):
            offsets.append(UVMorphOffset(
                vertex_index=r.read_vertex_index(),
                offset=r.read_vec4(),
            ))
        elif morph_type == MorphType.MATERIAL:
            offsets.append(MaterialMorphOffset(
                material_index=r.read_material_index(),
                blend_mode=r.read_uint8(),
                diffuse=r.read_vec4(),
                specular=r.read_vec3(),
                shininess=r.read_float(),
                ambient=r.read_vec3(),
                edge_color=r.read_vec4(),
                edge_size=r.read_float(),
                texture_factor=r.read_vec4(),
                sphere_texture_factor=r.read_vec4(),
                toon_texture_factor=r.read_vec4(),
            ))

    return Morph(
        name=name, name_e=name_e,
        category=category, morph_type=morph_type,
        offsets=offsets,
    )


def _parse_morphs(r: _Reader) -> list[Morph]:
    count = r.read_int32()
    log.debug("Parsing %d morphs", count)
    return [_parse_morph(r) for _ in range(count)]


def _parse_display_frames(r: _Reader) -> list[DisplayFrame]:
    count = r.read_int32()
    log.debug("Parsing %d display frames", count)
    frames = []
    for _ in range(count):
        name = r.read_text()
        name_e = r.read_text()
        is_special = r.read_uint8() != 0
        item_count = r.read_int32()
        items = []
        for _ in range(item_count):
            display_type = r.read_uint8()
            if display_type == 0:
                idx = r.read_bone_index()
            else:
                idx = r.read_morph_index()
            items.append(DisplayItem(display_type=display_type, index=idx))
        frames.append(DisplayFrame(
            name=name, name_e=name_e,
            is_special=is_special, items=items,
        ))
    return frames


def _parse_rigid_body(r: _Reader) -> RigidBody:
    name = r.read_text()
    name_e = r.read_text()
    bone_index = r.read_bone_index()
    collision_group_number = r.read_uint8()
    collision_group_mask = r.read_uint16()
    shape = RigidShape(r.read_uint8())
    sx, sy, sz = r.read_vec3()
    px, py, pz = r.read_vec3()
    rx, ry, rz = r.read_vec3()
    mass = r.read_float()
    linear_damping = r.read_float()
    angular_damping = r.read_float()
    bounce = r.read_float()
    friction = r.read_float()
    mode = RigidMode(r.read_uint8())

    return RigidBody(
        name=name, name_e=name_e,
        bone_index=bone_index,
        collision_group_number=collision_group_number,
        collision_group_mask=collision_group_mask,
        shape=shape,
        size=(sx, sy, sz),  # size is shape dimensions, not position — no coord swap
        position=_pos(px, py, pz),
        rotation=_rot(rx, ry, rz),
        mass=mass,
        linear_damping=linear_damping,
        angular_damping=angular_damping,
        bounce=bounce,
        friction=friction,
        mode=mode,
    )


def _parse_rigid_bodies(r: _Reader) -> list[RigidBody]:
    count = r.read_int32()
    log.debug("Parsing %d rigid bodies", count)
    return [_parse_rigid_body(r) for _ in range(count)]


def _parse_joint(r: _Reader) -> Joint:
    name = r.read_text()
    name_e = r.read_text()
    mode = JointMode(r.read_uint8())
    src_rigid = r.read_rigid_index()
    dest_rigid = r.read_rigid_index()
    px, py, pz = r.read_vec3()
    rx, ry, rz = r.read_vec3()
    move_lo = r.read_vec3()
    move_hi = r.read_vec3()
    rot_lo = r.read_vec3()
    rot_hi = r.read_vec3()
    spring_move = r.read_vec3()
    spring_rot = r.read_vec3()

    return Joint(
        name=name, name_e=name_e,
        mode=mode,
        src_rigid=src_rigid,
        dest_rigid=dest_rigid,
        position=_pos(px, py, pz),
        rotation=_rot(rx, ry, rz),
        limit_move_lower=_pos3(move_lo),
        limit_move_upper=_pos3(move_hi),
        limit_rotate_lower=_rot3(rot_lo),
        limit_rotate_upper=_rot3(rot_hi),
        spring_constant_move=_pos3(spring_move),
        spring_constant_rotate=_rot3(spring_rot),
    )


def _parse_joints(r: _Reader) -> list[Joint]:
    count = r.read_int32()
    log.debug("Parsing %d joints", count)
    return [_parse_joint(r) for _ in range(count)]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse(filepath: str | Path) -> Model:
    """Parse a PMX file and return a Model with all data in Blender coordinates."""
    filepath = Path(filepath)
    log.info("Parsing PMX: %s", filepath.name)

    with open(filepath, "rb") as f:
        r = _Reader(f)

        header = _parse_header(r)
        r.set_header(header)

        name = r.read_text()
        name_e = r.read_text()
        comment = r.read_text()
        comment_e = r.read_text()

        model = Model(
            header=header,
            name=name,
            name_e=name_e,
            comment=comment,
            comment_e=comment_e,
        )

        model.vertices = _parse_vertices(r, header)
        model.faces = _parse_faces(r)
        model.textures = _parse_textures(r)
        model.materials = _parse_materials(r)
        model.bones = _parse_bones(r)
        model.morphs = _parse_morphs(r)
        model.display_frames = _parse_display_frames(r)
        model.rigid_bodies = _parse_rigid_bodies(r)
        model.joints = _parse_joints(r)

    log.info(
        "Parsed: %d verts, %d faces, %d bones, %d materials, "
        "%d morphs, %d rigid bodies, %d joints",
        len(model.vertices), len(model.faces), len(model.bones),
        len(model.materials), len(model.morphs),
        len(model.rigid_bodies), len(model.joints),
    )

    return model
