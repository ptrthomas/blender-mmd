"""Blender operator layer — thin wrappers around core import logic."""

from __future__ import annotations

import logging

import bpy
from bpy.props import EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

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

    def execute(self, context):
        from .importer import import_pmx

        try:
            armature = import_pmx(self.filepath, self.scale)
            self.report({"INFO"}, f"Imported: {armature.name}")
            return {"FINISHED"}
        except Exception as e:
            log.exception("PMX import failed")
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}


def _find_mmd_armature(context) -> bpy.types.Object | None:
    """Find a blender_mmd-imported armature to apply VMD motion to.

    Priority:
    1. Active object if it's a blender_mmd armature
    2. Only blender_mmd armature in the scene (auto-detect)
    Returns None if no armature found or multiple ambiguous choices.
    """
    active = context.active_object
    if active and active.type == "ARMATURE" and _is_mmd_armature(active):
        return active

    candidates = [
        obj for obj in context.scene.objects
        if obj.type == "ARMATURE" and _is_mmd_armature(obj)
    ]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _is_mmd_armature(obj: bpy.types.Object) -> bool:
    """Check if an armature was imported by blender_mmd (has import_scale property)."""
    return obj.get("import_scale") is not None


class BLENDER_MMD_OT_import_vmd(bpy.types.Operator, ImportHelper):
    """Import a VMD motion file onto an MMD armature"""

    bl_idname = "blender_mmd.import_vmd"
    bl_label = "Import VMD"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".vmd"
    filter_glob: StringProperty(default="*.vmd", options={"HIDDEN"})

    def execute(self, context):
        from .vmd import parse
        from .vmd.importer import import_vmd

        armature_obj = _find_mmd_armature(context)
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
            import_vmd(vmd, armature_obj, scale)
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
            ("cloth", "Cloth", "Detect chains for cloth conversion"),
        ],
        default="none",
    )

    def execute(self, context):
        from .physics import build_physics
        from .pmx import parse

        armature_obj = _find_mmd_armature(context)
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
            build_physics(armature_obj, model, scale, mode=self.mode)
            self.report(
                {"INFO"},
                f"Physics built (mode={self.mode}): "
                f"{len(model.rigid_bodies)} rigid bodies, "
                f"{len(model.joints)} joints",
            )
            return {"FINISHED"}
        except Exception as e:
            log.exception("Physics build failed")
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}


class BLENDER_MMD_OT_clear_physics(bpy.types.Operator):
    """Remove rigid body physics for an MMD model"""

    bl_idname = "blender_mmd.clear_physics"
    bl_label = "Clear MMD Physics"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .physics import clear_physics

        armature_obj = _find_mmd_armature(context)
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


class BLENDER_MMD_OT_convert_chain_to_cloth(bpy.types.Operator):
    """Convert a physics chain to cloth simulation"""

    bl_idname = "blender_mmd.convert_chain_to_cloth"
    bl_label = "Convert Chain to Cloth"
    bl_options = {"REGISTER", "UNDO"}

    chain_index: IntProperty(
        name="Chain Index",
        description="Index of the chain to convert",
        default=0,
        min=0,
    )

    preset: EnumProperty(
        name="Preset",
        description="Cloth preset",
        items=[
            ("cotton", "Cotton", "General purpose (skirts, general)"),
            ("silk", "Silk", "Flowing fabric"),
            ("hair", "Hair", "Hair strands"),
        ],
        default="cotton",
    )

    collision_mesh: StringProperty(
        name="Collision Mesh",
        description="Name of mesh object for collision (e.g. body mesh)",
        default="",
    )

    def execute(self, context):
        import json
        from .chains import Chain
        from .cloth import convert_chain_to_cloth
        from .pmx import parse

        armature_obj = _find_mmd_armature(context)
        if armature_obj is None:
            self.report({"ERROR"}, "No MMD armature found.")
            return {"CANCELLED"}

        chains_json = armature_obj.get("mmd_physics_chains")
        if not chains_json:
            self.report({"ERROR"}, "No chains detected. Run build_physics with mode='cloth' first.")
            return {"CANCELLED"}

        chains_data = json.loads(chains_json)
        if self.chain_index >= len(chains_data):
            self.report({"ERROR"}, f"Chain index {self.chain_index} out of range (0-{len(chains_data)-1}).")
            return {"CANCELLED"}

        cd = chains_data[self.chain_index]
        chain = Chain(**cd)

        filepath = armature_obj.get("pmx_filepath")
        if not filepath:
            self.report({"ERROR"}, "No PMX filepath stored on armature.")
            return {"CANCELLED"}

        scale = armature_obj.get("import_scale", 0.08)
        model = parse(filepath)

        collision_obj = None
        if self.collision_mesh:
            collision_obj = bpy.data.objects.get(self.collision_mesh)

        try:
            cloth_obj = convert_chain_to_cloth(
                armature_obj, chain, model, scale,
                collision_mesh_obj=collision_obj,
                preset=self.preset,
            )
            self.report({"INFO"}, f"Cloth created: {cloth_obj.name}")
            return {"FINISHED"}
        except Exception as e:
            log.exception("Cloth conversion failed")
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}


