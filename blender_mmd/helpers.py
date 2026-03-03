"""Introspection and query helpers for Claude Code via blender-agent."""

from __future__ import annotations

import bpy


# ---------------------------------------------------------------------------
# Shared armature lookup (used by operators.py, panels.py, and helpers below)
# ---------------------------------------------------------------------------


def is_mmd_armature(obj) -> bool:
    """Check if an object is a blender_mmd-imported armature."""
    return (
        obj is not None
        and obj.type == "ARMATURE"
        and obj.get("import_scale") is not None
    )


def find_mmd_armature(context) -> bpy.types.Object | None:
    """Find the relevant MMD armature from context.

    Checks: active object → active object's parent (mesh child) → single armature in scene.
    """
    obj = context.active_object
    if obj is not None:
        if is_mmd_armature(obj):
            return obj
        if obj.parent and is_mmd_armature(obj.parent):
            return obj.parent
    # Auto-detect: single MMD armature in scene
    candidates = [o for o in context.scene.objects if is_mmd_armature(o)]
    return candidates[0] if len(candidates) == 1 else None


def get_model_info(armature_name: str | None = None) -> dict:
    """Return summary info about an imported MMD model."""
    if armature_name:
        arm_obj = bpy.data.objects.get(armature_name)
    else:
        arm_obj = bpy.context.active_object
        if arm_obj and arm_obj.type != "ARMATURE":
            arm_obj = None

    if not arm_obj or arm_obj.type != "ARMATURE":
        return {"error": "No armature found"}

    arm = arm_obj.data
    mesh_children = [c for c in arm_obj.children if c.type == "MESH"]

    info = {
        "name": arm_obj.name,
        "pmx_name": arm_obj.get("pmx_name", ""),
        "bone_count": len(arm.bones),
        "mesh_count": len(mesh_children),
    }

    for mesh_obj in mesh_children:
        m = mesh_obj.data
        info[f"mesh:{mesh_obj.name}"] = {
            "vertices": len(m.vertices),
            "polygons": len(m.polygons),
            "vertex_groups": len(mesh_obj.vertex_groups),
        }

    return info


def get_selected_bones() -> list[dict]:
    """Return info about currently selected pose bones."""
    obj = bpy.context.active_object
    if not obj or obj.type != "ARMATURE":
        return []

    results = []
    for pb in bpy.context.selected_pose_bones or []:
        bone = pb.bone
        info = {
            "name": bone.name,
            "mmd_name_j": bone.get("mmd_name_j", ""),
            "bone_id": bone.get("bone_id", -1),
            "head": list(pb.head),
            "tail": list(pb.tail),
        }
        results.append(info)
    return results


def get_ik_chains() -> list[dict]:
    """Return IK chain information from the active armature."""
    obj = bpy.context.active_object
    if not obj or obj.type != "ARMATURE":
        return []

    chains = []
    for pb in obj.pose.bones:
        for c in pb.constraints:
            if c.type == "IK":
                chains.append({
                    "bone": pb.name,
                    "target": c.target.name if c.target else None,
                    "subtarget": c.subtarget,
                    "chain_count": c.chain_count,
                    "iterations": c.iterations,
                })
    return chains


def get_physics_mode(armature_name: str | None = None) -> str | None:
    """Return the current physics mode for an MMD armature."""
    obj = _get_armature(armature_name)
    if not obj:
        return None
    return obj.get("physics_mode")


def get_physics_chains(armature_name: str | None = None) -> list[dict] | None:
    """Return detected physics chains from armature metadata."""
    import json
    obj = _get_armature(armature_name)
    if not obj:
        return None
    chains_json = obj.get("mmd_physics_chains")
    if not chains_json:
        return None
    return json.loads(chains_json)


