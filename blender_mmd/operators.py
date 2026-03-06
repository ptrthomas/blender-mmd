"""Blender operator layer — thin wrappers around core import logic."""

from __future__ import annotations

import logging

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

from .helpers import find_mmd_armature

log = logging.getLogger("blender_mmd")


class BLENDER_MMD_OT_import_pmx(bpy.types.Operator, ImportHelper):
    """Import an MMD model file (PMX/PMD)"""

    bl_idname = "blender_mmd.import_pmx"
    bl_label = "Import MMD Model"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".pmx"
    filter_glob: StringProperty(default="*.pmx;*.pmd", options={"HIDDEN"})

    scale: FloatProperty(
        name="Scale",
        description="Import scale factor",
        default=0.08,
        min=0.001,
        max=10.0,
    )

    use_toon_sphere: BoolProperty(
        name="Toon & Sphere Textures",
        description="Include toon and sphere texture nodes in materials (adds overhead)",
        default=False,
    )

    split_by_material: BoolProperty(
        name="Split by Material",
        description="Split mesh into per-material objects (enables per-object modifiers and shadow control)",
        default=True,
    )

    def execute(self, context):
        from .importer import import_pmx

        try:
            armature = import_pmx(
                self.filepath,
                self.scale,
                use_toon_sphere=self.use_toon_sphere,
                split_by_material=self.split_by_material,
            )
            self.report({"INFO"}, f"Imported: {armature.name}")
            return {"FINISHED"}
        except Exception as e:
            log.exception("PMX import failed")
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}



class BLENDER_MMD_OT_import_vmd(bpy.types.Operator, ImportHelper):
    """Import a VMD motion file onto an MMD armature"""

    bl_idname = "blender_mmd.import_vmd"
    bl_label = "Import VMD"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".vmd"
    filter_glob: StringProperty(default="*.vmd", options={"HIDDEN"})

    create_new_action: BoolProperty(
        name="Create New Action",
        description="Create new actions, replacing existing. Uncheck to append to current actions",
        default=False,
    )

    fps_mode: EnumProperty(
        name="FPS",
        description="Frame rate for imported animation",
        items=[
            ("30", "30 fps (MMD)", "Keep original 30fps timing"),
            ("60", "60 fps", "Scale keyframes to 60fps"),
            ("CUSTOM", "Custom", "Specify a custom frame rate"),
        ],
        default="30",
    )

    fps_custom: IntProperty(
        name="Custom FPS",
        description="Custom frame rate (used when FPS is set to Custom)",
        default=30,
        min=1,
        max=120,
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "create_new_action")
        layout.prop(self, "fps_mode")
        if self.fps_mode == "CUSTOM":
            layout.prop(self, "fps_custom")

    def execute(self, context):
        from .vmd import parse
        from .vmd.importer import import_vmd

        armature_obj = find_mmd_armature(context)
        if armature_obj is None:
            self.report(
                {"ERROR"},
                "No MMD armature found. Import a PMX model first, "
                "or select the target armature.",
            )
            return {"CANCELLED"}

        scale = armature_obj.get("import_scale", 0.08)
        target_fps = self.fps_custom if self.fps_mode == "CUSTOM" else int(self.fps_mode)

        try:
            vmd = parse(self.filepath)
            import_vmd(
                vmd, armature_obj, scale,
                create_new_action=self.create_new_action,
                target_fps=target_fps,
            )
            self.report(
                {"INFO"},
                f"VMD applied to '{armature_obj.name}': "
                f"{len(vmd.bone_keyframes)} bone kf, "
                f"{len(vmd.morph_keyframes)} morph kf"
                f" ({target_fps}fps)",
            )
            return {"FINISHED"}
        except Exception as e:
            log.exception("VMD import failed")
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}