class BLENDER_MMD_OT_clear_cloth(bpy.types.Operator):
    """Remove cloth simulation objects for an MMD model"""

    bl_idname = "blender_mmd.clear_cloth"
    bl_label = "Clear MMD Cloth"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .cloth import clear_cloth

        armature_obj = _find_mmd_armature(context)
        if armature_obj is None:
            self.report({"ERROR"}, "No MMD armature found.")
            return {"CANCELLED"}

        try:
            clear_cloth(armature_obj)
            self.report({"INFO"}, "Cloth cleared.")
            return {"FINISHED"}
        except Exception as e:
            log.exception("Cloth clear failed")
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}


class BLENDER_MMD_OT_convert_selection_to_cloth(bpy.types.Operator):
    """Convert selected pose bones to cloth simulation"""

    bl_idname = "blender_mmd.convert_selection_to_cloth"
    bl_label = "Convert Selection to Cloth"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (
            context.mode == "POSE"
            and context.active_object is not None
            and _is_mmd_armature(context.active_object)
            and context.selected_pose_bones
        )

    def execute(self, context):
        from .cloth import convert_selection_to_cloth
        from .panels import validate_bone_chain

        armature_obj = context.active_object
        selected = list(context.selected_pose_bones)
        valid, sorted_bones, message = validate_bone_chain(selected)
        if not valid:
            self.report({"ERROR"}, message)
            return {"CANCELLED"}

        bone_names = [pb.name for pb in sorted_bones]
        preset = context.scene.mmd4b_preset
        collision_obj = context.scene.mmd4b_collision_mesh

        try:
            cloth_obj = convert_selection_to_cloth(
                armature_obj,
                bone_names,
                collision_mesh_obj=collision_obj,
                preset=preset,
            )
            self.report({"INFO"}, f"Cloth created: {cloth_obj.name}")
            return {"FINISHED"}
        except Exception as e:
            log.exception("Cloth conversion failed")
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}


class BLENDER_MMD_OT_convert_group_to_cloth(bpy.types.Operator):
    """Convert selected parallel bone chains to a group cloth simulation"""

    bl_idname = "blender_mmd.convert_group_to_cloth"
    bl_label = "Convert Group to Cloth"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (
            context.mode == "POSE"
            and context.active_object is not None
            and _is_mmd_armature(context.active_object)
            and context.selected_pose_bones
        )

    def execute(self, context):
        from .cloth import convert_group_to_cloth
        from .panels import validate_bone_group

        armature_obj = context.active_object
        selected = list(context.selected_pose_bones)
        valid, chains, struts, message = validate_bone_group(
            selected, armature_obj
        )
        if not valid:
            self.report({"ERROR"}, message)
            return {"CANCELLED"}

        preset = context.scene.mmd4b_preset
        collision_obj = context.scene.mmd4b_collision_mesh

        try:
            cloth_obj = convert_group_to_cloth(
                armature_obj,
                chains,
                strut_names=struts if struts else None,
                collision_mesh_obj=collision_obj,
                preset=preset,
            )
            self.report(
                {"INFO"},
                f"Group cloth: {cloth_obj.name} "
                f"({len(chains)} chains)",
            )
            return {"FINISHED"}
        except Exception as e:
            log.exception("Group cloth conversion failed")
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}


