"""MMD4B UI panel in the 3D Viewport N-panel."""

from __future__ import annotations

import json

import bpy
from bpy.props import EnumProperty, FloatProperty

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
                    enabled = not c.mute
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

            ncc_mode = armature_obj.get("mmd_ncc_mode", "proximity")
            proximity = armature_obj.get("mmd_ncc_proximity", 1.5)
            if ncc_mode == "draft":
                quality_label = "Draft"
            elif ncc_mode == "all":
                quality_label = "All"
            else:
                quality_label = f"{proximity:.1f}"
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

            # Per-chain list with toggles, select, self-collision, and remove
            chains = _get_physics_chains(armature_obj)
            if chains:
                collision_disabled = set(json.loads(
                    armature_obj.get("mmd_chain_collision_disabled", "[]")
                ))
                physics_disabled = set(json.loads(
                    armature_obj.get("mmd_chain_physics_disabled", "[]")
                ))

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
            row.prop(context.scene, "mmd_ncc_mode", text="NCC")
            sub = row.row(align=True)
            sub.enabled = context.scene.mmd_ncc_mode == "proximity"
            sub.prop(context.scene, "mmd_ncc_proximity", text="Proximity")
            op = layout.operator(
                "blender_mmd.build_physics",
                text="Build Rigid Bodies",
                icon="PHYSICS",
            )
            op.mode = "rigid_body"
            op.ncc_mode = context.scene.mmd_ncc_mode
            op.ncc_proximity = context.scene.mmd_ncc_proximity


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


class BLENDER_MMD_PT_outlines(bpy.types.Panel):
    """MMD4B — edge/outline rendering controls."""

    bl_label = "Outlines"
    bl_idname = "BLENDER_MMD_PT_outlines"
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

        has_outlines = armature_obj.get("mmd_outlines_built", False)

        if has_outlines:
            # Count meshes with outline modifier
            outline_count = sum(
                1 for c in armature_obj.children
                if c.type == "MESH" and c.modifiers.get("mmd_edge")
            )
            layout.label(
                text=f"Active: {outline_count} meshes with outlines",
                icon="MOD_SOLIDIFY",
            )

            # Thickness slider + Rebuild/Remove
            row = layout.row(align=True)
            row.prop(context.scene, "mmd_edge_thickness", text="Thickness")

            row = layout.row(align=True)
            row.operator(
                "blender_mmd.build_outlines",
                text="Rebuild",
                icon="FILE_REFRESH",
            )
            row.operator(
                "blender_mmd.remove_outlines",
                text="Remove",
                icon="TRASH",
            )
        else:
            row = layout.row(align=True)
            row.prop(context.scene, "mmd_edge_thickness", text="Thickness")
            layout.operator(
                "blender_mmd.build_outlines",
                text="Build Outlines",
                icon="MOD_SOLIDIFY",
            )


class BLENDER_MMD_PT_sdef(bpy.types.Panel):
    """MMD4B — SDEF (spherical deformation) controls."""

    bl_label = "SDEF"
    bl_idname = "BLENDER_MMD_PT_sdef"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MMD4B"
    bl_parent_id = "BLENDER_MMD_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        arm = find_mmd_armature(context)
        return arm is not None and arm.get("mmd_has_sdef", False)

    def draw(self, context):
        layout = self.layout
        armature_obj = find_mmd_armature(context)

        sdef_count = armature_obj.get("mmd_sdef_count", 0)
        is_baked = armature_obj.get("mmd_sdef_baked", False)
        is_enabled = armature_obj.get("mmd_sdef_enabled", True)

        # Count SDEF meshes
        sdef_mesh_count = 0
        for child in armature_obj.children:
            if child.type == "MESH" and child.vertex_groups.get("mmd_sdef"):
                sdef_mesh_count += 1

        layout.label(
            text=f"{sdef_count} SDEF vertices across {sdef_mesh_count} meshes",
            icon="MESH_ICOSPHERE",
        )

        if is_baked:
            fs = armature_obj.get("mmd_sdef_frame_start", "?")
            fe = armature_obj.get("mmd_sdef_frame_end", "?")
            state = "ON" if is_enabled else "OFF (LBS)"
            layout.label(text=f"Baked: frames {fs}\u2013{fe} | {state}")

            # Toggle button
            toggle_text = "Disable SDEF" if is_enabled else "Enable SDEF"
            toggle_icon = "PAUSE" if is_enabled else "PLAY"
            layout.operator(
                "blender_mmd.toggle_sdef",
                text=toggle_text,
                icon=toggle_icon,
            )

            row = layout.row(align=True)
            row.operator(
                "blender_mmd.bake_sdef",
                text="Rebake",
                icon="FILE_REFRESH",
            )
            row.operator(
                "blender_mmd.clear_sdef_bake",
                text="Clear",
                icon="TRASH",
            )
        else:
            import bpy as _bpy
            if not _bpy.data.is_saved:
                layout.label(text="Save .blend to enable baking", icon="ERROR")
                row = layout.row()
                row.enabled = False
                row.operator("blender_mmd.bake_sdef", text="Bake SDEF", icon="RENDER_ANIMATION")
            else:
                layout.operator(
                    "blender_mmd.bake_sdef",
                    text="Bake SDEF",
                    icon="RENDER_ANIMATION",
                )

        layout.operator(
            "blender_mmd.select_sdef_vertices",
            text="Select SDEF Vertices",
            icon="VERTEXSEL",
        )


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
    BLENDER_MMD_PT_outlines,
    BLENDER_MMD_PT_sdef,
    BLENDER_MMD_PT_animation,
    BLENDER_MMD_PT_physics,
    BLENDER_MMD_PT_ik_toggle,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.mmd_edge_thickness = FloatProperty(
        name="Edge Thickness",
        description="Multiplier for edge/outline thickness",
        default=1.0,
        min=0.1,
        max=5.0,
        step=10,
    )
    bpy.types.Scene.mmd_ncc_mode = EnumProperty(
        name="NCC Mode",
        description="Non-collision constraint mode",
        items=[
            ("draft", "Draft", "No NCCs. Fast preview, bodies pass through each other"),
            ("proximity", "Proximity", "Distance-filtered NCCs. Faster builds, may miss distant collisions"),
            ("all", "All", "Every excluded pair gets an NCC. Most correct, most objects"),
        ],
        default="all",
    )
    bpy.types.Scene.mmd_ncc_proximity = FloatProperty(
        name="NCC Proximity",
        description="Distance factor for NCC filtering. Higher = wider radius = more NCCs",
        default=1.5,
        min=1.0,
        max=10.0,
        step=10,
    )


def unregister():
    if hasattr(bpy.types.Scene, "mmd_edge_thickness"):
        del bpy.types.Scene.mmd_edge_thickness
    if hasattr(bpy.types.Scene, "mmd_ncc_proximity"):
        del bpy.types.Scene.mmd_ncc_proximity
    if hasattr(bpy.types.Scene, "mmd_ncc_mode"):
        del bpy.types.Scene.mmd_ncc_mode
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
