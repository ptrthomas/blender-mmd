"""Physics — rigid bodies, joints, bone coupling, world setup.

Blender-specific imports (bpy, mathutils) are deferred to function bodies
so that pure-Python utility functions remain importable for unit tests.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from .pmx.types import RigidBody, RigidMode, RigidShape

if TYPE_CHECKING:
    import bpy
    from .pmx.types import Model

log = logging.getLogger("blender_mmd")

# Shape type mapping: PMX → Blender
_SHAPE_MAP = {
    RigidShape.SPHERE: "SPHERE",
    RigidShape.BOX: "BOX",
    RigidShape.CAPSULE: "CAPSULE",
}


def build_physics(armature_obj, model, scale: float, mode: str = "none") -> None:
    """Build physics for an MMD model.

    Args:
        mode: "none" (metadata only), "rigid_body" (Blender RB), or "cloth" (detect chains).
    """
    log.info(
        "Building physics (mode=%s): %d rigid bodies, %d joints",
        mode, len(model.rigid_bodies), len(model.joints),
    )

    # Clean up any existing physics first
    clear_physics(armature_obj)

    # Always store metadata — available for all modes
    armature_obj["mmd_physics_data"] = serialize_physics_data(model)
    armature_obj["physics_mode"] = mode

    if mode == "none":
        log.info("Physics mode 'none': metadata stored, no Blender objects created")
        return

    if mode == "cloth":
        from .chains import detect_chains
        chains = detect_chains(model)
        armature_obj["mmd_physics_chains"] = json.dumps(
            [_chain_to_dict(c) for c in chains]
        )
        log.info("Physics mode 'cloth': %d chains detected, metadata stored", len(chains))
        return

    if mode == "rigid_body":
        _build_rigid_body_physics(armature_obj, model, scale)
        return

    raise ValueError(f"Unknown physics mode: {mode!r}")


def _chain_to_dict(chain) -> dict:
    """Serialize a Chain dataclass to a JSON-compatible dict."""
    return {
        "name": chain.name,
        "group": chain.group,
        "root_rigid_index": chain.root_rigid_index,
        "root_bone_index": chain.root_bone_index,
        "rigid_indices": chain.rigid_indices,
        "bone_indices": chain.bone_indices,
        "joint_indices": chain.joint_indices,
    }


def _build_rigid_body_physics(armature_obj, model, scale: float) -> None:
    """Create rigid bodies, joints, bone coupling, and configure physics world.

    The rigid body world is disabled during setup (following mmd_tools' approach)
    to prevent the solver from computing forces while bodies are being added.
    Scene updates (frame_set) flush the depsgraph at key points.
    """
    import bpy

    # Disable rigid body world during setup to prevent solver interference.
    rbw_was_enabled = _set_rigid_body_world_enabled(bpy.context.scene, False)

    try:
        # Create physics collection with subcollections
        col_name = f"{armature_obj.name}_Physics"
        collection = bpy.data.collections.new(col_name)
        bpy.context.scene.collection.children.link(collection)
        armature_obj["physics_collection"] = col_name

        rb_col = bpy.data.collections.new("Rigid Bodies")
        collection.children.link(rb_col)
        joint_col = bpy.data.collections.new("Joints")
        collection.children.link(joint_col)
        track_col = bpy.data.collections.new("Tracking")
        collection.children.link(track_col)

        # Build bone name lookup: bone_index → Blender bone name
        bone_names = _build_bone_name_map(armature_obj)

        # Step 1: Rigid bodies (with collision margin fix)
        rigid_objects = _create_rigid_bodies(model, armature_obj, scale, rb_col)

        # Step 2: Reposition dynamic bodies to match current bone pose
        _reposition_dynamic_bodies(model, armature_obj, rigid_objects, bone_names, scale)

        # Flush depsgraph so matrix_world is up to date for joint/coupling steps
        bpy.context.scene.frame_set(bpy.context.scene.frame_current)

        # Step 3: Joints (with repositioning for dynamic body bones)
        joint_objects = _create_joints(model, armature_obj, rigid_objects, bone_names, scale, joint_col)

        # Step 4: Non-collision constraints (mask-based)
        _create_non_collision_constraints(model, rigid_objects, joint_objects, joint_col)

        # Flush depsgraph before bone coupling (tracking empties need correct matrix_world)
        bpy.context.scene.frame_set(bpy.context.scene.frame_current)

        # Step 5: Bone↔rigid body coupling
        _setup_bone_coupling(armature_obj, model, rigid_objects, bone_names, scale, track_col)

        # Flush depsgraph after all parenting/constraints are set
        bpy.context.scene.frame_set(bpy.context.scene.frame_current)

        # Step 6: Physics world settings
        _setup_physics_world(bpy.context.scene, scale)

    finally:
        # Always enable rigid body world after build — we just created physics,
        # so it should be active.
        _set_rigid_body_world_enabled(bpy.context.scene, True)

    log.info("Physics build complete: %d rigid bodies, %d joints",
             len(rigid_objects), len(model.joints))


def clear_physics(armature_obj) -> None:
    """Remove all physics objects and metadata for this armature."""
    import bpy

    col_name = armature_obj.get("physics_collection")
    if col_name:
        collection = bpy.data.collections.get(col_name)
        if collection:
            # Remove COPY_TRANSFORMS / COPY_ROTATION constraints from pose bones
            if armature_obj.pose:
                for pb in armature_obj.pose.bones:
                    to_remove = [
                        c for c in pb.constraints
                        if c.type in ("COPY_TRANSFORMS", "COPY_ROTATION")
                        and c.name.startswith("mmd_")
                    ]
                    for c in to_remove:
                        pb.constraints.remove(c)

            # Delete all objects in collection and subcollections
            def _remove_collection_recursive(col):
                for child in list(col.children):
                    _remove_collection_recursive(child)
                for obj in list(col.objects):
                    bpy.data.objects.remove(obj, do_unlink=True)
                bpy.data.collections.remove(col)

            _remove_collection_recursive(collection)

        if "physics_collection" in armature_obj:
            del armature_obj["physics_collection"]

    # Clean metadata keys
    for key in ("mmd_physics_data", "physics_mode", "mmd_physics_chains"):
        if key in armature_obj:
            del armature_obj[key]


def serialize_physics_data(model) -> str:
    """Serialize rigid body + joint data from a PMX model to JSON string.

    Pure Python — no Blender imports needed.
    """
    rigid_bodies = []
    for rb in model.rigid_bodies:
        rigid_bodies.append({
            "name": rb.name,
            "name_e": rb.name_e,
            "bone_index": rb.bone_index,
            "mode": rb.mode.value,
            "collision_group_number": rb.collision_group_number,
            "collision_group_mask": rb.collision_group_mask,
            "shape": rb.shape.value,
            "size": list(rb.size),
            "position": list(rb.position),
            "rotation": list(rb.rotation),
            "mass": rb.mass,
            "linear_damping": rb.linear_damping,
            "angular_damping": rb.angular_damping,
            "bounce": rb.bounce,
            "friction": rb.friction,
        })

    joints = []
    for j in model.joints:
        joints.append({
            "name": j.name,
            "name_e": j.name_e,
            "src_rigid": j.src_rigid,
            "dest_rigid": j.dest_rigid,
            "position": list(j.position),
            "rotation": list(j.rotation),
            "limit_move_lower": list(j.limit_move_lower),
            "limit_move_upper": list(j.limit_move_upper),
            "limit_rotate_lower": list(j.limit_rotate_lower),
            "limit_rotate_upper": list(j.limit_rotate_upper),
            "spring_constant_move": list(j.spring_constant_move),
            "spring_constant_rotate": list(j.spring_constant_rotate),
        })

    return json.dumps({"rigid_bodies": rigid_bodies, "joints": joints})


def deserialize_physics_data(json_str: str) -> dict:
    """Deserialize physics JSON back to dict. Pure Python."""
    return json.loads(json_str)


def _build_bone_name_map(armature_obj) -> dict[int, str]:
    """Map PMX bone index → Blender bone name using bone_id custom prop."""
    result = {}
    for bone in armature_obj.data.bones:
        idx = bone.get("bone_id")
        if idx is not None:
            result[idx] = bone.name
    return result


def _create_rigid_bodies(model, armature_obj, scale: float, collection) -> list:
    """Create rigid body objects with collision_collections and collision margin."""
    import bpy
    from mathutils import Euler, Vector

    rigid_objects = []

    for i, rigid in enumerate(model.rigid_bodies):
        name = f"RB_{i:03d}_{rigid.name}"

        # Create mesh with actual geometry matching the collision shape
        mesh = bpy.data.meshes.new(name)
        obj = bpy.data.objects.new(name, mesh)
        collection.objects.link(obj)

        _build_shape_mesh(obj, rigid, scale)

        # Position and rotation (Blender coords from parser)
        # Rotation needs negation: handedness flip reverses rotation direction.
        # Parser does Y↔Z swap; mmd_tools also negates: .xzy * -1
        obj.location = Vector(rigid.position) * scale
        obj.rotation_mode = "YXZ"
        rx, ry, rz = rigid.rotation
        obj.rotation_euler = Euler((-rx, -ry, -rz), "YXZ")

        # Visual settings
        obj.display_type = "WIRE"
        obj.hide_render = True

        # Make active and add to rigid body world
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.rigidbody.object_add()
        obj.select_set(False)

        rb = obj.rigid_body
        rb.collision_shape = _SHAPE_MAP[rigid.shape]
        rb.mass = rigid.mass
        rb.friction = rigid.friction
        rb.restitution = rigid.bounce
        rb.linear_damping = rigid.linear_damping
        rb.angular_damping = rigid.angular_damping
        rb.kinematic = (rigid.mode == RigidMode.STATIC)

        # Collision collections (shared layer 0 + own group)
        rb.collision_collections = _build_collision_collections(rigid)

        # Collision margin: at 0.08 scale, Blender's default 0.04 is huge.
        # mmd_tools uses 1e-6 to prevent capsules pushing each other apart.
        rb.use_margin = True
        rb.collision_margin = 1e-6

        # Store PMX index for joint lookups
        obj["mmd_rigid_index"] = i

        rigid_objects.append(obj)

    return rigid_objects


def _build_shape_mesh(obj, rigid: RigidBody, scale: float) -> None:
    """Build actual mesh geometry for the collision shape.

    Blender derives rigid body collision bounds from the object's bounding box,
    so we need real geometry — an empty mesh gives zero-size collision shapes.
    """
    import bmesh
    from mathutils import Matrix

    sx, sy, sz = rigid.size
    shape = rigid.shape
    bm = bmesh.new()

    if shape == RigidShape.SPHERE:
        radius = max(sx * scale, 1e-4)
        bmesh.ops.create_uvsphere(bm, u_segments=8, v_segments=5, radius=radius)
    elif shape == RigidShape.BOX:
        # size is (width, height, depth) in MMD; Y↔Z swap for Blender
        x = max(sx * scale, 1e-4)
        y = max(sz * scale, 1e-4)  # MMD depth → Blender Y
        z = max(sy * scale, 1e-4)  # MMD height → Blender Z
        mat = Matrix([
            [x, 0, 0, 0],
            [0, y, 0, 0],
            [0, 0, z, 0],
            [0, 0, 0, 1],
        ])
        bmesh.ops.create_cube(bm, size=2, matrix=mat)
    elif shape == RigidShape.CAPSULE:
        radius = max(sx * scale, 1e-4)
        height = max(sy * scale, 1e-4)
        _build_capsule_mesh(bm, radius, height)

    bm.to_mesh(obj.data)
    bm.free()


def _build_capsule_mesh(bm, radius: float, height: float, segments: int = 8, rings: int = 3) -> None:
    """Build a capsule mesh in bmesh: cylinder + hemisphere caps along Z axis."""
    import math

    verts = bm.verts
    half_h = height / 2.0

    # Top cap vertex
    verts.new((0, 0, half_h + radius))

    # Upper hemisphere rings
    for i in range(rings, 0, -1):
        z = radius * math.sin(0.5 * math.pi * i / rings)
        r = math.sqrt(radius ** 2 - z ** 2)
        for j in range(segments):
            theta = 2 * math.pi * j / segments
            verts.new((r * math.cos(theta), r * math.sin(theta), z + half_h))

    # Lower hemisphere rings
    for i in range(rings):
        z = -radius * math.sin(0.5 * math.pi * i / rings)
        r = math.sqrt(radius ** 2 - z ** 2)
        for j in range(segments):
            theta = 2 * math.pi * j / segments
            verts.new((r * math.cos(theta), r * math.sin(theta), z - half_h))

    # Bottom cap vertex
    verts.new((0, 0, -(half_h + radius)))

    verts.ensure_lookup_table()
    faces = bm.faces
    n = len(verts)

    # Top fan
    for j in range(segments):
        j2 = (j + 1) % segments
        faces.new([verts[0], verts[1 + j], verts[1 + j2]])

    # Quads for body rings
    total_rings = rings * 2
    for ring in range(total_rings - 1):
        base = 1 + ring * segments
        for j in range(segments):
            j2 = (j + 1) % segments
            faces.new([
                verts[base + j],
                verts[base + segments + j],
                verts[base + segments + j2],
                verts[base + j2],
            ])

    # Bottom fan
    last = n - 1
    base = 1 + (total_rings - 1) * segments
    for j in range(segments):
        j2 = (j + 1) % segments
        faces.new([verts[last], verts[base + j2], verts[base + j]])


def build_collision_collections(rigid: RigidBody) -> list[bool]:
    """Convert PMX collision group + mask → Blender 20-bool array.

    Public wrapper for testing.
    """
    return _build_collision_collections(rigid)


def _build_collision_collections(rigid: RigidBody) -> list[bool]:
    """Convert PMX collision group → Blender 20-bool array.

    All bodies go on layer 0 (shared) so everything potentially collides.
    Each body also gets its own group layer. Non-collision pairs are then
    suppressed via GENERIC constraints with disable_collisions=True
    (see _create_non_collision_constraints).
    """
    cols = [False] * 20
    cols[0] = True  # shared layer — all bodies can potentially collide
    cols[rigid.collision_group_number] = True
    return cols


def _reposition_dynamic_bodies(model, armature_obj, rigid_objects, bone_names, scale) -> None:
    """Reposition dynamic rigid bodies to match current bone pose.

    PMX rigid body positions are in rest-pose coordinates. If VMD animation
    has moved bones (e.g. head), static (bone-parented) bodies follow, but
    dynamic bodies stay at rest-pose positions. This creates a mismatch at
    joints, causing physics explosions.

    Builds local matrix from PMX data instead of reading obj.matrix_world,
    which may be stale for newly created objects.
    """
    from mathutils import Euler, Matrix, Vector

    for i, rigid in enumerate(model.rigid_bodies):
        if rigid.mode == RigidMode.STATIC:
            continue
        if rigid.bone_index < 0:
            continue
        bone_name = bone_names.get(rigid.bone_index)
        if not bone_name or bone_name not in armature_obj.data.bones:
            continue

        obj = rigid_objects[i]
        bone = armature_obj.data.bones[bone_name]
        pb = armature_obj.pose.bones[bone_name]

        # Compute pose-to-rest delta in world space
        rest_world = armature_obj.matrix_world @ bone.matrix_local
        pose_world = armature_obj.matrix_world @ pb.matrix
        delta = pose_world @ rest_world.inverted()

        # Build local matrix from known PMX data (don't use stale matrix_world)
        rx, ry, rz = rigid.rotation
        loc = Vector(rigid.position) * scale
        rot = Euler((-rx, -ry, -rz), "YXZ")
        local_matrix = Matrix.Translation(loc) @ rot.to_matrix().to_4x4()

        new_matrix = delta @ local_matrix
        t, r, _s = new_matrix.decompose()
        obj.location = t
        obj.rotation_euler = r.to_euler(obj.rotation_mode)


def _create_joints(model, armature_obj, rigid_objects: list, bone_names: dict,
                   scale: float, collection) -> list:
    """Create joint constraints with GENERIC_SPRING and actual spring values.

    Joint empties are repositioned to match bone pose (same delta as
    _reposition_dynamic_bodies) using the source rigid body's bone.
    """
    import bpy
    from mathutils import Euler, Vector

    joint_objects = []

    for i, joint in enumerate(model.joints):
        name = f"J_{i:03d}_{joint.name}"

        obj = bpy.data.objects.new(name, None)
        obj.empty_display_type = "ARROWS"
        obj.empty_display_size = 0.02
        collection.objects.link(obj)

        obj.location = Vector(joint.position) * scale
        obj.rotation_mode = "YXZ"
        # Negate rotation for handedness change (same as rigid bodies)
        rx, ry, rz = joint.rotation
        obj.rotation_euler = Euler((-rx, -ry, -rz), "YXZ")

        # Reposition joint to match posed bone (using src_rigid's bone)
        _reposition_joint_empty(obj, joint, model, armature_obj, bone_names, scale)

        # Add rigid body constraint
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.rigidbody.constraint_add(type="GENERIC_SPRING")
        obj.select_set(False)

        rbc = obj.rigid_body_constraint
        rbc.spring_type = "SPRING1"
        rbc.disable_collisions = False

        # Connect to rigid bodies
        if 0 <= joint.src_rigid < len(rigid_objects):
            rbc.object1 = rigid_objects[joint.src_rigid]
        if 0 <= joint.dest_rigid < len(rigid_objects):
            rbc.object2 = rigid_objects[joint.dest_rigid]

        # Enable all 6 DOF limits
        rbc.use_limit_lin_x = True
        rbc.use_limit_lin_y = True
        rbc.use_limit_lin_z = True
        rbc.use_limit_ang_x = True
        rbc.use_limit_ang_y = True
        rbc.use_limit_ang_z = True

        # Translation limits (with scale)
        rbc.limit_lin_x_lower = joint.limit_move_lower[0] * scale
        rbc.limit_lin_x_upper = joint.limit_move_upper[0] * scale
        rbc.limit_lin_y_lower = joint.limit_move_lower[1] * scale
        rbc.limit_lin_y_upper = joint.limit_move_upper[1] * scale
        rbc.limit_lin_z_lower = joint.limit_move_lower[2] * scale
        rbc.limit_lin_z_upper = joint.limit_move_upper[2] * scale

        # Rotation limits: negate AND swap min/max for handedness change.
        # mmd_tools: minimum_rotation = joint.maximum_rotation.xzy * -1
        #            maximum_rotation = joint.minimum_rotation.xzy * -1
        # Our parser already did .xzy swap, so we just negate and swap.
        rbc.limit_ang_x_lower = -joint.limit_rotate_upper[0]
        rbc.limit_ang_x_upper = -joint.limit_rotate_lower[0]
        rbc.limit_ang_y_lower = -joint.limit_rotate_upper[1]
        rbc.limit_ang_y_upper = -joint.limit_rotate_lower[1]
        rbc.limit_ang_z_lower = -joint.limit_rotate_upper[2]
        rbc.limit_ang_z_upper = -joint.limit_rotate_lower[2]

        # Springs provide restoring force that keeps chain bodies together.
        # Without springs, bodies scatter to joint limit edges under gravity.
        rbc.use_spring_x = True
        rbc.use_spring_y = True
        rbc.use_spring_z = True
        rbc.use_spring_ang_x = True
        rbc.use_spring_ang_y = True
        rbc.use_spring_ang_z = True

        rbc.spring_stiffness_x = joint.spring_constant_move[0]
        rbc.spring_stiffness_y = joint.spring_constant_move[1]
        rbc.spring_stiffness_z = joint.spring_constant_move[2]
        rbc.spring_stiffness_ang_x = joint.spring_constant_rotate[0]
        rbc.spring_stiffness_ang_y = joint.spring_constant_rotate[1]
        rbc.spring_stiffness_ang_z = joint.spring_constant_rotate[2]

        # Soft constraints (_apply_soft_constraints) disabled:
        # unlocking locked DOFs (lower > upper trick) causes oscillation.
        # Keep functions in file — tested and may re-enable for experimentation.

        obj["mmd_joint_index"] = i
        joint_objects.append(obj)

    return joint_objects


def _reposition_joint_empty(obj, joint, model, armature_obj, bone_names, scale) -> None:
    """Apply pose-to-rest delta to a joint empty using its src_rigid's bone.

    Builds the local matrix from the joint position/rotation directly instead
    of reading obj.matrix_world, which is stale for newly created objects
    (depsgraph hasn't evaluated yet).
    """
    from mathutils import Euler, Matrix, Vector

    if joint.src_rigid < 0 or joint.src_rigid >= len(model.rigid_bodies):
        return
    src_rigid = model.rigid_bodies[joint.src_rigid]
    if src_rigid.bone_index < 0:
        return
    bone_name = bone_names.get(src_rigid.bone_index)
    if not bone_name or bone_name not in armature_obj.data.bones:
        return

    bone = armature_obj.data.bones[bone_name]
    pb = armature_obj.pose.bones[bone_name]

    rest_world = armature_obj.matrix_world @ bone.matrix_local
    pose_world = armature_obj.matrix_world @ pb.matrix
    delta = pose_world @ rest_world.inverted()

    # Build local matrix from known location/rotation (don't use stale matrix_world)
    rx, ry, rz = joint.rotation
    loc = Vector(joint.position) * scale
    rot = Euler((-rx, -ry, -rz), "YXZ")
    local_matrix = Matrix.Translation(loc) @ rot.to_matrix().to_4x4()

    new_matrix = delta @ local_matrix
    t, r, _s = new_matrix.decompose()
    obj.location = t
    obj.rotation_euler = r.to_euler(obj.rotation_mode)


def is_locked_dof(lower: float, upper: float) -> bool:
    """Check if a DOF is locked (lower == upper within tolerance).

    Public for testing.
    """
    return abs(upper - lower) < 1e-6


def _apply_soft_constraints(rbc) -> None:
    """Unlock locked angular DOFs by setting lower > upper (Bullet: free).

    Only applied to angular limits. Translation [0,0] means "locked at joint
    pivot" which is correct — it keeps bodies connected. Unlocking translation
    would let bodies separate and fly apart.
    """
    for axis in ("x", "y", "z"):
        lo = getattr(rbc, f"limit_ang_{axis}_lower")
        hi = getattr(rbc, f"limit_ang_{axis}_upper")
        if abs(hi - lo) < 1e-6:
            setattr(rbc, f"limit_ang_{axis}_lower", 1.0)
            setattr(rbc, f"limit_ang_{axis}_upper", 0.0)


def _create_non_collision_constraints(model, rigid_objects, joint_objects, collection) -> None:
    """Create non-collision constraints based on PMX collision_group_mask.

    PMX uses a bilateral mask: each body has a 16-bit mask where bit N set
    means "collide with group N". Blender's collision_collections is symmetric
    and can't represent this, so all bodies share layer 0 (everything can
    potentially collide) and we use GENERIC constraints with
    disable_collisions=True to suppress unwanted pairs.

    For pairs that already have a joint, we set disable_collisions on the
    existing joint. For nearby pairs without a joint, we create a new GENERIC
    constraint empty.
    """
    import bpy

    # Build group map: group_number → list of rigid indices
    group_map: dict[int, list[int]] = {}
    for i, rigid in enumerate(model.rigid_bodies):
        group_map.setdefault(rigid.collision_group_number, []).append(i)

    # Map joint pairs → joint objects for reusing existing constraints
    joint_pair_map: dict[frozenset, object] = {}
    for j_idx, joint in enumerate(model.joints):
        src, dst = joint.src_rigid, joint.dest_rigid
        if 0 <= src < len(rigid_objects) and 0 <= dst < len(rigid_objects):
            joint_pair_map[frozenset((src, dst))] = joint_objects[j_idx]

    processed: set[frozenset] = set()
    nc_count = 0

    for i, rigid_a in enumerate(model.rigid_bodies):
        mask = rigid_a.collision_group_mask
        for g in range(16):
            if mask & (1 << g):
                continue  # bit set = collides with group g
            # bit unset = should NOT collide with group g
            for j in group_map.get(g, []):
                if i == j:
                    continue
                pair = frozenset((i, j))
                if pair in processed:
                    continue
                processed.add(pair)

                if pair in joint_pair_map:
                    # Set disable_collisions on existing joint
                    joint_obj = joint_pair_map[pair]
                    joint_obj.rigid_body_constraint.disable_collisions = True
                else:
                    # Check proximity — only create constraint for nearby bodies
                    obj_a = rigid_objects[i]
                    obj_b = rigid_objects[j]
                    dist = (obj_a.location - obj_b.location).length
                    range_a = _object_range(obj_a)
                    range_b = _object_range(obj_b)
                    threshold = 1.5 * (range_a + range_b) * 0.5

                    if dist < threshold:
                        _create_non_collision_empty(bpy, obj_a, obj_b, collection)
                        nc_count += 1

    if nc_count > 0:
        log.info("Created %d non-collision constraints", nc_count)


def _object_range(obj) -> float:
    """Bounding box diagonal of a Blender object."""
    d = obj.dimensions
    return (d[0] ** 2 + d[1] ** 2 + d[2] ** 2) ** 0.5


def _create_non_collision_empty(bpy, obj_a, obj_b, collection) -> None:
    """Create a GENERIC constraint empty that disables collision between two bodies."""
    name = f"NC_{obj_a.name[:15]}_{obj_b.name[:15]}"
    empty = bpy.data.objects.new(name, None)
    empty.empty_display_size = 0.01
    empty.hide_render = True
    collection.objects.link(empty)

    # Position at midpoint
    empty.location = (obj_a.location + obj_b.location) / 2

    bpy.context.view_layer.objects.active = empty
    empty.select_set(True)
    bpy.ops.rigidbody.constraint_add(type="GENERIC")
    empty.select_set(False)

    rbc = empty.rigid_body_constraint
    rbc.object1 = obj_a
    rbc.object2 = obj_b
    rbc.disable_collisions = True


def _setup_bone_coupling(
    armature_obj, model, rigid_objects: list,
    bone_names: dict[int, str], scale: float, collection,
) -> None:
    """Wire up bone↔rigid body for STATIC/DYNAMIC/DYNAMIC_BONE modes."""
    # Track which bones already have a dynamic rigid body assigned.
    # If multiple target the same bone, use the heaviest.
    bone_assignments: dict[str, tuple[float, int]] = {}  # bone_name → (mass, rigid_index)

    for i, rigid in enumerate(model.rigid_bodies):
        if rigid.bone_index < 0:
            continue
        bone_name = bone_names.get(rigid.bone_index)
        if not bone_name:
            continue

        if rigid.mode == RigidMode.STATIC:
            _setup_static_coupling(armature_obj, rigid_objects[i], bone_name)
        elif rigid.mode in (RigidMode.DYNAMIC, RigidMode.DYNAMIC_BONE):
            prev = bone_assignments.get(bone_name)
            if prev is None or rigid.mass > prev[0]:
                bone_assignments[bone_name] = (rigid.mass, i)

    # Apply dynamic couplings (heaviest wins per bone)
    for bone_name, (mass, rigid_idx) in bone_assignments.items():
        rigid = model.rigid_bodies[rigid_idx]
        rb_obj = rigid_objects[rigid_idx]
        if rigid.mode == RigidMode.DYNAMIC:
            _setup_dynamic_coupling(armature_obj, rb_obj, bone_name, collection)
        else:
            _setup_dynamic_bone_coupling(armature_obj, rb_obj, bone_name, collection)


def _setup_static_coupling(armature_obj, rb_obj, bone_name: str) -> None:
    """STATIC: bone drives rigid body via bone parenting."""
    from mathutils import Matrix

    rb_obj.parent = armature_obj
    rb_obj.parent_type = "BONE"
    rb_obj.parent_bone = bone_name

    # Bone parenting origin is at the bone's TAIL, using the bone's rest matrix.
    # Parent transform = armature.matrix_world @ bone.matrix_local @ T(0, bone_length, 0)
    bone = armature_obj.data.bones[bone_name]
    parent_matrix = (
        armature_obj.matrix_world
        @ bone.matrix_local
        @ Matrix.Translation((0, bone.length, 0))
    )
    rb_obj.matrix_parent_inverse = parent_matrix.inverted()


def _setup_dynamic_coupling(armature_obj, rb_obj, bone_name: str, collection) -> None:
    """DYNAMIC: physics drives bone rotation via tracking empty + COPY_ROTATION.

    Uses COPY_ROTATION (not COPY_TRANSFORMS) because bone position is determined
    by the armature hierarchy. Physics only drives rotation.
    """
    empty = _create_tracking_empty(armature_obj, rb_obj, bone_name, collection)
    pb = armature_obj.pose.bones[bone_name]
    c = pb.constraints.new("COPY_ROTATION")
    c.name = "mmd_dynamic"
    c.target = empty


def _setup_dynamic_bone_coupling(armature_obj, rb_obj, bone_name: str, collection) -> None:
    """DYNAMIC_BONE: physics drives bone rotation via tracking empty + COPY_ROTATION."""
    empty = _create_tracking_empty(armature_obj, rb_obj, bone_name, collection)
    pb = armature_obj.pose.bones[bone_name]
    c = pb.constraints.new("COPY_ROTATION")
    c.name = "mmd_dynamic_bone"
    c.target = empty


def _create_tracking_empty(armature_obj, rb_obj, bone_name: str, collection):
    """Create an empty at the bone's world position, parented to the rigid body.

    The empty inherits the bone's current world matrix so it tracks properly.
    When the rigid body moves (physics), the empty follows (parenting),
    and the bone follows (COPY_ROTATION constraint).
    """
    import bpy

    empty = bpy.data.objects.new(f"Track_{bone_name}", None)
    empty.empty_display_size = 0.01
    collection.objects.link(empty)

    # Set empty to bone's current world transform, then parent to rigid body.
    # matrix_parent_inverse preserves the world position after parenting.
    pb = armature_obj.pose.bones[bone_name]
    bone_world = armature_obj.matrix_world @ pb.matrix
    empty.parent = rb_obj
    empty.matrix_parent_inverse = rb_obj.matrix_world.inverted() @ bone_world
    return empty


def _setup_physics_world(scene, scale: float = 0.08) -> None:
    """Configure physics world: substeps, solver iterations, and gravity.

    At 0.08 import scale, models are ~1.3m in Blender representing ~160cm.
    Default gravity (-9.81) is effectively too strong for this scale, tearing
    joints apart. Scale gravity proportionally.
    """
    from mathutils import Vector

    rbw = scene.rigidbody_world
    if rbw is None:
        return

    # Higher substeps + iterations = tighter constraint enforcement.
    # At small scale, the solver needs more passes to maintain zero-translation joints.
    rbw.substeps_per_frame = 60
    rbw.solver_iterations = 60

    # Scale gravity to match model scale
    scene.gravity = Vector((0, 0, -9.81 * scale))


def _set_rigid_body_world_enabled(scene, enable: bool) -> bool:
    """Enable/disable the rigid body world, returning previous state.

    If no RB world exists yet, creates one (disabled). This prevents the
    solver from running while bodies are being added during physics build.
    """
    import bpy

    if scene.rigidbody_world is None:
        # bpy.ops.rigidbody.world_add() requires poll context
        if bpy.ops.rigidbody.world_add.poll():
            bpy.ops.rigidbody.world_add()
            scene.rigidbody_world.enabled = False

    rbw = scene.rigidbody_world
    if rbw is None:
        return True  # default: enabled
    was_enabled = rbw.enabled
    rbw.enabled = enable
    return was_enabled
