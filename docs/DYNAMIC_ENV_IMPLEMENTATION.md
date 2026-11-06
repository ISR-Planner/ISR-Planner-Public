# Dynamic Environment Feature - Implementation Summary

## Overview

This feature enables "live editing" during animation - users can add/remove targets, enable/disable drones while animation runs. Changes are staged until "Done" is pressed, which triggers checkpoint creation and replanning.

---

## Implementation Steps (8 commits)

### 1. Remove Cut Button
- Cut button removed from Mission Control
- Functionality absorbed into Done/Animate flow
- `canCut` permission removed from `getUiPermissions()`

### 2. Add Live Edit State Variables
Added to `missionState` object:
- `wasAnimatingOnEditEntry` - True if Edit clicked while drones were flying
- `hasEditChanges` - True if any edit was made during this edit session
- `stagedEnv` - Staged environment copy (edits go here during live edit)
- `stagedDroneConfigs` - Staged drone configs (edits go here during live edit)

### 3. Track Animation State on Edit Entry, Auto-Edit on Pause
- `enterEditMode()` now checks if animation is running and creates staged copies
- `pauseAnimation()` automatically enters edit mode when user pauses
- When entering via pause, `wasAnimatingOnEditEntry` is set to false (animation already stopped)

### 4. Allow Edit During Animation
- Added `MissionMode.ANIMATING` to `canEnterEdit` permission
- Edit button now enabled while drones are flying

### 5. Route Edits to Staged Copies
Added helper functions:
- `getEditableEnv()` - Returns staged copy during live edit, otherwise `state.env`
- `getEditableDroneConfigs()` - Returns staged copy during live edit, otherwise `state.droneConfigs`
- `markEditChange()` - Sets `hasEditChanges = true`

Updated all edit operations to use helpers:
- Add/delete/drag airports, targets, SAMs
- All drone config changes (enabled, start/end airport, refuel, speed, loiter, target access)
- Blocked fuel budget changes during edit mode

### 6. Modify acceptEdits/cancelEdits
Rewrote to handle two paths:

**acceptEditsLiveEdit():**
1. If no changes → exit edit mode, animation continues unchanged
2. If changes → Stop animation, apply staged copies, freeze checkpoint, run solver, auto-accept, auto-animate

**acceptEditsPausedEdit():**
1. If no changes → exit edit mode, return to PAUSED_MID_ANIMATION
2. If changes → Freeze checkpoint, run solver, show solution (user clicks Accept then Animate)

**cancelEdits():**
- Live edit → Discard staged, return to ANIMATING mode
- Paused edit → Restore previous env, return to appropriate mode

Added `exitEditModeCleanup()` helper to centralize cleanup.

### 7. Update UI
Status text updates:
- Live edit: "Live editing... (drones flying)"
- Regular edit: "Editing - Click Done when finished"
- Animating: "Animating..." (removed "Cut to checkpoint")
- Paused: "Paused - Make edits or Resume"

Fuel budget inputs disabled during edit mode.

### 8. Clear Live Edit State on Load/Reset
- `loadFromJson()` - Clears all live edit state when importing new environment
- `resetMission()` - Clears all live edit state during full reset

---

## Key Behaviors

### Path 1 - Live Edit (Edit while flying)
1. Click Edit during animation → drones keep flying
2. Edits staged in `stagedEnv` / `stagedDroneConfigs`
3. Done → Stop + Apply staged + Checkpoint + Solve + Auto-animate
4. Cancel → Discard staged, animation continues unchanged

### Path 2 - Paused Edit
1. Pause → drones stop, edit mode auto-enabled
2. Edits go directly to `state.env` / `state.droneConfigs`
3. Done → Checkpoint + Solve → Show solution (user accepts then animates)
4. Cancel → Return to paused state

### Edge Cases Handled
- **No changes made** → Exit edit mode, previous state preserved, no checkpoint created
- **New environment loaded** → Clear all edit/animation state
- **Reset** → Clear all edit/animation state

---

## Files Modified

| File | Changes |
|------|---------|
| `webapp/index.html` | Remove Cut button |
| `webapp/isr.js` | All logic changes (state variables, helpers, acceptEdits, cancelEdits, permissions, UI updates) |

---

## Animation Speed Slider (Separate Feature)

Also implemented but may need review:
- Slider in Mission Control (0.25x to 4x)
- Only affects animation playback speed, not fuel calculations
- Added `speedMultiplier` to `state.animation`
- CSS styling in `webapp/isr.css`
