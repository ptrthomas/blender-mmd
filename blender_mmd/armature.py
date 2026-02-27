"""Armature and bone creation from parsed PMX data."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import bpy
from mathutils import Matrix, Vector

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


@dataclass
class _ShadowBoneSpec:
    """Tracks a bone that needs additional transform shadow bones."""
    bone_name: str
    target_bone_name: str
    constraints: list = field(default_factory=list)  # TRANSFORM constraints to update subtarget


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


def _setup_additional_transforms(
    arm_obj: bpy.types.Object,
    pmx_bones: list[Bone],
    bone_names: list[str],
) -> list[_ShadowBoneSpec]:
    """Create TRANSFORM constraints for bones with additional transform (grant parent).

    Must be called in POSE mode. Returns a list of _ShadowBoneSpec for shadow bone
    creation in a subsequent EDIT mode pass.
    """
    specs: list[_ShadowBoneSpec] = []
    num_bones = len(pmx_bones)

    for i, pmx_bone in enumerate(pmx_bones):
        if pmx_bone.additional_transform is None:
            continue
        if not pmx_bone.has_additional_rotation and not pmx_bone.has_additional_location:
            continue

        target_idx, factor = pmx_bone.additional_transform
        if target_idx < 0 or target_idx >= num_bones:
            log.warning("Additional transform: bone %d target %d out of range", i, target_idx)
            continue
        if target_idx == i:
            log.warning("Additional transform: bone %d references itself", i)
            continue
        if factor == 0:
            continue

        bone_name = bone_names[i]
        target_name = bone_names[target_idx]
        pose_bone = arm_obj.pose.bones.get(bone_name)
        if not pose_bone:
            continue

        spec = _ShadowBoneSpec(bone_name, target_name)

        def _add_constraint(pb, name, map_type, value, influence):
            c = pb.constraints.new("TRANSFORM")
            c.name = name
            c.use_motion_extrapolate = True
            c.target = arm_obj
            # subtarget set later after shadow bone decision
            c.target_space = "LOCAL"
            c.owner_space = "LOCAL"
            c.map_from = map_type
            c.map_to = map_type
            c.map_to_x_from = "X"
            c.map_to_y_from = "Y"
            c.map_to_z_from = "Z"
            if influence < 0:
                c.from_rotation_mode = "ZYX"
            else:
                c.from_rotation_mode = "XYZ"
            c.to_euler_order = "XYZ"
            c.mix_mode_rot = "AFTER"
            # Set from min/max
            if map_type == "ROTATION":
                for attr in ("from_min_x_rot", "from_min_y_rot", "from_min_z_rot"):
                    setattr(c, attr, -value)
                for attr in ("from_max_x_rot", "from_max_y_rot", "from_max_z_rot"):
                    setattr(c, attr, value)
                for attr in ("to_min_x_rot", "to_min_y_rot", "to_min_z_rot"):
                    setattr(c, attr, -value * influence)
                for attr in ("to_max_x_rot", "to_max_y_rot", "to_max_z_rot"):
                    setattr(c, attr, value * influence)
            else:  # LOCATION
                for attr in ("from_min_x", "from_min_y", "from_min_z"):
                    setattr(c, attr, -value)
                for attr in ("from_max_x", "from_max_y", "from_max_z"):
                    setattr(c, attr, value)
                for attr in ("to_min_x", "to_min_y", "to_min_z"):
                    setattr(c, attr, -value * influence)
                for attr in ("to_max_x", "to_max_y", "to_max_z"):
                    setattr(c, attr, value * influence)
            spec.constraints.append(c)

        if pmx_bone.has_additional_rotation:
            _add_constraint(pose_bone, "mmd_additional_rotation", "ROTATION", math.pi, factor)
        if pmx_bone.has_additional_location:
            _add_constraint(pose_bone, "mmd_additional_location", "LOCATION", 100, factor)

        specs.append(spec)

    log.info("Additional transforms: %d bones with constraints", len(specs))
    return specs


def _create_shadow_edit_bones(
    arm_data: bpy.types.Armature,
    specs: list[_ShadowBoneSpec],
) -> set[str]:
    """Create shadow/dummy edit bones for non-aligned additional transform pairs.

    Must be called in EDIT mode. Returns set of bone names that got shadow bones.
    Well-aligned bones (dot > 0.99) skip shadow creation — their constraint subtargets
    will point directly at the target bone.
    """
    edit_bones = arm_data.edit_bones
    shadow_names: set[str] = set()

    # Ensure mmd_shadow bone collection exists
    shadow_coll = arm_data.collections.get("mmd_shadow")
    if not shadow_coll:
        shadow_coll = arm_data.collections.new("mmd_shadow")
        shadow_coll.is_visible = False

    for spec in specs:
        bone = edit_bones.get(spec.bone_name)
        target = edit_bones.get(spec.target_bone_name)
        if not bone or not target:
            continue

        # Well-aligned optimization: skip shadow bones
        if bone != target:
            x_dot = bone.x_axis.dot(target.x_axis)
            y_dot = bone.y_axis.dot(target.y_axis)
            if x_dot > 0.99 and y_dot > 0.99:
                continue

        # Create dummy bone: parented to target, same orientation as source bone
        dummy_name = "_dummy_" + spec.bone_name
        dummy = edit_bones.get(dummy_name) or edit_bones.new(name=dummy_name)
        dummy.parent = target
        dummy.head = target.head.copy()
        dummy.tail = dummy.head + (bone.tail - bone.head)
        dummy.roll = bone.roll
        dummy.use_deform = False
        shadow_coll.assign(dummy)

        # Create shadow bone: parented to target's parent, same shape as dummy
        shadow_name = "_shadow_" + spec.bone_name
        shadow = edit_bones.get(shadow_name) or edit_bones.new(name=shadow_name)
        shadow.parent = target.parent
        shadow.head = dummy.head.copy()
        shadow.tail = dummy.tail.copy()
        shadow.roll = bone.roll
        shadow.use_deform = False
        shadow_coll.assign(shadow)

        shadow_names.add(spec.bone_name)

    log.info("Shadow bones: %d pairs created", len(shadow_names))
    return shadow_names


def _finalize_shadow_constraints(
    arm_obj: bpy.types.Object,
    specs: list[_ShadowBoneSpec],
    shadow_names: set[str],
) -> None:
    """Set constraint subtargets after shadow bones are created.

    Must be called in POSE mode. For bones with shadow pairs, adds COPY_TRANSFORMS
    on the shadow bone and points TRANSFORM constraints at the shadow. For well-aligned
    bones, points directly at the target.
    """
    pose_bones = arm_obj.pose.bones

    for spec in specs:
        if spec.bone_name in shadow_names:
            shadow_name = "_shadow_" + spec.bone_name
            dummy_name = "_dummy_" + spec.bone_name

            shadow_pb = pose_bones.get(shadow_name)
            dummy_pb = pose_bones.get(dummy_name)
            if not shadow_pb or not dummy_pb:
                continue

            # COPY_TRANSFORMS on shadow bone targeting dummy bone
            c = shadow_pb.constraints.new("COPY_TRANSFORMS")
            c.name = "mmd_at_dummy"
            c.target = arm_obj
            c.subtarget = dummy_name
            c.target_space = "POSE"
            c.owner_space = "POSE"

            # Point TRANSFORM constraints at shadow bone
            for tc in spec.constraints:
                tc.subtarget = shadow_name
        else:
            # Well-aligned: point directly at target bone
            for tc in spec.constraints:
                tc.subtarget = spec.target_bone_name


def create_armature(
    model: Model, scale: float, ik_loop_factor: int = 1,
) -> bpy.types.Object:
    """Create a Blender armature from parsed PMX bone data.

    Args:
        ik_loop_factor: Multiplier for IK solver iterations. Blender's IK solver
            converges slower than MMD's CCDIK, so higher values improve precision.
            Default 1 uses raw PMX values. Increase to 5–10 if foot IK is imprecise.

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
    arm_obj["ik_loop_factor"] = ik_loop_factor

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
            _setup_ik(arm_obj, pose_bone, pmx_bone, bone_names, ik_loop_factor)

    # Additional transform (grant parent) constraints
    shadow_specs = _setup_additional_transforms(arm_obj, pmx_bones, bone_names)

    bpy.ops.object.mode_set(mode="OBJECT")

    # --- Edit mode pass 2: create shadow bones if needed ---
    if shadow_specs:
        bpy.ops.object.mode_set(mode="EDIT")
        shadow_names = _create_shadow_edit_bones(arm_data, shadow_specs)
        bpy.ops.object.mode_set(mode="OBJECT")

        # Finalize constraint subtargets (needs POSE mode)
        bpy.ops.object.mode_set(mode="POSE")
        _finalize_shadow_constraints(arm_obj, shadow_specs, shadow_names)
        bpy.ops.object.mode_set(mode="OBJECT")

    log.info("Created armature '%s' with %d bones", arm_name, len(pmx_bones))
    return arm_obj


