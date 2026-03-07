# Split Refactor: Per-Material Mesh Build + Control Mesh

## Problem

Current import builds one big mesh then splits with `bpy.ops.mesh.separate(type='MATERIAL')`.
This is slow (2.8s separate + 0.5s normals backup/restore = 3.3s) and creates shape key
management problems:

- Every split mesh gets copies of ALL shape keys, even if none of its vertices are affected
- No single source of truth for shape key values
- Editing a shape key only affects the selected mesh
- NLA morph sync requires a `frame_change_post` handler hack with a "primary mesh" concept
- Deleting the primary mesh breaks morph sync

## Solution

Two changes, done together:

### 1. Control mesh for shape keys

Create a hidden **control mesh** (`_mmd_morphs`) that owns all shape keys as value holders.
Split meshes' shape keys are driven by the control mesh via Blender drivers.

**Control mesh spec:**
- Single triangle (3 vertices) — minimal geometry, just enough to hold shape keys
- ALL shape keys from the model (vertex + flattened group morphs), values default to 0
- Shape keys are value-only (Basis + zero-delta keys) — no real vertex offsets needed
- `hide_viewport = True`, `hide_render = True`, `hide_select = False`
- Parented to armature, named `_mmd_morphs`
- `mmd_morph_map` JSON stored on this mesh (not armature — VMD import looks it up here)
- VMD morph import targets this mesh's shape keys
- NLA morph action lives on this mesh — no sync handler needed

**Driver setup on split meshes:**
- Each split mesh shape key `value` has a driver reading from the matching control mesh shape key
- Driver type: `AVERAGE`, single variable, `SINGLE_PROP` targeting control mesh's shape key value
- Data path: `data.shape_keys.key_blocks["MorphName"].value`
- Only create shape keys + drivers for morphs that affect vertices in that mesh

**Benefits:**
- Blender shape key UI works: select control mesh, adjust sliders, all meshes update
- Delete any visible mesh — control mesh and remaining meshes unaffected
- Add meshes — just add drivers pointing to control mesh
- NLA push-down on control mesh only, no frame_change handler
- VMD import writes to one place
- Fewer total shape keys across all meshes (each mesh only has relevant ones)

### 2. Per-material mesh build (eliminates split.separate)

Instead of building one mesh and calling `bpy.ops.mesh.separate`, build each per-material
mesh directly from the PMX data.

**Algorithm:**
```
for each material:
    1. Collect face indices belonging to this material (from sequential face_count ranges)
    2. Collect unique vertex indices referenced by those faces
    3. Build remapping: old_vertex_index -> new_vertex_index (0-based for this mesh)
    4. Create mesh with:
       - Vertices: subset, scaled, already in Blender coords
       - Faces: remapped to new vertex indices, winding already reversed by parser
       - UVs: subset via foreach_set (already working)
       - Normals: subset, apply via normals_split_custom_set
       - Vertex groups: subset (only bones referenced by this mesh's vertices)
       - Shape keys: subset (only morphs with offsets affecting this mesh's vertices)
       - SDEF attributes: subset
       - Edge scale vertex group: subset
    5. Assign material, parent to armature, add Armature modifier
    6. Set visible_shadow from mmd_drop_shadow
    7. Add shape key drivers pointing to control mesh
```

**What this eliminates:**
- `bpy.ops.mesh.separate` (2.8s)
- Normals backup as mmd_normal attribute + restore loop (0.5s)
- The entire `_split_mesh_by_material()` function
- Shape key duplication across all meshes

**What this adds:**
- Per-material vertex remapping (numpy, fast)
- Per-mesh normals_split_custom_set (smaller meshes = faster per call)
- Driver creation for shape keys

**Overlap fix (`_fix_overlapping_face_materials`):**
Run once on the full PMX face data before building meshes — just flag which material
indices need BLENDED. Then apply the flag during per-material mesh creation. No need to
iterate polygons after mesh creation.

## Impact on existing code

### Files changed:
- `mesh.py` — `create_mesh()` becomes `create_meshes()`, returns list of mesh objects.
  Shape key creation moves to per-mesh with subset logic. Control mesh creation added.
- `importer.py` — Remove `_split_mesh_by_material()`. Call `create_meshes()` instead of
  `create_mesh()`. Simplify flow (no normals backup/restore, no morph_map move to armature —
  it stays on the control mesh where it's created).
- `materials.py` — `create_materials()` called per-mesh or materials created separately
  and assigned. Overlap fix runs on PMX data, not Blender polygons.
- `vmd/importer.py` — Morph import targets control mesh (`_mmd_morphs`) instead of
  first mesh child. Reads `mmd_morph_map` from control mesh (not armature). Simpler —
  one mesh, one action, no slot sharing across split meshes.
- `operators.py` — NLA push-down simplified: morph action on control mesh only, no
  frame_change handler, no `mmd_morph_sync`.
- `panels.py` — Mesh panel: hide control mesh from mesh child listings. SDEF panel:
  skip control mesh.
- `sdef.py` — Skip control mesh when iterating mesh children.
- `outlines.py` — Skip control mesh (no `mmd_edge_enabled` material).

### What stays the same:
- PMX/PMD parser — no changes
- Armature creation — no changes
- Physics — no changes (operates on armature + rigid bodies)
- VMD bone import — no changes
- Translation system — no changes

## Migration

The `split_by_material=False` mode (single mesh) should still work — skip per-material
build, create one mesh with all data, still create control mesh for shape keys.

## Testing

1. Import YYB Miku — verify all 52 meshes created with correct geometry
2. Import VMD — verify morph animation plays on all meshes
3. Edit shape key on control mesh — verify all meshes update
4. Delete a visible mesh — verify no errors, other meshes still work
5. NLA push-down — verify morph strip on control mesh only
6. SDEF bake — verify control mesh skipped
7. Outlines — verify control mesh skipped
8. Performance — target < 5s total import (down from 7.8s)

## Estimated performance

| Phase | Current | Expected |
|-------|---------|----------|
| parse | 1.1s | 1.1s |
| create_armature | 0.06s | 0.06s |
| create_meshes (all) | 1.9s + 3.3s = 5.2s | ~2.5s |
| create_materials | 1.3s | ~1.0s |
| bone_collections | 0.05s | 0.05s |
| **TOTAL** | **7.8s** | **~4.7s** |

The 2.5s mesh estimate: per-material builds are individually fast (small meshes),
but we do 50-80 of them. The win comes from eliminating the 2.8s separate operator
and 0.5s normals restore, partially offset by per-mesh overhead.
