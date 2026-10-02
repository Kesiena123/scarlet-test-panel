"""Layer configuration (promt1.txt).

The Block Position Monitor runs at least 4 layers. Each layer maps 1:1 to one
calibration anchor point (an encoderCount, positionFt pair) captured during
CALIBRATE mode. The authoritative calibration is PERSISTED ON THE FIRMWARE
(EEPROM) and reported via STATUS / done-acks as calPositionN / calCounterN —
the dashboard only ever edits it through the two-stage set_calibration_point
-> CONFIRM -> ACK(echo) protocol and displays the device-confirmed values.

This module therefore stores only the *display/editing* helper data that lives
on the dashboard: an optional operator label per layer plus dashboard fallback
positions used until the device confirms its stored calibration. It is NOT
hard-coded — everything is persisted and editable by Supervisor/Engineer. The
example values from the reference image are illustrative only and are never
used as real project defaults.
"""

from scarlet_test_panel.services import settings

LAYER_COUNT = 4


def _default_layers():
    # 4 layers, all positions 0.0 ("not configured yet"); operators capture the
    # real positions+counters into the firmware during CALIBRATE mode. Nothing
    # is hardcoded from the reference image (promt1.txt).
    return [{"number": i + 1,
             "cal_position": 0.0,
             "name": f"Wrap {i + 1}"} for i in range(LAYER_COUNT)]


def _clean_ref(value, default=0.0):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if v < 1e9 else default


def load_layers():
    """Return the persistent layer list (normalized). Never raises.

    cal_position values are dashboard fallbacks only; the authoritative values
    are the firmware EEPROM calibration reported via STATUS / done-acks.
    """
    merged = settings.load()
    refs = merged.get("layer_positions") or []
    names = merged.get("layer_names") or []
    legacy = merged.get("layer_refs") or []

    layers = _default_layers()
    for i in range(LAYER_COUNT):
        position = 0.0
        name = layers[i]["name"]
        if i < len(refs):
            position = _clean_ref(refs[i])
        if i < len(names) and str(names[i]).strip():
            name = str(names[i])
        # Back-compat: a persisted old-style list of positions labels wins for
        # the labels if present and layer_names is empty.
        if isinstance(legacy, list) and i < len(legacy):
            item = legacy[i]
            if isinstance(item, dict):
                if not str(name).strip() or not (i < len(names) and str(names[i]).strip()):
                    if str(item.get("name", "") or "").strip():
                        name = str(item["name"])
        layers[i] = {"number": i + 1, "cal_position": position, "name": name}
    return layers


def save_layers(layers):
    """Persist the dashboard-side layer helper data (names + position fallbacks)."""
    clean_refs = []
    clean_names = []
    for i in range(LAYER_COUNT):
        item = layers[i] if i < len(layers) else _default_layers()[i]
        pos = item.get("cal_position", item.get("ref", 0.0)) if isinstance(item, dict) else 0.0
        clean_refs.append(_clean_ref(pos))
        if isinstance(item, dict) and str(item.get("name", "") or "").strip():
            clean_names.append(str(item["name"]))
        else:
            clean_names.append(f"Wrap {i + 1}")
    settings.save({"layer_positions": clean_refs, "layer_names": clean_names})


def derive_layer(live_counter, cal_counters):
    """Local mirror of the firmware rule (promt.txt).

    The "current layer" is the NUMBER OF FULLY COMPLETED LEVELS:
      * while live_counter < P1                    -> 0  (Level 1 not complete)
      * P1 <= live_counter < P2                    -> 1  (Level 1 complete)
      * P2 <= live_counter < P3                    -> 2
      * P3 <= live_counter < P4                    -> 3
      * P4 <= live_counter                         -> 4
      * live_counter > P4 -> 4 + floor((ct - P4) / (P4 - P3))
    A level is counted ONLY once its END boundary (anchor counter) is reached.
    Used for dashboard precondition checks / display fallbacks; the
    authoritative currentLayer always comes from the firmware report.
    """
    anchors = []
    for c in cal_counters:
        try:
            v = int(c)
        except (TypeError, ValueError):
            continue
        if v >= 0:
            anchors.append(v)
    anchors.sort()
    if not anchors:
        return 0
    if live_counter < anchors[0]:
        return 0
    completed = 1
    for k, c in enumerate(anchors):
        if live_counter >= c:
            completed = k + 1
        else:
            break
    if completed < len(anchors):
        return completed
    last = anchors[-1]
    gap = (last - anchors[-2]) if len(anchors) >= 2 else 1
    if gap <= 0:
        gap = 1
    if live_counter <= last:
        return len(anchors)
    return len(anchors) + (live_counter - last) // gap


__all__ = ["LAYER_COUNT", "load_layers", "save_layers", "derive_layer"]
