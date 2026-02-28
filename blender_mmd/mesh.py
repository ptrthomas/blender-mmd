"""Mesh creation from parsed PMX data."""

from __future__ import annotations

import logging

import bpy
import bmesh
from mathutils import Vector

from .pmx.types import (
    BoneWeightBDEF1,
    BoneWeightBDEF2,
    BoneWeightBDEF4,
    BoneWeightQDEF,
    BoneWeightSDEF,
    Model,
    MorphType,
    VertexMorphOffset,
)

log = logging.getLogger("blender_mmd")


def create_mesh(
    model: Model,
    armature_obj: bpy.types.Object,
    scale: float,
) -> bpy.types.Object:
    """Create a Blender mesh from parsed PMX vertex/face data.

    The mesh is parented to the armature with an Armature modifier.
    Vertex groups are created for bone weights.
    """
    pmx_verts = model.vertices
    pmx_faces = model.faces
    pmx_bones = model.bones

    # Build bone name list (must match armature creation)
    from .armature import _resolve_bone_name, _ensure_unique_names
    bone_names = _ensure_unique_names(pmx_bones)

    mesh_name = (model.name_e if model.name_e else model.name) + "_mesh"
    mesh_data = bpy.data.meshes.new(mesh_name)
    mesh_obj = bpy.data.objects.new(mesh_name, mesh_data)

    bpy.context.collection.objects.link(mesh_obj)

    # --- Build geometry ---
    vertices = [Vector(v.position) * scale for v in pmx_verts]
    mesh_data.from_pydata(vertices, [], pmx_faces)
    mesh_data.update()

    # --- Vertex groups (bone weights) ---
    # Pre-create all vertex groups
    for name in bone_names:
        mesh_obj.vertex_groups.new(name=name)

    # Assign weights
    for vi, pmx_v in enumerate(pmx_verts):
        w = pmx_v.weight
        if isinstance(w, BoneWeightBDEF1):
            if 0 <= w.bone < len(bone_names):
                vg = mesh_obj.vertex_groups[bone_names[w.bone]]
                vg.add([vi], 1.0, "REPLACE")
        elif isinstance(w, BoneWeightBDEF2):
            if 0 <= w.bone1 < len(bone_names) and w.weight > 0:
                mesh_obj.vertex_groups[bone_names[w.bone1]].add(
                    [vi], w.weight, "REPLACE"
                )
            if 0 <= w.bone2 < len(bone_names) and (1.0 - w.weight) > 0:
                mesh_obj.vertex_groups[bone_names[w.bone2]].add(
                    [vi], 1.0 - w.weight, "REPLACE"
                )
        elif isinstance(w, (BoneWeightBDEF4, BoneWeightQDEF)):
            for bone_idx, weight in zip(w.bones, w.weights):
                if 0 <= bone_idx < len(bone_names) and weight > 0:
                    mesh_obj.vertex_groups[bone_names[bone_idx]].add(
                        [vi], weight, "REPLACE"
                    )
        elif isinstance(w, BoneWeightSDEF):
            # SDEF uses same weight assignment as BDEF2
            if 0 <= w.bone1 < len(bone_names) and w.weight > 0:
                mesh_obj.vertex_groups[bone_names[w.bone1]].add(
                    [vi], w.weight, "REPLACE"
                )
            if 0 <= w.bone2 < len(bone_names) and (1.0 - w.weight) > 0:
                mesh_obj.vertex_groups[bone_names[w.bone2]].add(
                    [vi], 1.0 - w.weight, "REPLACE"
                )

    # --- UV coordinates ---
    # PMX uses DirectX convention (V=0 at top), Blender uses OpenGL (V=0 at bottom)
    uv_layer = mesh_data.uv_layers.new(name="UV")
    for face in mesh_data.polygons:
        for li in face.loop_indices:
            loop = mesh_data.loops[li]
            vi = loop.vertex_index
            u, v = pmx_verts[vi].uv
            uv_layer.data[li].uv = (u, 1.0 - v)

    # Additional UV layers
    for uv_idx in range(model.header.additional_uv_count):
        extra_uv = mesh_data.uv_layers.new(name=f"UV{uv_idx + 1}")
        for face in mesh_data.polygons:
            for li in face.loop_indices:
                loop = mesh_data.loops[li]
                vi = loop.vertex_index
                if uv_idx < len(pmx_verts[vi].additional_uvs):
                    auv = pmx_verts[vi].additional_uvs[uv_idx]
                    extra_uv.data[li].uv = (auv[0], 1.0 - auv[1])

    # --- Smooth shading on all faces ---
    mesh_data.polygons.foreach_set(
        "use_smooth", (True,) * len(mesh_data.polygons)
    )

    # --- Mark sharp edges before custom normals ---
    # normals_split_custom_set requires sharp edges to be marked first.
    # 179° threshold preserves nearly all custom normals (180° misses some).
    # Matches mmd_tools' __assignCustomNormals pattern.
    import math
    bpy.context.view_layer.objects.active = mesh_obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.mesh.edges_select_sharp(sharpness=math.radians(179))
    bpy.ops.mesh.mark_sharp()
    bpy.ops.object.mode_set(mode="OBJECT")

    # --- Custom split normals ---
    normals = [pmx_verts[v.vertex_index].normal for v in mesh_data.loops]
    mesh_data.normals_split_custom_set(normals)

    # --- Shape keys (vertex morphs) ---
    _create_shape_keys(model, mesh_obj, scale)

    # --- Parent to armature ---
    mesh_obj.parent = armature_obj
    mod = mesh_obj.modifiers.new(name="Armature", type="ARMATURE")
    mod.object = armature_obj

    log.info(
        "Created mesh '%s': %d verts, %d faces, %d vertex groups, %d UV layers",
        mesh_name, len(pmx_verts), len(pmx_faces),
        len(mesh_obj.vertex_groups),
        len(mesh_data.uv_layers),
    )
    return mesh_obj


