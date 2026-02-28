"""Blender operator layer — thin wrappers around core import logic."""

from __future__ import annotations

import logging

import bpy
from bpy.props import EnumProperty, FloatProperty, StringProperty, BoolProperty
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


# ---------------------------------------------------------------------------
# Soft Body cage operators (MMD4B panel)
# ---------------------------------------------------------------------------


def _get_mmd_armature_from_context(context) -> bpy.types.Object | None:
    """Get the MMD armature: active object or parent of active object."""
    obj = context.active_object
    if obj and obj.type == "ARMATURE" and _is_mmd_armature(obj):
        return obj
    # If active object is a mesh child of an armature
    if obj and obj.parent and obj.parent.type == "ARMATURE":
        if _is_mmd_armature(obj.parent):
            return obj.parent
    return None


class BLENDER_MMD_OT_generate_cage(bpy.types.Operator):
    """Generate a Soft Body cage for selected bone chain"""

    bl_idname = "blender_mmd.generate_cage"
    bl_label = "Generate Cage"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (
            context.mode == "POSE"
            and context.active_object
            and _is_mmd_armature(context.active_object)
            and context.selected_pose_bones
        )

    def execute(self, context):
        from .panels import validate_bone_chain
        from .softbody import generate_cage

        armature_obj = context.active_object
        selected = list(context.selected_pose_bones)
        valid, sorted_bones, msg = validate_bone_chain(selected)
        if not valid:
            self.report({"ERROR"}, msg)
            return {"CANCELLED"}

        bone_names = [pb.name for pb in sorted_bones]
        stiffness = context.scene.mmd4b_stiffness
        collision = context.scene.mmd4b_collision_mesh

        try:
            cage = generate_cage(armature_obj, bone_names, stiffness, collision)
            self.report({"INFO"}, f"Generated cage: {cage.name}")
            return {"FINISHED"}
        except Exception as e:
            log.exception("Cage generation failed")
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}


class BLENDER_MMD_OT_pin_vertices(bpy.types.Operator):
    """Add selected cage vertices to the goal (pin) group"""

    bl_idname = "blender_mmd.pin_vertices"
    bl_label = "Pin Vertices"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (
            obj is not None
            and obj.type == "MESH"
            and obj.name.startswith("SB_Cage_")
            and context.mode == "EDIT_MESH"
        )

    def execute(self, context):
        import bmesh

        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)
        selected = [v.index for v in bm.verts if v.select]
        if not selected:
            self.report({"WARNING"}, "No vertices selected")
            return {"CANCELLED"}

        goal_vg = obj.vertex_groups.get("goal")
        if goal_vg is None:
            goal_vg = obj.vertex_groups.new(name="goal")

        # Must switch to object mode briefly to modify vertex groups
        bpy.ops.object.mode_set(mode="OBJECT")
        goal_vg.add(selected, 1.0, "REPLACE")
        bpy.ops.object.mode_set(mode="EDIT")

        self.report({"INFO"}, f"Pinned {len(selected)} vertices")
        return {"FINISHED"}


class BLENDER_MMD_OT_unpin_vertices(bpy.types.Operator):
    """Remove selected cage vertices from the goal (pin) group"""

    bl_idname = "blender_mmd.unpin_vertices"
    bl_label = "Unpin Vertices"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (
            obj is not None
            and obj.type == "MESH"
            and obj.name.startswith("SB_Cage_")
            and context.mode == "EDIT_MESH"
        )

    def execute(self, context):
        import bmesh

        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)
        selected = [v.index for v in bm.verts if v.select]
        if not selected:
            self.report({"WARNING"}, "No vertices selected")
            return {"CANCELLED"}

        goal_vg = obj.vertex_groups.get("goal")
        if goal_vg is None:
            self.report({"WARNING"}, "No goal vertex group")
            return {"CANCELLED"}

        bpy.ops.object.mode_set(mode="OBJECT")
        goal_vg.remove(selected)
        bpy.ops.object.mode_set(mode="EDIT")

        self.report({"INFO"}, f"Unpinned {len(selected)} vertices")
        return {"FINISHED"}


class BLENDER_MMD_OT_rebind_surface_deform(bpy.types.Operator):
    """Rebind Surface Deform after cage geometry edits"""

    bl_idname = "blender_mmd.rebind_surface_deform"
    bl_label = "Rebind Surface Deform"
    bl_options = {"REGISTER", "UNDO"}

    cage_name: StringProperty()

    @classmethod
    def poll(cls, context):
        return _get_mmd_armature_from_context(context) is not None

    def execute(self, context):
        import json

        armature_obj = _get_mmd_armature_from_context(context)
        if armature_obj is None:
            self.report({"ERROR"}, "No MMD armature found")
            return {"CANCELLED"}

        raw = armature_obj.get("mmd_softbody_cages")
        if not raw:
            self.report({"ERROR"}, "No cages found")
            return {"CANCELLED"}

        cages = json.loads(raw)
        cage_info = None
        for c in cages:
            if c["cage_name"] == self.cage_name:
                cage_info = c
                break

        if cage_info is None:
            self.report({"ERROR"}, f"Cage '{self.cage_name}' not found")
            return {"CANCELLED"}

        mesh_obj = bpy.data.objects.get(cage_info.get("mesh_name", ""))
        sd_name = cage_info.get("sd_modifier_name", "")
        if not mesh_obj or sd_name not in mesh_obj.modifiers:
            self.report({"ERROR"}, "Surface Deform modifier not found")
            return {"CANCELLED"}

        sd_mod = mesh_obj.modifiers[sd_name]
        # Unbind first if already bound
        if sd_mod.is_bound:
            with bpy.context.temp_override(
                object=mesh_obj, active_object=mesh_obj
            ):
                bpy.ops.object.surfacedeform_bind(modifier=sd_name)

        # Rebind
        with bpy.context.temp_override(
            object=mesh_obj, active_object=mesh_obj
        ):
            bpy.ops.object.surfacedeform_bind(modifier=sd_name)

        self.report({"INFO"}, f"Rebound Surface Deform for {self.cage_name}")
        return {"FINISHED"}


