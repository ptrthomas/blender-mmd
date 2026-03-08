"""Mesh creation from parsed PMX data.

Per-material mesh build + control mesh for shape keys.
"""

from __future__ import annotations

import json
import logging
import math

import numpy as np
import bpy

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

# Name of the hidden control mesh that owns all shape keys
CONTROL_MESH_NAME = "_mmd_morphs"


def create_meshes(
    model: Model,
    armature_obj: bpy.types.Object,
    scale: float,
    *,
    split_by_material: bool = True,
    offset_overlapping: bool = False,
) -> list[bpy.types.Object]:
    """Create per-material Blender meshes and a control mesh from PMX data.

    Returns list of visible mesh objects (not including the control mesh).
    The control mesh is parented to the armature and holds all shape keys.
    """
    from .armature import _resolve_bone_name, _ensure_unique_names

    pmx_verts = model.vertices
    pmx_faces = model.faces
    pmx_bones = model.bones
    bone_names = _ensure_unique_names(pmx_bones)

    # Pre-compute per-vertex data as numpy arrays
    n_verts = len(pmx_verts)
    positions = np.array([v.position for v in pmx_verts], dtype=np.float64) * scale
    normals_arr = np.array([v.normal for v in pmx_verts], dtype=np.float32)
    vert_uvs = np.array([v.uv for v in pmx_verts], dtype=np.float32)
    vert_uvs[:, 1] = 1.0 - vert_uvs[:, 1]  # V-flip

    # Pre-compute morph data (which vertices each morph affects)
    morph_info = _precompute_morph_data(model, n_verts, scale)

    # Detect overlapping face materials on the full PMX data
    overlap_mats = _detect_overlapping_materials(model, positions)

    # Build per-material face ranges
    mat_ranges = []
    face_offset = 0
    for mat_data in model.materials:
        n_tris = mat_data.face_count // 3
        mat_ranges.append((face_offset, face_offset + n_tris))
        face_offset += n_tris

    if not split_by_material or len(model.materials) <= 1:
        # Single mesh mode
        mesh_obj = _build_single_mesh(
            model, armature_obj, scale, bone_names,
            positions, normals_arr, vert_uvs,
        )
        _create_shape_keys_on_mesh(model, mesh_obj, scale, morph_info, set(range(n_verts)))
        return [mesh_obj]

    # --- Per-material mesh build ---
    mesh_objects = []

    for mat_idx, (face_start, face_end) in enumerate(mat_ranges):
        if face_start >= face_end:
            continue

        # Collect faces for this material
        mat_faces = pmx_faces[face_start:face_end]

        # Find unique vertices referenced by these faces
        unique_verts_set = set()
        for f in mat_faces:
            unique_verts_set.update(f)
        unique_verts = sorted(unique_verts_set)

        # Build remapping: old_vertex_index -> new_vertex_index
        old_to_new = {old: new for new, old in enumerate(unique_verts)}
        n_mesh_verts = len(unique_verts)

        # Remap faces
        remapped_faces = [
            (old_to_new[f[0]], old_to_new[f[1]], old_to_new[f[2]])
            for f in mat_faces
        ]

        # Subset vertex data
        sub_positions = positions[unique_verts]
        sub_normals = normals_arr[unique_verts]
        sub_uvs = vert_uvs[unique_verts]

        # Offset overlapping material vertices along normals to prevent z-fighting
        if offset_overlapping and mat_idx in overlap_mats:
            sub_positions = sub_positions + sub_normals * (0.0002 * scale / 0.08)

        # Create mesh
        mat_data = model.materials[mat_idx]
        from .translations import MATERIAL_NAMES, resolve_name
        mat_name = resolve_name(mat_data.name, mat_data.name_e, MATERIAL_NAMES)

        mesh_data = bpy.data.meshes.new(mat_name)
        mesh_obj = bpy.data.objects.new(mat_name, mesh_data)
        bpy.context.collection.objects.link(mesh_obj)

        # Build geometry
        mesh_data.from_pydata(sub_positions.tolist(), [], remapped_faces)
        mesh_data.update()

        # --- Vertex groups (bone weights) ---
        _assign_vertex_weights(
            mesh_obj, pmx_verts, bone_names, unique_verts, old_to_new,
        )

        # --- SDEF attributes ---
        _assign_sdef_attributes(
            mesh_obj, pmx_verts, armature_obj, scale, unique_verts, old_to_new,
        )

        # --- Per-vertex edge scale ---
        _assign_edge_scale(mesh_obj, pmx_verts, unique_verts, old_to_new)

        # --- UV coordinates ---
        _assign_uvs(mesh_data, sub_uvs, model.header.additional_uv_count,
                     pmx_verts, unique_verts, old_to_new)

        # --- Smooth shading ---
        mesh_data.polygons.foreach_set(
            "use_smooth", (True,) * len(mesh_data.polygons)
        )

        # --- Sharp edges + custom normals ---
        _apply_normals(mesh_obj, mesh_data, sub_normals, old_to_new)

        # --- Shape keys ---
        unique_verts_set_frozen = frozenset(unique_verts)
        _create_shape_keys_on_mesh(
            model, mesh_obj, scale, morph_info, unique_verts_set_frozen,
            old_to_new=old_to_new,
        )

        # --- Parent to armature ---
        mesh_obj.parent = armature_obj
        mod = mesh_obj.modifiers.new(name="Armature", type="ARMATURE")
        mod.object = armature_obj

        # --- Set visible_shadow from mmd_drop_shadow ---
        if not mat_data.enabled_drop_shadow:
            mesh_obj.visible_shadow = False

        # --- Flag overlapping materials for diffuse shader ---
        if mat_idx in overlap_mats:
            mesh_obj["_mmd_overlap"] = True

        mesh_objects.append(mesh_obj)

    # --- Create control mesh ---
    _create_control_mesh(model, armature_obj, scale, morph_info)

    # --- Organize into collection ---
    model_name = model.name_e if model.name_e else model.name
    collection = bpy.data.collections.new(model_name)
    bpy.context.scene.collection.children.link(collection)

    ctrl_mesh = None
    for c in armature_obj.children:
        if c.type == "MESH" and c.get("mmd_control_mesh"):
            ctrl_mesh = c
            break

    all_objs = [armature_obj] + mesh_objects + ([ctrl_mesh] if ctrl_mesh else [])
    for obj in all_objs:
        for old_col in list(obj.users_collection):
            old_col.objects.unlink(obj)
        collection.objects.link(obj)

    log.info(
        "Created %d per-material meshes + control mesh, collection '%s'",
        len(mesh_objects), collection.name,
    )
    return mesh_objects


