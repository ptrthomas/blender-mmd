"""Apply parsed VMD motion data to a Blender armature and its mesh.

Handles:
- Bone keyframes → pose bone location/rotation F-curves
- Morph keyframes → shape key value F-curves
- Per-bone coordinate conversion from MMD (Y-up left-handed) to Blender (Z-up right-handed)
- Japanese → English bone name matching via mmd_name_j custom properties
- Morph name matching via mmd_morph_map JSON property on mesh object
- VMD Bézier interpolation → Blender F-curve Bézier handles
"""

from __future__ import annotations

import json
import logging
import unicodedata
from collections import defaultdict

import bpy
from mathutils import Matrix, Quaternion, Vector

from .types import BoneKeyframe, MorphKeyframe, PropertyKeyframe, VmdMotion

log = logging.getLogger("blender_mmd")

# Morph fallback aliases: VMD morph name → list of alternative Japanese names.
# When a VMD references a morph the model doesn't have, try these alternatives.
# Ordered by similarity — first match wins.
# fmt: off
MORPH_ALIASES: dict[str, list[str]] = {
    # Mouth shapes
    "ワ": ["あ"],                  # wa mouth ≈ open mouth (A)
    "わ": ["あ"],
    "えー": ["え"],                # elongated E ≈ E
    "にやり": ["笑い", "にこり"],  # smirk ≈ smile
    "にっこり": ["笑い", "にこり"],
    "なごみ": ["笑い"],            # gentle ≈ smile
    "はぅ": ["困る"],              # distressed sigh ≈ troubled
    "叫び": ["あ"],                # shout ≈ open mouth
    "わらい口": ["笑い"],          # laughing mouth ≈ smile
    "ぺろっ": ["あ"],              # tongue out ≈ open mouth
    # Eyes
    "笑い目": ["笑い"],            # smiling eyes ≈ smile
    "じと目": ["なごみ"],          # staring ≈ gentle
    "たれ目": ["なごみ"],          # droopy eyes ≈ gentle
    "つり目": ["怒り"],            # sharp eyes ≈ angry
    "瞳小": ["びっくり"],          # small pupils ≈ surprised
    # Brows
    "平行": ["真面目"],            # flat brows ≈ serious
    "短": ["真面目"],              # short brows ≈ serious
}
# fmt: on


