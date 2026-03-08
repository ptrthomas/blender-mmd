"""PMX import orchestrator — parse, build armature, build meshes."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

import bpy

from .pmx.types import Model, RigidMode
from .armature import create_armature
from .mesh import create_meshes
from .materials import create_materials, setup_drivers

log = logging.getLogger("blender_mmd")

DEFAULT_SCALE = 0.08


def _log_import_report(armature_obj: bpy.types.Object) -> None:
    """Log untranslated names to a Blender Text Editor datablock."""
    from .translations import _looks_english
    from .mesh import is_control_mesh

    # Bones
    bone_jp = []
    for bone in armature_obj.data.bones:
        if not _looks_english(bone.name):
            bone_jp.append(bone.name)

    # Shape keys (morphs) — from control mesh
    morph_jp = []
    n_morphs = 0
    for child in armature_obj.children:
        if child.type == "MESH" and is_control_mesh(child) and child.data.shape_keys:
            for kb in child.data.shape_keys.key_blocks:
                if kb.name == "Basis":
                    continue
                n_morphs += 1
                if not _looks_english(kb.name):
                    morph_jp.append(kb.name)
            break

    # Materials (deduplicate across split meshes)
    seen_mats: set[str] = set()
    mat_jp = []
    for child in armature_obj.children:
        if child.type == "MESH" and not is_control_mesh(child):
            for mat in child.data.materials:
                if mat and mat.name not in seen_mats:
                    seen_mats.add(mat.name)
                    if not _looks_english(mat.name):
                        mat_jp.append(mat.name)
    n_mats = len(seen_mats)
    n_bones = len(armature_obj.data.bones)

    lines = [f"=== Import Report: {armature_obj.name} ===", ""]
    lines.append(f"Bones: {n_bones - len(bone_jp)}/{n_bones} translated")
    if bone_jp:
        for name in bone_jp:
            lines.append(f"  {name}")
    lines.append("")
    lines.append(f"Morphs: {n_morphs - len(morph_jp)}/{n_morphs} translated")
    if morph_jp:
        for name in morph_jp:
            lines.append(f"  {name}")
    lines.append("")
    lines.append(f"Materials: {n_mats - len(mat_jp)}/{n_mats} translated")
    if mat_jp:
        for name in mat_jp:
            lines.append(f"  {name}")

    report_text = "\n".join(lines)
    _write_report_text(report_text)

    log.info(
        "Import report: bones %d/%d, morphs %d/%d, materials %d/%d translated"
        " (see 'MMD Import Report' in Text Editor)",
        n_bones - len(bone_jp), n_bones,
        n_morphs - len(morph_jp), n_morphs,
        n_mats - len(mat_jp), n_mats,
    )


def _write_report_text(text: str, name: str = "MMD Import Report") -> None:
    """Write text to a Blender Text datablock, creating or replacing it."""
    txt = bpy.data.texts.get(name)
    if txt:
        txt.clear()
    else:
        txt = bpy.data.texts.new(name)
    txt.write(text)


def _filter_degenerate_faces(model: Model) -> None:
    """Remove degenerate faces (duplicate vertex indices) from model data."""
    faces = model.faces
    materials = model.materials

    mat_face_counts = []
    for mat in materials:
        mat_face_counts.append(mat.face_count // 3)

    clean_faces = []
    clean_mat_counts = [0] * len(materials)
    face_idx = 0
    for mat_idx, n_faces in enumerate(mat_face_counts):
        for _ in range(n_faces):
            if face_idx < len(faces):
                f = faces[face_idx]
                if f[0] != f[1] and f[1] != f[2] and f[0] != f[2]:
                    clean_faces.append(f)
                    clean_mat_counts[mat_idx] += 1
                face_idx += 1

    removed = len(faces) - len(clean_faces)
    if removed > 0:
        log.warning(
            "Removed %d degenerate faces (duplicate vertex indices)", removed
        )
        model.faces = clean_faces
        for i, mat in enumerate(materials):
            mat.face_count = clean_mat_counts[i] * 3


def _setup_bone_collections(armature_obj, model) -> None:
    """Create bone collections and color-code physics bones."""
    arm_data = armature_obj.data

    bone_map: dict[int, str] = {}
    for bone in arm_data.bones:
        idx = bone.get("bone_id")
        if idx is not None:
            bone_map[idx] = bone.name

    dynamic_bones: set[str] = set()
    for rb in model.rigid_bodies:
        if rb.bone_index < 0:
            continue
        bone_name = bone_map.get(rb.bone_index)
        if bone_name and rb.mode in (RigidMode.DYNAMIC, RigidMode.DYNAMIC_BONE):
            dynamic_bones.add(bone_name)

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
        if shadow_coll and shadow_coll in ebone.collections.values():
            continue
        if ebone.name in dynamic_bones:
            physics_coll.assign(ebone)
        else:
            armature_coll.assign(ebone)

    bpy.ops.object.mode_set(mode="OBJECT")

    for bone in arm_data.bones:
        if bone.name in dynamic_bones:
            bone.color.palette = "CUSTOM"
            bone.color.custom.normal = (0.9, 0.3, 0.0)
            bone.color.custom.select = (1.0, 0.6, 0.0)
            bone.color.custom.active = (1.0, 0.8, 0.2)

    armature_coll.is_visible = False
    physics_coll.is_visible = False

    arm_data.show_bone_colors = True
    arm_data.display_type = "STICK"

    log.info(
        "Bone collections: %d armature, %d physics, %d shadow",
        len([b for b in arm_data.bones if armature_coll in b.collections.values()]),
        len(dynamic_bones),
        len([b for b in arm_data.bones if shadow_coll and shadow_coll in b.collections.values()]),
    )


def import_pmx(
    filepath: str,
    scale: float = DEFAULT_SCALE,
    *,
    use_toon_sphere: bool = False,
    split_by_material: bool = True,
) -> bpy.types.Object:
    """Import a PMX file into the current scene.

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

    # Remove degenerate faces before building geometry
    _filter_degenerate_faces(model)

    # Deselect everything
    bpy.ops.object.select_all(action="DESELECT")

    # Build armature
    armature_obj = create_armature(model, scale)

    # Reset SDEF count before mesh creation (meshes accumulate it)
    armature_obj["mmd_sdef_count"] = 0

    # Build meshes (per-material + control mesh, or single mesh)
    mesh_objects = create_meshes(
        model, armature_obj, scale,
        split_by_material=split_by_material,
    )

    # Create materials and assign to mesh faces
    from .mesh import is_control_mesh
    for i, mesh_obj in enumerate(mesh_objects):
        if is_control_mesh(mesh_obj):
            continue
        if split_by_material and len(model.materials) > 1:
            # Each mesh gets one material
            mat_idx = i
            if mat_idx < len(model.materials):
                create_materials(
                    model, mesh_obj, filepath,
                    armature_obj=armature_obj,
                    use_toon_sphere=use_toon_sphere,
                    single_mat_index=mat_idx,
                    use_diffuse_shader=bool(mesh_obj.get("_mmd_overlap")),
                )
                if "_mmd_overlap" in mesh_obj:
                    del mesh_obj["_mmd_overlap"]
        else:
            # Single mesh gets all materials
            create_materials(
                model, mesh_obj, filepath,
                armature_obj=armature_obj,
                use_toon_sphere=use_toon_sphere,
            )

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
        "Import complete: '%s' — %d bones, %d vertices, %d meshes",
        armature_obj.name,
        len(model.bones),
        len(model.vertices),
        len(mesh_objects),
    )

    _log_import_report(armature_obj)

    return armature_obj
