# Blender MMD Importer

Read `docs/SPEC.md` first. It is the single source of truth for architecture, decisions, and milestones.

## Quick orientation

- **This project**: PMX/PMD/VMD importer addon for Blender 5.0+, driven by Claude Code
- **Milestone 1** (done): PMX parser + armature + mesh import
- **Milestone 2** (done): Morphs / shape keys
- **Milestone 3** (done): VMD motion import (bone keyframes, morph keyframes, bone roll)
- **Milestone 3.5** (done): IK fix — correct constraint placement, native limits, VMD IK toggle
- **Milestone 4** (done): Rigid body physics — functional but limited by Blender's RB solver
- **Milestone 4b** (done): Physics cleanup — two modes (none, rigid_body), MMD4B panel with Build/Rebuild/Clear. NCC proximity slider + draft checkbox, per-chain collision/physics toggles, NCC rebuild. Cloth/soft body deferred to future phase.
- **Milestone 5** (done): Materials & textures — Principled BSDF-based "MMD Shader" node group, bundled toon textures with fallback, global controls via armature drivers (emission/toon/sphere), per-face assignment, UV V-flip, overlapping face fix.
- **Milestone 6** (in progress): Animation polish — additional transforms done (grant parent, shadow bones). PMD format support + VMD bone name auto-mapping done. Remaining: VMD camera, CCD IK
- **Milestone 7** (planned): Creative tools — edge/outline rendering, material morphs

## Reference repos (siblings in ../  )

- `../blender_mmd_tools/` — Original mmd_tools addon. Use `mmd_tools/core/pmx/__init__.py` as parser reference. Do NOT fork — clean rewrite.
- `../blender_mmd_tools_append/` — UuuNyaa's cloth physics converter. Key reference for M4b: converts MMD rigid body chains to Blender cloth sim. See `mmd_tools_append/converters/physics/rigid_body_to_cloth.py`.
- `../blender-agent/` — HTTP bridge for controlling Blender. Read its `CLAUDE.md` for usage. blender-mmd has no import dependency on it.

## Development

```bash
# Symlink addon into Blender extensions
scripts/setup.sh

# Start Blender (connects to existing instance or launches new one)
python3 ../blender-agent/start_server.py

# Use blender-agent (port 5656) for execution, screenshots, and log monitoring
# Module reloading is unreliable — restart Blender for code changes

# Restart Blender (for code changes)
curl -s localhost:5656 --data-binary @- <<< 'bpy.ops.wm.quit_blender()' || true
sleep 1 && pkill -x Blender 2>/dev/null || true
sleep 1 && python3 ../blender-agent/start_server.py

# Clear default scene objects before importing
curl -s localhost:5656 --data-binary @- <<'PYEOF'
import bpy
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
PYEOF

# Zoom to selected object (run after selecting something)
curl -s localhost:5656 --data-binary @- <<'PYEOF'
import bpy
for area in bpy.context.screen.areas:
    if area.type == 'VIEW_3D':
        for region in area.regions:
            if region.type == 'WINDOW':
                with bpy.context.temp_override(area=area, region=region):
                    bpy.ops.view3d.view_selected()
                break
        break
PYEOF

# Screenshots: two-step process (NEVER use curl -o to save screenshots)
# Step 1: Tell Blender to save screenshot to disk
curl -s localhost:5656 --data-binary @- <<'PYEOF'
bpy.ops.screen.screenshot(filepath=f"{OUTPUT}/screenshot.png")
PYEOF
# Step 2: Read the saved PNG file with the Read tool
# The curl response is JSON, NOT an image. Never pipe/save it as a PNG.
```

## Cross-project contributions

When working on blender-mmd and encountering opportunities to improve blender-agent (e.g. Blender 5.0 API hints, common 3D workflow helpers, better error messages), note them and contribute upstream to `../blender-agent/`.

## Key decisions

