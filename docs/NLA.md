# NLA & Animation Workflow

Live design document tracking the animation remixing features across multiple sessions.

---

## Phase 1: VMD FPS Control + NLA Push-Down

### Context

VMD motions are authored at 30fps with integer frame numbers. Currently `import_vmd()` hardcodes scene fps to 30 and places keyframes at their original frame numbers. Users working at other frame rates (60fps smooth, custom rates for music sync) must manually scale all keyframes in the graph editor across both armature and morph actions — tedious and error-prone.

Additionally, there's no way to layer multiple VMD imports via NLA. Each import either appends to or replaces the active action. Users want to remix motions (body dance + lip sync + facial expressions) using Blender's NLA system.

### Files to modify

| File | Changes |
|------|---------|
| `blender_mmd/vmd/importer.py` | Add `target_fps` param, fps_scale in all keyframe functions, `push_to_nla()` function |
| `blender_mmd/operators.py` | FPS properties on VMD import operator, new Push to NLA + Mark as Assets operators |
| `blender_mmd/panels.py` | Enhanced Animation panel with NLA info and action buttons |

No new modules. No changes to `__init__.py` (operators/panels already registered).

---

### 1. FPS Control (`vmd/importer.py`)

#### 1a. `import_vmd()` signature (line 55)

```python
def import_vmd(
    vmd: VmdMotion,
    armature_obj: bpy.types.Object,
    scale: float = 0.08,
    create_new_action: bool = False,
    target_fps: int = 30,  # NEW — 30 = MMD standard (no scaling)
) -> None:
```

Compute scale factor once at top of function:
```python
fps_scale = target_fps / 30.0
```

#### 1b. Pass `fps_scale` to all keyframe functions

- `_apply_bone_keyframes(action, armature_obj, pose_bone, keyframes, scale, fps_scale)` — line 117
- `_apply_morph_keyframes(morph_keyframes, armature_obj, create_new_action, fps_scale)` — line 133
- `_apply_ik_toggle_keyframes(property_keyframes, armature_obj, bone_action, jp_to_bone, fps_scale)` — line 149

#### 1c. Frame scaling in each function

**`_apply_bone_keyframes()`** (line 430): `frame = float(kf.frame) * fps_scale`

Bezier handles auto-scale because `_set_bezier_handles()` uses relative frame deltas (`df = kp1.co[0] - kp0.co[0]`). No changes needed there.

**`_apply_morph_keyframes()`** (line 659): `kp.co = (float(kf.frame) * fps_scale, kf.weight)`

**`_apply_ik_toggle_keyframes()`** (line 730): `frame = float(kf.frame) * fps_scale`

#### 1d. `_setup_scene_settings()` (line 215)

Add `target_fps` parameter:
```python
def _setup_scene_settings(armature_obj: bpy.types.Object, target_fps: int = 30) -> None:
```

Set scene to target fps instead of hardcoded 30:
```python
scene.render.fps = target_fps
scene.render.fps_base = 1
```

Frame range calculation already reads scaled values from `action.frame_range` — no change needed.

Call from `import_vmd()`: `_setup_scene_settings(armature_obj, target_fps)`

---

### 2. VMD Import Operator FPS UI (`operators.py`)

#### 2a. New properties on `BLENDER_MMD_OT_import_vmd` (after line 79)

```python
fps_mode: EnumProperty(
    name="FPS",
    items=[
        ("30", "30 fps (MMD)", "Keep original 30fps timing"),
        ("60", "60 fps", "Scale to 60fps"),
        ("CUSTOM", "Custom", "Specify a custom frame rate"),
    ],
    default="30",
)
fps_custom: IntProperty(
    name="Custom FPS",
    default=30,
    min=1, max=120,
)
```

#### 2b. Add `draw()` method (file browser sidebar layout)

```python
def draw(self, context):
    layout = self.layout
    layout.prop(self, "create_new_action")
    layout.prop(self, "fps_mode")
    if self.fps_mode == "CUSTOM":
        layout.prop(self, "fps_custom")
```

#### 2c. Resolve target_fps in `execute()` (line 98)

```python
target_fps = self.fps_custom if self.fps_mode == "CUSTOM" else int(self.fps_mode)
import_vmd(vmd, armature_obj, scale,
           create_new_action=self.create_new_action,
           target_fps=target_fps)
```

---

### 3. NLA Push-Down (`vmd/importer.py`)

#### 3a. New `push_to_nla()` function (add after `import_vmd`)

```python
def push_to_nla(armature_obj, strip_name="VMD"):
    """Push current bone and morph actions to NLA strips.

    Returns dict: {"bone_strips": int, "morph_strips": int}
    """
```

**Bone action push:**
- Get `armature_obj.animation_data.action`
- Set `action.use_fake_user = True` (prevent GC)
- Create NLA track: `anim.nla_tracks.new()`
- Create strip: `track.strips.new(strip_name, start_frame, action)`
- Set `strip.extrapolation = 'NOTHING'`, `strip.blend_type = 'COMBINE'`
- Clear active: `anim.action = None`

