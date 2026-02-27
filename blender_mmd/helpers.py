"""Introspection and query helpers for Claude Code via blender-agent."""

from __future__ import annotations

import bpy


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
