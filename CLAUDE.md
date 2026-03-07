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
- **Milestone 7** (in progress): Creative tools — edge/outline rendering done. Material morphs remaining.
- **Milestone 8** (in progress): NLA & animation workflow — FPS control done, NLA push-down done, asset marking done. See `docs/NLA.md` for design doc. Remaining: body-part splitting, asset library, remixing tools, rig retargeting.

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

## Debugging & performance profiling

blender-agent captures all stdout/stderr from executed code in `../blender-agent/output/agent.log`. This includes Python `logging` output (INFO/DEBUG/WARNING from the `blender_mmd` logger) and `print()` statements. When debugging performance or tracing issues:

1. Add `print()` or `log.info()` timing statements in the addon code
2. Restart Blender (module reloading is unreliable)
3. Run the operation via blender-agent
4. Read `../blender-agent/output/agent.log` with the Read tool or `grep` for the timing lines

**Example**: The `clear_physics` 36s bottleneck was found by adding `time.perf_counter()` prints between each step, then reading `agent.log` — revealed that `pb.constraints.remove()` triggered full physics re-solve per call because RBW was still enabled. Fix: disable RBW first → 0.5s.

**Key insight**: `log.info()` output may not appear in the curl JSON response (it goes to stderr), but it **always** appears in `agent.log`. When curl output is missing expected logs, check `agent.log`.

## Cross-project contributions

When working on blender-mmd and encountering opportunities to improve blender-agent (e.g. Blender 5.0 API hints, common 3D workflow helpers, better error messages), note them and contribute upstream to `../blender-agent/`.

## Key decisions

- **Name translation**: Unified `resolve_name()` in `translations.py` for all categories (bones, morphs, materials, rigid bodies, joints). Priority: full-name table → English name (if pure ASCII, no CJK/kana) → chunk-based translation → Japanese fallback. `translate_chunks()` does greedy longest-match against `NAME_CHUNKS` dict (~150 entries), handles 左/右→.L/.R, NFKC normalization. Covers Japanese and simplified Chinese model names. Japanese names stored as `mmd_name_j` custom properties for VMD matching.
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
- **MMD4B panel**: N-panel (tab "MMD4B"), all sub-panels collapsed by default. Sub-panels: Mesh (visible when mesh child selected — info, per-mesh outline toggle/color/thickness, physics chains with select, Delete Mesh), Outlines (global thickness slider, Build/Rebuild/Remove), SDEF (context-aware: per-mesh count when SDEF mesh selected, model-wide when armature selected), Physics (NCC mode dropdown [Draft/Proximity/All] + proximity slider, Build/Reset/Rebuild NCCs/Remove, selected RB info with Inspect/Colliders/Contacts, per-chain list with collision/physics toggles), IK Toggle (per-chain toggles + All On/Off), Animation (bone action name, morph action name, NLA track counts, Mark as Assets, Clear Animation).
- **Materials**: Two shader modes controlled by "Toon & Sphere Textures" checkbox on PMX import (off by default):
  - **Basic** (default): Bare Principled BSDF named "MMD Shader" — no node group. PMX specular mapped to native BSDF: `specular` luminance → `Specular IOR Level` (0–0.5), `specular` color → `Specular Tint`, `shininess` → `Roughness`. Models respond to scene lighting and reflections out of the box.
  - **Full** (checkbox on): "MMD Shader" node group with toon/sphere inputs via `ShaderNodeMix`. Adds "MMD UV" group for toon/sphere UVs. Bundled toon textures (toon01-10.bmp) with fallback resolution. Specular IOR Level = 0.0 (toon textures provide specular control).
  - Both modes: Global `mmd_emission` on armature (`Emission Strength` for basic, `Emission` group input for full). Full mode also uses `mmd_toon_fac`/`mmd_sphere_fac`. Applied via `update_materials()` (no per-material drivers — keeps material nodetrees free of `animation_data` for clean NLA). Alpha = PMX alpha × texture alpha. Edge color/size stored as material custom properties. Node lookup key: `"MMD Shader"` (both modes).
