# Cloth Simulation for MMD Skirts

Working document for converting rigid body skirt physics to Blender cloth simulation.

## Status: Proof of Concept Complete

Session 1 validated the full pipeline on YYB Hatsune Miku. Key discovery: the Armature modifier blocks Shrinkwrap/SurfaceDeform on the same mesh object. Mesh Deform on a separated skirt mesh is the working solution.

---

## Architecture

### Pipeline Overview

```
                    Armature modifier
                    (top row → LowerBody)
                           ↓
Cage Mesh ──────→ Cloth modifier ──────→ deformed cage
(closed tube,      (use_dynamic_mesh=True,
 from skirt         pin top row)
 material verts)
       ↑                                      ↓
       │                              Mesh Deform modifier
       │                                      ↓
Skirt Mesh ◄──────────────────────── deformed skirt
(separated from                    (follows cage volume)
 main mesh, NO
 Armature modifier)
```

### Three Objects

| Object | Role | Modifiers | Parent |
|--------|------|-----------|--------|
| **Armature** | Skeleton, animation | — | — |
| **Main Mesh** | Body (everything except skirt) | Armature, Collision | Armature |
| **Skirt Mesh** | Separated skirt faces only | Mesh Deform → Cage | None (unparented) |
| **Cage Mesh** | Low-poly cloth proxy | Armature, Cloth | None |

### Why Separate the Skirt?

The Armature modifier prevents Shrinkwrap, Surface Deform, AND Mesh Deform from working on the same object. Confirmed by testing: same mesh, same target — works without Armature modifier, zero effect with it. Separating the skirt into its own mesh (no Armature) solves this completely.

### Why Mesh Deform (not Shrinkwrap or Surface Deform)?

| Method | Result | Why |
|--------|--------|-----|
| **Shrinkwrap** | Weak (0.005 displacement) | Only snaps to surface; doesn't transfer volume deformation |
| **Surface Deform** | Bind fails | Cage is too thin (tube); can't project vertices onto faces |
| **Mesh Deform** | Works (0.083 displacement, 16x better) | Transfers volume deformation; cage caps create closed volume |

Mesh Deform requires a **closed cage** (tube + top/bottom caps).

---

## Cage Construction

### Shape Sampling

The cage is built by sampling the actual skirt mesh geometry, NOT from bone positions (which are much smaller than the rendered skirt).

1. Identify skirt faces by **material name** (e.g. `skirt01*`, `skirt02*`)
2. Collect all vertices from those faces
3. For each (angle, Z) bin, find the **max radius** of skirt vertices
4. Build a cylindrical tube mesh with that profile + small margin (2%)
5. Add **cap faces** (top and bottom ngons) to close the volume
6. Recalculate normals outward

### Parameters

| Param | Value | Notes |
|-------|-------|-------|
| Columns (angular segments) | 24 | Around circumference |
| Rows (Z rings) | 10 | Top to bottom |
| Margin | 2% | Outward from max radius per bin |
| Z range | From material verts | ~0.80 to ~1.05 for Miku |

### Pin Group

Blender cloth `vertex_group_mass` convention:
- **Weight 1.0 = PINNED** (vertex stays locked to armature position)
- **Weight 0.0 = FREE** (vertex simulated by cloth physics)
- Partial weights = spring-like (higher = stiffer)

Top row of cage: weight 1.0 (pinned, follows body via Armature modifier).
All other rows: weight 0.0 (free, cloth physics).

---

## Cloth Settings

### Modifier Stack on Cage

1. **Armature** (first) — top row weighted to `LowerBody` bone
2. **Cloth** (second) — `use_dynamic_mesh = True` is **critical**

`use_dynamic_mesh` recalculates the cloth rest shape each frame from the Armature modifier output. Without it, the pinned vertices don't follow body animation.

### Cloth Parameters (starting point)

```python
cloth.mass = 0.3
cloth.tension_stiffness = 15.0
cloth.compression_stiffness = 15.0
cloth.shear_stiffness = 5.0
cloth.bending_stiffness = 0.5      # low = flowy
cloth.pin_stiffness = 1.0
cloth.use_dynamic_mesh = True
```

### Collision

- **Main mesh** gets a `Collision` modifier (so cloth cage bounces off the body)
- Cage collision settings: `use_collision = True`, `distance_min = 0.001`
- Self-collision: off for now (performance)

---

## Collision Proxy (Future)

For performance, consider creating a **low-poly collision proxy** for the torso/legs region instead of using the full 135k vertex main mesh as a collision object:

- Extract torso/hip/upper-leg faces by material
- Decimate to ~500-1000 faces
- Use as collision object instead of full mesh
- Hide from render

This avoids the cloth solver testing against 135k vertices every frame.

---

## Rigid Body Coexistence

### Current physics system

`build_physics()` supports modes: `"none"`, `"rigid_body"`, `"cloth"` (metadata only). `clear_physics()` removes ALL physics objects, constraints, and metadata.

### For cloth conversion

Rigid body and cloth should NOT coexist on the same bone chains:
- RB uses `COPY_TRANSFORMS`/`COPY_ROTATION` constraints on bones
- Cloth drives the mesh directly via Mesh Deform (no bone constraints)
- IK muting conflicts

### Recommended approach

**Selective conversion** — don't nuke all physics, just the skirt chains:

1. If rigid body physics is active, remove only the skirt chain's RB objects, joints, and bone constraints
2. Keep other chains (hair, accessories) as rigid body
3. Build cage + cloth for the converted chains
4. Store per-chain physics mode in metadata

### Without rigid body (clean import)

If starting from `physics_mode="none"` (default import):
1. No teardown needed
2. Separate skirt mesh by material
3. Build cage from skirt vertices
4. Add Armature + Cloth to cage, Mesh Deform to skirt
5. Add Collision to main mesh

---

## Implementation Steps (TODO)

### Phase 1: Manual workflow (current)
- [x] Prove cage + cloth + Mesh Deform pipeline
- [x] Confirm pin weight convention (1=pinned, 0=free)
- [x] Confirm `use_dynamic_mesh` is required
- [x] Confirm skirt must be separated (no Armature modifier)
- [ ] Test with VMD animation (body motion driving cloth)
- [ ] Tune cloth parameters for natural skirt movement
- [ ] Test collision against body mesh
- [ ] Create collision proxy for performance

### Phase 2: Operator
- [ ] `blender_mmd.build_cloth_skirt` operator
  - Input: armature, material name patterns for skirt
  - Separates skirt mesh
  - Builds cage from material vertices
  - Sets up full modifier stack
  - Handles existing rigid body teardown if needed
- [ ] Add to MMD4B panel (alongside rigid body controls)

### Phase 3: Polish
- [ ] Gradient pin weights (rows near top partially pinned for stiffer waist)
- [ ] Per-model cloth presets (mass, stiffness tuning)
- [ ] Support multiple cloth regions (skirt + cape, etc.)
- [ ] Bake workflow for final renders

---

## Reference

- **UuuNyaa's converter**: `../blender_mmd_tools_append/mmd_tools_append/converters/physics/rigid_body_to_cloth.py` — cage mesh from RB positions, STRETCH_TO bone constraints, Surface Deform binding
- **Blender cloth API**: `vertex_group_mass` is the pin group (confusing name, known issue). Weight 1=pinned, 0=free.
- **Test model**: `YYB Hatsune Miku_default` — 108 skirt bones (18 cols x 6 rows), skirt materials: `skirt01*`, `skirt02*`
