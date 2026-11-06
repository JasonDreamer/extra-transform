# Layer storage, and how all layers are folded into the native Delta channels.
#
# Layer inputs are top-level custom properties: the UI can draw them, they can be
# keyed, and each gets its own ID_PROPERTY depsgraph node. Drivers must never read
# add-on (bpy.props) properties: those resolve to the object's PARAMETERS_EVAL node,
# and driving anything on the same object from them creates a dependency cycle.
#
# Location, scale and single-layer rotation are simple-expression drivers. Composing
# two or more rotations does not fit one, and a Python driver is blocked in files
# opened with auto-execution off, so that case is written by the add-on's handlers.

import math
import uuid

import bpy
from bpy_extras import anim_utils
from mathutils import Euler, Matrix, Quaternion

# 0.1.0 kept intermediate driver results in this group; it is removed on rebuild.
LEGACY_GROUP = "ExtraTransform"
DELTA_PATHS = ("delta_location", "delta_rotation_euler", "delta_rotation_quaternion", "delta_scale")
EULER_ORDERS = ('XYZ', 'XZY', 'YXZ', 'YZX', 'ZXY', 'ZYX')
# The scale driver multiplies one variable per layer in a 255-character expression.
MAX_LAYERS = 100

INPUTS = {
    "loc": ((0.0, 0.0, 0.0), 'TRANSLATION'),
    "rot": ((0.0, 0.0, 0.0), 'EULER'),
    "quat": ((1.0, 0.0, 0.0, 0.0), 'QUATERNION'),
    "scale": ((1.0, 1.0, 1.0), 'XYZ'),
}

# Named like the native channels ("X Delta Location" -> "X Extra 2 Location") so the
# Graph Editor reads naturally. The number is the layer's position in the stack.
LABELS = {"loc": "Location", "rot": "Euler Rotation", "quat": "Quaternion Rotation", "scale": "Scale"}
FCURVE_GROUP = "Object Transforms"


def _key(slot, uid, ch):
    # slot 0: not numbered yet (fresh layer, mid-renumber, or a file from 0.1.0)
    return f"Extra {slot} {LABELS[ch]}" if slot else f"et_{uid}_{ch}"


def input_key(layer, ch):
    return _key(layer.slot, layer.uid, ch)


def input_path(layer, ch):
    return f'["{input_key(layer, ch)}"]'


def input_paths(layer, ch):
    path = input_path(layer, ch)
    return [f"{path}[{k}]" for k in range(len(INPUTS[ch][0]))]


def layer_paths(layers):
    return {input_path(l, ch) for l in layers for ch in INPUTS}


# ---------------------------------------------------------------------------
# Layer data


def new_layer(ob, name, values=None, index=None):
    settings = ob.extra_transform
    name = unique_name(settings, name)
    layer = settings.layers.add()
    layer.uid = uuid.uuid4().hex[:8]
    layer.name = name
    for ch, (default, subtype) in INPUTS.items():
        key = input_key(layer, ch)
        ob[key] = list(values[ch]) if values and ch in values else list(default)
        ob.id_properties_ui(key).update(subtype=subtype, default=list(default))
    last = len(settings.layers) - 1
    if index is not None and index != last:
        settings.layers.move(last, index)
    else:
        index = last
    settings.active_index = index
    return settings.layers[index]


def unique_name(settings, name):
    taken = {l.name for l in settings.layers}
    if name not in taken:
        return name
    base = name.rsplit(".", 1)[0] if name[-4:-3] == "." and name[-3:].isdigit() else name
    n = 1
    while f"{base}.{n:03d}" in taken:
        n += 1
    return f"{base}.{n:03d}"


def read_inputs(ob, layer):
    return {ch: list(ob[input_key(layer, ch)]) for ch in INPUTS}


def write_inputs(ob, layer, values):
    for ch, v in values.items():
        # Go through RNA like a UI edit does. Plain ob[key] writes skip the property
        # update, so dependent drivers are not reliably re-evaluated.
        ob.path_resolve(input_path(layer, ch), False)[:] = list(v)


def remove_layer(ob, index):
    settings = ob.extra_transform
    layer = settings.layers[index]
    paths = layer_paths([layer])
    _remove_fcurves(ob, lambda fc: fc.data_path in paths)
    for ch in INPUTS:
        ob.pop(input_key(layer, ch), None)
    settings.layers.remove(index)
    settings.active_index = min(settings.active_index, len(settings.layers) - 1)


