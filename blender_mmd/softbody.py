"""Soft Body cage generation and Surface Deform setup for MMD4B.

Generates a low-poly cage tube along a bone chain, applies Soft Body physics
to the cage, and binds the visible mesh via Surface Deform. Affected vertices
are limited to those weighted to the selected bones.

Blender-specific imports (bpy, bmesh, mathutils) are deferred to function
bodies so that pure-Python utility functions remain importable for unit tests.
"""

from __future__ import annotations

import json
import logging
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import bpy

log = logging.getLogger("blender_mmd")

# Cage geometry
_HEX_SIDES = 8
_RADIUS_MARGIN = 1.05  # 5% oversizing — percentile radius already excludes outliers


def generate_cage(
    armature_obj,
    spine_names: list[str],
    stiffness: float = 0.7,
    all_bone_names: list[str] | None = None,
) -> tuple[object, int]:
    """Generate a Soft Body cage tube along a bone chain or tree spine.

    Automatically handles rigid body integration:
    - Adds COLLISION modifiers to static RBs (head, body) for cloth collision
    - Removes dynamic RBs on cage bones (replaced by cloth sim)

    Args:
        armature_obj: The MMD armature object.
        spine_names: Ordered root→tip bone names for the centerline.
        stiffness: 0.0–1.0 stiffness slider value.
        all_bone_names: All bone names for vertex collection (tree mode).
            If None, falls back to spine_names (linear chain mode).

    Returns:
        Tuple of (cage_object, removed_rb_count).
    """
    import bmesh
    import bpy
    from mathutils import Matrix, Vector

    arm_data = armature_obj.data
    collect_names = all_bone_names if all_bone_names is not None else spine_names

    # --- 1. Find the visible mesh parented to this armature ---
    mesh_obj = _find_mesh_child(armature_obj)
    if mesh_obj is None:
        raise RuntimeError("No mesh child found on armature")

    # --- 2. Collect affected vertices (weighted to selected bones) ---
    affected = _collect_affected_vertices(mesh_obj, collect_names)
    if not affected:
        raise RuntimeError("No vertices weighted to selected bones")
    affected_indices = [vi for vi, _w in affected]

    # --- 3. Determine pin bone (parent of root selected bone) ---
    root_bone = arm_data.bones[spine_names[0]]
    pin_bone_name = root_bone.parent.name if root_bone.parent else spine_names[0]

    # --- 4. Build centerline from spine head→tail positions ---
    # Use rest-pose (edit bone) positions in armature local space.
    # Prepend pin bone head so the cage extends into the anchor region —
    # this ensures the pinned rings sit inside the parent bone, creating
    # a smooth transition instead of a gap at the attachment point.
    centerline = []
    pin_bone = arm_data.bones[pin_bone_name]
    root_bone_obj = arm_data.bones[spine_names[0]]
    if pin_bone_name != spine_names[0]:
        centerline.append(Vector(pin_bone.head_local))
    for name in spine_names:
        bone = arm_data.bones[name]
        centerline.append(Vector(bone.head_local))
    # Add tail of last bone
    last_bone = arm_data.bones[spine_names[-1]]
    centerline.append(Vector(last_bone.tail_local))

    # --- 5. Compute per-ring cage radius from affected vertex positions ---
    ring_radii = _compute_per_ring_radius(
        mesh_obj, affected, centerline, armature_obj
    )
    ring_radii = [max(r * _RADIUS_MARGIN, 0.01) for r in ring_radii]

    # --- 6. Build cage mesh ---
    cage_obj = _build_cage_mesh(
        armature_obj, spine_names, centerline, ring_radii, pin_bone_name
    )

    # --- 7. Apply Armature modifier (before Cloth, so pinned verts follow bone) ---
    _apply_armature_modifier(cage_obj, armature_obj, pin_bone_name)

    # --- 8. Auto-detect static RBs for collision ---
    _setup_collision_from_rigid_bodies(armature_obj)

    # --- 9. Apply Cloth modifier on cage ---
    _apply_cloth(cage_obj, stiffness)

    # --- 10. Create affected vertex group on visible mesh + Surface Deform ---
    sd_mod_name = _apply_surface_deform(mesh_obj, cage_obj, affected_indices)

    # --- 11. Store metadata on armature ---
    _store_cage_metadata(
        armature_obj, cage_obj.name, collect_names, pin_bone_name,
        len(affected_indices), sd_mod_name, mesh_obj.name,
    )

    # --- 12. Place cage in Soft Body subcollection ---
    _move_to_softbody_collection(armature_obj, cage_obj)

    # --- 13. Remove dynamic RBs on cage bones (cloth replaces them) ---
    from .physics import remove_rigid_bodies_for_bones
    removed_count = remove_rigid_bodies_for_bones(armature_obj, set(collect_names))

    log.info(
        "Generated cage '%s': %d bones (%d spine), %d affected verts, "
        "radius=%.4f–%.4f, stiffness=%.2f, pin=%s, removed %d rigid bodies",
        cage_obj.name, len(collect_names), len(spine_names),
        len(affected_indices),
        min(ring_radii), max(ring_radii), stiffness, pin_bone_name,
        removed_count,
    )

    return cage_obj, removed_count


