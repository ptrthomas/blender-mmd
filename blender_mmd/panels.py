"""MMD4B UI panel — soft body deformation controls in the 3D Viewport N-panel."""

from __future__ import annotations

import json
import math

import bpy
from bpy.props import FloatProperty, PointerProperty


def _is_mmd_armature(obj) -> bool:
    return (
        obj is not None
        and obj.type == "ARMATURE"
        and obj.get("import_scale") is not None
    )


def _find_mmd_armature_for_panel(context) -> bpy.types.Object | None:
    """Find the relevant MMD armature for the panel.

    Works when active object is the armature itself, or a cage mesh
    parented to the armature.
    """
    obj = context.active_object
    if obj is None:
        return None
    if _is_mmd_armature(obj):
        return obj
    if obj.type == "MESH" and obj.parent and _is_mmd_armature(obj.parent):
        return obj.parent
    return None


def validate_bone_chain(pose_bones) -> tuple[bool, list, str]:
    """Validate that selected pose bones form a single parent→child chain.

    Returns:
        (valid, sorted_bones, message)
        sorted_bones is ordered root→tip by hierarchy depth.
    """
    if len(pose_bones) < 2:
        return False, [], "Select at least 2 bones"

    def depth(pb):
        d = 0
        p = pb.parent
        while p:
            d += 1
            p = p.parent
        return d

    sorted_bones = sorted(pose_bones, key=depth)
    names = {pb.name for pb in sorted_bones}

    # First bone's parent must NOT be in the selection (it's the anchor)
    if sorted_bones[0].parent and sorted_bones[0].parent.name in names:
        return False, sorted_bones, "Root bone's parent must not be selected"

    # Each subsequent bone must be a direct child of the previous one
    for i in range(1, len(sorted_bones)):
        if sorted_bones[i].parent is None:
            return False, sorted_bones, f"'{sorted_bones[i].name}' has no parent"
        if sorted_bones[i].parent.name != sorted_bones[i - 1].name:
            return (
                False,
                sorted_bones,
                f"'{sorted_bones[i].name}' is not a child of "
                f"'{sorted_bones[i - 1].name}'",
            )

    return True, sorted_bones, ""


def validate_bone_group(
    pose_bones, armature_obj
) -> tuple[bool, list[list[str]], list[str], str]:
    """Validate that selected bones form parallel chains for connected group cloth.

    Returns:
        (valid, chains, strut_names, message)
        chains: list of bone name lists, each root→tip, sorted by angle.
        strut_names: bone names belonging to the "Struts" bone collection.
    """
    if len(pose_bones) < 2:
        return False, [], [], "Need at least 2 bones"

    names = {pb.name for pb in pose_bones}
    pb_lookup = {pb.name: pb for pb in pose_bones}

    # Separate strut bones (members of "Struts" bone collection)
    strut_names: set[str] = set()
    struts_col = armature_obj.data.collections.get("Struts")
    if struts_col:
        for pb in pose_bones:
            if struts_col in pb.bone.collections.values():
                strut_names.add(pb.name)
    chain_names = names - strut_names

    if len(chain_names) < 2:
        return False, [], [], "Need at least 2 chain bones"

    # Find roots: chain bones whose parent is NOT in the chain selection
    roots = []
    for name in chain_names:
        pb = pb_lookup[name]
        if pb.parent is None or pb.parent.name not in chain_names:
            roots.append(pb)

    if len(roots) < 2:
        return False, [], [], "Need at least 2 chain roots for group mode"

    # Build chains from each root following children within the selection
    chains: list[list[str]] = []
    accounted: set[str] = set()
    for root in roots:
        chain = [root.name]
        accounted.add(root.name)
        current = root
        while True:
            children_in_sel = [
                c for c in current.children if c.name in chain_names
            ]
            if len(children_in_sel) == 0:
                break
            if len(children_in_sel) > 1:
                return (
                    False,
                    [],
                    [],
                    f"'{current.name}' branches in selection",
                )
            current = children_in_sel[0]
            chain.append(current.name)
            accounted.add(current.name)
        chains.append(chain)

    unaccounted = chain_names - accounted
    if unaccounted:
        return (
            False,
            [],
            [],
            f"Orphan bones: {', '.join(sorted(unaccounted))}",
        )

    # Sort chains by angle of root bone around armature center (XY plane)
    bones = armature_obj.data.bones

    def _root_angle(chain: list[str]) -> float:
        head = bones[chain[0]].head_local
        return math.atan2(head.x, -head.y)

    chains.sort(key=_root_angle)

    return True, chains, sorted(strut_names), ""


def _collision_mesh_poll(self, obj):
    return obj.type == "MESH"


def _get_panel_cage_list(armature_obj) -> list[dict]:
    """Get cage metadata list from armature for panel display."""
    raw = armature_obj.get("mmd_softbody_cages") if armature_obj else None
    if raw:
        return json.loads(raw)
    return []


def _count_vg_members(obj, vg_index: int) -> int:
    """Count vertices that belong to a vertex group."""
    count = 0
    for v in obj.data.vertices:
        for g in v.groups:
            if g.group == vg_index and g.weight > 0.001:
                count += 1
                break
    return count


