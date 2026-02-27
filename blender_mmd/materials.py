"""Materials & textures — create Blender materials from PMX data.

Supports two shader modes:
- "mmd": Full MMDShaderDev node group (toon, sphere, specular, backface culling)
- "simple": TransEmission node group (diffuse + emission + transparency)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .pmx.types import Material, Model

log = logging.getLogger("blender_mmd")


# ---------------------------------------------------------------------------
# Pure-Python helpers (testable without Blender)
# ---------------------------------------------------------------------------


def roughness_from_shininess(s: float) -> float:
    """Convert PMX shininess to Blender roughness. Matches mmd_tools."""
    return 1.0 / pow(max(s, 1), 0.37)


def mix_diffuse_ambient(
    diffuse: tuple[float, ...], ambient: tuple[float, ...]
) -> tuple[float, float, float]:
    """Blend diffuse and ambient colors. Matches mmd_tools."""
    return (
        min(1.0, 0.5 * diffuse[0] + ambient[0]),
        min(1.0, 0.5 * diffuse[1] + ambient[1]),
        min(1.0, 0.5 * diffuse[2] + ambient[2]),
    )


def build_material_indices(materials: list[Material]) -> list[int]:
    """Expand per-material face_count into per-face material_index list."""
    indices = []
    for i, mat in enumerate(materials):
        n_tris = mat.face_count // 3
        indices.extend([i] * n_tris)
    return indices


def resolve_texture_path(pmx_dir: str, rel_path: str) -> str:
    """Normalize backslashes and join with PMX directory."""
    rel_path = rel_path.replace("\\", os.sep)
    return os.path.join(pmx_dir, rel_path)


def shared_toon_filename(index: int) -> str:
    """Shared toon texture filename: 0→toon01.bmp, 9→toon10.bmp."""
    return f"toon{index + 1:02d}.bmp"


# ---------------------------------------------------------------------------
# Blender-dependent code below
# ---------------------------------------------------------------------------

try:
    import bpy
    from mathutils import Vector

    _HAS_BPY = True
except ImportError:
    _HAS_BPY = False


def _get_or_create_uv_group() -> "bpy.types.ShaderNodeTree":
    """Get or create the MMDTexUV node group. Matches mmd_tools."""
    group_name = "MMDTexUV"
    shader = bpy.data.node_groups.get(group_name)
    if shader is not None and len(shader.nodes):
        return shader
    if shader is None:
        shader = bpy.data.node_groups.new(name=group_name, type="ShaderNodeTree")

    ng = _NodeGroupUtils(shader)

    node_output = ng.new_node("NodeGroupOutput", (6, 0))

    tex_coord = ng.new_node("ShaderNodeTexCoord", (0, 0))

    tex_coord1 = ng.new_node("ShaderNodeUVMap", (4, -2))
    tex_coord1.uv_map = "UV1"

    vec_trans = ng.new_node("ShaderNodeVectorTransform", (1, -1))
    vec_trans.vector_type = "NORMAL"
    vec_trans.convert_from = "OBJECT"
    vec_trans.convert_to = "CAMERA"

    node_vector = ng.new_node("ShaderNodeMapping", (2, -1))
    node_vector.vector_type = "POINT"
    node_vector.inputs["Location"].default_value = (0.5, 0.5, 0.0)
    node_vector.inputs["Scale"].default_value = (0.5, 0.5, 1.0)

    links = ng.links
    links.new(tex_coord.outputs["Normal"], vec_trans.inputs["Vector"])
    links.new(vec_trans.outputs["Vector"], node_vector.inputs["Vector"])

    ng.new_output_socket("Base UV", tex_coord.outputs["UV"])
    ng.new_output_socket("Toon UV", node_vector.outputs["Vector"])
    ng.new_output_socket("Sphere UV", node_vector.outputs["Vector"])
    ng.new_output_socket("SubTex UV", tex_coord1.outputs["UV"])

    return shader


def _get_or_create_mmd_shader() -> "bpy.types.ShaderNodeTree":
    """Get or create the MMDShaderDev node group. Matches mmd_tools."""
    group_name = "MMDShaderDev"
    shader = bpy.data.node_groups.get(group_name)
    if shader is not None and len(shader.nodes):
        return shader
    if shader is None:
        shader = bpy.data.node_groups.new(name=group_name, type="ShaderNodeTree")

    ng = _NodeGroupUtils(shader)

    node_input = ng.new_node("NodeGroupInput", (-5, -1))
    node_output = ng.new_node("NodeGroupOutput", (11, 1))

    # Color chain: Ambient + Diffuse → ×BaseTex → ×ToonTex → Sphere
    node_diffuse = ng.new_mix_node("ADD", (-3, 4), fac=0.6)
    node_diffuse.use_clamp = True

    node_tex = ng.new_mix_node("MULTIPLY", (-2, 3.5))
    node_toon = ng.new_mix_node("MULTIPLY", (-1, 3))
    node_sph = ng.new_mix_node("MULTIPLY", (0, 2.5))
    node_spa = ng.new_mix_node("ADD", (0, 1.5))
    node_sphere = ng.new_mix_node("MIX", (1, 1))

    # Backface culling
    node_geo = ng.new_node("ShaderNodeNewGeometry", (6, 3.5))
    node_invert = ng.new_math_node("LESS_THAN", (7, 3))
    node_cull = ng.new_math_node("MAXIMUM", (8, 2.5))
    node_alpha = ng.new_math_node("MINIMUM", (9, 2))
    node_alpha.use_clamp = True

    # Alpha chain
    node_alpha_tex = ng.new_math_node("MULTIPLY", (-1, -2))
    node_alpha_toon = ng.new_math_node("MULTIPLY", (0, -2.5))
    node_alpha_sph = ng.new_math_node("MULTIPLY", (1, -3))

    # Roughness from Reflect
    node_reflect = ng.new_math_node("DIVIDE", (7, -1.5), value1=1)
    node_reflect.use_clamp = True

    # Shader nodes
    shader_diffuse = ng.new_node("ShaderNodeBsdfDiffuse", (8, 0))
    shader_glossy = ng.new_node("ShaderNodeBsdfAnisotropic", (8, -1))
    shader_base_mix = ng.new_node("ShaderNodeMixShader", (9, 0))
    shader_base_mix.inputs["Fac"].default_value = 0.02
    shader_trans = ng.new_node("ShaderNodeBsdfTransparent", (9, 1))
    shader_alpha_mix = ng.new_node("ShaderNodeMixShader", (10, 1))

    links = ng.links

    # Roughness → Glossy
    links.new(node_reflect.outputs["Value"], shader_glossy.inputs["Roughness"])
    links.new(shader_diffuse.outputs["BSDF"], shader_base_mix.inputs[1])
    links.new(shader_glossy.outputs["BSDF"], shader_base_mix.inputs[2])

    # Color chain links
    links.new(node_diffuse.outputs["Color"], node_tex.inputs["Color1"])
    links.new(node_tex.outputs["Color"], node_toon.inputs["Color1"])
    links.new(node_toon.outputs["Color"], node_sph.inputs["Color1"])
    links.new(node_toon.outputs["Color"], node_spa.inputs["Color1"])
    links.new(node_sph.outputs["Color"], node_sphere.inputs["Color1"])
    links.new(node_spa.outputs["Color"], node_sphere.inputs["Color2"])
    links.new(node_sphere.outputs["Color"], shader_diffuse.inputs["Color"])

    # Backface culling links
    links.new(node_geo.outputs["Backfacing"], node_invert.inputs[0])
    links.new(node_invert.outputs["Value"], node_cull.inputs[0])
    links.new(node_cull.outputs["Value"], node_alpha.inputs[0])

    # Alpha chain links
    links.new(node_alpha_tex.outputs["Value"], node_alpha_toon.inputs[0])
    links.new(node_alpha_toon.outputs["Value"], node_alpha_sph.inputs[0])
    links.new(node_alpha_sph.outputs["Value"], node_alpha.inputs[1])

    # Alpha → transparency mix
    links.new(node_alpha.outputs["Value"], shader_alpha_mix.inputs["Fac"])
    links.new(shader_trans.outputs["BSDF"], shader_alpha_mix.inputs[1])
    links.new(shader_base_mix.outputs["Shader"], shader_alpha_mix.inputs[2])

    # --- Input sockets ---
    ng.new_input_socket(
        "Ambient Color", node_diffuse.inputs["Color1"], (0.4, 0.4, 0.4, 1)
    )
    ng.new_input_socket(
        "Diffuse Color", node_diffuse.inputs["Color2"], (0.8, 0.8, 0.8, 1)
    )
    ng.new_input_socket(
        "Specular Color", shader_glossy.inputs["Color"], (0.0, 0.0, 0.0, 1)
    )
    ng.new_input_socket("Reflect", node_reflect.inputs[1], 50, min_max=(1, 512))
    ng.new_input_socket("Base Tex Fac", node_tex.inputs["Fac"], 1)
    ng.new_input_socket("Base Tex", node_tex.inputs["Color2"], (1, 1, 1, 1))
    ng.new_input_socket("Toon Tex Fac", node_toon.inputs["Fac"], 1)
    ng.new_input_socket("Toon Tex", node_toon.inputs["Color2"], (1, 1, 1, 1))
    ng.new_input_socket("Sphere Tex Fac", node_sph.inputs["Fac"], 1)
    ng.new_input_socket("Sphere Tex", node_sph.inputs["Color2"], (1, 1, 1, 1))
    ng.new_input_socket("Sphere Mul/Add", node_sphere.inputs["Fac"], 0)
    ng.new_input_socket("Double Sided", node_cull.inputs[1], 0, min_max=(0, 1))
    ng.new_input_socket("Alpha", node_alpha_tex.inputs[0], 1, min_max=(0, 1))
    ng.new_input_socket("Base Alpha", node_alpha_tex.inputs[1], 1, min_max=(0, 1))
    ng.new_input_socket("Toon Alpha", node_alpha_toon.inputs[1], 1, min_max=(0, 1))
    ng.new_input_socket("Sphere Alpha", node_alpha_sph.inputs[1], 1, min_max=(0, 1))

    # Extra links for sphere add path
    links.new(node_input.outputs["Sphere Tex Fac"], node_spa.inputs["Fac"])
    links.new(node_input.outputs["Sphere Tex"], node_spa.inputs["Color2"])

    # --- Output sockets ---
    ng.new_output_socket("Shader", shader_alpha_mix.outputs["Shader"])
    ng.new_output_socket("Color", node_sphere.outputs["Color"])
    ng.new_output_socket("Alpha", node_alpha.outputs["Value"])

    return shader


def _get_or_create_simple_shader() -> "bpy.types.ShaderNodeTree":
    """Get or create TransEmission node group (simple shader)."""
    group_name = "TransEmission"
    shader = bpy.data.node_groups.get(group_name)
    if shader is not None and len(shader.nodes):
        return shader
    if shader is None:
        shader = bpy.data.node_groups.new(name=group_name, type="ShaderNodeTree")

    ng = _NodeGroupUtils(shader)

    node_input = ng.new_node("NodeGroupInput", (-3, 0))
    node_output = ng.new_node("NodeGroupOutput", (4, 0))

    # Diffuse BSDF
    shader_diffuse = ng.new_node("ShaderNodeBsdfDiffuse", (-1, 1))
    # Emission
    shader_emission = ng.new_node("ShaderNodeEmission", (-1, 0))
    # Mix diffuse + emission (fac = Emission input)
    mix_emission = ng.new_node("ShaderNodeMixShader", (1, 1))
    # Transparent BSDF
    shader_trans = ng.new_node("ShaderNodeBsdfTransparent", (1, -1))
    # Mix (diffuse+emission) with transparent (fac = Alpha)
    mix_alpha = ng.new_node("ShaderNodeMixShader", (3, 0))

    links = ng.links

    # Diffuse + Emission → mix
    links.new(shader_diffuse.outputs["BSDF"], mix_emission.inputs[1])
    links.new(shader_emission.outputs["Emission"], mix_emission.inputs[2])

    # Transparent + combined → final mix
    links.new(shader_trans.outputs["BSDF"], mix_alpha.inputs[1])
    links.new(mix_emission.outputs["Shader"], mix_alpha.inputs[2])

    # --- Input sockets ---
    ng.new_input_socket("Color", shader_diffuse.inputs["Color"], (1, 1, 1, 1))
    ng.new_input_socket("Alpha", mix_alpha.inputs["Fac"], 0.0, min_max=(0, 1))
    ng.new_input_socket("Emission", mix_emission.inputs["Fac"], 0.5, min_max=(0, 1))

    # Color also drives emission color
    links.new(node_input.outputs["Color"], shader_emission.inputs["Color"])
    # Emission strength = 1.0 (mixing handled by fac)
    shader_emission.inputs["Strength"].default_value = 1.0

    # --- Output socket ---
    ng.new_output_socket("Shader", mix_alpha.outputs["Shader"])

    return shader


def _load_image(filepath: str) -> "bpy.types.Image":
    """Load image with dedup by absolute path. Matches mmd_tools."""
    abs_path = os.path.abspath(filepath)

    # Check existing images
    for img in bpy.data.images:
        if img.source == "FILE":
            img_path = bpy.path.abspath(img.filepath)
            if img_path == abs_path:
                return img
            try:
                if os.path.exists(img_path) and os.path.exists(abs_path):
                    if os.path.samefile(img_path, abs_path):
                        return img
            except Exception:
                pass

    # Load new
    try:
        img = bpy.data.images.load(abs_path)
    except Exception:
        log.warning("Cannot load texture '%s'", filepath)
        img = bpy.data.images.new(os.path.basename(filepath), 1, 1)
        img.source = "FILE"
        img.filepath = abs_path

    use_alpha = img.depth == 32 and img.file_format != "BMP"
    if not use_alpha:
        img.alpha_mode = "NONE"

    return img


# ---------------------------------------------------------------------------
# Node group utils (subset of mmd_tools shader.py)
# ---------------------------------------------------------------------------

SOCKET_TYPE_MAPPING = {"NodeSocketFloatFactor": "NodeSocketFloat"}
SOCKET_SUBTYPE_MAPPING = {"NodeSocketFloatFactor": "FACTOR"}


class _NodeGroupUtils:
    """Utility for building shader node groups. Based on mmd_tools."""

    def __init__(self, shader: "bpy.types.ShaderNodeTree"):
        self.shader = shader
        self.nodes = shader.nodes
        self.links = shader.links

    def new_node(self, idname: str, pos: tuple) -> "bpy.types.ShaderNode":
        node = self.nodes.new(idname)
        node.location = (pos[0] * 210, pos[1] * 220)
        return node

    def new_math_node(self, operation, pos, value1=None, value2=None):
        node = self.new_node("ShaderNodeMath", pos)
        node.operation = operation
        if value1 is not None:
            node.inputs[0].default_value = value1
        if value2 is not None:
            node.inputs[1].default_value = value2
        return node

    def new_mix_node(self, blend_type, pos, fac=None, color1=None, color2=None):
        node = self.new_node("ShaderNodeMixRGB", pos)
        node.blend_type = blend_type
        if fac is not None:
            node.inputs["Fac"].default_value = fac
        if color1 is not None:
            node.inputs["Color1"].default_value = color1
        if color2 is not None:
            node.inputs["Color2"].default_value = color2
        return node

    def new_input_socket(self, io_name, socket, default_val=None, min_max=None):
        # Find or create the interface socket
        node_input = self._find_node("NodeGroupInput")
        if node_input is None:
            node_input = self.new_node("NodeGroupInput", (-2, 0))
        io_sockets = node_input.outputs

        if io_name not in io_sockets:
            if socket is not None:
                idname = socket.bl_idname
            else:
                idname = "NodeSocketFloat"
            socket_type = SOCKET_TYPE_MAPPING.get(idname, idname)
            interface_socket = self.shader.interface.new_socket(
                name=io_name, in_out="INPUT", socket_type=socket_type
            )
            if idname in SOCKET_SUBTYPE_MAPPING:
                interface_socket.subtype = SOCKET_SUBTYPE_MAPPING[idname]
            if min_max is None:
                if idname.endswith("Factor") or io_name.endswith("Alpha"):
                    interface_socket.min_value, interface_socket.max_value = 0, 1
                elif idname.endswith(("Float", "Vector")):
                    interface_socket.min_value, interface_socket.max_value = -10, 10
            if default_val is not None:
                interface_socket.default_value = default_val
            if min_max is not None:
                interface_socket.min_value, interface_socket.max_value = min_max

        if socket is not None:
            self.links.new(io_sockets[io_name], socket)

    def new_output_socket(self, io_name, socket, default_val=None, min_max=None):
        node_output = self._find_node("NodeGroupOutput")
        if node_output is None:
            node_output = self.new_node("NodeGroupOutput", (2, 0))
        io_sockets = node_output.inputs

        if io_name not in io_sockets:
            if socket is not None:
                idname = socket.bl_idname
            else:
                idname = "NodeSocketFloat"
            socket_type = SOCKET_TYPE_MAPPING.get(idname, idname)
            interface_socket = self.shader.interface.new_socket(
                name=io_name, in_out="OUTPUT", socket_type=socket_type
            )
            if idname in SOCKET_SUBTYPE_MAPPING:
                interface_socket.subtype = SOCKET_SUBTYPE_MAPPING[idname]
            if min_max is None:
                if idname.endswith("Factor") or io_name.endswith("Alpha"):
                    interface_socket.min_value, interface_socket.max_value = 0, 1
                elif idname.endswith(("Float", "Vector")):
                    interface_socket.min_value, interface_socket.max_value = -10, 10
            if default_val is not None:
                interface_socket.default_value = default_val
            if min_max is not None:
                interface_socket.min_value, interface_socket.max_value = min_max

        if socket is not None:
            self.links.new(socket, io_sockets[io_name])

    def _find_node(self, node_type: str):
        return next(
            (n for n in self.nodes if n.bl_idname == node_type), None
        )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def create_materials(
    model: Model,
    mesh_obj: "bpy.types.Object",
    filepath: str,
    shader_mode: str = "mmd",
) -> None:
    """Create Blender materials from PMX data and assign to mesh faces.

    Args:
        model: Parsed PMX model.
        mesh_obj: The Blender mesh object.
        filepath: Path to the PMX file (for resolving textures).
        shader_mode: "mmd" for full MMDShaderDev, "simple" for TransEmission.
    """
    pmx_dir = os.path.dirname(os.path.abspath(filepath))
    mesh_data = mesh_obj.data

    # Resolve texture paths once
    tex_paths: list[str | None] = []
    for tex in model.textures:
        tex_paths.append(resolve_texture_path(pmx_dir, tex.path))

    # Get/create node groups
    uv_group = _get_or_create_uv_group()
    if shader_mode == "mmd":
        shader_group = _get_or_create_mmd_shader()
    else:
        shader_group = _get_or_create_simple_shader()

    for mat_data in model.materials:
        mat_name = mat_data.name_e if mat_data.name_e else mat_data.name
        mat = bpy.data.materials.new(name=mat_name)
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links

        # Remove default Principled BSDF
        default_bsdf = nodes.get("Principled BSDF")
        if default_bsdf:
            nodes.remove(default_bsdf)

        # Material output
        node_output = None
        for n in nodes:
            if isinstance(n, bpy.types.ShaderNodeOutputMaterial):
                node_output = n
                break
        if node_output is None:
            node_output = nodes.new("ShaderNodeOutputMaterial")
        node_output.location = (400, 300)

        # UV group node
        node_uv = nodes.new("ShaderNodeGroup")
        node_uv.name = "mmd_tex_uv"
        node_uv.node_tree = uv_group
        node_uv.location = (-5 * 210, -2.5 * 220)

        # Shader group node
        node_shader = nodes.new("ShaderNodeGroup")
        node_shader.name = "mmd_shader"
        node_shader.location = (0, 300)
        node_shader.width = 200
        node_shader.node_tree = shader_group

        # Link shader → output
        links.new(node_shader.outputs["Shader"], node_output.inputs["Surface"])

        # Compute derived values
        mixed_color = mix_diffuse_ambient(mat_data.diffuse[:3], mat_data.ambient)
        alpha = mat_data.diffuse[3]

        # Material viewport properties
        mat.diffuse_color = (*mixed_color, alpha)
        mat.roughness = roughness_from_shininess(mat_data.shininess)
        mat.metallic = 0.0
        mat.use_backface_culling = not mat_data.is_double_sided
        if hasattr(mat, "blend_method"):
            mat.blend_method = "HASHED"
        cast_shadows = alpha > 1e-3
        if hasattr(mat, "shadow_method"):
            mat.shadow_method = "HASHED" if cast_shadows else "NONE"

        # --- Base texture ---
        base_tex_node = None
        if 0 <= mat_data.texture_index < len(tex_paths):
            tex_path = tex_paths[mat_data.texture_index]
            if tex_path and os.path.exists(tex_path):
                base_tex_node = nodes.new("ShaderNodeTexImage")
                base_tex_node.name = "mmd_base_tex"
                base_tex_node.label = "Base Tex"
                base_tex_node.location = node_shader.location + Vector(
                    (-4 * 210, -1 * 220)
                )
                base_tex_node.image = _load_image(tex_path)
                # Wire UV
                links.new(
                    node_uv.outputs["Base UV"], base_tex_node.inputs["Vector"]
                )

        if shader_mode == "mmd":
            _setup_mmd_material(
                mat, mat_data, model, nodes, links, node_shader, node_uv,
                base_tex_node, tex_paths, pmx_dir,
            )
        else:
            _setup_simple_material(
                mat, mat_data, nodes, links, node_shader, base_tex_node,
                mixed_color, alpha,
            )

        # Append material to mesh
        mesh_data.materials.append(mat)

    # --- Per-face material assignment ---
    indices = build_material_indices(model.materials)
    if indices and len(indices) == len(mesh_data.polygons):
        mesh_data.polygons.foreach_set("material_index", indices)
        mesh_data.update()

    # Fix overlapping face materials (z-fighting layers like eye highlights)
    # Matches mmd_tools __fixOverlappingFaceMaterials
    _fix_overlapping_face_materials(mesh_data)

    log.info(
        "Created %d materials (mode=%s), assigned to %d faces",
        len(model.materials),
        shader_mode,
        len(indices),
    )


def _fix_overlapping_face_materials(mesh_data: "bpy.types.Mesh") -> None:
    """Detect overlapping faces and set overlay materials to BLEND.

    When two faces share the same vertex positions but belong to different
    materials, the later material is an overlay (e.g. eye highlights).
    Setting it to BLEND with show_transparent_back=False prevents z-fighting.
    Matches mmd_tools __fixOverlappingFaceMaterials.
    """
    if not hasattr(mesh_data.materials[0], "blend_method") if mesh_data.materials else True:
        return

    check: dict[tuple, int] = {}
    mi_skip = -1
    verts = mesh_data.vertices

    for poly in mesh_data.polygons:
        mi = poly.material_index
        if mi <= mi_skip:
            continue
        vis = [mesh_data.loops[li].vertex_index for li in poly.loop_indices]
        key = tuple(sorted(
            (round(verts[vi].co.x, 6), round(verts[vi].co.y, 6), round(verts[vi].co.z, 6))
            for vi in vis
        ))
        if key not in check:
            check[key] = mi
        elif check[key] < mi:
            mat = mesh_data.materials[mi]
            mat.blend_method = "BLEND"
            mat.show_transparent_back = False
            mi_skip = mi
            log.debug("Set BLEND for overlapping material: %s", mat.name)


def _setup_mmd_material(
    mat,
    mat_data: Material,
    model: Model,
    nodes,
    links,
    node_shader,
    node_uv,
    base_tex_node,
    tex_paths: list,
    pmx_dir: str,
) -> None:
    """Set up MMDShaderDev shader inputs and texture nodes."""
    # Set shader inputs
    node_shader.inputs["Ambient Color"].default_value = (
        *mat_data.ambient, 1.0
    )
    node_shader.inputs["Diffuse Color"].default_value = (
        *mat_data.diffuse[:3], 1.0
    )
    node_shader.inputs["Specular Color"].default_value = (
        *mat_data.specular, 1.0
    )
    node_shader.inputs["Reflect"].default_value = mat_data.shininess
    node_shader.inputs["Alpha"].default_value = mat_data.diffuse[3]
    node_shader.inputs["Double Sided"].default_value = (
        1.0 if mat_data.is_double_sided else 0.0
    )

    # Base texture wiring (Fac always 1.0 to match mmd_tools — multiplying
    # by the default white texture is identity when no file is loaded)
    if base_tex_node is not None:
        links.new(
            base_tex_node.outputs["Color"], node_shader.inputs["Base Tex"]
        )
        links.new(
            base_tex_node.outputs["Alpha"], node_shader.inputs["Base Alpha"]
        )

    # --- Toon texture ---
    toon_tex_node = None
    if mat_data.toon_sharing == 0:
        # Individual toon texture
        if 0 <= mat_data.toon_texture_index < len(tex_paths):
            toon_path = tex_paths[mat_data.toon_texture_index]
            if toon_path and os.path.exists(toon_path):
                toon_tex_node = _create_tex_node(
                    nodes, "mmd_toon_tex", "Toon Tex", toon_path,
                    node_shader.location + Vector((-3 * 210, -1.5 * 220)),
                )
    else:
        # Shared toon texture
        toon_filename = shared_toon_filename(mat_data.toon_texture_index)
        # Search in PMX dir first, then common locations
        toon_path = os.path.join(pmx_dir, toon_filename)
        if not os.path.exists(toon_path):
            # Try parent directory
            toon_path = os.path.join(os.path.dirname(pmx_dir), toon_filename)
        if os.path.exists(toon_path):
            toon_tex_node = _create_tex_node(
                nodes, "mmd_toon_tex", "Toon Tex", toon_path,
                node_shader.location + Vector((-3 * 210, -1.5 * 220)),
            )

    if toon_tex_node is not None:
        links.new(
            toon_tex_node.outputs["Color"], node_shader.inputs["Toon Tex"]
        )
        links.new(
            toon_tex_node.outputs["Alpha"], node_shader.inputs["Toon Alpha"]
        )
        links.new(
            node_uv.outputs["Toon UV"], toon_tex_node.inputs["Vector"]
        )
    # Toon Tex Fac stays at default 1.0 (multiply by white = identity when no texture)

    # --- Sphere texture ---
    sphere_tex_node = None
    if mat_data.sphere_mode in (1, 2, 3):
        if 0 <= mat_data.sphere_texture_index < len(tex_paths):
            sphere_path = tex_paths[mat_data.sphere_texture_index]
            if sphere_path and os.path.exists(sphere_path):
                sphere_tex_node = _create_tex_node(
                    nodes, "mmd_sphere_tex", "Sphere Tex", sphere_path,
                    node_shader.location + Vector((-2 * 210, -2 * 220)),
                )

                # Color space: Add mode uses Linear
                is_sph_add = mat_data.sphere_mode == 2
                if hasattr(sphere_tex_node.image, "colorspace_settings"):
                    sphere_tex_node.image.colorspace_settings.name = (
                        "Linear Rec.709" if is_sph_add else "sRGB"
                    )

                # UV mapping: subtex uses UV1, others use Sphere UV
                if mat_data.sphere_mode == 3:
                    links.new(
                        node_uv.outputs["SubTex UV"],
                        sphere_tex_node.inputs["Vector"],
                    )
                else:
                    links.new(
                        node_uv.outputs["Sphere UV"],
                        sphere_tex_node.inputs["Vector"],
                    )

    # Set sphere mode/defaults based on PMX data, regardless of texture existence
    if mat_data.sphere_mode in (1, 2, 3):
        is_sph_add = mat_data.sphere_mode == 2
        node_shader.inputs["Sphere Mul/Add"].default_value = (
            1.0 if is_sph_add else 0.0
        )
        if is_sph_add:
            node_shader.inputs["Sphere Tex"].default_value = (0, 0, 0, 1)
    else:
        node_shader.inputs["Sphere Tex Fac"].default_value = 0.0

    if sphere_tex_node is not None:
        links.new(
            sphere_tex_node.outputs["Color"],
            node_shader.inputs["Sphere Tex"],
        )
        links.new(
            sphere_tex_node.outputs["Alpha"],
            node_shader.inputs["Sphere Alpha"],
        )


def _setup_simple_material(
    mat,
    mat_data: Material,
    nodes,
    links,
    node_shader,
    base_tex_node,
    mixed_color: tuple,
    alpha: float,
) -> None:
    """Set up TransEmission (simple) shader inputs."""
    node_shader.inputs["Alpha"].default_value = alpha
    node_shader.inputs["Emission"].default_value = 0.5

    if base_tex_node is not None:
        # Wire texture color and alpha to shader
        links.new(
            base_tex_node.outputs["Color"], node_shader.inputs["Color"]
        )
        links.new(
            base_tex_node.outputs["Alpha"], node_shader.inputs["Alpha"]
        )
    else:
        # No texture — use mixed diffuse+ambient color
        node_shader.inputs["Color"].default_value = (*mixed_color, 1.0)


def _create_tex_node(nodes, name, label, filepath, location):
    """Create a ShaderNodeTexImage with loaded image."""
    tex_node = nodes.new("ShaderNodeTexImage")
    tex_node.name = name
    tex_node.label = label
    tex_node.location = location
    tex_node.image = _load_image(filepath)
    return tex_node
