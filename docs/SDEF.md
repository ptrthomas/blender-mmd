# SDEF — Spherical DEFormation

Volume-preserving skinning for MMD models in blender-mmd.

## What is SDEF?

Standard linear blend skinning (LBS/BDEF2) interpolates two bone transforms linearly. At joints like elbows and knees, this causes **volume collapse** — the "candy wrapper" artifact where the mesh pinches instead of bending naturally.

SDEF fixes this by storing three extra parameters per vertex — **C** (center), **R0**, **R1** (rotation references) — and using quaternion-weighted spherical interpolation instead of linear blending. The mesh surface stays on a sphere around C, preserving volume.

**Visual impact**: Most noticeable at arm twist (forearm rotation), elbows, knees, and shoulders. Some models also use SDEF on accessories like neckties.

---

## Architecture: bake-to-MDD

We do NOT run a per-frame Python driver like mmd_tools. Instead:

1. Compute SDEF deformation offline (iterate frames, apply math)
2. Write results to **MDD file** (per-frame vertex positions)
3. Apply via **Mesh Cache modifier** — zero-cost playback

SDEF is only active after baking an animation. Interactive posing uses standard LBS. This is acceptable because MMD workflow is animation-driven (VMD playback), not interactive posing.

### Modifier stack

For meshes **with** SDEF vertices (after bake):
- Armature modifier is **muted** — Mesh Cache provides all deformation (armature + SDEF combined)
- Other modifiers (Solidify for outlines) stack after Mesh Cache normally

For meshes **without** SDEF vertices:
- Completely unchanged. Armature + shape keys work normally.

On clear: Armature modifier is unmuted, Mesh Cache removed, MDD files deleted.

### Split mesh handling

Since meshes are split by material, SDEF vertices may span multiple mesh objects. Each gets its own SDEF attributes, MDD file, and Mesh Cache modifier. Meshes with zero SDEF vertices are skipped.

---

## SDEF algorithm

### Inputs per vertex