class BLENDER_MMD_OT_build_physics(bpy.types.Operator):
    """Build physics for an MMD model (modal with progress)"""

    bl_idname = "blender_mmd.build_physics"
    bl_label = "Build MMD Physics"
    bl_options = {"REGISTER", "UNDO"}

    mode: EnumProperty(
        name="Mode",
        description="Physics mode",
        items=[
            ("none", "None", "Store metadata only, no Blender objects"),
            ("rigid_body", "Rigid Body", "Blender rigid body physics"),
        ],
        default="none",
    )

    ncc_mode: EnumProperty(
        name="NCC Mode",
        description="Non-collision constraint mode",
        items=[
            ("draft", "Draft", "No NCCs. Fast preview, bodies pass through each other"),
            ("proximity", "Proximity", "Distance-filtered NCCs. Faster builds, may miss distant collisions"),
            ("all", "All", "Every excluded pair gets an NCC. Most correct, most objects"),
        ],
        default="all",
    )

    ncc_proximity: FloatProperty(
        name="NCC Proximity",
        description="Distance factor for NCC filtering (only used in Proximity mode)",
        default=1.5,
        min=1.0,
        max=10.0,
        step=10,  # 0.1 increments in Blender UI
    )

    _timer = None
    _generator = None
    _armature_name: str = ""
    _model = None

    def modal(self, context, event):
        if event.type == "ESC":
            self._cleanup(context, cancelled=True)
            self.report({"WARNING"}, "Physics build cancelled")
            return {"CANCELLED"}

        if event.type == "TIMER":
            try:
                progress, message = next(self._generator)
                # Update status bar and store progress on armature for panel display
                context.workspace.status_text_set(f"Building physics... {progress:.0%} — {message}")
                armature_obj = bpy.data.objects.get(self._armature_name)
                if armature_obj:
                    armature_obj["mmd_build_progress"] = progress
                    armature_obj["mmd_build_message"] = message
                # Force panel redraw
                for area in context.screen.areas:
                    if area.type == "VIEW_3D":
                        area.tag_redraw()
            except StopIteration:
                self._cleanup(context)
                armature_obj = bpy.data.objects.get(self._armature_name)
                if armature_obj:
                    n_rb = len(self._model.rigid_bodies) if self._model else 0
                    n_j = len(self._model.joints) if self._model else 0
                    ncc_label = self.ncc_mode if self.ncc_mode != "proximity" else f"proximity={self.ncc_proximity:.1f}"
                    self.report(
                        {"INFO"},
                        f"Physics built (ncc={ncc_label}): {n_rb} rigid bodies, {n_j} joints",
                    )
                return {"FINISHED"}
            except Exception as e:
                self._cleanup(context, cancelled=True)
                log.exception("Physics build failed")
                self.report({"ERROR"}, str(e))
                return {"CANCELLED"}

        return {"RUNNING_MODAL"}

    def _cleanup(self, context, cancelled=False):
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        context.workspace.status_text_set(None)
        # Close the generator to trigger its finally block (re-enables RBW)
        if self._generator:
            self._generator.close()
            self._generator = None
        armature_obj = bpy.data.objects.get(self._armature_name)
        if armature_obj:
            if "mmd_build_progress" in armature_obj:
                del armature_obj["mmd_build_progress"]
            if "mmd_build_message" in armature_obj:
                del armature_obj["mmd_build_message"]
            # On cancel, clean up partially-built physics
            if cancelled:
                from .physics import clear_physics
                clear_physics(armature_obj)
        self._model = None
        # Force panel redraw so stale progress bar disappears immediately
        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()

    def invoke(self, context, event):
        from pathlib import Path

        armature_obj = find_mmd_armature(context)
        if armature_obj is None:
            self.report({"ERROR"}, "No MMD armature found.")
            return {"CANCELLED"}

        filepath = armature_obj.get("pmx_filepath")
        if not filepath:
            self.report({"ERROR"}, "No PMX filepath stored on armature.")
            return {"CANCELLED"}

        # For non-rigid_body modes, just run synchronously
        if self.mode != "rigid_body":
            return self._execute_sync(context, armature_obj, filepath)

        scale = armature_obj.get("import_scale", 0.08)

        try:
            ext = Path(filepath).suffix.lower()
            if ext == ".pmd":
                from .pmd import parse
            else:
                from .pmx import parse

            from .physics import build_physics_iter

            model = parse(filepath)
            self._model = model
            self._armature_name = armature_obj.name
            self._generator = build_physics_iter(
                armature_obj, model, scale,
                mode=self.mode,
                ncc_mode=self.ncc_mode,
                ncc_proximity=self.ncc_proximity,
            )
        except Exception as e:
            log.exception("Physics build init failed")
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}

        self._timer = context.window_manager.event_timer_add(0.01, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        # Fallback for API/script calls (no invoke → no modal)
        armature_obj = find_mmd_armature(context)
        if armature_obj is None:
            self.report({"ERROR"}, "No MMD armature found.")
            return {"CANCELLED"}
        filepath = armature_obj.get("pmx_filepath")
        if not filepath:
            self.report({"ERROR"}, "No PMX filepath stored on armature.")
            return {"CANCELLED"}
        return self._execute_sync(context, armature_obj, filepath)

    def _execute_sync(self, context, armature_obj, filepath):
        from pathlib import Path

        from .physics import build_physics

        scale = armature_obj.get("import_scale", 0.08)
        try:
            ext = Path(filepath).suffix.lower()
            if ext == ".pmd":
                from .pmd import parse
            else:
                from .pmx import parse
            model = parse(filepath)
            build_physics(
                armature_obj, model, scale,
                mode=self.mode,
                ncc_mode=self.ncc_mode,
                ncc_proximity=self.ncc_proximity,
            )
            ncc_label = self.ncc_mode if self.ncc_mode != "proximity" else f"proximity={self.ncc_proximity:.1f}"
            self.report(
                {"INFO"},
                f"Physics built (mode={self.mode}, ncc={ncc_label}): "
                f"{len(model.rigid_bodies)} rigid bodies, "
                f"{len(model.joints)} joints",
            )
            return {"FINISHED"}
        except Exception as e:
            log.exception("Physics build failed")
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}


class BLENDER_MMD_OT_toggle_ik(bpy.types.Operator):
    """Toggle IK constraint on/off for an MMD bone chain"""

    bl_idname = "blender_mmd.toggle_ik"
    bl_label = "Toggle IK"
    bl_options = {"REGISTER", "UNDO"}

    target_bone: StringProperty(
        name="Target Bone",
        description="Name of the IK target bone (subtarget)",
    )

    def execute(self, context):
        armature_obj = find_mmd_armature(context)
        if armature_obj is None:
            self.report({"ERROR"}, "No MMD armature found.")
            return {"CANCELLED"}

        toggled = _toggle_ik_for_target(armature_obj, self.target_bone)
        if not toggled:
            self.report({"WARNING"}, f"No IK constraint targeting '{self.target_bone}'")
            return {"CANCELLED"}
        return {"FINISHED"}


class BLENDER_MMD_OT_toggle_all_ik(bpy.types.Operator):
    """Enable or disable all IK constraints"""

    bl_idname = "blender_mmd.toggle_all_ik"
    bl_label = "Toggle All IK"
    bl_options = {"REGISTER", "UNDO"}

    enable: bpy.props.BoolProperty(name="Enable", default=True)

    def execute(self, context):
        armature_obj = find_mmd_armature(context)
        if armature_obj is None:
            self.report({"ERROR"}, "No MMD armature found.")
            return {"CANCELLED"}

        mute = not self.enable
        count = 0
        for pb in armature_obj.pose.bones:
            for c in pb.constraints:
                if c.type == "IK" and c.subtarget:
                    c.mute = mute
                    _set_ik_chain_mute(armature_obj, pb, c, mute)
                    count += 1

        state = "enabled" if self.enable else "disabled"
        self.report({"INFO"}, f"IK {state}: {count} chains")
        return {"FINISHED"}


def _toggle_ik_for_target(armature_obj, target_bone_name: str) -> bool:
    """Toggle IK constraint mute for a given target bone. Returns True if found."""
    for pb in armature_obj.pose.bones:
        for c in pb.constraints:
            if c.type == "IK" and c.subtarget == target_bone_name:
                c.mute = not c.mute
                _set_ik_chain_mute(armature_obj, pb, c, c.mute)
                return True
    return False


def _set_ik_chain_mute(armature_obj, ik_bone, ik_constraint, mute: bool):
    """Set mute on LIMIT_ROTATION override constraints in the IK chain."""
    bone = ik_bone
    for _ in range(ik_constraint.chain_count):
        if bone is None:
            break
        for c in bone.constraints:
            if c.type == "LIMIT_ROTATION" and c.name == "mmd_ik_limit_override":
                c.mute = mute
        bone = bone.parent


