"""MMD4B UI panel in the 3D Viewport N-panel."""

from __future__ import annotations

import json

import bpy

from .helpers import find_mmd_armature


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


def _get_physics_chains(armature_obj) -> list[dict]:
    """Return stored physics chain data from armature."""
    chains_json = armature_obj.get("mmd_physics_chains")
    if not chains_json:
        return []
    return json.loads(chains_json)


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
        return find_mmd_armature(context) is not None

    def draw(self, context):
        layout = self.layout
        armature_obj = find_mmd_armature(context)

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
            row.operator(
                "blender_mmd.reset_physics",
                text="Reset",
                icon="FILE_REFRESH",
            )
            row.operator(
                "blender_mmd.clear_physics",
                text="Remove",
                icon="TRASH",
            )

            # Per-chain list with select and remove buttons
            chains = _get_physics_chains(armature_obj)
            if chains:
                box = layout.box()
                for i, chain in enumerate(chains):
                    row = box.row(align=True)
                    name = chain.get("name", f"Chain {i}")
                    group = chain.get("group", "other")
                    n_bodies = len(chain.get("rigid_indices", []))
                    op = row.operator(
                        "blender_mmd.select_chain",
                        text=f"{name}  ({group}, {n_bodies})",
                        icon="LINKED",
                    )
                    op.chain_index = i
                    op = row.operator(
                        "blender_mmd.remove_chain",
                        text="",
                        icon="X",
                    )
                    op.chain_index = i
        else:
            layout.label(text="No physics", icon="INFO")
            op = layout.operator(
                "blender_mmd.build_physics",
                text="Build Rigid Bodies",
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
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return find_mmd_armature(context) is not None

    def draw(self, context):
        layout = self.layout
        armature_obj = find_mmd_armature(context)

        chains = _get_ik_chains(armature_obj)
        if not chains:
            layout.label(text="No IK chains", icon="INFO")
            return

        # All On / All Off buttons
        row = layout.row(align=True)
        op = row.operator("blender_mmd.toggle_all_ik", text="All On", icon="HIDE_OFF")
        op.enable = True
        op = row.operator("blender_mmd.toggle_all_ik", text="All Off", icon="HIDE_ON")
        op.enable = False

        # Per-chain toggles
        col = layout.column(align=True)
        for target_name, display_name, enabled in chains:
            icon = "HIDE_OFF" if enabled else "HIDE_ON"
            op = col.operator(
                "blender_mmd.toggle_ik",
                text=display_name,
                icon=icon,
                depress=enabled,
            )
            op.target_bone = target_name


class BLENDER_MMD_PT_animation(bpy.types.Panel):
    """MMD4B — animation controls."""

    bl_label = "Animation"
    bl_idname = "BLENDER_MMD_PT_animation"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MMD4B"
    bl_parent_id = "BLENDER_MMD_PT_main"

    @classmethod
    def poll(cls, context):
        return find_mmd_armature(context) is not None

    def draw(self, context):
        layout = self.layout
        armature_obj = find_mmd_armature(context)

        has_anim = (
            armature_obj.animation_data is not None
            and armature_obj.animation_data.action is not None
        )

        if has_anim:
            action = armature_obj.animation_data.action
            layout.label(text=action.name, icon="ACTION")
            layout.operator(
                "blender_mmd.clear_animation",
                text="Clear Animation",
                icon="TRASH",
            )
        else:
            layout.label(text="No animation", icon="INFO")


class BLENDER_MMD_PT_main(bpy.types.Panel):
    """MMD4B — main panel container."""

    bl_label = "MMD4B"
    bl_idname = "BLENDER_MMD_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MMD4B"

    @classmethod
    def poll(cls, context):
        return find_mmd_armature(context) is not None

    def draw(self, context):
        armature_obj = find_mmd_armature(context)
        self.layout.label(text=armature_obj.name, icon="ARMATURE_DATA")


_classes = (
    BLENDER_MMD_PT_main,
    BLENDER_MMD_PT_animation,
    BLENDER_MMD_PT_physics,
    BLENDER_MMD_PT_ik_toggle,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
