"""MMD4B Physics UI panel in the 3D Viewport N-panel."""

from __future__ import annotations

import bpy


def _is_mmd_armature(obj) -> bool:
    return (
        obj is not None
        and obj.type == "ARMATURE"
        and obj.get("import_scale") is not None
    )


def _find_mmd_armature(context) -> bpy.types.Object | None:
    """Find the relevant MMD armature for the panel."""
    obj = context.active_object
    if obj is None:
        return None
    if _is_mmd_armature(obj):
        return obj
    if obj.parent and _is_mmd_armature(obj.parent):
        return obj.parent
    return None


class BLENDER_MMD_PT_physics(bpy.types.Panel):
    """MMD4B — rigid body physics controls."""

    bl_label = "MMD4B"
    bl_idname = "BLENDER_MMD_PT_physics"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MMD4B"

    @classmethod
    def poll(cls, context):
        return _find_mmd_armature(context) is not None

    def draw(self, context):
        layout = self.layout
        armature_obj = _find_mmd_armature(context)

        has_physics = armature_obj.get("physics_collection") is not None

        if has_physics:
            col_name = armature_obj.get("physics_collection", "")
            col = bpy.data.collections.get(col_name) if col_name else None
            rb_count = 0
            if col:
                rb_col = col.children.get("Rigid Bodies")
                if rb_col:
                    rb_count = len(rb_col.objects)
            layout.label(text=f"Active: {rb_count} rigid bodies", icon="PHYSICS")
            row = layout.row(align=True)
            op = row.operator(
                "blender_mmd.build_physics",
                text="Rebuild",
                icon="FILE_REFRESH",
            )
            op.mode = "rigid_body"
            row.operator(
                "blender_mmd.clear_physics",
                text="Clear",
                icon="TRASH",
            )
        else:
            layout.label(text="No physics", icon="INFO")
            op = layout.operator(
                "blender_mmd.build_physics",
                text="Build Rigid Body",
                icon="PHYSICS",
            )
            op.mode = "rigid_body"


_classes = (BLENDER_MMD_PT_physics,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
