# Blender MMD Specification

A ground-up rewrite of [blender_mmd_tools](../../../blender_mmd_tools) targeting **Blender 5.0+**, designed to be driven by **Claude Code** via [blender-agent](../../../blender-agent).

## Goals

1. Import PMX models into Blender with correct armature, mesh, and vertex weights
2. Rewrite physics integration using modern Blender APIs (collision_collections, drivers)
3. Eliminate the traditional addon UI — Claude Code is the interface
4. Fix the core physics problems in mmd_tools: O(n²) collision constraints, hard vs soft constraint mismatch, IK solver incompatibility

## Non-goals

- PMX/PMD/VMD export (one-way import only, no round-trip)
- PMD format support (PMX only)
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
│   │   └── types.py          # Dataclasses for PMX structures
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
tagline = "Import MMD (PMX) models into Blender"
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
- No PMD support, no `compat/` version checks, no Blender imports in the parser itself

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

## Object Hierarchy

### Structure

```
Armature (top-level, named after model)
  └── Mesh (child, with Armature modifier)
```

No root empty object. The armature is the top-level object. Model-level metadata (original PMX name, import scale) stored as custom properties on the armature.

This differs from mmd_tools' hierarchy (`Root Empty → Armature → Mesh`) intentionally, so both addons can coexist without object naming conflicts.

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

**Default tail offset for zero-length bones: `(0, 0, 1) * scale`** (along +Z, length = import scale). This matches mmd_tools' behavior. The direction matters because it determines the bone's local coordinate frame (y_axis = head→tail direction), which propagates through additional transform shadow bones.

**Bug found and fixed**: Originally used `(0, MIN_BONE_LENGTH, 0)` — along +Y with length 0.001. This caused 90° rotation errors on all additional transform bones (D bones, cancel bones, toe) because the shadow bone system copies the source bone's roll and tail offset. With the wrong tail direction, the shadow bone's local frame was rotated 90° relative to the target bone's frame, making TRANSFORM constraints produce wrong results.

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

**Verification**: At rest pose, all bone axes (x/y/z) match mmd_tools with dot product = 1.0000 for all tested bones including D bones, cancel bones, and toe bones.

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

**Results** (YYB Miku, 461 bones): 64 rotation + 46 location = 110 TRANSFORM constraints, 53 shadow bone pairs, 6 negative-factor cancel bones, 57 well-aligned optimizations.

### Transform order

Blender's dependency graph handles evaluation order based on constraint dependencies. The additional transform constraints create implicit dependency chains that the depsgraph resolves automatically.

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

**Current approach: shared layer + own group + non-collision constraints.** Each body is placed on shared layer 0 (so everything potentially collides) plus its own collision group layer. Non-colliding pairs are then suppressed via `GENERIC` constraints with `disable_collisions=True`, same pattern as mmd_tools. For pairs that already have a joint, the existing joint's `disable_collisions` is set instead of creating a new object. A proximity filter (1.5x average range) avoids creating constraints for distant bodies that would never collide anyway.

```python
blender_collections = [False] * 20
blender_collections[0] = True  # shared layer — all bodies can potentially collide
blender_collections[pmx_rigid.collision_group_number] = True  # own group
```

**Future improvement**: A more accurate approach would analyze the collision graph to find groups that mutually want to collide and merge them onto shared layers, while keeping non-colliding groups separated. This could reduce or eliminate the need for non-collision constraint objects.

### Rigid body creation

For each PMX rigid body:

1. Create mesh object with **actual collision geometry** via bmesh (sphere, box, or capsule). Empty mesh objects give zero-size collision shapes because Blender derives bounds from bounding box.
2. Add Blender rigid body (`bpy.ops.rigidbody.object_add`)
3. Set collision shape (SPHERE, BOX, CAPSULE)
4. Set physics properties (mass, friction, bounce, linear/angular damping)
5. Set `collision_collections` (own group only — see collision groups section)
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
7. Set `disable_collisions = False` on joint constraints (connected bodies should still collide)
7. Enable all 6 spring axes (`use_spring_x/y/z`, `use_spring_ang_x/y/z`)
8. **Actually set spring stiffness and damping values** from PMX `spring_constant_move` and `spring_constant_rotate`

