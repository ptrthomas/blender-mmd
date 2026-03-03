"""SDEF (Spherical DEFormation) computation and MDD baking.

Provides volume-preserving skinning for MMD models by computing
SDEF-corrected vertex positions and writing them to MDD mesh cache files.
"""

from __future__ import annotations

import logging
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger("blender_mmd")


# ---------------------------------------------------------------------------
# Precomputed per-vertex SDEF data
# ---------------------------------------------------------------------------

@dataclass
class SDEFVertexData:
    """Precomputed constants for a single SDEF vertex."""
    index: int           # vertex index in mesh
    bone0: str           # bone name for weight0
    bone1: str           # bone name for weight1
    w0: float            # weight for bone0
    w1: float            # weight for bone1
    pos_c: np.ndarray    # vertex_co - C (3,)
    cr0: np.ndarray      # midpoint(C, adjusted_r0) (3,)
    cr1: np.ndarray      # midpoint(C, adjusted_r1) (3,)


@dataclass
class SDEFMeshData:
    """All precomputed SDEF data for one mesh object."""
    vertices: list[SDEFVertexData] = field(default_factory=list)
    # Grouped by bone pair for vectorized computation
    bone_pairs: dict[tuple[str, str], list[int]] = field(default_factory=dict)


def _precompute_sdef_data(
    mesh_data,
    vertex_groups,
    bone_names_by_group: dict[str, str],
) -> SDEFMeshData:
    """Precompute per-vertex SDEF constants from rest-pose mesh data.

    Args:
        mesh_data: Blender mesh data (bpy.types.Mesh) with mmd_sdef_* attributes.
        vertex_groups: The mesh object's vertex_groups collection.
        bone_names_by_group: Mapping from vertex group name to bone name
            (typically identity, but allows for renaming).

    Returns:
        SDEFMeshData with precomputed constants for all SDEF vertices.
    """
    # Read SDEF attributes
    attr_c = mesh_data.attributes.get("mmd_sdef_c")
    attr_r0 = mesh_data.attributes.get("mmd_sdef_r0")
    attr_r1 = mesh_data.attributes.get("mmd_sdef_r1")
    if not (attr_c and attr_r0 and attr_r1):
        return SDEFMeshData()

    n_verts = len(mesh_data.vertices)

    # Read attribute data into numpy arrays
    c_data = np.zeros(n_verts * 3, dtype=np.float32)
    r0_data = np.zeros(n_verts * 3, dtype=np.float32)
    r1_data = np.zeros(n_verts * 3, dtype=np.float32)
    attr_c.data.foreach_get("vector", c_data)
    attr_r0.data.foreach_get("vector", r0_data)
    attr_r1.data.foreach_get("vector", r1_data)
    c_data = c_data.reshape(-1, 3)
    r0_data = r0_data.reshape(-1, 3)
    r1_data = r1_data.reshape(-1, 3)

    # Read rest-pose vertex positions
    co_data = np.zeros(n_verts * 3, dtype=np.float32)
    mesh_data.vertices.foreach_get("co", co_data)
    co_data = co_data.reshape(-1, 3)

    # Find SDEF vertex group
    vg_sdef = vertex_groups.get("mmd_sdef")
    if vg_sdef is None:
        return SDEFMeshData()
    sdef_group_index = vg_sdef.index

    # Find which vertices are in the SDEF group and get their bone weights
    result = SDEFMeshData()

    for vi in range(n_verts):
        # Check if this vertex is in the mmd_sdef group
        in_sdef = False
        for g in mesh_data.vertices[vi].groups:
            if g.group == sdef_group_index:
                in_sdef = True
                break
        if not in_sdef:
            continue

        # Check that C is not zero (non-SDEF verts have zeroed attributes)
        C = c_data[vi]
        if np.allclose(C, 0.0) and np.allclose(r0_data[vi], 0.0):
            continue

        R0 = r0_data[vi]
        R1 = r1_data[vi]
        vertex_co = co_data[vi]

        # Find the two bone weights for this vertex
        bone0_name = None
        bone1_name = None
        w0 = 0.0
        weights = []
        for g in mesh_data.vertices[vi].groups:
            if g.group == sdef_group_index:
                continue
            vg = vertex_groups[g.group]
            if vg.name.startswith("mmd_"):
                continue
            weights.append((vg.name, g.weight, g.group))

        # Sort by vertex group index ascending (matches mmd_tools).
        # PMX R0 corresponds to bone1, R1 to bone2 — preserving
        # PMX bone order ensures R0/R1 map to the correct bones.
        weights.sort(key=lambda x: x[2])
        if len(weights) < 2:
            continue

        bone0_name = weights[0][0]
        bone1_name = weights[1][0]
        w0 = weights[0][1]
        w1 = weights[1][1]
        # Normalize
        total = w0 + w1
        if total > 0:
            w0 /= total
            w1 /= total

        # Precompute constants (from SDEF.md algorithm)
        rw = R0 * w0 + R1 * w1
        r0 = C + R0 - rw
        r1 = C + R1 - rw
        pos_c = vertex_co - C
        cr0 = (C + r0) * 0.5
        cr1 = (C + r1) * 0.5

        vdata = SDEFVertexData(
            index=vi,
            bone0=bone0_name,
            bone1=bone1_name,
            w0=w0,
            w1=w1,
            pos_c=pos_c.copy(),
            cr0=cr0.copy(),
            cr1=cr1.copy(),
        )
        result.vertices.append(vdata)

        # Group by bone pair
        pair = (bone0_name, bone1_name)
        if pair not in result.bone_pairs:
            result.bone_pairs[pair] = []
        result.bone_pairs[pair].append(len(result.vertices) - 1)

    return result


