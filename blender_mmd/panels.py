"""MMD4B UI panel in the 3D Viewport N-panel."""

from __future__ import annotations

import json

import bpy
from bpy.props import EnumProperty, FloatProperty

from .helpers import (
    find_mmd_armature,
    find_selected_mesh,
    get_mesh_physics_chains,
    get_mesh_sdef_count,
)
from .mesh import is_control_mesh


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


class BLENDER_MMD_PT_mesh(bpy.types.Panel):
    """MMD4B — selected mesh info and operations."""

    bl_label = "Mesh"
    bl_idname = "BLENDER_MMD_PT_mesh"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MMD4B"
    bl_parent_id = "BLENDER_MMD_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        mesh = find_selected_mesh(context)
        return mesh is not None and not is_control_mesh(mesh)

    def draw(self, context):
        layout = self.layout
        mesh_obj = find_selected_mesh(context)
        armature_obj = mesh_obj.parent

        # --- Info ---
        layout.label(text=mesh_obj.name, icon="MESH_DATA")
        layout.label(
            text=f"{len(mesh_obj.data.vertices):,} vertices | "
            f"{len(mesh_obj.data.materials)} materials"
        )

        # --- Outlines ---
        mat = mesh_obj.data.materials[0] if mesh_obj.data.materials else None
        if mat and mat.get("mmd_edge_enabled", False):
            layout.separator()
            solidify = mesh_obj.modifiers.get("mmd_edge")
            has_outline = solidify is not None

            # Toggle + label
            row = layout.row(align=True)
            row.operator(
                "blender_mmd.toggle_mesh_outline",
                text="",
                icon="HIDE_OFF" if has_outline else "HIDE_ON",
                depress=has_outline,
            )
            row.label(
                text=f"Outline: {abs(solidify.thickness):.4f}" if has_outline else "Outline: OFF",
                icon="MOD_SOLIDIFY",
            )

            if has_outline:
                # Edge color swatch
                edge_color = mat.get("mmd_edge_color", [0.0, 0.0, 0.0, 1.0])
                op = layout.operator(
                    "blender_mmd.set_mesh_edge_color",
                    text=f"Edge Color",
                    icon="COLOR",
                )
                op.color = edge_color

                # Per-mesh thickness multiplier (auto-applies via update callback)
                row = layout.row(align=True)
                row.prop(mesh_obj, "mmd_edge_thickness_mult", text="Thickness")

        # --- Physics chains ---
        if armature_obj.get("physics_collection"):
            chains = get_mesh_physics_chains(mesh_obj, armature_obj)
            if chains:
                layout.separator()
                rb_count = sum(len(c.get("rigid_indices", [])) for c in chains)
                row = layout.row(align=True)
                row.label(
                    text=f"Physics: {len(chains)} chains, {rb_count} bodies",
                    icon="PHYSICS",
                )
                row.operator(
                    "blender_mmd.select_mesh_rigid_bodies",
                    text="",
                    icon="RESTRICT_SELECT_OFF",
                )
                col = layout.column(align=True)
                for chain in chains:
                    name = chain.get("name", "?")
                    group = chain.get("group", "?")
                    n = len(chain.get("rigid_indices", []))
                    col.label(text=f"  {name} ({group}, {n})")

        # --- Delete ---
        layout.separator()
        layout.operator(
            "blender_mmd.delete_mesh",
            text="Delete Mesh",
            icon="TRASH",
        )


class BLENDER_MMD_PT_physics(bpy.types.Panel):
    """MMD4B — rigid body physics controls."""

    bl_label = "Physics"
    bl_idname = "BLENDER_MMD_PT_physics"
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

        # Show progress bar during modal build
        build_progress = armature_obj.get("mmd_build_progress", -1.0)
        if build_progress >= 0.0:
            build_msg = armature_obj.get("mmd_build_message", "Building...")
            col = layout.column(align=True)
            if hasattr(layout, "progress"):
                col.progress(
                    factor=build_progress,
                    type="BAR",
                    text=f"{build_progress:.0%} — {build_msg}",
                )
            else:
                col.label(text=f"Building... {build_progress:.0%} — {build_msg}", icon="TIME")
            row = col.row()
            row.label(text="Press ESC to cancel", icon="INFO")
            return

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
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return find_mmd_armature(context) is not None

    def draw(self, context):
        layout = self.layout
        armature_obj = find_mmd_armature(context)

        # --- Bone action info ---
        has_bone_anim = (
            armature_obj.animation_data is not None
            and armature_obj.animation_data.action is not None
        )

        if has_bone_anim:
            action = armature_obj.animation_data.action
            row = layout.row()
            row.label(text=action.name, icon="ACTION")
            if action.asset_data:
                row.label(text="", icon="ASSET_MANAGER")

        # --- Morph action info ---
        morph_action = _find_morph_action(armature_obj)
        if morph_action:
            row = layout.row()
            row.label(text=morph_action.name, icon="SHAPEKEY_DATA")
            if morph_action.asset_data:
                row.label(text="", icon="ASSET_MANAGER")

        # --- NLA track info ---
        nla_bone_tracks = 0
        nla_morph_tracks = 0
        if armature_obj.animation_data:
            nla_bone_tracks = len(armature_obj.animation_data.nla_tracks)
        from .mesh import find_control_mesh
        ctrl = find_control_mesh(armature_obj)
        morph_mesh = ctrl
        if morph_mesh is None:
            for child in armature_obj.children:
                if child.type == "MESH" and child.data.shape_keys:
                    morph_mesh = child
                    break
        if morph_mesh and morph_mesh.data.shape_keys:
            sk = morph_mesh.data.shape_keys
            if sk.animation_data:
                nla_morph_tracks = len(sk.animation_data.nla_tracks)

        if nla_bone_tracks or nla_morph_tracks:
            layout.label(
                text=f"NLA: {nla_bone_tracks} bone, {nla_morph_tracks} morph tracks",
                icon="NLA",
            )

        has_anything = has_bone_anim or morph_action or nla_bone_tracks or nla_morph_tracks
        if not has_anything:
            layout.label(text="No animation", icon="INFO")
            return

        # --- Action buttons ---
        layout.separator()

        row = layout.row(align=True)
        row.operator("blender_mmd.rest_pose", text="Rest Pose", icon="ARMATURE_DATA")
        row.operator("blender_mmd.mark_actions_as_assets", text="Mark as Assets", icon="ASSET_MANAGER")

        layout.operator(
            "blender_mmd.clear_animation",
            text="Remove Animation",
            icon="TRASH",
        )