def remove_all(ob):
    for i in reversed(range(len(ob.extra_transform.layers))):
        remove_layer(ob, i)
    rebuild(ob)


def layer_is_animated(ob, layer):
    paths = layer_paths([layer])
    return any(fc.data_path in paths for fc in _fcurves(ob))


def _rename_inputs(ob, layer, slot):
    bag = _channelbag(ob)
    for ch in INPUTS:
        old, new = input_key(layer, ch), _key(slot, layer.uid, ch)
        if old == new or old not in ob:
            continue
        ui = ob.id_properties_ui(old).as_dict()
        ob[new] = ob[old].to_list()
        ob.id_properties_ui(new).update(**ui)
        del ob[old]
        if bag:
            for fc in bag.fcurves:
                if fc.data_path == f'["{old}"]':
                    fc.data_path = f'["{new}"]'
    layer.slot = slot


def renumber(ob):
    """Rename every layer's custom properties to match its position in the stack."""
    layers = ob.extra_transform.layers
    moving = [(i + 1, l) for i, l in enumerate(layers) if l.slot != i + 1]
    # Park first so a swap never lands on a name that is still taken.
    for _, layer in moving:
        _rename_inputs(ob, layer, 0)
    for slot, layer in moving:
        _rename_inputs(ob, layer, slot)


def group_fcurves(ob):
    """Keep layer channels under "Object Transforms", next to the native ones."""
    bag = _channelbag(ob)
    if not bag:
        return
    paths = layer_paths(ob.extra_transform.layers)
    stray = [fc for fc in bag.fcurves if fc.data_path in paths and (not fc.group or fc.group.name != FCURVE_GROUP)]
    if not stray:
        return
    group = bag.groups.get(FCURVE_GROUP) or bag.groups.new(FCURVE_GROUP)
    for fc in stray:
        fc.group = group


def _channelbag(ob):
    ad = ob.animation_data
    if not ad or not ad.action or not ad.action_slot:
        return None
    return anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)


def _fcurves(ob):
    bag = _channelbag(ob)
    return list(bag.fcurves) if bag else []


def _remove_fcurves(ob, match):
    bag = _channelbag(ob)
    if bag:
        for fc in [fc for fc in bag.fcurves if match(fc)]:
            bag.fcurves.remove(fc)


# ---------------------------------------------------------------------------
# Native Delta Transform <-> layers


def delta_is_used(ob):
    if (any(ob.delta_location) or any(ob.delta_rotation_euler)
            or tuple(ob.delta_rotation_quaternion) != (1.0, 0.0, 0.0, 0.0)
            or tuple(ob.delta_scale) != (1.0, 1.0, 1.0)):
        return True
    fcurves = _fcurves(ob) + list(ob.animation_data.drivers if ob.animation_data else [])
    return any(fc.data_path in DELTA_PATHS for fc in fcurves)


def migrate_delta(ob):
    """Move the object's own Delta Transform into a layer, animation and drivers included."""
    values = {
        "loc": ob.delta_location[:],
        "rot": ob.delta_rotation_euler[:],
        "quat": ob.delta_rotation_quaternion[:],
        "scale": ob.delta_scale[:],
    }
    layer = new_layer(ob, "Delta", values)
    remap = {
        "delta_location": input_path(layer, "loc"),
        "delta_rotation_euler": input_path(layer, "rot"),
        "delta_rotation_quaternion": input_path(layer, "quat"),
        "delta_scale": input_path(layer, "scale"),
    }
    fcurves = _fcurves(ob) + list(ob.animation_data.drivers if ob.animation_data else [])
    for fc in fcurves:
        if fc.data_path in remap:
            fc.data_path = remap[fc.data_path]
    return layer


IDENTITY = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0), (1.0, 1.0, 1.0))


def reset_delta(ob):
    ob.delta_location, ob.delta_rotation_euler, ob.delta_rotation_quaternion, ob.delta_scale = IDENTITY


# ---------------------------------------------------------------------------
# Writes to the native Delta from outside (Ctrl+A, Alt+G/R/S, Transforms to Deltas, scripts)
#
# The drivers would silently overwrite such a write on the next evaluation, so the
# change is folded back into the layers instead. After every evaluation the drivers'
# result is flushed to the original object; `expected` remembers it, and a mismatch
# seen before the next evaluation can only come from an outside write.

expected = {}  # Object.session_uid -> delta_snapshot()


def delta_snapshot(ob):
    return (ob.delta_location[:], ob.delta_rotation_euler[:], ob.delta_rotation_quaternion[:], ob.delta_scale[:])