class BLENDER_MMD_OT_clear_physics(bpy.types.Operator):
    """Remove rigid body physics for an MMD model"""

    bl_idname = "blender_mmd.clear_physics"
    bl_label = "Clear MMD Physics"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .physics import clear_physics

        armature_obj = find_mmd_armature(context)
        if armature_obj is None:
            self.report({"ERROR"}, "No MMD armature found.")
            return {"CANCELLED"}

        try:
            clear_physics(armature_obj)
            self.report({"INFO"}, "Physics cleared.")
            return {"FINISHED"}
        except Exception as e:
            log.exception("Physics clear failed")
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}


class BLENDER_MMD_OT_reset_physics(bpy.types.Operator):
    """Reset rigid bodies to match current bone pose"""

    bl_idname = "blender_mmd.reset_physics"
    bl_label = "Reset MMD Physics"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .physics import reset_physics

        armature_obj = find_mmd_armature(context)
        if armature_obj is None:
            self.report({"ERROR"}, "No MMD armature found.")
            return {"CANCELLED"}

        try:
            count = reset_physics(armature_obj)
            self.report({"INFO"}, f"Reset {count} rigid bodies to current pose.")
            return {"FINISHED"}
        except Exception as e:
            log.exception("Physics reset failed")
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}


class BLENDER_MMD_OT_select_chain(bpy.types.Operator):
    """Select rigid body objects for a physics chain"""

    bl_idname = "blender_mmd.select_chain"
    bl_label = "Select Physics Chain"
    bl_options = {"REGISTER", "UNDO"}

    chain_index: IntProperty(name="Chain Index", default=-1)

    def execute(self, context):
        import json

        armature_obj = find_mmd_armature(context)
        if armature_obj is None:
            self.report({"ERROR"}, "No MMD armature found.")
            return {"CANCELLED"}

        chains_json = armature_obj.get("mmd_physics_chains")
        if not chains_json:
            self.report({"ERROR"}, "No chain data.")
            return {"CANCELLED"}

        chains = json.loads(chains_json)
        if self.chain_index < 0 or self.chain_index >= len(chains):
            self.report({"ERROR"}, "Invalid chain index.")
            return {"CANCELLED"}

        chain = chains[self.chain_index]
        rigid_indices = set(chain.get("rigid_indices", []))

        col_name = armature_obj.get("physics_collection")
        if not col_name:
            return {"CANCELLED"}
        collection = bpy.data.collections.get(col_name)
        if not collection:
            return {"CANCELLED"}

        # Unhide physics collection so selection is visible
        vl_col = context.view_layer.layer_collection.children.get(col_name)
        if vl_col and vl_col.hide_viewport:
            vl_col.hide_viewport = False

        # Deselect all, then select chain rigid bodies
        bpy.ops.object.select_all(action="DESELECT")
        rb_col = collection.children.get("Rigid Bodies")
        count = 0
        if rb_col:
            for obj in rb_col.objects:
                idx = obj.get("mmd_rigid_index")
                if idx is not None and idx in rigid_indices:
                    obj.select_set(True)
                    count += 1
                    if count == 1:
                        context.view_layer.objects.active = obj

        self.report({"INFO"}, f"Selected {count} rigid bodies")
        return {"FINISHED"}


class BLENDER_MMD_OT_remove_chain(bpy.types.Operator):
    """Remove a single physics chain (rigid bodies, joints, tracking)"""

    bl_idname = "blender_mmd.remove_chain"
    bl_label = "Remove Physics Chain"
    bl_options = {"REGISTER", "UNDO"}

    chain_index: IntProperty(name="Chain Index", default=-1)

    def execute(self, context):
        from .physics import remove_chain

        armature_obj = find_mmd_armature(context)
        if armature_obj is None:
            self.report({"ERROR"}, "No MMD armature found.")
            return {"CANCELLED"}

        try:
            name = remove_chain(armature_obj, self.chain_index)
            self.report({"INFO"}, f"Removed chain: {name}")
            return {"FINISHED"}
        except Exception as e:
            log.exception("Chain removal failed")
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}


class BLENDER_MMD_OT_clear_animation(bpy.types.Operator):
    """Clear all animation from MMD model (bone keyframes, morph keyframes)"""

    bl_idname = "blender_mmd.clear_animation"
    bl_label = "Clear MMD Animation"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        armature_obj = find_mmd_armature(context)
        if armature_obj is None:
            self.report({"ERROR"}, "No MMD armature found.")
            return {"CANCELLED"}

        cleared = []

        # Clear armature action (bone keyframes, IK toggle keyframes)
        if armature_obj.animation_data:
            if armature_obj.animation_data.action:
                action = armature_obj.animation_data.action
                armature_obj.animation_data.action = None
                if action.users == 0:
                    bpy.data.actions.remove(action)
                cleared.append("bone keyframes")
            # Clear bone NLA tracks
            nla = armature_obj.animation_data.nla_tracks
            if len(nla):
                for track in list(nla):
                    for strip in track.strips:
                        action = strip.action
                        if action:
                            action.use_fake_user = False
                    nla.remove(track)
                cleared.append("bone NLA")

        # Reset all pose bones to rest position
        for pb in armature_obj.pose.bones:
            pb.location = (0, 0, 0)
            pb.rotation_quaternion = (1, 0, 0, 0)
            pb.rotation_euler = (0, 0, 0)
            pb.scale = (1, 1, 1)

        # Clear shape key animation on child meshes
        morph_cleared = False
        morph_nla_cleared = False
        for child in armature_obj.children:
            if child.type != "MESH":
                continue
            sk = child.data.shape_keys
            if sk is None:
                continue
            if sk.animation_data:
                if sk.animation_data.action:
                    action = sk.animation_data.action
                    sk.animation_data.action = None
                    if action.users == 0:
                        bpy.data.actions.remove(action)
                    morph_cleared = True
                # Clear morph NLA tracks
                nla = sk.animation_data.nla_tracks
                if len(nla):
                    for track in list(nla):
                        for strip in track.strips:
                            action = strip.action
                            if action:
                                action.use_fake_user = False
                        nla.remove(track)
                    morph_nla_cleared = True
            # Reset shape key values to 0
            for kb in sk.key_blocks:
                if kb != sk.reference_key:
                    kb.value = 0.0
        if morph_cleared:
            cleared.append("morph keyframes")
        if morph_nla_cleared:
            cleared.append("morph NLA")

        # Clear morph sync handler
        if armature_obj.get("mmd_morph_sync"):
            from .vmd.importer import _remove_morph_sync_handler
            del armature_obj["mmd_morph_sync"]
            _remove_morph_sync_handler()
            cleared.append("morph sync")

        # Restore material emission drivers (cleared during NLA push)
        from .materials import setup_drivers
        setup_drivers(armature_obj)

        # Go to frame 1 for clean state
        bpy.context.scene.frame_set(1)

        if cleared:
            self.report({"INFO"}, f"Cleared: {', '.join(cleared)}")
        else:
            self.report({"INFO"}, "No animation to clear.")
        return {"FINISHED"}