**Morph action push (handler-based, single strip):**
- Find all child meshes with shape keys that have an active action
- Get the shared morph action from **primary mesh** (first child with shape keys)
- Set `morph_action.use_fake_user = True`
- Create NLA track + strip on **primary mesh only**
- Assign correct slot: `strip.action_slot = anim.action_slot` (before clearing)
- Clear active: `anim.action = None`
- **Secondary meshes**: clear active action, then `animation_data_clear()` to remove them from the NLA editor entirely
- Store `armature["mmd_morph_sync"] = primary_mesh.name` and register a `frame_change_post` handler that copies primary mesh shape key values to all secondaries on each frame

This produces **1 morph NLA strip** instead of N (one per split mesh). Moving/scaling the single morph strip in the NLA editor controls all meshes simultaneously. Subsequent `import_vmd()` calls detect `mmd_morph_sync` on the armature and skip action assignment on secondary meshes (handler syncs values).

**Material driver cleanup**: `push_to_nla()` also calls `_clear_material_drivers()` which removes `animation_data` from material nodetrees (emission drivers). This prevents "Shader Nodetree" entries from cluttering the NLA editor. `clear_animation` restores drivers via `setup_drivers()`.

**Handler lifecycle**:
- `_ensure_morph_sync_handler()` registers the handler (idempotent)
- `_remove_morph_sync_handler()` unregisters it (called by `clear_animation`)
- `__init__.py` registers a `load_post` handler that re-registers the sync handler if any armature has `mmd_morph_sync` set (persists across file load)

**Edge cases:**
- No bone action → skip bone push, only push morphs
- No morph action → skip morph push, only push bones
- Neither → return zeros
- Already pushed (no active action) → return zeros
- Deleting primary mesh blocked when `mmd_morph_sync` is set (must clear animation first)

---

### 4. Push to NLA Operator (`operators.py`)

New operator `BLENDER_MMD_OT_push_to_nla`:
- `bl_idname = "blender_mmd.push_to_nla"`
- `poll()`: requires MMD armature with at least one active action (bone or morph)
- `execute()`: calls `push_to_nla(armature_obj, strip_name)`, strip_name from current action name
- Add to `_classes` tuple

---

### 5. Mark as Assets Operator (`operators.py`)

New operator `BLENDER_MMD_OT_mark_actions_as_assets`:
- `bl_idname = "blender_mmd.mark_actions_as_assets"`
- Marks active bone action + morph action as Blender assets (`action.asset_mark()`)
- Also marks NLA-stashed actions (iterates `nla_tracks` → `strips` → `strip.action`)
- Deduplicates via set of already-marked action names
- Add to `_classes` tuple

---

### 6. Enhanced Animation Panel (`panels.py`)

Rewrite `BLENDER_MMD_PT_animation.draw()` (line 362) to show:

```
Animation
  Bones: MyModel_VMD                          [ACTION icon]
  Morphs: MyModel_VMD_Morphs                  [SHAPEKEY_DATA icon]
  NLA: 2 bone, 1 morph tracks                 [NLA icon] (only if tracks exist)

  [Push to NLA]  [Mark as Assets]             (only if active actions exist)
  [Clear Animation]                            (only if any animation exists)
```

- Bone action info from `armature_obj.animation_data.action`
- Morph action found via helper `_find_morph_action()` scanning child meshes
- NLA track count from `armature_obj.animation_data.nla_tracks` + first mesh's `sk.animation_data.nla_tracks`
- "No animation" label when nothing exists (no active action AND no NLA tracks)
- Asset badge icon (`ASSET_MANAGER`) shown next to action name if `action.asset_data` is set

---

### 7. Implementation Order

1. FPS scaling in `vmd/importer.py`
2. VMD operator FPS properties + `draw()` in `operators.py`
3. `push_to_nla()` function in `vmd/importer.py`
4. Push + Asset operators in `operators.py`, add to `_classes`
5. Animation panel rewrite in `panels.py`
6. Test via blender-agent

### 8. Verification

```python
# Test FPS scaling
arm = importer.import_pmx("/path/to/model.pmx")
motion = vmd_parser.parse("/path/to/motion.vmd")
vmd_importer.import_vmd(motion, arm, target_fps=60)
assert bpy.context.scene.render.fps == 60
# Frame 30 in VMD should be at frame 60 in Blender (30 * 60/30 = 60)

# Test NLA push
vmd_importer.push_to_nla(arm, "Dance")
assert len(arm.animation_data.nla_tracks) == 1
assert arm.animation_data.action is None

# Test layering
lip = vmd_parser.parse("/path/to/lip.vmd")
vmd_importer.import_vmd(lip, arm)  # creates new action (no active)
vmd_importer.push_to_nla(arm, "LipSync")
assert len(arm.animation_data.nla_tracks) == 2
```

Run `pytest` after changes to catch regressions in parser tests.

---

## Phase 2: Body-Part Action Splitting (TODO)

Split a VMD action into separate body-part actions for per-track NLA mixing.

### Design considerations