- **Bone names**: English in Blender, Japanese stored as `mmd_name_j` custom property (for VMD matching)
- **Coordinate conversion**: Done in parser. Downstream code uses Blender coords only.
- **IK constraints**: Placed on first link bone (e.g. knee), NOT the end effector (ankle). Uses Blender-native `ik_min_x/max_x` properties instead of `LIMIT_ROTATION` constraints. `ik_loop_factor` param (default 5) multiplies PMX iteration count for good foot placement.
- **IK toggle**: VMD property section parsed and applied as IK constraint `influence` keyframes (0.0/1.0 with CONSTANT interpolation). MMD4B panel toggle uses `constraint.mute` (not influence) so user overrides persist during animation playback — `mute` is immune to F-curve evaluation. Physics build/clear preserves user mute state.
- **VMD quality**: Quaternion sign compatibility prevents NLERP long-path artifacts. Interpolation axis remapping via `_InterpolationHelper` (matches mmd_tools). F-curve first/last handle fixing.
- **Mesh smoothing**: All faces smooth-shaded, sharp edges marked at 179° before custom normals (required for `normals_split_custom_set` to work correctly).
- **Scene settings**: VMD import sets FPS to 30 (MMD standard) and extends frame range to fit animation.
- **Physics**: Two modes:
  - `none` (default): metadata only, no physics objects. Clean import.
  - `rigid_body`: RBW disabled during build, collision layers (shared layer 0 only), non-collision constraint (NCC) empties for excluded pairs with proximity-based filtering, all joints `disable_collisions=True`, margin 1e-6, dynamic body repositioning, depsgraph flushes. Build restructured into 3 phases (CREATE → POSITION → COUPLE & ACTIVATE). Blender's `collision_collections` is symmetric (shared layer = collide) while PMX masks are asymmetric (bilateral check: `A.group & B.mask && B.group & A.mask`), so NCC empties are required for correct exclusion. Own group layer was removed — it caused same-group bodies (e.g. hair chain) to collide with each other incorrectly.
  - **NCC mode** (stored on armature as `mmd_ncc_mode`): 3-way enum — `draft` (no NCCs, fast preview), `proximity` (distance-filtered, default), `all` (every excluded pair). Proximity uses `mmd_ncc_proximity` (FloatProperty 0.1–5.0, default 1.5) matching mmd_tools' `non_collision_distance_scale`. Higher = wider radius = more NCCs.
  - **Per-chain controls**: Each chain has independent collision toggle (eye icon — sets `collision_collections=[False]*20`) and physics toggle (physics icon — sets `kinematic=True`). Both are instant (no rebuild). Settings stored as `mmd_chain_collision_disabled`, `mmd_chain_physics_disabled` JSON on armature — preserved across clear/rebuild.
  - **NCC rebuild**: `rebuild_ncc()` reuses existing NCC empties by reassigning pairs, only creating/deleting the difference. Respects proximity setting and disabled chains. ~0.1s for 13K empties.
  - **Debug inspector**: Select a rigid body → Inspect (copies full diagnostic report to clipboard), Select Colliders (highlights collision-eligible bodies), Select Contacts (highlights bodies in contact at current frame using shape-aware distance check).
  - **Auto-reset**: VMD import automatically resets physics if rigid bodies exist.
- **MMD4B panel**: N-panel (tab "MMD4B") with sub-panels: Physics (NCC mode dropdown [Draft/Proximity/All] + proximity slider, Build/Reset/Rebuild NCCs/Remove, selected RB info with Inspect/Colliders/Contacts, per-chain list with collision/physics toggles), IK Toggle (per-chain toggles + All On/Off), Animation (action name, Clear Animation).
- **Materials**: Two shader modes controlled by "Toon & Sphere Textures" checkbox on PMX import (off by default):
  - **Basic** (default): Bare Principled BSDF named "MMD Shader" — no node group. PMX specular mapped to native BSDF: `specular` luminance → `Specular IOR Level` (0–0.5), `specular` color → `Specular Tint`, `shininess` → `Roughness`. Models respond to scene lighting and reflections out of the box.
  - **Full** (checkbox on): "MMD Shader" node group with toon/sphere inputs via `ShaderNodeMix`. Adds "MMD UV" group for toon/sphere UVs. Bundled toon textures (toon01-10.bmp) with fallback resolution. Specular IOR Level = 0.0 (toon textures provide specular control).
  - Both modes: Global `mmd_emission` driver on armature (`Emission Strength` for basic, `Emission` group input for full). Full mode also drives `mmd_toon_fac`/`mmd_sphere_fac`. Drivers are created after import (deferred `setup_drivers()`). Requires `use_scripts_auto_execute = True`. Alpha = PMX alpha × texture alpha. Edge color/size stored as material custom properties. Node lookup key: `"MMD Shader"` (both modes).
