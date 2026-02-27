#!/usr/bin/env python3
"""Compare blender_mmd parser output against mmd_tools for correctness.

Usage:
    python scripts/compare_parsers.py [pmx_file ...]

If no files given, parses both samples from ../blender_mmd_tools/samples/pmx/.
Requires mmd_tools at ../blender_mmd_tools (relative to project root).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# Setup paths
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from blender_mmd.pmx.parser import parse as blender_mmd_parse


def load_mmd_tools_parser():
    """Direct-import mmd_tools PMX parser, bypassing its __init__.py."""
    mmd_path = PROJECT_ROOT.parent / "blender_mmd_tools" / "mmd_tools" / "core" / "pmx" / "__init__.py"
    if not mmd_path.exists():
        print(f"mmd_tools not found at {mmd_path}", file=sys.stderr)
        sys.exit(1)
    spec = importlib.util.spec_from_file_location(
        "mmd_tools.core.pmx", str(mmd_path), submodule_search_locations=[]
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.load


def compare(filepath: Path, mmd_load):
    print(f"\n{'='*60}")
    print(f" {filepath.name}")
    print(f"{'='*60}")

    mmd = mmd_load(str(filepath))
    ours = blender_mmd_parse(filepath)

    # Counts
    checks = [
        ("Vertices", len(mmd.vertices), len(ours.vertices)),
        ("Faces", len(mmd.faces), len(ours.faces)),
        ("Bones", len(mmd.bones), len(ours.bones)),
        ("Materials", len(mmd.materials), len(ours.materials)),
        ("Morphs", len(mmd.morphs), len(ours.morphs)),
        ("Rigid bodies", len(mmd.rigids), len(ours.rigid_bodies)),
        ("Joints", len(mmd.joints), len(ours.joints)),
        ("Textures", len(mmd.textures), len(ours.textures)),
    ]

    print("\n  COUNTS:")
    for label, m, o in checks:
        status = "OK" if m == o else "MISMATCH"
        print(f"    {label:<14} mmd={m:>6}  ours={o:>6}  {status}")

    # Vertex positions (coord conversion check)
    print(f"\n  VERTEX POSITIONS (first 5, verifying coord conversion):")
    for i in range(min(5, len(mmd.vertices))):
        mx, my, mz = mmd.vertices[i].co
        ox, oy, oz = ours.vertices[i].position
        ok = abs(ox - mx) < 0.0001 and abs(oy - mz) < 0.0001 and abs(oz + my) < 0.0001
        print(f"    [{i}] mmd=({mx:>8.4f}, {my:>8.4f}, {mz:>8.4f}) "
              f"-> ours=({ox:>8.4f}, {oy:>8.4f}, {oz:>8.4f})  {'OK' if ok else 'FAIL'}")

    # Bone names
    print(f"\n  BONE NAMES (first 10):")
    for i in range(min(10, len(mmd.bones))):
        mb, ob = mmd.bones[i], ours.bones[i]
        match = mb.name == ob.name and mb.name_e == ob.name_e
        print(f"    [{i:>3}] j='{mb.name}' e='{mb.name_e}'  {'OK' if match else 'DIFF'}")

    # Bone parents
    mismatches = sum(
        1 for i in range(len(mmd.bones))
        if (mmd.bones[i].parent if mmd.bones[i].parent is not None else -1) != ours.bones[i].parent
    )
    print(f"\n  BONE PARENTS: {mismatches} mismatches / {len(mmd.bones)}")

    # Faces
    print(f"\n  FACES (first 5):")
    for i in range(min(5, len(mmd.faces))):
        print(f"    [{i}] mmd={mmd.faces[i]}  ours={ours.faces[i]}  "
              f"{'OK' if mmd.faces[i] == ours.faces[i] else 'DIFF'}")

    # Rigid bodies
    print(f"\n  RIGID BODIES (first 3):")
    for i in range(min(3, len(mmd.rigids))):
        mr, orr = mmd.rigids[i], ours.rigid_bodies[i]
        mr_bone = mr.bone if mr.bone is not None else -1
        print(f"    [{i}] name={'OK' if mr.name == orr.name else 'DIFF'} "
              f"bone={'OK' if mr_bone == orr.bone_index else 'DIFF'} "
              f"shape={'OK' if mr.type == orr.shape.value else 'DIFF'} "
              f"mass={'OK' if abs(mr.mass - orr.mass) < 0.0001 else 'DIFF'}")


def main():
    mmd_load = load_mmd_tools_parser()

    if len(sys.argv) > 1:
        files = [Path(f) for f in sys.argv[1:]]
    else:
        samples = PROJECT_ROOT.parent / "blender_mmd_tools" / "samples" / "pmx"
        files = sorted(samples.glob("*.pmx"))
        if not files:
            print(f"No PMX files found in {samples}", file=sys.stderr)
            sys.exit(1)

    for f in files:
        compare(f, mmd_load)

    print("\nDone.")


if __name__ == "__main__":
    main()