Step 8 is the critical fix — mmd_tools stores these values but never applies them to the Blender constraint.

### Soft constraint workaround

MMD's constraints are "soft" — even when a DOF is locked (`limit_min == limit_max`), bodies can move elastically past the limit with a spring restoring force. Blender's constraints are "hard" — locked DOFs are frozen.

**Status: disabled.** The Bullet trick (setting `lower > upper` to unlock DOFs) was implemented but caused instability in practice:

- Unlocking **translation** DOFs (e.g., `[0,0]` → unlocked) broke joint pivots — bodies separated and flew apart
- Unlocking **angular** DOFs with spring restoring forces caused oscillation/explosion at typical MMD spring stiffness values

The function `_apply_soft_constraints()` exists in `physics.py` but is not called. The locked DOFs remain hard-locked, which makes hair/clothing slightly stiffer than MMD but stable. This is a known compromise — future work may find better parameters or alternative approaches (e.g., only unlocking specific DOFs that have non-zero spring constants).

### Physics world settings

| Parameter | Value | Notes |
|-----------|-------|-------|
| `substeps_per_frame` | 6 | Matches mmd_tools. Higher values tighten constraints but slow playback. |
| `solver_iterations` | 10 | Matches mmd_tools. |
| `gravity` | Default (-9.81) | mmd_tools doesn't scale gravity. Gravity scaling was tested but removed. |
| `use_split_impulse` | False | Can reduce bounce artifacts but causes stacking instability |

### Springs and soft constraints

**Springs are enabled** with PMX stiffness values. They provide the restoring force that keeps chain bodies connected at joint pivots. Without springs, bodies scatter to joint limit edges under gravity (no force pulling them back to center).

**Soft constraints are disabled.** The Bullet trick (setting `lower > upper` to unlock frozen DOFs) causes oscillation at typical MMD spring stiffness values. The functions remain in code for experimentation.

This means hair/clothing is slightly stiffer than MMD (locked DOFs stay locked) but stable. Cloth mode is the quality path for natural movement.

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
def build_physics(armature_obj, model, scale: float, mode: str = "none") -> None:
    """mode: 'none' | 'rigid_body'"""
```

| Mode | What happens | When to use |
|------|-------------|-------------|
| `none` (default) | Store rigid body/joint data as custom properties on armature. No Blender physics objects created. Clean scene. | Default import |
| `rigid_body` | Create Blender rigid bodies, joints, bone coupling. Matches mmd_tools quality. | Standard physics for hair/skirt/accessories |

The MMD4B panel in the N-panel provides Build/Rebuild/Clear buttons. Rebuild after VMD import or pose changes to sync rigid bodies to the current bone pose.

---

## Operators

### Philosophy

Thin operator layer. Register Blender operators for:

- PMX import (with file browser, undo support)
- Physics build/teardown
- Any operation that benefits from Blender's undo stack

Core logic lives in plain Python functions that operators call. Claude can invoke operators via `bpy.ops` or call functions directly, whichever is cleaner.

### Import operator

```
blender_mmd.import_pmx
```

Parameters:
- `filepath`: Path to .pmx file
- `scale`: Import scale factor (default: 0.08)
- `use_toon_sphere`: Include toon and sphere texture nodes in materials (default: off)

Behavior:
1. Parse PMX file (full parse)
2. Create armature with bones
3. Create mesh with vertex weights
4. Set up IK constraints
5. Log summary of what was imported/skipped

### Physics operators

```
blender_mmd.build_physics              # Build physics (mode: none/rigid_body)
blender_mmd.clear_physics              # Remove all physics objects and metadata
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
- Scale auto-detected from armature's `import_scale` custom property