class BLENDER_MMD_OT_inspect_physics(bpy.types.Operator):
    """Inspect a rigid body's properties, connections, and collision groups"""

    bl_idname = "blender_mmd.inspect_physics"
    bl_label = "Inspect Rigid Body"
    bl_options = {"REGISTER"}

    def execute(self, context):
        from .physics import inspect_rigid_body

        armature_obj = find_mmd_armature(context)
        if armature_obj is None:
            self.report({"ERROR"}, "No MMD armature found.")
            return {"CANCELLED"}

        obj = context.active_object
        if obj is None or obj.get("mmd_rigid_index") is None:
            self.report({"ERROR"}, "Select a rigid body object first.")
            return {"CANCELLED"}

        idx = obj["mmd_rigid_index"]
        report = inspect_rigid_body(armature_obj, idx)
        context.window_manager.clipboard = report
        first_line = report.split("\n")[0] if report else "No data"
        self.report({"INFO"}, f"{first_line} — copied to clipboard")
        return {"FINISHED"}


class BLENDER_MMD_OT_select_colliders(bpy.types.Operator):
    """Select all rigid bodies that can collide with the active one"""

    bl_idname = "blender_mmd.select_colliders"
    bl_label = "Select Colliders"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .physics import get_collision_eligible_indices

        armature_obj = find_mmd_armature(context)
        if armature_obj is None:
            self.report({"ERROR"}, "No MMD armature found.")
            return {"CANCELLED"}

        obj = context.active_object
        if obj is None or obj.get("mmd_rigid_index") is None:
            self.report({"ERROR"}, "Select a rigid body object first.")
            return {"CANCELLED"}

        idx = obj["mmd_rigid_index"]
        eligible = get_collision_eligible_indices(armature_obj, idx)

        col_name = armature_obj.get("physics_collection")
        if not col_name:
            return {"CANCELLED"}
        col = bpy.data.collections.get(col_name)
        if not col:
            return {"CANCELLED"}

        # Unhide physics collection
        vl_col = context.view_layer.layer_collection.children.get(col_name)
        if vl_col and vl_col.hide_viewport:
            vl_col.hide_viewport = False

        bpy.ops.object.select_all(action="DESELECT")
        rb_col = col.children.get("Rigid Bodies")
        count = 0
        if rb_col:
            for rb_obj in rb_col.objects:
                rb_idx = rb_obj.get("mmd_rigid_index")
                if rb_idx is not None and rb_idx in eligible:
                    rb_obj.select_set(True)
                    count += 1

        # Keep the original object selected and active
        obj.select_set(True)
        context.view_layer.objects.active = obj

        self.report({"INFO"}, f"Selected {count} collision-eligible bodies")
        return {"FINISHED"}


class BLENDER_MMD_OT_select_contacts(bpy.types.Operator):
    """Select rigid bodies currently in contact with the active one"""

    bl_idname = "blender_mmd.select_contacts"
    bl_label = "Select Contacts"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .physics import get_collision_eligible_indices

        armature_obj = find_mmd_armature(context)
        if armature_obj is None:
            self.report({"ERROR"}, "No MMD armature found.")
            return {"CANCELLED"}

        obj = context.active_object
        if obj is None or obj.get("mmd_rigid_index") is None:
            self.report({"ERROR"}, "Select a rigid body object first.")
            return {"CANCELLED"}

        idx = obj["mmd_rigid_index"]
        eligible = get_collision_eligible_indices(armature_obj, idx)

        col_name = armature_obj.get("physics_collection")
        if not col_name:
            return {"CANCELLED"}
        col = bpy.data.collections.get(col_name)
        if not col:
            return {"CANCELLED"}

        # Unhide physics collection
        vl_col = context.view_layer.layer_collection.children.get(col_name)
        if vl_col and vl_col.hide_viewport:
            vl_col.hide_viewport = False

        # Find contacts using bounding box overlap
        rb_col = col.children.get("Rigid Bodies")
        if not rb_col:
            return {"CANCELLED"}

        # Build index → object map for eligible bodies
        eligible_objs = {}
        for rb_obj in rb_col.objects:
            rb_idx = rb_obj.get("mmd_rigid_index")
            if rb_idx is not None and rb_idx in eligible:
                eligible_objs[rb_idx] = rb_obj

        # Shape-aware contact detection using collision shape radii
        import json
        phys_data = json.loads(armature_obj["mmd_physics_data"])
        rbs_data = phys_data["rigid_bodies"]
        import_scale = armature_obj.get("import_scale", 0.08)
        margin = 0.005  # small contact threshold

        bpy.ops.object.select_all(action="DESELECT")
        count = 0
        pos_a = obj.matrix_world.translation
        radius_a = _shape_radius(rbs_data[idx], import_scale)

        for rb_idx, rb_obj in eligible_objs.items():
            pos_b = rb_obj.matrix_world.translation
            radius_b = _shape_radius(rbs_data[rb_idx], import_scale)
            dist = (pos_a - pos_b).length
            if dist < radius_a + radius_b + margin:
                rb_obj.select_set(True)
                count += 1

        obj.select_set(True)
        context.view_layer.objects.active = obj

        self.report({"INFO"}, f"Selected {count} bodies in contact")
        return {"FINISHED"}