class BLENDER_MMD_PT_softbody(bpy.types.Panel):
    """MMD4B — Soft body deformation panel."""

    bl_label = "MMD4B"
    bl_idname = "BLENDER_MMD_PT_softbody"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MMD4B"

    @classmethod
    def poll(cls, context):
        return _find_mmd_armature_for_panel(context) is not None

    def draw(self, context):
        layout = self.layout
        armature_obj = _find_mmd_armature_for_panel(context)
        obj = context.active_object

        # --- Physics Section ---
        if armature_obj:
            has_physics = armature_obj.get("physics_mode") is not None
            box = layout.box()
            box.label(text="Physics", icon="PHYSICS")
            if has_physics:
                mode = armature_obj.get("physics_mode", "none")
                box.label(text=f"Mode: {mode}")
                box.operator(
                    "blender_mmd.clear_physics",
                    text="Clear Physics",
                    icon="TRASH",
                )
            else:
                row = box.row(align=True)
                op = row.operator(
                    "blender_mmd.build_physics",
                    text="Build Rigid Body",
                    icon="RIGID_BODY",
                )
                op.mode = "rigid_body"

        # --- Convert Section (Pose Mode + selection) ---
        if context.mode == "POSE" and context.selected_pose_bones:
            selected = list(context.selected_pose_bones)

            # Try single chain first
            chain_ok, sorted_bones, chain_msg = validate_bone_chain(selected)
            # Try group if single chain fails
            group_ok, chains, struts, group_msg = (
                (False, [], [], "")
                if chain_ok
                else validate_bone_group(selected, armature_obj)
            )

            box = layout.box()
            if chain_ok:
                first = sorted_bones[0].name
                last = sorted_bones[-1].name
                pin_bone = (
                    sorted_bones[0].parent.name
                    if sorted_bones[0].parent
                    else sorted_bones[0].name
                )
                box.label(
                    text=f"Chain: {len(sorted_bones)} bones "
                    f"({first} \u2192 {last})"
                )
                box.label(text=f"Pin bone: {pin_bone}", icon="PINNED")
                box.prop(context.scene, "mmd4b_stiffness", text="Stiffness")
                box.prop(
                    context.scene, "mmd4b_collision_mesh", text="Collision"
                )
                box.operator(
                    "blender_mmd.generate_cage",
                    text="Generate Cage",
                    icon="MESH_CYLINDER",
                )
            elif group_ok:
                total = sum(len(c) for c in chains)
                depths = ", ".join(str(len(c)) for c in chains)
                box.label(
                    text=f"Group: {len(chains)} chains, {total} bones",
                    icon="MESH_CYLINDER",
                )
                box.label(text=f"Depths: {depths}")
                if struts:
                    box.label(text=f"Struts: {len(struts)}")
                box.prop(context.scene, "mmd4b_stiffness", text="Stiffness")
                box.prop(
                    context.scene, "mmd4b_collision_mesh", text="Collision"
                )
                box.label(text="Group mode: Phase 2", icon="INFO")
            else:
                box.label(
                    text=f"Selected: {len(selected)} bones", icon="ERROR"
                )
                box.label(text=chain_msg or group_msg)
        elif context.mode == "POSE":
            layout.label(text="Select bones to convert", icon="INFO")
        elif (
            context.mode == "EDIT_MESH"
            and obj
            and obj.type == "MESH"
            and obj.name.startswith("SB_Cage_")
        ):
            # Cage is in Edit Mode — show pin/unpin controls
            box = layout.box()
            box.label(text=f"Editing: {obj.name}", icon="MESH_CYLINDER")
            row = box.row(align=True)
            row.operator(
                "blender_mmd.pin_vertices", text="Pin", icon="PINNED"
            )
            row.operator(
                "blender_mmd.unpin_vertices", text="Unpin", icon="UNPINNED"
            )
            # Show pin count
            goal_vg = obj.vertex_groups.get("goal")
            if goal_vg:
                pin_count = _count_vg_members(obj, goal_vg.index)
                box.label(text=f"Pinned: {pin_count} vertices")
        else:
            layout.label(text="Enter Pose Mode to convert", icon="INFO")

        # --- Active Cages Section ---
        cages = _get_panel_cage_list(armature_obj)
        if cages:
            box = layout.box()
            box.label(text="Active Cages", icon="PHYSICS")
            for cage_info in cages:
                cage_name = cage_info["cage_name"]
                row = box.row(align=True)
                # Clickable label to select bones
                op = row.operator(
                    "blender_mmd.select_cage_bones",
                    text=cage_name,
                    icon="BONE_DATA",
                )
                op.cage_name = cage_name
                # Rebind button
                op = row.operator(
                    "blender_mmd.rebind_surface_deform",
                    text="",
                    icon="FILE_REFRESH",
                )
                op.cage_name = cage_name
                # Remove button
                op = row.operator(
                    "blender_mmd.remove_cage",
                    text="",
                    icon="X",
                )
                op.cage_name = cage_name
                # Info sub-row
                sub = box.row()
                n_bones = len(cage_info.get("bone_names", []))
                n_verts = cage_info.get("affected_verts", 0)
                sub.label(
                    text=f"  {n_bones} bones, {n_verts} affected verts",
                )

            # Bottom buttons
            row = box.row(align=True)
            row.operator(
                "blender_mmd.reset_soft_body",
                text="Reset Sims",
                icon="FILE_REFRESH",
            )
            row.operator(
                "blender_mmd.clear_all_cages",
                text="Clear All",
                icon="TRASH",
            )


_classes = (BLENDER_MMD_PT_softbody,)


def register():
    bpy.types.Scene.mmd4b_stiffness = FloatProperty(
        name="Stiffness",
        description="Soft body stiffness (0=floppy, 1=stiff)",
        default=0.7,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    bpy.types.Scene.mmd4b_collision_mesh = PointerProperty(
        name="Collision Mesh",
        type=bpy.types.Object,
        poll=_collision_mesh_poll,
    )
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.mmd4b_collision_mesh
    del bpy.types.Scene.mmd4b_stiffness