def import_vmd(
    vmd: VmdMotion,
    armature_obj: bpy.types.Object,
    scale: float = 0.08,
    create_new_action: bool = False,
    target_fps: int = 30,
    include_static: bool = False,
) -> None:
    """Apply VMD motion to an armature and its child mesh.

    The armature must have been imported with blender_mmd (bones have ``mmd_name_j``
    custom properties).  The child mesh must have an ``mmd_morph_map`` custom
    property (JSON dict mapping Japanese morph names to shape key names).

    Args:
        create_new_action: If True, always create new actions (replaces existing).
            If False (default), reuse existing actions and append keyframes.
        target_fps: Target frame rate. 30 = MMD standard (no scaling). Other values
            scale all keyframe positions by target_fps/30.
        include_static: If True, create F-curves for bones/morphs that stay at rest
            pose (zero location/identity rotation, weight=0). Default False — keeps
            the Graph Editor clean.
    """
    fps_scale = target_fps / 30.0
    # Build Japanese name → Blender bone name lookup
    jp_to_bone = _build_bone_lookup(armature_obj)

    # --- Bone keyframes ---
    matched_bones = 0
    unmatched_bones: list[str] = []
    bone_action = None

    if vmd.bone_keyframes:
        # Group bone keyframes by bone name
        bone_groups: dict[str, list[BoneKeyframe]] = defaultdict(list)
        for kf in vmd.bone_keyframes:
            bone_groups[kf.bone_name].append(kf)

        # Sort each group by frame
        for group in bone_groups.values():
            group.sort(key=lambda kf: kf.frame)

        # Get or create bone action
        if armature_obj.animation_data is None:
            armature_obj.animation_data_create()

        existing_action = armature_obj.animation_data.action
        if create_new_action or existing_action is None:
            bone_action = bpy.data.actions.new(f"{armature_obj.name}_VMD")
            armature_obj.animation_data.action = bone_action
        else:
            bone_action = existing_action

        # Set all pose bones to quaternion rotation mode
        for pb in armature_obj.pose.bones:
            pb.rotation_mode = "QUATERNION"

        # Apply bone keyframes (skip bones where all keyframes are at rest)
        skipped_static = 0
        for jp_name, keyframes in bone_groups.items():
            blender_name = jp_to_bone.get(jp_name)
            if blender_name is None:
                unmatched_bones.append(jp_name)
                continue

            pose_bone = armature_obj.pose.bones.get(blender_name)
            if pose_bone is None:
                unmatched_bones.append(jp_name)
                continue

            if not include_static and _is_static_bone(keyframes):
                skipped_static += 1
                continue

            matched_bones += 1
            _apply_bone_keyframes(
                bone_action, armature_obj, pose_bone, keyframes, scale,
                fps_scale,
            )

        if skipped_static:
            log.info("VMD: skipped %d static bones (all at rest pose)", skipped_static)

        if unmatched_bones:
            log.warning(
                "VMD: %d bone names unmatched: %s",
                len(unmatched_bones),
                ", ".join(unmatched_bones[:10])
                + ("..." if len(unmatched_bones) > 10 else ""),
            )

    # Apply morph keyframes to shape keys
    morph_count = 0
    unmatched_morphs: list[str] = []
    if vmd.morph_keyframes:
        morph_count, unmatched_morphs = _apply_morph_keyframes(
            vmd.morph_keyframes, armature_obj, create_new_action, fps_scale,
            include_static,
        )

    # Apply IK toggle keyframes (use whichever bone action is active)
    ik_toggle_count = 0
    if vmd.property_keyframes:
        # Ensure we have a bone action for IK toggles
        if bone_action is None:
            if armature_obj.animation_data and armature_obj.animation_data.action:
                bone_action = armature_obj.animation_data.action
            else:
                if armature_obj.animation_data is None:
                    armature_obj.animation_data_create()
                bone_action = bpy.data.actions.new(f"{armature_obj.name}_VMD")
                armature_obj.animation_data.action = bone_action
        ik_toggle_count = _apply_ik_toggle_keyframes(
            vmd.property_keyframes, armature_obj, bone_action, jp_to_bone,
            fps_scale,
        )

    # Set scene FPS and adjust frame range
    _setup_scene_settings(armature_obj, target_fps)

    # Auto-reset physics if rigid bodies are built — prevents displaced bodies
    # after VMD import without requiring manual reset
    if armature_obj.get("physics_mode") == "rigid_body":
        from ..physics import reset_physics
        count = reset_physics(armature_obj)
        log.info("Auto-reset %d rigid bodies after VMD import", count)

    bone_group_count = len(bone_groups) if vmd.bone_keyframes else 0
    log.info(
        "VMD applied: %d/%d bones matched, %d morph channels, %d IK toggles, "
        "%d total bone keyframes",
        matched_bones,
        bone_group_count,
        morph_count,
        ik_toggle_count,
        len(vmd.bone_keyframes),
    )

    # Write VMD report to Blender Text Editor
    _log_vmd_report(
        armature_obj, vmd,
        matched_bones, bone_group_count, unmatched_bones,
        morph_count, unmatched_morphs,
    )


def _log_vmd_report(
    armature_obj: bpy.types.Object,
    vmd: VmdMotion,
    matched_bones: int,
    total_bone_groups: int,
    unmatched_bones: list[str],
    morph_count: int,
    unmatched_morphs: list[str],
) -> None:
    """Append VMD match report to the 'MMD Import Report' Text datablock."""
    lines = ["", f"=== VMD Report: {vmd.model_name} ===", ""]
    lines.append(f"Bones: {matched_bones}/{total_bone_groups} matched")
    if unmatched_bones:
        for name in unmatched_bones:
            lines.append(f"  {name}")
    lines.append("")
    morph_total = morph_count + len(unmatched_morphs)
    lines.append(f"Morphs: {morph_count}/{morph_total} matched")
    if unmatched_morphs:
        for name in unmatched_morphs:
            lines.append(f"  {name}")

    report_text = "\n".join(lines)

    # Append to existing report text (PMX report may already be there)
    txt = bpy.data.texts.get("MMD Import Report")
    if txt:
        txt.write(report_text)
    else:
        txt = bpy.data.texts.new("MMD Import Report")
        txt.write(report_text)


