"""Materials & textures — create Blender materials from PMX data.

Uses a single Principled BSDF-based "MMD Shader" node group with optional
toon and sphere texture inputs. Global controls (emission, toon, sphere)
are driven from armature custom properties.

Uses ShaderNodeMix (Blender 4.0+) instead of deprecated ShaderNodeMixRGB.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .pmx.types import Material, Model

log = logging.getLogger("blender_mmd")

# Path to bundled toon textures shipped with the addon
_BUNDLED_TOONS_DIR = os.path.join(os.path.dirname(__file__), "data", "toons")


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


def resolve_shared_toon(pmx_dir: str, toon_idx: int) -> str | None:
    """Resolve shared toon texture path with bundled fallback.

    Search order:
    1. PMX directory
    2. Parent directory
    3. Bundled addon data/toons/
    """
    filename = shared_toon_filename(toon_idx)
    # PMX dir
    path = os.path.join(pmx_dir, filename)
    if os.path.exists(path):
        return path
    # Parent dir
    path = os.path.join(os.path.dirname(pmx_dir), filename)
    if os.path.exists(path):
        return path
    # Bundled fallback
    path = os.path.join(_BUNDLED_TOONS_DIR, filename)
    if os.path.exists(path):
        return path
    return None


# ---------------------------------------------------------------------------
# Blender-dependent code below
# ---------------------------------------------------------------------------

try:
    import bpy
    from mathutils import Vector

    _HAS_BPY = True
except ImportError:
    _HAS_BPY = False


# ---------------------------------------------------------------------------
# ShaderNodeMix helpers (replaces deprecated ShaderNodeMixRGB)
# ---------------------------------------------------------------------------

# ShaderNodeMix with data_type='RGBA' exposes these socket indices:
#   inputs:  0=Factor(float), 1=Factor(vector), 2=A(float), 3=B(float),
#            4=A(vector), 5=B(vector), 6=A(color), 7=B(color),
#            8=A(rotation), 9=B(rotation)
#   outputs: 0=Result(float), 1=Result(vector), 2=Result(color),
#            3=Result(rotation)
_MIX_FAC = 0   # Factor (float) input index
_MIX_A = 6     # A (color) input index
_MIX_B = 7     # B (color) input index
_MIX_OUT = 2   # Result (color) output index


def _new_mix_node(nodes, blend_type, location):
    """Create a ShaderNodeMix configured for RGBA color mixing."""
    node = nodes.new("ShaderNodeMix")
    node.data_type = "RGBA"
    node.blend_type = blend_type
    node.location = location
    return node


# ---------------------------------------------------------------------------
# Node group builders
# ---------------------------------------------------------------------------


def _get_or_create_uv_group() -> "bpy.types.ShaderNodeTree":
    """Get or create the MMD UV node group."""
    group_name = "MMD UV"
    shader = bpy.data.node_groups.get(group_name)
    if shader is not None and len(shader.nodes):
        return shader
    if shader is None:
        shader = bpy.data.node_groups.new(name=group_name, type="ShaderNodeTree")

    nodes = shader.nodes
    links = shader.links

    node_output = nodes.new("NodeGroupOutput")
    node_output.location = (6 * 210, 0)

    tex_coord = nodes.new("ShaderNodeTexCoord")
    tex_coord.location = (0, 0)

    tex_coord1 = nodes.new("ShaderNodeUVMap")
    tex_coord1.location = (4 * 210, -2 * 220)
    tex_coord1.uv_map = "UV1"

    vec_trans = nodes.new("ShaderNodeVectorTransform")
    vec_trans.location = (1 * 210, -1 * 220)
    vec_trans.vector_type = "NORMAL"
    vec_trans.convert_from = "OBJECT"
    vec_trans.convert_to = "CAMERA"

    node_vector = nodes.new("ShaderNodeMapping")
    node_vector.location = (2 * 210, -1 * 220)
    node_vector.vector_type = "POINT"
    node_vector.inputs["Location"].default_value = (0.5, 0.5, 0.0)
    node_vector.inputs["Scale"].default_value = (0.5, 0.5, 1.0)

    links.new(tex_coord.outputs["Normal"], vec_trans.inputs["Vector"])
    links.new(vec_trans.outputs["Vector"], node_vector.inputs["Vector"])

    # Create output sockets and wire them
    for name, output in [
        ("UV", tex_coord.outputs["UV"]),
        ("Toon", node_vector.outputs["Vector"]),
        ("Sphere", node_vector.outputs["Vector"]),
        ("SubTex", tex_coord1.outputs["UV"]),
    ]:
        shader.interface.new_socket(name=name, in_out="OUTPUT", socket_type="NodeSocketVector")
        links.new(output, node_output.inputs[name])

    return shader


def _get_or_create_mmd_shader() -> "bpy.types.ShaderNodeTree":
    """Get or create the MMD Shader node group (Principled BSDF-based).

    Inputs: Color, Alpha, Emission, Roughness,
            Toon Tex, Toon Fac, Sphere Tex, Sphere Fac, Sphere Add
    Output: Shader

    Uses ShaderNodeMix (RGBA mode) instead of deprecated ShaderNodeMixRGB.
    """
    group_name = "MMD Shader"
    shader = bpy.data.node_groups.get(group_name)
    if shader is not None and len(shader.nodes):
        return shader
    if shader is None:
        shader = bpy.data.node_groups.new(name=group_name, type="ShaderNodeTree")

    nodes = shader.nodes
    links = shader.links

    node_input = nodes.new("NodeGroupInput")
    node_input.location = (-5 * 210, 0)
    node_output = nodes.new("NodeGroupOutput")
    node_output.location = (5 * 210, 0)

    # --- Color chain: Color × Toon × Sphere → Principled BSDF ---

    # 1. Toon multiply: Color × Toon Tex (controlled by Toon Fac)
    node_toon = _new_mix_node(nodes, "MULTIPLY", (-3 * 210, 2 * 220))

    # 2. Sphere multiply path: toon_result × Sphere Tex
    node_sph_mul = _new_mix_node(nodes, "MULTIPLY", (-1 * 210, 3 * 220))

    # 3. Sphere add path: toon_result + Sphere Tex
    node_sph_add = _new_mix_node(nodes, "ADD", (-1 * 210, 1 * 220))

    # 4. Sphere mode select: mix between multiply and add result
    node_sphere_select = _new_mix_node(nodes, "MIX", (1 * 210, 2 * 220))

    # 5. Principled BSDF
    node_bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    node_bsdf.location = (3 * 210, 0)
    node_bsdf.inputs["Specular IOR Level"].default_value = 0.0

    # --- Internal links ---

    # Toon result → Sphere multiply and add inputs
    links.new(node_toon.outputs[_MIX_OUT], node_sph_mul.inputs[_MIX_A])
    links.new(node_toon.outputs[_MIX_OUT], node_sph_add.inputs[_MIX_A])

    # Sphere mode select: MIX between multiply result (A) and add result (B)
    links.new(node_sph_mul.outputs[_MIX_OUT], node_sphere_select.inputs[_MIX_A])
    links.new(node_sph_add.outputs[_MIX_OUT], node_sphere_select.inputs[_MIX_B])

    # Final color → Principled BSDF
    links.new(node_sphere_select.outputs[_MIX_OUT], node_bsdf.inputs["Base Color"])
    links.new(node_sphere_select.outputs[_MIX_OUT], node_bsdf.inputs["Emission Color"])

    # BSDF → Group Output
    shader.interface.new_socket(name="Shader", in_out="OUTPUT", socket_type="NodeSocketShader")
    links.new(node_bsdf.outputs["BSDF"], node_output.inputs["Shader"])

    # --- Input sockets ---
    def add_input(name, socket_type, default, target=None, min_val=None, max_val=None):
        isock = shader.interface.new_socket(name=name, in_out="INPUT", socket_type=socket_type)
        if default is not None:
            isock.default_value = default
        if min_val is not None:
            isock.min_value = min_val
        if max_val is not None:
            isock.max_value = max_val
        if target is not None:
            links.new(node_input.outputs[name], target)

    add_input("Color", "NodeSocketColor", (1, 1, 1, 1), node_toon.inputs[_MIX_A])
    add_input("Alpha", "NodeSocketFloat", 1.0, node_bsdf.inputs["Alpha"], 0, 1)
    add_input("Emission", "NodeSocketFloat", 0.3, node_bsdf.inputs["Emission Strength"], 0, 2)
    add_input("Roughness", "NodeSocketFloat", 0.8, node_bsdf.inputs["Roughness"], 0, 1)
    add_input("Toon Tex", "NodeSocketColor", (1, 1, 1, 1), node_toon.inputs[_MIX_B])
    add_input("Toon Fac", "NodeSocketFloat", 0.0, node_toon.inputs[_MIX_FAC], 0, 1)
    add_input("Sphere Tex", "NodeSocketColor", (1, 1, 1, 1))  # wired manually below
    add_input("Sphere Fac", "NodeSocketFloat", 0.0, min_val=0, max_val=1)  # wired manually below
    add_input("Sphere Add", "NodeSocketFloat", 0.0, node_sphere_select.inputs[_MIX_FAC], 0, 1)

    # Wire Sphere Tex to both multiply and add paths
    links.new(node_input.outputs["Sphere Tex"], node_sph_mul.inputs[_MIX_B])
    links.new(node_input.outputs["Sphere Tex"], node_sph_add.inputs[_MIX_B])

    # Wire Sphere Fac to both multiply and add paths
    links.new(node_input.outputs["Sphere Fac"], node_sph_mul.inputs[_MIX_FAC])
    links.new(node_input.outputs["Sphere Fac"], node_sph_add.inputs[_MIX_FAC])

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
# Driver helpers
# ---------------------------------------------------------------------------


def _add_driver(
    node_shader: "bpy.types.ShaderNode",
    input_name: str,
    armature_obj: "bpy.types.Object",
    prop_name: str,
) -> None:
    """Add a driver to a shader group node input, targeting an armature custom property."""
    fcurve = node_shader.inputs[input_name].driver_add("default_value")
    drv = fcurve.driver
    drv.type = "AVERAGE"
    var = drv.variables.new()
    var.name = "val"
    var.type = "SINGLE_PROP"
    target = var.targets[0]
    target.id_type = "OBJECT"
    target.id = armature_obj
    target.data_path = f'["{prop_name}"]'


def _setup_armature_controls(armature_obj: "bpy.types.Object") -> None:
    """Add global material control custom properties to the armature."""
    rna = armature_obj.id_properties_ui

    if "mmd_emission" not in armature_obj:
        armature_obj["mmd_emission"] = 0.3
        ui = rna("mmd_emission")
        ui.update(min=0.0, max=2.0, soft_min=0.0, soft_max=1.0, description="Emission strength for all materials")

    if "mmd_toon_fac" not in armature_obj:
        armature_obj["mmd_toon_fac"] = 1.0
        ui = rna("mmd_toon_fac")
        ui.update(min=0.0, max=1.0, description="Toon texture influence for all materials")

    if "mmd_sphere_fac" not in armature_obj:
        armature_obj["mmd_sphere_fac"] = 1.0
        ui = rna("mmd_sphere_fac")
        ui.update(min=0.0, max=1.0, description="Sphere texture influence for all materials")


def setup_drivers(armature_obj: "bpy.types.Object") -> None:
    """Add drivers to all materials on the mesh child of the armature.

    Must be called after the full import is complete and the depsgraph
    has been flushed, otherwise drivers evaluate as invalid.
    """
    for child in armature_obj.children:
        if child.type != "MESH":
            continue
        for mat in child.data.materials:
            if not mat or not mat.use_nodes:
                continue
            shader = mat.node_tree.nodes.get("Shader")
            if shader is None:
                continue
            # Skip if already has drivers
            ad = mat.node_tree.animation_data
            if ad and ad.drivers:
                continue
            _add_driver(shader, "Emission", armature_obj, "mmd_emission")
            # Check if toon/sphere textures are connected
            has_toon = any(
                link.to_socket.name == "Toon Tex"
                for link in mat.node_tree.links
                if link.to_node == shader
            )
            has_sphere = any(
                link.to_socket.name == "Sphere Tex"
                for link in mat.node_tree.links
                if link.to_node == shader
            )
            if has_toon:
                _add_driver(shader, "Toon Fac", armature_obj, "mmd_toon_fac")
            if has_sphere:
                _add_driver(shader, "Sphere Fac", armature_obj, "mmd_sphere_fac")


def update_materials(armature_obj: "bpy.types.Object") -> None:
    """Update all materials on the mesh to match current armature control values.

    Call after changing mmd_emission, mmd_toon_fac, or mmd_sphere_fac
    if drivers are not working (e.g. auto-execute scripts is disabled).
    """
    emission = armature_obj.get("mmd_emission", 0.3)
    toon_fac = armature_obj.get("mmd_toon_fac", 1.0)
    sphere_fac = armature_obj.get("mmd_sphere_fac", 1.0)

    for child in armature_obj.children:
        if child.type != "MESH":
            continue
        for mat in child.data.materials:
            if not mat or not mat.use_nodes:
                continue
            shader = mat.node_tree.nodes.get("Shader")
            if shader is None:
                continue
            shader.inputs["Emission"].default_value = emission
            if "Toon Fac" in shader.inputs:
                shader.inputs["Toon Fac"].default_value = toon_fac
            if "Sphere Fac" in shader.inputs:
                shader.inputs["Sphere Fac"].default_value = sphere_fac


# ---------------------------------------------------------------------------
# Texture node helpers
# ---------------------------------------------------------------------------


def _create_tex_node(nodes, name, label, filepath, location):
    """Create a ShaderNodeTexImage with loaded image."""
    tex_node = nodes.new("ShaderNodeTexImage")
    tex_node.name = name
    tex_node.label = label
    tex_node.location = location
    tex_node.image = _load_image(filepath)
    return tex_node


def _setup_toon_texture(
    mat_data: Material,
    model: Model,
    nodes,
    links,
    node_shader,
    node_uv,
    tex_paths: list,
    pmx_dir: str,
) -> "bpy.types.ShaderNode | None":
    """Set up toon texture node. Returns the tex node or None."""
    toon_path = None

    if mat_data.toon_sharing == 0:
        # Individual toon texture
        if 0 <= mat_data.toon_texture_index < len(tex_paths):
            candidate = tex_paths[mat_data.toon_texture_index]
            if candidate and os.path.exists(candidate):
                toon_path = candidate
    else:
        # Shared toon texture — with bundled fallback
        toon_path = resolve_shared_toon(pmx_dir, mat_data.toon_texture_index)

    if toon_path is None:
        return None

    toon_tex_node = _create_tex_node(
        nodes, "Toon Texture", "Toon Texture", toon_path,
        node_shader.location + Vector((-3 * 210, -1.5 * 220)),
    )
    links.new(toon_tex_node.outputs["Color"], node_shader.inputs["Toon Tex"])
    links.new(node_uv.outputs["Toon"], toon_tex_node.inputs["Vector"])
    return toon_tex_node


def _setup_sphere_texture(
    mat_data: Material,
    nodes,
    links,
    node_shader,
    node_uv,
    tex_paths: list,
) -> "bpy.types.ShaderNode | None":
    """Set up sphere texture node. Returns the tex node or None."""
    if mat_data.sphere_mode not in (1, 2, 3):
        return None

    sphere_tex_node = None
    if 0 <= mat_data.sphere_texture_index < len(tex_paths):
        sphere_path = tex_paths[mat_data.sphere_texture_index]
        if sphere_path and os.path.exists(sphere_path):
            sphere_tex_node = _create_tex_node(
                nodes, "Sphere Texture", "Sphere Texture", sphere_path,
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
                    node_uv.outputs["SubTex"],
                    sphere_tex_node.inputs["Vector"],
                )
            else:
                links.new(
                    node_uv.outputs["Sphere"],
                    sphere_tex_node.inputs["Vector"],
                )

            links.new(
                sphere_tex_node.outputs["Color"],
                node_shader.inputs["Sphere Tex"],
            )

    # Set sphere mode defaults
    is_sph_add = mat_data.sphere_mode == 2
    node_shader.inputs["Sphere Add"].default_value = 1.0 if is_sph_add else 0.0
    if is_sph_add and sphere_tex_node is None:
        # Additive mode with no texture: default black (add identity)
        node_shader.inputs["Sphere Tex"].default_value = (0, 0, 0, 1)

    return sphere_tex_node


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def create_materials(
    model: Model,
    mesh_obj: "bpy.types.Object",
    filepath: str,
    armature_obj: "bpy.types.Object | None" = None,
) -> None:
    """Create Blender materials from PMX data and assign to mesh faces.

    Args:
        model: Parsed PMX model.
        mesh_obj: The Blender mesh object.
        filepath: Path to the PMX file (for resolving textures).
        armature_obj: The armature object (for driver setup). Optional.
    """
    pmx_dir = os.path.dirname(os.path.abspath(filepath))
    mesh_data = mesh_obj.data

    # Resolve texture paths once
    tex_paths: list[str | None] = []
    for tex in model.textures:
        tex_paths.append(resolve_texture_path(pmx_dir, tex.path))

    # Get/create node groups
    uv_group = _get_or_create_uv_group()
    shader_group = _get_or_create_mmd_shader()

    # Set up armature controls if available
    if armature_obj is not None:
        _setup_armature_controls(armature_obj)
        # Flush depsgraph so custom properties are visible to drivers
        bpy.context.view_layer.update()

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
        node_uv.name = "UV"
        node_uv.node_tree = uv_group
        node_uv.location = (-5 * 210, -2.5 * 220)

        # Shader group node
        node_shader = nodes.new("ShaderNodeGroup")
        node_shader.name = "Shader"
        node_shader.location = (0, 300)
        node_shader.width = 200
        node_shader.node_tree = shader_group

        # Link shader → output
        links.new(node_shader.outputs["Shader"], node_output.inputs["Surface"])

        # Compute derived values
        mixed_color = mix_diffuse_ambient(mat_data.diffuse[:3], mat_data.ambient)
        alpha = mat_data.diffuse[3]
        roughness = roughness_from_shininess(mat_data.shininess)

        # Set shader inputs
        node_shader.inputs["Color"].default_value = (*mixed_color, 1.0)
        node_shader.inputs["Alpha"].default_value = alpha
        node_shader.inputs["Roughness"].default_value = roughness

        # Material viewport properties
        mat.diffuse_color = (*mixed_color, alpha)
        mat.roughness = roughness
        mat.metallic = 0.0
        mat.use_backface_culling = not mat_data.is_double_sided
        if hasattr(mat, "blend_method"):
            mat.blend_method = "HASHED"
        cast_shadows = alpha > 1e-3
        if hasattr(mat, "shadow_method"):
            mat.shadow_method = "HASHED" if cast_shadows else "NONE"

        # Store edge data as custom properties for future outline support
        mat["mmd_edge_color"] = list(mat_data.edge_color)
        mat["mmd_edge_size"] = mat_data.edge_size

        # --- Base texture ---
        base_tex_node = None
        if 0 <= mat_data.texture_index < len(tex_paths):
            tex_path = tex_paths[mat_data.texture_index]
            if tex_path and os.path.exists(tex_path):
                base_tex_node = _create_tex_node(
                    nodes, "Base Texture", "Base Texture", tex_path,
                    node_shader.location + Vector((-4 * 210, -1 * 220)),
                )
                links.new(
                    node_uv.outputs["UV"], base_tex_node.inputs["Vector"]
                )
                links.new(
                    base_tex_node.outputs["Color"],
                    node_shader.inputs["Color"],
                )
                # Multiply PMX alpha with texture alpha (matching mmd_tools)
                if base_tex_node.image and base_tex_node.image.depth == 32 and base_tex_node.image.file_format != "BMP":
                    alpha_mul = nodes.new("ShaderNodeMath")
                    alpha_mul.name = "Alpha Multiply"
                    alpha_mul.operation = "MULTIPLY"
                    alpha_mul.location = node_shader.location + Vector((-1 * 210, -2.5 * 220))
                    alpha_mul.inputs[0].default_value = alpha
                    links.new(base_tex_node.outputs["Alpha"], alpha_mul.inputs[1])
                    links.new(alpha_mul.outputs["Value"], node_shader.inputs["Alpha"])

        # --- Toon texture ---
        has_toon = _setup_toon_texture(
            mat_data, model, nodes, links, node_shader, node_uv,
            tex_paths, pmx_dir,
        ) is not None

        # --- Sphere texture ---
        has_sphere = _setup_sphere_texture(
            mat_data, nodes, links, node_shader, node_uv, tex_paths,
        ) is not None

        # Append material to mesh
        mesh_data.materials.append(mat)

    # --- Per-face material assignment ---
    indices = build_material_indices(model.materials)
    if indices and len(indices) == len(mesh_data.polygons):
        mesh_data.polygons.foreach_set("material_index", indices)
        mesh_data.update()

    # Fix overlapping face materials (z-fighting layers like eye highlights)
    _fix_overlapping_face_materials(mesh_data)

    # Note: drivers are added separately via setup_drivers() after import
    # completes, since the depsgraph needs the armature fully registered.

    log.info(
        "Created %d materials, assigned to %d faces",
        len(model.materials),
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
