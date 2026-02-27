# Blender MMD Importer

Read `docs/SPEC.md` first. It is the single source of truth for architecture, decisions, and milestones.

## Quick orientation

- **This project**: PMX/VMD importer addon for Blender 5.0+, driven by Claude Code
- **Milestone 1** (done): PMX parser + armature + mesh import
- **Milestone 2** (done): Morphs / shape keys
- **Milestone 3** (done): VMD motion import (bone keyframes, morph keyframes, bone roll)
- **Milestone 3.5** (done): IK fix — correct constraint placement, native limits, VMD IK toggle
- **Milestone 4** (done): Rigid body physics — functional but limited by Blender's RB solver
- **Milestone 4b** (done): Physics rework — three modes (none/rigid_body/cloth), chain detection, cloth conversion
- **MMD4B** (in progress): Cloth UI panel — manual bone selection, Phase 1 (single chain) done, Phase 2 (connected group) planned
- **Milestone 5** (next): Materials & textures

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
```

## Cross-project contributions

When working on blender-mmd and encountering opportunities to improve blender-agent (e.g. Blender 5.0 API hints, common 3D workflow helpers, better error messages), note them and contribute upstream to `../blender-agent/`.

## Key decisions

- **Bone names**: English in Blender, Japanese stored as `mmd_name_j` custom property (for VMD matching)
- **Coordinate conversion**: Done in parser. Downstream code uses Blender coords only.
- **IK constraints**: Placed on first link bone (e.g. knee), NOT the end effector (ankle). Uses Blender-native `ik_min_x/max_x` properties instead of `LIMIT_ROTATION` constraints. `ik_loop_factor` param (default 1) multiplies PMX iteration count for better convergence.
- **IK toggle**: VMD property section parsed and applied as IK constraint `influence` keyframes (0.0/1.0 with CONSTANT interpolation). More Blender-native than mmd_tools' custom property + callback approach.
- **Scene settings**: VMD import sets FPS to 30 (MMD standard) and extends frame range to fit animation.
- **Physics**: Three modes (see SPEC.md Milestone 4b for full plan):
  - `none` (default): metadata only, no physics objects. Clean import.
  - `rigid_body`: M4 implementation. RBW disabled during build, collision layers, non-collision constraints, margin 1e-6, dynamic body repositioning, depsgraph flushes. "Good enough" mmd_tools-quality.
  - `cloth`: Interactive chain-by-chain conversion to Blender cloth sim. Claude guides user through selecting chains and collision surfaces. Reference: `../blender_mmd_tools_append/`.
- **MMD4B panel**: N-panel (tab "MMD4B") for cloth conversion. Select bones in Pose Mode, pick preset + collision mesh, click Convert. Phase 2 uses spatial auto-connect + manual strut bones for skirt tube mesh (struts replace self-collision via compression stiffness).
- **No export**: One-way import only. No PMX/VMD/PMD export.
- **Logging**: Use blender-agent's session log. Python `logging` to stderr for diagnostics.

## Testing

```bash
# Run unit tests (no Blender needed)
pytest -v
```

Run `pytest` after significant refactorings to catch regressions. Tests use bundled sample PMX files in `tests/samples/`.

For Blender integration testing, use blender-agent manually.
