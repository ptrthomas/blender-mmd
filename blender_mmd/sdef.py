"""SDEF (Spherical DEFormation) computation and MDD baking.

Provides volume-preserving skinning for MMD models by computing
SDEF-corrected vertex positions and writing them to MDD mesh cache files.
"""

from __future__ import annotations

import logging
import struct
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
            weights.append((vg.name, g.weight))

        # Sort by weight descending, take top 2
        weights.sort(key=lambda x: x[1], reverse=True)
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
            quat = mat.to_quaternion()
        bone_cache[bone_name] = (mat, quat)
        return mat, quat

    # Process each bone pair
    for (bone0_name, bone1_name), vert_indices in precomputed.bone_pairs.items():
        mat0, rot0 = _get_bone_data(bone0_name)
        mat1, rot1 = _get_bone_data(bone1_name)

        # Ensure shortest rotation path
        if rot1.dot(rot0) < 0:
            rot1 = -rot1

        # Convert matrices to numpy for vectorized math
        mat0_np = np.array(mat0, dtype=np.float64)[:3, :3]
        mat1_np = np.array(mat1, dtype=np.float64)[:3, :3]

        for vi_idx in vert_indices:
            vd = precomputed.vertices[vi_idx]

            # Weighted quaternion blend (NLERP)
            blended = rot0 * vd.w0 + rot1 * vd.w1
            blended.normalize()
            mat_rot = np.array(blended.to_matrix(), dtype=np.float64)

            # SDEF position:
            # (mat_rot @ pos_c) + (mat0 @ cr0) * w0 + (mat1 @ cr1) * w1
            new_pos = (
                mat_rot @ vd.pos_c.astype(np.float64)
                + (mat0_np @ vd.cr0.astype(np.float64)) * vd.w0
                + (mat1_np @ vd.cr1.astype(np.float64)) * vd.w1
            )

            positions[vd.index] = new_pos.astype(np.float32)

    return positions


# ---------------------------------------------------------------------------
# MDD writer
# ---------------------------------------------------------------------------

def write_mdd(path: str | Path, frames: list[np.ndarray]) -> None:
    """Write vertex positions to an MDD (Motion Dynamics Data) file.

    MDD is a simple per-frame vertex cache format read by Blender's
    Mesh Cache modifier. **Big-endian** byte order is required.

    Args:
        path: Output file path.
        frames: List of NumPy arrays, each shape (vertex_count, 3).
            All frames must have the same vertex count.
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

        for frame_positions in frames:
            if frame_positions.shape[0] != vertex_count:
                raise ValueError(
                    f"Frame vertex count mismatch: expected {vertex_count}, "
                    f"got {frame_positions.shape[0]}"
                )
            # Flatten to (vertex_count * 3,) and write as big-endian float32
            flat = frame_positions.astype(np.float32).flatten()
            f.write(struct.pack(f">{len(flat)}f", *flat))


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

        frames = []
        floats_per_frame = vertex_count * 3
        bytes_per_frame = floats_per_frame * 4

        for _ in range(frame_count):
            raw = f.read(bytes_per_frame)
            if len(raw) < bytes_per_frame:
                raise ValueError("Unexpected end of MDD file")
            values = struct.unpack(f">{floats_per_frame}f", raw)
            arr = np.array(values, dtype=np.float32).reshape(vertex_count, 3)
            frames.append(arr)

    return frame_count, vertex_count, frames
