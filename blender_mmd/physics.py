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


def build_physics(
    armature_obj, model, scale: float, mode: str = "none",
    collision_quality: str = "high",
) -> None:
    """Build physics for an MMD model.

    Args:
        mode: "none" (metadata only), "rigid_body" (Blender RB), or "cloth" (detect chains).
        collision_quality: "high" (full NCC empties) or "draft" (no collisions, fast preview).
    """
    log.info(
        "Building physics (mode=%s, quality=%s): %d rigid bodies, %d joints",
        mode, collision_quality, len(model.rigid_bodies), len(model.joints),
    )

    # Clean up any existing physics first
    clear_physics(armature_obj)

    # Always store metadata — available for all modes
    armature_obj["mmd_physics_data"] = serialize_physics_data(model)
    armature_obj["physics_mode"] = mode
    armature_obj["collision_quality"] = collision_quality

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
        _build_rigid_body_physics(armature_obj, model, scale, collision_quality)
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


def _build_rigid_body_physics(
    armature_obj, model, scale: float, collision_quality: str = "high",
) -> None:
    """Create rigid bodies, joints, bone coupling, and configure physics world.

    Three-phase build:
      Phase 1 (CREATE): Collections, rigid bodies, joints, joint non-collisions.
      Phase 2 (POSITION): Reposition dynamic bodies and tracking empties.
      Phase 3 (COUPLE & ACTIVATE): Bone coupling, world setup, chains.

    The rigid body world is disabled during setup (following mmd_tools' approach)
    to prevent the solver from computing forces while bodies are being added.
    """
    import bpy

    _set_rigid_body_world_enabled(bpy.context.scene, False)

    try:
        # --- Phase 1: CREATE (no depsgraph needed) ---

        # Remove stale collections from previous builds (objects removed but
        # collections left behind → bpy.data.collections.new gives ".001" suffix).
        # Must remove children first (Rigid Bodies, Joints, Tracking) or they
        # become orphans that cause ".001" on the next build.
        col_name = f"{armature_obj.name}_Physics"
        for stale in [c for c in bpy.data.collections if c.name.startswith(col_name)]:
            for child in list(stale.children):
                bpy.data.collections.remove(child)
            bpy.data.collections.remove(stale)

        collection = bpy.data.collections.new(col_name)
        bpy.context.scene.collection.children.link(collection)
        armature_obj["physics_collection"] = col_name

        rb_col = bpy.data.collections.new("Rigid Bodies")
        collection.children.link(rb_col)
        joint_col = bpy.data.collections.new("Joints")
        collection.children.link(joint_col)
        track_col = bpy.data.collections.new("Tracking")
        collection.children.link(track_col)

        bone_names = _build_bone_name_map(armature_obj)

        # Read per-chain disable states for collision layer assignment
        collision_disabled = set(json.loads(armature_obj.get("mmd_chain_collision_disabled", "[]")))
        physics_disabled = set(json.loads(armature_obj.get("mmd_chain_physics_disabled", "[]")))

        # Detect chains early so we can use them for collision layer decisions
        from .chains import detect_chains
        chains = detect_chains(model)
        chain_dicts = [_chain_to_dict(c) for c in chains]

        # Build rigid_index → chain_name lookup
        rigid_to_chain = _build_rigid_to_chain_map(chain_dicts)

        rigid_objects = _create_rigid_bodies(
            model, armature_obj, scale, rb_col,
            collision_quality=collision_quality,
            collision_disabled_chains=collision_disabled,
            rigid_to_chain=rigid_to_chain,
        )
        joint_objects = _create_joints(model, armature_obj, rigid_objects, bone_names, scale, joint_col)

        if collision_quality == "high":
            self_collision_disabled = set(json.loads(
                armature_obj.get("mmd_chain_self_collision_disabled", "[]")
            ))
            _create_non_collision_constraints(
                model, rigid_objects, joint_objects, joint_col,
                chain_dicts=chain_dicts,
                collision_disabled_chains=collision_disabled,
                rigid_to_chain=rigid_to_chain,
                self_collision_disabled_chains=self_collision_disabled,
            )
        else:
            # Draft: skip NCC empties entirely. Set disable_collisions on joints.
            for j_obj in joint_objects:
                rbc = j_obj.rigid_body_constraint
                if rbc:
                    rbc.disable_collisions = True
            log.info("Draft quality: skipped NCC empties, %d joints set disable_collisions", len(joint_objects))

        # --- Phase 2: POSITION (needs depsgraph for matrix_world) ---

        _mute_physics_ik_constraints(armature_obj, model, bone_names, mute=True)
        _reposition_dynamic_bodies(model, armature_obj, rigid_objects, bone_names, scale)

        # Flush so matrix_world is current for tracking empty creation
        bpy.context.scene.frame_set(bpy.context.scene.frame_current)

        empty_parent_map = _setup_bone_coupling(
            armature_obj, model, rigid_objects, bone_names, scale, track_col,
        )

        # Flush so tracking empties have correct matrix_world before reparenting
        bpy.context.scene.frame_set(bpy.context.scene.frame_current)

        _reparent_tracking_empties(empty_parent_map)

        # Flush after reparenting so parent inverse matrices are evaluated
        bpy.context.scene.frame_set(bpy.context.scene.frame_current)

        # --- Phase 3: COUPLE & ACTIVATE ---

        _unmute_tracking_constraints(armature_obj)
        _mute_physics_ik_constraints(armature_obj, model, bone_names, mute=False)
        _setup_physics_world(bpy.context.scene, scale)

        vl_col = bpy.context.view_layer.layer_collection.children.get(col_name)
        if vl_col:
            vl_col.hide_viewport = True

    finally:
        _set_rigid_body_world_enabled(bpy.context.scene, True)

    # Store chains (already detected above)
    armature_obj["mmd_physics_chains"] = json.dumps(chain_dicts)

    # Apply per-chain physics disabled (kinematic) state
    if physics_disabled:
        _apply_chain_physics_disabled(armature_obj, chain_dicts, physics_disabled, model)

    log.info("Physics build complete: %d rigid bodies, %d joints, %d chains (quality=%s)",
             len(rigid_objects), len(model.joints), len(chains), collision_quality)


def _build_rigid_to_chain_map(chain_dicts: list[dict]) -> dict[int, str]:
    """Map rigid body index → chain name from chain dicts."""
    result: dict[int, str] = {}
    for chain in chain_dicts:
        name = chain["name"]
        for ri in chain.get("rigid_indices", []):
            result[ri] = name
    return result