Behavior:
1. Parse VMD file (bone keyframes, morph keyframes, property/IK toggle keyframes)
2. Find the target armature (active selection or auto-detect)
3. Build Japanese→English bone name lookup from `mmd_name_j` custom properties
4. Apply bone keyframes via per-bone coordinate converter (`_BoneConverter`)
5. Apply morph keyframes to shape key F-curves via `mmd_morph_map`
6. Apply VMD Bézier interpolation handles to F-curves
7. Apply IK toggle keyframes as constraint influence F-curves (CONSTANT interpolation)
8. Set scene FPS to 30 (MMD standard) and extend frame range to fit animation
9. Log summary of matched/unmatched bones and morphs

**Per-bone VMD conversion**: VMD keyframes are in bone-local space. The `_BoneConverter` class constructs a conversion matrix from `bone.matrix_local` (with Y↔Z row swap + transpose) and converts each keyframe via matrix conjugation: `q_mat @ q_vmd @ q_mat.conjugated()`. This depends on correct bone roll (see above).

**Quaternion sign compatibility**: Adjacent quaternion keyframes are checked for sign flips. Since `q` and `-q` represent the same rotation but Blender's NLERP interpolates them differently, we pick the sign closest to the previous keyframe. Without this, bones can take the "long path" (~360° spin instead of staying still). Matches mmd_tools' `__minRotationDiff`.

**Interpolation axis remapping**: VMD Bézier interpolation curves are per-axis (X, Y, Z location + rotation). The `_InterpolationHelper` class computes the correct axis permutation from the bone's conversion matrix, reading from the full 64-byte VMD interpolation block at proper row offsets. Matches mmd_tools' `_InterpolationHelper`. Previous implementation used a hardcoded Y↔Z swap which was incorrect for bones with unusual local axis orientations.

**F-curve handle fixing**: First and last keyframe handles are set explicitly to prevent extrapolation artifacts. Matches mmd_tools' `__fixFcurveHandles`.

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

## Milestones

### Milestone 1: Import PMX — Armature + Mesh ✅

**Deliverables:**
- PMX parser (clean rewrite, Blender-coord output, full PMX 2.0/2.1 support)
- Armature creation with all bones in correct rest positions
- Mesh creation with correct geometry, vertex weights, normals, and UVs
- IK constraints using Blender's native solver
- Import operator with file browser and scale parameter
- Basic helper functions for Claude introspection
- Symlink setup script
- Parser test harness (batch parse + comparison against mmd_tools)

**Validation:**
- Batch parse all user's test PMX files without errors
- Field-by-field comparison against mmd_tools parser output
- Import user's test PMX file, visual comparison: armature matches mmd_tools output
- Vertex weights are correct (test by posing a bone)
- IK chains work (move IK target, chain follows)
- Normals render correctly (smooth shading matches mmd_tools)
- UVs are present and correct (visible in UV editor)

### Milestone 2: Morphs & Shape Keys ✅

Morphs are needed before VMD import, since VMD files contain morph keyframes for facial animation. Without shape keys, VMD playback would be body-only with no facial expressions.

**Deliverables:**
- Vertex morph import as Blender shape keys
- UV morph support (stored as shape key layers or custom data)
- Bone morph support (stored for VMD application)
- Material morph support (stored for future material milestone)
- Shape key management helpers for Claude

**Validation:**
- Import PMX model, verify shape keys appear in Properties > Object Data > Shape Keys
- Activate a facial morph shape key, verify mesh deformation is correct
- Compare shape key count and names against mmd_tools import of the same model

### Milestone 3: VMD Motion Import ✅

VMD import follows morphs so we can see both body motion AND facial animation during playback.