def _shape_radius(rb_data: dict, scale: float) -> float:
    """Approximate bounding radius of a rigid body shape."""
    import math
    sx, sy, sz = rb_data["size"]
    shape = rb_data["shape"]
    if shape == 0:  # SPHERE
        return sx * scale
    elif shape == 1:  # BOX
        return math.sqrt((sx * scale) ** 2 + (sy * scale) ** 2 + (sz * scale) ** 2)
    elif shape == 2:  # CAPSULE
        r = sx * scale
        h = sy * scale
        return r + h / 2
    return 0.01


class BLENDER_MMD_OT_select_sdef_vertices(bpy.types.Operator):
    """Select all SDEF vertices on the active mesh (or first mesh child)"""

    bl_idname = "blender_mmd.select_sdef_vertices"
    bl_label = "Select SDEF Vertices"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        import bmesh

        armature_obj = find_mmd_armature(context)
        if armature_obj is None:
            self.report({"ERROR"}, "No MMD armature found.")
            return {"CANCELLED"}

        # Find a mesh to work with — prefer active object if it's a mesh child
        mesh_obj = None
        if context.active_object and context.active_object.type == "MESH":
            if context.active_object.parent == armature_obj:
                mesh_obj = context.active_object

        # If no active mesh child, pick the first one with SDEF
        if mesh_obj is None:
            for child in armature_obj.children:
                if child.type == "MESH" and child.vertex_groups.get("mmd_sdef"):
                    mesh_obj = child
                    break

        if mesh_obj is None:
            self.report({"ERROR"}, "No mesh with SDEF vertices found.")
            return {"CANCELLED"}

        # Select mesh, enter edit mode
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        mesh_obj.select_set(True)
        context.view_layer.objects.active = mesh_obj
        bpy.ops.object.mode_set(mode="EDIT")

        bm = bmesh.from_edit_mesh(mesh_obj.data)

        # Find SDEF group index
        vg_sdef = mesh_obj.vertex_groups.get("mmd_sdef")
        if vg_sdef is None:
            bpy.ops.object.mode_set(mode="OBJECT")
            self.report({"ERROR"}, "No mmd_sdef vertex group.")
            return {"CANCELLED"}

        deform_layer = bm.verts.layers.deform.active
        if deform_layer is None:
            bpy.ops.object.mode_set(mode="OBJECT")
            self.report({"ERROR"}, "No deform layer.")
            return {"CANCELLED"}

        # Deselect all, then select SDEF verts
        bm.select_flush(False)
        for v in bm.verts:
            v.select = False
        count = 0
        gi = vg_sdef.index
        for v in bm.verts:
            dvert = v[deform_layer]
            if gi in dvert:
                v.select = True
                count += 1

        bm.select_flush(True)
        bmesh.update_edit_mesh(mesh_obj.data)

        self.report({"INFO"}, f"Selected {count} SDEF vertices on {mesh_obj.name}")
        return {"FINISHED"}


class BLENDER_MMD_OT_bake_sdef(bpy.types.Operator):
    """Bake SDEF deformation to MDD mesh cache files (modal with progress)"""

    bl_idname = "blender_mmd.bake_sdef"
    bl_label = "Bake SDEF"
    bl_options = {"REGISTER", "UNDO"}

    _timer = None
    _generator = None
    _armature_name: str = ""

    def modal(self, context, event):
        if event.type == "ESC":
            self._cleanup(context, cancelled=True)
            self.report({"WARNING"}, "SDEF bake cancelled")
            return {"CANCELLED"}

        if event.type == "TIMER":
            try:
                progress, message, result = next(self._generator)
                context.workspace.status_text_set(f"Baking SDEF... {progress:.0%} — {message}")
                armature_obj = bpy.data.objects.get(self._armature_name)
                if armature_obj:
                    armature_obj["mmd_sdef_bake_progress"] = progress
                    armature_obj["mmd_sdef_bake_message"] = message
                for area in context.screen.areas:
                    if area.type == "VIEW_3D":
                        area.tag_redraw()
                if result is not None:
                    self._cleanup(context)
                    self.report(
                        {"INFO"},
                        f"SDEF baked: {result['meshes']} meshes, "
                        f"{result['frames']} frames in {result['time']:.1f}s",
                    )
                    return {"FINISHED"}
            except Exception as e:
                self._cleanup(context, cancelled=True)
                log.exception("SDEF bake failed")
                self.report({"ERROR"}, str(e))
                return {"CANCELLED"}

        return {"RUNNING_MODAL"}

    def _cleanup(self, context, cancelled=False):
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        context.workspace.status_text_set(None)
        if self._generator:
            self._generator.close()
            self._generator = None
        armature_obj = bpy.data.objects.get(self._armature_name)
        if armature_obj:
            if "mmd_sdef_bake_progress" in armature_obj:
                del armature_obj["mmd_sdef_bake_progress"]
            if "mmd_sdef_bake_message" in armature_obj:
                del armature_obj["mmd_sdef_bake_message"]
            if cancelled:
                from .sdef import clear_sdef_bake
                clear_sdef_bake(armature_obj)
        # Force panel redraw so stale progress bar disappears immediately
        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()

    def invoke(self, context, event):
        armature_obj = find_mmd_armature(context)
        if armature_obj is None:
            self.report({"ERROR"}, "No MMD armature found.")
            return {"CANCELLED"}

        if not bpy.data.is_saved:
            self.report({"ERROR"}, "Save the .blend file before baking SDEF.")
            return {"CANCELLED"}

        frame_start = context.scene.frame_start
        frame_end = context.scene.frame_end

        if context.active_object and context.active_object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        try:
            from .sdef import bake_sdef_iter

            self._armature_name = armature_obj.name
            self._generator = bake_sdef_iter(armature_obj, frame_start, frame_end)
        except Exception as e:
            log.exception("SDEF bake init failed")
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}

        self._timer = context.window_manager.event_timer_add(0.01, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        # Fallback for API/script calls
        from .sdef import bake_sdef

        armature_obj = find_mmd_armature(context)
        if armature_obj is None:
            self.report({"ERROR"}, "No MMD armature found.")
            return {"CANCELLED"}

        if not bpy.data.is_saved:
            self.report({"ERROR"}, "Save the .blend file before baking SDEF.")
            return {"CANCELLED"}

        frame_start = context.scene.frame_start
        frame_end = context.scene.frame_end

        if context.active_object and context.active_object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        try:
            result = bake_sdef(armature_obj, frame_start, frame_end)
            self.report(
                {"INFO"},
                f"SDEF baked: {result['meshes']} meshes, "
                f"{result['frames']} frames in {result['time']:.1f}s",
            )
            return {"FINISHED"}
        except Exception as e:
            log.exception("SDEF bake failed")
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}


