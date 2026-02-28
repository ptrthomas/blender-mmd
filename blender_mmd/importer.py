"""PMX import orchestrator — parse, build armature, build mesh."""

from __future__ import annotations

import logging
from pathlib import Path

import bpy

from .pmx import parse
from .armature import create_armature
from .mesh import create_mesh
from .materials import create_materials

log = logging.getLogger("blender_mmd")

DEFAULT_SCALE = 0.08


def import_pmx(
    filepath: str, scale: float = DEFAULT_SCALE
) -> bpy.types.Object:
    """Import a PMX file into the current scene.

    Returns the armature object.
    """
    filepath = str(Path(filepath).resolve())
    log.info("Importing PMX: %s (scale=%.4f)", filepath, scale)

    # Parse
    model = parse(filepath)

    # Deselect everything
    bpy.ops.object.select_all(action="DESELECT")

    # Build armature
    armature_obj = create_armature(model, scale)

    # Build mesh
    mesh_obj = create_mesh(model, armature_obj, scale)

    # Create materials and assign to faces (pass armature for driver setup)
    create_materials(model, mesh_obj, filepath, armature_obj=armature_obj)

    # Store filepath for deferred physics build
    armature_obj["pmx_filepath"] = filepath

    # Hide armature bones in viewport (less clutter, still selectable in outliner)
    armature_obj.data.display_type = "WIRE"
    armature_obj.show_in_front = False
    armature_obj.hide_set(True)

    # Select armature as active
    bpy.context.view_layer.objects.active = armature_obj
    armature_obj.select_set(True)

    log.info(
        "Import complete: '%s' — %d bones, %d vertices",
        armature_obj.name,
        len(model.bones),
        len(model.vertices),
    )
    return armature_obj