def _find_morph_action(armature_obj) -> "bpy.types.Action | None":
    """Find the morph action from control mesh or first mesh with shape keys."""
    from .mesh import find_control_mesh
    ctrl = find_control_mesh(armature_obj)
    if ctrl and ctrl.data.shape_keys:
        sk = ctrl.data.shape_keys
        if sk.animation_data and sk.animation_data.action:
            return sk.animation_data.action
    # Legacy fallback
    for child in armature_obj.children:
        if child.type != "MESH" or is_control_mesh(child):
            continue
        sk = child.data.shape_keys
        if sk and sk.animation_data and sk.animation_data.action:
            return sk.animation_data.action
    return None


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
            # Count meshes with outline modifier (exclude control mesh)
            outline_count = sum(
                1 for c in armature_obj.children
                if c.type == "MESH" and not is_control_mesh(c)
                and c.modifiers.get("mmd_edge")
            )
            layout.label(
                text=f"Active: {outline_count} meshes with outlines",
                icon="MOD_SOLIDIFY",
            )

            # Thickness slider + Rebuild/Remove
            row = layout.row(align=True)
            row.prop(armature_obj, '["mmd_edge_thickness"]', text="Thickness")

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
            row.prop(armature_obj, '["mmd_edge_thickness"]', text="Thickness")
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

        # Show progress bar during modal bake
        bake_progress = armature_obj.get("mmd_sdef_bake_progress", -1.0)
        if bake_progress >= 0.0:
            bake_msg = armature_obj.get("mmd_sdef_bake_message", "Baking...")
            col = layout.column(align=True)
            if hasattr(layout, "progress"):
                col.progress(
                    factor=bake_progress,
                    type="BAR",
                    text=f"{bake_progress:.0%} — {bake_msg}",
                )
            else:
                col.label(text=f"Baking... {bake_progress:.0%} — {bake_msg}", icon="TIME")
            row = col.row()
            row.label(text="Press ESC to cancel", icon="INFO")
            return

        is_baked = armature_obj.get("mmd_sdef_baked", False)
        is_enabled = armature_obj.get("mmd_sdef_enabled", True)

        # Per-mesh info when an SDEF mesh is selected, model-wide otherwise
        sel_mesh = find_selected_mesh(context)
        if sel_mesh and sel_mesh.vertex_groups.get("mmd_sdef"):
            mesh_count = get_mesh_sdef_count(sel_mesh)
            layout.label(
                text=f"{mesh_count:,} SDEF vertices on this mesh",
                icon="MESH_ICOSPHERE",
            )
        else:
            sdef_count = armature_obj.get("mmd_sdef_count", 0)
            sdef_mesh_count = sum(
                1 for child in armature_obj.children
                if child.type == "MESH" and not is_control_mesh(child)
                and child.vertex_groups.get("mmd_sdef")
            )
            layout.label(
                text=f"{sdef_count:,} SDEF vertices across {sdef_mesh_count} meshes",
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
        row = self.layout.row()
        row.label(text=armature_obj.name, icon="ARMATURE_DATA")
        if bpy.data.texts.get("MMD Import Report"):
            row.operator(
                "blender_mmd.view_import_report",
                text="", icon="TEXT",
            )


_classes = (
    BLENDER_MMD_PT_main,
    BLENDER_MMD_PT_mesh,
    BLENDER_MMD_PT_outlines,
    BLENDER_MMD_PT_sdef,
    BLENDER_MMD_PT_animation,
    BLENDER_MMD_PT_physics,
    BLENDER_MMD_PT_ik_toggle,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
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
    if hasattr(bpy.types.Scene, "mmd_ncc_proximity"):
        del bpy.types.Scene.mmd_ncc_proximity
    if hasattr(bpy.types.Scene, "mmd_ncc_mode"):
        del bpy.types.Scene.mmd_ncc_mode
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
