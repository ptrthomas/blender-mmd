"""MMD4B UI panel in the 3D Viewport N-panel."""

from __future__ import annotations

import json

import bpy
from bpy.props import EnumProperty

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


class BLENDER_MMD_OT_exclude_chain_menu(bpy.types.Operator):
    """Open menu to toggle collision exclusions with other chains"""

    bl_idname = "blender_mmd.exclude_chain_menu"
    bl_label = "Chain Exclusions"
    bl_options = {"REGISTER", "INTERNAL"}

    chain_index: bpy.props.IntProperty(name="Chain Index", default=-1)

    def execute(self, context):
        return {"FINISHED"}

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=200)

    def draw(self, context):
        layout = self.layout
        armature_obj = find_mmd_armature(context)
        if not armature_obj:
            return

        chains = _get_physics_chains(armature_obj)
        if self.chain_index < 0 or self.chain_index >= len(chains):
            return

        chain = chains[self.chain_index]
        chain_name = chain.get("name", "")
        exclusions = json.loads(armature_obj.get("mmd_chain_exclusions", "{}"))

        # Build set of chains excluded from this one (bidirectional)
        excluded_set: set[str] = set()
        for a, b_list in exclusions.items():
            if a == chain_name:
                excluded_set.update(b_list)
            elif chain_name in b_list:
                excluded_set.add(a)

        layout.label(text=f"Exclude from: {chain_name}")
        layout.separator()

        for i, other_chain in enumerate(chains):
            other_name = other_chain.get("name", "")
            if other_name == chain_name:
                continue
            is_excluded = other_name in excluded_set
            icon = "CHECKBOX_HLT" if is_excluded else "CHECKBOX_DEHLT"
            op = layout.operator(
                "blender_mmd.exclude_chain_collision",
                text=other_name,
                icon=icon,
                depress=is_excluded,
            )
            op.chain_index = self.chain_index
            op.exclude_chain_name = other_name


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
            ncc_count = 0
            if col:
                rb_col = col.children.get("Rigid Bodies")
                if rb_col:
                    rb_count = len(rb_col.objects)
                joint_col = col.children.get("Joints")
                if joint_col:
                    for obj in joint_col.objects:
                        if obj.get("mmd_joint_index") is None and obj.rigid_body_constraint:
                            ncc_count += 1

            quality = armature_obj.get("collision_quality", "high")
            quality_label = "High" if quality == "high" else "Draft"
            layout.label(
                text=f"Active: {rb_count} bodies ({quality_label}, {ncc_count} NCCs)",
                icon="PHYSICS",
            )

            row = layout.row(align=True)
            row.operator(
                "blender_mmd.reset_physics",
                text="Reset",
                icon="FILE_REFRESH",
            )
            row.operator(
                "blender_mmd.rebuild_ncc",
                text="Rebuild NCCs",
                icon="MOD_PHYSICS",
            )
            row.operator(
                "blender_mmd.clear_physics",
                text="Remove",
                icon="TRASH",
            )

            # Selected rigid body info
            active = context.active_object
            if active and active.get("mmd_rigid_index") is not None:
                rb_idx = active["mmd_rigid_index"]
                phys_json = armature_obj.get("mmd_physics_data")
                if phys_json:
                    phys_data = json.loads(phys_json)
                    rbs = phys_data.get("rigid_bodies", [])
                    if 0 <= rb_idx < len(rbs):
                        rb = rbs[rb_idx]
                        mode_names = {0: "STATIC", 1: "DYNAMIC", 2: "DYNAMIC_BONE"}
                        box = layout.box()
                        box.label(text=f"Selected: {active.name}", icon="OBJECT_DATA")
                        box.label(
                            text=f"Mode: {mode_names.get(rb['mode'], '?')} | "
                            f"Mass: {rb['mass']:.2f} | "
                            f"Group: {rb['collision_group_number']}"
                        )
                        # Chain membership
                        chains_json = armature_obj.get("mmd_physics_chains")
                        if chains_json:
                            for chain in json.loads(chains_json):
                                if rb_idx in chain.get("rigid_indices", []):
                                    box.label(
                                        text=f"Chain: {chain['name']} ({chain.get('group', '?')})",
                                        icon="LINKED",
                                    )
                                    break
                        row = box.row(align=True)
                        row.operator("blender_mmd.inspect_physics", text="Inspect", icon="VIEWZOOM")
                        row.operator("blender_mmd.select_colliders", text="Colliders", icon="SHADING_BBOX")
                        row.operator("blender_mmd.select_contacts", text="Contacts", icon="MOD_PHYSICS")

            # Per-chain list with toggles, select, exclude, and remove
            chains = _get_physics_chains(armature_obj)
            if chains:
                collision_disabled = set(json.loads(
                    armature_obj.get("mmd_chain_collision_disabled", "[]")
                ))
                physics_disabled = set(json.loads(
                    armature_obj.get("mmd_chain_physics_disabled", "[]")
                ))
                exclusions = json.loads(armature_obj.get("mmd_chain_exclusions", "{}"))

                box = layout.box()
                for i, chain in enumerate(chains):
                    chain_name = chain.get("name", f"Chain {i}")
                    group = chain.get("group", "other")
                    n_bodies = len(chain.get("rigid_indices", []))

                    row = box.row(align=True)

                    # Collision toggle (eye icon)
                    col_enabled = chain_name not in collision_disabled
                    op = row.operator(
                        "blender_mmd.toggle_chain_collisions",
                        text="",
                        icon="HIDE_OFF" if col_enabled else "HIDE_ON",
                        depress=col_enabled,
                    )
                    op.chain_index = i

                    # Physics toggle (physics icon)
                    phys_enabled = chain_name not in physics_disabled
                    op = row.operator(
                        "blender_mmd.toggle_chain_physics",
                        text="",
                        icon="PHYSICS" if phys_enabled else "GHOST_DISABLED",
                        depress=phys_enabled,
                    )
                    op.chain_index = i

                    # Chain name + select
                    op = row.operator(
                        "blender_mmd.select_chain",
                        text=f"{chain_name}  ({group}, {n_bodies})",
                        icon="LINKED",
                    )
                    op.chain_index = i

                    # Exclude dropdown
                    op = row.operator(
                        "blender_mmd.exclude_chain_menu",
                        text="",
                        icon="FILTER",
                    )
                    op.chain_index = i

                    # Remove
                    op = row.operator(
                        "blender_mmd.remove_chain",
                        text="",
                        icon="X",
                    )
                    op.chain_index = i
        else:
            layout.label(text="No physics", icon="INFO")
            row = layout.row(align=True)
            row.prop(context.scene, "mmd_collision_quality", text="Quality")
            op = row.operator(
                "blender_mmd.build_physics",
                text="Build Rigid Bodies",
                icon="PHYSICS",
            )
            op.mode = "rigid_body"
            op.collision_quality = context.scene.mmd_collision_quality


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
    BLENDER_MMD_OT_exclude_chain_menu,
    BLENDER_MMD_PT_main,
    BLENDER_MMD_PT_animation,
    BLENDER_MMD_PT_physics,
    BLENDER_MMD_PT_ik_toggle,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.mmd_collision_quality = EnumProperty(
        name="Collision Quality",
        items=[
            ("high", "High", "Full collision detection. Best for final baking"),
            ("draft", "Draft", "No collisions. Fastest preview"),
        ],
        default="high",
    )


def unregister():
    if hasattr(bpy.types.Scene, "mmd_collision_quality"):
        del bpy.types.Scene.mmd_collision_quality
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