def _setup_scene_settings(armature_obj: bpy.types.Object, target_fps: int = 30) -> None:
    """Set scene FPS and frame range to match the imported VMD.

    The frame range is derived from all actions on the armature and its child
    meshes (shape key actions). Keyframes are already scaled to target_fps.
    """
    scene = bpy.context.scene

    scene.render.fps = target_fps
    scene.render.fps_base = 1

    # Collect frame range from all relevant actions
    frame_end = scene.frame_start
    for action in bpy.data.actions:
        if action.users == 0:
            continue
        start, end = action.frame_range
        frame_end = max(frame_end, int(end))

    if frame_end > scene.frame_end:
        scene.frame_end = frame_end
        log.info("Scene frame range set to %d–%d at %dfps", scene.frame_start, frame_end, target_fps)

    # Match rigid body cache end to animation length
    if scene.rigidbody_world and scene.rigidbody_world.point_cache:
        scene.rigidbody_world.point_cache.frame_end = frame_end


def _build_bone_lookup(armature_obj: bpy.types.Object) -> dict[str, str]:
    """Build a mapping from Japanese bone name → Blender bone name.

    Reads ``mmd_name_j`` custom property from each bone in the armature.
    Also builds fallback lookups for cross-era VMD compatibility:
    - NFKC normalization: catches half-width↔full-width katakana
      (e.g. ``ｽｶｰﾄ`` → ``スカート``)
    - Alias table: known semantic differences between PMD/PMX eras
      (e.g. ``人指`` ↔ ``人差指``)
    """
    jp_to_bone: dict[str, str] = {}
    # NFKC-normalized name → Blender name (fallback)
    nfkc_to_bone: dict[str, str] = {}

    for bone in armature_obj.data.bones:
        jp_name = bone.get("mmd_name_j")
        if jp_name:
            jp_to_bone[jp_name] = bone.name
            nfkc = unicodedata.normalize("NFKC", jp_name)
            if nfkc != jp_name:
                nfkc_to_bone[nfkc] = bone.name
            # Also register the NFKC form so lookups from either direction work
            nfkc_to_bone.setdefault(nfkc, bone.name)

    # Register aliases for known PMD↔PMX naming differences
    _ALIASES: list[tuple[str, str]] = [
        ("人指", "人差指"),      # index finger abbreviation
        ("上半身2", "上半身２"),  # arabic vs fullwidth numeral
    ]

    for a, b in _ALIASES:
        for side in ("左", "右"):
            for suffix in ("１", "２", "３", "1", "2", "3", ""):
                key_a = side + a + suffix
                key_b = side + b + suffix
                if key_a in jp_to_bone and key_b not in jp_to_bone:
                    jp_to_bone[key_b] = jp_to_bone[key_a]
                elif key_b in jp_to_bone and key_a not in jp_to_bone:
                    jp_to_bone[key_a] = jp_to_bone[key_b]
        # Non-sided variants
        for suffix in ("", "1", "2", "3", "１", "２", "３"):
            key_a = a + suffix
            key_b = b + suffix
            if key_a in jp_to_bone and key_b not in jp_to_bone:
                jp_to_bone[key_b] = jp_to_bone[key_a]
            elif key_b in jp_to_bone and key_a not in jp_to_bone:
                jp_to_bone[key_a] = jp_to_bone[key_b]

    # Merge NFKC fallbacks (don't override exact matches)
    for nfkc_name, bl_name in nfkc_to_bone.items():
        jp_to_bone.setdefault(nfkc_name, bl_name)

    return jp_to_bone


class _InterpolationHelper:
    """Compute axis permutation for interpolation channel remapping.

    VMD interpolation has separate curves per axis (X, Y, Z location + rotation).
    When the bone's local axes are permuted relative to the standard Y↔Z swap,
    the interpolation channels must be remapped to match.

    Matches mmd_tools' _InterpolationHelper.
    """

    __slots__ = ("_indices",)

    def __init__(self, mat: Matrix) -> None:
        indices = [0, 1, 2]
        # Find the dominant axis mapping by sorting matrix elements
        sorted_list = sorted(
            (-abs(mat[i][j]), i, j) for i in range(3) for j in range(3)
        )
        _, i, j = sorted_list[0]
        if i != j:
            indices[i], indices[j] = indices[j], indices[i]
        _, i, j = next(k for k in sorted_list if k[1] != i and k[2] != j)
        if indices[i] != j:
            idx = indices.index(j)
            indices[i], indices[idx] = indices[idx], indices[i]
        self._indices = indices

    def convert(self, interp_xyz: tuple[int, ...]) -> tuple[int, ...]:
        """Remap interpolation byte offsets according to axis permutation."""
        return tuple(interp_xyz[i] for i in self._indices)