class BLENDER_MMD_OT_reset_cloth_sims(bpy.types.Operator):
    """Reset all cloth simulation caches and return to frame 1"""

    bl_idname = "blender_mmd.reset_cloth_sims"
    bl_label = "Reset Cloth Sims"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        armature_obj = _find_mmd_armature(context)
        if armature_obj is None:
            self.report({"ERROR"}, "No MMD armature found.")
            return {"CANCELLED"}

        col_name = armature_obj.get("cloth_collection")
        if not col_name:
            self.report({"INFO"}, "No cloth sims to reset.")
            return {"FINISHED"}

        collection = bpy.data.collections.get(col_name)
        if not collection:
            return {"FINISHED"}

        # Mute cloth constraints so rest pose is clean
        cloth_constraints = []
        for pb in armature_obj.pose.bones:
            for c in pb.constraints:
                if c.name.startswith("mmd_cloth") and not c.mute:
                    c.mute = True
                    cloth_constraints.append(c)

        # Toggle cloth modifiers off/on to force cache reset
        for obj in collection.objects:
            cloth_mod = obj.modifiers.get("Cloth")
            if cloth_mod:
                cloth_mod.show_viewport = False
                cloth_mod.show_viewport = True

        # Go to frame start with rest pose (constraints muted)
        context.scene.frame_set(context.scene.frame_start)
        context.view_layer.update()

        # Unmute cloth constraints — sim starts fresh from rest
        for c in cloth_constraints:
            c.mute = False

        # Evaluate once at frame start so cloth initialises cleanly
        context.scene.frame_set(context.scene.frame_start)

        self.report({"INFO"}, "Cloth sims reset.")
        return {"FINISHED"}


class BLENDER_MMD_OT_select_cloth_bones(bpy.types.Operator):
    """Select the bones belonging to a cloth simulation"""

    bl_idname = "blender_mmd.select_cloth_bones"
    bl_label = "Select Cloth Bones"

    cloth_object_name: StringProperty(
        name="Cloth Object",
        description="Name of the cloth object whose bones to select",
    )

    @classmethod
    def poll(cls, context):
        return context.mode == "POSE"

    def execute(self, context):
        cloth_obj = bpy.data.objects.get(self.cloth_object_name)
        if not cloth_obj:
            self.report({"ERROR"}, f"Cloth object '{self.cloth_object_name}' not found.")
            return {"CANCELLED"}

        bone_names_str = cloth_obj.get("mmd_bone_names", "")
        if not bone_names_str:
            return {"CANCELLED"}

        armature_obj = context.active_object
        bpy.ops.pose.select_all(action="DESELECT")
        count = 0
        for name in bone_names_str.split(","):
            pb = armature_obj.pose.bones.get(name)
            if pb:
                pb.select = True
                count += 1

        self.report({"INFO"}, f"Selected {count} bones")
        return {"FINISHED"}


class BLENDER_MMD_OT_remove_cloth_sim(bpy.types.Operator):
    """Remove a specific cloth simulation"""

    bl_idname = "blender_mmd.remove_cloth_sim"
    bl_label = "Remove Cloth Sim"
    bl_options = {"REGISTER", "UNDO"}

    cloth_object_name: StringProperty(
        name="Cloth Object",
        description="Name of the cloth object to remove",
    )

    def execute(self, context):
        from .cloth import remove_cloth_sim

        armature_obj = _find_mmd_armature(context)
        if armature_obj is None:
            self.report({"ERROR"}, "No MMD armature found.")
            return {"CANCELLED"}

        try:
            remove_cloth_sim(armature_obj, self.cloth_object_name)
            self.report({"INFO"}, f"Removed: {self.cloth_object_name}")
            return {"FINISHED"}
        except Exception as e:
            log.exception("Cloth removal failed")
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
    BLENDER_MMD_OT_convert_chain_to_cloth,
    BLENDER_MMD_OT_clear_cloth,
    BLENDER_MMD_OT_convert_selection_to_cloth,
    BLENDER_MMD_OT_convert_group_to_cloth,
    BLENDER_MMD_OT_reset_cloth_sims,
    BLENDER_MMD_OT_select_cloth_bones,
    BLENDER_MMD_OT_remove_cloth_sim,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
