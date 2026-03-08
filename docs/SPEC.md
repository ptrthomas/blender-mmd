# Blender MMD Specification

A ground-up rewrite of [blender_mmd_tools](../../../blender_mmd_tools) targeting **Blender 5.0+**, designed to be driven by **Claude Code** via [blender-agent](../../../blender-agent).

## Goals

1. Import PMX models into Blender with correct armature, mesh, and vertex weights
2. Rewrite physics integration using modern Blender APIs (collision_collections, drivers)
3. Eliminate the traditional addon UI — Claude Code is the interface
4. Fix the core physics problems in mmd_tools: O(n²) collision constraints, hard vs soft constraint mismatch, IK solver incompatibility

## Non-goals

- PMX/PMD/VMD export (one-way import only, no round-trip)
- Traditional Blender UI panels or sidebar (except MMD4B physics panel)
- Backwards compatibility with mmd_tools object hierarchy or metadata
- Material library system
- Rigify integration

---

## Architecture

### Addon identity

| Field | Value |
|-------|-------|
| Project name | `blender-mmd` |
| Blender addon ID | `blender_mmd` |
| Display name | `Blender MMD` |
| Target Blender | 5.0+ |
| Python | 3.11+ |
| License | GPL-3.0-or-later |

### Dependency on blender-agent

blender-mmd expects blender-agent to be installed and running. This is documented, not enforced via `blender_manifest.toml`. Claude Code communicates with Blender through blender-agent's HTTP bridge and calls blender_mmd's operators and helper functions.

The addon should be designed so Claude can debug and compare output against the original mmd_tools addon when both are installed simultaneously.

### Project layout

```
blender-mmd/
├── blender_mmd/              # Blender addon (symlinked to extensions dir)
│   ├── __init__.py           # Addon entry, auto-registration
│   ├── blender_manifest.toml # Blender 5.0+ extension manifest
│   ├── pmx/                  # PMX parser and data model
│   │   ├── __init__.py
│   │   ├── parser.py         # Binary PMX reader (clean rewrite)
│   │   └── types.py          # Dataclasses for PMX/PMD structures
│   ├── pmd/                  # PMD parser (outputs same types as PMX)
│   │   ├── __init__.py
│   │   └── parser.py         # Binary PMD reader → pmx.types.Model
│   ├── vmd/                  # VMD parser and importer
│   │   ├── __init__.py
│   │   ├── parser.py         # Binary VMD reader
│   │   ├── types.py          # Dataclasses for VMD structures
│   │   └── importer.py       # VMD → Blender F-curves, IK toggle
│   ├── importer.py           # PMX → Blender object creation
│   ├── physics.py            # Rigid body and joint setup (metadata, build, clear)
│   ├── chains.py             # Chain detection from RB/joint topology (pure Python)
│   ├── armature.py           # Bone creation, IK setup, additional transforms, shadow bones
│   ├── mesh.py               # Mesh creation and vertex weights
│   ├── materials.py          # Material/shader creation, texture loading
│   ├── outlines.py           # Edge/outline rendering (Solidify + Emission material)
│   ├── operators.py          # Thin Blender operator layer
│   ├── panels.py             # MMD4B N-panel for physics controls
│   ├── helpers.py            # Introspection and query helpers for Claude
│   └── translations.py       # Japanese ↔ English bone name dictionary
├── docs/
│   └── SPEC.md               # This file
├── tests/
│   └── ...
└── scripts/
    ├── setup.sh              # Symlink setup script
    └── scan_translations.py  # Build translation table from PMX/VMD files
```

### Internal imports

Use relative imports throughout: `from .pmx import parser`, `from . import helpers`.

### Development workflow

Symlink `blender_mmd/` into Blender's extensions directory:

```bash
mkdir -p ~/Library/Application\ Support/Blender/5.0/extensions/user_default
ln -sf $(pwd)/blender_mmd ~/Library/Application\ Support/Blender/5.0/extensions/user_default/blender_mmd
```

### Iteration loop

Claude Code drives development through blender-agent (separate repo, pure HTTP transport layer). The workflow:

1. Edit blender_mmd source files
2. Restart Blender to reload the addon (`python3 ../blender-agent/start_server.py`)
3. Execute test code via blender-agent (`POST http://localhost:5656`)
4. Read results from blender-agent's log (`output/agent.log`)
5. Take screenshots for visual validation (`bpy.ops.screen.screenshot(filepath=f"{OUTPUT}/screenshot.png")`)
6. Repeat

blender-agent provides: code execution, output directory, logging, screenshots, and Blender lifecycle control. blender-mmd has no Python import dependency on blender-agent — they are independent Blender extensions that communicate only through Claude Code.

### Extension manifest

`blender_mmd/blender_manifest.toml`:

```toml
schema_version = "1.0.0"

id = "blender_mmd"
version = "0.1.0"
name = "Blender MMD"
tagline = "Import MMD (PMX/PMD) models into Blender"
maintainer = "blender-mmd contributors"
type = "add-on"
blender_version_min = "5.0.0"
license = ["SPDX:GPL-3.0-or-later"]
```

---

## PMX Parser

### Approach

**Clean rewrite** of the PMX parser, using mmd_tools' `pmx/__init__.py` (~1600 LOC) as reference but not as a fork. Goals:

- Standalone, well-documented parser useful as a reference implementation (e.g. for a future three.js port)
- Python 3.11+ type hints throughout
- Output as dataclasses (defined in `types.py`)
- Full PMX 2.0/2.1 spec support
- PMD support via separate parser that outputs the same types
- No `compat/` version checks, no Blender imports in the parser itself

### Correctness testing

The rewrite is validated against the battle-tested mmd_tools parser:

- **Batch parse test**: Parse all PMX files in a configurable test directory, assert no exceptions
- **Comparison test**: For each test file, parse with both blender_mmd and mmd_tools parsers, compare output field-by-field (vertex positions, bone data, materials, etc.)
- Test PMX file directory is configured at test time (not hardcoded)

### PMX binary format

The PMX file is a sequential binary format. Key details for the parser:

**Header**: Magic bytes `PMX ` (with space), version float (2.0 or 2.1), globals byte array defining:
- Text encoding (0 = UTF-16-LE, 1 = UTF-8)
- Additional UV count (0–4)
- Index sizes for vertices, textures, materials, bones, morphs, rigid bodies (1, 2, or 4 bytes each)

**Sections** (read in order, each prefixed by a 32-bit count):
1. Vertices — position, normal, UV, optional additional UVs, bone weight (BDEF1/2/4, SDEF), edge scale
2. Faces — flat array of vertex indices (every 3 = one triangle)
3. Textures — string table of texture file paths
4. Materials — diffuse/specular/ambient colors, texture indices, face count (how many indices this material covers)
5. Bones — name, position, parent index, flags, display connection, IK data
6. Morphs — vertex/bone/UV/material/group morphs with type-specific data
7. Display frames — UI grouping (parsed but unused in milestone 1)
8. Rigid bodies — shape, mass, damping, collision group/mask
9. Joints — connected bodies, translation/rotation limits, spring parameters

**Variable-size indices**: Vertex, bone, texture, material, morph, and rigid body indices use 1, 2, or 4 bytes as declared in the header globals. The parser must read the correct size per index type.