def _build_single_mesh(
    model: Model,
    armature_obj: bpy.types.Object,
    scale: float,
    bone_names: list[str],
    positions: np.ndarray,
    normals_arr: np.ndarray,
    vert_uvs: np.ndarray,
) -> bpy.types.Object:
    """Build a single mesh containing all vertices (no split)."""
    pmx_verts = model.vertices
    n_verts = len(pmx_verts)

    mesh_name = (model.name_e if model.name_e else model.name) + "_mesh"
    mesh_data = bpy.data.meshes.new(mesh_name)
    mesh_obj = bpy.data.objects.new(mesh_name, mesh_data)
    bpy.context.collection.objects.link(mesh_obj)

    mesh_data.from_pydata(positions.tolist(), [], model.faces)
    mesh_data.update()

    # Vertex groups
    all_verts = list(range(n_verts))
    identity_map = {i: i for i in range(n_verts)}
    _assign_vertex_weights(mesh_obj, pmx_verts, bone_names, all_verts, identity_map)

    # SDEF
    _assign_sdef_attributes(mesh_obj, pmx_verts, armature_obj, scale, all_verts, identity_map)

    # Edge scale
    _assign_edge_scale(mesh_obj, pmx_verts, all_verts, identity_map)

    # UVs
    _assign_uvs(mesh_data, vert_uvs, model.header.additional_uv_count,
                 pmx_verts, all_verts, identity_map)

    # Smooth shading
    mesh_data.polygons.foreach_set("use_smooth", (True,) * len(mesh_data.polygons))

    # Normals
    _apply_normals(mesh_obj, mesh_data, normals_arr, identity_map)

    # Parent
    mesh_obj.parent = armature_obj
    mod = mesh_obj.modifiers.new(name="Armature", type="ARMATURE")
    mod.object = armature_obj

    return mesh_obj


