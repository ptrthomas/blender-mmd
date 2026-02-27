"""Chain detection from rigid body / joint topology.

Pure Python — no Blender imports. Testable with pytest.

Algorithm:
1. Build directed adjacency graph from joints (src_rigid → dest_rigid)
2. Find STATIC rigid bodies with DYNAMIC/DYNAMIC_BONE neighbors (chain roots)
3. BFS from each root through dynamic bodies
4. Track visited bodies to prevent duplicates across chains
5. Name chains from root rigid body, classify by name pattern
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .pmx.types import Model

from .pmx.types import RigidMode


@dataclass
class Chain:
    """A physics chain: a STATIC root with connected DYNAMIC bodies."""
    name: str
    group: str  # "hair", "skirt", "accessory", "other"
    root_rigid_index: int
    root_bone_index: int
    rigid_indices: list[int] = field(default_factory=list)  # ordered root→tip
    bone_indices: list[int] = field(default_factory=list)
    joint_indices: list[int] = field(default_factory=list)


def detect_chains(model: Model) -> list[Chain]:
    """Detect physics chains from rigid body/joint topology.

    Returns chains ordered by root rigid body index.
    """
    rigid_bodies = model.rigid_bodies
    joints = model.joints

    # Build adjacency: for each rigid body, which other bodies are connected via joints?
    # Also track joint indices for each edge.
    neighbors: dict[int, list[tuple[int, int]]] = {}  # rb_index → [(neighbor_rb, joint_idx)]
    for j_idx, joint in enumerate(joints):
        src, dst = joint.src_rigid, joint.dest_rigid
        if src < 0 or dst < 0 or src >= len(rigid_bodies) or dst >= len(rigid_bodies):
            continue
        neighbors.setdefault(src, []).append((dst, j_idx))
        neighbors.setdefault(dst, []).append((src, j_idx))

    # Find chain roots: STATIC bodies connected to at least one DYNAMIC body
    roots = []
    for i, rb in enumerate(rigid_bodies):
        if rb.mode != RigidMode.STATIC:
            continue
        for nb_idx, _ in neighbors.get(i, []):
            if rigid_bodies[nb_idx].mode != RigidMode.STATIC:
                roots.append(i)
                break

    # BFS from each root through DYNAMIC/DYNAMIC_BONE bodies
    visited: set[int] = set()
    chains: list[Chain] = []

    for root_idx in sorted(roots):
        # BFS to collect connected dynamic bodies
        chain_rigids: list[int] = []
        chain_joints: list[int] = []
        queue: deque[int] = deque()

        # Seed: dynamic neighbors of root
        for nb_idx, j_idx in neighbors.get(root_idx, []):
            if rigid_bodies[nb_idx].mode == RigidMode.STATIC:
                continue
            if nb_idx not in visited:
                visited.add(nb_idx)
                queue.append(nb_idx)
                chain_rigids.append(nb_idx)
                chain_joints.append(j_idx)

        # Expand through dynamic bodies
        while queue:
            current = queue.popleft()
            for nb_idx, j_idx in neighbors.get(current, []):
                if nb_idx in visited or nb_idx == root_idx:
                    continue
                if rigid_bodies[nb_idx].mode == RigidMode.STATIC:
                    continue
                visited.add(nb_idx)
                queue.append(nb_idx)
                chain_rigids.append(nb_idx)
                if j_idx not in chain_joints:
                    chain_joints.append(j_idx)

        if not chain_rigids:
            continue

        root_rb = rigid_bodies[root_idx]
        bone_indices = [rigid_bodies[ri].bone_index for ri in chain_rigids
                        if rigid_bodies[ri].bone_index >= 0]

        chain = Chain(
            name=root_rb.name,
            group=_classify_chain(root_rb.name, rigid_bodies, chain_rigids),
            root_rigid_index=root_idx,
            root_bone_index=root_rb.bone_index,
            rigid_indices=chain_rigids,
            bone_indices=bone_indices,
            joint_indices=sorted(chain_joints),
        )
        chains.append(chain)

    return chains


# Patterns for chain classification (Japanese + English)
_HAIR_RE = re.compile(r"髪|hair|前髪|後髪|横髪|ポニテ|ponytail|twintail|ツインテ", re.IGNORECASE)
_SKIRT_RE = re.compile(r"スカート|skirt|裾", re.IGNORECASE)
_ACCESSORY_RE = re.compile(r"リボン|ribbon|ネクタイ|tie|アクセ|acc", re.IGNORECASE)


def _classify_chain(root_name: str, rigid_bodies, rigid_indices: list[int]) -> str:
    """Classify a chain by name pattern matching."""
    # Check root name and all body names in chain
    names = [root_name] + [rigid_bodies[i].name for i in rigid_indices]
    combined = " ".join(names)

    if _HAIR_RE.search(combined):
        return "hair"
    if _SKIRT_RE.search(combined):
        return "skirt"
    if _ACCESSORY_RE.search(combined):
        return "accessory"
    return "other"
