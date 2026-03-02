# SDEF Implementation Plan

Spherical DEFormation — volume-preserving skinning for MMD models in blender-mmd.

## What is SDEF?

Standard linear blend skinning (LBS/BDEF2) interpolates two bone transforms linearly. At joints like elbows and knees, this causes **volume collapse** — the "candy wrapper" artifact where the mesh pinches instead of bending naturally.

SDEF fixes this by storing three extra parameters per vertex — **C** (center), **R0**, **R1** (rotation references) — and using quaternion-weighted spherical interpolation instead of linear blending. The mesh surface stays on a sphere around C, preserving volume.

**Visual impact**: Most noticeable at arm twist (forearm rotation), elbows, knees, and shoulders. Some models also use SDEF on accessories like neckties.

---

## Current state in blender-mmd

### Already done
- PMX parser reads SDEF data: `BoneWeightSDEF` with `bone1`, `bone2`, `weight`, `c`, `r0`, `r1` fields (`pmx/types.py:81`)
- PMD parser converts edge flag and assigns SDEF where applicable
- Coordinate conversion (Y↔Z swap) applied to C/R0/R1 in parser
- Mesh import assigns BDEF2-equivalent weights for SDEF vertices (correct bone assignment, but no spherical correction)
- **(Session 1)** SDEF C/R0/R1 stored as `mmd_sdef_c`/`mmd_sdef_r0`/`mmd_sdef_r1` POINT-domain float3 mesh attributes in `mesh.py`. `mmd_sdef` vertex group (locked). `mmd_has_sdef` + `mmd_sdef_count` on armature. Survives `mesh.separate()`.
- **(Session 1)** SDEF computation module `sdef.py`: `_precompute_sdef_data()`, `compute_sdef_frame()`, `write_mdd()`/`read_mdd()`
- **(Session 1)** 17 unit tests in `tests/test_sdef.py` (preprocessing, identity, rotation, MDD round-trip)
- **(Session 1)** Verified on YYB Miku: 6,032 SDEF vertices across 8 split meshes

### Not yet done
- No baking pipeline (bake_sdef / clear_sdef_bake / toggle)
- No SDEF operators or UI panel
- No visualization operator (select SDEF vertices)
- No visual verification of SDEF vs LBS quality

---

## Architecture decision: Bake-only (no live driver)

**We will NOT implement a per-frame Python driver** like mmd_tools. Their approach runs Python matrix math for every SDEF vertex every frame, which tanks performance on complex models. Instead:

1. Compute SDEF deformation offline (iterate frames, apply math)
2. Write results to **MDD file** (per-frame vertex positions)
3. Apply via **Mesh Cache modifier** — zero-cost playback

This means SDEF is only active after baking an animation. Interactive posing uses standard LBS. This is acceptable because:
- MMD workflow is animation-driven (VMD → playback), not interactive posing
- Bake takes seconds, playback is free
- Users can rebake after changing animation

### Modifier stack interaction

The key challenge: the Mesh Cache modifier provides **absolute vertex positions**, and the Armature modifier also deforms vertices. They can't both be active.

**Solution: per-mesh bake with Armature muting.**

For meshes WITH SDEF vertices:
1. During bake: read fully-deformed positions from evaluated mesh (armature + shape keys applied by depsgraph), overwrite SDEF vertices with our computation, write complete positions to MDD
2. During playback: **mute the Armature modifier**. The Mesh Cache provides all deformation (armature + SDEF combined). Other modifiers (Solidify for outlines) remain active — they stack after Mesh Cache.
3. Shape key morphs on baked meshes are frozen into the bake. This is acceptable because SDEF vertices are typically on body/limb meshes, not face meshes where morph animation matters.

For meshes WITHOUT SDEF vertices (face, eyes, accessories):
- Completely unchanged. Armature modifier + shape keys work normally.
- No MDD file, no Mesh Cache modifier.

This works because `mesh.separate()` splits SDEF and non-SDEF geometry into different objects by material. Arm twist and knee SDEF are on body materials; facial morphs are on face materials.