def _assign_vertex_weights(
    mesh_obj: bpy.types.Object,
    pmx_verts: list,
    bone_names: list[str],
    unique_verts: list[int],
    old_to_new: dict[int, int],
) -> None:
    """Create vertex groups and assign bone weights for a subset of vertices."""
    n_bones = len(bone_names)
    # Only create vertex groups for bones actually used by this mesh
    used_bones: set[int] = set()
    for old_vi in unique_verts:
        w = pmx_verts[old_vi].weight
        if isinstance(w, BoneWeightBDEF1):
            if 0 <= w.bone < n_bones:
                used_bones.add(w.bone)
        elif isinstance(w, (BoneWeightBDEF2, BoneWeightSDEF)):
            if 0 <= w.bone1 < n_bones:
                used_bones.add(w.bone1)
            if 0 <= w.bone2 < n_bones:
                used_bones.add(w.bone2)
        elif isinstance(w, (BoneWeightBDEF4, BoneWeightQDEF)):
            for bone_idx in w.bones:
                if 0 <= bone_idx < n_bones:
                    used_bones.add(bone_idx)

    # Create all needed vertex groups
    vg_by_bone: dict[int, bpy.types.VertexGroup] = {}
    for bone_idx in sorted(used_bones):
        vg_by_bone[bone_idx] = mesh_obj.vertex_groups.new(name=bone_names[bone_idx])

    # Collect assignments
    unity: dict[int, list[int]] = {}
    varying: dict[int, list[tuple[int, float]]] = {}

    for old_vi in unique_verts:
        new_vi = old_to_new[old_vi]
        w = pmx_verts[old_vi].weight
        if isinstance(w, BoneWeightBDEF1):
            if 0 <= w.bone < n_bones:
                unity.setdefault(w.bone, []).append(new_vi)
        elif isinstance(w, (BoneWeightBDEF2, BoneWeightSDEF)):
            if w.bone1 == w.bone2:
                if 0 <= w.bone1 < n_bones:
                    unity.setdefault(w.bone1, []).append(new_vi)
            else:
                if 0 <= w.bone1 < n_bones and w.weight > 0:
                    varying.setdefault(w.bone1, []).append((new_vi, w.weight))
                w2 = 1.0 - w.weight
                if 0 <= w.bone2 < n_bones and w2 > 0:
                    varying.setdefault(w.bone2, []).append((new_vi, w2))
        elif isinstance(w, (BoneWeightBDEF4, BoneWeightQDEF)):
            for bone_idx, weight in zip(w.bones, w.weights):
                if 0 <= bone_idx < n_bones and weight > 0:
                    if weight == 1.0:
                        unity.setdefault(bone_idx, []).append(new_vi)
                    else:
                        varying.setdefault(bone_idx, []).append((new_vi, weight))

    for bone_idx, verts in unity.items():
        if bone_idx in vg_by_bone:
            vg_by_bone[bone_idx].add(verts, 1.0, "REPLACE")

    for bone_idx, pairs in varying.items():
        if bone_idx in vg_by_bone:
            vg = vg_by_bone[bone_idx]
            for vi, w in pairs:
                vg.add([vi], w, "REPLACE")