**Deliverables:**
- VMD parser (bone keyframes + morph keyframes + property/IK toggle keyframes)
- Apply bone keyframes to armature as Blender actions/F-curves
- Apply morph keyframes to shape key F-curves
- VMD import operator
- Japanese → English bone name matching via `mmd_name_j` custom properties
- Translation table (`translations.py`) seeded by scanning user's PMX/VMD collection
- `scripts/scan_translations.py` tool
- IK constraint placement fix (first link bone, not end effector)
- Blender-native IK limit properties (no LIMIT_ROTATION constraints)
- IK limit angle conversion (Blender-global → bone-local space)
- VMD IK toggle via constraint influence keyframes
- Scene FPS (30) and frame range auto-set on VMD import

**Validation:**
- Import PMX model + VMD motion, play timeline
- All VMD bone keyframes map to the correct English-named Blender bones
- Character moves according to VMD with facial expressions animating
- Compare playback side-by-side with mmd_tools on the same model + motion
- IK constraint on knee (not ankle), native IK limits on knee PoseBone
- Baseline comparison: 21 bones × 11 frames all within <0.05 tolerance
- Floor penetration comparable to mmd_tools reference (Blender IK solver limitation)

### Milestone 4: Rigid Body Physics ✅

Rigid body physics implemented as intermediate step. Works but has fundamental limitations of Blender's rigid body solver for MMD-style chain physics. The long-term plan is Milestone 4b (cloth conversion).

**Done:**
- ✅ Rigid body creation with collision_collections (shared layer 0 + own group)
- ✅ Non-collision constraints from PMX mask (GENERIC with disable_collisions=True)
- ✅ Joint setup with GENERIC_SPRING (default SPRING2) with spring values applied
- ✅ Soft constraints for locked DOFs (lower > upper = free, spring provides resistance)
- ✅ Collision margin fix (1e-6, Blender default 0.04 too large at 0.08 scale)
- ✅ Dynamic body repositioning to match current bone pose
- ✅ Joint empty repositioning using src_rigid bone delta
- ✅ Bone ↔ rigid body coupling (STATIC, DYNAMIC, DYNAMIC_BONE)
- ✅ RB world disabled during setup (mmd_tools pattern, prevents solver corruption)
- ✅ Depsgraph flushes at key build steps
- ✅ Physics build/clear operators
- ✅ Springs enabled with PMX values, soft constraints disabled (matches mmd_tools baseline)
- ✅ 19 pure-Python unit tests (collision, soft constraints, metadata serialization)

**Known issues:**
- Scrubbing/rewinding can reset baked simulation; must re-bake after rewind
- UI: MMD4B panel provides Build/Rebuild/Clear buttons
- Hair may appear too stiff or too loose depending on model
- No automatic bake on build (user must bake manually or via playback)
- Blender's rigid body solver is fundamentally wrong for MMD chain physics (hair, skirt)

**Where to find baked physics in Blender:**
- Scene Properties → Rigid Body World → Cache section
- "Bake" button bakes simulation, "Free Bake" clears it
- Or via Python: `bpy.ops.ptcache.bake()` / `bpy.ops.ptcache.free_bake()`
- Point cache frame range: `scene.rigidbody_world.point_cache.frame_start/frame_end`

**Test data (`tests/samples/`):**
- `初音ミク.pmx` — simple Miku model (122 bones, 45 rigid bodies, 27 joints)
- `galaxias.vmd` — Galaxias dance motion
- `baseline_mmd_tools.json` — mmd_tools bone transforms at key frames (pose + IK only). 21 bones tracked across 11 frames (0, 100, ..., 1000). Uses Japanese bone names with .L/.R suffixes. Extracted from mmd_tools import with `ik_loop_factor=5`. The sample model is simpler (122 bones) — no arm twist or shoulder cancel bones.
- `miku_galaxias.blend` — mmd_tools reference: **pose + IK only, no physics baked**. Do not use this blend for physics comparison — mmd_tools physics was never applied to it.

### Milestone 4b: Physics Cleanup ✅