def _resolve_morph_name(morph: "Morph") -> str:
    """Choose English shape key name for a morph."""
    from .translations import resolve_morph_name
    return resolve_morph_name(morph.name, morph.name_e)


def _create_shape_keys(
    model: Model,
    mesh_obj: bpy.types.Object,
    scale: float,
) -> None:
    """Create Blender shape keys from PMX vertex morphs.

    Only vertex morphs become shape keys. Bone, material, UV, and group
    morphs are stored as metadata for later milestones (VMD import).

    Shape keys are named in English where possible. A mapping from
    Japanese name → shape key name is stored on the mesh object as
    ``mmd_morph_map`` for VMD import to find shape keys by Japanese name.
    """
    vertex_morphs = [
        m for m in model.morphs if m.morph_type == MorphType.VERTEX
    ]
    if not vertex_morphs:
        return

    # Create basis shape key
    basis = mesh_obj.shape_key_add(name="Basis", from_mix=False)
    basis.value = 0.0

    # Get basis coordinates (flat array: x0,y0,z0, x1,y1,z1, ...)
    n_verts = len(mesh_obj.data.vertices)
    basis_coords = [0.0] * (n_verts * 3)
    basis.data.foreach_get("co", basis_coords)

    # Track Japanese → shape key name mapping for VMD import
    morph_map: dict[str, str] = {}
    # Track used names to avoid duplicates
    used_names: set[str] = {"Basis"}

    for morph in vertex_morphs:
        name = _resolve_morph_name(morph)
        # Ensure unique name
        if name in used_names:
            suffix = 1
            while f"{name}.{suffix:03d}" in used_names:
                suffix += 1
            name = f"{name}.{suffix:03d}"
        used_names.add(name)

        sk = mesh_obj.shape_key_add(name=name, from_mix=False)
        sk.value = 0.0
        morph_map[morph.name] = sk.name  # Blender may rename on collision

        # Copy basis coords then apply offsets
        coords = basis_coords.copy()
        for offset in morph.offsets:
            vi = offset.vertex_index
            if 0 <= vi < n_verts:
                base = vi * 3
                coords[base] += offset.offset[0] * scale
                coords[base + 1] += offset.offset[1] * scale
                coords[base + 2] += offset.offset[2] * scale

        sk.data.foreach_set("co", coords)

    # Store mapping on mesh object for VMD import
    import json
    mesh_obj["mmd_morph_map"] = json.dumps(morph_map, ensure_ascii=False)

    translated = sum(1 for m in vertex_morphs if _resolve_morph_name(m) != m.name)
    log.info(
        "Created %d shape keys from vertex morphs (%d translated)",
        len(vertex_morphs), translated,
    )