def _normalized_quat(q):
    # normalize_qt() turns a zero quaternion into (0, 1, 0, 0)
    q = Quaternion(q)
    return q.normalized() if q.magnitude != 0.0 else Quaternion((0.0, 1.0, 0.0, 0.0))


def absorb_delta_write(ob, old, new):
    """Change the layers so the composed Delta becomes `new`, as the native Delta would hold it.

    A channel written to identity (Ctrl+A, Alt+G/R/S) clears that channel in every layer.
    Any other write is solved into the outermost layer against the composition of the
    layers below it, read in the object's *current* rotation mode, because some
    operators (Transforms to Deltas) also change the mode in the same step.
    """
    enabled = [l for l in ob.extra_transform.layers if l.enabled]
    vals = [read_inputs(ob, l) for l in enabled]
    if not vals:
        return
    *inner, last = vals
    mode = ob.rotation_mode
    (old_loc, old_eul, old_quat, old_scale), (loc, eul, quat, scale) = old, new

    if loc != old_loc:
        if loc == IDENTITY[0]:
            for v in vals:
                v["loc"] = [0.0, 0.0, 0.0]
        else:
            last["loc"] = [n - sum(v["loc"][k] for v in inner) for k, n in enumerate(loc)]

    if scale != old_scale:
        if scale == IDENTITY[3]:
            for v in vals:
                v["scale"] = [1.0, 1.0, 1.0]
        else:
            for k, n in enumerate(scale):
                below = math.prod(v["scale"][k] for v in inner)
                if below:
                    last["scale"][k] = n / below

    is_quat = mode == 'QUATERNION'
    rot, old_rot = (quat, old_quat) if is_quat else (eul, old_eul)
    if mode != 'AXIS_ANGLE' and rot != old_rot:
        if rot == (IDENTITY[2] if is_quat else IDENTITY[1]):
            for v in vals:
                v["rot"] = [0.0, 0.0, 0.0]
                v["quat"] = [1.0, 0.0, 0.0, 0.0]
        elif not inner:
            last["quat" if is_quat else "rot"] = list(rot)
        elif is_quat:
            below = Quaternion()
            for v in inner:
                below = _normalized_quat(v["quat"]) @ below
            last["quat"] = list(_normalized_quat(quat) @ below.inverted())
        else:
            below = Matrix.Identity(3)
            for v in inner:
                below = Euler(v["rot"], mode).to_matrix() @ below
            prev = Euler(last["rot"], mode)
            last["rot"] = list((Euler(eul, mode).to_matrix() @ below.transposed()).to_euler(mode, prev))

    for l, v in zip(enabled, vals):
        write_inputs(ob, l, v)


# ---------------------------------------------------------------------------
# Driver graph


def _drive(ob, path, index, expression, variables, driver_type='SCRIPTED', use_self=False):
    fcurve = ob.driver_add(path, index)
    # driver_add() seeds a (0,0)-(1,1) curve. Interpolating through it costs an ulp,
    # so drop it and let the driver value pass through untouched.
    fcurve.keyframe_points.clear()
    driver = fcurve.driver
    driver.type = driver_type
    driver.use_self = use_self
    for name, source in variables:
        var = driver.variables.new()
        var.name = name
        var.type = 'SINGLE_PROP'
        var.targets[0].id = ob
        var.targets[0].data_path = source
    if driver_type == 'SCRIPTED':
        driver.expression = expression


def _var_names(count):
    letters = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    names = list(letters) + [a + b for a in letters for b in letters]
    return names[:count]


def clear_drivers(ob):
    ad = ob.animation_data
    if not ad:
        return
    legacy_prefix = f'["{LEGACY_GROUP}"]'
    for fc in [fc for fc in ad.drivers if fc.data_path in DELTA_PATHS or fc.data_path.startswith(legacy_prefix)]:
        ad.drivers.remove(fc)


# 1.0.0 composed rotation in a Python driver calling this function.
LEGACY_ROTATION_FUNCTION = "extra_transform_rotation"


def has_legacy_drivers(ob):
    ad = ob.animation_data
    return bool(ad) and any(fc.driver.expression.startswith(LEGACY_ROTATION_FUNCTION) for fc in ad.drivers)


def handles_rotation(ob):
    """True when the composed rotation is written by the add-on instead of a driver."""
    return ob.rotation_mode != 'AXIS_ANGLE' and sum(l.enabled for l in ob.extra_transform.layers) > 1


