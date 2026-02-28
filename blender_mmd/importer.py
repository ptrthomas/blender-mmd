"""PMX import orchestrator — parse, build armature, build mesh."""

from __future__ import annotations

import logging
from pathlib import Path

import bpy

from .pmx import parse
from .pmx.types import RigidMode
from .armature import create_armature
from .mesh import create_mesh
from .materials import create_materials, setup_drivers

log = logging.getLogger("blender_mmd")

DEFAULT_SCALE = 0.08


def _setup_bone_collections(armature_obj, model) -> None:
    """Create bone collections and color-code physics bones.

    Creates "Armature" (standard bones) and "Physics" (dynamic rigid body bones)
    collections. Physics bones get orange custom color for easy identification.
    """
    arm_data = armature_obj.data

    # Build bone_id → bone name map
    bone_map: dict[int, str] = {}
    for bone in arm_data.bones:
        idx = bone.get("bone_id")
        if idx is not None:
            bone_map[idx] = bone.name

    # Identify dynamic rigid body bones
    dynamic_bones: set[str] = set()
    for rb in model.rigid_bodies:
        if rb.bone_index < 0:
            continue
        bone_name = bone_map.get(rb.bone_index)
        if bone_name and rb.mode in (RigidMode.DYNAMIC, RigidMode.DYNAMIC_BONE):
            dynamic_bones.add(bone_name)

    # Create collections in edit mode
    bpy.context.view_layer.objects.active = armature_obj
    bpy.ops.object.mode_set(mode="EDIT")

    armature_coll = arm_data.collections.get("Armature")
    if not armature_coll:
        armature_coll = arm_data.collections.new("Armature")
    physics_coll = arm_data.collections.get("Physics")
    if not physics_coll:
        physics_coll = arm_data.collections.new("Physics")

    shadow_coll = arm_data.collections.get("mmd_shadow")

    for ebone in arm_data.edit_bones:
        # Skip shadow/dummy bones — they stay in mmd_shadow only
        if shadow_coll and shadow_coll in ebone.collections.values():
            continue
        if ebone.name in dynamic_bones:
            physics_coll.assign(ebone)
        else:
            armature_coll.assign(ebone)

    bpy.ops.object.mode_set(mode="OBJECT")

    # Color physics bones orange
    for bone in arm_data.bones:
        if bone.name in dynamic_bones:
            bone.color.palette = "CUSTOM"
            bone.color.custom.normal = (0.9, 0.3, 0.0)
            bone.color.custom.select = (1.0, 0.6, 0.0)
            bone.color.custom.active = (1.0, 0.8, 0.2)

    # Hide all bone collections by default (unhide from outliner when needed)
    armature_coll.is_visible = False
    physics_coll.is_visible = False

    # Enable bone colors and use STICK display
    arm_data.show_bone_colors = True
    arm_data.display_type = "STICK"

    log.info(
        "Bone collections: %d armature, %d physics, %d shadow",
        len([b for b in arm_data.bones if armature_coll in b.collections.values()]),
        len(dynamic_bones),
        len([b for b in arm_data.bones if shadow_coll and shadow_coll in b.collections.values()]),
    )


def import_pmx(
    filepath: str, scale: float = DEFAULT_SCALE
) -> bpy.types.Object:
    """Import a PMX file into the current scene.

    Returns the armature object.
    """
    filepath = str(Path(filepath).resolve())
    log.info("Importing PMX: %s (scale=%.4f)", filepath, scale)

    # Parse
    model = parse(filepath)

    # Deselect everything
    bpy.ops.object.select_all(action="DESELECT")

    # Build armature
    armature_obj = create_armature(model, scale)

    # Build mesh
    mesh_obj = create_mesh(model, armature_obj, scale)

    # Create materials and assign to faces (pass armature for driver setup)
    create_materials(model, mesh_obj, filepath, armature_obj=armature_obj)

    # Store filepath for deferred physics build
    armature_obj["pmx_filepath"] = filepath

    # Set up bone collections and physics bone coloring
    _setup_bone_collections(armature_obj, model)

    # Armature display settings
    armature_obj.show_in_front = False

    # Select armature as active
    bpy.context.view_layer.objects.active = armature_obj
    armature_obj.select_set(True)

    # Set up material drivers (must be after full scene registration)
    bpy.context.view_layer.update()
    setup_drivers(armature_obj)

    log.info(
        "Import complete: '%s' — %d bones, %d vertices",
        armature_obj.name,
        len(model.bones),
        len(model.vertices),
    )
    return armature_obj
