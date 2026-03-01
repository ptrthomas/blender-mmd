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
| Materials | Custom ~20-node shader group per material | Bare Principled BSDF (default) — native Blender shader, responds to scene lighting and reflections. Optional toon/sphere mode for full MMD look |
| Hair/skirt physics | Rigid body only | Rigid body (cloth-on-cage planned) |
| Collision filtering | Proximity-filtered — drops distant pairs that may collide during animation | Exact bilateral mask check — all excluded pairs enforced, no false collisions |
| Physics workflow | Must build from rest pose, complex UI | Build/reset/clear anytime, auto-resets after VMD import |
| Physics debugging | None | Inspect (clipboard report), Select Colliders, Select Contacts |
| Physics springs | Applied via property update callbacks | Applied directly during joint creation |
| Split by material | "HIGH RISK & BUGGY" (their words), VMD import doesn't handle split meshes | Fully supported — slotted action shared across all meshes, morph animation works on every piece |
| VMD layering | Single import only — each VMD replaces previous | Append mode (default) — layer body + lip sync + camera from separate VMDs |
| UI | Sidebar panels, menus, property groups | Minimal — designed for Claude Code + MMD4B panel |

Both projects share the same core approach for IK constraints (first link bone placement), IK limits (native properties + LIMIT_ROTATION override), and additional transforms (TRANSFORM constraints + shadow bones).

## What's implemented

- **PMX import** — full PMX 2.0/2.1 parser, armature, mesh, vertex weights, normals, UVs
- **Materials** — Default mode uses a bare Principled BSDF (no node group) with PMX specular/shininess mapped to native properties — models respond to scene lighting, reflections, and environment out of the box, without toon/sphere textures. Optional "Toon & Sphere" mode adds the full MMD look via a ~7-node group (vs ~20 in mmd_tools). Bundled shared toon files (toon01–10.bmp). Global controls via armature drivers, per-material override by removing driver
- **VMD motion** — bone keyframes, morph keyframes, IK toggle, bezier interpolation. Append mode layers multiple VMDs (body + lip sync) without replacing existing animation
- **Morphs** — vertex, UV, bone, material, group morphs as Blender shape keys
- **Rigid body physics** — correct PMX collision group/mask enforcement via bilateral check (both masks must agree), all joints `disable_collisions=True`, 3-phase build pipeline, auto-reset after VMD import. Debug tools: Inspect (copies full diagnostic to clipboard), Select Colliders (highlights eligible collision partners), Select Contacts (highlights bodies in contact at current frame). MMD4B panel for build/reset/clear with per-chain management
- **Additional transforms** — grant parent system (D bones, shoulder cancel, arm twist, eye tracking)
- **IK** — correct constraint placement, native limits, per-bone angle conversion

## Physics: correct collision filtering

MMD uses 16 collision groups with a bilateral mask: bodies A and B collide only if A's mask includes B's group AND B's mask includes A's group. Blender's `collision_collections` is symmetric — it uses the same bitmask for both group and mask — so PMX masks cannot be mapped to Blender layers directly.

mmd_tools works around this with a proximity filter that skips non-collision constraints for distant body pairs. This drops pairs that are far apart at rest but may collide during animation (e.g., twin tails swinging into the body). The result is occasional false collisions during dynamic motion.

Blender MMD enforces **every** excluded pair via `GENERIC` constraints with `disable_collisions=True` — no proximity filter, no dropped pairs. Additionally, all joint-connected body pairs get `disable_collisions=True` (connected bodies should never collide; the joint manages their relationship). This produces higher-quality simulation where hair, skirt, and accessories interact correctly without fighting or clipping through each other.

The debug inspector helps diagnose physics issues: select any rigid body and use **Inspect** (copies a full diagnostic report to clipboard), **Select Colliders** (highlights all bodies that can collide with it based on PMX masks), or **Select Contacts** (highlights bodies actually in contact at the current frame).

## Customization via armature properties

After import, key settings live as custom properties on the armature object. Change a single value and all materials or IK constraints update via drivers:

| Property | Default | Effect |
|---|---|---|
| `mmd_emission` | 1.0 | Emission strength across all materials |
| `mmd_toon_fac` | 1.0 | Toon texture influence (0 = off, 1 = full) |
| `mmd_sphere_fac` | 1.0 | Sphere texture influence (0 = off, 1 = full) |
| `ik_loop_factor` | 1 | Multiplier for IK solver iterations (increase for better foot plant accuracy) |

**Note:** Drivers require **Preferences > Save & Load > Auto Run Python Scripts** to be enabled. Per-material override: remove the driver on that material's shader group input and set it manually in the Shader Editor.

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

# Layer a lip sync VMD on top (appends by default, preserves bone animation)
lip = vmd_parser.parse("/path/to/lip.vmd")
vmd_importer.import_vmd(lip, arm)
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
