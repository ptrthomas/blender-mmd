# Blender MMD

A ground-up rewrite of [mmd_tools](https://github.com/MMD-Blender/blender_mmd_tools) for **Blender 5.0+**, built entirely with [Claude Code](https://claude.ai/claude-code).

Imports PMX models and VMD motions into Blender with correct armature, mesh, materials, physics, and animation.

## Why rewrite mmd_tools?

mmd_tools is a battle-tested addon that has served the MMD-Blender community for years. This project rewrites it from scratch for modern Blender with a cleaner architecture:

**Blender 5.0+ only.** No legacy code paths, no backwards compatibility shims. Uses current APIs throughout: extension manifest, `collision_collections`, modern normals API, slot-based action system.

**Coordinate conversion in the parser.** mmd_tools scatters `.xzy` swizzles throughout the importer, bone utilities, and physics code. We convert once in the parser — all downstream code works in pure Blender coordinates. This eliminates an entire class of coordinate bugs.

**Clean, documented codebase.** The full architecture is documented in [`docs/SPEC.md`](docs/SPEC.md) — every design decision, coordinate system detail, and known limitation. The PMX and VMD parsers are standalone reference implementations with Python type hints and dataclasses, useful beyond Blender (e.g., for a future three.js port).

**Cloth-on-cage physics.** Beyond standard rigid body physics, we offer a cloth simulation approach for hair and skirts: select bones, generate a cage tube, and let Cloth + Surface Deform handle the deformation. This produces more natural movement than rigid bodies alone.

**Designed for AI-assisted development.** No traditional UI panels beyond the import operator and MMD4B cloth panel. The addon is designed to be driven by Claude Code via [blender-agent](https://github.com/ptrthomas/blender-agent).

## Key differences from mmd_tools

| | mmd_tools | Blender MMD |
|---|---|---|
| Blender version | 2.83–4.x | 5.0+ only |
| Codebase | ~15k LOC, accumulated over years | ~11k LOC, clean rewrite with full spec |
| Object hierarchy | Root Empty > Armature > Mesh | Armature > Mesh (no root empty) |
| Bone names | Japanese by default (optional translation) | English by default (Japanese stored as `mmd_name_j`) |
| Coordinate conversion | `.xzy` swizzles scattered across importer, bone, physics code | Done once in parser — downstream is pure Blender coords |
| IK toggle (VMD) | Custom `mmd_ik_toggle` property + update callback | Constraint influence keyframes (more Blender-native) |
| Hair/skirt physics | Rigid body only | Rigid body + cloth-on-cage with Surface Deform |
| Physics springs | Applied via property update callbacks | Applied directly during joint creation |
| UI | Sidebar panels, menus, property groups | Minimal — designed for Claude Code |

Both projects share the same core approach for IK constraints (first link bone placement), IK limits (native properties + LIMIT_ROTATION override), and additional transforms (TRANSFORM constraints + shadow bones).

## What's implemented

- **PMX import** — full PMX 2.0/2.1 parser, armature, mesh, vertex weights, normals, UVs
- **Materials** — two shader modes: `mmd` (full toon/sphere pipeline) and `simple` (clean diffuse+emission)
- **VMD motion** — bone keyframes, morph keyframes, IK toggle, bezier interpolation
- **Morphs** — vertex, UV, bone, material, group morphs as Blender shape keys
- **Rigid body physics** — three modes: `none`, `rigid_body`, `cloth`
- **Cloth physics** — MMD4B panel: select bones, generate cage tube, cloth sim + Surface Deform
- **Additional transforms** — grant parent system (D bones, shoulder cancel, arm twist, eye tracking)
- **IK** — correct constraint placement, native limits, per-bone angle conversion

## Requirements

- Blender 5.0+
- Python 3.11+

## Install

```bash
# Symlink into Blender's extensions directory
scripts/setup.sh
```

## Usage

```python
# PMX import
import bl_ext.user_default.blender_mmd.importer as importer
arm = importer.import_pmx("/path/to/model.pmx", shader_mode="mmd")

# VMD import
import bl_ext.user_default.blender_mmd.vmd.parser as vmd_parser
import bl_ext.user_default.blender_mmd.vmd.importer as vmd_importer
motion = vmd_parser.parse("/path/to/motion.vmd")
vmd_importer.import_vmd(motion, arm)
```

Or use the Blender operator: **File > Import > MMD PMX (.pmx)**

## Contributing

The project is designed for development with Claude Code. The full specification in [`docs/SPEC.md`](docs/SPEC.md) covers architecture, decisions, coordinate systems, and remaining work. Areas where contributions are welcome:

- VMD camera motion import
- CCD IK solver
- Performance optimizations (UV foreach_set, degenerate face cleanup)
- SDEF spherical deformation

## License

GPL-3.0-or-later