**Text strings**: Each string is prefixed by a 32-bit byte length, then the encoded bytes (UTF-16-LE or UTF-8 per header).

### Coordinate conversion

**At parse time.** The parser outputs all positions, normals, and rotations in Blender's coordinate system (Z-up, right-handed). The conversion from MMD's Y-up left-handed system happens inside the parser:

- Positions: `(x, z, y)` — swap Y↔Z. MMD Y-up → Blender Z-up. This is a reflection (det=-1), which changes handedness.
- Rotations: `(x, z, y)` — same axis remapping as positions
- Normals: same conversion as positions
- Face winding: reversed `(f3, f2, f1)` in parser to correct for handedness change

**Why Y↔Z swap, not `(x, -z, y)`**: The negated-Z formula `(x, -z, y)` has det=+1 (a rotation, preserves handedness), which points the model 180° the wrong direction. The simple swap `(x, z, y)` has det=-1 (a reflection), correctly changing from left-handed to right-handed. This matches mmd_tools' `.xzy` swizzle.

Downstream code never deals with MMD coordinates.

### Parse scope

Parse the **entire PMX file** into Python dataclasses on every import. The full data model is available even if only a subset is used in the current milestone. This avoids re-visiting the parser when adding features.

### Data structures

Dataclasses for: `Header`, `Vertex`, `BoneWeight` (BDEF1/2/4, SDEF), `Material`, `Bone`, `Morph`, `DisplayFrame`, `RigidBody`, `Joint`, `Texture`. All coordinate values are in Blender space after parsing.

---

## PMD Parser

### Approach

The PMD parser (`pmd/parser.py`) reads PMD 1.0 binary files and outputs the **same `pmx.types.Model`** dataclasses as the PMX parser. This means the entire downstream pipeline (armature, mesh, materials, physics, VMD) works unchanged.

### Key conversions from PMD → PMX types

- **Vertices**: Weight byte (0-100) → float (0.0-1.0). BDEF1 if both bones equal, BDEF2 otherwise. Edge flag inverted (PMD 0=on → edge_scale=1.0).
- **Bones**: 10 PMD type codes (0-9) → PMX flag bits. IK data merged from separate IK section into bone fields. `control_weight * 4` factor. Knee bones get automatic -180°→-0.5° X-axis limits.
- **Morphs**: Base morph (type 0) provides vertex index map. All other morphs remapped to absolute vertex indices.
- **Rigid bodies**: Position is offset from parent bone in PMD → converted to absolute by adding bone position.
- **Materials**: Texture path split on `*` (diffuse*sphere). Sphere mode from extension (.spa=add, .sph=multiply). Toon uses shared index (0-9).
- **Strings**: All CP932 encoded, fixed-size (20-256 bytes), null-terminated.
- **English extension**: Optional section providing English names for bones/morphs.

### Format auto-detection

`importer.import_pmx()` auto-detects format by file extension (`.pmd` → PMD parser, `.pmx` → PMX parser). The import operator accepts both `*.pmx` and `*.pmd` files.

### VMD bone name auto-mapping

VMD bone lookup includes fallback matching for cross-era compatibility:

1. **Exact match** on `mmd_name_j` custom property (existing behavior)
2. **NFKC normalization**: `unicodedata.normalize("NFKC", name)` — catches half-width↔full-width katakana (e.g., `ｽｶｰﾄ` → `スカート`)
3. **Alias table**: Known semantic differences between PMD/PMX eras (e.g., `人指` ↔ `人差指` for index finger)

---

## Object Hierarchy

### Structure

Default (split by material):
```
Scene Collection
  └── "Model Name" (collection)
        ├── Armature (top-level, named after model)
        ├── _mmd_morphs (control mesh — hidden single-triangle, owns all shape keys)
        ├── body (mesh, Armature modifier)
        ├── hair (mesh, Armature modifier)
        ├── eyes (mesh, Armature modifier)
        └── ...
```

Single mesh fallback (`split_by_material=False`):
```
Armature (top-level, named after model)
  └── Mesh (child, with Armature modifier)
```

No root empty object. The armature is the top-level object. Model-level metadata (original PMX name, import scale) stored as custom properties on the armature. Split meshes are named after their first material. The `_mmd_morphs` control mesh is a single degenerate triangle with no material — invisible in viewports but not `hide_viewport` (which would block animation evaluation).

This differs from mmd_tools' hierarchy (`Root Empty → Armature → Mesh`) intentionally, so both addons can coexist without object naming conflicts.

### Per-material mesh build

Each material gets its own mesh object built directly from PMX data — no `bpy.ops.mesh.separate()`. This eliminates the need for normals backup/restore (custom normals are set per-mesh during construction) and avoids `mesh.separate()`'s performance cost (~2.8s on complex models). Benefits:
- Per-object modifiers (cloth sim on skirt, solidify for outlines)
- Light linking (per-object in Blender 5.0)
- Per-object `visible_shadow` (honors PMX `mmd_drop_shadow` flag)
- Selective outline rendering

Each mesh only gets shape keys for morphs that affect its vertices (sparse — most meshes have zero or few morphs). A hidden **control mesh** (`_mmd_morphs`) owns all shape keys as value holders. VMD morph animation targets only the control mesh — a single action, a single NLA track. A `frame_change_post` handler copies control mesh shape key values to visible meshes each frame.

This architecture produces a clean NLA editor with exactly 2 entries: armature (bone action) + `_mmd_morphs` (morph action). No `animation_data` exists on any visible mesh, shape key, or material nodetree.

### Mesh construction

**Single mesh object** for milestone 1. Vertex groups for bone weights. No materials — the mesh renders as default gray.

**Face construction**: PMX stores a flat array of vertex indices (every 3 indices = one triangle). Materials reference faces by count: material 0 owns the first N/3 triangles, material 1 owns the next M/3, etc. In milestone 1, faces are created from the full index array but material slot assignment is deferred (single gray material). Per-face material indices will be assigned when materials are implemented in milestone 3.

**Smooth shading**: All faces are set to smooth shading (`use_smooth = True`) via `foreach_set`. Sharp edges are marked at 179° angle threshold before applying custom normals — this is required for `normals_split_custom_set()` to work correctly (undocumented Blender requirement, confirmed by mmd_tools).

**Normals**: PMX provides per-vertex normals (already converted to Blender coords by the parser). Sharp edges are marked first (179° threshold in edit mode), then custom split normals are applied via `mesh.normals_split_custom_set()`.

**UV coordinates**: PMX provides per-vertex UVs. Create a UV map layer and assign coordinates. Additional UV sets (0–4 per vertex, declared in header) are imported as additional UV map layers named `UV1`, `UV2`, etc.

Split-by-material is a future post-import operation (needed for anime-style outlines on selected parts). Shape keys (morphs) are also deferred — single mesh keeps them simple when implemented.

### Bone naming

Use **English names** as Blender bone names so they're readable in the viewport and outliner. Store the original Japanese name (`name_j`) as a custom property (`mmd_name_j`) on each bone for VMD import matching.

**Name resolution order** (for choosing the Blender bone name):
1. PMX `name_e` (English name from the model) — if non-empty
2. Translation table lookup of `name_j` — if a known translation exists
3. `name_j` as-is — fallback, keeps the bone usable even without translation