def remove_cage(armature_obj, cage_name: str) -> None:
    """Remove a specific cage and its Surface Deform modifier."""
    import bpy

    # Read metadata
    cages = _get_cage_list(armature_obj)
    cage_info = None
    for c in cages:
        if c["cage_name"] == cage_name:
            cage_info = c
            break

    if cage_info is None:
        log.warning("Cage '%s' not found in metadata", cage_name)
        return

    # Remove Surface Deform modifier from mesh
    mesh_obj = bpy.data.objects.get(cage_info.get("mesh_name", ""))
    if mesh_obj:
        sd_name = cage_info.get("sd_modifier_name", "")
        if sd_name and sd_name in mesh_obj.modifiers:
            mesh_obj.modifiers.remove(mesh_obj.modifiers[sd_name])
        # Remove affected vertex group
        vg_name = f"sb_affected_{cage_name}"
        vg = mesh_obj.vertex_groups.get(vg_name)
        if vg:
            mesh_obj.vertex_groups.remove(vg)

    # Remove cage object
    cage_obj = bpy.data.objects.get(cage_name)
    if cage_obj:
        bpy.data.objects.remove(cage_obj, do_unlink=True)

    # Update metadata
    cages = [c for c in cages if c["cage_name"] != cage_name]
    _set_cage_list(armature_obj, cages)

    log.info("Removed cage '%s'", cage_name)


def clear_all_cages(armature_obj) -> None:
    """Remove all soft body cages for this armature."""
    cages = _get_cage_list(armature_obj)
    for c in list(cages):
        remove_cage(armature_obj, c["cage_name"])

    # Clean up the Soft Body subcollection
    _remove_softbody_collection(armature_obj)

    # Remove metadata key
    if "mmd_softbody_cages" in armature_obj:
        del armature_obj["mmd_softbody_cages"]

    log.info("Cleared all soft body cages")


def reset_caches(armature_obj) -> None:
    """Reset Cloth caches by toggling modifier off/on and returning to frame 1."""
    import bpy

    cages = _get_cage_list(armature_obj)
    for c in cages:
        cage_obj = bpy.data.objects.get(c["cage_name"])
        if cage_obj is None:
            continue
        for mod in cage_obj.modifiers:
            if mod.type == "CLOTH":
                mod.show_viewport = False
                mod.show_viewport = True

    bpy.context.scene.frame_set(bpy.context.scene.frame_start)
    log.info("Reset cloth caches, frame set to %d", bpy.context.scene.frame_start)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _find_mesh_child(armature_obj):
    """Find the primary mesh child of the armature."""
    mesh_children = [c for c in armature_obj.children if c.type == "MESH"]
    if not mesh_children:
        return None
    # Prefer the largest mesh (most vertices)
    return max(mesh_children, key=lambda m: len(m.data.vertices))


