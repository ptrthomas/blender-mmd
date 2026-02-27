"""Blender MMD — PMX/VMD importer for Blender 5.0+"""

import logging

log = logging.getLogger("blender_mmd")
log.setLevel(logging.DEBUG)


def register():
    from . import operators
    operators.register()
    log.info("Blender MMD registered")


def unregister():
    from . import operators
    operators.unregister()
    log.info("Blender MMD unregistered")
