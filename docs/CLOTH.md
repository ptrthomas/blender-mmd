# Cloth Simulation for MMD Models

## Approach

Start with a simple base model (swimsuit/bodysuit type). Build cloth garments on top with clean topology designed for Blender's cloth sim. This gives proper clothes-to-body and clothes-to-clothes collision.

### Pipeline

1. Import PMX base model
2. Delete original garment faces from mesh (by material)
3. Create cloth garment — clean quad topology, user-editable
4. Separate collision body from base mesh (just the skin areas that garments touch)
5. Apply modifiers: Armature + Cloth on garment, Collision on body
6. Optionally bake original textures onto new garment mesh (Selected to Active bake)

### Objects

| Object | Modifiers | Notes |
|--------|-----------|-------|
| **Armature** | — | Skeleton + VMD animation |
| **Base Mesh** | Armature | Body with garment faces deleted, NO Collision |
| **Cloth Garment** | Armature → Cloth | Clean quads, smooth shaded, `use_dynamic_mesh=True` |
| **Collision Body** | Armature → Collision | Separated skin materials (e.g. thighs, torso), follows animation |

### Preserving Original UV/Textures

Original modeler's UV work can be transferred to new cloth mesh:
1. Keep original mesh hidden as reference
2. UV unwrap the new cloth mesh
3. Bake > Diffuse from original (Selected) to new mesh (Active)
4. Use shader nodes for animated/luminous effects that MMD materials had

---

## Key Decisions

- **Garment IS the cloth mesh** — don't try to deform the original hi-res mesh via Surface Deform or Mesh Deform (causes crumpling from normal discontinuities and overlapping decal layers)
- **Targeted collision only** — collide against specific body materials (e.g. `body02` + `pantsu` for thighs), NOT the full mesh. Full mesh causes false collisions with hair, sleeves, etc.
- **Delete original garment faces** — they must be removed from the base mesh, not just hidden. Otherwise cloth collides against original geometry.
- **Small model scale** — MMD models are ~1.5 Blender units tall. Collision distances must be ~0.001, not the typical 0.005+.
- **`use_dynamic_mesh = True`** — critical. Recalculates cloth rest shape from Armature each frame so pinned vertices follow body.
- **Simple topology wins** — a clean cone/cylinder with predictable quads simulates more stably than a decimated or scanned approximation of the original shape.

### Approaches Tried and Rejected

- **Mesh Deform with closed cage**: works but cylindrical cage can't handle non-cylindrical garments
- **Surface Deform to hi-res mesh**: binds OK but crumples during simulation
- **Decimated original as cloth mesh**: irregular topology, unstable sim
- **3D-scan profiling**: shape matches well but body intersection issues, smoothing artifacts
- **Collision against full body mesh**: false collisions everywhere

---

## Cloth Settings

### Pin Group

`vertex_group_mass`: **1.0 = pinned** (follows armature), **0.0 = free** (cloth physics).

Top edge of garment pinned, 2-3 row gradient below for stiff waistband, rest free.

### Parameters (Starting Point)

```python
cloth.quality = 12                  # substeps
cloth.mass = 0.3
cloth.air_damping = 2.0             # prevents rubber oscillation
cloth.tension_stiffness = 15.0
cloth.compression_stiffness = 15.0
cloth.shear_stiffness = 5.0
cloth.bending_stiffness = 0.5       # low = flowy skirt
cloth.tension_damping = 5.0
cloth.compression_damping = 5.0
cloth.shear_damping = 2.0
cloth.bending_damping = 0.5
cloth.pin_stiffness = 1.0
cloth.use_dynamic_mesh = True
```

### Collision Parameters

```python
# On collision body object
collision.thickness_outer = 0.001
collision.thickness_inner = 0.0005
collision.cloth_friction = 5.0

# On cloth garment
collision_settings.distance_min = 0.001
collision_settings.collision_quality = 5
collision_settings.use_self_collision = False  # enable later if needed
```

---

## TODO

### Phase 1: Base Model Workflow
- [ ] Find/prepare simple base model (swimsuit type)
- [ ] Build cloth skirt on base model
- [ ] Get cloth-to-body collision working cleanly
- [ ] Test with VMD animation
- [ ] Tune parameters for natural movement

### Phase 2: Operator
- [ ] `build_cloth_garment` — material selection → face deletion → proxy generation → modifier wiring
- [ ] Add to MMD4B panel
- [ ] Expose cloth params for tuning

### Phase 3: Full Garment System
- [ ] Multiple garment layers with inter-garment collision
- [ ] UV bake workflow for transferring original textures
- [ ] Shader nodes for MMD-style effects (emission, toon)
- [ ] Per-model presets
- [ ] Bake workflow for final renders
