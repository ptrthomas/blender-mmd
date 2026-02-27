# Blender MMD Importer

Read `docs/SPEC.md` first. It is the single source of truth for architecture, decisions, and milestones.

## Quick orientation

- **This project**: PMX/VMD importer addon for Blender 5.0+, driven by Claude Code
- **Milestone 1** (done): PMX parser + armature + mesh import
- **Milestone 2** (done): Morphs / shape keys
- **Milestone 3** (done): VMD motion import (bone keyframes, morph keyframes, bone roll)
- **Milestone 3.5** (done): IK fix — correct constraint placement, native limits, VMD IK toggle
- **Milestone 4** (done): Rigid body physics — functional but limited by Blender's RB solver
- **Milestone 4b** (next, PRIMARY): Convert rigid body chains → Blender cloth simulation
- **Milestone 5**: Materials & textures
- **Primary motivation**: Replace mmd_tools' broken rigid body physics with Blender-native cloth sim

## Reference repos (siblings in ../  )

- `../blender_mmd_tools/` — Original mmd_tools addon. Use `mmd_tools/core/pmx/__init__.py` as parser reference. Do NOT fork — clean rewrite.
- `../blender_mmd_tools_append/` — UuuNyaa's cloth physics converter. Key reference for M4b: converts MMD rigid body chains to Blender cloth sim. See `mmd_tools_append/converters/physics/rigid_body_to_cloth.py`.
- `../blender-agent/` — HTTP bridge for controlling Blender. Read its `CLAUDE.md` for usage. blender-mmd has no import dependency on it.

## Development

```bash
# Symlink addon into Blender extensions
scripts/setup.sh

# Restart Blender to reload after code changes
# Use blender-agent (port 5656) for execution, screenshots, and log monitoring
```

## Cross-project contributions

When working on blender-mmd and encountering opportunities to improve blender-agent (e.g. Blender 5.0 API hints, common 3D workflow helpers, better error messages), note them and contribute upstream to `../blender-agent/`.

## Key decisions

- **Bone names**: English in Blender, Japanese stored as `mmd_name_j` custom property (for VMD matching)
- **Coordinate conversion**: Done in parser. Downstream code uses Blender coords only.
- **IK constraints**: Placed on first link bone (e.g. knee), NOT the end effector (ankle). Uses Blender-native `ik_min_x/max_x` properties instead of `LIMIT_ROTATION` constraints. `ik_loop_factor` param (default 1) multiplies PMX iteration count for better convergence.
- **IK toggle**: VMD property section parsed and applied as IK constraint `influence` keyframes (0.0/1.0 with CONSTANT interpolation). More Blender-native than mmd_tools' custom property + callback approach.
- **Scene settings**: VMD import sets FPS to 30 (MMD standard) and extends frame range to fit animation.
- **Physics (M4 rigid body)**: Functional but limited. RB world disabled during build (mmd_tools pattern). Collision: shared layer 0 + own group + non-collision constraints. Margin 1e-6. Soft constraints enabled (lower>upper for locked DOFs). Dynamic body repositioning to match bone pose. Depsgraph flushes at key points. This is an intermediate solution — M4b (cloth conversion) is the real fix.
- **Physics (M4b cloth — planned)**: One-time conversion of PMX rigid body chains → Blender cloth simulation. Reference: [blender_mmd_tools_append](https://github.com/MMD-Blender/blender_mmd_tools_append). Skip SPRING values entirely, use Blender cloth presets. Chain detection → mesh generation → pin groups → cloth sim → surface deform → bone binding.
- **No export**: One-way import only. No PMX/VMD/PMD export.
- **No UI panels**: Claude Code is the interface.
- **Logging**: Use blender-agent's session log. Python `logging` to stderr for diagnostics.

## Testing

```bash
# Run unit tests (no Blender needed)
pytest -v
```

Run `pytest` after significant refactorings to catch regressions. Tests use bundled sample PMX files in `tests/samples/`.

For Blender integration testing, use blender-agent manually.
