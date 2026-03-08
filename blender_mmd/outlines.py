"""Edge/outline rendering using Solidify modifier + Emission edge material.

MMD uses an inverted hull method: mesh geometry extruded outward along normals
with flipped faces. Backface culling makes only silhouette edges visible.

Each material has edge_color (RGBA), edge_size (thickness), and
enabled_toon_edge (flag). Per-vertex edge_scale is stored as the
mmd_edge_scale vertex group.
"""

from __future__ import annotations

import logging

import bpy

log = logging.getLogger("blender_mmd")

# Thickness factor calibrated to match mmd_tools effective outline width.
# MMD edge_size 1.0 at default scale 0.08: 1.0 * 0.08 * 0.05 = 0.004 BU
_THICKNESS_FACTOR = 0.05

_MODIFIER_NAME = "mmd_edge"
_EDGE_MAT_PREFIX = "mmd_edge."


def _on_thickness_mult_update(obj, _context):
    """Auto-apply outline thickness when the slider changes."""
    mod = obj.modifiers.get(_MODIFIER_NAME)
    if not mod:
        return
    base_mat = obj.data.materials[0] if obj.data.materials else None
    if not base_mat:
        return
    arm = obj.parent
    if not arm:
        return
    scale = arm.get("import_scale", 0.08)
    global_mult = arm.mmd_edge_thickness
    edge_size = base_mat.get("mmd_edge_size", 1.0)
    mod.thickness = edge_size * scale * _THICKNESS_FACTOR * global_mult * obj.mmd_edge_thickness_mult


def build_outlines(armature_obj: bpy.types.Object) -> int:
    """Build outline modifiers on all eligible mesh children.

    Returns the number of meshes that received outlines.
    """
    scale = armature_obj.get("import_scale", 0.08)
    global_mult = armature_obj.mmd_edge_thickness
    count = 0

    from .mesh import is_control_mesh
    for obj in armature_obj.children:
        if obj.type != "MESH" or is_control_mesh(obj):
            continue
        mat = obj.data.materials[0] if obj.data.materials else None
        if mat is None:
            continue
        if not mat.get("mmd_edge_enabled", False):
            continue

        edge_color = mat.get("mmd_edge_color", [0.0, 0.0, 0.0, 1.0])
        edge_size = mat.get("mmd_edge_size", 1.0)

        # Create edge material and add as slot 1
        edge_mat = _create_edge_material(mat.name, edge_color)
        obj.data.materials.append(edge_mat)

        # Add Solidify modifier
        mod = obj.modifiers.new(name=_MODIFIER_NAME, type="SOLIDIFY")
        mod.use_flip_normals = True
        mod.use_rim = False
        mod.offset = 1  # extrude outward
        mod.material_offset = 1  # use slot 1 for shell
        per_mesh_mult = obj.mmd_edge_thickness_mult
        mod.thickness = edge_size * scale * _THICKNESS_FACTOR * global_mult * per_mesh_mult

        # Per-vertex thickness via mmd_edge_scale vertex group
        if "mmd_edge_scale" in obj.vertex_groups:
            mod.vertex_group = "mmd_edge_scale"

        count += 1

    if count > 0:
        armature_obj["mmd_outlines_built"] = True

    log.info("Built outlines on %d meshes (global_mult=%.2f)", count, global_mult)
    return count