def _apply_chain_physics_disabled(
    armature_obj, chain_dicts: list[dict], disabled_chains: set[str], model,
) -> None:
    """Set kinematic=True for bodies in physics-disabled chains after build."""
    import bpy

    col_name = armature_obj.get("physics_collection")
    if not col_name:
        return
    col = bpy.data.collections.get(col_name)
    if not col:
        return
    rb_col = col.children.get("Rigid Bodies")
    if not rb_col:
        return

    rb_by_index: dict[int, object] = {}
    for obj in rb_col.objects:
        idx = obj.get("mmd_rigid_index")
        if idx is not None:
            rb_by_index[idx] = obj

    for chain in chain_dicts:
        if chain["name"] not in disabled_chains:
            continue
        for ri in chain.get("rigid_indices", []):
            obj = rb_by_index.get(ri)
            if obj and obj.rigid_body:
                obj.rigid_body.kinematic = True


def clear_physics(armature_obj) -> None:
    """Remove all physics objects and metadata for this armature."""
    import bpy

    col_name = armature_obj.get("physics_collection")
    if col_name:
        collection = bpy.data.collections.get(col_name)
        if collection:
            # Remove tracking constraints and unmute IK constraints
            if armature_obj.pose:
                # Only remove physics-specific constraints, NOT import-time
                # constraints like mmd_at_dummy (additional transform shadow
                # bones) or mmd_ik_limit_override.
                _physics_constraint_names = ("mmd_dynamic", "mmd_dynamic_bone")
                for pb in armature_obj.pose.bones:
                    to_remove = [
                        c for c in pb.constraints
                        if c.name in _physics_constraint_names
                    ]
                    for c in to_remove:
                        pb.constraints.remove(c)
                    # Unmute any IK constraints that were muted during build
                    for c in pb.constraints:
                        if c.type == "IK" and c.mute:
                            c.mute = False

            # Disable RBW before mass deletion
            rbw = bpy.context.scene.rigidbody_world
            if rbw:
                rbw.enabled = False

            # Batch-remove all physics objects in one call to avoid
            # per-object depsgraph/physics updates that hang on 25K+ objects
            all_objs = []
            def _collect_objects(col):
                for child in list(col.children):
                    _collect_objects(child)
                all_objs.extend(list(col.objects))
            _collect_objects(collection)

            if all_objs:
                bpy.data.batch_remove(all_objs)

            # Remove empty collections
            def _remove_collections(col):
                for child in list(col.children):
                    _remove_collections(child)
                bpy.data.collections.remove(col)
            _remove_collections(collection)

        if "physics_collection" in armature_obj:
            del armature_obj["physics_collection"]

    # Clean metadata keys (preserve user settings: disabled chains, quality)
    for key in ("mmd_physics_data", "physics_mode", "mmd_physics_chains"):
        if key in armature_obj:
            del armature_obj[key]
    # Note: mmd_chain_collision_disabled, mmd_chain_physics_disabled,
    # mmd_chain_self_collision_disabled, and collision_quality are
    # intentionally preserved across clear/rebuild so user settings survive.


def _mute_tracking_constraints(armature_obj, mute: bool = True) -> None:
    """Mute or unmute mmd_dynamic / mmd_dynamic_bone constraints on pose bones."""
    if not armature_obj.pose:
        return
    for pb in armature_obj.pose.bones:
        for c in pb.constraints:
            if c.name in ("mmd_dynamic", "mmd_dynamic_bone"):
                c.mute = mute


def reset_physics(armature_obj) -> int:
    """Reset existing rigid bodies to match current bone pose.

    Fast alternative to rebuild — repositions dynamic bodies and tracking
    empties without recreating any objects. Frees the RBW cache so physics
    re-simulates from the new positions.

    Returns the number of bodies repositioned.
    """
    import bpy
    import json
    from mathutils import Euler, Matrix, Vector

    col_name = armature_obj.get("physics_collection")
    if not col_name:
        return 0

    collection = bpy.data.collections.get(col_name)
    if not collection:
        return 0

    phys_json = armature_obj.get("mmd_physics_data")
    if not phys_json:
        return 0

    data = json.loads(phys_json)
    rigid_bodies = data["rigid_bodies"]
    scale = armature_obj.get("import_scale", 0.08)

    # Build lookups
    bone_names = _build_bone_name_map(armature_obj)

    # Index existing rigid body objects by their PMX index
    rb_col = collection.children.get("Rigid Bodies")
    if not rb_col:
        return 0

    rb_objects: dict[int, object] = {}
    for obj in rb_col.objects:
        idx = obj.get("mmd_rigid_index")
        if idx is not None:
            rb_objects[idx] = obj

    # Mute dynamic tracking constraints before computing bone deltas.
    # Without this, pose bones are pulled toward displaced rigid bodies
    # (via COPY_ROTATION/COPY_TRANSFORMS), making the delta non-identity
    # even when bones should be at rest. This creates a circular dependency
    # where reset computes positions matching the already-displaced state.
    _mute_tracking_constraints(armature_obj, mute=True)
    bpy.context.view_layer.update()

    # Disable rigid body world to prevent cache from overriding positions
    scene = bpy.context.scene
    rbw = scene.rigidbody_world
    rbw_was_enabled = False
    if rbw:
        rbw_was_enabled = rbw.enabled
        rbw.enabled = False

    # Reposition dynamic rigid bodies
    count = 0
    for i, rb_data in enumerate(rigid_bodies):
        mode = rb_data["mode"]  # 0=STATIC, 1=DYNAMIC, 2=DYNAMIC_BONE
        if mode == 0:
            continue
        bone_idx = rb_data["bone_index"]
        if bone_idx < 0:
            continue
        bone_name = bone_names.get(bone_idx)
        if not bone_name or bone_name not in armature_obj.data.bones:
            continue
        obj = rb_objects.get(i)
        if obj is None:
            continue

        bone = armature_obj.data.bones[bone_name]
        pb = armature_obj.pose.bones[bone_name]

        # Compute pose-to-rest delta
        rest_world = armature_obj.matrix_world @ bone.matrix_local
        pose_world = armature_obj.matrix_world @ pb.matrix
        delta = pose_world @ rest_world.inverted()

        # Build rest-pose matrix from stored PMX data
        rx, ry, rz = rb_data["rotation"]
        loc = Vector(rb_data["position"]) * scale
        rot = Euler((-rx, -ry, -rz), "YXZ")
        local_matrix = Matrix.Translation(loc) @ rot.to_matrix().to_4x4()

        new_matrix = delta @ local_matrix
        t, r, _s = new_matrix.decompose()
        obj.location = t
        obj.rotation_euler = r.to_euler(obj.rotation_mode)
        count += 1

    # Flush RB positions to depsgraph — tracking empties are parented to RBs,
    # so parent matrix_world must be current before we set empty.matrix_world
    bpy.context.view_layer.update()

    # Reposition tracking empties to match bone world positions
    track_col = collection.children.get("Tracking")
    if track_col:
        for empty in track_col.objects:
            # Track_<bone_name>
            if not empty.name.startswith("Track_"):
                continue
            bone_name = empty.name[6:]  # strip "Track_"
            pb = armature_obj.pose.bones.get(bone_name)
            if pb is None:
                continue
            bone_world = armature_obj.matrix_world @ pb.matrix
            # Preserve parent relationship — save and restore matrix_world
            empty.matrix_world = bone_world

    # Reposition joint empties
    joint_col = collection.children.get("Joints")
    if joint_col:
        joints = data.get("joints", [])
        for obj in joint_col.objects:
            j_idx = obj.get("mmd_joint_index")
            if j_idx is None or j_idx >= len(joints):
                continue
            joint = joints[j_idx]
            src_idx = joint["src_rigid"]
            if src_idx < 0 or src_idx >= len(rigid_bodies):
                continue
            src_rb = rigid_bodies[src_idx]
            bone_idx = src_rb["bone_index"]
            if bone_idx < 0:
                continue
            bone_name = bone_names.get(bone_idx)
            if not bone_name or bone_name not in armature_obj.data.bones:
                continue

            bone = armature_obj.data.bones[bone_name]
            pb = armature_obj.pose.bones[bone_name]
            rest_world = armature_obj.matrix_world @ bone.matrix_local
            pose_world = armature_obj.matrix_world @ pb.matrix
            delta = pose_world @ rest_world.inverted()

            rx, ry, rz = joint["rotation"]
            loc = Vector(joint["position"]) * scale
            rot = Euler((-rx, -ry, -rz), "YXZ")
            local_matrix = Matrix.Translation(loc) @ rot.to_matrix().to_4x4()

            new_matrix = delta @ local_matrix
            t, r, _s = new_matrix.decompose()
            obj.location = t
            obj.rotation_euler = r.to_euler(obj.rotation_mode)

    # Flush repositioned transforms to depsgraph while physics is still disabled
    bpy.context.view_layer.update()

    # Re-enable rigid body world with cleared cache
    if rbw and rbw_was_enabled:
        rbw.enabled = True
        if rbw.point_cache:
            rbw.point_cache.frame_start = scene.frame_start
            rbw.point_cache.frame_end = scene.frame_end
    _mute_tracking_constraints(armature_obj, mute=False)

    # Go to start frame — Blender initializes RB positions from transforms
    # at frame_start (no simulation step), so bodies stay where we put them.
    bpy.context.scene.frame_set(scene.frame_start)

    log.info("Reset %d dynamic rigid bodies to current pose", count)
    return count


