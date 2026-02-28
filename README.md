# Blender MMD

A ground-up rewrite of [mmd_tools](https://github.com/MMD-Blender/blender_mmd_tools) for **Blender 5.0+**, built entirely with [Claude Code](https://claude.ai/claude-code).

Imports PMX models and VMD motions into Blender with correct armature, mesh, materials, physics, and animation.

## Why rewrite mmd_tools?

mmd_tools is a battle-tested addon that has served the MMD-Blender community for years. This project rewrites it from scratch to fix fundamental issues and target modern Blender:

**Physics that actually works.** mmd_tools stores PMX spring stiffness values in custom properties but never applies them to Blender's joint constraints. This is the main reason rigid bodies fly apart. We actually set the spring values. We also offer a cloth-on-cage approach (MMD4B panel) for hair and skirt physics that produces more natural movement than rigid bodies alone.

**No O(n^2) collision objects.** mmd_tools creates an empty object with `disable_collisions=True` for every non-colliding rigid body pair. A model with 100 rigid bodies can generate 200+ extra objects. We use Blender's native `collision_collections` API instead.

**Blender 5.0+ only.** No legacy code paths, no backwards compatibility shims. Uses current APIs throughout: extension manifest, `collision_collections`, modern normals API, slot-based action system.

**Clean, documented codebase.** The full architecture is documented in [`docs/SPEC.md`](docs/SPEC.md). The PMX and VMD parsers are standalone reference implementations with Python type hints, useful beyond Blender (e.g., for a future three.js port).

## Key differences from mmd_tools

| | mmd_tools | Blender MMD |
|---|---|---|
| Blender version | 2.83–4.x | 5.0+ only |
| Object hierarchy | Root Empty > Armature > Mesh | Armature > Mesh (no root empty) |
| Bone names | Japanese (with .L/.R) | English (Japanese stored as custom property) |
| IK constraints | On end effector (ankle) | On first link (knee) — correct for Blender's solver |
| IK limits | LIMIT_ROTATION constraints | Native PoseBone IK properties |
| IK toggle (VMD) | Custom property + update callback | Constraint influence keyframes (CONSTANT interpolation) |
| Physics springs | Stored but never applied | Applied to GENERIC_SPRING constraints |
| Non-collision pairs | O(n^2) empty objects | `collision_collections` API |
| Coordinate conversion | Scattered throughout | Done once in parser — downstream is pure Blender coords |
| Hair/skirt physics | Rigid body only | Rigid body + cloth-on-cage with Surface Deform |
| UI | Sidebar panels, menus | Minimal — designed to be driven by Claude Code |
| Additional transforms | COPY_ROTATION | TRANSFORM constraints + shadow bones (handles negative factors, euler discontinuities) |

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
