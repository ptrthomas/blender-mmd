#!/usr/bin/env python3
"""Scan PMX files to extract Japanese → English bone name pairs.

Usage:
    python scripts/scan_translations.py /path/to/pmx/directory

Outputs a Python dict literal to stdout, suitable for pasting into translations.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add parent to path so we can import the parser
sys.path.insert(0, str(Path(__file__).parent.parent))

from blender_mmd.pmx.parser import parse


def scan_directory(directory: Path) -> dict[str, str]:
    """Scan all PMX files and collect (name_j, name_e) pairs."""
    translations: dict[str, str] = {}

    for pmx_path in sorted(directory.rglob("*.pmx")):
        try:
            model = parse(pmx_path)
        except Exception as e:
            print(f"  SKIP {pmx_path.name}: {e}", file=sys.stderr)
            continue

        for bone in model.bones:
            if bone.name and bone.name_e and bone.name_e.strip():
                name_j = bone.name
                name_e = bone.name_e.strip()
                if name_j not in translations:
                    translations[name_j] = name_e

        print(f"  Scanned {pmx_path.name}: {len(model.bones)} bones", file=sys.stderr)

    return translations


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <directory>", file=sys.stderr)
        sys.exit(1)

    directory = Path(sys.argv[1])
    if not directory.is_dir():
        print(f"Not a directory: {directory}", file=sys.stderr)
        sys.exit(1)

    translations = scan_directory(directory)

    # Output as Python dict
    print("BONE_NAMES = {")
    for name_j in sorted(translations.keys()):
        name_e = translations[name_j]
        print(f'    "{name_j}": "{name_e}",')
    print("}")

    print(f"\n# Total: {len(translations)} translations", file=sys.stderr)


if __name__ == "__main__":
    main()