class _BoneConverter:
    """Per-bone coordinate converter from MMD bone-local space to Blender bone-local space.

    VMD keyframes are in bone-local space.  Because the coordinate conversion
    (Y↔Z swap) changes each bone's local axes differently depending on the
    bone's rest pose orientation, we need a per-bone conversion matrix.

    Matches mmd_tools' BoneConverter class.
    """

    __slots__ = ("_mat", "_scale", "_interp_helper")

    def __init__(self, pose_bone: bpy.types.PoseBone, scale: float) -> None:
        # Get bone's rest pose matrix (bone-local → armature space)
        mat = pose_bone.bone.matrix_local.to_3x3()
        # Swap Y and Z rows to account for MMD↔Blender coordinate change
        mat[1], mat[2] = mat[2].copy(), mat[1].copy()
        # Transpose to get the inverse rotation (armature → adjusted bone-local)
        self._mat = mat.transposed()
        self._scale = scale
        self._interp_helper = _InterpolationHelper(self._mat)

    def convert_location(self, loc: tuple[float, float, float]) -> Vector:
        """Convert VMD bone-local location to Blender bone-local location."""
        return self._mat @ Vector(loc) * self._scale

    def convert_rotation(self, rot: tuple[float, float, float, float]) -> Quaternion:
        """Convert VMD bone-local quaternion to Blender bone-local quaternion.

        VMD stores quaternion as (x, y, z, w).
        The matrix conjugation handles both the axis remapping and handedness.
        """
        qx, qy, qz, qw = rot
        q_mmd = Quaternion((qw, qx, qy, qz))
        q_mat = self._mat.to_quaternion()
        return (q_mat @ q_mmd @ q_mat.conjugated()).normalized()

    def convert_interpolation(self, interp_xyz: tuple[int, ...]) -> tuple[int, ...]:
        """Remap interpolation byte offsets for axis permutation."""
        return self._interp_helper.convert(interp_xyz)


def _compatible_quaternion(prev_q: Quaternion, curr_q: Quaternion) -> Quaternion:
    """Ensure adjacent quaternion keyframes don't have sign flips.

    q and -q represent the same rotation, but Blender's NLERP interpolation
    treats them differently — interpolating between q and -q takes the long
    path (spinning ~360° instead of staying still). This function picks the
    sign that's closer to the previous quaternion.

    Matches mmd_tools' __minRotationDiff.
    """
    t1 = ((prev_q.w - curr_q.w) ** 2 + (prev_q.x - curr_q.x) ** 2
          + (prev_q.y - curr_q.y) ** 2 + (prev_q.z - curr_q.z) ** 2)
    t2 = ((prev_q.w + curr_q.w) ** 2 + (prev_q.x + curr_q.x) ** 2
          + (prev_q.y + curr_q.y) ** 2 + (prev_q.z + curr_q.z) ** 2)
    return -curr_q if t2 < t1 else curr_q


def _is_static_bone(keyframes: list[BoneKeyframe]) -> bool:
    """Check if all keyframes are at rest pose (zero location, identity rotation)."""
    for kf in keyframes:
        lx, ly, lz = kf.location
        rx, ry, rz, rw = kf.rotation
        if (abs(lx) > 1e-6 or abs(ly) > 1e-6 or abs(lz) > 1e-6
                or abs(rx) > 1e-6 or abs(ry) > 1e-6 or abs(rz) > 1e-6
                or min(abs(rw - 1.0), abs(rw + 1.0)) > 1e-6):
            return False
    return True


def _is_static_morph(keyframes: list[MorphKeyframe]) -> bool:
    """Check if all keyframes have weight=0."""
    return all(abs(kf.weight) < 1e-6 for kf in keyframes)