Simplified physics into two modes: none (default) and rigid_body. Cloth/soft body cage code removed (planned for future phase). Added MMD4B panel for physics controls.

#### Phase 1: Restructure + Default-Off (`mode="none"`) ✅

- [x] Refactored `physics.py` with `mode` parameter (`none` / `rigid_body`)
- [x] Metadata storage: JSON on `armature_obj["mmd_physics_data"]` (all RB+joint fields)
- [x] `none` mode stores metadata, creates nothing
- [x] `rigid_body` mode extracted into `_build_rigid_body_physics()` helper
- [x] 6 metadata serialization tests (round-trip, fields, JSON validity)
- [x] Operator updated with `mode` EnumProperty
- [x] MMD4B panel: Build/Rebuild/Clear buttons in N-panel

#### Phase 2: Simplify Rigid Body Mode (`mode="rigid_body"`) ✅

- [x] Springs enabled with PMX stiffness/damping values (matches mmd_tools which uses Blender defaults)
- [x] Soft constraints disabled (no `_apply_soft_constraints()` call) — oscillates
- [x] Matches mmd_tools baseline: stable physics with springs providing restoring force
- [x] Functions kept in file for future experimentation

#### Phase 3: Soft body / cloth improvements (planned)

Cloth-on-cage approach for hair/skirt deformation is planned as a future phase. The idea: generate a hidden cage tube along a bone chain, apply Cloth modifier to the cage, and bind the visible mesh via Surface Deform. Chain detection code exists in `chains.py` (pure Python, 9 tests). Implementation deferred until rigid body workflow is mature.

#### Rigid Body Mode — Lessons Learned (Historical)

What we learned from implementing and testing M4 rigid body physics. This informs Phase 2 cleanup and sets realistic expectations.

**What works (keep these):**
- RBW disabled during setup (`rigidbody_world.enabled = False`) — without this, solver corrupts initial state
- Depsgraph flushes (`scene.frame_set(scene.frame_current)`) at key build points — without this, `matrix_world` is stale
- Collision margin 1e-6 — Blender default 0.04 is huge at 0.08 model scale
- Collision layers: shared layer 0 + own group, with GENERIC constraints for non-collision pairs
- Dynamic body repositioning to match bone pose before creating joints
- Joint empty repositioning using src_rigid's bone delta
- Bone coupling: STATIC → bone parent, DYNAMIC → COPY_TRANSFORMS, DYNAMIC_BONE → COPY_ROTATION via tracking empty
- CAPSULE/SPHERE/BOX mesh geometry for collision shapes (empty mesh = zero-size collision)
- Rotation negation `(-x,-y,-z)` beyond parser's Y↔Z swap
- Joint rotation limits: negate AND swap min/max
- IK muting on physics bones (prevents IK solver from fighting COPY_TRANSFORMS on chain bones)
- Deferred tracking empty reparenting (mmd_tools two-phase pattern: build muted → depsgraph flush → reparent → unmute)
- Tracking constraint muting during build (create muted, unmute after reparenting)
- Physics cache end matches scene frame range

**What doesn't work well (known compromises):**
- **Soft constraints (lower > upper trick)**: Meant to make locked DOFs elastic. In practice, causes oscillation or explosion with typical MMD spring stiffness. Currently disabled.
- **Springs**: Applied from PMX values using Blender default SPRING2 (matching mmd_tools). Damping is capped at 1.0 — some models may need manual tuning.
- **Scrubbing/rewinding**: Resets baked simulation. Must re-bake after rewind. This is a Blender limitation, not a bug.
- **`frame_set()` doesn't run physics**: Only timeline playback or explicit baking advances the simulation. No way to "preview" physics at a specific frame.
- **One-frame lag**: Inherent to Blender's dependency graph. Physics always trails bone motion by one frame.
- **Physics explosion on rewind**: Baked cache gets cleared, dynamic bodies may be in wrong positions. Mitigation: always bake before playback, re-bake after rewind.