def _axis_aligned_permutation(mat: Matrix) -> Matrix:
    """Snap a 3x3 matrix to an axis-aligned permutation matrix.

    For each row, find the column with the largest absolute value and assign
    +1 or -1. This produces a permutation matrix that best approximates the
    input — used to convert per-axis IK limits through a rotation.

    Matches mmd_tools' convertIKLimitAngles snapping logic.
    """
    m = Matrix([[0, 0, 0], [0, 0, 0], [0, 0, 0]])
    i_set = [0, 1, 2]
    j_set = [0, 1, 2]
    for _ in range(3):
        ii, jj = i_set[0], j_set[0]
        for i in i_set:
            for j in j_set:
                if abs(mat[i][j]) > abs(mat[ii][jj]):
                    ii, jj = i, j
        i_set.remove(ii)
        j_set.remove(jj)
        m[ii][jj] = -1.0 if mat[ii][jj] < 0 else 1.0
    return m


def _convert_ik_limits(
    limit_min: tuple[float, float, float],
    limit_max: tuple[float, float, float],
    bone_matrix_local: Matrix,
) -> tuple[Vector, Vector]:
    """Transform IK limits from Blender-global to Blender bone-local space.

    Our parser already applied Y↔Z swap to the limits. The remaining transform
    is: negate the bone's rest rotation matrix, swap Y↔Z rows, then transpose.
    This matches mmd_tools' convertIKLimitAngles.

    Then snap to an axis-aligned permutation (limits are per-axis, not arbitrary 3D).
    """
    mat = bone_matrix_local.to_3x3() * -1
    mat[1], mat[2] = mat[2].copy(), mat[1].copy()
    mat.transpose()

    perm = _axis_aligned_permutation(mat)

    new_min = perm @ Vector(limit_min)
    new_max = perm @ Vector(limit_max)
    # Ensure min <= max per axis
    for i in range(3):
        if new_min[i] > new_max[i]:
            new_min[i], new_max[i] = new_max[i], new_min[i]
    return new_min, new_max