def compute_sdef_frame(
    armature_obj,
    mesh_obj,
    depsgraph,
    precomputed: SDEFMeshData,
) -> np.ndarray:
    """Compute SDEF-corrected vertex positions for a single frame.

    Reads the evaluated (LBS-deformed) mesh, then replaces SDEF vertex
    positions with spherical deformation results.

    Args:
        armature_obj: The armature object (for pose bone access).
        mesh_obj: The mesh object (unevaluated).
        depsgraph: Current dependency graph.
        precomputed: Precomputed SDEF constants from _precompute_sdef_data().

    Returns:
        NumPy array of shape (vertex_count, 3) with all vertex positions.
        Non-SDEF vertices retain their LBS positions from the depsgraph.
    """
    # Get evaluated mesh (armature + shape keys applied)
    eval_obj = mesh_obj.evaluated_get(depsgraph)
    eval_mesh = eval_obj.data
    n_verts = len(eval_mesh.vertices)

    # Read all deformed positions
    positions = np.zeros(n_verts * 3, dtype=np.float32)
    eval_mesh.vertices.foreach_get("co", positions)
    positions = positions.reshape(-1, 3)

    if not precomputed.vertices:
        return positions

    # Cache bone transform data per bone pair
    from mathutils import Matrix, Quaternion

    bone_cache: dict[str, tuple] = {}  # bone_name -> (mat, quat)

    def _get_bone_data(bone_name: str):
        if bone_name in bone_cache:
            return bone_cache[bone_name]
        pose_bone = armature_obj.pose.bones.get(bone_name)
        if pose_bone is None:
            # Identity fallback
            mat = Matrix.Identity(4)
            quat = Quaternion((1, 0, 0, 0))
        else:
            # Deformation matrix: pose_space @ bind_space_inverse
            mat = pose_bone.matrix @ pose_bone.bone.matrix_local.inverted()
            # Use to_euler("YXZ") then to_quaternion() — matches mmd_tools.
            # Direct to_quaternion() gives slightly different results for
            # some rotation combinations.
            quat = mat.to_euler("YXZ").to_quaternion()
        bone_cache[bone_name] = (mat, quat)
        return mat, quat

    # Process each bone pair
    for (bone0_name, bone1_name), vert_indices in precomputed.bone_pairs.items():
        mat0, rot0 = _get_bone_data(bone0_name)
        mat1, rot1 = _get_bone_data(bone1_name)

        # Ensure shortest rotation path
        if rot1.dot(rot0) < 0:
            rot1 = -rot1

        # Convert to numpy — full 4x4 for cr0/cr1 (need translation)
        mat0_np = np.array(mat0, dtype=np.float64)
        mat1_np = np.array(mat1, dtype=np.float64)

        for vi_idx in vert_indices:
            vd = precomputed.vertices[vi_idx]

            # Weighted quaternion blend (NLERP)
            blended = rot0 * vd.w0 + rot1 * vd.w1
            blended.normalize()
            mat_rot = np.array(blended.to_matrix(), dtype=np.float64)

            # SDEF position:
            # (mat_rot @ pos_c) + (mat0 @ cr0) * w0 + (mat1 @ cr1) * w1
            # mat_rot is 3x3 (pure rotation from blended quat)
            # mat0/mat1 are 4x4 (rotation + translation from bone deformation)
            cr0_h = np.append(vd.cr0.astype(np.float64), 1.0)
            cr1_h = np.append(vd.cr1.astype(np.float64), 1.0)
            new_pos = (
                mat_rot @ vd.pos_c.astype(np.float64)
                + (mat0_np @ cr0_h)[:3] * vd.w0
                + (mat1_np @ cr1_h)[:3] * vd.w1
            )

            positions[vd.index] = new_pos.astype(np.float32)

    return positions