def _apply_bone_keyframes(
    action: bpy.types.Action,
    armature_obj: bpy.types.Object,
    pose_bone: bpy.types.PoseBone,
    keyframes: list[BoneKeyframe],
    scale: float,
    fps_scale: float = 1.0,
) -> None:
    """Create F-curves for a single bone's keyframes."""
    bone_name = pose_bone.name
    loc_path = f'pose.bones["{bone_name}"].location'
    rot_path = f'pose.bones["{bone_name}"].rotation_quaternion'

    # Create F-curves: 3 for location, 4 for rotation quaternion
    loc_fcs = [
        action.fcurve_ensure_for_datablock(
            armature_obj, loc_path, index=i, group_name=bone_name
        )
        for i in range(3)
    ]
    rot_fcs = [
        action.fcurve_ensure_for_datablock(
            armature_obj, rot_path, index=i, group_name=bone_name
        )
        for i in range(4)
    ]

    n = len(keyframes)

    # Pre-allocate keyframe points
    for fc in loc_fcs + rot_fcs:
        fc.keyframe_points.add(n)

    # Build per-bone converter
    converter = _BoneConverter(pose_bone, scale)

    # Fill keyframe data with quaternion sign compatibility
    prev_rot = None
    for ki, kf in enumerate(keyframes):
        loc = converter.convert_location(kf.location)
        rot = converter.convert_rotation(kf.rotation)
        frame = float(kf.frame) * fps_scale

        # Ensure quaternion sign consistency between adjacent keyframes
        if prev_rot is not None:
            rot = _compatible_quaternion(prev_rot, rot)
        prev_rot = rot

        # Location
        for ci in range(3):
            kp = loc_fcs[ci].keyframe_points[ki]
            kp.co = (frame, loc[ci])
            kp.interpolation = "BEZIER"

        # Rotation (Blender quaternion order: W, X, Y, Z)
        rot_vals = (rot.w, rot.x, rot.y, rot.z)
        for ci in range(4):
            kp = rot_fcs[ci].keyframe_points[ki]
            kp.co = (frame, rot_vals[ci])
            kp.interpolation = "BEZIER"

    # Apply interpolation handles
    _apply_bone_interpolation(loc_fcs, rot_fcs, keyframes, converter)

    # Fix first/last keyframe handles (matches mmd_tools __fixFcurveHandles)
    for fc in loc_fcs + rot_fcs:
        fc.update()
        if len(fc.keyframe_points) >= 1:
            kp0 = fc.keyframe_points[0]
            kp0.handle_left_type = "FREE"
            kp0.handle_left = (kp0.co[0] - 1, kp0.co[1])
            kp_last = fc.keyframe_points[-1]
            kp_last.handle_right_type = "FREE"
            kp_last.handle_right = (kp_last.co[0] + 1, kp_last.co[1])


def _apply_bone_interpolation(
    loc_fcs: list,
    rot_fcs: list,
    keyframes: list[BoneKeyframe],
    converter: _BoneConverter,
) -> None:
    """Apply VMD Bézier interpolation curves to F-curve keyframe handles.

    VMD interpolation data is 64 bytes per keyframe, encoding Bézier control
    points for 4 channels: X-location, Y-location, Z-location, Rotation.

    The 64 bytes are a 4×16 transposed layout where rows are shifted copies.
    Channel data is at stride-4 offsets from the row start:
      Row 0 (offset  0): X-location  → bytes [0, 4, 8, 12]
      Row 1 (offset 16): Y-location  → bytes [16, 20, 24, 28]
      Row 2 (offset 32): Z-location  → bytes [32, 36, 40, 44]
      Row 3 (offset 48): Rotation    → bytes [48, 52, 56, 60]

    The axis remapping uses the bone converter's interpolation helper to
    compute the correct row offsets for each axis (matches mmd_tools).
    """
    n = len(keyframes)
    if n < 2:
        return

    # Compute axis-remapped interpolation byte offsets for location channels
    # (0, 16, 32) are the row start offsets for X, Y, Z in the 64-byte block
    loc_indices = converter.convert_interpolation((0, 16, 32))
    # Rotation uses row 3 (offset 48) for all 4 quaternion components
    rot_index = 48

    for ki in range(n - 1):
        interp = keyframes[ki + 1].interpolation
        if len(interp) < 64:
            continue

        # Location channels: read 4 bytes at stride 4 from remapped row
        for bl_axis, idx in enumerate(loc_indices):
            x1 = interp[idx]
            y1 = interp[idx + 4]
            x2 = interp[idx + 8]
            y2 = interp[idx + 12]
            _set_bezier_handles(
                loc_fcs[bl_axis], ki, ki + 1, x1, y1, x2, y2
            )

        # Rotation channel (all 4 quaternion components share the same curve)
        x1 = interp[rot_index]
        y1 = interp[rot_index + 4]
        x2 = interp[rot_index + 8]
        y2 = interp[rot_index + 12]
        for fc in rot_fcs:
            _set_bezier_handles(fc, ki, ki + 1, x1, y1, x2, y2)


