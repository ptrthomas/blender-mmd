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
    uv_layer = mesh_data.uv_layers.new(name="UV")
    for face in mesh_data.polygons:
        for li in face.loop_indices:
            loop = mesh_data.loops[li]
            vi = loop.vertex_index
            uv_layer.data[li].uv = pmx_verts[vi].uv

    # Additional UV layers
    for uv_idx in range(model.header.additional_uv_count):
        extra_uv = mesh_data.uv_layers.new(name=f"UV{uv_idx + 1}")
        for face in mesh_data.polygons:
            for li in face.loop_indices:
                loop = mesh_data.loops[li]
                vi = loop.vertex_index
                if uv_idx < len(pmx_verts[vi].additional_uvs):
                    auv = pmx_verts[vi].additional_uvs[uv_idx]
                    extra_uv.data[li].uv = (auv[0], auv[1])

    # --- Custom split normals ---
    normals = [pmx_verts[v.vertex_index].normal for v in mesh_data.loops]
    mesh_data.normals_split_custom_set(normals)

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
