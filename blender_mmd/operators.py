"""Blender operator layer — thin wrappers around core import logic."""

from __future__ import annotations

import logging

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

from .helpers import find_mmd_armature

log = logging.getLogger("blender_mmd")


class BLENDER_MMD_OT_import_pmx(bpy.types.Operator, ImportHelper):
    """Import a PMX model file"""

    bl_idname = "blender_mmd.import_pmx"
    bl_label = "Import PMX"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".pmx"
    filter_glob: StringProperty(default="*.pmx", options={"HIDDEN"})

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

        try:
            vmd = parse(self.filepath)
            import_vmd(vmd, armature_obj, scale, create_new_action=self.create_new_action)
            self.report(
                {"INFO"},
                f"VMD applied to '{armature_obj.name}': "
                f"{len(vmd.bone_keyframes)} bone kf, "
                f"{len(vmd.morph_keyframes)} morph kf",
            )
            return {"FINISHED"}
        except Exception as e:
            log.exception("VMD import failed")
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}


class BLENDER_MMD_OT_build_physics(bpy.types.Operator):
    """Build physics for an MMD model"""

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

    def execute(self, context):
        from .physics import build_physics
        from .pmx import parse

        armature_obj = find_mmd_armature(context)
        if armature_obj is None:
            self.report({"ERROR"}, "No MMD armature found.")
            return {"CANCELLED"}

        filepath = armature_obj.get("pmx_filepath")
        if not filepath:
            self.report({"ERROR"}, "No PMX filepath stored on armature.")
            return {"CANCELLED"}

        scale = armature_obj.get("import_scale", 0.08)

        try:
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

        influence = 1.0 if self.enable else 0.0
        count = 0
        for pb in armature_obj.pose.bones:
            for c in pb.constraints:
                if c.type == "IK" and c.subtarget:
                    c.influence = influence
                    _set_ik_limit_influence(armature_obj, pb, c, influence)
                    count += 1

        state = "enabled" if self.enable else "disabled"
        self.report({"INFO"}, f"IK {state}: {count} chains")
        return {"FINISHED"}


def _toggle_ik_for_target(armature_obj, target_bone_name: str) -> bool:
    """Toggle IK constraint influence for a given target bone. Returns True if found."""
    for pb in armature_obj.pose.bones:
        for c in pb.constraints:
            if c.type == "IK" and c.subtarget == target_bone_name:
                new_influence = 0.0 if c.influence > 0.5 else 1.0
                c.influence = new_influence
                _set_ik_limit_influence(armature_obj, pb, c, new_influence)
                return True
    return False


def _set_ik_limit_influence(armature_obj, ik_bone, ik_constraint, influence: float):
    """Set influence on LIMIT_ROTATION override constraints in the IK chain."""
    # Walk the IK chain and toggle mmd_ik_limit_override constraints
    bone = ik_bone
    for _ in range(ik_constraint.chain_count):
        if bone is None:
            break
        for c in bone.constraints:
            if c.type == "LIMIT_ROTATION" and c.name == "mmd_ik_limit_override":
                c.influence = influence
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
        if armature_obj.animation_data and armature_obj.animation_data.action:
            action = armature_obj.animation_data.action
            armature_obj.animation_data.action = None
            if action.users == 0:
                bpy.data.actions.remove(action)
            cleared.append("bone keyframes")

        # Reset all pose bones to rest position
        for pb in armature_obj.pose.bones:
            pb.location = (0, 0, 0)
            pb.rotation_quaternion = (1, 0, 0, 0)
            pb.rotation_euler = (0, 0, 0)
            pb.scale = (1, 1, 1)

        # Clear shape key animation on child meshes
        morph_cleared = False
        for child in armature_obj.children:
            if child.type != "MESH":
                continue
            sk = child.data.shape_keys
            if sk is None:
                continue
            if sk.animation_data and sk.animation_data.action:
                action = sk.animation_data.action
                sk.animation_data.action = None
                if action.users == 0:
                    bpy.data.actions.remove(action)
                morph_cleared = True
            # Reset shape key values to 0
            for kb in sk.key_blocks:
                if kb != sk.reference_key:
                    kb.value = 0.0
        if morph_cleared:
            cleared.append("morph keyframes")

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


def menu_func_import(self, context):
    self.layout.operator(
        BLENDER_MMD_OT_import_pmx.bl_idname,
        text="MMD model (.pmx)",
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
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