def _set_bezier_handles(
    fc, ki0: int, ki1: int,
    x1: int, y1: int, x2: int, y2: int,
) -> None:
    """Set Bézier handles between two adjacent keyframes.

    (x1, y1) is the right control point of kp0.
    (x2, y2) is the left control point of kp1.
    Values are in [0, 127] representing normalized position within the
    frame/value span between the two keyframes.
    """
    kp0 = fc.keyframe_points[ki0]
    kp1 = fc.keyframe_points[ki1]

    df = kp1.co[0] - kp0.co[0]  # frame delta
    dv = kp1.co[1] - kp0.co[1]  # value delta

    if df == 0:
        return

    # Check if this is linear (default) interpolation
    if x1 == 20 and y1 == 20 and x2 == 107 and y2 == 107:
        # These are roughly the default linear-ish handles, skip
        pass

    kp0.handle_right_type = "FREE"
    kp1.handle_left_type = "FREE"

    kp0.handle_right = (
        kp0.co[0] + df * x1 / 127.0,
        kp0.co[1] + dv * y1 / 127.0,
    )
    kp1.handle_left = (
        kp0.co[0] + df * x2 / 127.0,
        kp0.co[1] + dv * y2 / 127.0,
    )


def _apply_morph_keyframes(
    morph_keyframes: list[MorphKeyframe],
    armature_obj: bpy.types.Object,
    create_new_action: bool = False,
    fps_scale: float = 1.0,
    include_static: bool = False,
) -> tuple[int, list[str]]:
    """Apply morph keyframes to shape key F-curves on the control mesh.

    The control mesh (_mmd_morphs) owns all shape keys as value holders.
    Visible meshes' shape keys are driven from the control mesh via drivers.

    Returns (applied_count, unmatched_names).
    """
    from ..mesh import find_control_mesh

    # Find control mesh (preferred) or fall back to first mesh with shape keys
    ctrl_mesh = find_control_mesh(armature_obj)
    if ctrl_mesh and ctrl_mesh.data.shape_keys:
        target_mesh = ctrl_mesh
    else:
        # Legacy / single-mesh mode: use first mesh with shape keys
        target_mesh = None
        for c in armature_obj.children:
            if c.type == "MESH" and c.data.shape_keys:
                target_mesh = c
                break

    if not target_mesh:
        log.warning("VMD: No mesh with shape keys found on armature")
        return 0, []

    shape_keys = target_mesh.data.shape_keys

    # Load morph name mapping from control mesh or armature (legacy)
    morph_map_json = None
    if ctrl_mesh:
        morph_map_json = ctrl_mesh.get("mmd_morph_map")
    if not morph_map_json:
        morph_map_json = armature_obj.get("mmd_morph_map")
    if not morph_map_json:
        morph_map_json = target_mesh.get("mmd_morph_map")
    if morph_map_json:
        morph_map: dict[str, str] = json.loads(morph_map_json)
    else:
        morph_map = {}
        log.warning("VMD: No mmd_morph_map found, trying direct name match")

    # Group morph keyframes by name
    morph_groups: dict[str, list[MorphKeyframe]] = defaultdict(list)
    for kf in morph_keyframes:
        morph_groups[kf.morph_name].append(kf)

    for group in morph_groups.values():
        group.sort(key=lambda kf: kf.frame)

    # Get or create morph action on the target mesh's shape key datablock
    if shape_keys.animation_data is None:
        shape_keys.animation_data_create()

    existing_action = shape_keys.animation_data.action
    if create_new_action or existing_action is None:
        morph_action = bpy.data.actions.new(f"{armature_obj.name}_VMD_Morphs")
        shape_keys.animation_data.action = morph_action
    else:
        morph_action = existing_action

    applied = 0
    unmatched: list[str] = []

    for jp_name, keyframes in morph_groups.items():
        # Find the shape key name: morph_map → direct match → alias fallback
        sk_name = morph_map.get(jp_name)
        if sk_name is None:
            if jp_name in shape_keys.key_blocks:
                sk_name = jp_name
        if sk_name is None:
            for alias_jp in MORPH_ALIASES.get(jp_name, []):
                alias_en = morph_map.get(alias_jp)
                if alias_en and alias_en in shape_keys.key_blocks:
                    sk_name = alias_en
                    log.debug("VMD morph alias: '%s' → '%s' (%s)", jp_name, alias_jp, sk_name)
                    break

        if sk_name is None or sk_name not in shape_keys.key_blocks:
            unmatched.append(jp_name)
            continue

        if not include_static and _is_static_morph(keyframes):
            continue

        data_path = f'key_blocks["{sk_name}"].value'
        fc = morph_action.fcurve_ensure_for_datablock(
            shape_keys, data_path, index=0, group_name=sk_name
        )

        n = len(keyframes)
        fc.keyframe_points.add(n)

        for ki, kf in enumerate(keyframes):
            kp = fc.keyframe_points[ki]
            kp.co = (float(kf.frame) * fps_scale, kf.weight)
            kp.interpolation = "LINEAR"

        fc.update()
        applied += 1

    if unmatched:
        log.warning(
            "VMD: %d morph names unmatched: %s",
            len(unmatched),
            ", ".join(unmatched[:10])
            + ("..." if len(unmatched) > 10 else ""),
        )

    log.info(
        "VMD morphs: %d/%d channels applied on %s",
        applied, len(morph_groups), target_mesh.name,
    )
    return applied, unmatched