**On clear/unbake:** restore (unmute) the Armature modifier on affected meshes.

---

## MDD file format

MDD (Motion Dynamics Data) is the simplest per-frame vertex cache format.

**Important: Blender's Mesh Cache modifier reads MDD in big-endian byte order.**

```
Header (big-endian):
  int32    frame_count
  int32    vertex_count

Per frame (frame_count times):
  float32[vertex_count * 3]   vertex positions (x,y,z interleaved, big-endian)
```

Total size: `8 + frame_count * vertex_count * 12` bytes.

Python writing example:
```python
import struct
with open(path, "wb") as f:
    f.write(struct.pack(">ii", frame_count, vertex_count))  # > = big-endian
    for frame_positions in all_frames:
        f.write(struct.pack(f">{vertex_count * 3}f", *frame_positions.flat))
```

The Mesh Cache modifier stores **all vertices** per frame (not just SDEF vertices), so file size = all vertices in the mesh. For a 5K vertex mesh × 300 frames = ~18 MB. Acceptable for animation preview/render.

Blender's **Mesh Cache modifier** reads MDD natively. No external libraries needed.

---

## SDEF algorithm

The core computation per SDEF vertex, derived from mmd_tools and the MMD SDEF specification:

### Inputs per vertex
- `bone0`, `bone1`: the two influencing bones
- `w0`, `w1`: bone weights (normalized, `w0 + w1 = 1.0`)
- `C`: center point (from PMX, in Blender coords after parser conversion)
- `R0`, `R1`: rotation reference points (from PMX, in Blender coords)
- `vertex_co`: **rest-pose** vertex position (from `mesh.vertices[i].co`, before any modifier)

### Preprocessing (once at bind time)
```python
# Weighted center offset
rw = R0 * w0 + R1 * w1

# Convert R0/R1 to offsets from weighted center
r0 = C + R0 - rw
r1 = C + R1 - rw

# Per-vertex constants for deformation
pos_c = vertex_co - C          # vertex position relative to C
cr0 = (C + r0) / 2             # midpoint between C and adjusted R0
cr1 = (C + r1) / 2             # midpoint between C and adjusted R1
```

### Per-frame deformation
```python
# Bone deformation matrices (pose-space relative to bind-pose)
mat0 = bone0.matrix @ bone0.bone.matrix_local.inverted()
mat1 = bone1.matrix @ bone1.bone.matrix_local.inverted()

# Extract rotations as quaternions
rot0 = mat0.to_quaternion()
rot1 = mat1.to_quaternion()

# Ensure shortest rotation path
if rot1.dot(rot0) < 0:
    rot1 = -rot1

# Weighted quaternion blend (NLERP — normalized linear interpolation)
mat_rot = (rot0 * w0 + rot1 * w1).normalized().to_matrix()

# Final vertex position:
#   - Rotate vertex offset around C by blended rotation
#   - Add weighted bone-transformed midpoints
new_pos = (mat_rot @ pos_c) + (mat0 @ cr0) * w0 + (mat1 @ cr1) * w1
```

The key insight: instead of linearly blending two transformed positions (LBS), SDEF **rotates the vertex around C** using a blended quaternion, then adds bone-local correction terms. This keeps vertices on a spherical arc.

---

## Implementation plan

### Step 1: Store SDEF parameters during mesh import

**File:** `blender_mmd/mesh.py` — `create_mesh()`

After vertex group creation, for SDEF vertices:
- Create float3 mesh attributes: `mmd_sdef_c`, `mmd_sdef_r0`, `mmd_sdef_r1`
- Domain: POINT (per-vertex)
- Store the PMX C/R0/R1 values (already in Blender coords from parser)
- Create vertex group `mmd_sdef` with weight 1.0 for all SDEF vertices (for visualization + modifier masking)

These attributes survive `mesh.separate()` (same as `mmd_normal` backup).