**Phase 2 approach (implemented):**
- Dropped `_apply_soft_constraints()` — the lower>upper trick is unreliable. DOFs stay hard-locked as PMX defines them.
- Springs enabled with PMX values — provides restoring force that keeps chain bodies connected. Matches mmd_tools (which uses Blender defaults).
- Don't try to match MMD physics perfectly — the goal is "mmd_tools quality" which is itself imperfect.
- Non-collision constraints kept — they're correct and prevent false collisions.
- Non-collision constraint creation uses template-and-duplicate pattern (O(log N) bpy.ops calls) to avoid Blender hanging on models with many constraint pairs.

**What NOT to do:**
- Don't try to match MMD's CCDIK-based physics solver — fundamentally different architecture

#### Rigid Body Parity Audit (mmd_tools alignment) ✅

Systematic audit of rigid body build pattern against mmd_tools. Fixes applied to match mmd_tools' proven build order and constraint types.

**Fixed:**
- [x] DYNAMIC bodies use COPY_TRANSFORMS (location + rotation from RB), not COPY_ROTATION. Prevents chain divergence at hair tips.
- [x] IK constraints muted on DYNAMIC/DYNAMIC_BONE bones during physics build AND playback. Without this, IK solver (e.g. hair IK chain_count=5) overrides COPY_TRANSFORMS positions on chain bones.
- [x] Tracking constraints (COPY_TRANSFORMS/COPY_ROTATION) created muted during build, unmuted in post-build after tracking empties are reparented.
- [x] Tracking empty reparenting deferred to post-build: empties created with matrix_world from bone pose, reparented to RBs in batch after depsgraph flush (preserving matrix_world).
- [x] Physics cache frame_end synced to scene frame_end (both on VMD import and physics build).
- [x] IK unmuted in `clear_physics()` when physics is removed.
- [x] Spring type: removed explicit SPRING1 override, using Blender default (SPRING2) to match mmd_tools which never sets spring_type.
- [x] LIMIT_ROTATION override for IK limits: Blender clamps `ik_min_x` to [-π,0], silently losing small positive minimums (e.g. knee 0.0087 rad). Added `mmd_ik_limit_override` LIMIT_ROTATION constraint on affected axes only.

**Not changed (investigated, no fix needed):**
- Bone disconnection: all hair chain bones already have `use_connect=False` from PMX data.
- Soft constraints: mmd_tools doesn't implement inverted-limits workaround either. Springs enabled, soft constraints disabled — matches mmd_tools.

### MMD4B Panel

**N-panel**: Tab "MMD4B" in 3D Viewport sidebar. Visible when active object is an MMD armature (or child mesh).

**Layout** (parent panel shows model name, sub-panels below):

**Physics sub-panel:**
- **No physics state:** "Build Rigid Body" button (calls `build_physics` with `mode=rigid_body`)
- **Physics active:** Shows rigid body count, "Rebuild" button (re-parses PMX and rebuilds), "Clear" button

**IK Toggle sub-panel:**
- **All On / All Off** buttons at top
- Per-chain toggle buttons showing current state (checkbox icon, `depress` for visual feedback)
- Toggles IK constraint `influence` between 0.0 and 1.0
- Also toggles `mmd_ik_limit_override` LIMIT_ROTATION constraints in the chain
- Chains discovered by scanning pose bones for IK constraints

**Workflow:** Import PMX → optionally import VMD → click "Build Rigid Body" in MMD4B panel → play animation. If you import a VMD after building physics, click "Rebuild" to sync rigid bodies to the new starting pose. Use IK Toggle to disable IK chains for non-standard poses.

### Milestone 5: Materials & Textures ✅

**Status:** Done — Principled BSDF-based shader with global controls