def remove_chain(armature_obj, chain_index: int) -> str:
    """Remove a single physics chain by index.

    Deletes the chain's rigid bodies, joints, tracking empties, and
    bone constraints. Updates stored chain metadata.

    Returns the chain name for reporting.
    """
    import bpy
    import json

    chains_json = armature_obj.get("mmd_physics_chains")
    if not chains_json:
        raise ValueError("No chain data stored on armature")

    chains = json.loads(chains_json)
    if chain_index < 0 or chain_index >= len(chains):
        raise ValueError(f"Chain index {chain_index} out of range (0-{len(chains)-1})")

    chain = chains[chain_index]
    chain_name = chain["name"]
    bone_names = _build_bone_name_map(armature_obj)

    col_name = armature_obj.get("physics_collection")
    if not col_name:
        raise ValueError("No physics collection found")
    collection = bpy.data.collections.get(col_name)
    if not collection:
        raise ValueError(f"Collection '{col_name}' not found")

    rigid_indices = set(chain["rigid_indices"])
    joint_indices = set(chain["joint_indices"])
    chain_bone_indices = set(chain.get("bone_indices", []))

    removed = 0

    # Remove rigid body objects
    rb_col = collection.children.get("Rigid Bodies")
    if rb_col:
        for obj in list(rb_col.objects):
            idx = obj.get("mmd_rigid_index")
            if idx is not None and idx in rigid_indices:
                bpy.data.objects.remove(obj, do_unlink=True)
                removed += 1

    # Remove joint objects and NCC empties referencing chain's rigid bodies
    joint_col = collection.children.get("Joints")
    if joint_col:
        for obj in list(joint_col.objects):
            idx = obj.get("mmd_joint_index")
            if idx is not None and idx in joint_indices:
                bpy.data.objects.remove(obj, do_unlink=True)
                continue
            # Check NCC empties (no mmd_joint_index, have rigid_body_constraint)
            if idx is None and obj.rigid_body_constraint:
                rbc = obj.rigid_body_constraint
                obj1_idx = rbc.object1.get("mmd_rigid_index") if rbc.object1 else None
                obj2_idx = rbc.object2.get("mmd_rigid_index") if rbc.object2 else None
                if (obj1_idx is not None and obj1_idx in rigid_indices) or \
                   (obj2_idx is not None and obj2_idx in rigid_indices):
                    bpy.data.objects.remove(obj, do_unlink=True)

    # Remove tracking empties and bone constraints for chain bones
    track_col = collection.children.get("Tracking")
    for bone_idx in chain_bone_indices:
        bname = bone_names.get(bone_idx)
        if not bname:
            continue

        # Remove tracking empty
        if track_col:
            empty_name = f"Track_{bname}"
            for obj in list(track_col.objects):
                if obj.name == empty_name:
                    bpy.data.objects.remove(obj, do_unlink=True)
                    break

        # Remove physics constraints on the bone
        pb = armature_obj.pose.bones.get(bname)
        if pb:
            for c in list(pb.constraints):
                if c.name in ("mmd_dynamic", "mmd_dynamic_bone"):
                    pb.constraints.remove(c)

    # Update stored chain data (remove this chain)
    chains.pop(chain_index)
    armature_obj["mmd_physics_chains"] = json.dumps(chains)

    # Flush depsgraph so freed bones snap back to rest/keyframed pose
    bpy.context.scene.frame_set(bpy.context.scene.frame_current)

    log.info("Removed chain '%s': %d rigid bodies", chain_name, removed)
    return chain_name


