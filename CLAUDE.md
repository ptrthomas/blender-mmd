# Blender MMD Importer

Read `docs/SPEC.md` first. It is the single source of truth for architecture, decisions, and milestones.

## Quick orientation

- **This project**: PMX/PMD/VMD importer addon for Blender 5.0+, driven by Claude Code
- PMX/PMD parser, armature, per-material mesh build, vertex weights, normals, UVs
- Morphs (vertex + group flattening), control mesh architecture, clean 2-track NLA
- VMD motion (bone/morph keyframes, IK toggle, interpolation, append mode, FPS control, static channel filtering)
- Rigid body physics (3-phase build, NCC empties, debug inspector, per-chain management)
- Materials (bare Principled BSDF default, optional toon/sphere, no drivers)
- Additional transforms (grant parent, shadow bones), SDEF, edge outlines
- PMD support, cross-era VMD bone name mapping, chunk-based name translation
- See `docs/SPEC.md` "Open items" for remaining work

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

All architecture and design decisions are documented in `docs/SPEC.md`. Key points for quick reference:

- **Coordinate conversion** done in parser — downstream code uses Blender coords only
- **Per-material mesh build** — each material is its own mesh object, control mesh (`_mmd_morphs`) for morphs
- **No export** — one-way import only
- **No drivers** — material controls synced via `update_materials()` and registered property callbacks
- **Logging** — use blender-agent's log (`output/agent.log`). Python `logging` to stderr for diagnostics

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
build_outlines(arm)  # uses PMX edge_color/edge_size per material, armature mmd_edge_thickness

# Rebuild with different thickness
arm.mmd_edge_thickness = 2.0
remove_outlines(arm)
build_outlines(arm)

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
