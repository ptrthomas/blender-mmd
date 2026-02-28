"""MMD4B UI panel in the 3D Viewport N-panel."""

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


def _get_ik_chains(armature_obj) -> list[tuple[str, str, bool]]:
    """Return list of (target_bone_name, display_name, is_enabled) for all IK chains."""
    chains = []
    for pb in armature_obj.pose.bones:
        for c in pb.constraints:
            if c.type == "IK" and c.subtarget:
                target_bone = armature_obj.data.bones.get(c.subtarget)
                if target_bone:
                    display = target_bone.name
                    enabled = c.influence > 0.5
                    chains.append((c.subtarget, display, enabled))
    chains.sort(key=lambda x: x[1])
    return chains


class BLENDER_MMD_PT_physics(bpy.types.Panel):
    """MMD4B — rigid body physics controls."""

    bl_label = "Physics"
    bl_idname = "BLENDER_MMD_PT_physics"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MMD4B"
    bl_parent_id = "BLENDER_MMD_PT_main"

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


class BLENDER_MMD_PT_ik_toggle(bpy.types.Panel):
    """MMD4B — IK chain toggles."""

    bl_label = "IK Toggle"
    bl_idname = "BLENDER_MMD_PT_ik_toggle"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MMD4B"
    bl_parent_id = "BLENDER_MMD_PT_main"

    @classmethod
    def poll(cls, context):
        return _find_mmd_armature(context) is not None

    def draw(self, context):
        layout = self.layout
        armature_obj = _find_mmd_armature(context)

        chains = _get_ik_chains(armature_obj)
        if not chains:
            layout.label(text="No IK chains", icon="INFO")
            return

        # All On / All Off buttons
        row = layout.row(align=True)
        op = row.operator("blender_mmd.toggle_all_ik", text="All On", icon="CHECKBOX_HLT")
        op.enable = True
        op = row.operator("blender_mmd.toggle_all_ik", text="All Off", icon="CHECKBOX_DEHLT")
        op.enable = False

        # Per-chain toggles
        col = layout.column(align=True)
        for target_name, display_name, enabled in chains:
            icon = "CHECKBOX_HLT" if enabled else "CHECKBOX_DEHLT"
            op = col.operator(
                "blender_mmd.toggle_ik",
                text=display_name,
                icon=icon,
                depress=enabled,
            )
            op.target_bone = target_name


class BLENDER_MMD_PT_main(bpy.types.Panel):
    """MMD4B — main panel container."""

    bl_label = "MMD4B"
    bl_idname = "BLENDER_MMD_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MMD4B"

    @classmethod
    def poll(cls, context):
        return _find_mmd_armature(context) is not None

    def draw(self, context):
        armature_obj = _find_mmd_armature(context)
        self.layout.label(text=armature_obj.name, icon="ARMATURE_DATA")


_classes = (
    BLENDER_MMD_PT_main,
    BLENDER_MMD_PT_physics,
    BLENDER_MMD_PT_ik_toggle,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