**VMD bone matching**: VMD files reference bones by Japanese name. On VMD import, build a reverse lookup from `mmd_name_j` custom properties to find the corresponding Blender bone. This is an O(n) scan done once at import time.

### Translation table

Module: `blender_mmd.translations`

A Japanese → English dictionary of common MMD bone names, shipped with the addon. Covers standard bones (体, 頭, 左腕, etc.) and common variations across model authors.

**Building the table**: `scripts/scan_translations.py` scans a directory of PMX and VMD files, extracts all `(name_j, name_e)` pairs from PMX bones and all bone names referenced in VMD files, and outputs a merged translation dictionary. This is run offline to grow the table as more models are encountered.

The table is a plain Python dict in `translations.py` — no external data files, no runtime I/O. Claude can also add translations on the fly by updating the module.

### Bone visibility

Import all bones visible. Organize into bone collections that allow Claude or the user to show/hide groups later. No automatic hiding in milestone 1.

### Metadata policy

Minimal metadata on Blender objects. Store only what's needed for physics and animation:

- Bone: `bone_id`, `mmd_name_j` (Japanese name, for VMD matching), IK-related flags
- Armature: `pmx_name`, `import_scale`
- Rigid body/joint: physics parameters needed for tuning

No MMD-specific PropertyGroups. Use Blender custom properties where needed.

---

## Armature & Bones

### Bone creation

1. Enter edit mode on armature
2. Create edit bones from PMX bone data (positions already in Blender coords)
3. Set parent-child relationships from `bone.parent` index
4. Set bone tails from `displayConnection` (see below)
5. Handle tip bones (zero-length bones get minimum length to prevent Blender deletion)
6. Exit edit mode

### Bone tail / display connection

PMX bones have a `displayConnection` field whose meaning depends on a flag bit:

- **Flag bit set → bone index**: The bone's tail points at the position of the referenced bone. If the referenced bone index is -1 or invalid, use the default tail offset.
- **Flag bit unset → position offset**: The bone's tail is at `head + offset`. If the offset is zero (tail == head), use the default tail offset.

**Default tail offset for zero-length bones: `(0, 0, 1) * scale`** (along +Z, length = import scale). This matches mmd_tools' behavior. The direction matters because it determines the bone's local coordinate frame (y_axis = head→tail direction), which propagates through additional transform shadow bones. Using +Y instead of +Z would cause 90° rotation errors on shadow bones.

### Bone roll / local axes

**Critical for both VMD motion import and additional transforms.** Bone roll determines the bone's local coordinate frame (`matrix_local`). This affects:
1. **VMD conversion**: The per-bone `_BoneConverter` uses `bone.matrix_local` to transform keyframes
2. **Additional transforms**: Shadow bones copy the source bone's roll — if the source bone has wrong roll (from wrong tail direction), the entire TRANSFORM constraint chain produces wrong output
3. **IK limits**: `_convert_ik_limits()` transforms limits through `bone.matrix_local`

Bone roll depends on the **tail direction** — Blender derives `y_axis` from head→tail, then computes `x_axis`/`z_axis` from roll angle around that direction. So getting the tail right (see above) is a prerequisite for correct roll.

MMD bones have specific local axis orientations defined in two ways:

1. **Explicit local axes** (`localCoordinate` in PMX, flag bit 0x0800): The PMX bone stores X-axis and Z-axis vectors. Only ~14 bones in a typical model use this (thumbs, fingertips). These vectors are in MMD coordinates and get Y↔Z swapped by the parser.

2. **Auto-computed axes** for arm/finger bones: Shoulder, arm, elbow, wrist, and finger bones get their local axes computed geometrically from head/tail positions in the XZ plane. This covers ~50+ bones. Bones covered: `左肩/右肩`, `左腕/右腕`, `左ひじ/右ひじ`, `左手首/右手首`, plus semi-standard (`腕捩`, `手捩`, `肩P`, `ダミー`), plus all finger bones containing `親指/人指/中指/薬指/小指`.

3. **All other bones**: No explicit roll computation. Roll defaults to Blender's automatic calculation from tail direction. This is why the tail direction for zero-length bones is critical — it determines the default roll for D bones, cancel bones, toe bones, etc.

**Why this matters for retargeting**: Standard Blender rig animation (Mixamo, Rigify, motion capture) fails on MMD armatures because the bone rolls don't match. A "rotate arm 45° around X" keyframe means different physical rotations when the bone's local X-axis points in different directions. This is why direct animation mapping between standard rigs and MMD models produces broken poses.

**Implementation** (in `armature.py`):
- `_set_bone_roll_from_axes()`: Sets roll from PMX local axis data using `EditBone.align_roll()`
- `_set_auto_bone_roll()`: Geometrically computes axes for arm/finger bones, matching mmd_tools' `FnBone.update_auto_bone_roll()`
- Applied after setting bone tails, before leaving edit mode
- Bones shorter than `MIN_BONE_LENGTH` are skipped (no meaningful direction to derive roll from)

### IK setup

Uses **Blender's native IK solver** with correct constraint placement:

- IK constraint placed on the **first link bone** (e.g. knee), NOT the end effector (ankle). Blender's IK solver positions the constrained bone's TAIL at the target, so placing on knee makes the ankle (knee's tail) reach the IK bone position.
- Edge case: if first IK link == IK target, remove that link and use next link (matches mmd_tools)
- Set `chain_count` from PMX IK link count (adjusted after any link removal)
- Set `iterations` from PMX `loopCount * ik_loop_factor` (default factor=1, configurable)
- Per-link rotation limits use **Blender-native IK properties** (`use_ik_limit_x`, `ik_min_x`, etc.) — more performant and idiomatic. When Blender clamps a value (e.g. `ik_min_x` clamped to [-π,0], losing a positive minimum like 0.0087 rad), a `LIMIT_ROTATION` constraint (`mmd_ik_limit_override`) is added as a fallback override on only the affected axes.
- IK limits converted from Blender-global to bone-local space via `_convert_ik_limits()`: negate bone matrix, Y↔Z row swap, transpose, snap to axis-aligned permutation (matches mmd_tools' `convertIKLimitAngles`)

**IK iteration multiplier**: Blender's IK solver converges slower than MMD's CCDIK. The `ik_loop_factor` parameter (stored as custom property on armature) multiplies PMX iteration counts. Default 5 (matching common mmd_tools usage) gives 200 iterations for typical leg IK (PMX loopCount=40), which provides good foot placement precision.

**VMD IK toggle**: VMD files contain per-frame IK enable/disable states. These are imported as IK constraint `influence` keyframes (0.0/1.0 with CONSTANT interpolation), which is more Blender-native than mmd_tools' custom property + update callback approach.

CCD IK solver is a future enhancement for VMD motion fidelity.

### Additional transform (grant parent / 付与親)

Bones can inherit rotation and/or location from another bone, scaled by a factor. This powers D bones (leg deformation doubles), shoulder cancel, arm twist, and eye tracking. Without it, VMD animation shows visibly wrong legs/arms because keyframes assume the constraint system is in place.

**Approach**: TRANSFORM constraints + shadow bones (matching mmd_tools):

- **TRANSFORM constraint** (not COPY_ROTATION) — avoids ±180° euler discontinuities
- Constraint settings: `use_motion_extrapolate=True`, `target_space=LOCAL`, `owner_space=LOCAL`, `map_to_x/y/z_from=X/Y/Z`, `to_euler_order=XYZ`, `mix_mode_rot=AFTER`
- Rotation: `from_min/max = ±π`, `to_min/max = ±π×factor`
- Location: `from_min/max = ±100`, `to_min/max = ±100×factor`
- **Negative factor** (cancel bones like 肩C, 腰キャンセル, 目戻): `from_rotation_mode=ZYX` produces correct inverse rotation via ZYX→XYZ euler inversion
- **Positive factor**: `from_rotation_mode=XYZ`

**Shadow bones** for non-aligned bone pairs:
- `_dummy_<name>`: parent=target, head=target.head, tail offset=bone.tail-bone.head, roll=bone.roll
- `_shadow_<name>`: parent=target.parent, same head/tail/roll as dummy
- COPY_TRANSFORMS on shadow targeting dummy (POSE space)
- Both hidden in `mmd_shadow` bone collection, `use_deform=False`
- **Well-aligned optimization**: if `bone.x_axis·target.x_axis > 0.99` AND `bone.y_axis·target.y_axis > 0.99`, skip shadow bones — constraint points directly at target

**Implementation** (in `armature.py`):
- `_setup_additional_transforms()`: Creates TRANSFORM constraints in POSE mode, returns `_ShadowBoneSpec` list
- `_create_shadow_edit_bones()`: Creates dummy/shadow pairs in EDIT mode (second pass)
- `_finalize_shadow_constraints()`: Wires up COPY_TRANSFORMS and subtargets in POSE mode
- Chain dependencies (A→B→C) handled automatically by Blender's depsgraph
- Validation: skip self-reference, out-of-range targets, zero factor

### SDEF

Parse SDEF data from PMX and store it in the data model. Do not implement deformation logic in milestone 1. Future implementation via Geometry Nodes (using the Geometry Attribute Constraint available in Blender 5.0+).

### Scale

Default import scale: **0.08** (matching mmd_tools). Configurable at import time via operator parameter.

---

## Physics System

### Overview

The physics system uses Blender's rigid body physics (Bullet engine) with the same core approach as mmd_tools: `GENERIC_SPRING` joints with spring values applied, `disable_collisions` constraints for non-colliding pairs, and bone coupling via COPY_TRANSFORMS/COPY_ROTATION. Both mmd_tools and blender-mmd apply PMX spring stiffness values to Blender constraints (mmd_tools via property update callbacks, blender-mmd directly during joint creation).

The main limitation is Blender's hard constraint model vs MMD's soft constraints: when a DOF is locked (`limit_min == limit_max`), MMD allows elastic movement while Blender freezes it. This makes hair/clothing stiffer than MMD. The cloth-on-cage approach (Milestone 4b) is the quality path for natural movement.

### What we can and cannot fix

| Problem | Fix | Status |
|---------|-----|--------|
| Stiff hair/clothing (hard constraints) | Cloth-on-cage simulation (M4b) | Done |
| One-frame physics lag behind bone motion | Inherent to Blender's dependency graph — cannot fix | N/A |
| Interactive posing with live physics | Not possible — physics only advances during timeline playback | N/A |

**Workflow expectation**: Import model, apply VMD motion, play timeline forward (or bake). Physics settles over the first few frames. For better hair/skirt physics, use the MMD4B cloth panel to convert bone chains to cloth simulation.

### Collision groups

Use Blender's `collision_collections` property (20-element boolean array on `RigidBodyObject`). This is a stable, documented API.

MMD uses 16 collision groups with a bilateral "non-collision mask" (bit set = don't collide). Blender uses "collision collections" where objects sharing ANY layer collide (symmetric). This asymmetry means PMX's bilateral mask system cannot be directly mapped — adding mask-based layers causes false cross-group collisions.

**Current approach: shared layer 0 + non-collision constraints.** Each body is placed on shared layer 0 only, so everything potentially collides. Collision exclusion is handled entirely by NCC constraint empties.

```python
blender_collections = [False] * 20
blender_collections[0] = True  # shared layer — all bodies can potentially collide
```

Previously bodies were also placed on their own group layer (`blender_collections[group_number] = True`), but this caused same-group bodies (e.g. all hair bodies in group 3) to collide with each other via the shared group layer — incorrect behavior since MMD's non-collision masks typically exclude same-group pairs. Removing the own group layer matches mmd_tools' approach.

Non-colliding pairs are suppressed via `GENERIC` constraints with `disable_collisions=True` (same pattern as mmd_tools). **All joints** get `disable_collisions=True` — connected bodies should never collide (the joint manages their relationship). For non-joint excluded pairs, NCC empties are created using a template-and-duplicate O(log N) doubling strategy.

**Proximity-based filtering.** NCC empties are only created for body pairs within `ncc_proximity * avg_bounding_size` distance (matching mmd_tools' `non_collision_distance_scale`). Default proximity factor is 1.5. Set to 0 to disable filtering (all excluded pairs get NCCs). `_rigid_bounding_range()` computes bounding box diagonal per shape type. This significantly reduces NCC count while keeping nearby collision pairs correct.

**Why not mask-based layers?** Blender's `collision_collections` uses the SAME bitmask for both Bullet's `collisionFilterGroup` AND `collisionFilterMask` (symmetric). PMX's system is asymmetric — both masks must agree: `(A.group & B.mask) && (B.group & A.mask)`. Placing bodies on mask-derived layers causes false cross-group collisions because shared layers are bidirectional. For example, skirt bodies (group 14, mask excludes group 14) would all collide with each other since they share the same layers. NCC empties remain the only correct approach in Blender.

**Future improvement**: Geometry Nodes data sheets, per-frame Python physics, or custom collision filtering could reduce the NCC empty count while preserving correct bilateral mask checking.

### Rigid body creation

For each PMX rigid body:

1. Create mesh object with **actual collision geometry** via bmesh (sphere, box, or capsule). Empty mesh objects give zero-size collision shapes because Blender derives bounds from bounding box.
2. Add Blender rigid body (`bpy.ops.rigidbody.object_add`)
3. Set collision shape (SPHERE, BOX, CAPSULE)
4. Set physics properties (mass, friction, bounce, linear/angular damping)
5. Set `collision_collections` (shared layer 0 only — see collision groups section)
6. Set kinematic flag based on mode (STATIC = kinematic)
7. **Negate rotation**: Parser does Y↔Z swap `(x,z,y)` but physics rotation also needs negation `(-x,-y,-z)` for correct handedness. This matches mmd_tools' `.xzy * -1` pattern.

### Rigid body modes

| PMX Mode | Behavior | Blender Implementation |
|----------|----------|----------------------|
| STATIC (0) | Bone-driven, no physics | Kinematic rigid body, parented to bone |
| DYNAMIC (1) | Free physics simulation | Active rigid body, bone reads physics via COPY_TRANSFORMS |
| DYNAMIC_BONE (2) | Physics with bone tracking | Active rigid body, bone reads rotation via COPY_ROTATION |

**STATIC**: The rigid body follows the bone. It pushes other active bodies but is not affected by physics. Implemented as kinematic rigid body parented directly to the bone (BONE parent type). Bone parenting origin is at bone TAIL with rest matrix: `parent_matrix = armature.matrix_world @ bone.matrix_local @ Translation(0, bone.length, 0)`.

**DYNAMIC**: Physics drives the rigid body. A tracking empty is parented to the rigid body, and the bone has a COPY_TRANSFORMS constraint targeting the empty. Uses COPY_TRANSFORMS (location + rotation) — matching mmd_tools. DYNAMIC bodies need full transform from physics to prevent chain divergence at hair tips.

**DYNAMIC_BONE**: Physics drives bone rotation only. A tracking empty is parented to the rigid body, and the bone has a COPY_ROTATION constraint targeting the empty. Translation comes from the bone's parent. This is the typical mode for hair and clothing — the strand rotates with physics but stays attached to the head/body.

**Multiple rigid bodies on same bone**: The heaviest one (highest mass) wins.

### Joints

Create joint constraints using `GENERIC_SPRING` rigid body constraint type with Blender's default spring type (SPRING2). We use the default to match mmd_tools, which never sets `spring_type` explicitly.

For each PMX joint:

1. Create empty object with `rigid_body_constraint`
2. Set type to `GENERIC_SPRING`
3. Connect source and destination rigid bodies (`object1`, `object2`)
4. Enable all 6 DOF limits (`use_limit_lin_x/y/z`, `use_limit_ang_x/y/z`)
5. Set translation limits from PMX `limit_move_lower/upper` (scaled by import scale)
6. Set rotation limits: **swap min/max AND negate** — `limit_ang_x_lower = -joint.limit_rotate_upper[0]`, etc. This matches mmd_tools' pattern: `minimum_rotation = joint.maximum_rotation.xzy * -1`
7. Set `disable_collisions = True` on all joint constraints (connected bodies should not collide — the joint manages their relationship)
7. Enable all 6 spring axes (`use_spring_x/y/z`, `use_spring_ang_x/y/z`)
8. **Actually set spring stiffness and damping values** from PMX `spring_constant_move` and `spring_constant_rotate`

Step 8 is the critical fix — mmd_tools stores these values but never applies them to the Blender constraint.

### Soft constraint workaround

MMD's constraints are "soft" — even when a DOF is locked (`limit_min == limit_max`), bodies can move elastically past the limit with a spring restoring force. Blender's constraints are "hard" — locked DOFs are frozen.

**Disabled.** The Bullet trick (setting `lower > upper` to unlock DOFs) caused oscillation/explosion at typical MMD spring values. The function `_apply_soft_constraints()` exists in `physics.py` but is not called. Hair/clothing is slightly stiffer than MMD but stable.

### Physics world settings

| Parameter | Value | Notes |
|-----------|-------|-------|
| `substeps_per_frame` | 6 | Matches mmd_tools. Higher values tighten constraints but slow playback. |
| `solver_iterations` | 10 | Matches mmd_tools. |
| `gravity` | Default (-9.81) | Not scaled (matches mmd_tools) |
| `use_split_impulse` | False | Can reduce bounce artifacts but causes stacking instability |

### Physics chain discovery

Chain detection in `chains.py` (pure Python, no Blender imports):
- Build adjacency graph from joints (`src_rigid → dest_rigid`)
- Find STATIC rigid bodies connected to DYNAMIC neighbors (chain roots)
- BFS from each root through DYNAMIC/DYNAMIC_BONE bodies
- Classify chains by name pattern matching (hair/skirt/accessory/other)
- Track visited bodies to prevent duplicates across chains

### Known limitations

These are inherent to Blender's Bullet integration and cannot be fixed in addon code:

- **One-frame lag**: Blender evaluates armature → physics → feeds back next frame. Hair/clothing trails body motion by one frame.
- **No interactive physics**: Physics only advances during timeline playback. Cannot pose and see physics respond without playing.
- **No mesh deformation feedback**: Collision shapes don't update when the mesh deforms. Physics bodies use their rest-pose shapes.
- **Spring precision**: Blender spring damping is capped at 1.0. Some MMD models may need manual damping adjustment.

### Physics modes

The physics system supports two modes, controlled by a `mode` parameter on `build_physics`:

```python
def build_physics(armature_obj, model, scale: float, mode: str = "none",
                  ncc_mode: str = "proximity", ncc_proximity: float = 1.5) -> None:
    """mode: 'none' | 'rigid_body', ncc_mode: 'draft' | 'proximity' | 'all'"""
```

| Mode | What happens | When to use |
|------|-------------|-------------|
| `none` (default) | Store rigid body/joint data as custom properties on armature. No Blender physics objects created. Clean scene. | Default import |
| `rigid_body` | Create Blender rigid bodies, joints, bone coupling. Matches mmd_tools quality. | Standard physics for hair/skirt/accessories |

**NCC mode** (stored on armature as `mmd_ncc_mode`): 3-way enum controlling non-collision constraint behavior:

| NCC Mode | NCC empties | Collision layers | Use case |
|----------|-------------|-----------------|----------|
| `draft` | None | `[False]*20` (no collisions) | Fast preview — springs/joints work but bodies pass through |
| `proximity` (default) | Distance-filtered pairs | shared layer 0 + own group | Best balance of speed and correctness |
| `all` | Every excluded pair | shared layer 0 + own group | Maximum correctness, most objects |

**NCC proximity** (stored on armature as `mmd_ncc_proximity`): FloatProperty 0.1–5.0, default 1.5. Only used when `ncc_mode="proximity"`. Matches mmd_tools' `non_collision_distance_scale`. Higher value = wider radius = more NCCs created.

Additional physics operations (no re-parse of PMX needed):

- `reset_physics(armature_obj)` — Reposition existing dynamic rigid bodies, tracking empties, and joints to match current bone pose. Fast alternative to full rebuild.
- `remove_chain(armature_obj, chain_index)` — Remove a single physics chain (rigid bodies, joints, tracking empties, bone constraints, and NCC empties referencing chain's bodies). Chain data stored as `mmd_physics_chains` JSON on armature during build.
- `rebuild_ncc(armature_obj)` — Recompute NCC pair table from serialized data, reuse existing empties by reassigning pairs, create/delete only the difference. Respects proximity setting and disabled chains. Returns (old_count, new_count).
- `toggle_chain_collisions(armature_obj, chain_index, enable)` — Set chain's bodies to `collision_collections = [False]*20` (disable) or restore from PMX data (enable). Instant, no rebuild needed.
- `toggle_chain_physics(armature_obj, chain_index, enable)` — Set chain's bodies to `kinematic=True` (disable/freeze) or restore from PMX mode (enable). Instant, no rebuild.
- `clear_physics(armature_obj)` — Remove all physics objects and metadata. Preserves user settings (disabled chains, NCC mode/proximity). Only removes `mmd_dynamic`/`mmd_dynamic_bone` constraints (not import-time constraints like `mmd_at_dummy`). Disables RBW before constraint removal to prevent per-constraint physics re-solve (without this, removal takes ~36s on 13K objects; with it, <1s).
- `build_physics_iter(armature_obj, model, scale, ...)` — Generator version of `build_physics` for modal operator use. Yields `(progress, message)` tuples at phase boundaries. Used by the modal operator for responsive UI; `build_physics()` is a sync wrapper that exhausts the generator.

---

## Operators

### Philosophy

Thin operator layer. Register Blender operators for:

- PMX import (with file browser, undo support)
- Physics build/teardown
- Any operation that benefits from Blender's undo stack

Core logic lives in plain Python functions that operators call. Claude can invoke operators via `bpy.ops` or call functions directly, whichever is cleaner.

### Modal operators with progress

Long-running operators (physics build, SDEF bake) use Blender's modal operator pattern for responsive UI:

- Core functions are implemented as **generators** that `yield (progress, message)` tuples at natural chunking points
- **Sync wrappers** exhaust the generator for API/script calls (blender-agent path)
- **Modal operators** drive the generator via `TIMER` events (0.01s interval), yielding control to Blender between steps for UI redraw and ESC handling
- Progress is stored as custom properties on the armature (`mmd_build_progress`, `mmd_sdef_bake_progress`) and displayed in the MMD4B panel
- ESC cancels the operation, closes the generator (triggering `finally` cleanup), and removes partially-built objects
- `tag_redraw()` on cleanup ensures the progress bar disappears immediately after cancel/finish

### Import operator

```
blender_mmd.import_pmx
```

Parameters:
- `filepath`: Path to .pmx or .pmd file (auto-detected by extension)
- `scale`: Import scale factor (default: 0.08)
- `use_toon_sphere`: Include toon and sphere texture nodes in materials (default: off)
- `split_by_material`: Split mesh into per-material objects (default: on)

Behavior:
1. Parse PMX file (full parse)
2. Create armature with bones
3. Create mesh with vertex weights
4. Set up IK constraints
5. Build per-material meshes directly from PMX data, create control mesh with all shape keys
6. Log summary of what was imported/skipped

### Physics operators

```
blender_mmd.build_physics              # Build physics (mode, ncc_mode, ncc_proximity)
blender_mmd.reset_physics              # Reposition rigid bodies to current bone pose
blender_mmd.clear_physics              # Remove all physics objects and metadata
blender_mmd.rebuild_ncc                # Rebuild NCC empties (respects proximity setting)
blender_mmd.select_chain               # Select rigid bodies for a chain (chain_index)
blender_mmd.remove_chain               # Remove a single physics chain (chain_index)
blender_mmd.toggle_chain_collisions    # Toggle collision layers for a chain (chain_index)
blender_mmd.toggle_chain_physics       # Toggle kinematic mode for a chain (chain_index)
blender_mmd.inspect_physics            # Copy full RB diagnostic report to clipboard
blender_mmd.select_colliders           # Select all collision-eligible RBs for active RB
blender_mmd.select_contacts            # Select RBs in contact with active RB at current frame
blender_mmd.rest_pose                  # Reset bones to rest pose, morphs to zero (temporary)
blender_mmd.clear_animation            # Remove all animation (actions + NLA tracks)
blender_mmd.mark_actions_as_assets     # Mark all actions as Blender assets
blender_mmd.view_import_report         # Open MMD Import Report in Text Editor
blender_mmd.toggle_ik                  # Toggle IK for one chain (target_bone)
blender_mmd.toggle_all_ik              # Enable/disable all IK (enable: bool)
blender_mmd.build_outlines             # Build edge outlines (Solidify + Emission)
blender_mmd.remove_outlines            # Remove edge outlines
blender_mmd.toggle_mesh_outline        # Toggle outline on/off for selected mesh
blender_mmd.set_mesh_edge_color        # Set edge color for selected mesh (color: RGBA)
blender_mmd.select_mesh_rigid_bodies   # Select rigid bodies related to selected mesh
blender_mmd.delete_mesh                # Delete selected mesh child, select armature
```

### VMD binary format

The VMD file is sequential binary with a 30-byte header (`Vocaloid Motion Data 0002\0`, model name in CP932). Sections are read in order, each prefixed by a 32-bit count:

1. **Bone keyframes** (111 bytes each) — bone name (15 bytes CP932), frame number (u32), position (3×f32), rotation quaternion (4×f32), interpolation curves (64 bytes)
2. **Morph keyframes** (23 bytes each) — morph name (15 bytes CP932), frame number (u32), weight (f32)
3. **Camera keyframes** (61 bytes each) — parsed but not imported
4. **Light keyframes** (28 bytes each) — parsed but not imported
5. **Shadow keyframes** (9 bytes each) — parsed but not imported
6. **Property keyframes** (variable) — frame (u32), visible (u8), IK count (u32), then per IK: name (20 bytes CP932), enabled (u8). Controls IK on/off per frame.

Property section IK toggle is imported as IK constraint `influence` keyframes (0.0/1.0 with CONSTANT interpolation).

### VMD import operator

```
blender_mmd.import_vmd
```

Parameters:
- `filepath`: Path to .vmd file
- `create_new_action`: Create new actions replacing existing (default: off — appends to current actions)
- `include_static`: Create F-curves for bones/morphs at rest pose (default: off — keeps Graph Editor clean)
- Scale auto-detected from armature's `import_scale` custom property

Behavior:
1. Parse VMD file (bone keyframes, morph keyframes, property/IK toggle keyframes)
2. Find the target armature (active selection or auto-detect)
3. Build Japanese→English bone name lookup from `mmd_name_j` custom properties
4. If bone keyframes exist: get or create bone action (reuse existing unless `create_new_action`), skip static bones (all keyframes at rest pose) unless `include_static`, apply via per-bone coordinate converter (`_BoneConverter`). If no bone keyframes: skip entirely, preserving existing bone animation
5. If morph keyframes exist: get or create morph action on the `_mmd_morphs` control mesh, skip static morphs (all keyframes at weight=0) unless `include_static`, apply to shape key F-curves via `mmd_morph_map`
6. Apply VMD Bézier interpolation handles to F-curves
7. Apply IK toggle keyframes as constraint influence F-curves (CONSTANT interpolation), using whichever bone action is active
8. Set scene FPS to 30 (MMD standard) and extend frame range to fit animation
9. Auto-reset physics if rigid bodies exist (repositions dynamic bodies to match animation start pose)
10. Log summary of matched/unmatched bones and morphs

**Append mode** (default): Multiple VMDs can be layered onto the same armature — e.g., body dance motion first, then lip sync VMD on top. Morph-only VMDs preserve existing bone animation. `create_new_action=True` replaces all actions instead.

**Per-bone VMD conversion**: VMD keyframes are in bone-local space. The `_BoneConverter` class constructs a conversion matrix from `bone.matrix_local` (with Y↔Z row swap + transpose) and converts each keyframe via matrix conjugation: `q_mat @ q_vmd @ q_mat.conjugated()`. This depends on correct bone roll (see above).

**Quaternion sign compatibility**: Adjacent quaternion keyframes are checked for sign flips. Since `q` and `-q` represent the same rotation but Blender's NLERP interpolates them differently, we pick the sign closest to the previous keyframe. Without this, bones can take the "long path" (~360° spin instead of staying still). Matches mmd_tools' `__minRotationDiff`.

**Interpolation axis remapping**: VMD Bézier interpolation curves are per-axis (X, Y, Z location + rotation). The `_InterpolationHelper` class computes the correct axis permutation from the bone's conversion matrix, reading from the full 64-byte VMD interpolation block at proper row offsets. Matches mmd_tools' `_InterpolationHelper`. Previous implementation used a hardcoded Y↔Z swap which was incorrect for bones with unusual local axis orientations.

**F-curve handle fixing**: First and last keyframe handles are set explicitly to prevent extrapolation artifacts. Matches mmd_tools' `__fixFcurveHandles`.

**Morph fallback aliases**: When a VMD references a morph the model doesn't have (e.g. `ワ`), `MORPH_ALIASES` maps it to a similar morph that might exist (e.g. `あ` / A). ~18 aliases for common mouth shapes, eye expressions, and brow morphs. First match wins. Neither mmd_tools nor MMD itself does this — VMD morphs that don't match are silently dropped.

---

## Helper Functions (for Claude)

Module: `blender_mmd.helpers`

Provide introspection and state-change helpers that Claude calls via blender-agent:

### Introspection

- `get_selected_bones()` — Returns names/properties of currently selected pose bones
- `get_model_info()` — Returns model name, bone count, mesh stats
- `get_ik_chains()` — Returns IK chain information (target, chain bones, limits)
- `get_physics_objects()` — Returns rigid bodies and joints with their properties
- `get_physics_mode()` — Returns current physics mode ("none", "rigid_body", "cloth")
- `get_physics_chains()` — Returns detected physics chains from armature metadata

### State changes

- `set_bone_visibility(collection_name, visible)` — Show/hide bone collections
- `select_bones_by_name(names)` — Select specific bones programmatically

These helpers evolve over time. Start minimal, add as needed.

---

## Logging

blender-mmd relies on **blender-agent's existing logging infrastructure**. blender-agent logs every code execution request and response to `output/agent.log`. Since Claude Code drives all operations through blender-agent, import results, errors, and tracebacks are captured there automatically.

Within addon code, use Python's `logging` module with a `blender_mmd` logger for structured diagnostics:

- **INFO**: Import summary (bone count, mesh stats, skipped features)
- **WARNING**: Unimplemented features encountered during import
- **DEBUG**: Detailed per-bone, per-vertex information

When a PMX file uses features not yet implemented (morphs, display frames, etc.), log a warning and skip. No errors, no user-facing dialogs.

The `blender_mmd` logger uses the default Python handler (stderr). During development, launch Blender from a terminal to see output live. Claude can also read blender-agent's log (`output/agent.log`) for diagnostics. No separate log file or custom file handler needed — blender-agent already handles this.

### Error handling

Let parsing exceptions propagate. The import operator catches exceptions at the top level and logs the error. No defensive recovery logic in the parser — if a PMX file is corrupt or truncated, the import fails with a clear traceback.

---

## Status

### What's done

- **PMX/PMD import** — full parser (clean rewrite), armature with bones, per-material mesh build, vertex weights, normals, UVs, IK constraints. PMD auto-detected by extension, same downstream pipeline.
- **Morphs** — vertex morphs as shape keys, group morphs flattened into composite vertex keys. Hidden control mesh (`_mmd_morphs`) owns all shape keys — single morph action, clean 2-track NLA editor.
- **VMD motion** — bone/morph keyframes as F-curves, IK toggle via constraint influence, interpolation curves, append mode for layering, static channel filtering for clean Graph Editor, FPS control.
- **Rigid body physics** — 3-phase build, GENERIC_SPRING joints, bilateral collision mask enforcement via NCC empties, debug inspector (inspect/colliders/contacts), auto-reset after VMD import, per-chain management in MMD4B panel.
- **Materials** — two shader modes (bare Principled BSDF default, optional toon/sphere node group), specular mapping, bundled toon textures, global controls via armature properties (no drivers). Overlapping materials (e.g. eye highlights) auto-detected and use Diffuse BSDF + Transparent BSDF mix (BLENDED) to prevent z-fighting.
- **Additional transforms** — grant parent system (D bones, shoulder cancel, arm twist, eye tracking), shadow bones.
- **Edge outlines** — Solidify modifier + Emission BSDF, per-material edge color/size/alpha, per-vertex edge_scale, per-mesh controls.
- **SDEF** — bake-to-MDD pipeline, Mesh Cache playback, instant A/B toggle.
- **Name translation** — unified 4-tier engine: full-name table → chunk translation (CamelCase) → name_e fallback → Japanese. ~320 chunk entries, covers 95%+ of names.
- **PMD support** — separate parser outputting same types, cross-era VMD bone name mapping, WaistCancel fix, knee pre-bend.

**Test data (`tests/samples/`):**
- `初音ミク.pmx` — simple Miku model (122 bones, 45 rigid bodies, 27 joints)
- `lat.pmd` — Lat式ミク PMD model (134 bones, rigid bodies, joints)
- `galaxias.vmd` — Galaxias dance motion
- `baseline_mmd_tools.json` — mmd_tools bone transforms at key frames (pose + IK only, `ik_loop_factor=5`)
- `miku_galaxias.blend` — mmd_tools reference (**pose + IK only, no physics baked**)

### MMD4B Panel

**N-panel**: Tab "MMD4B" in 3D Viewport sidebar. Visible when active object is an MMD armature (or child mesh).

**Layout** (parent panel shows model name + report button, all sub-panels collapsed by default):

**Header:** Model name with text icon button (visible when "MMD Import Report" text exists). Clicking opens the report in a Text Editor area — shows untranslated names from PMX import and unmatched names from VMD import.

**Mesh sub-panel** (visible only when a mesh child is selected):
- **Info:** Mesh name, vertex count, material count
- **Outlines** (only if material has `mmd_edge_enabled`): Toggle button (eye icon) to enable/disable outline on this mesh. When active: edge color button (updates Emission node + viewport display instantly), per-mesh thickness multiplier slider (`mmd_edge_thickness_mult` registered FloatProperty with `update` callback for instant reactivity). Thickness formula: `edge_size × import_scale × 0.05 × global_mult × per_mesh_mult`.
- **Physics** (only if physics built and chains affect this mesh): Shows chain count and rigid body count with a select button that highlights the related rigid bodies. Lists each chain by name, group, and body count. Chain management stays in the Physics sub-panel.
- **Delete Mesh** button: Removes the selected mesh object and selects the armature.

**Animation sub-panel:**
- Shows current action name when animation is loaded
- "Rest Pose" button: temporarily resets all pose bones and morph values to zero — keyframes take over again on playback/scrub
- "Remove Animation" button: removes all actions and NLA tracks, resets to rest pose, returns to frame 1

**Physics sub-panel:**
- **No physics state:** NCC mode dropdown (Draft/Proximity/All) + proximity slider (greyed out unless Proximity selected) + "Build Rigid Bodies" button (calls `build_physics` with `mode=rigid_body`)
- **Physics active:** Shows rigid body count, NCC mode info (Draft/All/proximity value), and NCC count (e.g. "Active: 265 bodies (1.5, 1988 NCCs)"). "Reset" button (repositions existing rigid bodies), "Rebuild NCCs" button (re-applies current NCC settings), "Remove" button (deletes all physics objects)
- **Selected RB info:** When a rigid body is selected (has `mmd_rigid_index`), shows a box with name, mode/mass/group, chain membership, and three debug buttons:
  - **Inspect**: Copies full diagnostic report to clipboard (PMX data, joints, collision mask, position, warnings)
  - **Colliders**: Selects all RBs that share at least one collision-eligible group (bidirectional PMX mask check)
  - **Contacts**: Selects RBs in contact at current frame (shape-aware centroid distance check, not AABB)
- **Chain list:** Each chain row has four controls:
  - **Eye icon** (collision toggle): depressed = collisions active. Toggle off sets `collision_collections = [False]*20` (instant, no rebuild). Toggle on restores from PMX data.
  - **Physics icon** (physics toggle): depressed = physics active. Toggle off sets `kinematic=True` (bodies freeze). Toggle on restores from PMX mode.
  - **Chain name** (select): shows name, group, body count. Clicking selects chain's rigid bodies.
  - **X button** (remove): removes chain (rigid bodies, joints, tracking empties, NCC empties, bone constraints).
- Per-chain settings stored on armature: `mmd_chain_collision_disabled` (JSON list), `mmd_chain_physics_disabled` (JSON list). Preserved across clear/rebuild. Full rebuild brings back all deleted chains, respecting these settings.
- Chain data detected via `chains.py` during physics build and stored as `mmd_physics_chains` JSON on the armature

**IK Toggle sub-panel** (collapsed by default):
- **All On / All Off** buttons at top (eye icons)
- Per-chain toggle buttons showing current state (eye icon, `depress` for visual feedback)
- Toggles IK constraint `mute` (not `influence`) so user overrides persist during animation playback. VMD F-curves drive `influence` but `mute` takes precedence — a muted constraint is completely skipped regardless of F-curve values.
- Also mutes `mmd_ik_limit_override` LIMIT_ROTATION constraints in the chain
- Physics build/clear preserves user mute state (saved/restored around internal IK muting)
- Chains discovered by scanning pose bones for IK constraints

**Outlines sub-panel** (collapsed by default):
- **No outlines state:** Global thickness multiplier slider (default 1.0, range 0.1–5.0) + "Build Outlines" button
- **Outlines active:** Shows count of meshes with outlines, global thickness slider, "Rebuild" button (remove + build with new thickness), "Remove" button
- Uses Solidify modifier (`mmd_edge`) per mesh child with `use_flip_normals=True`, `offset=1` (outward), `material_offset=1` (edge material in slot 1)
- Edge material: Emission BSDF (unlit, lighting-independent) with PMX edge color. Alpha < 1.0 uses Mix Shader (Emission + Transparent) with BLENDED surface render method
- Per-vertex thickness modulation via `mmd_edge_scale` vertex group (populated during mesh import from PMX `edge_scale` data)
- Only builds on meshes where material has `mmd_edge_enabled=True` (eyes, face details, etc. skipped)
- **Per-mesh controls** in Mesh sub-panel: toggle outline on/off per mesh, edit edge color, per-mesh thickness multiplier (`mmd_edge_thickness_mult`, default 1.0, reactive via `update` callback)
- Thickness formula: `edge_size × import_scale × 0.05 × global_mult × per_mesh_mult`

**Workflow:** Import PMX → click "Build Rigid Bodies" in MMD4B panel → optionally import VMD (physics auto-resets) → play animation. Use "Reset" after changing pose to reposition rigid bodies without full rebuild. Use "Rest Pose" to temporarily return to rest pose while editing. Use "Remove Animation" to strip all keyframes and reload a different VMD. Use per-chain X buttons to remove physics from specific parts (e.g. remove skirt physics to replace with cloth sim). Use IK Toggle to disable IK chains for non-standard poses. Select a rigid body and use Inspect/Colliders/Contacts for debugging collision issues.

### Open items

- **Cloth simulation** — Blender-native cloth for garments/hair as alternative to rigid body physics. See `docs/CLOTH.md` for design notes.

---

## Blender 5.0+ API Notes

API changes from earlier Blender versions that affect our code:

- `bpy_struct.keys()` removed — use `hasattr()` instead
- BGL module removed — use `gpu` module for any GPU operations
- EEVEE identifier: `BLENDER_EEVEE` (not `BLENDER_EEVEE_NEXT`)
- Legacy action API removed (`action.fcurves`, `action.groups`)
- `scene.node_tree` replaced with `scene.compositing_node_group`
- `collision_collections` (not `collision_groups`) on rigid bodies
- New `Geometry Attribute Constraint` for bones reading geometry node attributes
- Shape Keys UI overhauled (multi-selection, drag-and-drop)
- **Stale `matrix_world`**: Newly created objects have identity `matrix_world` until depsgraph evaluates. Setting `obj.location`/`obj.rotation_euler` does NOT update it immediately. When you need the world matrix of a just-created object, build it manually from known position/rotation data instead of reading `obj.matrix_world`. This caused a subtle bug where all joint empties were placed at the origin.
- **Material shadow/blend API**: `shadow_method` removed — shadow casting is object-level only (`obj.visible_shadow`). Per-material shadow flags cannot be enforced; stored as custom properties. `blend_method` deprecated — use `surface_render_method` (`DITHERED` replaces `HASHED`/`OPAQUE`/`CLIP`, `BLENDED` replaces `BLEND`). `show_transparent_back` deprecated — use `use_transparency_overlap`.
- **Backface culling**: `use_backface_culling_shadow` added — should mirror `use_backface_culling` for single-sided materials (reduces Virtual Shadow Map cost).

---

## Design Principles

1. **One-way pipeline**: PMX → Blender. No export, no round-trip concerns.
2. **Claude is the UI**: No panels, no sidebar, no menus beyond import. All interaction through Claude Code + blender-agent.
3. **Blender-native first**: Use Blender's own systems (IK solver, rigid bodies, collections) rather than reimplementing. Customize only where MMD compatibility demands it.
4. **Progressive enhancement**: Each milestone builds on the previous. The addon is useful at every stage.
5. **Minimal metadata**: Don't pollute Blender objects with MMD-specific data. Store only what's needed for the current feature set.
6. **Fix as we go**: Handle Blender API issues as they surface during testing rather than auditing upfront.

---

## Community Contributions Welcome

Features not currently planned by the maintainer. Contributions welcome from anyone interested.

- **VMD camera motion** — import camera keyframes (position, rotation, FOV, distance) as Blender camera animation. VMD camera data is already parsed; needs Blender camera creation and F-curve setup.
- **CCD IK solver** — MMD uses CCD (Cyclic Coordinate Descent) IK which converges differently than Blender's built-in solver. A custom CCD solver (per-frame via handler) would match MMD motion more precisely. High complexity, moderate impact (current IK is close enough for most motions).
- **Bone morphs** — VMD can keyframe bone morphs (pose presets like "T-pose", "fist"). Currently parsed but not applied. Needs action-based implementation.
- **Material morphs** — VMD material keyframes (per-frame color/alpha/texture changes). Parsed but not applied.
- **Retargeting tools** — MMD↔Mixamo/Rigify/mocap retargeting. Correct bone rolls make this feasible.

#### UV Morphs

PMX UV morphs offset UV coordinates per-vertex. Need shape key UV layers or a Geometry Nodes approach since Blender shape keys only affect vertex positions, not UVs.

#### Decimate / Face Reduction

Many MMD models are over-tessellated (100k+ faces where 30k would suffice). Post-import decimate pass or operator that intelligently reduces face count while preserving UV seams, shape keys, and material boundaries.

#### Lookup Consolidation

Centralized reverse-lookup maps on the armature to eliminate redundant O(n) bone scans. ~4 subsystems each scan all bones for `bone_id` or `mmd_name_j`. Low priority — each scan is O(n) over 100-400 bones, runs once per operation, negligible cost.