def _setup_ik(
    arm_obj: bpy.types.Object,
    pose_bone: bpy.types.PoseBone,
    pmx_bone: Bone,
    bone_names: list[str],
    ik_loop_factor: int = 1,
) -> None:
    """Set up IK constraint on the correct chain bone.

    Blender's IK solver positions the constrained bone's TAIL at the target.
    So the IK constraint must go on the first link bone (e.g. knee), not the
    IK target bone (e.g. ankle). This way the knee's tail (= ankle's head)
    reaches the IK bone position.

    Matches mmd_tools' IK constraint placement logic.
    """
    assert pmx_bone.ik_target is not None
    assert pmx_bone.ik_links is not None

    target_name = bone_names[pmx_bone.ik_target]
    target_pb = arm_obj.pose.bones.get(target_name)
    if not target_pb:
        log.warning("IK target bone '%s' not found", target_name)
        return

    # Work with a copy of the links list since we may remove the first entry
    ik_links = list(pmx_bone.ik_links)

    # Determine which bone gets the IK constraint: first link bone
    if not ik_links:
        log.warning("IK bone '%s' has no links", pose_bone.name)
        return

    first_link_name = bone_names[ik_links[0].bone_index]
    ik_constraint_pb = arm_obj.pose.bones.get(first_link_name)
    if not ik_constraint_pb:
        log.warning("IK first link bone '%s' not found", first_link_name)
        return

    # Edge case: if first link bone IS the IK target, remove it and use next link
    # (mmd_tools importer.py lines 322-327)
    if ik_constraint_pb == target_pb:
        ik_links = ik_links[1:]
        if not ik_links:
            log.warning("IK bone '%s': all links removed after fix", pose_bone.name)
            return
        first_link_name = bone_names[ik_links[0].bone_index]
        ik_constraint_pb = arm_obj.pose.bones.get(first_link_name)
        if not ik_constraint_pb:
            log.warning("IK second link bone '%s' not found", first_link_name)
            return
        log.debug("IK fix: removed first link (== target) for '%s'", pose_bone.name)

    ik = ik_constraint_pb.constraints.new("IK")
    ik.target = arm_obj
    ik.subtarget = pose_bone.name
    ik.chain_count = len(ik_links)
    ik.iterations = (pmx_bone.ik_loop_count or 40) * ik_loop_factor

    # Per-link rotation limits using Blender-native IK limit properties
    for link in ik_links:
        if not link.has_limits or link.limit_min is None or link.limit_max is None:
            continue
        if link.bone_index < 0 or link.bone_index >= len(bone_names):
            continue

        link_name = bone_names[link.bone_index]
        link_pb = arm_obj.pose.bones.get(link_name)
        if not link_pb:
            continue

        # Convert limits from Blender-global to bone-local space
        new_min, new_max = _convert_ik_limits(
            link.limit_min, link.limit_max,
            link_pb.bone.matrix_local,
        )

        link_pb.use_ik_limit_x = True
        link_pb.use_ik_limit_y = True
        link_pb.use_ik_limit_z = True
        link_pb.ik_min_x = new_min[0]
        link_pb.ik_max_x = new_max[0]
        link_pb.ik_min_y = new_min[1]
        link_pb.ik_max_y = new_max[1]
        link_pb.ik_min_z = new_min[2]
        link_pb.ik_max_z = new_max[2]

        # Blender clamps ik_min to [-π,0] and ik_max to [0,π], so small
        # positive minimums (e.g. knee min_x=0.0087) get silently lost.
        # Add a LIMIT_ROTATION constraint to enforce the actual limits
        # where they differ from what native IK can represent (mmd_tools
        # does the same with "mmd_ik_limit_override").
        needs_override = (
            link_pb.ik_min_x != new_min[0]
            or link_pb.ik_max_x != new_max[0]
            or link_pb.ik_min_y != new_min[1]
            or link_pb.ik_max_y != new_max[1]
            or link_pb.ik_min_z != new_min[2]
            or link_pb.ik_max_z != new_max[2]
        )
        if needs_override:
            c = link_pb.constraints.new("LIMIT_ROTATION")
            c.name = "mmd_ik_limit_override"
            c.owner_space = "LOCAL"
            c.min_x = new_min[0]
            c.max_x = new_max[0]
            c.min_y = new_min[1]
            c.max_y = new_max[1]
            c.min_z = new_min[2]
            c.max_z = new_max[2]
            c.use_limit_x = (link_pb.ik_min_x != new_min[0]
                             or link_pb.ik_max_x != new_max[0])
            c.use_limit_y = (link_pb.ik_min_y != new_min[1]
                             or link_pb.ik_max_y != new_max[1])
            c.use_limit_z = (link_pb.ik_min_z != new_min[2]
                             or link_pb.ik_max_z != new_max[2])

    log.debug(
        "IK: %s → %s (constraint on %s, chain=%d, iter=%d)",
        pose_bone.name, target_name, ik_constraint_pb.name,
        len(ik_links),
        pmx_bone.ik_loop_count or 40,
    )
