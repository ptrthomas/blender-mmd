"""Cloth conversion — convert physics chains to Blender cloth simulation.

Creates a ribbon mesh from chain rigid body positions, pins the root,
applies cloth physics, and binds bones via STRETCH_TO constraints.

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
    "cotton": {"tension": 15.0, "compression": 15.0, "bending": 0.5, "mass": 0.3},
    "silk": {"tension": 5.0, "compression": 5.0, "bending": 0.05, "mass": 0.15},
    "hair": {"tension": 20.0, "compression": 20.0, "bending": 5.0, "mass": 0.4},
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

    # --- Step 3: Bone vertex groups (for STRETCH_TO binding) ---
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
    cs.tension_damping = 25.0
    cs.compression_damping = 25.0
    cs.bending_damping = 0.5

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

    # --- Step 6: Bone binding via STRETCH_TO ---
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

        # Add STRETCH_TO targeting bone's vertex group on cloth mesh
        con = pb.constraints.new("STRETCH_TO")
        con.name = "mmd_cloth"
        con.target = cloth_obj
        con.subtarget = bone_name
        con.rest_length = pb.bone.length

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
        # Remove STRETCH_TO constraints from pose bones
        if armature_obj.pose:
            for pb in armature_obj.pose.bones:
                to_remove = [
                    c for c in pb.constraints
                    if c.type == "STRETCH_TO" and c.name.startswith("mmd_cloth")
                ]
                for c in to_remove:
                    pb.constraints.remove(c)

        # Delete all cloth mesh objects
        for obj in list(collection.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.collections.remove(collection)

    if "cloth_collection" in armature_obj:
        del armature_obj["cloth_collection"]
