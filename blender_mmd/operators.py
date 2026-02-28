"""Blender operator layer — thin wrappers around core import logic."""

from __future__ import annotations

import logging

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty
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

    use_toon_sphere: BoolProperty(
        name="Toon & Sphere Textures",
        description="Include toon and sphere texture nodes in materials (adds overhead)",
        default=False,
    )

    def execute(self, context):
        from .importer import import_pmx

        try:
            armature = import_pmx(self.filepath, self.scale, use_toon_sphere=self.use_toon_sphere)
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
        armature_obj = _find_mmd_armature(context)
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
        armature_obj = _find_mmd_armature(context)
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
    BLENDER_MMD_OT_toggle_ik,
    BLENDER_MMD_OT_toggle_all_ik,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