def _collect_affected_vertices(
    mesh_obj, bone_names: list[str]
) -> list[tuple[int, float]]:
    """Collect vertices weighted to any of the given bone groups.

    Returns:
        List of (vertex_index, max_weight) tuples, sorted by index.
        max_weight is the highest weight across all matching bone groups.
    """
    # vertex_index → max weight across all bone groups
    weight_map: dict[int, float] = {}
    for name in bone_names:
        vg = mesh_obj.vertex_groups.get(name)
        if vg is None:
            continue
        vg_index = vg.index
        for v in mesh_obj.data.vertices:
            for g in v.groups:
                if g.group == vg_index and g.weight > 0.001:
                    cur = weight_map.get(v.index, 0.0)
                    if g.weight > cur:
                        weight_map[v.index] = g.weight
                    break
    return sorted(weight_map.items(), key=lambda x: x[0])


def _compute_per_ring_radius(
    mesh_obj, affected: list[tuple[int, float]], centerline: list, armature_obj
) -> list[float]:
    """Compute per-ring radius using weighted 85th percentile of perpendicular distance.

    For each ring slab, collects (distance, weight) pairs from affected vertices,
    sorts by distance, and finds the distance at which 85% of total slab weight
    is reached. This produces a tighter cage that encloses high-influence geometry
    while ignoring low-weight outlier strands.
    """
    from mathutils import Vector

    _WEIGHT_PERCENTILE = 0.85

    mesh_to_arm = armature_obj.matrix_world.inverted() @ mesh_obj.matrix_world
    verts = mesh_obj.data.vertices
    n_rings = len(centerline)

    # Per-ring list of (perpendicular_distance, weight) pairs
    ring_samples: list[list[tuple[float, float]]] = [[] for _ in range(n_rings)]

    for vi, w in affected:
        v_pos = mesh_to_arm @ verts[vi].co
        # Find closest segment and attribute to nearest ring
        best_seg = 0
        best_t = 0.0
        best_perp = float("inf")
        for i in range(n_rings - 1):
            a, b = centerline[i], centerline[i + 1]
            seg = b - a
            seg_len_sq = seg.length_squared
            if seg_len_sq < 1e-12:
                perp = (v_pos - a).length
                t = 0.0
            else:
                t = max(0.0, min(1.0, (v_pos - a).dot(seg) / seg_len_sq))
                closest = a + seg * t
                perp = (v_pos - closest).length
            if perp < best_perp:
                best_perp = perp
                best_seg = i
                best_t = t
        ring_idx = best_seg if best_t < 0.5 else best_seg + 1
        ring_samples[ring_idx].append((best_perp, w))

    # Compute weighted percentile radius per ring
    ring_radii = [0.0] * n_rings
    for i in range(n_rings):
        samples = ring_samples[i]
        if not samples:
            continue
        # Sort by distance ascending
        samples.sort(key=lambda x: x[0])
        total_weight = sum(w for _, w in samples)
        if total_weight < 1e-9:
            continue
        threshold = total_weight * _WEIGHT_PERCENTILE
        accum = 0.0
        for dist, w in samples:
            accum += w
            if accum >= threshold:
                ring_radii[i] = dist
                break
        else:
            # All accumulated but never crossed threshold (shouldn't happen)
            ring_radii[i] = samples[-1][0]

    # Fill in any rings with zero radius by interpolating from neighbours
    for i in range(n_rings):
        if ring_radii[i] < 1e-6:
            left = right = None
            for j in range(i - 1, -1, -1):
                if ring_radii[j] > 1e-6:
                    left = j
                    break
            for j in range(i + 1, n_rings):
                if ring_radii[j] > 1e-6:
                    right = j
                    break
            if left is not None and right is not None:
                t = (i - left) / (right - left)
                ring_radii[i] = ring_radii[left] * (1 - t) + ring_radii[right] * t
            elif left is not None:
                ring_radii[i] = ring_radii[left]
            elif right is not None:
                ring_radii[i] = ring_radii[right]

    return ring_radii