- **Split by material**: Mesh is split into per-material objects after import (default on). Enables per-object modifiers (cloth, solidify for outlines), light linking, and per-object `visible_shadow` (honors `mmd_drop_shadow`). Custom normals backed up as `mmd_normal` attribute before split, restored after. `mmd_morph_map` moved to armature for VMD import. All objects organized into a collection named after the model. VMD morph action shared across all split meshes via Blender 5.0 slotted actions: `fcurve_ensure_for_datablock` auto-creates a slot on the primary mesh's ShapeKey, secondary meshes share that same slot via `animation_data.action_slot`. Shape key names preserved by `mesh.separate`.
- **VMD append mode**: `import_vmd()` defaults to `create_new_action=False` — reuses existing bone/morph actions and appends keyframes. Allows layering motions (e.g., body dance + lip sync from separate VMDs). `create_new_action=True` replaces existing actions. Morph-only VMDs (no bone keyframes) skip bone action creation entirely, preserving existing bone animation. File browser exposes "Create New Action" checkbox.
- **Armature visibility**: STICK display, bones hidden by default. All three bone collections start hidden (`is_visible = False`): "Armature" (standard), "Physics" (dynamic RB bones, orange), "mmd_shadow" (helper bones). Unhide from armature properties when needed.
- **PMD support**: PMD parser (`pmd/parser.py`) outputs the same `pmx.types.Model` dataclasses — entire downstream pipeline unchanged. Auto-detected by `.pmd` extension in `import_pmx()`. PMD bone types (0-9) mapped to PMX flags. Morph base→absolute index remapping. Rigid body bone-relative→absolute position conversion. English extension parsed for bone/morph names.
- **VMD bone name auto-mapping**: `_build_bone_lookup()` in `vmd/importer.py` includes NFKC normalization (half-width↔full-width katakana) and alias table (PMD/PMX era naming differences like `人指`↔`人差指`) for cross-era VMD compatibility.
- **No export**: One-way import only. No PMX/VMD/PMD export.
- **Logging**: Use blender-agent's log (`output/agent.log`). Python `logging` to stderr for diagnostics.

## API usage (inside Blender via blender-agent)

```python
# PMX/PMD import (basic shader, no toon/sphere, split by material)
import bl_ext.user_default.blender_mmd.importer as importer
arm = importer.import_pmx("/path/to/model.pmx")  # or .pmd — auto-detected

# PMX import with toon & sphere textures
arm = importer.import_pmx("/path/to/model.pmx", use_toon_sphere=True)

# PMX import as single mesh (no split)
arm = importer.import_pmx("/path/to/model.pmx", split_by_material=False)

# VMD import — TWO steps: parse first, then apply to armature
import bl_ext.user_default.blender_mmd.vmd.parser as vmd_parser
import bl_ext.user_default.blender_mmd.vmd.importer as vmd_importer
motion = vmd_parser.parse("/path/to/motion.vmd")
vmd_importer.import_vmd(motion, arm)

# Layer a second VMD (e.g. lip sync) — appends to existing actions by default
lip = vmd_parser.parse("/path/to/lip.vmd")
vmd_importer.import_vmd(lip, arm)  # create_new_action=False (default)

# Replace all actions instead of appending
vmd_importer.import_vmd(motion, arm, create_new_action=True)
```

## Testing

```bash
# Run unit tests (no Blender needed)
pytest -v
```

Run `pytest` after significant refactorings to catch regressions. Tests use bundled sample PMX files in `tests/samples/`.

For Blender integration testing, use blender-agent manually.
