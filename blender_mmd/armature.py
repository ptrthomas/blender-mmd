"""Armature and bone creation from parsed PMX data."""

from __future__ import annotations

import logging
import math

import bpy
from mathutils import Vector

from .pmx.types import Bone, Model
from .translations import normalize_lr, translate

log = logging.getLogger("blender_mmd")

# Minimum bone length to prevent Blender from deleting zero-length bones
MIN_BONE_LENGTH = 0.001

# Japanese bone names that need auto-computed local axes for correct roll.
# These are arms, shoulders, elbows, wrists and their twist/parent variants.
_AUTO_AXIS_ARMS = frozenset({
    "左肩", "左腕", "左ひじ", "左手首",
    "右肩", "右腕", "右ひじ", "右手首",
})
_AUTO_AXIS_SEMI = frozenset({
    "左腕捩", "左手捩", "左肩P", "左ダミー",
    "右腕捩", "右手捩", "右肩P", "右ダミー",
})
_AUTO_AXIS_FINGERS = ("親指", "人指", "中指", "薬指", "小指")


def _resolve_bone_name(bone: Bone) -> str:
    """Choose the Blender bone name from PMX data.

    Priority: translation table → English name → Japanese name as-is.
    Translation table wins because PMX English names are often abbreviated
    or incorrect (e.g. "view cnt", "D", "arm twist_L").
    """
    translated = translate(bone.name)
    if translated:
        return translated
    if bone.name_e and bone.name_e.strip():
        return normalize_lr(bone.name_e.strip())
    return bone.name


def _ensure_unique_names(bones: list[Bone]) -> list[str]:
    """Generate unique Blender bone names for all PMX bones."""
    names: list[str] = []
    seen: dict[str, int] = {}
    for bone in bones:
        base = _resolve_bone_name(bone)
        if base in seen:
            seen[base] += 1
            name = f"{base}.{seen[base]:03d}"
        else:
            seen[base] = 0
            name = base
        names.append(name)
    return names


def _needs_auto_local_axis(name_j: str) -> bool:
    """Check if a bone (by Japanese name) should get auto-computed local axes."""
    if not name_j:
        return False
    if name_j in _AUTO_AXIS_ARMS or name_j in _AUTO_AXIS_SEMI:
        return True
    return any(f in name_j for f in _AUTO_AXIS_FINGERS)


def _set_bone_roll_from_axes(
    edit_bone: bpy.types.EditBone,
    local_x: tuple[float, float, float],
    local_z: tuple[float, float, float],
) -> None:
    """Set bone roll from local axis vectors (already in Blender coordinates).

    Matches mmd_tools' FnBone.update_bone_roll / get_axes logic:
    1. Orthogonalize the X/Z axes into a proper frame
    2. Find which axis is most aligned with the bone direction
    3. Use align_roll() with the perpendicular axis
    """
    x_axis = Vector(local_x).normalized()
    z_axis = Vector(local_z).normalized()
    y_axis = z_axis.cross(x_axis).normalized()
    z_axis = x_axis.cross(y_axis).normalized()

    axes = (x_axis, y_axis, z_axis)
    idx, val = max(
        [(i, edit_bone.vector.dot(v)) for i, v in enumerate(axes)],
        key=lambda x: abs(x[1]),
    )
    edit_bone.align_roll(axes[(idx - 1) % 3 if val < 0 else (idx + 1) % 3])


def _set_auto_bone_roll(edit_bone: bpy.types.EditBone) -> None:
    """Auto-compute bone roll for arm/finger bones from geometry.

    Matches mmd_tools' FnBone.update_auto_bone_roll: creates a synthetic
    triangle from the bone's head/tail in the XZ plane to derive a consistent
    local axis frame.
    """
    p1 = edit_bone.head.copy()
    p2 = edit_bone.tail.copy()
    p3 = p2.copy()

    xz = Vector((p2.x - p1.x, p2.z - p1.z))
    if xz.length < 1e-8:
        return
    xz.normalize()
    theta = math.atan2(xz.y, xz.x)
    norm = edit_bone.vector.length
    p3.z += norm * math.cos(theta)
    p3.x -= norm * math.sin(theta)

    bone_dir = (p2 - p1).normalized()
    z_tmp = (p3 - p1).normalized()
    face_normal = bone_dir.cross(z_tmp)

    # Effective result of mmd_tools' double .xzy cancellation:
    # align_roll with face_normal × bone_dir
    edit_bone.align_roll(face_normal.cross(bone_dir))