class BLENDER_MMD_OT_clear_sdef_bake(bpy.types.Operator):
    """Clear SDEF bake: remove Mesh Cache modifiers, restore Armature, delete MDD files"""

    bl_idname = "blender_mmd.clear_sdef_bake"
    bl_label = "Clear SDEF Bake"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .sdef import clear_sdef_bake

        armature_obj = find_mmd_armature(context)
        if armature_obj is None:
            self.report({"ERROR"}, "No MMD armature found.")
            return {"CANCELLED"}

        try:
            count = clear_sdef_bake(armature_obj)
            self.report({"INFO"}, f"SDEF bake cleared: {count} meshes")
            return {"FINISHED"}
        except Exception as e:
            log.exception("SDEF clear failed")
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}


class BLENDER_MMD_OT_toggle_sdef(bpy.types.Operator):
    """Toggle SDEF on/off (swap Mesh Cache vs Armature modifier visibility)"""

    bl_idname = "blender_mmd.toggle_sdef"
    bl_label = "Toggle SDEF"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .sdef import toggle_sdef

        armature_obj = find_mmd_armature(context)
        if armature_obj is None:
            self.report({"ERROR"}, "No MMD armature found.")
            return {"CANCELLED"}

        try:
            new_state = toggle_sdef(armature_obj)
            state = "enabled" if new_state else "disabled"
            self.report({"INFO"}, f"SDEF {state}")
            return {"FINISHED"}
        except Exception as e:
            log.exception("SDEF toggle failed")
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}


class BLENDER_MMD_OT_build_outlines(bpy.types.Operator):
    """Build edge outlines on MMD model meshes"""

    bl_idname = "blender_mmd.build_outlines"
    bl_label = "Build Outlines"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .outlines import build_outlines, remove_outlines

        armature_obj = find_mmd_armature(context)
        if armature_obj is None:
            self.report({"ERROR"}, "No MMD armature found.")
            return {"CANCELLED"}

        # Remove existing outlines first (supports rebuild)
        if armature_obj.get("mmd_outlines_built"):
            remove_outlines(armature_obj)

        thickness_mult = context.scene.mmd_edge_thickness

        try:
            count = build_outlines(armature_obj, thickness_mult)
            self.report({"INFO"}, f"Outlines built on {count} meshes")
            return {"FINISHED"}
        except Exception as e:
            log.exception("Outline build failed")
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}


class BLENDER_MMD_OT_remove_outlines(bpy.types.Operator):
    """Remove edge outlines from MMD model"""

    bl_idname = "blender_mmd.remove_outlines"
    bl_label = "Remove Outlines"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .outlines import remove_outlines

        armature_obj = find_mmd_armature(context)
        if armature_obj is None:
            self.report({"ERROR"}, "No MMD armature found.")
            return {"CANCELLED"}

        try:
            remove_outlines(armature_obj)
            self.report({"INFO"}, "Outlines removed.")
            return {"FINISHED"}
        except Exception as e:
            log.exception("Outline removal failed")
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}


class BLENDER_MMD_OT_toggle_mesh_outline(bpy.types.Operator):
    """Toggle outline on/off for the selected mesh"""

    bl_idname = "blender_mmd.toggle_mesh_outline"
    bl_label = "Toggle Mesh Outline"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        from .helpers import find_selected_mesh
        mesh_obj = find_selected_mesh(context)
        if not mesh_obj:
            return False
        mat = mesh_obj.data.materials[0] if mesh_obj.data.materials else None
        return mat is not None and mat.get("mmd_edge_enabled", False)

    def execute(self, context):
        from .helpers import find_selected_mesh
        from .outlines import toggle_mesh_outline

        mesh_obj = find_selected_mesh(context)
        if mesh_obj is None:
            self.report({"ERROR"}, "No MMD mesh selected.")
            return {"CANCELLED"}

        new_state = toggle_mesh_outline(mesh_obj, mesh_obj.parent)
        state = "enabled" if new_state else "disabled"
        self.report({"INFO"}, f"Outline {state} on '{mesh_obj.name}'")
        return {"FINISHED"}


class BLENDER_MMD_OT_set_mesh_edge_color(bpy.types.Operator):
    """Set edge outline color for the selected mesh"""

    bl_idname = "blender_mmd.set_mesh_edge_color"
    bl_label = "Set Edge Color"
    bl_options = {"REGISTER", "UNDO"}

    color: FloatVectorProperty(
        name="Edge Color",
        subtype="COLOR",
        size=4,
        min=0.0,
        max=1.0,
        default=(0.0, 0.0, 0.0, 1.0),
    )

    @classmethod
    def poll(cls, context):
        from .helpers import find_selected_mesh
        return find_selected_mesh(context) is not None

    def execute(self, context):
        from .helpers import find_selected_mesh
        from .outlines import set_mesh_edge_color

        mesh_obj = find_selected_mesh(context)
        if mesh_obj is None:
            self.report({"ERROR"}, "No MMD mesh selected.")
            return {"CANCELLED"}

        set_mesh_edge_color(mesh_obj, tuple(self.color))
        return {"FINISHED"}


class BLENDER_MMD_OT_rebuild_ncc(bpy.types.Operator):
    """Rebuild non-collision constraint empties (respects self-collision settings)"""

    bl_idname = "blender_mmd.rebuild_ncc"
    bl_label = "Rebuild NCCs"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .physics import rebuild_ncc

        armature_obj = find_mmd_armature(context)
        if armature_obj is None:
            self.report({"ERROR"}, "No MMD armature found.")
            return {"CANCELLED"}

        try:
            old_count, new_count = rebuild_ncc(armature_obj)
            self.report({"INFO"}, f"NCCs rebuilt: {old_count} → {new_count}")
            return {"FINISHED"}
        except Exception as e:
            log.exception("NCC rebuild failed")
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}