def _get_armature(armature_name: str | None = None):
    """Get armature object by name or active object."""
    if armature_name:
        arm_obj = bpy.data.objects.get(armature_name)
    else:
        arm_obj = bpy.context.active_object
        if arm_obj and arm_obj.type != "ARMATURE":
            arm_obj = None
    if not arm_obj or arm_obj.type != "ARMATURE":
        return None
    return arm_obj


def get_physics_objects() -> dict:
    """Return rigid body and joint objects related to the active armature."""
    rigid_bodies = []
    joints = []
    for obj in bpy.data.objects:
        if obj.rigid_body:
            rigid_bodies.append({
                "name": obj.name,
                "type": obj.rigid_body.type,
                "shape": obj.rigid_body.collision_shape,
                "mass": obj.rigid_body.mass,
                "kinematic": obj.rigid_body.kinematic,
            })
        if obj.rigid_body_constraint:
            joints.append({
                "name": obj.name,
                "type": obj.rigid_body_constraint.type,
                "object1": obj.rigid_body_constraint.object1.name
                if obj.rigid_body_constraint.object1 else None,
                "object2": obj.rigid_body_constraint.object2.name
                if obj.rigid_body_constraint.object2 else None,
            })
    return {"rigid_bodies": rigid_bodies, "joints": joints}


def find_selected_mesh(context) -> bpy.types.Object | None:
    """Return active object if it's a mesh child of an MMD armature."""
    obj = context.active_object
    if obj and obj.type == "MESH" and obj.parent and is_mmd_armature(obj.parent):
        return obj
    return None


def get_mesh_sdef_count(mesh_obj) -> int:
    """Count vertices in the mmd_sdef vertex group."""
    vg = mesh_obj.vertex_groups.get("mmd_sdef")
    if not vg:
        return 0
    count = 0
    vg_idx = vg.index
    for v in mesh_obj.data.vertices:
        for g in v.groups:
            if g.group == vg_idx and g.weight > 0:
                count += 1
                break
    return count


def get_mesh_physics_chains(mesh_obj, armature_obj) -> list[dict]:
    """Return physics chains that affect bones weighted on this mesh.

    A chain affects a mesh if any of its rigid bodies are attached to
    bones that have non-empty vertex groups on the mesh.
    """
    import json

    chains_json = armature_obj.get("mmd_physics_chains")
    phys_json = armature_obj.get("mmd_physics_data")
    if not chains_json or not phys_json:
        return []

    # Build set of bone names that have vertex groups on this mesh
    mesh_bone_names = set()
    arm_data = armature_obj.data
    for vg in mesh_obj.vertex_groups:
        if arm_data.bones.get(vg.name):
            mesh_bone_names.add(vg.name)

    if not mesh_bone_names:
        return []

    # Build bone_index → bone_name map from armature
    bone_idx_to_name = {}
    for bone in arm_data.bones:
        idx = bone.get("bone_id")
        if idx is not None:
            bone_idx_to_name[idx] = bone.name

    # Build rigid_index → bone_index map from physics data
    phys_data = json.loads(phys_json)
    rigid_to_bone_idx = {}
    for i, rb in enumerate(phys_data.get("rigid_bodies", [])):
        rigid_to_bone_idx[i] = rb.get("bone_index", -1)

    # Check each chain
    chains = json.loads(chains_json)
    matching = []
    for chain in chains:
        for ri in chain.get("rigid_indices", []):
            bone_idx = rigid_to_bone_idx.get(ri, -1)
            bone_name = bone_idx_to_name.get(bone_idx)
            if bone_name and bone_name in mesh_bone_names:
                matching.append(chain)
                break

    return matching


def select_bones_by_name(names: list[str]) -> int:
    """Select pose bones by name. Returns count of bones selected."""
    obj = bpy.context.active_object
    if not obj or obj.type != "ARMATURE":
        return 0

    bpy.ops.object.mode_set(mode="POSE")
    bpy.ops.pose.select_all(action="DESELECT")
    count = 0
    for name in names:
        pb = obj.pose.bones.get(name)
        if pb:
            pb.bone.select = True
            count += 1
    return count