**Also store on armature:** `mmd_has_sdef = True` flag + count for panel display.

### Step 2: SDEF computation module

**File:** `blender_mmd/sdef.py` (new)

#### `compute_sdef_frame(armature_obj, mesh_obj, depsgraph, precomputed) -> np.ndarray`

For a single frame, compute SDEF-corrected vertex positions for one mesh:
1. Get the **evaluated mesh** via `mesh_obj.evaluated_get(depsgraph)` — this gives us positions with Armature modifier + shape keys already applied by Blender's depsgraph
2. Read all deformed vertex positions into a NumPy array (these are LBS-deformed)
3. For each unique bone pair in the precomputed SDEF data:
   - Get pose bone matrices: `bone.matrix @ bone.bone.matrix_local.inverted()`
   - Extract quaternions, ensure shortest path (dot product sign flip)
4. For each SDEF vertex: apply the SDEF algorithm, **replacing** the LBS position with the SDEF position
5. Return the full vertex position array (non-SDEF vertices keep their LBS positions, SDEF vertices get corrected)

The `precomputed` dict contains per-vertex constants (`pos_c`, `cr0`, `cr1`, `w0`, `w1`, bone names) computed once at bake start from rest-pose data and SDEF attributes.

#### `bake_sdef(armature_obj, frame_start, frame_end) -> str`

Bake SDEF across frame range:
1. Derive cache directory from .blend filename + armature name, create if needed
2. For each mesh child with SDEF attributes:
   - Precompute per-vertex constants from rest-pose data (pos_c, cr0, cr1, weights, bone names)
   - Allocate frame buffer (frame_count × vertex_count × 3)
3. For each frame in range:
   - `scene.frame_set(frame)` (triggers depsgraph evaluation)
   - Get depsgraph: `context.evaluated_depsgraph_get()`
   - For each SDEF mesh: call `compute_sdef_frame()`, store in frame buffer
4. Write MDD file per mesh (big-endian)
5. Apply Mesh Cache modifier on each mesh (placed after Armature in stack)
6. **Mute the Armature modifier** on baked meshes (MDD provides all deformation)
7. Set `mmd_sdef_baked = True` on armature
8. Return cache directory path

#### `clear_sdef_bake(armature_obj)`

1. For each mesh child with Mesh Cache modifier named `mmd_sdef`:
   - Remove the Mesh Cache modifier
   - **Unmute the Armature modifier** (restore normal deformation)
2. Delete MDD files and empty cache directories
3. Remove `mmd_sdef_baked` from armature

### Step 3: SDEF visualization

**File:** `blender_mmd/operators.py`

#### `BLENDER_MMD_OT_select_sdef_vertices`

Operator to visualize SDEF vertices:
1. Find active mesh child (or iterate all mesh children)
2. Enter edit mode
3. Deselect all
4. Select vertices in the `mmd_sdef` vertex group
5. Report count

This gives immediate visual feedback about which vertices use SDEF.

### Step 4: Operators and panel

**File:** `blender_mmd/operators.py`

#### `BLENDER_MMD_OT_bake_sdef`
- Checks .blend is saved (`bpy.data.is_saved`), cancels with error if not
- Reads frame range from scene (or action range)
- Calls `bake_sdef()`
- Reports bake time and mesh count

#### `BLENDER_MMD_OT_clear_sdef_bake`
- Calls `clear_sdef_bake()`
- Reports cleanup

**File:** `blender_mmd/panels.py`

#### `BLENDER_MMD_PT_sdef` sub-panel under MMD4B main