def _assign_sdef_attributes(
    mesh_obj: bpy.types.Object,
    pmx_verts: list,
    armature_obj: bpy.types.Object,
    scale: float,
    unique_verts: list[int],
    old_to_new: dict[int, int],
) -> None:
    """Store SDEF C/R0/R1 as mesh attributes for the vertex subset."""
    sdef_verts = [
        (old_vi, pmx_verts[old_vi].weight)
        for old_vi in unique_verts
        if isinstance(pmx_verts[old_vi].weight, BoneWeightSDEF)
    ]
    if not sdef_verts:
        return

    mesh_data = mesh_obj.data
    n_mesh_verts = len(mesh_data.vertices)

    mesh_data.attributes.new("mmd_sdef_c", "FLOAT_VECTOR", "POINT")
    mesh_data.attributes.new("mmd_sdef_r0", "FLOAT_VECTOR", "POINT")
    mesh_data.attributes.new("mmd_sdef_r1", "FLOAT_VECTOR", "POINT")

    c_data = np.zeros(n_mesh_verts * 3, dtype=np.float32)
    r0_data = np.zeros(n_mesh_verts * 3, dtype=np.float32)
    r1_data = np.zeros(n_mesh_verts * 3, dtype=np.float32)

    for old_vi, w in sdef_verts:
        new_vi = old_to_new[old_vi]
        base = new_vi * 3
        c_data[base:base + 3] = [v * scale for v in w.c]
        r0_data[base:base + 3] = [v * scale for v in w.r0]
        r1_data[base:base + 3] = [v * scale for v in w.r1]

    mesh_data.attributes["mmd_sdef_c"].data.foreach_set("vector", c_data)
    mesh_data.attributes["mmd_sdef_r0"].data.foreach_set("vector", r0_data)
    mesh_data.attributes["mmd_sdef_r1"].data.foreach_set("vector", r1_data)

    vg_sdef = mesh_obj.vertex_groups.new(name="mmd_sdef")
    vg_sdef.add([old_to_new[old_vi] for old_vi, _ in sdef_verts], 1.0, "REPLACE")
    vg_sdef.lock_weight = True

    # Accumulate total SDEF count on armature
    prev_count = armature_obj.get("mmd_sdef_count", 0)
    armature_obj["mmd_sdef_count"] = prev_count + len(sdef_verts)
    armature_obj["mmd_has_sdef"] = True


def _assign_edge_scale(
    mesh_obj: bpy.types.Object,
    pmx_verts: list,
    unique_verts: list[int],
    old_to_new: dict[int, int],
) -> None:
    """Create mmd_edge_scale vertex group for the vertex subset."""
    vg_edge = mesh_obj.vertex_groups.new(name="mmd_edge_scale")
    edge_by_weight: dict[float, list[int]] = {}
    for old_vi in unique_verts:
        pmx_v = pmx_verts[old_vi]
        if pmx_v.edge_scale > 0:
            edge_by_weight.setdefault(round(pmx_v.edge_scale, 6), []).append(
                old_to_new[old_vi]
            )
    for weight, indices in edge_by_weight.items():
        vg_edge.add(indices, weight, "REPLACE")
    vg_edge.lock_weight = True


def _assign_uvs(
    mesh_data,
    sub_uvs: np.ndarray,
    additional_uv_count: int,
    pmx_verts: list,
    unique_verts: list[int],
    old_to_new: dict[int, int],
) -> None:
    """Assign UV layers to a mesh."""
    n_loops = len(mesh_data.loops)
    vi_array = np.empty(n_loops, dtype=np.int32)
    mesh_data.loops.foreach_get("vertex_index", vi_array)

    loop_uvs = sub_uvs[vi_array].ravel()
    uv_layer = mesh_data.uv_layers.new(name="UV")
    uv_layer.data.foreach_set("uv", loop_uvs)

    # Additional UV layers
    n_mesh_verts = len(unique_verts)
    for uv_idx in range(additional_uv_count):
        extra_uv = mesh_data.uv_layers.new(name=f"UV{uv_idx + 1}")
        extra_vert_uvs = np.zeros((n_mesh_verts, 2), dtype=np.float32)
        for new_vi, old_vi in enumerate(unique_verts):
            pmx_v = pmx_verts[old_vi]
            if uv_idx < len(pmx_v.additional_uvs):
                auv = pmx_v.additional_uvs[uv_idx]
                extra_vert_uvs[new_vi] = (auv[0], 1.0 - auv[1])
        extra_loop_uvs = extra_vert_uvs[vi_array].ravel()
        extra_uv.data.foreach_set("uv", extra_loop_uvs)