def _create_edge_material(
    base_name: str,
    color: list | tuple,
) -> bpy.types.Material:
    """Create an unlit edge material using Emission BSDF.

    Uses backface culling so only the outward-facing shell is visible.
    Supports alpha via Mix Shader between Emission and Transparent.
    """
    mat_name = f"{_EDGE_MAT_PREFIX}{base_name}"
    mat = bpy.data.materials.new(name=mat_name)
    mat.use_nodes = True
    mat.use_backface_culling = True
    mat.use_backface_culling_shadow = True
    mat.surface_render_method = "BLENDED"

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    # Find or create output node
    node_output = None
    for n in nodes:
        if isinstance(n, bpy.types.ShaderNodeOutputMaterial):
            node_output = n
            break
    if node_output is None:
        node_output = nodes.new("ShaderNodeOutputMaterial")
    node_output.location = (400, 0)

    # Remove default Principled BSDF
    default_bsdf = nodes.get("Principled BSDF")
    if default_bsdf:
        nodes.remove(default_bsdf)

    r, g, b = color[0], color[1], color[2]
    alpha = color[3] if len(color) > 3 else 1.0

    # Emission shader (unlit, lighting-independent)
    emission = nodes.new("ShaderNodeEmission")
    emission.location = (0, 0)
    emission.inputs["Color"].default_value = (r, g, b, 1.0)
    emission.inputs["Strength"].default_value = 1.0

    if alpha < 1.0 - 1e-3:
        # Mix between Emission and Transparent for alpha edges
        transparent = nodes.new("ShaderNodeBsdfTransparent")
        transparent.location = (0, -150)

        mix = nodes.new("ShaderNodeMixShader")
        mix.location = (200, 0)
        mix.inputs["Fac"].default_value = alpha

        links.new(transparent.outputs["BSDF"], mix.inputs[1])
        links.new(emission.outputs["Emission"], mix.inputs[2])
        links.new(mix.outputs["Shader"], node_output.inputs["Surface"])

        mat.use_transparent_shadow = True
    else:
        links.new(emission.outputs["Emission"], node_output.inputs["Surface"])

    # Viewport display
    mat.diffuse_color = (r, g, b, alpha)

    return mat


def remove_outlines(armature_obj: bpy.types.Object) -> None:
    """Remove all outline modifiers and edge materials."""
    orphaned_mats: set[str] = set()

    for obj in armature_obj.children:
        if obj.type != "MESH":
            continue

        # Remove Solidify modifier
        mod = obj.modifiers.get(_MODIFIER_NAME)
        if mod:
            obj.modifiers.remove(mod)

        # Remove edge material slots (iterate backwards to handle index shifts)
        for i in range(len(obj.data.materials) - 1, -1, -1):
            mat = obj.data.materials[i]
            if mat and mat.name.startswith(_EDGE_MAT_PREFIX):
                orphaned_mats.add(mat.name)
                obj.data.materials.pop(index=i)

    # Delete orphaned edge materials from bpy.data
    for mat_name in orphaned_mats:
        mat = bpy.data.materials.get(mat_name)
        if mat and mat.users == 0:
            bpy.data.materials.remove(mat)

    if "mmd_outlines_built" in armature_obj:
        del armature_obj["mmd_outlines_built"]

    log.info("Removed outlines from armature '%s'", armature_obj.name)


def toggle_mesh_outline(mesh_obj: bpy.types.Object, armature_obj: bpy.types.Object) -> bool:
    """Toggle outline on/off for a single mesh. Returns new enabled state.

    When disabling: removes Solidify modifier and edge material.
    When enabling: re-adds them using stored PMX edge properties.
    """
    mod = mesh_obj.modifiers.get(_MODIFIER_NAME)

    if mod:
        # Disable: remove modifier and edge material
        mesh_obj.modifiers.remove(mod)
        for i in range(len(mesh_obj.data.materials) - 1, -1, -1):
            mat = mesh_obj.data.materials[i]
            if mat and mat.name.startswith(_EDGE_MAT_PREFIX):
                mat_ref = mat
                mesh_obj.data.materials.pop(index=i)
                if mat_ref.users == 0:
                    bpy.data.materials.remove(mat_ref)
        return False
    else:
        # Enable: rebuild from stored properties
        base_mat = mesh_obj.data.materials[0] if mesh_obj.data.materials else None
        if base_mat is None or not base_mat.get("mmd_edge_enabled", False):
            return False

        scale = armature_obj.get("import_scale", 0.08)
        global_mult = armature_obj.mmd_edge_thickness
        per_mesh_mult = mesh_obj.mmd_edge_thickness_mult
        edge_color = base_mat.get("mmd_edge_color", [0.0, 0.0, 0.0, 1.0])
        edge_size = base_mat.get("mmd_edge_size", 1.0)

        edge_mat = _create_edge_material(base_mat.name, edge_color)
        mesh_obj.data.materials.append(edge_mat)

        new_mod = mesh_obj.modifiers.new(name=_MODIFIER_NAME, type="SOLIDIFY")
        new_mod.use_flip_normals = True
        new_mod.use_rim = False
        new_mod.offset = 1
        new_mod.material_offset = 1
        new_mod.thickness = edge_size * scale * _THICKNESS_FACTOR * global_mult * per_mesh_mult

        if "mmd_edge_scale" in mesh_obj.vertex_groups:
            new_mod.vertex_group = "mmd_edge_scale"

        return True