def rebuild_ncc(armature_obj) -> tuple[int, int]:
    """Rebuild non-collision constraint empties respecting current settings.

    Reuses existing NCC empties by reassigning their object1/object2 pairs.
    Only creates or removes the difference. Uses serialized physics data on the
    armature (no PMX re-parse needed).

    Returns (old_count, new_count) of NCC empties.
    """
    import bpy

    phys_json = armature_obj.get("mmd_physics_data")
    if not phys_json:
        raise ValueError("No physics data on armature")

    col_name = armature_obj.get("physics_collection")
    if not col_name:
        raise ValueError("No physics collection found")
    collection = bpy.data.collections.get(col_name)
    if not collection:
        raise ValueError(f"Collection '{col_name}' not found")

    joint_col = collection.children.get("Joints")
    if not joint_col:
        raise ValueError("No Joints collection found")

    rb_col = collection.children.get("Rigid Bodies")
    if not rb_col:
        raise ValueError("No Rigid Bodies collection found")

    # Collect existing NCC empties (have rigid_body_constraint but no mmd_joint_index)
    ncc_objs = [
        obj for obj in joint_col.objects
        if obj.get("mmd_joint_index") is None and obj.rigid_body_constraint
    ]
    old_count = len(ncc_objs)

    # Use serialized data from armature (no PMX re-parse)
    phys_data = json.loads(phys_json)
    rb_data_list = phys_data["rigid_bodies"]
    joints_data = phys_data["joints"]

    # Build rigid body object lookup
    rigid_objects = [None] * len(rb_data_list)
    for obj in rb_col.objects:
        idx = obj.get("mmd_rigid_index")
        if idx is not None and 0 <= idx < len(rigid_objects):
            rigid_objects[idx] = obj

    # Read settings from armature
    collision_disabled = set(json.loads(armature_obj.get("mmd_chain_collision_disabled", "[]")))
    self_collision_disabled = set(json.loads(
        armature_obj.get("mmd_chain_self_collision_disabled", "[]")
    ))
    chains_json = armature_obj.get("mmd_physics_chains")
    chain_dicts = json.loads(chains_json) if chains_json else []
    rigid_to_chain = _build_rigid_to_chain_map(chain_dicts)

    # Compute NCC pair table from serialized data (pure Python, fast)
    pair_table = _compute_ncc_pairs(
        rb_data_list, joints_data, rigid_objects,
        collision_disabled_chains=collision_disabled,
        rigid_to_chain=rigid_to_chain,
        self_collision_disabled_chains=self_collision_disabled,
    )
    new_count = len(pair_table)
    print(f"NCC rebuild: {old_count} existing, {new_count} needed")

    # Disable RBW during modifications
    scene = bpy.context.scene
    rbw = scene.rigidbody_world
    rbw_was_enabled = False
    if rbw:
        rbw_was_enabled = rbw.enabled
        rbw.enabled = False

    try:
        # Reassign existing NCC empties to new pairs (no create/delete needed)
        reuse_count = min(old_count, new_count)
        for i in range(reuse_count):
            rbc = ncc_objs[i].rigid_body_constraint
            rbc.object1 = pair_table[i][0]
            rbc.object2 = pair_table[i][1]

        if new_count < old_count:
            # Batch-remove excess NCC empties (single depsgraph update)
            excess_objs = ncc_objs[new_count:]
            print(f"Removing {len(excess_objs)} excess NCC empties...")
            bpy.data.batch_remove(excess_objs)
            print(f"Removed {len(excess_objs)} NCC empties")
        elif new_count > old_count:
            # Create additional NCC empties — needs visible collection for
            # bpy.ops.rigidbody.constraint_add / bpy.ops.object.duplicate
            vl_col = bpy.context.view_layer.layer_collection.children.get(col_name)
            was_hidden = vl_col and vl_col.hide_viewport
            if was_hidden:
                vl_col.hide_viewport = False
            extra_pairs = pair_table[old_count:]
            print(f"Creating {len(extra_pairs)} additional NCC empties...")
            _create_non_collision_empties(bpy, extra_pairs, joint_col)
            if was_hidden:
                vl_col.hide_viewport = True

        print(f"NCC rebuild done: {old_count} → {new_count}")
    finally:
        if rbw and rbw_was_enabled:
            rbw.enabled = True

    # Clear physics cache
    scene.frame_set(scene.frame_current)

    log.info("Rebuilt NCCs: %d → %d", old_count, new_count)
    return old_count, new_count


def _compute_ncc_pairs(
    rb_data_list: list[dict], joints_data: list[dict], rigid_objects: list,
    collision_disabled_chains: set[str] | None = None,
    rigid_to_chain: dict[int, str] | None = None,
    self_collision_disabled_chains: set[str] | None = None,
) -> list[tuple]:
    """Compute non-collision pair table from serialized physics data.

    Pure Python except for rigid_objects references in the output tuples.
    Used by both initial build (via _create_non_collision_constraints) and
    rebuild_ncc to avoid re-parsing PMX.

    Returns list of (obj_a, obj_b) tuples for NCC empty creation.
    """
    collision_disabled_chains = collision_disabled_chains or set()
    rigid_to_chain = rigid_to_chain or {}
    self_collision_disabled_chains = self_collision_disabled_chains or set()

    n_bodies = len(rigid_objects)

    # Group rigid body indices by collision_group_number
    group_map: dict[int, list[int]] = {}
    for i, rb in enumerate(rb_data_list):
        group_map.setdefault(rb["collision_group_number"], []).append(i)

    # Map joint pairs (already have disable_collisions on joint objects)
    joint_pair_set: set[frozenset] = set()
    for joint in joints_data:
        src, dst = joint["src_rigid"], joint["dest_rigid"]
        if 0 <= src < n_bodies and 0 <= dst < n_bodies:
            joint_pair_set.add(frozenset((src, dst)))

    # Find non-colliding pairs
    non_collision_pairs: set[frozenset] = set()
    pair_table: list[tuple] = []

    for i, rb_a in enumerate(rb_data_list):
        chain_a = rigid_to_chain.get(i)
        mask_a = rb_a["collision_group_mask"]
        for grp in range(16):
            if mask_a & (1 << grp):
                continue
            for j in group_map.get(grp, []):
                if i == j:
                    continue
                pair = frozenset((i, j))
                if pair in non_collision_pairs:
                    continue
                non_collision_pairs.add(pair)

                if pair in joint_pair_set:
                    continue

                chain_b = rigid_to_chain.get(j)
                if chain_a and chain_a in collision_disabled_chains:
                    continue
                if chain_b and chain_b in collision_disabled_chains:
                    continue

                # Skip intra-chain pairs when self-collision is disabled
                if chain_a and chain_a == chain_b and chain_a in self_collision_disabled_chains:
                    continue

                obj_a = rigid_objects[i]
                obj_b = rigid_objects[j]
                if obj_a is not None and obj_b is not None:
                    pair_table.append((obj_a, obj_b))

    return pair_table