def _build_cage_mesh(
    armature_obj, bone_names: list[str], centerline: list,
    ring_radii: list[float], pin_bone_name: str,
    subdivs_base: int = 3,
) -> object:
    """Build the tube cage mesh along the centerline with gradient density.

    Uses parallel transport to propagate ring orientation along the curve,
    preventing twists that occur with independent per-ring to_track_quat().
    Each ring gets its own radius from ring_radii for a tighter fit.

    Gradient subdivision inserts extra rings between bone joints — more near
    the pinned root (stability) and fewer toward the free tip (freedom).
    """
    import bmesh
    import bpy
    from mathutils import Matrix, Quaternion, Vector

    bm = bmesh.new()

    # --- Gradient subdivision: expand centerline with extra rings ---
    n_segments = len(centerline) - 1
    subdivs_per_seg = []
    for i in range(n_segments):
        t = i / max(1, n_segments - 1)
        s = round(subdivs_base * (1.0 - t * 0.67))
        subdivs_per_seg.append(max(1, s))

    expanded_cl = [Vector(centerline[0])]
    expanded_radii = [ring_radii[0]]
    for i in range(n_segments):
        n_sub = subdivs_per_seg[i]
        for j in range(1, n_sub + 1):
            t = j / (n_sub + 1)
            pos = centerline[i].lerp(centerline[i + 1], t)
            r = ring_radii[i] * (1 - t) + ring_radii[i + 1] * t
            expanded_cl.append(pos)
            expanded_radii.append(r)
        expanded_cl.append(Vector(centerline[i + 1]))
        expanded_radii.append(ring_radii[i + 1])

    n_rings = len(expanded_cl)
    ring_verts = []  # list of lists, one per ring

    # --- Parallel transport: compute a consistent normal/binormal frame ---
    # Start with first segment direction and an arbitrary perpendicular
    tangents = []
    for i in range(n_rings):
        if i < n_rings - 1:
            t = (expanded_cl[i + 1] - expanded_cl[i]).normalized()
        else:
            t = (expanded_cl[i] - expanded_cl[i - 1]).normalized()
        tangents.append(t)

    # Initial frame: pick a stable perpendicular to first tangent
    t0 = tangents[0]
    up = Vector((0, 0, 1))
    if abs(t0.dot(up)) > 0.99:
        up = Vector((1, 0, 0))
    normal = t0.cross(up).normalized()
    binormal = t0.cross(normal).normalized()

    frames = [(normal.copy(), binormal.copy())]

    # Propagate frame along curve using parallel transport
    for i in range(1, n_rings):
        t_prev = tangents[i - 1]
        t_curr = tangents[i]
        # Rotation axis between consecutive tangents
        axis = t_prev.cross(t_curr)
        if axis.length > 1e-8:
            axis.normalize()
            angle = t_prev.angle(t_curr)
            rot = Quaternion(axis, angle)
            normal = rot @ normal
            binormal = rot @ binormal
        # Re-orthogonalize to prevent drift
        binormal = t_curr.cross(normal).normalized()
        normal = binormal.cross(t_curr).normalized()
        frames.append((normal.copy(), binormal.copy()))

    for ring_idx in range(n_rings):
        center = expanded_cl[ring_idx]
        r = expanded_radii[ring_idx]
        n_vec, b_vec = frames[ring_idx]

        # Create ring vertices using the parallel-transported frame
        ring = []
        for i in range(_HEX_SIDES):
            angle = 2 * math.pi * i / _HEX_SIDES
            offset = n_vec * (math.cos(angle) * r) + b_vec * (math.sin(angle) * r)
            bv = bm.verts.new(center + offset)
            ring.append(bv)
        ring_verts.append(ring)

    bm.verts.ensure_lookup_table()

    # Create faces between adjacent rings
    for r in range(n_rings - 1):
        for i in range(_HEX_SIDES):
            i_next = (i + 1) % _HEX_SIDES
            v1 = ring_verts[r][i]
            v2 = ring_verts[r][i_next]
            v3 = ring_verts[r + 1][i_next]
            v4 = ring_verts[r + 1][i]
            bm.faces.new((v1, v2, v3, v4))

    # Cap both ends (closed mesh required for Surface Deform binding)
    bm.faces.new(ring_verts[0])   # top cap
    bm.faces.new(ring_verts[-1])  # bottom cap

    # Triangulate — curved tube produces non-planar quads which prevent
    # Surface Deform binding.  Triangles are always planar.
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.normal_update()

    # Create cage mesh object
    cage_name = f"SB_Cage_{bone_names[0]}"
    mesh_data = bpy.data.meshes.new(cage_name)
    bm.to_mesh(mesh_data)
    bm.free()

    cage_obj = bpy.data.objects.new(cage_name, mesh_data)

    # Place cage in armature space
    cage_obj.matrix_world = armature_obj.matrix_world.copy()

    # Parent cage to armature object
    cage_obj.parent = armature_obj
    cage_obj.parent_type = "OBJECT"

    # Link to scene temporarily (will be moved to collection later)
    bpy.context.scene.collection.objects.link(cage_obj)

    # --- Vertex groups ---

    # Goal vertex group with gradient weights for smooth pin transition.
    # Ring 0 = 1.0 (fully pinned), ring 1 = 0.8, ring 2 = 0.5, rest = 0.0
    _PIN_WEIGHTS = [1.0, 0.8, 0.5]
    goal_vg = cage_obj.vertex_groups.new(name="goal")
    for ring_idx, weight in enumerate(_PIN_WEIGHTS):
        if ring_idx >= n_rings:
            break
        indices = list(range(ring_idx * _HEX_SIDES, (ring_idx + 1) * _HEX_SIDES))
        goal_vg.add(indices, weight, "REPLACE")

    # Pin bone vertex group (for Armature modifier) — includes all pinned rings
    # so the Armature gives them target positions that goal weights blend with
    pin_vg = cage_obj.vertex_groups.new(name=pin_bone_name)
    n_pinned_rings = min(len(_PIN_WEIGHTS), n_rings)
    pinned_indices = list(range(n_pinned_rings * _HEX_SIDES))
    pin_vg.add(pinned_indices, 1.0, "REPLACE")

    log.info(
        "Cage subdivisions: %s (total %d rings from %d segments)",
        subdivs_per_seg, n_rings, n_segments,
    )

    return cage_obj


