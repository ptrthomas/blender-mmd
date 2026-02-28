# Blender MMD Importer

Read `docs/SPEC.md` first. It is the single source of truth for architecture, decisions, and milestones.

## Quick orientation

- **This project**: PMX/VMD importer addon for Blender 5.0+, driven by Claude Code
- **Milestone 1** (done): PMX parser + armature + mesh import
- **Milestone 2** (done): Morphs / shape keys
- **Milestone 3** (done): VMD motion import (bone keyframes, morph keyframes, bone roll)
- **Milestone 3.5** (done): IK fix — correct constraint placement, native limits, VMD IK toggle
- **Milestone 4** (done): Rigid body physics — functional but limited by Blender's RB solver
- **Milestone 4b** (done): Cloth on cage tube + Surface Deform for hair/skirt/tie. Three physics modes coexist: none, rigid_body, soft_body.
- **Milestone 5** (done): Materials & textures — Principled BSDF-based "MMD Shader" node group, bundled toon textures with fallback, global controls via armature drivers (emission/toon/sphere), per-face assignment, UV V-flip, overlapping face fix.
- **Milestone 6** (in progress): Animation polish — additional transforms done (grant parent, shadow bones). Remaining: VMD camera, CCD IK
- **Milestone 7** (planned): Creative tools — edge/outline rendering, material morphs

## Reference repos (siblings in ../  )

- `../blender_mmd_tools/` — Original mmd_tools addon. Use `mmd_tools/core/pmx/__init__.py` as parser reference. Do NOT fork — clean rewrite.
- `../blender_mmd_tools_append/` — UuuNyaa's cloth physics converter. Key reference for M4b: converts MMD rigid body chains to Blender cloth sim. See `mmd_tools_append/converters/physics/rigid_body_to_cloth.py`.
- `../blender-agent/` — HTTP bridge for controlling Blender. Read its `CLAUDE.md` for usage. blender-mmd has no import dependency on it.

## Development

```bash
# Symlink addon into Blender extensions
scripts/setup.sh

# ALWAYS check if Blender is already running before launching:
curl -s localhost:5656 --data-binary @- <<< 'bpy.app.version_string'
# Only launch if the check above fails (connection refused):
/Applications/Blender.app/Contents/MacOS/Blender --python ../blender-agent/start_server.py &

# Use blender-agent (port 5656) for execution, screenshots, and log monitoring
# Module reloading is unreliable — restart Blender for code changes

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
bpy.ops.screen.screenshot(filepath="/tmp/blender_screenshot.png")
PYEOF
# Step 2: Read the saved PNG file with the Read tool
# The curl response is JSON, NOT an image. Never pipe/save it as a PNG.
```

## Cross-project contributions

When working on blender-mmd and encountering opportunities to improve blender-agent (e.g. Blender 5.0 API hints, common 3D workflow helpers, better error messages), note them and contribute upstream to `../blender-agent/`.

## Key decisions

- **Bone names**: English in Blender, Japanese stored as `mmd_name_j` custom property (for VMD matching)
- **Coordinate conversion**: Done in parser. Downstream code uses Blender coords only.
- **IK constraints**: Placed on first link bone (e.g. knee), NOT the end effector (ankle). Uses Blender-native `ik_min_x/max_x` properties instead of `LIMIT_ROTATION` constraints. `ik_loop_factor` param (default 1) multiplies PMX iteration count for better convergence.
- **IK toggle**: VMD property section parsed and applied as IK constraint `influence` keyframes (0.0/1.0 with CONSTANT interpolation). More Blender-native than mmd_tools' custom property + callback approach.
- **Scene settings**: VMD import sets FPS to 30 (MMD standard) and extends frame range to fit animation.
- **Physics**: Three modes, can coexist (rigid_body provides collision surfaces for soft_body cages):
  - `none` (default): metadata only, no physics objects. Clean import.
  - `rigid_body`: M4 implementation. RBW disabled during build, collision layers, non-collision constraints, margin 1e-6, dynamic body repositioning, depsgraph flushes. "Good enough" mmd_tools-quality.
  - `soft_body`: MMD4B panel. User selects bone chain, algorithm generates octagonal cage tube with gradient density (more rings near root), Cloth modifier on cage + Surface Deform on visible mesh. Gradient pinning `[1.0, 0.8, 0.5]` for smooth transition. Auto-integrates with rigid_body mode: static RBs get COLLISION modifiers (body collision), dynamic RBs on cage bones are removed (cloth replaces them). Rebuilding rigid body physics preserves existing cages.
- **MMD4B panel**: N-panel (tab "MMD4B") for soft body deformation. Select bones in Pose Mode → generate cage → play. Uses Cloth modifier (not Soft Body) because Cloth respects Armature modifier output for pinned vertices. Collision is auto-detected from static rigid bodies (no manual picker).
- **Materials**: Single "MMD Shader" node group (Principled BSDF-based) with toon/sphere inputs. Global controls via armature custom properties (`mmd_emission`, `mmd_toon_fac`, `mmd_sphere_fac`) driven to all materials. Bundled toon textures (toon01-10.bmp) with fallback resolution. Alpha = PMX alpha × texture alpha (matching mmd_tools). Edge color/size stored as material custom properties.
- **Armature visibility**: Hidden by default on import (`hide_set(True)`, STICK display). Unhide from outliner when needed. Bones split into three collections: "Armature" (standard), "Physics" (dynamic RB bones, orange), "mmd_shadow" (hidden helper bones).
- **No export**: One-way import only. No PMX/VMD/PMD export.
- **Logging**: Use blender-agent's session log. Python `logging` to stderr for diagnostics.

## API usage (inside Blender via blender-agent)

```python
# PMX import
import bl_ext.user_default.blender_mmd.importer as importer
arm = importer.import_pmx("/path/to/model.pmx")

# VMD import — TWO steps: parse first, then apply to armature
import bl_ext.user_default.blender_mmd.vmd.parser as vmd_parser
import bl_ext.user_default.blender_mmd.vmd.importer as vmd_importer
motion = vmd_parser.parse("/path/to/motion.vmd")
vmd_importer.import_vmd(motion, arm)
```

## Testing

```bash
# Run unit tests (no Blender needed)
pytest -v
```

Run `pytest` after significant refactorings to catch regressions. Tests use bundled sample PMX files in `tests/samples/`.

For Blender integration testing, use blender-agent manually.
