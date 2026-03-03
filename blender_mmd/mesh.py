"""Mesh creation from parsed PMX data."""

from __future__ import annotations

import logging

import numpy as np
import bpy
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
        elif isinstance(w, (BoneWeightBDEF2, BoneWeightSDEF)):
            if w.bone1 == w.bone2:
                # Both bones identical — assign weight 1.0 to avoid
                # second assignment overwriting the first with near-zero
                if 0 <= w.bone1 < len(bone_names):
                    mesh_obj.vertex_groups[bone_names[w.bone1]].add(
                        [vi], 1.0, "REPLACE"
                    )
            else:
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

    # --- SDEF attributes ---
    # Store SDEF C/R0/R1 parameters as per-vertex float3 mesh attributes
    # and create a vertex group for visualization/masking.
    sdef_verts: list[tuple[int, BoneWeightSDEF]] = [
        (vi, pmx_v.weight)
        for vi, pmx_v in enumerate(pmx_verts)
        if isinstance(pmx_v.weight, BoneWeightSDEF)
    ]
    if sdef_verts:
        n_verts = len(pmx_verts)
        # Create float3 attributes (domain=POINT)
        # Must create all first, then look up by name — creating new
        # attributes invalidates previously returned RNA references.
        mesh_data.attributes.new("mmd_sdef_c", "FLOAT_VECTOR", "POINT")
        mesh_data.attributes.new("mmd_sdef_r0", "FLOAT_VECTOR", "POINT")
        mesh_data.attributes.new("mmd_sdef_r1", "FLOAT_VECTOR", "POINT")

        # Build flat arrays (all zeros initially, SDEF verts get their values)
        c_data = np.zeros(n_verts * 3, dtype=np.float32)
        r0_data = np.zeros(n_verts * 3, dtype=np.float32)
        r1_data = np.zeros(n_verts * 3, dtype=np.float32)

        for vi, w in sdef_verts:
            base = vi * 3
            c_data[base:base + 3] = [v * scale for v in w.c]
            r0_data[base:base + 3] = [v * scale for v in w.r0]
            r1_data[base:base + 3] = [v * scale for v in w.r1]

        # Look up by name after all attributes exist
        mesh_data.attributes["mmd_sdef_c"].data.foreach_set("vector", c_data)
        mesh_data.attributes["mmd_sdef_r0"].data.foreach_set("vector", r0_data)
        mesh_data.attributes["mmd_sdef_r1"].data.foreach_set("vector", r1_data)

        # Create mmd_sdef vertex group (weight 1.0 for all SDEF verts)
        vg_sdef = mesh_obj.vertex_groups.new(name="mmd_sdef")
        vg_sdef.add([vi for vi, _ in sdef_verts], 1.0, "REPLACE")
        vg_sdef.lock_weight = True

        # Store flag and count on armature
        armature_obj["mmd_has_sdef"] = True
        armature_obj["mmd_sdef_count"] = len(sdef_verts)

        log.info("Stored SDEF data: %d vertices", len(sdef_verts))

    # --- Per-vertex edge scale ---
    # Stored as a locked vertex group for Solidify modifier per-vertex thickness
    vg_edge = mesh_obj.vertex_groups.new(name="mmd_edge_scale")
    for vi, pmx_v in enumerate(pmx_verts):
        if pmx_v.edge_scale > 0:
            vg_edge.add([vi], pmx_v.edge_scale, "REPLACE")
    vg_edge.lock_weight = True

    # --- UV coordinates ---
    # PMX uses DirectX convention (V=0 at top), Blender uses OpenGL (V=0 at bottom)
    # Bulk assign via foreach_set for performance (avoids 300k+ Python-level assignments)
    n_loops = len(mesh_data.loops)

    # Get loop→vertex mapping (bulk read)
    vi_array = np.empty(n_loops, dtype=np.int32)
    mesh_data.loops.foreach_get("vertex_index", vi_array)

    # Build per-vertex UV array from PMX data, V-flipped
    vert_uvs = np.array([v.uv for v in pmx_verts], dtype=np.float32)  # (n_verts, 2)
    vert_uvs[:, 1] = 1.0 - vert_uvs[:, 1]  # V-flip

    # Index by loop vertex to get per-loop UVs, flatten for foreach_set
    loop_uvs = vert_uvs[vi_array].ravel()  # (n_loops * 2,)

    uv_layer = mesh_data.uv_layers.new(name="UV")
    uv_layer.data.foreach_set("uv", loop_uvs)

    # Additional UV layers
    for uv_idx in range(model.header.additional_uv_count):
        extra_uv = mesh_data.uv_layers.new(name=f"UV{uv_idx + 1}")
        # Build per-vertex extra UV array (zero for vertices without this UV)
        n_verts = len(pmx_verts)
        extra_vert_uvs = np.zeros((n_verts, 2), dtype=np.float32)
        for vi, pmx_v in enumerate(pmx_verts):
            if uv_idx < len(pmx_v.additional_uvs):
                auv = pmx_v.additional_uvs[uv_idx]
                extra_vert_uvs[vi] = (auv[0], 1.0 - auv[1])
        extra_loop_uvs = extra_vert_uvs[vi_array].ravel()
        extra_uv.data.foreach_set("uv", extra_loop_uvs)

    # --- Smooth shading on all faces ---
    mesh_data.polygons.foreach_set(
        "use_smooth", (True,) * len(mesh_data.polygons)
    )

    # --- Mark all edges sharp for custom normals ---
    # normals_split_custom_set needs edges marked sharp to split normals.
    # Previously used bpy.ops.mesh.edges_select_sharp (179°) but that operator
    # and bmesh.normal_update() both hang on meshes with degenerate topology.
    # Marking ALL edges sharp is safe — custom normals override everything,
    # so over-marking has no visual effect.
    mesh_data.edges.foreach_set(
        "use_edge_sharp", (True,) * len(mesh_data.edges)
    )

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
    from .translations import MORPH_NAMES, resolve_name
    return resolve_name(morph.name, morph.name_e, MORPH_NAMES)


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
