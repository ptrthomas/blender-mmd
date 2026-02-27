"""MMD4B UI panel — cloth conversion controls in the 3D Viewport N-panel."""

from __future__ import annotations

import math

import bpy
from bpy.props import EnumProperty, PointerProperty


def _is_mmd_armature(obj) -> bool:
    return (
        obj is not None
        and obj.type == "ARMATURE"
        and obj.get("import_scale") is not None
    )


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


class BLENDER_MMD_PT_cloth(bpy.types.Panel):
    """MMD4B — Cloth conversion panel."""

    bl_label = "MMD4B"
    bl_idname = "BLENDER_MMD_PT_cloth"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MMD4B"

    @classmethod
    def poll(cls, context):
        return _is_mmd_armature(context.active_object)

    def draw(self, context):
        layout = self.layout
        obj = context.active_object

        # --- Convert Section (Pose Mode + selection) ---
        if context.mode == "POSE" and context.selected_pose_bones:
            selected = list(context.selected_pose_bones)

            # Try single chain first (Phase 1)
            chain_ok, sorted_bones, chain_msg = validate_bone_chain(selected)
            # Try group (Phase 2) if single chain fails
            group_ok, chains, struts, group_msg = (
                (False, [], [], "")
                if chain_ok
                else validate_bone_group(selected, obj)
            )

            box = layout.box()
            if chain_ok:
                first = sorted_bones[0].name
                last = sorted_bones[-1].name
                box.label(
                    text=f"Chain: {len(sorted_bones)} bones "
                    f"({first} \u2192 {last})"
                )
                box.prop(context.scene, "mmd4b_preset", text="Preset")
                box.prop(context.scene, "mmd4b_collision_mesh", text="Collision")
                box.operator(
                    "blender_mmd.convert_selection_to_cloth",
                    text="Convert to Cloth",
                    icon="MOD_CLOTH",
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
                box.prop(context.scene, "mmd4b_preset", text="Preset")
                box.prop(context.scene, "mmd4b_collision_mesh", text="Collision")
                box.operator(
                    "blender_mmd.convert_group_to_cloth",
                    text="Convert Group to Cloth",
                    icon="MOD_CLOTH",
                )
            else:
                box.label(
                    text=f"Selected: {len(selected)} bones", icon="ERROR"
                )
                box.label(text=chain_msg or group_msg)
        elif context.mode == "POSE":
            layout.label(text="Select bones to convert", icon="INFO")
        else:
            layout.label(text="Enter Pose Mode to convert", icon="INFO")

        # --- Active Cloth Sims ---
        layout.separator()
        layout.label(text="Active Cloth Sims", icon="MOD_CLOTH")

        col_name = obj.get("cloth_collection")
        has_cloths = False
        if col_name:
            collection = bpy.data.collections.get(col_name)
            if collection and collection.objects:
                has_cloths = True
                for cloth_obj in collection.objects:
                    row = layout.row(align=True)
                    bone_names = cloth_obj.get("mmd_bone_names", "")
                    count = len(bone_names.split(",")) if bone_names else "?"
                    # Clickable label to select bones
                    sel_op = row.operator(
                        "blender_mmd.select_cloth_bones",
                        text=f"{cloth_obj.name}  ({count} bones)",
                        icon="BONE_DATA",
                    )
                    sel_op.cloth_object_name = cloth_obj.name
                    op = row.operator(
                        "blender_mmd.remove_cloth_sim",
                        text="",
                        icon="X",
                    )
                    op.cloth_object_name = cloth_obj.name

        if not has_cloths:
            layout.label(text="None")

        # Reset / Clear buttons
        if has_cloths:
            row = layout.row(align=True)
            row.operator(
                "blender_mmd.reset_cloth_sims",
                text="Reset Sims",
                icon="FILE_REFRESH",
            )
            row.operator(
                "blender_mmd.clear_cloth",
                text="Clear All",
                icon="TRASH",
            )


_classes = (BLENDER_MMD_PT_cloth,)


def register():
    bpy.types.Scene.mmd4b_preset = EnumProperty(
        name="Preset",
        items=[
            ("hair", "Hair", "Stiff strands (hair, ties)"),
            ("cotton", "Cotton", "General fabric (skirts, capes)"),
            ("silk", "Silk", "Light flowing fabric"),
        ],
        default="hair",
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
    del bpy.types.Scene.mmd4b_preset