def _apply_normals(
    mesh_obj: bpy.types.Object,
    mesh_data,
    sub_normals: np.ndarray,
    old_to_new: dict[int, int],
) -> None:
    """Mark sharp edges and apply custom split normals."""
    bpy.context.view_layer.objects.active = mesh_obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.mesh.edges_select_sharp(sharpness=math.radians(179))
    bpy.ops.mesh.mark_sharp()
    bpy.ops.object.mode_set(mode="OBJECT")

    # Custom split normals — index by loop vertex
    n_loops = len(mesh_data.loops)
    vi_array = np.empty(n_loops, dtype=np.int32)
    mesh_data.loops.foreach_get("vertex_index", vi_array)
    loop_normals = sub_normals[vi_array].tolist()
    mesh_data.normals_split_custom_set(loop_normals)


# ---------------------------------------------------------------------------
# Morph precomputation
# ---------------------------------------------------------------------------

def _resolve_morph_name(morph) -> str:
    """Choose English shape key name for a morph."""
    from .translations import MORPH_NAMES, resolve_name
    return resolve_name(morph.name, morph.name_e, MORPH_NAMES)


def _flatten_group_morph(
    model: Model,
    morph_index: int,
    factor: float,
    vertex_deltas: dict[int, list[float]],
    visited: set[int],
    n_verts: int,
    scale: float,
) -> int:
    """Recursively resolve a group morph into accumulated vertex deltas."""
    morph = model.morphs[morph_index]
    skipped = 0
    for child_offset in morph.offsets:
        ci = child_offset.morph_index
        if ci < 0 or ci >= len(model.morphs):
            continue
        child = model.morphs[ci]
        effective = factor * child_offset.factor
        if child.morph_type == MorphType.VERTEX:
            for off in child.offsets:
                vi = off.vertex_index
                if 0 <= vi < n_verts:
                    if vi not in vertex_deltas:
                        vertex_deltas[vi] = [0.0, 0.0, 0.0]
                    vertex_deltas[vi][0] += off.offset[0] * scale * effective
                    vertex_deltas[vi][1] += off.offset[1] * scale * effective
                    vertex_deltas[vi][2] += off.offset[2] * scale * effective
        elif child.morph_type == MorphType.GROUP:
            if ci not in visited:
                visited.add(ci)
                skipped += _flatten_group_morph(
                    model, ci, effective, vertex_deltas, visited, n_verts, scale
                )
        else:
            skipped += 1
    return skipped


def _precompute_morph_data(
    model: Model, n_verts: int, scale: float,
) -> list[tuple[str, str, dict[int, list[float]]]]:
    """Precompute all morphs as (jp_name, en_name, {vertex_index: [dx,dy,dz]}).

    Returns list of morphs that have vertex deltas (vertex morphs + flattened groups).
    """
    result = []
    used_names: set[str] = {"Basis"}

    # Pass 1: vertex morphs
    for morph in model.morphs:
        if morph.morph_type != MorphType.VERTEX:
            continue
        name = _resolve_morph_name(morph)
        if name in used_names:
            suffix = 1
            while f"{name}.{suffix:03d}" in used_names:
                suffix += 1
            name = f"{name}.{suffix:03d}"
        used_names.add(name)

        deltas: dict[int, list[float]] = {}
        for off in morph.offsets:
            vi = off.vertex_index
            if 0 <= vi < n_verts:
                deltas[vi] = [
                    off.offset[0] * scale,
                    off.offset[1] * scale,
                    off.offset[2] * scale,
                ]
        if deltas:
            result.append((morph.name, name, deltas))

    # Pass 2: group morphs
    for morph_idx, morph in enumerate(model.morphs):
        if morph.morph_type != MorphType.GROUP:
            continue
        name = _resolve_morph_name(morph)
        if name in used_names:
            continue

        vertex_deltas: dict[int, list[float]] = {}
        visited: set[int] = {morph_idx}
        for child_offset in morph.offsets:
            ci = child_offset.morph_index
            if ci < 0 or ci >= len(model.morphs):
                continue
            child = model.morphs[ci]
            effective = child_offset.factor
            if child.morph_type == MorphType.VERTEX:
                for off in child.offsets:
                    vi = off.vertex_index
                    if 0 <= vi < n_verts:
                        if vi not in vertex_deltas:
                            vertex_deltas[vi] = [0.0, 0.0, 0.0]
                        vertex_deltas[vi][0] += off.offset[0] * scale * effective
                        vertex_deltas[vi][1] += off.offset[1] * scale * effective
                        vertex_deltas[vi][2] += off.offset[2] * scale * effective
            elif child.morph_type == MorphType.GROUP:
                if ci not in visited:
                    visited.add(ci)
                    _flatten_group_morph(
                        model, ci, effective, vertex_deltas, visited, n_verts, scale
                    )

        if vertex_deltas:
            used_names.add(name)
            result.append((morph.name, name, vertex_deltas))

    return result


