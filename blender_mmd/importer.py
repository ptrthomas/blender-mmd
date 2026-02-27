"""PMX import orchestrator — parse, build armature, build mesh."""

from __future__ import annotations

import logging
from pathlib import Path

import bpy

from .pmx import parse
from .armature import create_armature
from .mesh import create_mesh

log = logging.getLogger("blender_mmd")

DEFAULT_SCALE = 0.08


def import_pmx(filepath: str, scale: float = DEFAULT_SCALE) -> bpy.types.Object:
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