def scene_frame():
    scene = getattr(bpy.context, "scene", None)
    return scene.frame_current_final if scene else None


def _input_at(ob, layer, ch, frame, curves):
    # Animated components are read from their F-Curves at `frame`: frame_change_pre runs
    # before animation is evaluated, so the property still holds the previous frame.
    values = list(ob[input_key(layer, ch)])
    path = input_path(layer, ch)
    for k in range(len(values)):
        fc = curves.get((path, k))
        if fc and not fc.mute:
            values[k] = fc.evaluate(frame)
    return values


def composed_rotation(ob, frame=None):
    """(Delta property, value) for the composition of all enabled layers' rotations."""
    layers = [l for l in ob.extra_transform.layers if l.enabled]
    mode = ob.rotation_mode
    curves = {} if frame is None else {(fc.data_path, fc.array_index): fc for fc in _fcurves(ob)}
    if mode == 'QUATERNION':
        q = Quaternion()
        for l in layers:
            q = _normalized_quat(_input_at(ob, l, "quat", frame, curves)) @ q
        return "delta_rotation_quaternion", q
    m = Matrix.Identity(3)
    for l in layers:
        m = Euler(_input_at(ob, l, "rot", frame, curves), mode).to_matrix() @ m
    return "delta_rotation_euler", m.to_euler(mode)


def sync_rotation(ob, frame=None):
    """Write the composed rotation into the Delta. Returns True if it changed."""
    if not handles_rotation(ob):
        return False
    path, value = composed_rotation(ob, frame)
    target = getattr(ob, path)
    old = target[:]
    target[:] = value
    if target[:] == old:  # compare after the float32 round trip
        return False
    if ob.session_uid in expected:
        expected[ob.session_uid] = delta_snapshot(ob)
    return True


def rebuild(ob):
    """Recreate every driver from the current layer list and rotation mode.

    With two or more layers the rotation has no driver; sync_rotation() writes it.
    Every driver reads the layer inputs directly and writes a native Delta channel.
    Chaining drivers through driven custom properties is not reliable in Blender 5.2:
    under threaded evaluation a driver writing a custom property occasionally keeps
    its previous value (roughly 1 in 50 updates).
    """
    settings = ob.extra_transform
    mode = ob.rotation_mode
    settings.built_mode = mode
    expected.pop(ob.session_uid, None)

    clear_drivers(ob)
    reset_delta(ob)
    renumber(ob)
    group_fcurves(ob)
    ob.pop(LEGACY_GROUP, None)

    layers = [l for l in settings.layers if l.enabled]
    if not layers:
        return

    # Location sums, scale multiplies (channel-wise, exactly like dloc / dscale).
    names = _var_names(len(layers))
    for k in range(3):
        _drive(ob, "delta_location", k, None,
               [(n, input_paths(l, "loc")[k]) for n, l in zip(names, layers)], driver_type='SUM')
        _drive(ob, "delta_scale", k, "*".join(names),
               [(n, input_paths(l, "scale")[k]) for n, l in zip(names, layers)])

    # Blender has no RNA for the axis-angle delta, and its own Delta panel hides rotation there too.
    if mode == 'AXIS_ANGLE':
        return

    target, ch = ("delta_rotation_quaternion", "quat") if mode == 'QUATERNION' else ("delta_rotation_euler", "rot")

    # One layer: pass the input straight through so Blender itself does the conversion.
    if len(layers) == 1:
        for k, src in enumerate(input_paths(layers[0], ch)):
            _drive(ob, target, k, "v", [("v", src)])
        return

    # Several layers: the handlers keep it current from here on (see the header).
    sync_rotation(ob, scene_frame())


# ---------------------------------------------------------------------------
# Value-level composition used by Merge / Apply


def compose(outer, inner, mode):
    """Fold two layers' current values into one, applying `outer` after `inner`."""
    order = mode if mode in EULER_ORDERS else 'XYZ'
    inner_eul = Euler(inner["rot"], order)
    rot = (Euler(outer["rot"], order).to_matrix() @ inner_eul.to_matrix()).to_euler(order, inner_eul)
    quat = Quaternion(outer["quat"]).normalized() @ Quaternion(inner["quat"]).normalized()
    return {
        "loc": [a + b for a, b in zip(outer["loc"], inner["loc"])],
        "rot": list(rot),
        "quat": list(quat),
        "scale": [a * b for a, b in zip(outer["scale"], inner["scale"])],
    }


def is_editable(ob):
    return ob is not None and ob.library is None and not ob.override_library