class BLENDER_MMD_OT_toggle_chain_collisions(bpy.types.Operator):
    """Toggle collision detection for a physics chain"""

    bl_idname = "blender_mmd.toggle_chain_collisions"
    bl_label = "Toggle Chain Collisions"
    bl_options = {"REGISTER", "UNDO"}

    chain_index: IntProperty(name="Chain Index", default=-1)

    def execute(self, context):
        import json
        from .physics import toggle_chain_collisions

        armature_obj = find_mmd_armature(context)
        if armature_obj is None:
            self.report({"ERROR"}, "No MMD armature found.")
            return {"CANCELLED"}

        # Determine current state to toggle
        chains = json.loads(armature_obj.get("mmd_physics_chains", "[]"))
        if self.chain_index < 0 or self.chain_index >= len(chains):
            self.report({"ERROR"}, "Invalid chain index.")
            return {"CANCELLED"}

        chain_name = chains[self.chain_index]["name"]
        disabled = set(json.loads(armature_obj.get("mmd_chain_collision_disabled", "[]")))
        enable = chain_name in disabled  # if currently disabled, enable

        try:
            toggle_chain_collisions(armature_obj, self.chain_index, enable)
            state = "enabled" if enable else "disabled"
            self.report({"INFO"}, f"Chain '{chain_name}' collisions {state}")
            return {"FINISHED"}
        except Exception as e:
            log.exception("Toggle chain collisions failed")
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}


class BLENDER_MMD_OT_toggle_chain_physics(bpy.types.Operator):
    """Toggle physics simulation for a physics chain (kinematic mode)"""

    bl_idname = "blender_mmd.toggle_chain_physics"
    bl_label = "Toggle Chain Physics"
    bl_options = {"REGISTER", "UNDO"}

    chain_index: IntProperty(name="Chain Index", default=-1)

    def execute(self, context):
        import json
        from .physics import toggle_chain_physics

        armature_obj = find_mmd_armature(context)
        if armature_obj is None:
            self.report({"ERROR"}, "No MMD armature found.")
            return {"CANCELLED"}

        chains = json.loads(armature_obj.get("mmd_physics_chains", "[]"))
        if self.chain_index < 0 or self.chain_index >= len(chains):
            self.report({"ERROR"}, "Invalid chain index.")
            return {"CANCELLED"}

        chain_name = chains[self.chain_index]["name"]
        disabled = set(json.loads(armature_obj.get("mmd_chain_physics_disabled", "[]")))
        enable = chain_name in disabled  # if currently disabled, enable

        try:
            toggle_chain_physics(armature_obj, self.chain_index, enable)
            state = "enabled" if enable else "disabled"
            self.report({"INFO"}, f"Chain '{chain_name}' physics {state}")
            return {"FINISHED"}
        except Exception as e:
            log.exception("Toggle chain physics failed")
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}


class BLENDER_MMD_OT_select_mesh_rigid_bodies(bpy.types.Operator):
    """Select rigid bodies related to the active mesh"""

    bl_idname = "blender_mmd.select_mesh_rigid_bodies"
    bl_label = "Select Mesh Rigid Bodies"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        from .helpers import find_selected_mesh
        return find_selected_mesh(context) is not None

    def execute(self, context):
        from .helpers import find_selected_mesh, get_mesh_physics_chains

        mesh_obj = find_selected_mesh(context)
        if mesh_obj is None:
            self.report({"ERROR"}, "No MMD mesh selected.")
            return {"CANCELLED"}

        armature_obj = mesh_obj.parent
        chains = get_mesh_physics_chains(mesh_obj, armature_obj)
        if not chains:
            self.report({"INFO"}, "No physics chains affect this mesh")
            return {"CANCELLED"}

        # Collect all rigid indices from matching chains
        rigid_indices = set()
        for chain in chains:
            rigid_indices.update(chain.get("rigid_indices", []))

        col_name = armature_obj.get("physics_collection")
        if not col_name:
            return {"CANCELLED"}
        collection = bpy.data.collections.get(col_name)
        if not collection:
            return {"CANCELLED"}

        # Unhide physics collection
        vl_col = context.view_layer.layer_collection.children.get(col_name)
        if vl_col and vl_col.hide_viewport:
            vl_col.hide_viewport = False

        bpy.ops.object.select_all(action="DESELECT")
        rb_col = collection.children.get("Rigid Bodies")
        count = 0
        if rb_col:
            for obj in rb_col.objects:
                idx = obj.get("mmd_rigid_index")
                if idx is not None and idx in rigid_indices:
                    obj.select_set(True)
                    count += 1
                    if count == 1:
                        context.view_layer.objects.active = obj

        self.report({"INFO"}, f"Selected {count} rigid bodies for '{mesh_obj.name}'")
        return {"FINISHED"}


class BLENDER_MMD_OT_delete_mesh(bpy.types.Operator):
    """Delete the selected mesh child of an MMD armature"""

    bl_idname = "blender_mmd.delete_mesh"
    bl_label = "Delete Mesh"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        from .helpers import find_selected_mesh
        return find_selected_mesh(context) is not None

    def execute(self, context):
        from .helpers import find_selected_mesh
        mesh_obj = find_selected_mesh(context)
        if mesh_obj is None:
            self.report({"ERROR"}, "No MMD mesh selected.")
            return {"CANCELLED"}

        armature_obj = mesh_obj.parent

        # Prevent deleting the primary morph mesh when it holds NLA strips
        # or is the sync source for secondary meshes
        primary_name = armature_obj.get("mmd_morph_sync")
        if primary_name and mesh_obj.name == primary_name:
            self.report(
                {"ERROR"},
                "Cannot delete: this mesh holds morph NLA strips. "
                "Clear animation first.",
            )
            return {"CANCELLED"}

        name = mesh_obj.name
        vert_count = len(mesh_obj.data.vertices)

        bpy.data.objects.remove(mesh_obj, do_unlink=True)

        # Select the armature
        context.view_layer.objects.active = armature_obj
        armature_obj.select_set(True)

        self.report({"INFO"}, f"Deleted '{name}' ({vert_count} vertices)")
        return {"FINISHED"}