# ---------------------------------------------------------------------------
# MDD writer
# ---------------------------------------------------------------------------

def write_mdd(
    path: str | Path,
    frames: list[np.ndarray],
    fps: float = 30.0,
) -> None:
    """Write vertex positions to an MDD (LightWave PointCache2) file.

    MDD format (all big-endian):
      int32   frame_count
      int32   vertex_count
      float32[frame_count]  timestamps (seconds)
      Per frame:
        float32[vertex_count * 3]  positions (x, y, z interleaved)

    Args:
        path: Output file path.
        frames: List of NumPy arrays, each shape (vertex_count, 3).
            All frames must have the same vertex count.
        fps: Frames per second for timestamp generation (default 30).
    """
    if not frames:
        raise ValueError("No frames to write")

    frame_count = len(frames)
    vertex_count = frames[0].shape[0]

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "wb") as f:
        # Header: frame_count, vertex_count (big-endian int32)
        f.write(struct.pack(">ii", frame_count, vertex_count))

        # Timestamps: one float per frame (seconds)
        timestamps = np.arange(frame_count, dtype=np.float32) / fps
        f.write(timestamps.astype(">f4").tobytes())

        for frame_positions in frames:
            if frame_positions.shape[0] != vertex_count:
                raise ValueError(
                    f"Frame vertex count mismatch: expected {vertex_count}, "
                    f"got {frame_positions.shape[0]}"
                )
            # Write as big-endian float32
            f.write(frame_positions.astype(">f4").tobytes())


def read_mdd(path: str | Path) -> tuple[int, int, list[np.ndarray]]:
    """Read an MDD file back into frame data.

    Returns:
        Tuple of (frame_count, vertex_count, list of position arrays).
        Each position array has shape (vertex_count, 3).
    """
    path = Path(path)
    with open(path, "rb") as f:
        header = f.read(8)
        frame_count, vertex_count = struct.unpack(">ii", header)

        # Skip timestamps
        f.read(frame_count * 4)

        frames = []
        bytes_per_frame = vertex_count * 3 * 4

        for _ in range(frame_count):
            raw = f.read(bytes_per_frame)
            if len(raw) < bytes_per_frame:
                raise ValueError("Unexpected end of MDD file")
            arr = np.frombuffer(raw, dtype=">f4").astype(np.float32).reshape(vertex_count, 3)
            frames.append(arr)

    return frame_count, vertex_count, frames


# ---------------------------------------------------------------------------
# Bake pipeline (requires Blender — bpy)
# ---------------------------------------------------------------------------


def _cache_dir(armature_obj) -> Path:
    """Return the MDD cache directory for this armature, as an absolute path."""
    import bpy

    blend_path = Path(bpy.data.filepath)
    blend_stem = blend_path.stem  # e.g. "miku_scene"
    arm_name = armature_obj.name
    return blend_path.parent / f"{blend_stem}_sdef" / arm_name