class BLENDER_MMD_OT_reset_soft_body(bpy.types.Operator):
    """Reset Soft Body simulation caches"""

    bl_idname = "blender_mmd.reset_soft_body"
    bl_label = "Reset Sims"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        arm = _get_mmd_armature_from_context(context)
        return arm is not None and arm.get("mmd_softbody_cages") is not None

    def execute(self, context):
        from .softbody import reset_caches

        armature_obj = _get_mmd_armature_from_context(context)
        if armature_obj is None:
            self.report({"ERROR"}, "No MMD armature found")
            return {"CANCELLED"}

        reset_caches(armature_obj)
        self.report({"INFO"}, "Soft body caches reset")
        return {"FINISHED"}


class BLENDER_MMD_OT_remove_cage(bpy.types.Operator):
    """Remove a specific Soft Body cage"""

    bl_idname = "blender_mmd.remove_cage"
    bl_label = "Remove Cage"
    bl_options = {"REGISTER", "UNDO"}

    cage_name: StringProperty()

    @classmethod
    def poll(cls, context):
        return _get_mmd_armature_from_context(context) is not None

    def execute(self, context):
        from .softbody import remove_cage

        armature_obj = _get_mmd_armature_from_context(context)
        if armature_obj is None:
            self.report({"ERROR"}, "No MMD armature found")
            return {"CANCELLED"}

        remove_cage(armature_obj, self.cage_name)
        self.report({"INFO"}, f"Removed cage: {self.cage_name}")
        return {"FINISHED"}


class BLENDER_MMD_OT_clear_all_cages(bpy.types.Operator):
    """Remove all Soft Body cages"""

    bl_idname = "blender_mmd.clear_all_cages"
    bl_label = "Clear All Cages"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        arm = _get_mmd_armature_from_context(context)
        return arm is not None and arm.get("mmd_softbody_cages") is not None

    def execute(self, context):
        from .softbody import clear_all_cages

        armature_obj = _get_mmd_armature_from_context(context)
        if armature_obj is None:
            self.report({"ERROR"}, "No MMD armature found")
            return {"CANCELLED"}

        clear_all_cages(armature_obj)
        self.report({"INFO"}, "All cages cleared")
        return {"FINISHED"}


class BLENDER_MMD_OT_select_cage_bones(bpy.types.Operator):
    """Select bones associated with a cage"""

    bl_idname = "blender_mmd.select_cage_bones"
    bl_label = "Select Cage Bones"

    cage_name: StringProperty()

    @classmethod
    def poll(cls, context):
        return _get_mmd_armature_from_context(context) is not None

    def execute(self, context):
        import json

        armature_obj = _get_mmd_armature_from_context(context)
        if armature_obj is None:
            self.report({"ERROR"}, "No MMD armature found")
            return {"CANCELLED"}

        raw = armature_obj.get("mmd_softbody_cages")
        if not raw:
            self.report({"ERROR"}, "No cages found")
            return {"CANCELLED"}

        cages = json.loads(raw)
        cage_info = None
        for c in cages:
            if c["cage_name"] == self.cage_name:
                cage_info = c
                break

        if cage_info is None:
            self.report({"ERROR"}, f"Cage '{self.cage_name}' not found")
            return {"CANCELLED"}

        # Switch to pose mode and select the bones
        if context.mode != "POSE":
            bpy.ops.object.mode_set(mode="POSE")

        bpy.ops.pose.select_all(action="DESELECT")
        for name in cage_info.get("bone_names", []):
            pb = armature_obj.pose.bones.get(name)
            if pb:
                pb.select = True

        self.report(
            {"INFO"},
            f"Selected {len(cage_info.get('bone_names', []))} bones",
        )
        return {"FINISHED"}


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
    BLENDER_MMD_OT_generate_cage,
    BLENDER_MMD_OT_pin_vertices,
    BLENDER_MMD_OT_unpin_vertices,
    BLENDER_MMD_OT_rebind_surface_deform,
    BLENDER_MMD_OT_reset_soft_body,
    BLENDER_MMD_OT_remove_cage,
    BLENDER_MMD_OT_clear_all_cages,
    BLENDER_MMD_OT_select_cage_bones,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
