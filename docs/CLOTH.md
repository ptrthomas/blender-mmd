# Cloth Simulation for MMD Models

## Approach: Cage-based deformation

Preserve original meshes (with all materials, textures, emission, alpha effects) and deform them via a low-poly cloth cage.

### Pipeline

1. User selects meshes that make up a garment (e.g. 5 skirt meshes)
2. Generate an open-ended cylinder cage around the selection (N rings x M segments)
3. Shrinkwrap cage onto mesh surface to get initial shape
4. User edits cage to better fit model contours (optional)
5. Auto-weight cage to armature bones
6. Add Cloth modifier to cage with pin group on top ring(s)
7. Bind original meshes to cage via Mesh Deform
8. User picks body meshes for collision (e.g. thighs, pantsu)
9. Play animation — cage simulates cloth, original meshes follow

### Why this approach

- **Preserves original materials** — no need to rebake textures, emission, alpha, decals
- **One cage per garment** — even composite garments (5 skirt meshes for emission/transparency layers) share one cage
- **User-editable** — cage is simple enough to tweak by hand
- **Clean separation** — cage handles physics, original meshes handle rendering

### Objects

| Object | Modifiers | Notes |
|--------|-----------|-------|
| **Armature** | — | Skeleton + VMD animation |
| **Original meshes** | Armature + MeshDeform | Unchanged rendering, deformed by cage |
| **Cloth cage** | Armature + Cloth | Low-poly, wireframe display, `use_dynamic_mesh=True` |
| **Collision body** | Armature + Collision | User-selected body meshes (thighs, torso, etc.) |

### Binding: Mesh Deform (not Surface Deform)

Surface Deform requires the target to enclose/surround the source vertices and projects onto faces — fails when the cage is shrinkwrapped ON the surface rather than around it. Mesh Deform works with a cage that approximates the mesh volume and handles vertices outside the cage bbox gracefully.

---

## Proof of concept findings

Tested on YYB Miku skirt (5 meshes, 18K verts) with twirl animation.

### What worked

- Cylinder generation (12 rings x 24 segments = 288 verts)
- Shrinkwrap to largest skirt mesh — cage conforms to skirt shape
- Mesh Deform binding — all 5 skirt meshes bound successfully
- Auto-weights from armature — cage follows body movement
- Pin group on top 2 rings (full pin + half pin gradient)

### What needs work

- **Cloth stability** — twirl motion flings cage outward. Needs:
  - Higher stiffness values (tension/compression/shear)
  - More pin rows (3-4 instead of 2) with steeper gradient
  - Possibly higher damping to prevent oscillation
  - Mass tuning for small MMD scale
- **Cage fit** — shrinkwrap targets only the largest mesh. Could use a merged temporary target from all selected meshes for better fit
- **Collision tuning** — small model scale (1.5 Blender units tall) needs tiny collision distances (~0.001)

---

## Cloth settings (starting point)

```python
cloth.quality = 12                  # substeps
cloth.mass = 0.3
cloth.air_damping = 2.0
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

### Collision parameters

```python
# On collision body object
collision.thickness_outer = 0.001
collision.thickness_inner = 0.0005
collision.cloth_friction = 5.0

# On cloth cage
collision_settings.distance_min = 0.001
collision_settings.collision_quality = 5
collision_settings.use_self_collision = False  # enable later if needed
```

---

## Planned operator

`blender_mmd.build_cloth` in MMD4B panel:

1. User selects garment meshes in outliner/viewport
2. Click "Build Cloth Cage" — generates cylinder, shrinkwraps, auto-weights, adds Cloth + MeshDeform
3. User picks collision body meshes
4. Expose key cloth params for tuning (stiffness, damping, pin rows)
5. "Remove Cloth" to tear down cage and restore original modifiers

### TODO

- **MeshDeform not transferring cage motion** — skirt meshes stay frozen at rest pose when Armature modifier is removed (only MeshDeform from cage remains). With both Armature + MeshDeform, the Armature dominates and MeshDeform has no visible effect. Need to investigate: possibly MeshDeform binds to the cage's rest shape and doesn't pick up Armature+Cloth evaluated mesh. May need to use a different binding approach, or bind after applying Armature as rest shape on the cage. Alternatively, try Surface Deform with a cage that fully encloses the mesh (add caps + slight inflation beyond shrinkwrap).

### Key decisions still needed

- **Pin bone detection** — auto-detect which bone to pin to (nearest ancestor of garment vertices), or let user pick
- **Cylinder dimensions** — auto-size from bounding box, or let user specify rings/segments
- **Multi-garment** — cloth-to-cloth collision between e.g. top overlay and skirt
- **Bake workflow** — bake cloth sim for final renders (Blender native bake)
