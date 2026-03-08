"""Blender MMD — PMX/VMD importer for Blender 5.0+"""

import logging

log = logging.getLogger("blender_mmd")
log.setLevel(logging.DEBUG)


def _restore_morph_sync_handler(*_args):
    """Re-register morph sync handler after file load if any armature has a control mesh."""
    import bpy
    from .mesh import find_control_mesh, _ensure_morph_sync_handler
    for obj in bpy.data.objects:
        if obj.type == "ARMATURE" and find_control_mesh(obj):
            _ensure_morph_sync_handler()
            return


def register():
    import bpy
    from . import materials, operators, outlines, panels
    materials.register()
    outlines.register()
    operators.register()
    panels.register()
    bpy.app.handlers.load_post.append(_restore_morph_sync_handler)
    log.info("Blender MMD registered")


def unregister():
    import bpy
    from . import materials, operators, outlines, panels
    from .mesh import _remove_morph_sync_handler
    _remove_morph_sync_handler()
    bpy.app.handlers.load_post[:] = [
        h for h in bpy.app.handlers.load_post
        if h is not _restore_morph_sync_handler
    ]
    panels.unregister()
    operators.unregister()
    outlines.unregister()
    materials.unregister()
    log.info("Blender MMD unregistered")