- **When model has no SDEF:** hidden (don't show panel if no SDEF vertices)
- **When SDEF present, not baked:**
  - Label: "N SDEF vertices across M meshes"
  - "Select SDEF Vertices" button
  - "Bake SDEF" button (uses scene frame range, disabled if .blend is unsaved — shows warning)
- **When SDEF baked:**
  - Label: "Baked: frames X–Y"
  - "Select SDEF Vertices" button
  - "Rebake" button (clear + bake)
  - "Clear Bake" button

---

## Split mesh considerations

Since meshes are split by material, SDEF vertices may span multiple mesh objects. Each mesh gets its own:
- SDEF attributes (preserved by `mesh.separate()`)
- MDD bake file
- Mesh Cache modifier

The bake iterates all mesh children of the armature. Meshes with zero SDEF vertices are skipped (their MDD would be identical to the Armature modifier output).

---

## Preprocessing optimization

The SDEF algorithm has per-vertex constants (`pos_c`, `cr0`, `cr1`) that only depend on rest-pose data. These can be precomputed once at bake start and reused across all frames:

```python
# Precompute once
for each SDEF vertex:
    rw = R0 * w0 + R1 * w1
    r0 = C + R0 - rw
    r1 = C + R1 - rw
    pos_c = vertex_co - C
    cr0 = (C + r0) / 2
    cr1 = (C + r1) / 2

# Per frame: only bone matrices change
for each frame:
    for each bone pair:
        mat0, mat1, rot0, rot1 = get_bone_transforms()
    for each SDEF vertex:
        mat_rot = nlerp(rot0, rot1, w0, w1)
        new_pos = mat_rot @ pos_c + mat0 @ cr0 * w0 + mat1 @ cr1 * w1
```

With NumPy, the per-frame inner loop can be vectorized per bone pair (all vertices sharing the same bone pair computed in one matrix multiply).

---

## MDD file management

### Cache directory

The cache path is **derived from the .blend filename** — no user configuration needed.

**Path formula:**
```
//{blend_name}_sdef/{armature_name}/{mesh_name}.mdd
```

Example: for `miku_scene.blend` with armature "YYB Miku" and mesh "Body01":
```
//miku_scene_sdef/YYB Miku/Body01.mdd
```

**Rules:**
- `.blend` file must be saved before baking (Bake button disabled if unsaved)
- Blend name prefix prevents collision across .blend files in the same directory
- Armature name subdirectory prevents collision with multiple MMD models in one .blend
- If the .blend is renamed, old bake is orphaned — user must rebake
- Path uses Blender's `//` relative prefix so it resolves via `bpy.path.abspath()`
- Mesh Cache modifier `filepath` uses the relative path (moves with the .blend if cache dir is kept alongside)

**Cleanup:**
- `clear_sdef_bake()` removes Mesh Cache modifiers, restores (unmutes) Armature modifiers, deletes MDD files, removes empty cache directories

---

## Testing plan

### Unit tests (no Blender)
- SDEF preprocessing math: given C/R0/R1/weights, verify pos_c/cr0/cr1
- MDD write/read round-trip: write sample data, read back, compare
- SDEF deformation at identity (no bone movement → vertex unchanged)
- SDEF deformation with known rotation → verify against mmd_tools reference

### Integration tests (via blender-agent)
- Import model with SDEF vertices, verify attributes exist on mesh
- Verify `mmd_sdef` vertex group populated with correct count
- Select SDEF vertices operator → correct selection count
- Bake SDEF → MDD file created, Mesh Cache modifier applied
- Clear bake → modifier removed, MDD deleted
- Bake → rebake cycle is clean

### Visual verification
- Test model: `YYB Hatsune Miku_default_1.0ver.pmx` (6,032 SDEF vertices)
- Import + VMD animation
- Screenshot with standard LBS (current)
- Bake SDEF, screenshot same frame
- Compare arm twist area — SDEF should show rounder cross-section

---

## Session plan

### Session 1: Foundation (store + compute + unit tests) — DONE

- [x] Step 1: Store SDEF attributes during mesh import (`mesh.py`)
  - float3 attributes: `mmd_sdef_c`, `mmd_sdef_r0`, `mmd_sdef_r1` (POINT domain)
  - Vertex group: `mmd_sdef` (locked, weight 1.0 for SDEF verts)
  - `mmd_has_sdef` + `mmd_sdef_count` on armature
  - **Gotcha**: must create all attributes first, then look up by name — `attributes.new()` invalidates previously returned RNA references
- [x] Step 2: SDEF computation module (`sdef.py`)
  - `_precompute_sdef_data()` — reads SDEF attrs + rest-pose positions, computes `pos_c`/`cr0`/`cr1` grouped by bone pair
  - `compute_sdef_frame()` — NLERP quaternion blending, replaces LBS positions for SDEF verts
  - `write_mdd()` / `read_mdd()` — big-endian MDD writer and reader
- [x] Step 3: Unit tests (`tests/test_sdef.py`, 17 tests)
  - Preprocessing math (equal, full, asymmetric weights)
  - Identity deformation (origin + offset center)
  - Rotation (90° single bone, blended ±30°, volume preservation vs LBS)
  - MDD round-trip (single/multi frame, large vertex count, file size, endianness)
- [x] Step 4: Integration test via blender-agent
  - YYB Miku: 6,032 SDEF vertices stored correctly across 8 split meshes
  - Attributes survive `mesh.separate()`, non-zero C/R0/R1 confirmed
- [ ] Commit (pending — commit with Session 2 work)

### Session 2: Bake pipeline + UI + visual verification

- [ ] Step 5: Bake/clear functions in `sdef.py`
  - `bake_sdef()` — full frame-range bake to MDD + apply Mesh Cache + mute Armature
  - `clear_sdef_bake()` — remove Mesh Cache + unmute Armature + delete MDD
  - `toggle_sdef(armature_obj)` — swap modifier visibility on SDEF meshes:
    - SDEF on: Mesh Cache `show_viewport=True`, Armature `show_viewport=False`
    - SDEF off: Mesh Cache `show_viewport=False`, Armature `show_viewport=True`
    - Only affects meshes that have a Mesh Cache modifier (i.e. were baked)
    - Instant — no recomputation, just flips modifier visibility
    - Store current state on armature as `mmd_sdef_enabled` (bool)
- [ ] Step 6: SDEF visualization operator (`operators.py`)
  - `BLENDER_MMD_OT_select_sdef_vertices` — enter edit mode, select SDEF verts
- [ ] Step 7: Bake/clear/toggle operators (`operators.py`)
  - `BLENDER_MMD_OT_bake_sdef` — check .blend saved, call `bake_sdef()`
  - `BLENDER_MMD_OT_clear_sdef_bake` — call `clear_sdef_bake()`
  - `BLENDER_MMD_OT_toggle_sdef` — call `toggle_sdef()`, label shows "Disable SDEF" / "Enable SDEF" based on current state
- [ ] Step 8: SDEF panel (`panels.py`)
  - `BLENDER_MMD_PT_sdef` sub-panel with:
    - Bake / Rebake / Clear buttons
    - Toggle SDEF on/off button (only visible when baked) — instant A/B comparison on any frame
    - Select SDEF Vertices button
    - Status label: vertex count, bake frame range, enabled/disabled state
- [ ] Step 9: Visual verification
  - Import YYB Miku + VMD with arm movement (e.g. waving/dancing)
  - Go to a frame with forearm twist or elbow bend
  - Bake SDEF
  - Use toggle button to flip between SDEF and LBS on the same frame
  - **Best test areas**: forearm twist (most dramatic), elbow bend, knee bend
  - **Quick manual test**: unhide Armature bones, select forearm bone, rotate ~90° in pose mode — the "candy wrapper" pinch at the wrist is what SDEF fixes
- [ ] Step 10: Update CLAUDE.md, SPEC.md, commit

---

## References

- mmd_tools SDEF: `../blender_mmd_tools/mmd_tools/core/sdef.py` (driver-based approach, NumPy bulk mode)
- MDD format: Blender Mesh Cache modifier docs, big-endian int32+float32
- MMD SDEF spec: spherical deformation as implemented in MikuMikuDance
- Test model: `YYB Hatsune Miku_default_1.0ver.pmx` — 6,032 SDEF vertices across 20 bone pairs