def toggle_chain_collisions(armature_obj, chain_index: int, enable: bool) -> str:
    """Toggle collision layers for a physics chain's rigid bodies.

    Disable: collision_collections = [False]*20 (bodies pass through everything).
    Enable: restore shared layer 0 + own group from PMX data.

    Returns the chain name.
    """
    import bpy

    chains_json = armature_obj.get("mmd_physics_chains")
    if not chains_json:
        raise ValueError("No chain data stored on armature")
    chains = json.loads(chains_json)
    if chain_index < 0 or chain_index >= len(chains):
        raise ValueError(f"Chain index {chain_index} out of range")

    chain = chains[chain_index]
    chain_name = chain["name"]
    rigid_indices = set(chain["rigid_indices"])

    # Get PMX data for restoring collision layers
    phys_json = armature_obj.get("mmd_physics_data")
    if not phys_json:
        raise ValueError("No physics data on armature")
    phys_data = json.loads(phys_json)
    rigid_bodies_data = phys_data["rigid_bodies"]

    col_name = armature_obj.get("physics_collection")
    if not col_name:
        raise ValueError("No physics collection found")
    col = bpy.data.collections.get(col_name)
    if not col:
        raise ValueError(f"Collection '{col_name}' not found")

    rb_col = col.children.get("Rigid Bodies")
    if not rb_col:
        return chain_name

    for obj in rb_col.objects:
        idx = obj.get("mmd_rigid_index")
        if idx is None or idx not in rigid_indices:
            continue
        rb = obj.rigid_body
        if not rb:
            continue
        if enable:
            # Restore from PMX data
            rb_data = rigid_bodies_data[idx]
            rigid = _rb_data_to_rigid(rb_data)
            rb.collision_collections = _build_collision_collections(rigid)
        else:
            rb.collision_collections = [False] * 20

    # Update disabled chains list
    disabled = set(json.loads(armature_obj.get("mmd_chain_collision_disabled", "[]")))
    if enable:
        disabled.discard(chain_name)
    else:
        disabled.add(chain_name)
    armature_obj["mmd_chain_collision_disabled"] = json.dumps(sorted(disabled))

    # Clear physics cache
    bpy.context.scene.frame_set(bpy.context.scene.frame_current)

    state = "enabled" if enable else "disabled"
    log.info("Chain '%s' collisions %s", chain_name, state)
    return chain_name


def toggle_chain_physics(armature_obj, chain_index: int, enable: bool) -> str:
    """Toggle physics (kinematic) for a physics chain's rigid bodies.

    Disable: kinematic=True (bodies freeze, follow bone positions).
    Enable: restore kinematic from PMX mode (STATIC=True, DYNAMIC/DYNAMIC_BONE=False).

    Returns the chain name.
    """
    import bpy

    chains_json = armature_obj.get("mmd_physics_chains")
    if not chains_json:
        raise ValueError("No chain data stored on armature")
    chains = json.loads(chains_json)
    if chain_index < 0 or chain_index >= len(chains):
        raise ValueError(f"Chain index {chain_index} out of range")

    chain = chains[chain_index]
    chain_name = chain["name"]
    rigid_indices = set(chain["rigid_indices"])

    # Get PMX data for restoring kinematic state
    phys_json = armature_obj.get("mmd_physics_data")
    if not phys_json:
        raise ValueError("No physics data on armature")
    phys_data = json.loads(phys_json)
    rigid_bodies_data = phys_data["rigid_bodies"]

    col_name = armature_obj.get("physics_collection")
    if not col_name:
        raise ValueError("No physics collection found")
    col = bpy.data.collections.get(col_name)
    if not col:
        raise ValueError(f"Collection '{col_name}' not found")

    rb_col = col.children.get("Rigid Bodies")
    if not rb_col:
        return chain_name

    for obj in rb_col.objects:
        idx = obj.get("mmd_rigid_index")
        if idx is None or idx not in rigid_indices:
            continue
        rb = obj.rigid_body
        if not rb:
            continue
        if enable:
            # Restore from PMX mode: STATIC(0)=True, DYNAMIC(1)/DYNAMIC_BONE(2)=False
            rb.kinematic = (rigid_bodies_data[idx]["mode"] == 0)
        else:
            rb.kinematic = True

    # Update disabled chains list
    disabled = set(json.loads(armature_obj.get("mmd_chain_physics_disabled", "[]")))
    if enable:
        disabled.discard(chain_name)
    else:
        disabled.add(chain_name)
    armature_obj["mmd_chain_physics_disabled"] = json.dumps(sorted(disabled))

    # Clear physics cache
    bpy.context.scene.frame_set(bpy.context.scene.frame_current)

    state = "enabled" if enable else "disabled"
    log.info("Chain '%s' physics %s", chain_name, state)
    return chain_name


def toggle_chain_self_collision(armature_obj, chain_index: int, enable: bool) -> str:
    """Toggle self-collision for a physics chain.

    Self-collision ON (default): intra-chain NCC empties exist, bodies avoid
    each other within the chain.
    Self-collision OFF: intra-chain NCC empties are skipped, bodies can clip
    through each other. Safe for linear chains (hair, ties); fan chains
    (skirt) should keep self-collision on.

    Does NOT auto-rebuild NCCs — caller is responsible for calling rebuild_ncc().

    Returns the chain name.
    """
    chains_json = armature_obj.get("mmd_physics_chains")
    if not chains_json:
        raise ValueError("No chain data stored on armature")
    chains = json.loads(chains_json)
    if chain_index < 0 or chain_index >= len(chains):
        raise ValueError(f"Chain index {chain_index} out of range")

    chain_name = chains[chain_index]["name"]
    disabled = set(json.loads(armature_obj.get("mmd_chain_self_collision_disabled", "[]")))

    if enable:
        disabled.discard(chain_name)
    else:
        disabled.add(chain_name)

    armature_obj["mmd_chain_self_collision_disabled"] = json.dumps(sorted(disabled))
    state = "enabled" if enable else "disabled"
    log.info("Chain '%s' self-collision %s", chain_name, state)
    return chain_name


def get_ncc_count(armature_obj) -> int:
    """Count non-collision constraint empties in the Joints collection."""
    import bpy

    col_name = armature_obj.get("physics_collection")
    if not col_name:
        return 0
    col = bpy.data.collections.get(col_name)
    if not col:
        return 0
    joint_col = col.children.get("Joints")
    if not joint_col:
        return 0

    count = 0
    for obj in joint_col.objects:
        if obj.get("mmd_joint_index") is None and obj.rigid_body_constraint:
            count += 1
    return count


