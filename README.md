# Blender MMD

A ground-up rewrite of [mmd_tools](https://github.com/MMD-Blender/blender_mmd_tools) for **Blender 5.0+**, built entirely with [Claude Code](https://claude.ai/claude-code).

Imports PMX models and VMD motions into Blender with correct armature, mesh, materials, physics, and animation.

## Why rewrite mmd_tools?

mmd_tools is a battle-tested addon that has served the MMD-Blender community for years. This project rewrites it from scratch for modern Blender with a cleaner architecture:

**Blender 5.0+ only.** No legacy code paths, no backwards compatibility shims. Uses current APIs throughout: extension manifest, `collision_collections`, modern normals API, slot-based action system.

**Coordinate conversion in the parser.** mmd_tools scatters `.xzy` swizzles throughout the importer, bone utilities, and physics code. We convert once in the parser — all downstream code works in pure Blender coordinates. This eliminates an entire class of coordinate bugs.

**Clean, documented codebase.** The full architecture is documented in [`docs/SPEC.md`](docs/SPEC.md) — every design decision, coordinate system detail, and known limitation. The PMX and VMD parsers are standalone reference implementations with Python type hints and dataclasses, useful beyond Blender (e.g., for a future three.js port).

**Designed for AI-assisted development.** Minimal UI — import operators plus an MMD4B physics panel for build/clear. The addon is designed to be driven by Claude Code via [blender-agent](https://github.com/ptrthomas/blender-agent).

## Key differences from mmd_tools

| | mmd_tools | Blender MMD |
|---|---|---|
| Blender version | 2.83–4.x | 5.0+ only |
| Codebase | ~15k LOC, accumulated over years | ~11k LOC, clean rewrite with full spec |
| Object hierarchy | Root Empty > Armature > Mesh | Armature > Mesh (no root empty) |
| Bone names | Japanese by default (optional translation) | English by default (Japanese stored as `mmd_name_j`) |
| Coordinate conversion | `.xzy` swizzles scattered across importer, bone, physics code | Done once in parser — downstream is pure Blender coords |
| IK toggle (VMD) | Custom `mmd_ik_toggle` property + update callback | Constraint influence keyframes (more Blender-native) |
| Materials | Custom ~20-node shader group per material | Single Principled BSDF group (~7 nodes), global driver controls |
| Hair/skirt physics | Rigid body only | Rigid body (cloth-on-cage planned) |
| Physics workflow | Must build from rest pose, complex UI | Build/rebuild/clear anytime, one-click MMD4B panel |
| Physics springs | Applied via property update callbacks | Applied directly during joint creation |
| UI | Sidebar panels, menus, property groups | Minimal — designed for Claude Code + MMD4B panel |

Both projects share the same core approach for IK constraints (first link bone placement), IK limits (native properties + LIMIT_ROTATION override), and additional transforms (TRANSFORM constraints + shadow bones).

## What's implemented

- **PMX import** — full PMX 2.0/2.1 parser, armature, mesh, vertex weights, normals, UVs
- **Materials** — Principled BSDF-based "MMD Shader" node group with toon/sphere texture support. ~7 internal nodes (vs ~20 in mmd_tools) while preserving the MMD look. Bundled shared toon files (toon01–10.bmp). Global controls via armature custom properties and drivers
- **VMD motion** — bone keyframes, morph keyframes, IK toggle, bezier interpolation
- **Morphs** — vertex, UV, bone, material, group morphs as Blender shape keys
- **Rigid body physics** — build/rebuild/clear via MMD4B panel, even after loading animation. Rebuild after VMD import to sync physics to starting pose
- **Additional transforms** — grant parent system (D bones, shoulder cancel, arm twist, eye tracking)
- **IK** — correct constraint placement, native limits, per-bone angle conversion

## Customization via armature properties

After import, key settings live as custom properties on the armature object. Change a single value and all materials or IK constraints update via drivers:

| Property | Default | Effect |
|---|---|---|
| `mmd_emission` | 0.3 | Emission strength across all materials |
| `mmd_toon_fac` | 1.0 | Toon texture influence (0 = off, 1 = full) |
| `mmd_sphere_fac` | 1.0 | Sphere texture influence (0 = off, 1 = full) |
| `ik_loop_factor` | 1 | Multiplier for IK solver iterations (increase for better foot plant accuracy) |

Per-material override: remove the driver on that material's shader group input and set it manually in the Shader Editor.

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
arm = importer.import_pmx("/path/to/model.pmx")

# VMD import
import bl_ext.user_default.blender_mmd.vmd.parser as vmd_parser
import bl_ext.user_default.blender_mmd.vmd.importer as vmd_importer
motion = vmd_parser.parse("/path/to/motion.vmd")
vmd_importer.import_vmd(motion, arm)
```

Or use the Blender operator: **File > Import > MMD PMX (.pmx)**

## Contributing

The project is designed for development with Claude Code. The full specification in [`docs/SPEC.md`](docs/SPEC.md) covers architecture, decisions, coordinate systems, and remaining work. Areas where contributions are welcome:

- Soft body / cloth improvements (cage-based hair/skirt deformation)
- VMD camera motion import
- CCD IK solver
- Performance optimizations (UV foreach_set, degenerate face cleanup)
- SDEF spherical deformation

## License

GPL-3.0-or-later