def _create_shape_keys_on_mesh(
    model: Model,
    mesh_obj: bpy.types.Object,
    scale: float,
    morph_info: list[tuple[str, str, dict[int, list[float]]]],
    mesh_verts: frozenset[int] | set[int],
    old_to_new: dict[int, int] | None = None,
) -> None:
    """Create shape keys on a mesh for morphs that affect its vertices.

    Only creates shape keys for morphs that have at least one affected vertex
    in this mesh's vertex set.
    """
    # Filter morphs to those affecting this mesh
    relevant = []
    for jp_name, en_name, deltas in morph_info:
        affected = set(deltas.keys()) & mesh_verts
        if affected:
            relevant.append((jp_name, en_name, deltas))

    if not relevant:
        return

    basis = mesh_obj.shape_key_add(name="Basis", from_mix=False)
    basis.value = 0.0

    n_mesh_verts = len(mesh_obj.data.vertices)
    basis_coords = [0.0] * (n_mesh_verts * 3)
    basis.data.foreach_get("co", basis_coords)

    for jp_name, en_name, deltas in relevant:
        sk = mesh_obj.shape_key_add(name=en_name, from_mix=False)
        sk.value = 0.0

        coords = basis_coords.copy()
        for vi, delta in deltas.items():
            if vi not in mesh_verts:
                continue
            new_vi = old_to_new[vi] if old_to_new else vi
            base = new_vi * 3
            coords[base] += delta[0]
            coords[base + 1] += delta[1]
            coords[base + 2] += delta[2]
        sk.data.foreach_set("co", coords)


def _create_control_mesh(
    model: Model,
    armature_obj: bpy.types.Object,
    scale: float,
    morph_info: list[tuple[str, str, dict[int, list[float]]]],
) -> bpy.types.Object | None:
    """Create the hidden control mesh that owns all shape key values.

    This mesh has minimal geometry (single triangle) and all shape keys
    as value-only holders (Basis + zero-delta keys). Visible meshes' shape
    keys are driven from here.
    """
    if not morph_info:
        return None

    mesh_data = bpy.data.meshes.new(CONTROL_MESH_NAME)
    ctrl_obj = bpy.data.objects.new(CONTROL_MESH_NAME, mesh_data)
    bpy.context.collection.objects.link(ctrl_obj)

    # Minimal geometry: single triangle
    mesh_data.from_pydata(
        [(0, 0, 0), (0.001, 0, 0), (0, 0.001, 0)],
        [],
        [(0, 1, 2)],
    )
    mesh_data.update()

    # Create Basis shape key and name the ShapeKey datablock
    basis = ctrl_obj.shape_key_add(name="Basis", from_mix=False)
    basis.value = 0.0
    ctrl_obj.data.shape_keys.name = CONTROL_MESH_NAME

    # Create all morph shape keys (value-only, no vertex offsets)
    morph_map: dict[str, str] = {}
    for jp_name, en_name, deltas in morph_info:
        sk = ctrl_obj.shape_key_add(name=en_name, from_mix=False)
        sk.value = 0.0
        morph_map[jp_name] = sk.name

    # Store morph map on control mesh
    ctrl_obj["mmd_morph_map"] = json.dumps(morph_map, ensure_ascii=False)
    ctrl_obj["mmd_control_mesh"] = True

    # Parent to armature
    ctrl_obj.parent = armature_obj

    log.info(
        "Created control mesh '%s' with %d shape keys",
        CONTROL_MESH_NAME, len(morph_info),
    )

    # Register the morph sync handler
    _ensure_morph_sync_handler()

    return ctrl_obj


