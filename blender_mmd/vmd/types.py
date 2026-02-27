"""VMD data model — dataclasses for VMD motion structures.

Coordinate values are stored as-is from the VMD file (MMD coordinate system).
Conversion to Blender coordinates happens at import time.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BoneKeyframe:
    """A single bone keyframe from a VMD file."""

    bone_name: str  # Japanese name (CP932 decoded)
    frame: int
    location: tuple[float, float, float]  # MMD coords
    rotation: tuple[float, float, float, float]  # quaternion (x, y, z, w)
    interpolation: bytes  # 64 bytes of Bézier interpolation data


@dataclass
class MorphKeyframe:
    """A single morph/shape key keyframe from a VMD file."""

    morph_name: str  # Japanese name (CP932 decoded)
    frame: int
    weight: float


@dataclass
class CameraKeyframe:
    """A single camera keyframe from a VMD file."""

    frame: int
    distance: float
    location: tuple[float, float, float]
    rotation: tuple[float, float, float]  # Euler angles (radians)
    interpolation: bytes  # 24 bytes
    fov: int  # degrees
    orthographic: bool


@dataclass
class VmdMotion:
    """Parsed VMD motion data."""

    model_name: str
    bone_keyframes: list[BoneKeyframe] = field(default_factory=list)
    morph_keyframes: list[MorphKeyframe] = field(default_factory=list)
    camera_keyframes: list[CameraKeyframe] = field(default_factory=list)