- `bone0`, `bone1`: the two influencing bones (sorted by vertex group index — see [bone ordering](#bone-ordering))
- `w0`, `w1`: bone weights (normalized, `w0 + w1 = 1.0`)
- `C`: center point (from PMX, in Blender coords after parser Y/Z swap)
- `R0`, `R1`: rotation reference points (from PMX, in Blender coords)
- `vertex_co`: rest-pose vertex position

### Preprocessing (once at bind/bake time)

```python
rw = R0 * w0 + R1 * w1           # weighted center offset

r0 = C + R0 - rw                  # adjusted R0
r1 = C + R1 - rw                  # adjusted R1

pos_c = vertex_co - C             # vertex offset from center
cr0 = (C + r0) / 2                # midpoint: C ↔ adjusted R0
cr1 = (C + r1) / 2                # midpoint: C ↔ adjusted R1
```

### Per-frame deformation

```python
# Bone deformation matrices (pose-space relative to bind-pose)
mat0 = bone0.matrix @ bone0.bone.matrix_local.inverted()
mat1 = bone1.matrix @ bone1.bone.matrix_local.inverted()

# Extract rotations — use YXZ euler intermediate (see quaternion extraction below)
rot0 = mat0.to_euler("YXZ").to_quaternion()
rot1 = mat1.to_euler("YXZ").to_quaternion()

# Ensure shortest rotation path
if rot1.dot(rot0) < 0:
    rot1 = -rot1

# NLERP — normalized linear interpolation of quaternions
mat_rot = (rot0 * w0 + rot1 * w1).normalized().to_matrix()

# Final position: spherical rotation + bone-local correction
new_pos = (mat_rot @ pos_c) + (mat0 @ cr0) * w0 + (mat1 @ cr1) * w1
```

The key insight: instead of linearly blending two transformed positions (LBS), SDEF **rotates the vertex around C** using a blended quaternion, then adds bone-local correction terms. This keeps vertices on a spherical arc.

---

## Critical implementation details

### Bone ordering

**Bones must be sorted by vertex group index (ascending), NOT by weight.**

PMX defines R0 as the rotation reference for bone1 and R1 for bone2, where bone1/bone2 follow PMX bone order. Vertex group indices in Blender preserve PMX bone order. Sorting by weight instead of group index swaps bones for some vertices, causing R0/R1 to map to the wrong bones. The SDEF formula is NOT symmetric in R0 vs R1.

**Symptom**: "pointy" shading at bent joints (knees, elbows) — vertices on one side of the joint arc incorrectly.

```python
# CORRECT — matches mmd_tools, preserves PMX bone order
weights.sort(key=lambda x: x.group_index)

# WRONG — swaps bones when lighter bone has lower group index
weights.sort(key=lambda x: x.weight, reverse=True)
```

### Quaternion extraction

**Use `to_euler("YXZ").to_quaternion()` instead of direct `to_quaternion()`.**

This matches mmd_tools. Direct `mat.to_quaternion()` gives slightly different results for certain rotation combinations. The difference is small (max ~0.004 quaternion distance in testing) but contributes to visible artifacts when combined with other errors.

### Coordinate space

C/R0/R1 are converted from MMD coordinates (Y-up, left-handed) to Blender coordinates (Z-up, right-handed) in the parser via Y/Z swap. All downstream SDEF math operates in Blender coordinate space — same space as bone matrices from `pose_bone.matrix`.

---

## MDD file format

MDD (Motion Dynamics Data) is the simplest per-frame vertex cache format. Blender's Mesh Cache modifier reads it natively.

**Important: big-endian byte order.**

```
Header:
  int32    frame_count       (big-endian)
  int32    vertex_count      (big-endian)

Per frame (frame_count times):
  float32[vertex_count * 3]  vertex positions (x,y,z interleaved, big-endian)
```

Total size: `8 + frame_count × vertex_count × 12` bytes. A 5K vertex mesh at 300 frames is ~18 MB.

### Cache directory

Path derived from .blend filename:
```
{blend_dir}/{blend_stem}_sdef/{armature_name}/{mesh_name}.mdd
```

The .blend must be saved before baking. `clear_sdef_bake()` deletes MDD files and empty cache directories.

---

## Storage

### Mesh attributes (set during PMX import, survive `mesh.separate()`)

| Attribute | Domain | Type | Description |
|-----------|--------|------|-------------|
| `mmd_sdef_c` | POINT | FLOAT_VECTOR | Center point (Blender coords, scaled by `import_scale`) |
| `mmd_sdef_r0` | POINT | FLOAT_VECTOR | Rotation reference 0 |
| `mmd_sdef_r1` | POINT | FLOAT_VECTOR | Rotation reference 1 |

### Vertex group

- `mmd_sdef`: weight 1.0 for SDEF vertices (locked). Used for visualization (Select operator) and modifier masking.

### Armature custom properties

- `mmd_has_sdef` (bool): model contains SDEF vertices
- `mmd_sdef_count` (int): total SDEF vertex count across all meshes
- `mmd_sdef_baked` (bool): SDEF bake is active

---

## UI

MMD4B panel > SDEF sub-panel:

- **Bake**: iterates frame range, computes SDEF positions, writes MDD, applies Mesh Cache, mutes Armature. Requires .blend to be saved.
- **Clear**: removes Mesh Cache, unmutes Armature, deletes MDD files.
- **Toggle**: swap SDEF on/off for instant A/B comparison (mutes/unmutes modifiers, no recomputation).
- **Select**: enters edit mode and selects all SDEF vertices.

---

## References

- mmd_tools SDEF: `../blender_mmd_tools/mmd_tools/core/sdef.py` — `FnSDEF` class (driver-based approach)
- MDD format: Blender Mesh Cache modifier docs
- MMD SDEF spec: spherical deformation as implemented in MikuMikuDance
- Offline comparison test: `tests/test_sdef_reference.py` — verified bind-time constants match mmd_tools exactly (0.0 diff across 5,933 vertices)