- **Auto-detect from MMD bone categories**: Standard MMD models have predictable bone hierarchies (upper body, lower body, fingers, face/eyes). Could auto-classify bones.
- **Manual split operator**: User selects bones → "Extract to new action" → creates a new action with only those bones' F-curves. More flexible.
- **Blender native**: Blender's NLA Editor already supports drag/select on strips. Check if native workflow is sufficient before building custom operators.
- **Bone groups**: Define named presets (upper body, lower body, face, fingers, IK targets) based on `mmd_name_j` patterns.

### TODO
- [ ] Research if Blender's native NLA F-curve filtering / track management is sufficient
- [ ] Define standard MMD body-part bone groups
- [ ] Implement split operator (extract selected bone F-curves to new action)
- [ ] Panel UI for body-part presets

---

## Phase 3: Asset Library Integration (TODO)

Persistent motion library for reuse across projects.

### Design considerations

- **Current file assets**: Already planned in Phase 1 (`mark_actions_as_assets` operator). Works for single-project use.
- **Dedicated library directory**: User-configured folder with .blend files per motion. Catalog structure: `MMD/Dance`, `MMD/LipSync`, `MMD/Poses`. Requires writing `blender_assets.cats.txt` (no Python API for catalogs — must write file directly).
- **Export to library**: Operator that saves selected action(s) to a .blend file in the library directory, marks as assets, assigns catalog.
- **Drag-and-drop**: Blender's Asset Browser already supports dragging action assets onto objects. Need to verify NLA strip creation from asset drag works correctly.

### TODO
- [ ] Implement "Export to Library" operator (save action to external .blend)
- [ ] Auto-generate `blender_assets.cats.txt` with MMD catalog structure
- [ ] User preference for library directory path
- [ ] Test Asset Browser → NLA drag-and-drop workflow
- [ ] Thumbnail generation for action previews

---

## Phase 4: Animation Remixing Tools (TODO)

Advanced NLA workflow features for creating dance videos.

### Speed/timing control
- [ ] Strip speed scaling operator (change `strip.scale` property)
- [ ] Time remapping for music sync (keyframe `strip.strip_time`)
- [ ] BPM-based time scaling (input song BPM + motion BPM → auto-scale)

### Mixing and blending
- [ ] Influence keyframing helper (set strip influence at current frame)
- [ ] Auto-crossfade between adjacent strips on same track
- [ ] Blend mode presets (COMBINE for layering, ADD for additive, REPLACE for override)

### Bone masking
- [ ] Per-strip bone filtering (since Blender doesn't support this natively, use separate tracks)
- [ ] Quick "mute upper/lower body" toggle per NLA track
- [ ] Constraint-based bone override (COPY_TRANSFORMS with keyframed influence)

### Workflow helpers
- [ ] "Duplicate strip to new track" operator
- [ ] "Mirror strip" (left/right body swap for symmetry variations)
- [ ] "Loop strip" (set repeat count to fill time range)
- [ ] NLA preview with auto-play from strip start

---

## Phase 5: Rig Retargeting (TODO)

Transfer animation between different MMD models or between MMD and standard rigs.

### Design considerations

- **MMD → MMD retargeting**: Models share standard bone names but may have different proportions. Need bone mapping + position correction.
- **MMD ↔ Mixamo/Rigify**: Requires bone roll matching (our rolls are correct — this is where `_BoneConverter` and explicit roll computation pay off). Need explicit bone name mapping tables.
- **Motion capture → MMD**: BVH/FBX import → retarget to MMD armature.
- **Constraint-based**: COPY_ROTATION/COPY_LOCATION constraints with source→target bone mapping. Bake to keyframes after retarget.

### TODO
- [ ] Define retarget bone mapping format (JSON: source_bone → target_bone)
- [ ] Implement constraint-based retarget (add COPY constraints, bake, remove)
- [ ] Standard mapping presets: MMD↔Mixamo, MMD↔Rigify
- [ ] Handle proportion differences (scale location keyframes)
- [ ] UI for mapping editor (source bone → target bone picker)

---

## Blender NLA API Reference

Key classes and patterns used throughout this work:

```python
# Push action to NLA
anim = obj.animation_data
track = anim.nla_tracks.new()
strip = track.strips.new("Name", start_frame, action)
strip.blend_type = 'COMBINE'      # REPLACE, COMBINE, ADD, SUBTRACT, MULTIPLY
strip.extrapolation = 'NOTHING'   # NOTHING, HOLD, HOLD_FORWARD
strip.influence = 1.0             # 0.0-1.0
strip.scale = 1.0                 # playback speed
strip.mute = False
strip.use_reverse = False
strip.repeat = 1.0
anim.action = None  # clear active to let NLA evaluate

# Slotted actions (Blender 5.0)
strip.action_slot = anim.action_slot  # preserve slot assignment

# Asset marking
action.asset_mark()
action.asset_generate_preview()
action.asset_data.description = "..."
action.asset_data.tags.new("dance")
```

**Limitation**: No per-bone strip filtering in NLA. Must use separate actions on separate tracks for body-part mixing.