def _rb_data_to_rigid(rb_data: dict) -> RigidBody:
    """Create a minimal RigidBody from serialized data for collision layer computation."""
    return RigidBody(
        name=rb_data["name"],
        name_e=rb_data.get("name_e", ""),
        bone_index=rb_data["bone_index"],
        collision_group_number=rb_data["collision_group_number"],
        collision_group_mask=rb_data["collision_group_mask"],
        shape=RigidShape(rb_data["shape"]),
        size=tuple(rb_data["size"]),
        position=tuple(rb_data["position"]),
        rotation=tuple(rb_data["rotation"]),
        mass=rb_data["mass"],
        linear_damping=rb_data["linear_damping"],
        angular_damping=rb_data["angular_damping"],
        bounce=rb_data["bounce"],
        friction=rb_data["friction"],
        mode=RigidMode(rb_data["mode"]),
    )


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


def _create_rigid_bodies(
    model, armature_obj, scale: float, collection,
    collision_quality: str = "high",
    collision_disabled_chains: set[str] | None = None,
    rigid_to_chain: dict[int, str] | None = None,
) -> list:
    """Create rigid body objects with collision_collections and collision margin."""
    import bpy
    from mathutils import Euler, Vector

    collision_disabled_chains = collision_disabled_chains or set()
    rigid_to_chain = rigid_to_chain or {}
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

        # Collision collections: draft = no collisions, high = shared layer + own group
        # Also disable collisions for bodies in collision-disabled chains
        chain_name = rigid_to_chain.get(i)
        if collision_quality == "draft" or (chain_name and chain_name in collision_disabled_chains):
            rb.collision_collections = [False] * 20
        else:
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


def build_collision_collections(
    rigid: RigidBody, collision_quality: str = "high",
) -> list[bool]:
    """Convert PMX collision group + mask → Blender 20-bool array.

    Public wrapper for testing.

    Args:
        collision_quality: "high" (shared layer 0 + own group) or "draft" (all False).
    """
    if collision_quality == "draft":
        return [False] * 20
    return _build_collision_collections(rigid)