def _mesh_has_sdef(mesh_obj) -> bool:
    """Check if a mesh object has SDEF attributes."""
    mesh = mesh_obj.data
    return (
        mesh.attributes.get("mmd_sdef_c") is not None
        and mesh.attributes.get("mmd_sdef_r0") is not None
        and mesh.attributes.get("mmd_sdef_r1") is not None
        and mesh_obj.vertex_groups.get("mmd_sdef") is not None
    )


def _get_sdef_meshes(armature_obj) -> list:
    """Return child mesh objects that have SDEF data."""
    return [
        child for child in armature_obj.children
        if child.type == "MESH" and _mesh_has_sdef(child)
    ]


def bake_sdef(armature_obj, frame_start: int, frame_end: int) -> dict:
    """Bake SDEF deformation to MDD files for all SDEF meshes.

    For each mesh with SDEF vertices:
    1. Precompute per-vertex SDEF constants from rest-pose data
    2. For each frame: evaluate depsgraph (LBS), replace SDEF verts, store
    3. Write MDD file per mesh
    4. Apply Mesh Cache modifier, mute Armature modifier

    Args:
        armature_obj: The MMD armature object.
        frame_start: First frame to bake.
        frame_end: Last frame to bake (inclusive).

    Returns:
        Dict with 'meshes' (count), 'frames' (count), 'time' (seconds),
        'cache_dir' (absolute path string).
    """
    import bpy

    t0 = time.perf_counter()
    scene = bpy.context.scene
    cache = _cache_dir(armature_obj)

    sdef_meshes = _get_sdef_meshes(armature_obj)
    if not sdef_meshes:
        raise RuntimeError("No SDEF meshes found on armature")

    frame_count = frame_end - frame_start + 1

    # Build bone name mapping (vertex group name → bone name, typically identity)
    bone_names = {}
    for bone in armature_obj.data.bones:
        bone_names[bone.name] = bone.name

    # Phase 1: Precompute per-mesh SDEF data
    mesh_data = {}
    for mesh_obj in sdef_meshes:
        pre = _precompute_sdef_data(mesh_obj.data, mesh_obj.vertex_groups, bone_names)
        if pre.vertices:
            mesh_data[mesh_obj] = pre

    if not mesh_data:
        raise RuntimeError("No SDEF vertices found after precomputation")

    # Phase 2: Allocate frame buffers
    frame_buffers: dict = {}  # mesh_obj -> list[np.ndarray]
    for mesh_obj in mesh_data:
        frame_buffers[mesh_obj] = []

    # Phase 3: Iterate frames
    log.info("SDEF bake: %d frames, %d meshes", frame_count, len(mesh_data))

    wm = bpy.context.window_manager
    wm.progress_begin(0, frame_count)

    for i, frame in enumerate(range(frame_start, frame_end + 1)):
        scene.frame_set(frame)
        depsgraph = bpy.context.evaluated_depsgraph_get()

        for mesh_obj, pre in mesh_data.items():
            positions = compute_sdef_frame(armature_obj, mesh_obj, depsgraph, pre)
            frame_buffers[mesh_obj].append(positions)

        wm.progress_update(i + 1)

    wm.progress_end()

    # Phase 4: Write MDD files + apply modifiers
    for mesh_obj, frames in frame_buffers.items():
        # Sanitize mesh name — some models have names like "/armor//belt"
        # which would override the cache directory when joined as a path.
        safe_name = mesh_obj.name.replace("/", "_").replace("\\", "_").strip("_")
        mdd_path = cache / f"{safe_name}.mdd"
        write_mdd(mdd_path, frames)
        log.info("SDEF bake: wrote %s (%d frames, %d verts)",
                 mdd_path, len(frames), frames[0].shape[0])

        # Remove existing Mesh Cache modifier if present
        existing = mesh_obj.modifiers.get("mmd_sdef")
        if existing:
            mesh_obj.modifiers.remove(existing)

        # Add Mesh Cache modifier
        mod = mesh_obj.modifiers.new(name="mmd_sdef", type="MESH_CACHE")
        mod.cache_format = "MDD"
        mod.filepath = str(mdd_path)
        mod.frame_start = frame_start
        # Play mode: index by frame number
        mod.play_mode = "SCENE"
        mod.show_viewport = True
        mod.show_render = True

        # Move Mesh Cache before Armature in modifier stack
        # (we want it to replace armature deformation)
        # Actually, since we mute Armature, order doesn't matter much,
        # but ensure it's high in the stack
        while mesh_obj.modifiers.find("mmd_sdef") > 0:
            bpy.context.view_layer.objects.active = mesh_obj
            bpy.ops.object.modifier_move_up(modifier="mmd_sdef")

        # Mute the Armature modifier (MDD provides all deformation)
        arm_mod = mesh_obj.modifiers.get("Armature")
        if arm_mod:
            arm_mod.show_viewport = False
            arm_mod.show_render = False

    # Phase 5: Store bake metadata on armature
    armature_obj["mmd_sdef_baked"] = True
    armature_obj["mmd_sdef_enabled"] = True
    armature_obj["mmd_sdef_frame_start"] = frame_start
    armature_obj["mmd_sdef_frame_end"] = frame_end

    elapsed = time.perf_counter() - t0
    log.info("SDEF bake complete: %.1fs", elapsed)

    return {
        "meshes": len(mesh_data),
        "frames": frame_count,
        "time": elapsed,
        "cache_dir": str(cache),
    }