def _apply_ik_toggle_keyframes(
    property_keyframes: list[PropertyKeyframe],
    armature_obj: bpy.types.Object,
    bone_action: bpy.types.Action,
    jp_to_bone: dict[str, str],
    fps_scale: float = 1.0,
) -> int:
    """Apply IK toggle keyframes by animating IK constraint influence.

    More Blender-native than mmd_tools' custom property + callback approach:
    we directly keyframe the constraint's influence (0.0 or 1.0).

    Returns the number of IK bones with toggle keyframes applied.
    """
    # Build a map: IK bone name (Japanese) → (pose_bone with IK constraint, constraint)
    # The IK constraint's subtarget points to the IK bone (the one with is_ik flag)
    ik_constraints: dict[str, list[tuple]] = {}
    for pb in armature_obj.pose.bones:
        for c in pb.constraints:
            if c.type == "IK" and c.subtarget:
                # Find the Japanese name of the subtarget bone
                subtarget_bone = armature_obj.data.bones.get(c.subtarget)
                if subtarget_bone:
                    jp_name = subtarget_bone.get("mmd_name_j")
                    if jp_name:
                        if jp_name not in ik_constraints:
                            ik_constraints[jp_name] = []
                        ik_constraints[jp_name].append((pb, c))

    applied = 0
    ik_fcurves: set = set()
    for kf in property_keyframes:
        for ik_name, enabled in kf.ik_states:
            entries = ik_constraints.get(ik_name)
            if not entries:
                continue

            influence = 1.0 if enabled else 0.0
            frame = float(kf.frame) * fps_scale

            for pb, constraint in entries:
                constraint.influence = influence
                data_path = f'pose.bones["{pb.name}"].constraints["{constraint.name}"].influence'
                fc = bone_action.fcurve_ensure_for_datablock(
                    armature_obj, data_path, index=0,
                    group_name=pb.name,
                )
                # Add a single keyframe point
                kp_count = len(fc.keyframe_points)
                fc.keyframe_points.add(1)
                kp = fc.keyframe_points[kp_count]
                kp.co = (frame, influence)
                kp.interpolation = "CONSTANT"
                ik_fcurves.add(fc)

            applied += 1

    # Finalize all IK toggle F-curves
    for fc in ik_fcurves:
        fc.update()

    if applied:
        log.info("VMD IK toggles: %d state changes applied", applied)
    return len(ik_constraints)


def _ensure_morph_sync_handler() -> None:
    """No-op — kept for backward compatibility. Drivers handle sync now."""
    pass


def _remove_morph_sync_handler() -> None:
    """No-op — kept for backward compatibility."""
    pass
