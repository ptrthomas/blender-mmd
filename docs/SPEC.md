# BMMD Specification

A ground-up rewrite of [blender_mmd_tools](../../../blender_mmd_tools) targeting **Blender 5.0+**, designed to be driven by **Claude Code** via [blender-agent](../../../blender-agent).

## Goals

1. Import PMX models into Blender with correct armature, mesh, and vertex weights
2. Rewrite physics integration using modern Blender APIs (collision_collections, drivers)
3. Eliminate the traditional addon UI — Claude Code is the interface
4. Fix the core physics problems in mmd_tools: O(n²) collision constraints, hard vs soft constraint mismatch, IK solver incompatibility

## Non-goals

- PMX/PMD/VMD export (one-way import only, no round-trip)
- PMD format support (PMX only)
- Traditional Blender UI panels or sidebar
- Backwards compatibility with mmd_tools object hierarchy or metadata
- Material library system
- Rigify integration

---

## Architecture

### Addon identity

| Field | Value |
|-------|-------|
| Project name | `bmmd` |
| Blender addon ID | `blender_mmd` |
| Display name | `Blender MMD` |
| Target Blender | 5.0+ |
| Python | 3.11+ |
| License | GPL-3.0-or-later |

### Dependency on blender-agent

bmmd expects blender-agent to be installed and running. This is documented, not enforced via `blender_manifest.toml`. Claude Code communicates with Blender through blender-agent's HTTP bridge and calls bmmd's operators and helper functions.

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
│   ├── importer.py           # PMX → Blender object creation
│   ├── physics.py            # Rigid body and joint setup
│   ├── armature.py           # Bone creation and IK setup
│   ├── mesh.py               # Mesh creation and vertex weights
│   ├── operators.py          # Thin Blender operator layer
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

1. Edit bmmd source files
2. Restart Blender to reload the addon (blender-agent supports quit + relaunch from shell)
3. Execute test code via blender-agent (`POST http://localhost:5656`)
4. Read results from blender-agent's session log (`output/<timestamp>/agent.log`)
5. Take screenshots for visual validation (`bpy.ops.screen.screenshot(filepath=f"{SESSION}/screenshot.png")`)
6. Repeat

blender-agent provides: code execution, session-scoped output directory, logging, screenshots, and Blender lifecycle control. bmmd has no Python import dependency on blender-agent — they are independent Blender extensions that communicate only through Claude Code.

### Extension manifest

`blender_mmd/blender_manifest.toml`:

```toml
schema_version = "1.0.0"

id = "blender_mmd"
version = "0.1.0"
name = "Blender MMD"
tagline = "Import MMD (PMX) models into Blender"
maintainer = "bmmd contributors"
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
- **Comparison test**: For each test file, parse with both bmmd and mmd_tools parsers, compare output field-by-field (vertex positions, bone data, materials, etc.)
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

**At parse time.** The parser outputs all positions, normals, and rotations in Blender's coordinate system (Z-up, right-handed). The conversion from MMD's Y-up system happens inside the parser:

- Positions: `(x, -z, y)` — MMD Y-up → Blender Z-up, MMD Z-forward → Blender -Y
- Rotations: `(x, -z, y)` — same axis remapping as positions
- Normals: same conversion as positions

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

**Normals**: PMX provides per-vertex normals (already converted to Blender coords by the parser). Apply as custom split normals via the Blender 5.0+ normals API (e.g. `mesh.normals_split_custom_set_from_vertices()` or its current equivalent).

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

- **Flag bit set → bone index**: The bone's tail points at the position of the referenced bone. If the referenced bone index is -1 or invalid, use a minimum-length offset.
- **Flag bit unset → position offset**: The bone's tail is at `head + offset`. If the offset is zero (tail == head), use a minimum-length offset along the bone's local Y axis to prevent Blender from deleting the zero-length bone.

The minimum length offset should be small (e.g. 0.001 in Blender units) — just enough to keep the bone alive.

### IK setup

Use **Blender's native IK solver** for milestone 1:

- Add `IK` constraint to the IK constraint bone
- Set `chain_count` from PMX IK link count
- Set `iterations` from PMX `loopCount`
- Apply per-link rotation limits via `LIMIT_ROTATION` constraints
- Convert PMX angle limits to Blender's local bone coordinate space

CCD IK solver is a future enhancement for VMD motion fidelity.

### Transform order

Ignored for milestone 1. Blender's dependency graph handles evaluation order based on constraint dependencies. When additional transforms are implemented, transform_order will be used to set up correct constraint evaluation.

### SDEF

Parse SDEF data from PMX and store it in the data model. Do not implement deformation logic in milestone 1. Future implementation via Geometry Nodes (using the Geometry Attribute Constraint available in Blender 5.0+).

### Scale

Default import scale: **0.08** (matching mmd_tools). Configurable at import time via operator parameter.

---

## Physics System

### Overview

The physics system is the primary motivation for bmmd. Blender's rigid body physics (Bullet engine) is fundamentally capable but mmd_tools has three bugs/limitations that cause most of the problems users experience:

1. **Spring values never applied** — mmd_tools stores PMX spring stiffness/damping in custom properties but never sets them on the actual `GENERIC_SPRING` constraints. This is the main reason rigid bodies fly apart.
2. **O(n²) non-collision constraint objects** — mmd_tools creates an EMPTY with `disable_collisions=True` for every non-colliding pair. A model with 100 rigid bodies can generate 200+ extra objects. Blender's `collision_collections` API eliminates all of these.
3. **Hard vs soft constraint mismatch** — When PMX locks a DOF (`limit_min == limit_max`), MMD treats it as elastic. Blender freezes it. This makes hair/clothing stiff and unnatural.

### What we can and cannot fix

| Problem | Fix | Confidence |
|---------|-----|------------|
| Rigid bodies flying apart | Actually apply spring stiffness/damping to constraints | High |
| 200+ extra non-collision objects | Use `collision_collections` (native API) | High |
| Stiff hair/clothing (hard constraints) | Unlock frozen DOFs, use spring restoring force | Medium |
| One-frame physics lag behind bone motion | Inherent to Blender's dependency graph — cannot fix | N/A |
| Interactive posing with live physics | Not possible — physics only advances during timeline playback | N/A |

**Workflow expectation**: Import model, apply VMD motion, play timeline forward (or bake). Physics settles over the first few frames. This matches the existing mmd_tools workflow but with bodies that actually stay connected and respond elastically.

### Collision groups

Use Blender's `collision_collections` property (20-element boolean array on `RigidBodyObject`). This is a stable, documented API.

MMD uses 16 collision groups with a "non-collision mask" (bit set = don't collide). Blender uses "collision collections" (bit set = do collide). Conversion:

```python
# MMD: collision_group_number (0-15), collision_group_mask (16-bit, 1=don't collide)
# Blender: collision_collections (20-bool, True=do collide)

blender_collections = [False] * 20
blender_collections[pmx_rigid.collision_group_number] = True  # own group

for i in range(16):
    if not (pmx_rigid.collision_group_mask & (1 << i)):  # bit NOT set = DO collide
        blender_collections[i] = True
