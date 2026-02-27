"""MMD4B UI panel — cloth conversion controls in the 3D Viewport N-panel."""

from __future__ import annotations

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
            valid, sorted_bones, message = validate_bone_chain(selected)

            box = layout.box()
            if valid:
                first = sorted_bones[0].name
                last = sorted_bones[-1].name
                box.label(
                    text=f"Selected: {len(sorted_bones)} bones "
                    f"({first} \u2192 {last})"
                )

                box.prop(context.scene, "mmd4b_preset", text="Preset")
                box.prop(context.scene, "mmd4b_collision_mesh", text="Collision")

                box.operator(
                    "blender_mmd.convert_selection_to_cloth",
                    text="Convert to Cloth",
                    icon="MOD_CLOTH",
                )
            else:
                box.label(
                    text=f"Selected: {len(selected)} bones", icon="ERROR"
                )
                box.label(text=message)
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
                    row.label(text=f"{cloth_obj.name}  ({count} bones)")
                    op = row.operator(
                        "blender_mmd.remove_cloth_sim",
                        text="",
                        icon="X",
                    )
                    op.cloth_object_name = cloth_obj.name

        if not has_cloths:
            layout.label(text="None")

        # Clear All button
        if has_cloths:
            layout.operator(
                "blender_mmd.clear_cloth",
                text="Clear All Cloth",
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
