"""PMX import orchestrator — parse, build armature, build mesh."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

import bpy

from .pmx.types import Model, RigidMode
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


def _split_mesh_by_material(
    mesh_obj: bpy.types.Object,
    armature_obj: bpy.types.Object,
    model: Model,
) -> list[bpy.types.Object]:
    """Split a single mesh into per-material mesh objects.

    1. Move mmd_morph_map from mesh to armature (for VMD import after split)
    2. Back up custom split normals as mesh attribute (separate destroys them)
    3. Split by material using bpy.ops.mesh.separate
    4. Restore normals on each resulting mesh
    5. Name each mesh after its first material
    6. Set visible_shadow based on mmd_drop_shadow flags
    7. Organize into a collection named after the model

    Returns the list of split mesh objects.
    """
    # 1. Move mmd_morph_map to armature so VMD import can find it
    morph_map_json = mesh_obj.get("mmd_morph_map")
    if morph_map_json:
        armature_obj["mmd_morph_map"] = morph_map_json
        del mesh_obj["mmd_morph_map"]

    # 2. Back up loop normals as a mesh attribute (separate destroys custom normals)
    mesh_data = mesh_obj.data
    n_loops = len(mesh_data.loops)
    if n_loops > 0:
        attr = mesh_data.attributes.new("mmd_normal", "FLOAT_VECTOR", "CORNER")
        normals = np.empty(n_loops * 3, dtype=np.float32)
        mesh_data.loops.foreach_get("normal", normals)
        attr.data.foreach_set("vector", normals)

    # 3. Split by material
    bpy.ops.object.select_all(action="DESELECT")
    mesh_obj.select_set(True)
    bpy.context.view_layer.objects.active = mesh_obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.separate(type="MATERIAL")
    bpy.ops.object.mode_set(mode="OBJECT")

    # 4. Collect all mesh children of the armature
    split_meshes = [c for c in armature_obj.children if c.type == "MESH"]

    # 5. Restore normals, name meshes, set visible_shadow
    for obj in split_meshes:
        md = obj.data
        attr = md.attributes.get("mmd_normal")
        if attr:
            n = len(md.loops)
            norms = np.empty(n * 3, dtype=np.float32)
            attr.data.foreach_get("vector", norms)
            md.normals_split_custom_set(norms.reshape(-1, 3).tolist())
            md.attributes.remove(attr)

        # Name after first material
        if md.materials and md.materials[0]:
            obj.name = md.materials[0].name
            md.name = md.materials[0].name

        # Set visible_shadow = False if ALL materials have mmd_drop_shadow == False
        if md.materials:
            all_no_shadow = all(
                not mat.get("mmd_drop_shadow", True)
                for mat in md.materials if mat
            )
            if all_no_shadow:
                obj.visible_shadow = False

    # 6. Organize into collection
    model_name = model.name_e if model.name_e else model.name
    collection = bpy.data.collections.new(model_name)
    bpy.context.scene.collection.children.link(collection)

    # Link armature and all meshes into new collection, unlink from old collections
    for obj in [armature_obj] + split_meshes:
        # Unlink from all current collections
        for old_col in list(obj.users_collection):
            old_col.objects.unlink(obj)
        collection.objects.link(obj)

    log.info(
        "Split mesh into %d objects, organized in collection '%s'",
        len(split_meshes),
        collection.name,
    )
    return split_meshes


def import_pmx(
    filepath: str,
    scale: float = DEFAULT_SCALE,
    *,
    use_toon_sphere: bool = False,
    split_by_material: bool = True,
) -> bpy.types.Object:
    """Import a PMX file into the current scene.

    Args:
        filepath: Path to .pmx file.
        scale: Import scale factor.
        use_toon_sphere: Include toon and sphere texture nodes in materials.
        split_by_material: Split mesh into per-material objects (default True).

    Returns the armature object.
    """
    filepath = str(Path(filepath).resolve())
    log.info("Importing: %s (scale=%.4f)", filepath, scale)

    # Parse — auto-detect format by extension
    ext = Path(filepath).suffix.lower()
    if ext == ".pmd":
        from .pmd import parse as pmd_parse
        model = pmd_parse(filepath)
    else:
        from .pmx import parse as pmx_parse
        model = pmx_parse(filepath)

    # Deselect everything
    bpy.ops.object.select_all(action="DESELECT")

    # Build armature
    armature_obj = create_armature(model, scale)

    # Build mesh
    mesh_obj = create_mesh(model, armature_obj, scale)

    # Create materials and assign to faces (pass armature for driver setup)
    create_materials(model, mesh_obj, filepath, armature_obj=armature_obj, use_toon_sphere=use_toon_sphere)

    # Split mesh by material (after materials assigned, before drivers)
    if split_by_material and len(model.materials) > 1:
        _split_mesh_by_material(mesh_obj, armature_obj, model)

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