def clear_sdef_bake(armature_obj) -> int:
    """Remove SDEF bake: delete Mesh Cache modifiers, restore Armature, delete MDD files.

    Returns:
        Number of meshes cleared.
    """
    import bpy

    count = 0
    for child in armature_obj.children:
        if child.type != "MESH":
            continue
        mod = child.modifiers.get("mmd_sdef")
        if mod is None:
            continue

        # Remove Mesh Cache modifier
        child.modifiers.remove(mod)

        # Unmute Armature modifier
        arm_mod = child.modifiers.get("Armature")
        if arm_mod:
            arm_mod.show_viewport = True
            arm_mod.show_render = True

        count += 1

    # Delete MDD files
    cache = _cache_dir(armature_obj)
    if cache.exists():
        import shutil
        shutil.rmtree(cache, ignore_errors=True)
        # Also remove parent dir if empty
        parent = cache.parent
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()

    # Clear metadata
    for key in ("mmd_sdef_baked", "mmd_sdef_enabled",
                "mmd_sdef_frame_start", "mmd_sdef_frame_end"):
        if key in armature_obj:
            del armature_obj[key]

    log.info("SDEF bake cleared: %d meshes", count)
    return count


def toggle_sdef(armature_obj) -> bool:
    """Toggle SDEF on/off by swapping Mesh Cache vs Armature modifier visibility.

    Only affects meshes that have a baked Mesh Cache modifier.

    Returns:
        New state: True = SDEF active, False = LBS active.
    """
    if not armature_obj.get("mmd_sdef_baked"):
        raise RuntimeError("No SDEF bake to toggle")

    currently_enabled = armature_obj.get("mmd_sdef_enabled", True)
    new_state = not currently_enabled

    for child in armature_obj.children:
        if child.type != "MESH":
            continue
        sdef_mod = child.modifiers.get("mmd_sdef")
        if sdef_mod is None:
            continue

        arm_mod = child.modifiers.get("Armature")

        if new_state:
            # SDEF on: Mesh Cache visible, Armature hidden
            sdef_mod.show_viewport = True
            sdef_mod.show_render = True
            if arm_mod:
                arm_mod.show_viewport = False
                arm_mod.show_render = False
        else:
            # SDEF off (LBS): Mesh Cache hidden, Armature visible
            sdef_mod.show_viewport = False
            sdef_mod.show_render = False
            if arm_mod:
                arm_mod.show_viewport = True
                arm_mod.show_render = True

    armature_obj["mmd_sdef_enabled"] = new_state
    log.info("SDEF toggled: %s", "ON" if new_state else "OFF")
    return new_state