def _detect_overlapping_materials(
    model: Model,
    positions: np.ndarray,
) -> set[int]:
    """Detect which material indices have faces overlapping earlier materials.

    Runs on the full PMX face data before per-material mesh build.
    Returns set of material indices that overlap.
    """
    faces = model.faces
    materials = model.materials

    rounded = np.round(positions, 6)

    check: dict[tuple, int] = {}
    overlap_mats: set[int] = set()
    mi_skip = -1

    face_offset = 0
    for mat_idx, mat_data in enumerate(materials):
        n_tris = mat_data.face_count // 3
        if mat_idx <= mi_skip:
            face_offset += n_tris
            continue
        for fi in range(face_offset, face_offset + n_tris):
            if fi >= len(faces):
                break
            f = faces[fi]
            key = tuple(sorted(
                (tuple(rounded[f[0]]), tuple(rounded[f[1]]), tuple(rounded[f[2]]))
            ))
            if key not in check:
                check[key] = mat_idx
            elif check[key] < mat_idx:
                overlap_mats.add(mat_idx)
                mi_skip = mat_idx
                break
        face_offset += n_tris

    if overlap_mats:
        log.debug("Detected %d overlapping material indices", len(overlap_mats))

    return overlap_mats


def find_control_mesh(armature_obj: bpy.types.Object) -> bpy.types.Object | None:
    """Find the control mesh child of an armature."""
    for child in armature_obj.children:
        if child.type == "MESH" and child.get("mmd_control_mesh"):
            return child
    return None


def is_control_mesh(obj: bpy.types.Object) -> bool:
    """Check if an object is the hidden control mesh."""
    return obj.get("mmd_control_mesh", False)


# ---------------------------------------------------------------------------
# Morph sync handler — copies control mesh shape key values to visible meshes
# ---------------------------------------------------------------------------

def _morph_sync_handler(scene: bpy.types.Scene) -> None:
    """frame_change_post handler: sync control mesh → visible meshes + material controls."""
    from .materials import update_materials

    for obj in scene.objects:
        if obj.type != "ARMATURE" or obj.get("import_scale") is None:
            continue

        # --- Material controls: sync toon_fac, sphere_fac, emission ---
        update_materials(obj)

        # --- Morph sync: control mesh → visible meshes ---
        ctrl = None
        sk_children = []
        for c in obj.children:
            if c.type != "MESH":
                continue
            if c.get("mmd_control_mesh"):
                ctrl = c
            elif c.data.shape_keys:
                sk_children.append(c)

        if ctrl and ctrl.data.shape_keys and sk_children:
            ctrl_kb = ctrl.data.shape_keys.key_blocks
            for child in sk_children:
                for kb in child.data.shape_keys.key_blocks:
                    if kb == child.data.shape_keys.reference_key:
                        continue
                    src = ctrl_kb.get(kb.name)
                    if src is not None:
                        kb.value = src.value


def _ensure_morph_sync_handler() -> None:
    """Register the morph sync handler if not already registered."""
    for h in bpy.app.handlers.frame_change_post:
        if getattr(h, "__name__", "") == "_morph_sync_handler":
            return
    bpy.app.handlers.frame_change_post.append(_morph_sync_handler)


def _remove_morph_sync_handler() -> None:
    """Unregister the morph sync handler."""
    bpy.app.handlers.frame_change_post[:] = [
        h for h in bpy.app.handlers.frame_change_post
        if getattr(h, "__name__", "") != "_morph_sync_handler"
    ]