def create_armature(model: Model, scale: float) -> bpy.types.Object:
    """Create a Blender armature from parsed PMX bone data.

    Returns the armature object (already linked to the scene).
    """
    pmx_bones = model.bones
    bone_names = _ensure_unique_names(pmx_bones)

    # Create armature data and object
    arm_name = model.name_e if model.name_e else model.name
    arm_data = bpy.data.armatures.new(arm_name)
    arm_obj = bpy.data.objects.new(arm_name, arm_data)
    arm_obj["pmx_name"] = model.name
    arm_obj["import_scale"] = scale

    bpy.context.collection.objects.link(arm_obj)
    bpy.context.view_layer.objects.active = arm_obj

    # --- Edit mode: create bones ---
    bpy.ops.object.mode_set(mode="EDIT")

    edit_bones: list[bpy.types.EditBone] = []
    for i, pmx_bone in enumerate(pmx_bones):
        eb = arm_data.edit_bones.new(bone_names[i])
        pos = Vector(pmx_bone.position) * scale
        eb.head = pos
        # Temporary tail — will be set properly below
        eb.tail = pos + Vector((0, MIN_BONE_LENGTH, 0))
        eb.use_connect = False
        edit_bones.append(eb)

    # Set parent relationships
    for i, pmx_bone in enumerate(pmx_bones):
        if pmx_bone.parent >= 0 and pmx_bone.parent < len(edit_bones):
            edit_bones[i].parent = edit_bones[pmx_bone.parent]

    # Set bone tails from display_connection
    for i, pmx_bone in enumerate(pmx_bones):
        eb = edit_bones[i]
        if pmx_bone.is_tail_bone_index:
            # display_connection is a bone index
            target_idx = pmx_bone.display_connection
            if isinstance(target_idx, int) and 0 <= target_idx < len(edit_bones):
                target_pos = edit_bones[target_idx].head
                if (target_pos - eb.head).length > MIN_BONE_LENGTH:
                    eb.tail = target_pos
                else:
                    eb.tail = eb.head + Vector((0, MIN_BONE_LENGTH, 0))
            else:
                eb.tail = eb.head + Vector((0, MIN_BONE_LENGTH, 0))
        else:
            # display_connection is a position offset
            offset = pmx_bone.display_connection
            if isinstance(offset, tuple):
                offset_vec = Vector(offset) * scale
                if offset_vec.length > MIN_BONE_LENGTH:
                    eb.tail = eb.head + offset_vec
                else:
                    eb.tail = eb.head + Vector((0, MIN_BONE_LENGTH, 0))
            else:
                eb.tail = eb.head + Vector((0, MIN_BONE_LENGTH, 0))

    # Set bone roll from PMX local axis data or auto-compute for arm/finger bones
    for i, pmx_bone in enumerate(pmx_bones):
        eb = edit_bones[i]
        if eb.vector.length < MIN_BONE_LENGTH:
            continue
        if pmx_bone.has_local_axis and pmx_bone.local_axis_x and pmx_bone.local_axis_z:
            _set_bone_roll_from_axes(eb, pmx_bone.local_axis_x, pmx_bone.local_axis_z)
        elif _needs_auto_local_axis(pmx_bone.name):
            _set_auto_bone_roll(eb)

    bpy.ops.object.mode_set(mode="OBJECT")

    # --- Pose mode: set custom properties and IK constraints ---
    bpy.ops.object.mode_set(mode="POSE")

    for i, pmx_bone in enumerate(pmx_bones):
        pose_bone = arm_obj.pose.bones[bone_names[i]]
        bone = pose_bone.bone

        # Store metadata
        bone["bone_id"] = i
        bone["mmd_name_j"] = pmx_bone.name

        # IK constraints
        if pmx_bone.is_ik and pmx_bone.ik_links:
            _setup_ik(arm_obj, pose_bone, pmx_bone, bone_names)

    bpy.ops.object.mode_set(mode="OBJECT")

    log.info("Created armature '%s' with %d bones", arm_name, len(pmx_bones))
    return arm_obj


def _setup_ik(
    arm_obj: bpy.types.Object,
    pose_bone: bpy.types.PoseBone,
    pmx_bone: Bone,
    bone_names: list[str],
) -> None:
    """Set up IK constraint on a pose bone."""
    assert pmx_bone.ik_target is not None
    assert pmx_bone.ik_links is not None

    target_name = bone_names[pmx_bone.ik_target]

    # The IK constraint goes on the target bone (end effector),
    # pointing back at the IK bone as the target
    target_pb = arm_obj.pose.bones.get(target_name)
    if not target_pb:
        log.warning("IK target bone '%s' not found", target_name)
        return

    ik = target_pb.constraints.new("IK")
    ik.target = arm_obj
    ik.subtarget = pose_bone.name
    ik.chain_count = len(pmx_bone.ik_links)
    ik.iterations = pmx_bone.ik_loop_count or 40

    # Per-link rotation limits
    for link in pmx_bone.ik_links:
        if not link.has_limits or link.limit_min is None or link.limit_max is None:
            continue
        if link.bone_index < 0 or link.bone_index >= len(bone_names):
            continue

        link_name = bone_names[link.bone_index]
        link_pb = arm_obj.pose.bones.get(link_name)
        if not link_pb:
            continue

        limit = link_pb.constraints.new("LIMIT_ROTATION")
        limit.owner_space = "LOCAL"

        limit.use_limit_x = True
        limit.min_x = link.limit_min[0]
        limit.max_x = link.limit_max[0]

        limit.use_limit_y = True
        limit.min_y = link.limit_min[1]
        limit.max_y = link.limit_max[1]

        limit.use_limit_z = True
        limit.min_z = link.limit_min[2]
        limit.max_z = link.limit_max[2]

    log.debug(
        "IK: %s → %s (chain=%d, iter=%d, links=%d)",
        pose_bone.name, target_name,
        len(pmx_bone.ik_links),
        pmx_bone.ik_loop_count or 40,
        len(pmx_bone.ik_links),
    )