class BLENDER_MMD_OT_view_import_report(bpy.types.Operator):
    """Open the MMD Import Report in a text editor area"""

    bl_idname = "blender_mmd.view_import_report"
    bl_label = "View Import Report"

    @classmethod
    def poll(cls, context):
        return bpy.data.texts.get("MMD Import Report") is not None

    def execute(self, context):
        txt = bpy.data.texts.get("MMD Import Report")
        if not txt:
            self.report({"WARNING"}, "No import report found")
            return {"CANCELLED"}

        # Find an existing text editor area, or convert the smallest area
        text_area = None
        for area in context.screen.areas:
            if area.type == "TEXT_EDITOR":
                text_area = area
                break

        if text_area is None:
            # Find the smallest non-VIEW_3D area to convert
            candidates = [
                a for a in context.screen.areas
                if a.type not in ("VIEW_3D", "PROPERTIES", "OUTLINER")
            ]
            if not candidates:
                candidates = [
                    a for a in context.screen.areas
                    if a.type == "OUTLINER"
                ]
            if candidates:
                text_area = min(candidates, key=lambda a: a.width * a.height)
                text_area.type = "TEXT_EDITOR"

        if text_area:
            for space in text_area.spaces:
                if space.type == "TEXT_EDITOR":
                    space.text = txt
                    break

        return {"FINISHED"}


class BLENDER_MMD_OT_push_to_nla(bpy.types.Operator):
    """Push current VMD actions to NLA strips for layering"""

    bl_idname = "blender_mmd.push_to_nla"
    bl_label = "Push to NLA"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        arm = find_mmd_armature(context)
        if not arm:
            return False
        has_bone = arm.animation_data and arm.animation_data.action
        has_morph = any(
            c.type == "MESH"
            and c.data.shape_keys
            and c.data.shape_keys.animation_data
            and c.data.shape_keys.animation_data.action
            for c in arm.children
        )
        return has_bone or has_morph

    def execute(self, context):
        from .vmd.importer import push_to_nla

        armature_obj = find_mmd_armature(context)
        if armature_obj is None:
            self.report({"ERROR"}, "No MMD armature found.")
            return {"CANCELLED"}

        # Use bone action name as strip name, or fallback
        strip_name = "VMD"
        if armature_obj.animation_data and armature_obj.animation_data.action:
            strip_name = armature_obj.animation_data.action.name

        try:
            result = push_to_nla(armature_obj, strip_name)
            self.report(
                {"INFO"},
                f"Pushed to NLA: {result['bone_strips']} bone, "
                f"{result['morph_strips']} morph strips",
            )
            return {"FINISHED"}
        except Exception as e:
            log.exception("NLA push failed")
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}


class BLENDER_MMD_OT_mark_actions_as_assets(bpy.types.Operator):
    """Mark bone and morph actions as Blender assets for reuse"""

    bl_idname = "blender_mmd.mark_actions_as_assets"
    bl_label = "Mark Actions as Assets"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        arm = find_mmd_armature(context)
        return arm is not None

    def execute(self, context):
        armature_obj = find_mmd_armature(context)
        if armature_obj is None:
            self.report({"ERROR"}, "No MMD armature found.")
            return {"CANCELLED"}

        marked = []
        seen = set()

        def _mark(action):
            if action and action.name not in seen and not action.asset_data:
                action.asset_mark()
                marked.append(action.name)
                seen.add(action.name)

        # Active bone action
        if armature_obj.animation_data:
            _mark(armature_obj.animation_data.action)
            # NLA-stashed bone actions
            for track in armature_obj.animation_data.nla_tracks:
                for strip in track.strips:
                    _mark(strip.action)

        # Active + NLA morph actions
        for child in armature_obj.children:
            if child.type != "MESH":
                continue
            sk = child.data.shape_keys
            if sk and sk.animation_data:
                _mark(sk.animation_data.action)
                for track in sk.animation_data.nla_tracks:
                    for strip in track.strips:
                        _mark(strip.action)
            break  # only check primary mesh (all share same action)

        if marked:
            self.report({"INFO"}, f"Marked as assets: {', '.join(marked)}")
        else:
            self.report({"INFO"}, "No new actions to mark")
        return {"FINISHED"}


def menu_func_import(self, context):
    self.layout.operator(
        BLENDER_MMD_OT_import_pmx.bl_idname,
        text="MMD model (.pmx/.pmd)",
    )
    self.layout.operator(
        BLENDER_MMD_OT_import_vmd.bl_idname,
        text="MMD motion (.vmd)",
    )


_classes = (
    BLENDER_MMD_OT_import_pmx,
    BLENDER_MMD_OT_import_vmd,
    BLENDER_MMD_OT_build_physics,
    BLENDER_MMD_OT_clear_physics,
    BLENDER_MMD_OT_reset_physics,
    BLENDER_MMD_OT_build_outlines,
    BLENDER_MMD_OT_toggle_mesh_outline,
    BLENDER_MMD_OT_set_mesh_edge_color,
    BLENDER_MMD_OT_remove_outlines,
    BLENDER_MMD_OT_rebuild_ncc,
    BLENDER_MMD_OT_select_chain,
    BLENDER_MMD_OT_remove_chain,
    BLENDER_MMD_OT_toggle_chain_collisions,
    BLENDER_MMD_OT_toggle_chain_physics,
    BLENDER_MMD_OT_clear_animation,
    BLENDER_MMD_OT_toggle_ik,
    BLENDER_MMD_OT_toggle_all_ik,
    BLENDER_MMD_OT_inspect_physics,
    BLENDER_MMD_OT_select_colliders,
    BLENDER_MMD_OT_select_contacts,
    BLENDER_MMD_OT_select_sdef_vertices,
    BLENDER_MMD_OT_bake_sdef,
    BLENDER_MMD_OT_clear_sdef_bake,
    BLENDER_MMD_OT_toggle_sdef,
    BLENDER_MMD_OT_select_mesh_rigid_bodies,
    BLENDER_MMD_OT_delete_mesh,
    BLENDER_MMD_OT_view_import_report,
    BLENDER_MMD_OT_push_to_nla,
    BLENDER_MMD_OT_mark_actions_as_assets,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