def _apply_armature_modifier(cage_obj, armature_obj, pin_bone_name: str) -> None:
    """Apply Armature modifier on cage so pinned ring follows pin bone.

    Must be called BEFORE _apply_cloth() so it evaluates first in the stack.
    """
    arm_mod = cage_obj.modifiers.new("Armature", "ARMATURE")
    arm_mod.object = armature_obj
    arm_mod.use_vertex_groups = True


def _setup_collision_from_rigid_bodies(armature_obj) -> None:
    """Add COLLISION modifiers to static rigid bodies for cloth collision.

    Blender's Cloth solver automatically checks all scene objects with
    COLLISION modifiers, so we just need to tag the static RBs.
    """
    from .physics import get_static_collision_objects

    static_objs = get_static_collision_objects(armature_obj)
    if not static_objs:
        return

    count = 0
    for obj in static_objs:
        if not any(m.type == "COLLISION" for m in obj.modifiers):
            obj.modifiers.new("Collision", "COLLISION")
            count += 1

    if count > 0:
        log.info("Added COLLISION modifiers to %d static rigid bodies", count)


def _apply_cloth(cage_obj, stiffness: float) -> None:
    """Apply Cloth modifier with stiffness-mapped parameters.

    Cloth respects the Armature modifier output for pinned vertices (unlike
    Soft Body which only anchors to rest shape), so pinned ring follows the
    bone correctly during animation.

    Collision is handled automatically — Blender's Cloth solver checks all
    scene objects with COLLISION modifiers.

    Stiffness mapping (0.0–1.0 slider → Cloth parameters):
        tension/compression: 5 + stiffness * 45   (range 5–50)
        bending:             0.1 + stiffness * 4.9 (range 0.1–5)
        damping:             2 + stiffness * 13     (range 2–15)
    """
    cloth_mod = cage_obj.modifiers.new("Cloth", "CLOTH")
    cs = cloth_mod.settings

    # Pin group — weight 1.0 = fully pinned, 0.0 = free
    cs.vertex_group_mass = "goal"

    # Structural stiffness
    cs.tension_stiffness = 5.0 + stiffness * 45.0
    cs.compression_stiffness = 5.0 + stiffness * 45.0
    cs.bending_stiffness = 0.1 + stiffness * 4.9
    cs.tension_damping = 2.0 + stiffness * 13.0
    cs.compression_damping = 2.0 + stiffness * 13.0
    cs.bending_damping = 0.5

    # General
    cs.quality = 10
    cs.mass = 0.08

    # Pressure — resists tube collapse/squashing without internal geometry
    cs.use_pressure = True
    cs.uniform_pressure_force = 2.0
    cs.use_pressure_volume = True

    # Internal springs — virtual springs between opposite verts for volume
    cs.use_internal_springs = True
    cs.internal_spring_max_diversion = 0.785  # ~45 degrees

    # Extend cache well beyond typical scene length so it doesn't cut short
    # if VMD is imported after cage generation.
    import bpy
    pc = cloth_mod.point_cache
    pc.frame_end = max(bpy.context.scene.frame_end, 10000)

    # Enable cloth collision — Blender solver checks all COLLISION objects
    cloth_mod.collision_settings.use_collision = True