def _build_collision_collections(rigid: RigidBody) -> list[bool]:
    """Convert PMX collision group → Blender 20-bool array.

    All bodies go on layer 0 (shared) so everything potentially collides.
    Each body also gets its own group layer. Non-collision pairs are then
    suppressed via GENERIC constraints with disable_collisions=True
    (see _create_non_collision_constraints).

    Blender's collision_collections uses the SAME bitmask for both Bullet's
    collisionFilterGroup and collisionFilterMask, making it symmetric —
    two bodies collide if they share ANY layer. PMX's model is asymmetric
    (both masks must agree), so we cannot encode PMX masks in Blender layers.
    Instead, we use shared layer 0 + NCC constraint empties for exclusion.
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


def _create_non_collision_constraints(
    model, rigid_objects, joint_objects, collection,
    chain_dicts: list[dict] | None = None,
    collision_disabled_chains: set[str] | None = None,
    rigid_to_chain: dict[int, str] | None = None,
    self_collision_disabled_chains: set[str] | None = None,
) -> None:
    """Apply non-collision settings based on PMX collision_group_mask.

    All joints get disable_collisions=True (adjacent bodies should never
    collide). Non-joint excluded pairs get NCC empties. Collision-disabled
    chains and self-collision-disabled chains skip NCC empties.

    Delegates pair computation to _compute_ncc_pairs (shared with rebuild_ncc).
    """
    import bpy

    # Pass 1: Set disable_collisions=True on ALL joints.
    for j_obj in joint_objects:
        rbc = j_obj.rigid_body_constraint
        if rbc:
            rbc.disable_collisions = True
    log.info("Set disable_collisions on all %d joints", len(joint_objects))

    # Pass 2: Build NCC pair table from model data.
    # Convert model objects to serialized-data format for _compute_ncc_pairs.
    rb_data_list = [
        {
            "collision_group_number": rb.collision_group_number,
            "collision_group_mask": rb.collision_group_mask,
        }
        for rb in model.rigid_bodies
    ]
    joints_data = [
        {"src_rigid": j.src_rigid, "dest_rigid": j.dest_rigid}
        for j in model.joints
    ]

    pair_table = _compute_ncc_pairs(
        rb_data_list, joints_data, rigid_objects,
        collision_disabled_chains=collision_disabled_chains,
        rigid_to_chain=rigid_to_chain,
        self_collision_disabled_chains=self_collision_disabled_chains,
    )

    # Create NCC empties for all non-joint excluded pairs (no proximity filter)
    if pair_table:
        _create_non_collision_empties(bpy, pair_table, collection)


def _create_non_collision_empties(bpy, pair_table: list[tuple], collection) -> None:
    """Create GENERIC constraint empties for non-colliding body pairs.

    Uses template-and-duplicate pattern: create ONE constraint via bpy.ops,
    then duplicate with bpy.ops.object.duplicate() using a doubling strategy
    (O(log N) operator calls instead of O(N)).
    """
    total = len(pair_table)
    if total < 1:
        return

    # Deselect everything
    for obj in bpy.context.selected_objects:
        obj.select_set(False)

    # Create template empty with GENERIC constraint
    template = bpy.data.objects.new("ncc", None)
    template.empty_display_size = 0.001
    template.hide_render = True
    collection.objects.link(template)

    bpy.context.view_layer.objects.active = template
    template.select_set(True)
    bpy.ops.rigidbody.constraint_add(type="GENERIC")
    template.rigid_body_constraint.disable_collisions = True

    # Duplicate using doubling strategy
    all_objs = [template]
    while len(all_objs) < total:
        needed = total - len(all_objs)
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        to_dup = min(needed, len(all_objs))
        for obj in all_objs[:to_dup]:
            obj.select_set(True)
        bpy.ops.object.duplicate()
        new_objs = list(bpy.context.selected_objects)
        all_objs.extend(new_objs)

    # Trim to exact count
    extras = all_objs[total:]
    all_objs = all_objs[:total]
    for obj in extras:
        bpy.data.objects.remove(obj, do_unlink=True)

    # Assign pairs to constraint empties
    for ncc_obj, (obj_a, obj_b) in zip(all_objs, pair_table):
        rbc = ncc_obj.rigid_body_constraint
        rbc.object1 = obj_a
        rbc.object2 = obj_b
        ncc_obj.hide_set(True)

    log.info("Created %d non-collision constraint empties", total)


def _setup_bone_coupling(
    armature_obj, model, rigid_objects: list,
    bone_names: dict[int, str], scale: float, collection,
) -> dict:
    """Wire up bone↔rigid body for STATIC/DYNAMIC/DYNAMIC_BONE modes.

    Returns dict mapping tracking empties → rigid body objects for
    deferred reparenting in _reparent_tracking_empties().
    """
    empty_parent_map: dict = {}

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
            pair = _setup_dynamic_coupling(armature_obj, rb_obj, bone_name, collection)
        else:
            pair = _setup_dynamic_bone_coupling(armature_obj, rb_obj, bone_name, collection)
        empty_parent_map[pair[0]] = pair[1]

    return empty_parent_map


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


def _setup_dynamic_coupling(armature_obj, rb_obj, bone_name: str, collection) -> tuple:
    """DYNAMIC: physics drives bone via tracking empty + COPY_TRANSFORMS.

    Uses COPY_TRANSFORMS (location + rotation) — matching mmd_tools.
    DYNAMIC bodies need full transform from physics, not just rotation.
    Constraint is created muted; unmuted in post-build after reparenting.
    """
    empty = _create_tracking_empty(armature_obj, bone_name, collection)
    pb = armature_obj.pose.bones[bone_name]
    c = pb.constraints.new("COPY_TRANSFORMS")
    c.name = "mmd_dynamic"
    c.target = empty
    c.mute = True
    return (empty, rb_obj)


def _setup_dynamic_bone_coupling(armature_obj, rb_obj, bone_name: str, collection) -> tuple:
    """DYNAMIC_BONE: physics drives bone rotation via tracking empty + COPY_ROTATION.

    Constraint is created muted; unmuted in post-build after reparenting.
    """
    empty = _create_tracking_empty(armature_obj, bone_name, collection)
    pb = armature_obj.pose.bones[bone_name]
    c = pb.constraints.new("COPY_ROTATION")
    c.name = "mmd_dynamic_bone"
    c.target = empty
    c.mute = True
    return (empty, rb_obj)


def _create_tracking_empty(armature_obj, bone_name: str, collection):
    """Create an empty at the bone's world position.

    Sets matrix_world from bone pose. Parenting to the rigid body is
    deferred to _reparent_tracking_empties() (after depsgraph flush)
    to match mmd_tools' two-phase pattern.
    """
    import bpy

    empty = bpy.data.objects.new(f"Track_{bone_name}", None)
    empty.empty_display_size = 0.01
    empty.empty_display_type = "ARROWS"
    collection.objects.link(empty)

    pb = armature_obj.pose.bones[bone_name]
    bone_world = armature_obj.matrix_world @ pb.matrix
    empty.matrix_world = bone_world
    return empty


def _mute_physics_ik_constraints(armature_obj, model, bone_names: dict, mute: bool = True) -> None:
    """Mute or unmute IK constraints on bones linked to DYNAMIC/DYNAMIC_BONE rigid bodies.

    During physics build, IK constraints fight the depsgraph flushes (frame_set)
    by moving bones while we're trying to position rigid bodies at bone locations.
    mmd_tools mutes IK in __preBuild and unmutes after build is complete.
    """
    if not armature_obj.pose:
        return

    for rigid in model.rigid_bodies:
        if rigid.mode not in (RigidMode.DYNAMIC, RigidMode.DYNAMIC_BONE):
            continue
        if rigid.bone_index < 0:
            continue
        bone_name = bone_names.get(rigid.bone_index)
        if not bone_name or bone_name not in armature_obj.pose.bones:
            continue

        pb = armature_obj.pose.bones[bone_name]
        for c in pb.constraints:
            if c.type == "IK":
                c.mute = mute
                c.influence = c.influence  # trigger Blender update

    log.debug("IK constraints %s for physics bones", "muted" if mute else "unmuted")


def _reparent_tracking_empties(empty_parent_map: dict) -> None:
    """Reparent tracking empties to rigid bodies in batch, preserving matrix_world.

    This is the mmd_tools __postBuild pattern: empties are created with correct
    matrix_world, then reparented to their rigid bodies after the depsgraph has
    flushed. Saving and restoring matrix_world through the reparenting ensures
    the empty stays at the bone's world position.
    """
    for empty, rb_obj in empty_parent_map.items():
        world = empty.matrix_world.copy()
        empty.parent = rb_obj
        empty.matrix_world = world

    log.debug("Reparented %d tracking empties to rigid bodies", len(empty_parent_map))


def _unmute_tracking_constraints(armature_obj) -> None:
    """Unmute mmd_dynamic / mmd_dynamic_bone constraints on pose bones.

    Called in post-build after tracking empties are reparented and depsgraph
    has flushed. Matches mmd_tools' __postBuild unmuting pattern.
    """
    if not armature_obj.pose:
        return

    for pb in armature_obj.pose.bones:
        for c in pb.constraints:
            if c.name in ("mmd_dynamic", "mmd_dynamic_bone"):
                c.mute = False

    log.debug("Unmuted tracking constraints")


def _setup_physics_world(scene, scale: float = 0.08) -> None:
    """Configure physics world: substeps and solver iterations."""
    rbw = scene.rigidbody_world
    if rbw is None:
        return

    # Match mmd_tools defaults
    rbw.substeps_per_frame = 6
    rbw.solver_iterations = 10

    # Match cache end to scene frame range
    if rbw.point_cache:
        rbw.point_cache.frame_end = scene.frame_end


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


def inspect_rigid_body(armature_obj, rb_name_or_index) -> str:
    """Generate a diagnostic report for a rigid body.

    Args:
        rb_name_or_index: RB object name (e.g. "RB_042_右HairA22") or PMX index (int).

    Returns multi-line string with physics properties, connections, and warnings.
    """
    import math

    phys_json = armature_obj.get("mmd_physics_data")
    if not phys_json:
        return "No physics data on armature"

    data = json.loads(phys_json)
    rigid_bodies = data["rigid_bodies"]
    joints = data.get("joints", [])

    # Resolve index
    if isinstance(rb_name_or_index, int):
        idx = rb_name_or_index
    elif isinstance(rb_name_or_index, str):
        # Try to extract index from name like "RB_042_name"
        if rb_name_or_index.startswith("RB_"):
            try:
                idx = int(rb_name_or_index[3:6])
            except (ValueError, IndexError):
                idx = -1
        else:
            # Search by PMX name
            idx = next(
                (i for i, rb in enumerate(rigid_bodies) if rb["name"] == rb_name_or_index),
                -1,
            )
    else:
        return f"Invalid input: {rb_name_or_index!r}"

    if idx < 0 or idx >= len(rigid_bodies):
        return f"Rigid body index {idx} out of range (0-{len(rigid_bodies)-1})"

    rb = rigid_bodies[idx]
    mode_names = {0: "STATIC", 1: "DYNAMIC", 2: "DYNAMIC_BONE"}
    shape_names = {0: "SPHERE", 1: "BOX", 2: "CAPSULE"}
    mode_str = mode_names.get(rb["mode"], f"UNKNOWN({rb['mode']})")
    shape_str = shape_names.get(rb["shape"], f"UNKNOWN({rb['shape']})")

    bone_names = _build_bone_name_map(armature_obj)
    bone_name = bone_names.get(rb["bone_index"], f"(index {rb['bone_index']})")

    lines = []
    lines.append(f"=== RB_{idx:03d}_{rb['name']} ===")
    lines.append(f"PMX: {rb['name']} (index {idx}), bone: {bone_name}, mode: {mode_str}")

    # Chain membership
    chains_json = armature_obj.get("mmd_physics_chains")
    if chains_json:
        chains = json.loads(chains_json)
        for chain in chains:
            if idx in chain.get("rigid_indices", []):
                n_bodies = len(chain["rigid_indices"])
                lines.append(f"Chain: {chain['name']} ({chain.get('group', '?')}, {n_bodies} bodies)")
                break

    lines.append("")
    lines.append(
        f"Physics: mass={rb['mass']:.2f}, friction={rb['friction']:.2f}, "
        f"bounce={rb['bounce']:.2f}"
    )
    lines.append(
        f"         linear_damp={rb['linear_damping']:.2f}, "
        f"angular_damp={rb['angular_damping']:.2f}"
    )
    lines.append(f"         shape={shape_str}")

    # Collision info
    grp = rb["collision_group_number"]
    mask = rb["collision_group_mask"]
    collides = [str(g) for g in range(16) if mask & (1 << g)]
    excludes = [str(g) for g in range(16) if not (mask & (1 << g))]
    lines.append("")
    lines.append(
        f"Collision: group={grp}, mask=0b{mask:016b} "
        f"(collides: {','.join(collides) or 'none'}, "
        f"excludes: {','.join(excludes) or 'none'})"
    )

    # Joint connections
    lines.append("")
    lines.append("Joints:")
    has_joints = False
    for j_idx, joint in enumerate(joints):
        if joint["src_rigid"] == idx:
            dest = joint["dest_rigid"]
            dest_name = rigid_bodies[dest]["name"] if 0 <= dest < len(rigid_bodies) else "?"
            _append_joint_line(lines, "→", j_idx, f"RB_{dest:03d}_{dest_name}", joint)
            has_joints = True
        elif joint["dest_rigid"] == idx:
            src = joint["src_rigid"]
            src_name = rigid_bodies[src]["name"] if 0 <= src < len(rigid_bodies) else "?"
            _append_joint_line(lines, "←", j_idx, f"RB_{src:03d}_{src_name}", joint)
            has_joints = True
    if not has_joints:
        lines.append("  (none)")

    # Live position from Blender object
    col_name = armature_obj.get("physics_collection")
    if col_name:
        import bpy
        col = bpy.data.collections.get(col_name)
        if col:
            rb_col = col.children.get("Rigid Bodies")
            if rb_col:
                for obj in rb_col.objects:
                    if obj.get("mmd_rigid_index") == idx:
                        pos = obj.matrix_world.translation
                        lines.append("")
                        lines.append(f"Position: world=({pos.x:.3f}, {pos.y:.3f}, {pos.z:.3f})")
                        break

    # Warnings
    warnings = []
    if rb["mass"] == 0 and rb["mode"] != 0:
        warnings.append("mass=0 on dynamic body")
    if rb["angular_damping"] > 0.95:
        warnings.append(f"angular_damping={rb['angular_damping']:.2f} (very high, rigid feel)")
    if rb["linear_damping"] > 0.95:
        warnings.append(f"linear_damping={rb['linear_damping']:.2f} (very high, frozen feel)")
    if mask == 0:
        warnings.append("collision mask is empty (collides with nothing)")

    if warnings:
        lines.append("")
        for w in warnings:
            lines.append(f"⚠ {w}")

    return "\n".join(lines)


def _append_joint_line(lines: list, arrow: str, j_idx: int, target: str, joint: dict) -> None:
    """Append a formatted joint info line to the report."""
    import math

    lo_m = joint["limit_move_lower"]
    hi_m = joint["limit_move_upper"]
    lo_r = joint["limit_rotate_lower"]
    hi_r = joint["limit_rotate_upper"]
    sp_m = joint["spring_constant_move"]
    sp_r = joint["spring_constant_rotate"]

    def _fmt_range(lo, hi):
        return f"±{abs(hi - lo) / 2:.2f}"

    def _fmt_deg(lo, hi):
        return f"±{math.degrees(abs(hi - lo) / 2):.1f}°"

    move = ",".join(_fmt_range(lo_m[i], hi_m[i]) for i in range(3))
    rot = ",".join(_fmt_deg(lo_r[i], hi_r[i]) for i in range(3))
    sm = ",".join(f"{sp_m[i]:.0f}" for i in range(3))
    sr = ",".join(f"{sp_r[i]:.0f}" for i in range(3))

    lines.append(
        f"  {arrow} J_{j_idx:03d} to {target}: "
        f"move({move}) rot({rot}) spring_m({sm}) spring_r({sr})"
    )


def get_collision_eligible_indices(armature_obj, rb_index: int) -> set[int]:
    """Return indices of rigid bodies that can collide with rb_index per PMX masks.

    PMX collision: A and B collide iff A.mask has B.group bit AND B.mask has A.group bit.
    """
    phys_json = armature_obj.get("mmd_physics_data")
    if not phys_json:
        return set()

    data = json.loads(phys_json)
    rigid_bodies = data["rigid_bodies"]
    if rb_index < 0 or rb_index >= len(rigid_bodies):
        return set()

    rb = rigid_bodies[rb_index]
    result = set()
    for i, other in enumerate(rigid_bodies):
        if i == rb_index:
            continue
        # Both masks must agree for collision
        a_hits_b = rb["collision_group_mask"] & (1 << other["collision_group_number"])
        b_hits_a = other["collision_group_mask"] & (1 << rb["collision_group_number"])
        if a_hits_b and b_hits_a:
            result.add(i)
    return result