**Deliverables:**
- Two shader modes via "Toon & Sphere Textures" checkbox on PMX import (off by default):
  - **"MMD Shader Basic"** (default): Principled BSDF with Color, Alpha, Emission, Roughness inputs only. No toon/sphere nodes or UV group. Lightweight for rendering and compositing.
  - **"MMD Shader"** (checkbox on): Full toon/sphere pipeline — 5 processing nodes (toon multiply, sphere multiply/add, sphere select, Principled BSDF). Includes "MMD UV" node group for sphere/toon coordinate generation.
- Toon texture support (full mode only) with bundled fallback: `blender_mmd/data/toons/toon01-10.bmp` — shared toon textures resolve from PMX dir → parent dir → bundled addon data
- Sphere texture modes (full mode only): multiply (sRGB), add (Linear), subtex (UV1) — controlled by Sphere Fac and Sphere Add inputs
- Global material controls via armature custom properties + drivers: `mmd_emission` on both modes; `mmd_toon_fac`, `mmd_sphere_fac` on full mode only
- Texture loading (diffuse, sphere, toon) with dedup by path
- Per-face material assignment via `foreach_set`
- UV V-flip (`V = 1.0 - V`) for PMX DirectX→Blender OpenGL convention
- Overlapping face detection: overlay materials (eye highlights, layers) auto-set to `blend_method="BLEND"` with `show_transparent_back=False` to prevent z-fighting
- Material flag properties: `enabled_toon_edge`, `enabled_drop_shadow`, `enabled_self_shadow_map`, `enabled_self_shadow`
- Edge color/size stored as custom properties on Blender materials for future outline support

**Not yet done (optimization):**
- `foreach_set` for UV assignment (currently per-loop Python iteration — slow on large models)
- Degenerate face cleanup (mmd_tools removes 2 faces on this model; we keep all)
- Sharp edge detection from normals (mmd_tools marks sharp edges at angle threshold)

**Deferred to future milestones:**
- Edge/outline (solidify + edge materials, using stored `mmd_edge_color`/`mmd_edge_size`)
- Material morphs (VMD keyframes driving material properties)
- Per-material mesh split (needed for selective outline rendering)
- Light linking (Principled BSDF compatible, but needs per-object mesh split)

### Milestone 6: Animation Polish

**Status:** In progress — remaining items are low priority, contributions welcome

**Done:**
- Additional transform (grant parent / 付与親) — TRANSFORM constraints + shadow bones for D bones, shoulder cancel, arm twist, eye tracking

**Remaining (contributions welcome — Claude Code recommended for development):**
- VMD camera motion import
- CCD IK solver (matching MMD output more closely)

### Milestone 7: Custom Shader & Creative Tools (planned)

Now that we're independent of mmd_tools and have a solid import pipeline, we can start doing more creative things beyond MMD compatibility.

**Custom simplified shader:**
- A clean, modern shader that looks good in EEVEE and Cycles without the MMD-specific toon/sphere pipeline
- Goal: "anime-style but production-ready" — good for rendering, compositing, and creative projects
- Not bound by MMD's material model — can use Principled BSDF, custom node groups, or procedural approaches
- Switchable post-import (alongside existing mmd/simple modes)