def _apply_surface_deform(
    mesh_obj, cage_obj, affected_indices: list[int]
) -> str:
    """Add Surface Deform modifier on visible mesh bound to cage.

    Returns the modifier name.
    """
    import bpy

    # Create affected vertex group on visible mesh
    vg_name = f"sb_affected_{cage_obj.name}"
    vg = mesh_obj.vertex_groups.get(vg_name)
    if vg:
        mesh_obj.vertex_groups.remove(vg)
    vg = mesh_obj.vertex_groups.new(name=vg_name)
    vg.add(affected_indices, 1.0, "REPLACE")

    # Add Surface Deform modifier
    sd_name = f"SD_{cage_obj.name}"
    sd_mod = mesh_obj.modifiers.new(sd_name, "SURFACE_DEFORM")
    sd_mod.target = cage_obj
    sd_mod.vertex_group = vg_name

    # Bind Surface Deform
    with bpy.context.temp_override(object=mesh_obj, active_object=mesh_obj):
        bpy.ops.object.surfacedeform_bind(modifier=sd_name)

    return sd_name


def _store_cage_metadata(
    armature_obj, cage_name: str, bone_names: list[str],
    pin_bone_name: str, affected_count: int, sd_modifier_name: str,
    mesh_name: str,
) -> None:
    """Store cage info as JSON custom property on armature."""
    cages = _get_cage_list(armature_obj)
    cages.append({
        "cage_name": cage_name,
        "bone_names": bone_names,
        "pin_bone": pin_bone_name,
        "affected_verts": affected_count,
        "sd_modifier_name": sd_modifier_name,
        "mesh_name": mesh_name,
    })
    _set_cage_list(armature_obj, cages)


def _get_cage_list(armature_obj) -> list[dict]:
    """Get the list of cage metadata dicts from the armature."""
    raw = armature_obj.get("mmd_softbody_cages")
    if raw:
        return json.loads(raw)
    return []


def _set_cage_list(armature_obj, cages: list[dict]) -> None:
    """Set the list of cage metadata dicts on the armature."""
    armature_obj["mmd_softbody_cages"] = json.dumps(cages)


def _move_to_softbody_collection(armature_obj, cage_obj) -> None:
    """Move cage object into a 'Soft Body' subcollection under the armature's collection."""
    import bpy

    # Find or create the Soft Body collection
    col_name = f"{armature_obj.name}_SoftBody"
    collection = bpy.data.collections.get(col_name)
    if collection is None:
        collection = bpy.data.collections.new(col_name)
        bpy.context.scene.collection.children.link(collection)
        armature_obj["softbody_collection"] = col_name

    # Move cage from scene collection to Soft Body collection
    collection.objects.link(cage_obj)
    for col in list(cage_obj.users_collection):
        if col != collection:
            col.objects.unlink(cage_obj)


def _remove_softbody_collection(armature_obj) -> None:
    """Remove the Soft Body subcollection if empty."""
    import bpy

    col_name = armature_obj.get("softbody_collection")
    if not col_name:
        return

    collection = bpy.data.collections.get(col_name)
    if collection is None:
        return

    # Remove any remaining objects
    for obj in list(collection.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    bpy.data.collections.remove(collection)

    if "softbody_collection" in armature_obj:
        del armature_obj["softbody_collection"]
