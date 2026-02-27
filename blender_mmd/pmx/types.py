"""PMX data model — dataclasses for every PMX structure.

All coordinate values are in Blender space (Z-up, right-handed) after parsing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Union


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class WeightType(IntEnum):
    BDEF1 = 0
    BDEF2 = 1
    BDEF4 = 2
    SDEF = 3
    QDEF = 4


class MorphCategory(IntEnum):
    SYSTEM = 0
    EYEBROW = 1
    EYE = 2
    MOUTH = 3
    OTHER = 4


class MorphType(IntEnum):
    GROUP = 0
    VERTEX = 1
    BONE = 2
    UV = 3
    UV1 = 4
    UV2 = 5
    UV3 = 6
    UV4 = 7
    MATERIAL = 8


class RigidShape(IntEnum):
    SPHERE = 0
    BOX = 1
    CAPSULE = 2


class RigidMode(IntEnum):
    STATIC = 0
    DYNAMIC = 1
    DYNAMIC_BONE = 2


class JointMode(IntEnum):
    SPRING_6DOF = 0


# ---------------------------------------------------------------------------
# Bone weight variants
# ---------------------------------------------------------------------------

@dataclass
class BoneWeightBDEF1:
    bone: int

@dataclass()
class BoneWeightBDEF2:
    bone1: int
    bone2: int
    weight: float

@dataclass()
class BoneWeightBDEF4:
    bones: tuple[int, int, int, int]
    weights: tuple[float, float, float, float]

@dataclass()
class BoneWeightSDEF:
    bone1: int
    bone2: int
    weight: float
    c: tuple[float, float, float]
    r0: tuple[float, float, float]
    r1: tuple[float, float, float]

@dataclass()
class BoneWeightQDEF:
    bones: tuple[int, int, int, int]
    weights: tuple[float, float, float, float]


BoneWeight = Union[
    BoneWeightBDEF1,
    BoneWeightBDEF2,
    BoneWeightBDEF4,
    BoneWeightSDEF,
    BoneWeightQDEF,
]


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

@dataclass()
class Header:
    version: float
    encoding: str  # "utf-16-le" or "utf-8"
    additional_uv_count: int
    vertex_index_size: int
    texture_index_size: int
    material_index_size: int
    bone_index_size: int
    morph_index_size: int
    rigid_index_size: int


# ---------------------------------------------------------------------------
# Vertex
# ---------------------------------------------------------------------------

@dataclass()
class Vertex:
    position: tuple[float, float, float]
    normal: tuple[float, float, float]
    uv: tuple[float, float]
    additional_uvs: list[tuple[float, float, float, float]]
    weight_type: WeightType
    weight: BoneWeight
    edge_scale: float


# ---------------------------------------------------------------------------
# Material
# ---------------------------------------------------------------------------

@dataclass()
class Material:
    name: str
    name_e: str
    diffuse: tuple[float, float, float, float]
    specular: tuple[float, float, float]
    shininess: float
    ambient: tuple[float, float, float]
    flags: int
    edge_color: tuple[float, float, float, float]
    edge_size: float
    texture_index: int
    sphere_texture_index: int
    sphere_mode: int  # 0=off, 1=mul, 2=add, 3=subtex
    toon_sharing: int  # 0=individual, 1=shared
    toon_texture_index: int
    comment: str
    face_count: int  # number of vertex indices (not triangles)

    @property
    def is_double_sided(self) -> bool:
        return bool(self.flags & 0x01)


# ---------------------------------------------------------------------------
# Bone
# ---------------------------------------------------------------------------

@dataclass()
class IKLink:
    bone_index: int
    has_limits: bool
    limit_min: tuple[float, float, float] | None  # radians, Blender coords
    limit_max: tuple[float, float, float] | None


@dataclass()
class Bone:
    name: str
    name_e: str
    position: tuple[float, float, float]
    parent: int  # -1 = no parent
    transform_order: int
    flags: int

    # Display connection — bone index or offset, determined by flag bit 0
    display_connection: int | tuple[float, float, float]

    # Optional fields based on flags
    additional_transform: tuple[int, float] | None  # (bone_index, factor)
    fixed_axis: tuple[float, float, float] | None
    local_axis_x: tuple[float, float, float] | None
    local_axis_z: tuple[float, float, float] | None
    external_parent: int | None

    # IK data (if flag bit 5 is set)
    ik_target: int | None
    ik_loop_count: int | None
    ik_limit_angle: float | None
    ik_links: list[IKLink] | None

    @property
    def is_tail_bone_index(self) -> bool:
        return bool(self.flags & 0x0001)

    @property
    def is_rotatable(self) -> bool:
        return bool(self.flags & 0x0002)

    @property
    def is_movable(self) -> bool:
        return bool(self.flags & 0x0004)

    @property
    def is_visible(self) -> bool:
        return bool(self.flags & 0x0008)

    @property
    def is_controllable(self) -> bool:
        return bool(self.flags & 0x0010)

    @property
    def is_ik(self) -> bool:
        return bool(self.flags & 0x0020)

    @property
    def has_additional_rotation(self) -> bool:
        return bool(self.flags & 0x0100)

    @property
    def has_additional_location(self) -> bool:
        return bool(self.flags & 0x0200)

    @property
    def has_fixed_axis(self) -> bool:
        return bool(self.flags & 0x0400)

    @property
    def has_local_axis(self) -> bool:
        return bool(self.flags & 0x0800)

    @property
    def transform_after_physics(self) -> bool:
        return bool(self.flags & 0x1000)

    @property
    def has_external_parent(self) -> bool:
        return bool(self.flags & 0x2000)


# ---------------------------------------------------------------------------
# Morph offsets
# ---------------------------------------------------------------------------

@dataclass()
class GroupMorphOffset:
    morph_index: int
    factor: float

@dataclass()
class VertexMorphOffset:
    vertex_index: int
    offset: tuple[float, float, float]

@dataclass()
class BoneMorphOffset:
    bone_index: int
    location: tuple[float, float, float]
    rotation: tuple[float, float, float, float]  # quaternion (x, y, z, w)

@dataclass()
class UVMorphOffset:
    vertex_index: int
    offset: tuple[float, float, float, float]

@dataclass()
class MaterialMorphOffset:
    material_index: int
    blend_mode: int  # 0=mul, 1=add
    diffuse: tuple[float, float, float, float]
    specular: tuple[float, float, float]
    shininess: float
    ambient: tuple[float, float, float]
    edge_color: tuple[float, float, float, float]
    edge_size: float
    texture_factor: tuple[float, float, float, float]
    sphere_texture_factor: tuple[float, float, float, float]
    toon_texture_factor: tuple[float, float, float, float]


MorphOffset = Union[
    GroupMorphOffset,
    VertexMorphOffset,
    BoneMorphOffset,
    UVMorphOffset,
    MaterialMorphOffset,
]


# ---------------------------------------------------------------------------
# Morph
# ---------------------------------------------------------------------------

@dataclass()
class Morph:
    name: str
    name_e: str
    category: MorphCategory
    morph_type: MorphType
    offsets: list[MorphOffset]


# ---------------------------------------------------------------------------
# Display frame
# ---------------------------------------------------------------------------

@dataclass()
class DisplayItem:
    display_type: int  # 0=bone, 1=morph
    index: int

@dataclass()
class DisplayFrame:
    name: str
    name_e: str
    is_special: bool
    items: list[DisplayItem]


# ---------------------------------------------------------------------------
# Rigid body
# ---------------------------------------------------------------------------

@dataclass()
class RigidBody:
    name: str
    name_e: str
    bone_index: int  # -1 = no bone
    collision_group_number: int
    collision_group_mask: int
    shape: RigidShape
    size: tuple[float, float, float]
    position: tuple[float, float, float]
    rotation: tuple[float, float, float]
    mass: float
    linear_damping: float
    angular_damping: float
    bounce: float
    friction: float
    mode: RigidMode


# ---------------------------------------------------------------------------
# Joint
# ---------------------------------------------------------------------------

@dataclass()
class Joint:
    name: str
    name_e: str
    mode: JointMode
    src_rigid: int  # -1 = none
    dest_rigid: int  # -1 = none
    position: tuple[float, float, float]
    rotation: tuple[float, float, float]
    limit_move_lower: tuple[float, float, float]
    limit_move_upper: tuple[float, float, float]
    limit_rotate_lower: tuple[float, float, float]
    limit_rotate_upper: tuple[float, float, float]
    spring_constant_move: tuple[float, float, float]
    spring_constant_rotate: tuple[float, float, float]


# ---------------------------------------------------------------------------
# Texture
# ---------------------------------------------------------------------------

@dataclass()
class Texture:
    path: str


# ---------------------------------------------------------------------------
# Top-level model
# ---------------------------------------------------------------------------

@dataclass()
class Model:
    header: Header
    name: str
    name_e: str
    comment: str
    comment_e: str
    vertices: list[Vertex] = field(default_factory=list)
    faces: list[tuple[int, int, int]] = field(default_factory=list)
    textures: list[Texture] = field(default_factory=list)
    materials: list[Material] = field(default_factory=list)
    bones: list[Bone] = field(default_factory=list)
    morphs: list[Morph] = field(default_factory=list)
    display_frames: list[DisplayFrame] = field(default_factory=list)
    rigid_bodies: list[RigidBody] = field(default_factory=list)
    joints: list[Joint] = field(default_factory=list)