def set_mesh_edge_color(mesh_obj: bpy.types.Object, color: tuple) -> None:
    """Update edge material color on a mesh (instant, no rebuild)."""
    r, g, b, a = color[0], color[1], color[2], color[3] if len(color) > 3 else 1.0
    for mat in mesh_obj.data.materials:
        if mat and mat.name.startswith(_EDGE_MAT_PREFIX):
            # Update Emission node color
            emission = mat.node_tree.nodes.get("Emission")
            if emission:
                emission.inputs["Color"].default_value = (r, g, b, 1.0)
            # Update viewport display
            mat.diffuse_color = (r, g, b, a)
            break

    # Also update stored property on the base material
    base_mat = mesh_obj.data.materials[0] if mesh_obj.data.materials else None
    if base_mat and not base_mat.name.startswith(_EDGE_MAT_PREFIX):
        base_mat["mmd_edge_color"] = list(color)


def update_mesh_outline_thickness(mesh_obj: bpy.types.Object, armature_obj: bpy.types.Object) -> None:
    """Recalculate Solidify thickness from current multipliers."""
    mod = mesh_obj.modifiers.get(_MODIFIER_NAME)
    if not mod:
        return
    base_mat = mesh_obj.data.materials[0] if mesh_obj.data.materials else None
    if not base_mat:
        return

    scale = armature_obj.get("import_scale", 0.08)
    global_mult = armature_obj.mmd_edge_thickness
    per_mesh_mult = mesh_obj.mmd_edge_thickness_mult
    edge_size = base_mat.get("mmd_edge_size", 1.0)
    mod.thickness = edge_size * scale * _THICKNESS_FACTOR * global_mult * per_mesh_mult


def _on_global_thickness_update(self, _context):
    """Auto-apply outline thickness when the global slider changes."""
    if self.type != "ARMATURE":
        return
    from .mesh import is_control_mesh
    for obj in self.children:
        if obj.type != "MESH" or is_control_mesh(obj):
            continue
        update_mesh_outline_thickness(obj, self)


def register():
    from bpy.props import FloatProperty

    bpy.types.Object.mmd_edge_thickness = FloatProperty(
        name="Outline Thickness",
        description="Global outline thickness multiplier",
        default=1.0,
        min=0.1,
        max=5.0,
        soft_min=0.1,
        soft_max=3.0,
        step=10,
        precision=2,
        update=_on_global_thickness_update,
    )

    bpy.types.Object.mmd_edge_thickness_mult = FloatProperty(
        name="Outline Thickness",
        description="Per-mesh outline thickness multiplier",
        default=1.0,
        min=0.0,
        max=5.0,
        soft_min=0.1,
        soft_max=3.0,
        step=10,
        precision=2,
        update=_on_thickness_mult_update,
    )


def unregister():
    if hasattr(bpy.types.Object, "mmd_edge_thickness"):
        del bpy.types.Object.mmd_edge_thickness
    if hasattr(bpy.types.Object, "mmd_edge_thickness_mult"):
        del bpy.types.Object.mmd_edge_thickness_mult
