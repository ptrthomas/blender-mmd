"""Blender operator layer — thin wrappers around core import logic."""

from __future__ import annotations

import logging

import bpy
from bpy.props import FloatProperty, StringProperty
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


def menu_func_import(self, context):
    self.layout.operator(
        BLENDER_MMD_OT_import_pmx.bl_idname,
        text="MMD model (.pmx)",
    )


_classes = (BLENDER_MMD_OT_import_pmx,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
