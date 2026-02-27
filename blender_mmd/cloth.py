"""Cloth conversion — convert bone chains to Blender cloth simulation.

Two APIs:
- convert_selection_to_cloth(): new bone-position-based (Phase 1, UI panel)
- convert_chain_to_cloth(): legacy RB-position-based (backwards compat)

Creates a ribbon mesh, pins the root, applies cloth physics, and binds
bones via DAMPED_TRACK constraints.

Reference: blender_mmd_tools_append/converters/physics/rigid_body_to_cloth.py
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import bpy
    from .chains import Chain
    from .pmx.types import Model

log = logging.getLogger("blender_mmd")

# Cloth presets: tension stiffness, compression, bending
CLOTH_PRESETS = {
    "cotton": {"tension": 40.0, "compression": 40.0, "bending": 5.0, "mass": 0.3, "damping": 50.0},
    "silk": {"tension": 15.0, "compression": 15.0, "bending": 0.5, "mass": 0.15, "damping": 25.0},
    "hair": {"tension": 80.0, "compression": 80.0, "bending": 50.0, "mass": 0.3, "damping": 50.0},
}


def convert_chain_to_cloth(
    armature_obj,
    chain: Chain,
    model,
    scale: float,
    collision_mesh_obj=None,
    preset: str = "cotton",
) -> object:
    """Convert a single physics chain to a cloth-simulated ribbon mesh.

    Args:
        armature_obj: The MMD armature object.
        chain: Chain dataclass from detect_chains().
        model: Parsed PMX model.
        scale: Import scale factor.
        collision_mesh_obj: Optional mesh object for collision (e.g. body mesh).
        preset: Cloth preset name ("cotton", "silk", "hair").

    Returns:
        The created cloth mesh object.
    """
    import bpy
    import bmesh
    from mathutils import Vector

    preset_vals = CLOTH_PRESETS.get(preset, CLOTH_PRESETS["cotton"])
    rigid_bodies = model.rigid_bodies

    # Get or create cloth collection
    col_name = f"{armature_obj.name}_Cloth"
    collection = bpy.data.collections.get(col_name)
    if not collection:
        collection = bpy.data.collections.new(col_name)
        bpy.context.scene.collection.children.link(collection)
    armature_obj["cloth_collection"] = col_name

    # Build bone name lookup
    bone_names = {}
    for bone in armature_obj.data.bones:
        idx = bone.get("bone_id")
        if idx is not None:
            bone_names[idx] = bone.name

    # --- Step 1: Build ribbon mesh from RB positions ---
    # Vertices: root anchor + one per chain rigid body
    root_rb = rigid_bodies[chain.root_rigid_index]
    root_pos = Vector(root_rb.position) * scale

    positions = [root_pos]  # vertex 0 = root anchor (pinned)
    for ri in chain.rigid_indices:
        rb = rigid_bodies[ri]
        positions.append(Vector(rb.position) * scale)

    # Edges: root→first, then sequential through chain
    edges = []
    for i in range(len(positions) - 1):
        edges.append((i, i + 1))

    # Create mesh from edge skeleton
    mesh_name = f"Cloth_{chain.name}"
    mesh = bpy.data.meshes.new(mesh_name)
    cloth_obj = bpy.data.objects.new(mesh_name, mesh)
    collection.objects.link(cloth_obj)

    # Use bmesh to build ribbon (extrude edges to create faces)
    bm = bmesh.new()

    # Create skeleton vertices and edges
    bm_verts = [bm.verts.new(pos) for pos in positions]
    bm.verts.ensure_lookup_table()

    for v1_idx, v2_idx in edges:
        bm.edges.new((bm_verts[v1_idx], bm_verts[v2_idx]))

    # Extrude to create ribbon width
    # Direction: perpendicular to chain direction in XY plane
    if len(positions) >= 2:
        chain_dir = (positions[1] - positions[0]).normalized()
        # Cross with world Z to get perpendicular in XY
        up = Vector((0, 0, 1))
        perp = chain_dir.cross(up)
        if perp.length < 0.001:
            # Chain is vertical — use Y as fallback
            perp = chain_dir.cross(Vector((0, 1, 0)))
        perp.normalize()
        # Ribbon width proportional to first RB size
        first_rb = rigid_bodies[chain.rigid_indices[0]]
        width = max(first_rb.size[0], 0.01) * scale
        offset = perp * width
    else:
        offset = Vector((0.01, 0, 0))

    # Duplicate vertices with offset to create ribbon
    extruded_verts = []
    for v in bm_verts:
        ev = bm.verts.new(v.co + offset)
        extruded_verts.append(ev)
    bm.verts.ensure_lookup_table()

    # Create quads between original and extruded edges
    for v1_idx, v2_idx in edges:
        v1 = bm_verts[v1_idx]
        v2 = bm_verts[v2_idx]
        v3 = extruded_verts[v2_idx]
        v4 = extruded_verts[v1_idx]
        bm.faces.new([v1, v2, v3, v4])

    bm.to_mesh(mesh)
    bm.free()

    # --- Step 2: Pin vertex group (root vertices pinned) ---
    pin_group = cloth_obj.vertex_groups.new(name="pin")
    # Pin vertex 0 (root) and its extruded counterpart
    pin_group.add([0, len(positions)], 1.0, "REPLACE")

    # --- Step 3: Bone vertex groups (for DAMPED_TRACK binding) ---
    for i, ri in enumerate(chain.rigid_indices):
        rb = rigid_bodies[ri]
        if rb.bone_index < 0:
            continue
        bone_name = bone_names.get(rb.bone_index)
        if not bone_name:
            continue
        vg = cloth_obj.vertex_groups.new(name=bone_name)
        vert_idx = i + 1  # +1 because vertex 0 is root anchor
        vg.add([vert_idx, vert_idx + len(positions)], 1.0, "REPLACE")

    # --- Step 4: Modifier stack ---
    # Armature modifier for pin tracking
    arm_mod = cloth_obj.modifiers.new("Armature", "ARMATURE")
    arm_mod.object = armature_obj

    # Cloth modifier
    cloth_mod = cloth_obj.modifiers.new("Cloth", "CLOTH")
    cs = cloth_mod.settings
    cs.vertex_group_mass = "pin"
    cs.mass = preset_vals["mass"]
    cs.tension_stiffness = preset_vals["tension"]
    cs.compression_stiffness = preset_vals["compression"]
    cs.bending_stiffness = preset_vals["bending"]
    cs.quality = 8  # solver substeps

    # Damping
    cs.tension_damping = preset_vals.get("damping", 25.0)
    cs.compression_damping = preset_vals.get("damping", 25.0)
    cs.bending_damping = preset_vals.get("damping", 25.0) * 0.5

    # Corrective smooth for wrinkling
    smooth_mod = cloth_obj.modifiers.new("Smooth", "CORRECTIVE_SMOOTH")
    smooth_mod.iterations = 5
    smooth_mod.use_pin_boundary = True

    # --- Step 5: Collision on body mesh (if provided) ---
    if collision_mesh_obj is not None:
        if not collision_mesh_obj.modifiers.get("Collision"):
            col_mod = collision_mesh_obj.modifiers.new("Collision", "COLLISION")
            col_mod.settings.thickness_outer = 0.002
            col_mod.settings.thickness_inner = 0.001
            col_mod.settings.cloth_friction = 5.0

    # --- Step 6: Bone binding via DAMPED_TRACK ---
    for i, ri in enumerate(chain.rigid_indices):
        rb = rigid_bodies[ri]
        if rb.bone_index < 0:
            continue
        bone_name = bone_names.get(rb.bone_index)
        if not bone_name or bone_name not in armature_obj.pose.bones:
            continue

        pb = armature_obj.pose.bones[bone_name]
        # Remove existing mmd_dynamic constraints
        to_remove = [c for c in pb.constraints if c.name.startswith("mmd_")]
        for c in to_remove:
            pb.constraints.remove(c)

        # Add DAMPED_TRACK targeting bone's vertex group on cloth mesh
        con = pb.constraints.new("DAMPED_TRACK")
        con.name = "mmd_cloth"
        con.target = cloth_obj
        con.subtarget = bone_name

    # Store chain index on cloth object for reference
    cloth_obj["mmd_chain_name"] = chain.name
    cloth_obj["mmd_chain_group"] = chain.group

    log.info("Cloth created for chain '%s': %d vertices, preset=%s",
             chain.name, len(positions) * 2, preset)

    return cloth_obj


def clear_cloth(armature_obj) -> None:
    """Remove all cloth objects and constraints for this armature."""
    import bpy

    col_name = armature_obj.get("cloth_collection")
    if not col_name:
        return

    collection = bpy.data.collections.get(col_name)
    if collection:
        # Remove DAMPED_TRACK constraints from pose bones
        if armature_obj.pose:
            for pb in armature_obj.pose.bones:
                to_remove = [
                    c for c in pb.constraints
                    if c.name.startswith("mmd_cloth")
                ]
                for c in to_remove:
                    pb.constraints.remove(c)

        # Delete all cloth mesh objects
        for obj in list(collection.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.collections.remove(collection)

    if "cloth_collection" in armature_obj:
        del armature_obj["cloth_collection"]


# ---------------------------------------------------------------------------
# Phase 1: bone-position-based cloth (UI panel workflow)
# ---------------------------------------------------------------------------


def _get_or_create_cloth_collection(armature_obj):
    """Get or create the cloth collection for this armature."""
    import bpy

    col_name = f"{armature_obj.name}_Cloth"
    collection = bpy.data.collections.get(col_name)
    if not collection:
        collection = bpy.data.collections.new(col_name)
        bpy.context.scene.collection.children.link(collection)
    armature_obj["cloth_collection"] = col_name
    return collection


def convert_selection_to_cloth(
    armature_obj,
    bone_names: list[str],
    collision_mesh_obj=None,
    preset: str = "hair",
) -> object:
    """Convert a chain of bones to a cloth-simulated ribbon mesh.

    Builds the ribbon from bone head/tail positions (no PMX re-parse needed).
    The first bone's head is pinned; each bone's tail becomes a free vertex.

    Args:
        armature_obj: The MMD armature object.
        bone_names: Bone names sorted root→tip (validated by caller).
        collision_mesh_obj: Optional mesh for body collision.
        preset: Cloth preset name ("hair", "cotton", "silk").

    Returns:
        The created cloth mesh object.
    """
    import bpy
    import bmesh
    from mathutils import Vector

    preset_vals = CLOTH_PRESETS.get(preset, CLOTH_PRESETS["hair"])
    bones = armature_obj.data.bones
    world = armature_obj.matrix_world
    collection = _get_or_create_cloth_collection(armature_obj)

    # --- Build vertex positions from bone geometry ---
    # Vertex 0  = first bone's head (pinned)
    # Vertex i+1 = bone[i]'s tail
    positions = [world @ bones[bone_names[0]].head_local]
    for name in bone_names:
        positions.append(world @ bones[name].tail_local)

    n_verts = len(positions)  # N+1 center-line vertices

    # Ribbon width: 30% of average bone length
    total_length = sum(
        (positions[i + 1] - positions[i]).length for i in range(n_verts - 1)
    )
    width = (total_length / len(bone_names)) * 0.3

    # Perpendicular direction for ribbon extrusion
    chain_dir = (positions[-1] - positions[0]).normalized()
    up = Vector((0, 0, 1))
    perp = chain_dir.cross(up)
    if perp.length < 0.001:
        perp = chain_dir.cross(Vector((0, 1, 0)))
    perp.normalize()
    offset = perp * width

    # --- Build ribbon mesh ---
    mesh_name = f"Cloth_{bone_names[0]}"
    mesh = bpy.data.meshes.new(mesh_name)
    cloth_obj = bpy.data.objects.new(mesh_name, mesh)
    collection.objects.link(cloth_obj)

    bm = bmesh.new()
    center = [bm.verts.new(p) for p in positions]
    bm.verts.ensure_lookup_table()
    extruded = [bm.verts.new(v.co + offset) for v in center]
    bm.verts.ensure_lookup_table()

    for i in range(n_verts - 1):
        bm.faces.new([center[i], center[i + 1], extruded[i + 1], extruded[i]])

    bm.to_mesh(mesh)
    bm.free()

    # --- Vertex groups ---
    # Pin group: root vertices (vertex 0 and its extruded counterpart)
    pin_vg = cloth_obj.vertex_groups.new(name="pin")
    pin_vg.add([0, n_verts], 1.0, "REPLACE")

    # Parent bone group (Armature modifier moves pinned verts with parent)
    first_bone = bones[bone_names[0]]
    if first_bone.parent:
        parent_vg = cloth_obj.vertex_groups.new(name=first_bone.parent.name)
        parent_vg.add([0, n_verts], 1.0, "REPLACE")

    # Per-bone groups: each bone's tail vertex pair
    for i, name in enumerate(bone_names):
        vg = cloth_obj.vertex_groups.new(name=name)
        vg.add([i + 1, i + 1 + n_verts], 1.0, "REPLACE")

    # --- Modifier stack ---
    arm_mod = cloth_obj.modifiers.new("Armature", "ARMATURE")
    arm_mod.object = armature_obj

    cloth_mod = cloth_obj.modifiers.new("Cloth", "CLOTH")
    cs = cloth_mod.settings
    cs.vertex_group_mass = "pin"
    cs.mass = preset_vals["mass"]
    cs.tension_stiffness = preset_vals["tension"]
    cs.compression_stiffness = preset_vals["compression"]
    cs.bending_stiffness = preset_vals["bending"]
    cs.quality = 8
    cs.tension_damping = preset_vals.get("damping", 25.0)
    cs.compression_damping = preset_vals.get("damping", 25.0)
    cs.bending_damping = preset_vals.get("damping", 25.0) * 0.5

    smooth_mod = cloth_obj.modifiers.new("Smooth", "CORRECTIVE_SMOOTH")
    smooth_mod.iterations = 5
    smooth_mod.use_pin_boundary = True

    # --- Collision on body mesh ---
    if collision_mesh_obj is not None:
        if not collision_mesh_obj.modifiers.get("Collision"):
            col_mod = collision_mesh_obj.modifiers.new("Collision", "COLLISION")
            col_mod.settings.thickness_outer = 0.002
            col_mod.settings.thickness_inner = 0.001
            col_mod.settings.cloth_friction = 5.0

    # --- Bone binding via DAMPED_TRACK ---
    for name in bone_names:
        pb = armature_obj.pose.bones.get(name)
        if not pb:
            continue
        # Remove existing mmd_ constraints on this bone
        for c in [c for c in pb.constraints if c.name.startswith("mmd_")]:
            pb.constraints.remove(c)
        con = pb.constraints.new("DAMPED_TRACK")
        con.name = "mmd_cloth"
        con.target = cloth_obj
        con.subtarget = name

    # --- Metadata ---
    cloth_obj["mmd_bone_names"] = ",".join(bone_names)
    cloth_obj["mmd_preset"] = preset

    log.info(
        "Cloth created for '%s': %d bones, %d vertices, preset=%s",
        bone_names[0],
        len(bone_names),
        n_verts * 2,
        preset,
    )
    return cloth_obj


def convert_group_to_cloth(
    armature_obj,
    chains: list[list[str]],
    strut_names: list[str] | None = None,
    collision_mesh_obj=None,
    preset: str = "cotton",
) -> object:
    """Convert parallel bone chains to a cloth tube/cylinder mesh.

    Chains are connected laterally in ring topology to form a closed tube.
    Strut bones (in "Struts" bone collection) add extra lateral faces between
    non-adjacent chains for additional structural stiffness.

    Args:
        armature_obj: The MMD armature object.
        chains: List of bone name lists, each root→tip, sorted by angle.
        strut_names: Bone names in the "Struts" collection (structural only).
        collision_mesh_obj: Optional mesh for body collision.
        preset: Cloth preset name.

    Returns:
        The created cloth mesh object.
    """
    import bpy
    import bmesh
    from mathutils import Vector

    preset_vals = CLOTH_PRESETS.get(preset, CLOTH_PRESETS["cotton"])
    bones = armature_obj.data.bones
    world = armature_obj.matrix_world
    collection = _get_or_create_cloth_collection(armature_obj)

    N = len(chains)

    # --- Build vertex positions per chain ---
    # vertex_pos[i] = [head, tail0, tail1, ...] in world space
    vertex_pos: list[list] = []
    for chain in chains:
        positions = [world @ bones[chain[0]].head_local]
        for name in chain:
            positions.append(world @ bones[name].tail_local)
        vertex_pos.append(positions)

    # Cumulative vertex offsets for flat indexing
    offsets = [0]
    for vp in vertex_pos:
        offsets.append(offsets[-1] + len(vp))

    def vi(chain_i: int, depth_j: int) -> int:
        return offsets[chain_i] + depth_j

    # --- Create bmesh ---
    bm = bmesh.new()
    bm_verts = []
    for vp in vertex_pos:
        for pos in vp:
            bm_verts.append(bm.verts.new(pos))
    bm.verts.ensure_lookup_table()

    # --- Determine which chain pairs to connect ---
    connected_pairs: set[tuple[int, int]] = set()
    # Ring-adjacent pairs
    for i in range(N):
        ni = (i + 1) % N
        pair = (min(i, ni), max(i, ni))
        connected_pairs.add(pair)

    # Strut-indicated pairs (non-adjacent connections)
    if strut_names:
        for sname in strut_names:
            bone = bones.get(sname)
            if not bone:
                continue
            head_w = world @ bone.head_local
            tail_w = world @ bone.tail_local
            # Find nearest chain for each strut endpoint
            def _nearest_chain(pos):
                best_i, best_d2 = 0, float("inf")
                for ci in range(N):
                    for p in vertex_pos[ci]:
                        d2 = (p - pos).length_squared
                        if d2 < best_d2:
                            best_i, best_d2 = ci, d2
                return best_i

            ci_h = _nearest_chain(head_w)
            ci_t = _nearest_chain(tail_w)
            if ci_h != ci_t:
                pair = (min(ci_h, ci_t), max(ci_h, ci_t))
                connected_pairs.add(pair)

    # --- Create faces between connected chain pairs ---
    for ci, cj in connected_pairs:
        min_depth = min(len(chains[ci]), len(chains[cj]))
        for d in range(min_depth):
            v1 = bm_verts[vi(ci, d)]
            v2 = bm_verts[vi(ci, d + 1)]
            v3 = bm_verts[vi(cj, d + 1)]
            v4 = bm_verts[vi(cj, d)]
            try:
                bm.faces.new([v1, v2, v3, v4])
            except ValueError:
                pass  # face already exists

    # --- Create mesh object ---
    label = chains[0][0]
    mesh_name = f"ClothGroup_{label}"
    mesh = bpy.data.meshes.new(mesh_name)
    cloth_obj = bpy.data.objects.new(mesh_name, mesh)
    collection.objects.link(cloth_obj)

    bm.to_mesh(mesh)
    bm.free()

    # --- Vertex groups ---
    # Pin: depth-0 vertices of all chains (roots)
    pin_vg = cloth_obj.vertex_groups.new(name="pin")
    pin_vg.add([vi(i, 0) for i in range(N)], 1.0, "REPLACE")

    # Parent bone groups for armature tracking of pinned verts
    parent_map: dict[str, list[int]] = {}
    for i, chain in enumerate(chains):
        root_bone = bones[chain[0]]
        if root_bone.parent:
            pname = root_bone.parent.name
            parent_map.setdefault(pname, []).append(vi(i, 0))
    for pname, indices in parent_map.items():
        vg = cloth_obj.vertex_groups.new(name=pname)
        vg.add(indices, 1.0, "REPLACE")

    # Per-bone vertex groups for DAMPED_TRACK binding
    for i, chain in enumerate(chains):
        for j, name in enumerate(chain):
            vg = cloth_obj.vertex_groups.new(name=name)
            vg.add([vi(i, j + 1)], 1.0, "REPLACE")

    # --- Modifier stack ---
    arm_mod = cloth_obj.modifiers.new("Armature", "ARMATURE")
    arm_mod.object = armature_obj

    cloth_mod = cloth_obj.modifiers.new("Cloth", "CLOTH")
    cs = cloth_mod.settings
    cs.vertex_group_mass = "pin"
    cs.mass = preset_vals["mass"]
    cs.tension_stiffness = preset_vals["tension"]
    cs.compression_stiffness = preset_vals["compression"]
    cs.bending_stiffness = preset_vals["bending"]
    cs.quality = 8
    cs.tension_damping = preset_vals.get("damping", 25.0)
    cs.compression_damping = preset_vals.get("damping", 25.0)
    cs.bending_damping = preset_vals.get("damping", 25.0) * 0.5

    smooth_mod = cloth_obj.modifiers.new("Smooth", "CORRECTIVE_SMOOTH")
    smooth_mod.iterations = 5
    smooth_mod.use_pin_boundary = True

    # --- Collision ---
    if collision_mesh_obj is not None:
        if not collision_mesh_obj.modifiers.get("Collision"):
            col_mod = collision_mesh_obj.modifiers.new("Collision", "COLLISION")
            col_mod.settings.thickness_outer = 0.002
            col_mod.settings.thickness_inner = 0.001
            col_mod.settings.cloth_friction = 5.0

    # --- Bone binding via DAMPED_TRACK ---
    all_bone_names = []
    for chain in chains:
        for name in chain:
            pb = armature_obj.pose.bones.get(name)
            if not pb:
                continue
            for c in [c for c in pb.constraints if c.name.startswith("mmd_")]:
                pb.constraints.remove(c)
            con = pb.constraints.new("DAMPED_TRACK")
            con.name = "mmd_cloth"
            con.target = cloth_obj
            con.subtarget = name
            all_bone_names.append(name)

    # --- Armature display ---
    armature_obj.data.display_type = "STICK"

    # --- Struts collection color ---
    if strut_names:
        struts_col = armature_obj.data.collections.get("Struts")
        if struts_col:
            try:
                struts_col.color.palette = "THEME06"
            except Exception:
                pass  # color API may vary

    # --- Metadata ---
    cloth_obj["mmd_bone_names"] = ",".join(all_bone_names)
    cloth_obj["mmd_preset"] = preset
    cloth_obj["mmd_is_group"] = True

    log.info(
        "Group cloth created for '%s': %d chains, %d bones, %d vertices, preset=%s",
        label,
        N,
        len(all_bone_names),
        len(bm_verts),
        preset,
    )
    return cloth_obj


def remove_cloth_sim(armature_obj, cloth_object_name: str) -> None:
    """Remove a single cloth simulation by object name.

    Deletes the cloth object and removes DAMPED_TRACK constraints from
    the affected bones.
    """
    import bpy

    cloth_obj = bpy.data.objects.get(cloth_object_name)
    if not cloth_obj:
        return

    # Remove DAMPED_TRACK constraints from affected bones
    bone_names_str = cloth_obj.get("mmd_bone_names", "")
    if bone_names_str and armature_obj.pose:
        for name in bone_names_str.split(","):
            pb = armature_obj.pose.bones.get(name)
            if pb:
                for c in [
                    c
                    for c in pb.constraints
                    if c.name.startswith("mmd_cloth")
                ]:
                    pb.constraints.remove(c)

    bpy.data.objects.remove(cloth_obj, do_unlink=True)

    # Clean up empty collection
    col_name = armature_obj.get("cloth_collection")
    if col_name:
        collection = bpy.data.collections.get(col_name)
        if collection and len(collection.objects) == 0:
            bpy.data.collections.remove(collection)
            if "cloth_collection" in armature_obj:
                del armature_obj["cloth_collection"]