- **Outlines**: Inverted hull method via Solidify modifier + Emission BSDF edge material. Per-material `mmd_edge_enabled`, `mmd_edge_color` (RGBA), `mmd_edge_size` from PMX. Per-vertex `mmd_edge_scale` stored as locked vertex group during mesh import. Edge materials use BLENDED surface render method for smooth semi-transparency. Thickness: `edge_size × import_scale × 0.05 × global_mult × per_mesh_mult`. Global thickness slider in Outlines panel, per-mesh thickness via `mmd_edge_thickness_mult` (registered `FloatProperty` on `Object` with `update` callback for instant reactivity). Per-mesh outline toggle/color editing in Mesh panel. `toggle_mesh_outline()` adds/removes Solidify + edge material on a single mesh. `set_mesh_edge_color()` updates Emission node color instantly. Build/Rebuild/Remove via MMD4B Outlines panel or `outlines.build_outlines()`/`outlines.remove_outlines()`.
- **Per-material mesh build**: Each material gets its own mesh built directly from PMX data (no `bpy.ops.mesh.separate`). Eliminates normals backup/restore. A hidden **control mesh** (`_mmd_morphs`) owns all shape keys as value holders — VMD morph animation targets only this mesh (single action, single NLA track). A `frame_change_post` handler copies control mesh shape key values to visible meshes each frame. Each visible mesh only gets shape keys for morphs affecting its vertices (sparse). `mmd_morph_map` stored on control mesh. All objects organized into a collection named after the model. Clean NLA editor: exactly 2 tracks (armature + `_mmd_morphs`). No `animation_data` on any visible mesh, shape key, or material nodetree.
- **Group morph flattening**: GROUP morphs (type 0) are flattened into composite vertex shape keys at import time. `_flatten_group_morph()` recursively walks the morph tree, accumulating weighted vertex deltas from VERTEX children. Non-vertex children (UV/BONE/MATERIAL) are skipped with a log count. Cycle detection via `visited` set. This enables VMD mouth animation (あ/い/う/え/お) on models like YYB Miku where these are GROUP morphs composing vertex morph children.
- **VMD append mode**: `import_vmd()` defaults to `create_new_action=False` — reuses existing bone/morph actions and appends keyframes. Allows layering motions (e.g., body dance + lip sync from separate VMDs). `create_new_action=True` replaces existing actions. Morph-only VMDs (no bone keyframes) skip bone action creation entirely, preserving existing bone animation. File browser exposes "Create New Action" checkbox.
- **VMD FPS control**: `import_vmd(target_fps=N)` scales all keyframe frame positions by `N/30` and sets scene to target FPS. Default 30 (no scaling). File browser exposes FPS dropdown (30/60/Custom). Bézier handles auto-scale since `_set_bezier_handles()` uses relative frame deltas. Note: changing FPS does NOT rescale existing animations — all VMDs in a project should use the same target FPS.
- **NLA workflow**: NLA push-down uses Blender's native NLA editor — no custom operator needed. With only 2 animation datablocks (armature for bones, `_mmd_morphs` for morphs), users can push down each track independently in the NLA editor for full control. The `frame_change_post` handler syncs control mesh shape key values to visible meshes — works identically whether driven by action or NLA strip. MMD4B Animation panel has "Mark as Assets" and "Clear Animation" buttons.
- **Armature visibility**: STICK display, bones hidden by default. All three bone collections start hidden (`is_visible = False`): "Armature" (standard), "Physics" (dynamic RB bones, orange), "mmd_shadow" (helper bones). Unhide from armature properties when needed.
- **PMD support**: PMD parser (`pmd/parser.py`) outputs the same `pmx.types.Model` dataclasses — entire downstream pipeline unchanged. Auto-detected by `.pmd` extension in `import_pmx()`. PMD bone types (0-9) mapped to PMX flags. Morph base→absolute index remapping. Rigid body bone-relative→absolute position conversion. English extension parsed for bone/morph names. WaistCancel bones that cancel LowerBody rotation are neutralized (PMD-era convention breaks modern VMDs that assume legs inherit LowerBody lean).
- **Knee pre-bend**: `_ensure_knee_prebend()` in `armature.py` nudges knee bone heads forward (-Y) when rest-pose geometry lacks a clear forward offset. Only touches bones named "ひざ" (knee). Some models (especially PMD) have tiny lateral offsets that dominate the forward component, making Blender's IK solver choose the wrong bend direction. Runs after roll computation to avoid affecting bone rolls.
- **VMD bone name auto-mapping**: `_build_bone_lookup()` in `vmd/importer.py` includes NFKC normalization (half-width↔full-width katakana) and alias table (PMD/PMX era naming differences like `人指`↔`人差指`) for cross-era VMD compatibility.
- **SDEF**: Spherical DEFormation — volume-preserving skinning via bake-to-MDD pipeline. SDEF C/R0/R1 stored as `mmd_sdef_c`/`mmd_sdef_r0`/`mmd_sdef_r1` float3 mesh attributes (scaled by `import_scale`). `mmd_sdef` vertex group marks SDEF vertices. `bake_sdef()` iterates frame range, computes NLERP quaternion blending per vertex, writes MDD files (LightWave PointCache2 format with timestamps). Mesh Cache modifier replaces Armature modifier on baked meshes. `toggle_sdef()` swaps modifier visibility for instant SDEF/LBS A/B comparison. `clear_sdef_bake()` removes Mesh Cache, restores Armature, deletes MDD files. Cache stored alongside .blend as `{blend_stem}_sdef/{armature_name}/{mesh_name}.mdd`. MMD4B SDEF sub-panel with Bake/Clear/Toggle/Select controls.
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

# VMD import at custom FPS (scale keyframes, set scene to target fps)
vmd_importer.import_vmd(motion, arm, target_fps=60)  # 60fps: frames doubled

# Layer a second VMD (e.g. lip sync) — appends to existing actions by default
lip = vmd_parser.parse("/path/to/lip.vmd")
vmd_importer.import_vmd(lip, arm)  # create_new_action=False (default)

# Replace all actions instead of appending
vmd_importer.import_vmd(motion, arm, create_new_action=True)

# NLA push-down: use Blender's native NLA editor to push tracks individually
# Only 2 datablocks have animation: armature (bones) + _mmd_morphs (morphs)

# Build edge outlines (after import, split meshes only)
from bl_ext.user_default.blender_mmd.outlines import build_outlines, remove_outlines
build_outlines(arm, thickness_mult=1.0)  # uses PMX edge_color/edge_size per material

# Rebuild with different thickness
remove_outlines(arm)
build_outlines(arm, thickness_mult=2.0)

# Bake SDEF deformation (requires .blend to be saved)
from bl_ext.user_default.blender_mmd.sdef import bake_sdef, clear_sdef_bake, toggle_sdef
result = bake_sdef(arm, frame_start=1, frame_end=300)  # writes MDD, applies Mesh Cache

# Toggle SDEF on/off for A/B comparison (instant, no recomputation)
toggle_sdef(arm)  # SDEF off (LBS)
toggle_sdef(arm)  # SDEF on

# Clear bake (restore Armature modifier, delete MDD files)
clear_sdef_bake(arm)
```

## Testing

```bash
# Run unit tests (no Blender needed)
pytest -v
```

Run `pytest` after significant refactorings to catch regressions. Tests use bundled sample PMX files in `tests/samples/`.

For Blender integration testing, use blender-agent manually.