```

This eliminates all non-collision constraint objects entirely. For a typical model this removes 100–300 Blender objects.

### Rigid body creation

For each PMX rigid body:

1. Create empty mesh object
2. Add Blender rigid body (`bpy.ops.rigidbody.object_add`)
3. Set collision shape (SPHERE, BOX, CAPSULE)
4. Set physics properties (mass, friction, bounce, linear/angular damping)
5. Set `collision_collections` from inverted MMD mask
6. Set kinematic flag based on mode (STATIC = kinematic)

### Rigid body modes

| PMX Mode | Behavior | Blender Implementation |
|----------|----------|----------------------|
| STATIC (0) | Bone-driven, no physics | Kinematic rigid body, parented to bone |
| DYNAMIC (1) | Free physics simulation | Active rigid body, bone reads physics via COPY_TRANSFORMS |
| DYNAMIC_BONE (2) | Physics with bone tracking | Active rigid body, bone reads rotation via COPY_ROTATION |

**STATIC**: The rigid body follows the bone. It pushes other active bodies but is not affected by physics. Implemented as kinematic rigid body parented directly to the bone (BONE parent type).

**DYNAMIC**: Physics drives the rigid body freely. A tracking empty is parented to the rigid body, and the bone has a COPY_TRANSFORMS constraint targeting the empty. The bone follows the physics body.

**DYNAMIC_BONE**: Like DYNAMIC, but the bone only copies rotation (not translation) from the physics body. Translation comes from the bone's parent. This is the typical mode for hair and clothing — the strand rotates with physics but stays attached to the head/body.

### Joints

Create joint constraints using `GENERIC_SPRING` rigid body constraint type with **`spring_type = 'SPRING1'`**.

SPRING1 vs SPRING2: Blender offers two spring implementations. SPRING2 has a [known bug](https://projects.blender.org/blender/blender/issues/55958) where angular springs at high stiffness cause jitter and explosions. Since MMD joints rely heavily on angular springs for hair/skirt physics, we use SPRING1 exclusively.

For each PMX joint:

1. Create empty object with `rigid_body_constraint`
2. Set type to `GENERIC_SPRING`, `spring_type` to `SPRING1`
3. Connect source and destination rigid bodies (`object1`, `object2`)
4. Enable all 6 DOF limits (`use_limit_lin_x/y/z`, `use_limit_ang_x/y/z`)
5. Set translation limits from PMX `limit_move_lower/upper` (with coordinate conversion)
6. Set rotation limits from PMX `limit_rotate_lower/upper` (with coordinate conversion)
7. Enable all 6 spring axes (`use_spring_x/y/z`, `use_spring_ang_x/y/z`)
8. **Actually set spring stiffness and damping values** from PMX `spring_constant_move` and `spring_constant_rotate`

Step 8 is the critical fix — mmd_tools stores these values but never applies them to the Blender constraint.

### Soft constraint workaround

MMD's constraints are "soft" — even when a DOF is locked (`limit_min == limit_max`), bodies can move elastically past the limit with a spring restoring force. Blender's constraints are "hard" — locked DOFs are frozen.

Workaround using a Bullet trick:

```python
# When PMX has limit_min == limit_max (locked DOF):
# Set lower > upper to "unlock" the DOF in Bullet
constraint.limit_lin_x_lower = 1.0
constraint.limit_lin_x_upper = 0.0
# Then let spring forces provide the restoring behavior
constraint.use_spring_x = True
constraint.spring_stiffness_x = pmx_spring_value  # or tuned default
constraint.spring_damping_x = pmx_damping_value
```

When `lower > upper`, Bullet treats the axis as unconstrained but the spring pulls the body back toward the rest position. This approximates MMD's elastic behavior.

This won't match MMD's solver exactly, but it should produce natural-looking hair and clothing motion rather than the stiff/frozen behavior in mmd_tools.

### Physics world settings

Recommended defaults for MMD models:

| Parameter | Value | Notes |
|-----------|-------|-------|
| `substeps_per_frame` | 10–20 | Higher = more accurate, slower. 10 for preview, 20 for render bake |
| `solver_iterations` | 10 | Default is fine for most models |
| `use_split_impulse` | False | Can reduce bounce artifacts but causes stacking instability |

### Physics chain discovery

Users select bones in Blender's viewport, then ask Claude to apply/modify physics. Claude reads the selection via helper functions and operates on the selected bones.

No automatic chain detection or name-based body part identification. Claude can query the rigid body/joint graph topology via helpers if needed.

### Known limitations

These are inherent to Blender's Bullet integration and cannot be fixed in addon code:

- **One-frame lag**: Blender evaluates armature → physics → feeds back next frame. Hair/clothing trails body motion by one frame.
- **No interactive physics**: Physics only advances during timeline playback. Cannot pose and see physics respond without playing.
- **No mesh deformation feedback**: Collision shapes don't update when the mesh deforms. Physics bodies use their rest-pose shapes.
- **Spring precision**: SPRING1 damping is capped at 1.0. Some MMD models may need manual damping adjustment.

### Future: alternative physics modes

The architecture supports destructive swap of physics modes per bone chain:

- A user selects a skirt bone chain and asks Claude to convert from rigid bodies to Blender cloth simulation
- The original rigid bodies are removed
- New cloth sim is applied to the mesh region influenced by those bones
- This is one-way (no re-export to PMX)

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

Behavior:
1. Parse PMX file (full parse)
2. Create armature with bones
3. Create mesh with vertex weights
4. Set up IK constraints
5. Log summary of what was imported/skipped

### Physics operators

```
blender_mmd.build_physics     # Create rigid bodies and joints from parsed PMX data
blender_mmd.clear_physics     # Remove all physics objects
```

### VMD import operator

```
blender_mmd.import_vmd
```

Parameters:
- `filepath`: Path to .vmd file
- `scale`: Scale factor (default: 0.08, must match import scale)

Behavior:
1. Parse VMD file (bone keyframes, camera, morph keys)
2. Apply bone keyframes to the active armature
3. Log summary of applied keyframes

---

## Helper Functions (for Claude)

Module: `blender_mmd.helpers`

Provide introspection and state-change helpers that Claude calls via blender-agent:

### Introspection

- `get_selected_bones()` — Returns names/properties of currently selected pose bones
- `get_model_info()` — Returns model name, bone count, mesh stats
- `get_ik_chains()` — Returns IK chain information (target, chain bones, limits)
- `get_physics_objects()` — Returns rigid bodies and joints with their properties

### State changes

- `set_bone_visibility(collection_name, visible)` — Show/hide bone collections
- `select_bones_by_name(names)` — Select specific bones programmatically

These helpers evolve over time. Start minimal, add as needed.

---

## Logging

bmmd relies on **blender-agent's existing logging infrastructure**. blender-agent already logs every code execution request and response to a session-scoped `agent.log` file (in `output/<timestamp>/agent.log`). Since Claude Code drives all operations through blender-agent, import results, errors, and tracebacks are captured there automatically.

Within addon code, use Python's `logging` module with a `blender_mmd` logger for structured diagnostics:

- **INFO**: Import summary (bone count, mesh stats, skipped features)
- **WARNING**: Unimplemented features encountered during import
- **DEBUG**: Detailed per-bone, per-vertex information

When a PMX file uses features not yet implemented (morphs, display frames, etc.), log a warning and skip. No errors, no user-facing dialogs.

The `blender_mmd` logger uses the default Python handler (stderr). During development, launch Blender from a terminal to see output live. Claude can also read blender-agent's session log for diagnostics. No separate log file or custom file handler needed — blender-agent already handles this.

### Error handling

Let parsing exceptions propagate. The import operator catches exceptions at the top level and logs the error. No defensive recovery logic in the parser — if a PMX file is corrupt or truncated, the import fails with a clear traceback.

---

## Milestones

### Milestone 1: Import PMX — Armature + Mesh (current)

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

### Milestone 2: Physics

Physics comes early because it's the primary motivation for bmmd. We need to prove the improvements work before investing in materials, morphs, etc.

**Deliverables:**
- Rigid body creation with `collision_collections` (no non-collision constraint objects)
- Joint setup with `GENERIC_SPRING` / `SPRING1` — **spring values actually applied**
- Soft constraint workaround for locked DOFs
- Bone ↔ rigid body coupling for all three modes (STATIC, DYNAMIC, DYNAMIC_BONE)
- Physics build/clear operators
- Physics world setup with recommended defaults

**Validation:**
- Import a model with hair/skirt physics, build physics, play timeline
- Hair and clothing move naturally and don't fly apart
- Rigid bodies stay connected to their constraints
- Compare object count vs mmd_tools (should be 100–300 fewer objects)
- Compare side-by-side with mmd_tools physics on the same model

### Milestone 3: VMD Motion Import

VMD import is pulled forward to milestone 3 because physics validation requires motion. A static model with physics proves nothing — we need to see hair and clothing respond to body movement during playback.

**Deliverables:**
- VMD parser (bone keyframes at minimum)
- Apply bone keyframes to armature as Blender actions/F-curves
- VMD import operator
- Japanese → English bone name matching via `mmd_name_j` custom properties
- Translation table (`translations.py`) seeded by scanning user's PMX/VMD collection
- `scripts/scan_translations.py` tool

**Validation:**
- Import PMX model + VMD motion, build physics, play timeline
- All VMD bone keyframes map to the correct English-named Blender bones
- Character moves according to VMD, hair/clothing follows with physics
- This is the key end-to-end test: does our physics actually work better than mmd_tools?
- Compare playback side-by-side with mmd_tools on the same model + motion

### Milestone 4: Materials & Textures

**Deliverables:**
- Material creation from PMX data
- Texture loading (diffuse, sphere, toon)
- Basic EEVEE material setup
- Per-material mesh split operation (for outline support)

### Milestone 5: Morphs & Shape Keys

**Deliverables:**
- Vertex morph import as shape keys
- Bone morph support
- Material morph support
- Shape key management helpers for Claude

### Milestone 6: Animation Polish

**Deliverables:**
- VMD camera motion import
- VMD morph keyframe import (requires milestone 5)
- CCD IK solver (matching MMD output more closely)
- Keyframe management helpers

### Milestone 7: Advanced Physics

**Deliverables:**
- Driver-based DYNAMIC_BONE coupling (replacing constraints, reducing lag)
- Cloth/hair simulation conversion for selected bone chains
- SDEF implementation via Geometry Nodes
- Physics tuning helpers for Claude

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

---

## Design Principles

1. **One-way pipeline**: PMX → Blender. No export, no round-trip concerns.
2. **Claude is the UI**: No panels, no sidebar, no menus beyond import. All interaction through Claude Code + blender-agent.
3. **Blender-native first**: Use Blender's own systems (IK solver, rigid bodies, collections) rather than reimplementing. Customize only where MMD compatibility demands it.
4. **Progressive enhancement**: Each milestone builds on the previous. The addon is useful at every stage.
5. **Minimal metadata**: Don't pollute Blender objects with MMD-specific data. Store only what's needed for the current feature set.
6. **Fix as we go**: Handle Blender API issues as they surface during testing rather than auditing upfront.