**Edge/outline rendering:**
- Solidify modifier + inverted-normals material for anime outlines
- Per-material control (some materials need outlines, others don't)
- May require per-material mesh split for selective application

**Material morphs:**
- VMD material keyframes → Blender material property drivers/keyframes
- Covers effects like: glow, transparency fade, color shift, toon changes
- Needs careful mapping of MMD material morph operations (add/multiply) to Blender node inputs

### Future Roadmap

Features beyond the current milestone plan. Ordered roughly by impact/feasibility.

#### SDEF — Spherical Deformation (volume preservation)

PMX supports SDEF bone weighting as an alternative to standard linear blend skinning (LBS). SDEF preserves volume at joints — prevents the "candy wrapper" collapse at elbows and knees that LBS causes when bones rotate significantly.

**How it works in MMD:** Each SDEF vertex stores three extra parameters: C (center point), R0, and R1 (reference positions for each bone). Instead of linearly blending the two bone transforms, SDEF spherically interpolates rotations around C, keeping the mesh surface on a sphere and preventing volume loss.

**Current state:** Our parser reads and stores SDEF data (`BoneWeightSDEF` with `center`, `r0`, `r1` fields). Weight assignment uses the same BDEF2 logic (linear blend). The spherical correction is not applied.

**Implementation options:**
1. **Geometry Nodes** (most Blender-native, Blender 5.0+ has Geometry Attribute Constraint): Compute corrective offsets per-vertex based on bone rotations and SDEF parameters. Would run as a modifier after Armature.
2. **Shape key driver** approach: Pre-compute corrective shape keys for key rotation angles, blend via drivers. Approximation but fast.
3. **Corrective Smooth modifier** (quick approximation): Already available, preserves volume but not SDEF-accurate. Could be a "good enough" interim solution.

**Impact:** High for character animation quality. Noticeable on elbows, knees, shoulders. Most visible in close-up renders and extreme poses.

#### Performance & Mesh Optimizations

- **UV foreach_set:** Replace per-loop UV assignment with flat-array `foreach_set` (same pattern as material_index assignment). Significant speedup on 100k+ vertex models.
- **Degenerate face cleanup:** Remove zero-area faces during import (matching mmd_tools). Prevents rendering artifacts and reduces poly count.
- **Sharp edge marking:** Detect edge angles from custom normals and mark sharp edges (mmd_tools does this at 179° threshold). Affects auto-smooth and edge split behavior.
- **Parallel texture loading:** Load textures in batch rather than one-at-a-time during material setup.
- **Decimate / face reduction:** Many MMD models are over-tessellated (100k+ faces where 30k would suffice). Post-import decimate pass or operator that intelligently reduces face count while preserving UV seams, shape keys, and material boundaries. Could use Blender's Decimate modifier with smart settings, or a custom approach that respects MMD-specific topology (face layers, edge loops at material boundaries).

#### VMD Camera Motion

Import VMD camera keyframes (position, rotation, FOV, distance) as Blender camera animation. VMD camera data is already parsed; just needs the Blender camera creation and F-curve setup.

#### Bone Morphs / Group Morphs

VMD can keyframe bone morphs (pose presets like "T-pose", "fist") and group morphs (combinations). Currently parsed but not applied. Needs driver or action-based implementation.

#### UV Morphs

PMX UV morphs offset UV coordinates per-vertex. Need shape key UV layers or a Geometry Nodes approach since Blender shape keys only affect vertex positions, not UVs.

#### CCD IK Solver

MMD uses CCD (Cyclic Coordinate Descent) IK which converges differently than Blender's built-in IK. A custom CCD solver (evaluated per-frame via driver or handler) would match MMD motion more precisely. High complexity, moderate impact (current IK is "close enough" for most motions).

#### Retargeting Tools

Since we understand MMD bone rolls and local axes, we could build tools to retarget between:
- MMD models (different bone structures)
- MMD ↔ standard rigs (Mixamo, Rigify)
- Motion capture data → MMD armature

This is where correct bone roll really pays off — the roll defines what "rotate arm 45° around X" means physically.

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

---

## Design Principles

1. **One-way pipeline**: PMX → Blender. No export, no round-trip concerns.
2. **Claude is the UI**: No panels, no sidebar, no menus beyond import. All interaction through Claude Code + blender-agent.
3. **Blender-native first**: Use Blender's own systems (IK solver, rigid bodies, collections) rather than reimplementing. Customize only where MMD compatibility demands it.
4. **Progressive enhancement**: Each milestone builds on the previous. The addon is useful at every stage.
5. **Minimal metadata**: Don't pollute Blender objects with MMD-specific data. Store only what's needed for the current feature set.
6. **Fix as we go**: Handle Blender API issues as they surface during testing rather than auditing upfront.
