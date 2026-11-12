# Run: blender -b --factory-startup --python tests/test_parity.py
# Compares the add-on against the native Delta Transform and against the closed-form
# composition T(loc + sum dloc) * (R_n ... R_1 * R) * S(scale * prod dscale).

import math
import os
import random
import sys
import traceback

import bpy
from mathutils import Euler, Matrix, Quaternion

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import extra_transform  # noqa: E402
from extra_transform import stack  # noqa: E402

extra_transform.register()

MODES = ('XYZ', 'XZY', 'YXZ', 'YZX', 'ZXY', 'ZYX', 'QUATERNION')
TOL = 2e-5
rng = random.Random(7)
failures = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def update():
    bpy.context.view_layer.update()


def evaluated(ob):
    return ob.evaluated_get(bpy.context.evaluated_depsgraph_get())


def diff(a, b):
    return max(abs(a[r][c] - b[r][c]) for r in range(4) for c in range(4))


def new_object(mode='XYZ'):
    bpy.ops.mesh.primitive_cube_add()
    ob = bpy.context.object
    ob.rotation_mode = mode
    return ob


def rand_values(weighted=False):
    return {
        "loc": [rng.uniform(-5, 5) for _ in range(3)],
        "rot": [rng.uniform(-7, 7) for _ in range(3)],
        "quat": [rng.uniform(-1, 1) for _ in range(4)],
        "scale": [rng.uniform(-2, 2) or 1.0 for _ in range(3)],
        "influence": rng.uniform(0.2, 1.0) if weighted else 1.0,
    }


def layer_values(o, layer):
    """A layer's inputs as read from an original or evaluated object."""
    return {ch: (list(o[stack.input_key(layer, ch)]) if stack.is_array(ch) else o[stack.input_key(layer, ch)])
            for ch in stack.INPUTS}


def randomize_base(ob):
    v = rand_values()
    ob.location, ob.rotation_euler, ob.rotation_quaternion, ob.scale = v["loc"], v["rot"], v["quat"], v["scale"]


def add_layer(ob, values):
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.extra_transform_add()
    layer = ob.extra_transform.layers[-1]
    stack.write_inputs(ob, layer, values)
    return layer


def norm_quat(q):
    # normalize_qt() fallback for a zero quaternion
    q = Quaternion(q)
    return q.normalized() if q.magnitude != 0 else Quaternion((0, 1, 0, 0))


def layer_matrix(values, mode):
    # Influence blends each value from identity, like an NLA strip in Replace mode.
    w = values.get("influence", 1.0)
    if mode == 'QUATERNION':
        q = values["quat"]
        return norm_quat([1 - w + q[0] * w] + [c * w for c in q[1:]]).to_matrix()
    return Euler([a * w for a in values["rot"]], mode).to_matrix()


def reference(ob, values_per_layer):
    mode = ob.rotation_mode
    base = {"rot": ob.rotation_euler[:], "quat": ob.rotation_quaternion[:]}
    rot = layer_matrix(base, mode)
    loc = ob.location.copy()
    scale = ob.scale.copy()
    for v in values_per_layer:
        w = v.get("influence", 1.0)
        rot = layer_matrix(v, mode) @ rot
        loc += type(loc)([a * w for a in v["loc"]])
        scale = type(scale)([a * (1 - w + b * w) for a, b in zip(scale, v["scale"])])
    return Matrix.LocRotScale(loc, rot, scale)


def native_twin(ob, values):
    twin = new_object(ob.rotation_mode)
    twin.location, twin.rotation_euler, twin.rotation_quaternion, twin.scale = (
        ob.location, ob.rotation_euler, ob.rotation_quaternion, ob.scale)
    twin.delta_location = values["loc"]
    twin.delta_rotation_euler = values["rot"]
    twin.delta_rotation_quaternion = values["quat"]
    twin.delta_scale = values["scale"]
    return twin


def test(fn):
    try:
        fn()
    except Exception:
        traceback.print_exc()
        failures.append(fn.__name__)
    return fn


@test
def single_layer_matches_native_delta():
    for mode in MODES:
        worst = 0.0
        for _ in range(20):
            ob = new_object(mode)
            randomize_base(ob)
            values = rand_values()
            add_layer(ob, values)
            twin = native_twin(ob, values)
            update()
            worst = max(worst, diff(evaluated(ob).matrix_world, evaluated(twin).matrix_world))
        check(f"single layer == native delta [{mode}]", worst == 0.0, f"max diff {worst:.3g}")


@test
def zero_quaternion_matches_native():
    ob = new_object('QUATERNION')
    values = {"loc": [0] * 3, "rot": [0] * 3, "quat": [0, 0, 0, 0], "scale": [1] * 3}
    add_layer(ob, values)
    twin = native_twin(ob, values)
    update()
    check("zero quaternion == native", diff(evaluated(ob).matrix_world, evaluated(twin).matrix_world) == 0.0)
    add_layer(ob, rand_values())
    update()
    vals = [stack.read_inputs(ob, l) for l in ob.extra_transform.layers]
    check("zero quaternion in a stack", diff(evaluated(ob).matrix_basis, reference(ob, vals)) < TOL)


@test
def stacks_match_closed_form():
    for mode in MODES:
        worst = 0.0
        for _ in range(20):
            ob = new_object(mode)
            randomize_base(ob)
            for _ in range(rng.randint(2, 6)):
                add_layer(ob, rand_values(weighted=rng.random() < 0.7))
            for layer in ob.extra_transform.layers:
                layer.enabled = rng.random() > 0.2
            update()
            vals = [stack.read_inputs(ob, l) for l in ob.extra_transform.layers if l.enabled]
            worst = max(worst, diff(evaluated(ob).matrix_basis, reference(ob, vals)))
        check(f"stack == closed form [{mode}]", worst < TOL, f"max diff {worst:.3g}")


@test
def all_drivers_are_simple_and_single_hop():
    # Anything but a simple expression is blocked in files opened with auto-execution off.
    bad = []
    for ob in bpy.data.objects:
        if ob.animation_data:
            for fc in ob.animation_data.drivers:
                d = fc.driver
                simple = d.type == 'SUM' or d.is_simple_expression
                reads_driven = any(f'["{stack.LEGACY_GROUP}"]' in v.targets[0].data_path for v in d.variables)
                if not (d.is_valid and simple and fc.data_path in stack.DELTA_PATHS and not reads_driven):
                    bad.append((ob.name, fc.data_path, d.type, d.expression[:40]))
    check("every driver is a simple expression writing a native Delta channel", not bad, str(bad[:3]))


@test
def animation_has_no_frame_lag():
    ob = new_object('YXZ')
    add_layer(ob, rand_values())
    add_layer(ob, rand_values())
    a, b = ob.extra_transform.layers
    key = stack.input_key(a, "rot")
    for frame, val in ((1, 0.0), (20, 6.0)):
        ob[key] = [val, val * 0.5, -val]
        ob.keyframe_insert(f'["{key}"]', frame=frame)
    key = stack.input_key(b, "loc")
    for frame, val in ((1, 0.0), (20, 3.0)):
        ob[key] = [val, 0, 0]
        ob.keyframe_insert(f'["{key}"]', frame=frame)
    worst = 0.0
    for frame in (1, 7, 13, 20, 4):
        bpy.context.scene.frame_set(frame)
        ev = evaluated(ob)
        vals = [layer_values(ev, l) for l in ob.extra_transform.layers]
        worst = max(worst, diff(ev.matrix_basis, reference(ob, vals)))
    check("animated layers evaluate on the same frame", worst < TOL, f"max diff {worst:.3g}")


@test
def rotation_mode_change_rebuilds():
    ob = new_object('XYZ')
    add_layer(ob, rand_values())
    add_layer(ob, rand_values())
    update()
    for mode in ('ZXY', 'QUATERNION', 'XZY'):
        ob.rotation_mode = mode
        update()
        update()
        vals = [stack.read_inputs(ob, l) for l in ob.extra_transform.layers]
        d = diff(evaluated(ob).matrix_basis, reference(ob, vals))
        check(f"rotation mode -> {mode} rebuilds drivers", ob.extra_transform.built_mode == mode and d < TOL,
              f"built={ob.extra_transform.built_mode} diff={d:.3g}")


@test
def reorder_and_remove():
    ob = new_object('XYZ')
    for _ in range(4):
        add_layer(ob, rand_values())
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.extra_transform_move(index=0, direction='DOWN')
    bpy.ops.object.extra_transform_remove(index=2)
    update()
    vals = [stack.read_inputs(ob, l) for l in ob.extra_transform.layers]
    check("move + remove", len(vals) == 3 and diff(evaluated(ob).matrix_basis, reference(ob, vals)) < TOL)
    check("no intermediate storage is left on the object", stack.LEGACY_GROUP not in ob)


@test
def merge_up_keeps_result():
    for mode in ('ZYX', 'QUATERNION'):
        ob = new_object(mode)
        randomize_base(ob)
        for _ in range(3):
            add_layer(ob, rand_values(weighted=True))
        update()
        before = evaluated(ob).matrix_world.copy()
        bpy.context.view_layer.objects.active = ob
        bpy.ops.object.extra_transform_merge_up(index=2)
        update()
        d = diff(before, evaluated(ob).matrix_world)
        check(f"merge up keeps matrix [{mode}]", len(ob.extra_transform.layers) == 2 and d < TOL, f"{d:.3g}")


@test
def apply_and_bake_keep_result():
    for mode in ('YZX', 'QUATERNION'):
        for op in ("extra_transform_apply", "extra_transform_bake_delta"):
            ob = new_object(mode)
            randomize_base(ob)
            for _ in range(3):
                add_layer(ob, rand_values(weighted=True))
            update()
            before = evaluated(ob).matrix_world.copy()
            bpy.context.view_layer.objects.active = ob
            getattr(bpy.ops.object, op)()
            update()
            d = diff(before, evaluated(ob).matrix_world)
            clean = not ob.extra_transform.layers and stack.LEGACY_GROUP not in ob and not any(
                k.startswith("et_") for k in ob.keys())
            check(f"{op} keeps matrix [{mode}]", d < TOL and clean, f"{d:.3g}")


@test
def existing_delta_is_migrated():
    ob = new_object('XYZ')
    ob.delta_location = (1, 2, 3)
    ob.delta_rotation_euler = (0.3, 0.2, 0.1)
    ob.delta_scale = (2, 2, 2)
    ob.keyframe_insert("delta_location", index=0, frame=1)
    ob.delta_location[0] = 5
    ob.keyframe_insert("delta_location", index=0, frame=10)
    twin = new_object('XYZ')
    twin.delta_rotation_euler = (0.3, 0.2, 0.1)
    twin.delta_scale = (2, 2, 2)
    twin.delta_location = (1, 2, 3)
    twin.keyframe_insert("delta_location", index=0, frame=1)
    twin.delta_location[0] = 5
    twin.keyframe_insert("delta_location", index=0, frame=10)

    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.extra_transform_add()
    layers = ob.extra_transform.layers
    check("delta migrated into a layer", len(layers) == 2 and layers[0].name == "Delta")
    worst = 0.0
    for frame in (1, 4, 10):
        bpy.context.scene.frame_set(frame)
        worst = max(worst, diff(evaluated(ob).matrix_world, evaluated(twin).matrix_world))
    check("migrated delta animation still plays", worst < TOL, f"{worst:.3g}")


@test
def duplicate_object_has_own_drivers():
    ob = new_object('XYZ')
    add_layer(ob, rand_values())
    add_layer(ob, rand_values())
    bpy.ops.object.select_all(action='DESELECT')
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.duplicate()
    dup = bpy.context.object
    targets = {v.targets[0].id for fc in dup.animation_data.drivers for v in fc.driver.variables}
    check("duplicate drivers point at the duplicate", targets == {dup})
    stack.write_inputs(dup, dup.extra_transform.layers[0], rand_values())
    update()
    vals = [stack.read_inputs(dup, l) for l in dup.extra_transform.layers]
    check("duplicate evaluates independently", diff(evaluated(dup).matrix_basis, reference(dup, vals)) < TOL)


def world_points(ob):
    ev = evaluated(ob)
    return [ev.matrix_world @ v.co for v in ev.data.vertices]


def points_diff(a, b):
    return max((p - q).length for p, q in zip(a, b))


NATIVE_OPS = {
    "Ctrl+A rotation & scale": lambda: bpy.ops.object.transform_apply(location=False, rotation=True, scale=True),
    "Ctrl+A all": lambda: bpy.ops.object.transform_apply(location=True, rotation=True, scale=True),
    "Ctrl+A location": lambda: bpy.ops.object.transform_apply(location=True, rotation=False, scale=False),
    "Alt+G clear delta": lambda: bpy.ops.object.location_clear(clear_delta=True),
    "Alt+R clear delta": lambda: bpy.ops.object.rotation_clear(clear_delta=True),
    "Alt+S clear delta": lambda: bpy.ops.object.scale_clear(clear_delta=True),
    "All Transforms to Deltas": lambda: bpy.ops.object.transforms_to_deltas(mode='ALL'),
    "Location to Deltas": lambda: bpy.ops.object.transforms_to_deltas(mode='LOC'),
    "Rotation to Deltas": lambda: bpy.ops.object.transforms_to_deltas(mode='ROT'),
    "Scale to Deltas": lambda: bpy.ops.object.transforms_to_deltas(mode='SCALE'),
}


def run_on(ob, op):
    bpy.ops.object.select_all(action='DESELECT')
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    op()
    update()


@test
def native_operators_behave_like_native_delta():
    # The twin carries the composed result in a real Delta. Running the same operator on
    # both must leave them identical, before and after editing the layers again.
    for mode in ('XYZ', 'ZXY', 'QUATERNION'):
        for layer_count in (1, 3):
            for name, op in NATIVE_OPS.items():
                ob = new_object(mode)
                randomize_base(ob)
                ob.scale = [abs(s) + 0.5 for s in ob.scale]
                for _ in range(layer_count):
                    v = rand_values(weighted=True)
                    v["scale"] = [abs(s) + 0.5 for s in v["scale"]]
                    add_layer(ob, v)
                update()
                ev = evaluated(ob)
                twin = native_twin(ob, {"loc": ev.delta_location[:], "rot": ev.delta_rotation_euler[:],
                                        "quat": ev.delta_rotation_quaternion[:], "scale": ev.delta_scale[:]})
                update()
                run_on(ob, op)
                run_on(twin, op)
                d = points_diff(world_points(ob), world_points(twin))
                ev, tv = evaluated(ob), evaluated(twin)
                # Equal rotations may be spelled with different euler triples, so compare matrices.
                def delta_matrix(o):
                    m = o.rotation_mode
                    rot = (norm_quat(o.delta_rotation_quaternion) if m == 'QUATERNION'
                           else Euler(o.delta_rotation_euler, m)).to_matrix()
                    return Matrix.LocRotScale(o.delta_location, rot, o.delta_scale)
                same_mode = ob.rotation_mode == twin.rotation_mode
                dd = diff(delta_matrix(ev), delta_matrix(tv))
                check(f"{name} [{mode}, {layer_count} layers]", d < 1e-4 and dd < 1e-4 and same_mode,
                      f"points {d:.3g} delta {dd:.3g}")


@test
def playback_and_edits_do_not_trigger_absorb():
    ob = new_object('XYZ')
    add_layer(ob, rand_values())
    add_layer(ob, rand_values())
    a = ob.extra_transform.layers[0]
    animated_uid = a.uid
    key = stack.input_key(a, "loc")
    for frame, val in ((1, 0.0), (20, 4.0)):
        ob[key] = [val, val, val]
        ob.keyframe_insert(f'["{key}"]', frame=frame)
    for frame in (1, 5, 9, 14):
        bpy.context.scene.frame_set(frame)
    before = {l.uid: stack.read_inputs(ob, l) for l in ob.extra_transform.layers}
    ob.location.x += 1.0
    update()
    other = new_object('XYZ')
    other.location.y = 3
    update()
    bpy.context.scene.frame_set(17)
    ob.rotation_euler.z += 0.3
    update()
    after = {l.uid: stack.read_inputs(ob, l) for l in ob.extra_transform.layers}
    unanimated = [u for u in before if u != animated_uid]
    check("playback and base edits leave layer values alone", all(before[u] == after[u] for u in unanimated))


@test
def remove_last_layer_resets_delta():
    ob = new_object('XYZ')
    add_layer(ob, rand_values())
    update()
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.extra_transform_remove(index=0)
    update()
    check("removing every layer leaves an identity delta",
          stack.delta_snapshot(evaluated(ob)) == stack.IDENTITY and stack.LEGACY_GROUP not in ob)


@test
def edits_and_playback_never_drop_a_value():
    # Regression for driver chains through custom properties, which lost about one
    # update in fifty under threaded evaluation. Every update is checked here.
    worst, misses = 0.0, 0
    for mode in MODES:
        for _ in range(25):
            ob = new_object(mode)
            randomize_base(ob)
            for _ in range(rng.randint(1, 4)):
                add_layer(ob, rand_values())
            update()
            for layer in ob.extra_transform.layers:
                stack.write_inputs(ob, layer, rand_values(weighted=True))
            update()
            vals = [stack.read_inputs(ob, l) for l in ob.extra_transform.layers]
            d = diff(evaluated(ob).matrix_basis, reference(ob, vals))
            worst = max(worst, d)
            misses += d >= TOL
    check("edits are always picked up", misses == 0, f"{misses} misses, max diff {worst:.3g}")

    obs = []
    for mode in MODES:
        ob = new_object(mode)
        for _ in range(3):
            add_layer(ob, rand_values())
        for layer in ob.extra_transform.layers:
            for ch in stack.INPUTS:
                key = stack.input_key(layer, ch)
                for frame in (1, 10, 20):
                    stack.write_inputs(ob, layer, {ch: rand_values(weighted=True)[ch]})
                    ob.keyframe_insert(f'["{key}"]', frame=frame)
        obs.append(ob)
    misses = 0
    for frame in list(range(1, 21)) * 5:
        bpy.context.scene.frame_set(frame)
        for ob in obs:
            ev = evaluated(ob)
            vals = [layer_values(ev, l) for l in ob.extra_transform.layers]
            misses += diff(ev.matrix_basis, reference(ob, vals)) >= TOL
    check("playback is always picked up", misses == 0, f"{misses} misses of {100 * len(obs)}")
    bpy.context.scene.frame_set(1)


def names(ob):
    return sorted(k for k in ob.keys() if k != stack.LEGACY_GROUP)


@test
def property_names_follow_stack_position():
    ob = new_object('XYZ')
    for _ in range(3):
        add_layer(ob, rand_values())
    expected_names = sorted(f"Extra {n} {label}" for n in (1, 2, 3) for label in stack.LABELS.values())
    check("properties are named Extra N <channel>", names(ob) == expected_names, str(names(ob)[:4]))

    # Animate the last layer, move it to the top, then delete the middle one.
    last = ob.extra_transform.layers[2]
    uid = last.uid
    key = stack.input_key(last, "loc")
    for frame, val in ((1, 0.0), (10, 5.0)):
        ob[key][0] = val
        ob.keyframe_insert(f'["{key}"]', index=0, frame=frame)
    update()
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.extra_transform_move(index=2, direction='UP')
    bpy.ops.object.extra_transform_move(index=1, direction='UP')
    layers = ob.extra_transform.layers
    check("moved layer is renamed to Extra 1", layers[0].uid == uid and stack.input_key(layers[0], "loc") == "Extra 1 Location")
    bpy.ops.object.extra_transform_remove(index=1)
    check("names close the gap after removal",
          names(ob) == sorted(f"Extra {n} {label}" for n in (1, 2) for label in stack.LABELS.values()))

    bag = stack._channelbag(ob)
    paths = [fc.data_path for fc in bag.fcurves]
    check("animation follows the renamed property", paths == ['["Extra 1 Location"]'], str(paths))
    bpy.context.scene.frame_set(10)
    check("renamed animation still plays", abs(evaluated(ob)["Extra 1 Location"][0] - 5.0) < 1e-6)
    vals = [stack.read_inputs(ob, l) for l in ob.extra_transform.layers]
    check("stack still evaluates after renames", diff(evaluated(ob).matrix_basis, reference(ob, vals)) < TOL)
    bpy.context.scene.frame_set(1)


@test
def keyed_channels_live_in_object_transforms():
    ob = new_object('XYZ')
    add_layer(ob, rand_values())
    ob.keyframe_insert("location", frame=1)
    layer = ob.extra_transform.layers[0]
    ob.keyframe_insert(stack.input_path(layer, "rot"), frame=1)
    ob.keyframe_insert(stack.input_path(layer, "scale"), index=1, frame=1)
    update()
    bag = stack._channelbag(ob)
    ours = [fc for fc in bag.fcurves if fc.data_path.startswith('["Extra')]
    check("layer F-Curves are grouped under Object Transforms",
          len(ours) == 4 and all(fc.group and fc.group.name == stack.FCURVE_GROUP for fc in ours),
          str([(fc.data_path, fc.group.name if fc.group else None) for fc in ours]))


@test
def files_from_0_1_0_are_renamed():
    # 0.1.0 stored inputs as et_<uid>_<channel> with no slot number.
    ob = new_object('XYZ')
    add_layer(ob, rand_values())
    add_layer(ob, rand_values())
    for layer in ob.extra_transform.layers:
        stack._rename_inputs(ob, layer, 0)
    key = stack.input_key(ob.extra_transform.layers[1], "rot")
    ob.keyframe_insert(f'["{key}"]', frame=1)
    check("legacy names set up", any(k.startswith("et_") for k in ob.keys()))
    update()
    update()
    vals = [stack.read_inputs(ob, l) for l in ob.extra_transform.layers]
    paths = [fc.data_path for fc in stack._channelbag(ob).fcurves]
    check("legacy names migrate on the next update",
          not any(k.startswith("et_") for k in ob.keys()) and paths == ['["Extra 2 Euler Rotation"]'] * 3
          and diff(evaluated(ob).matrix_basis, reference(ob, vals)) < TOL, str(paths))


@test
def influence_scales_a_layer_like_a_weaker_delta():
    # A single layer at influence w must equal a native Delta holding the blended values.
    for mode in MODES:
        worst = 0.0
        for _ in range(10):
            ob = new_object(mode)
            randomize_base(ob)
            v = rand_values(weighted=True)
            add_layer(ob, v)
            w = v["influence"]
            q = v["quat"]
            twin = native_twin(ob, {"loc": [a * w for a in v["loc"]], "rot": [a * w for a in v["rot"]],
                                    "quat": [1 - w + q[0] * w] + [c * w for c in q[1:]],
                                    "scale": [1 - w + a * w for a in v["scale"]]})
            update()
            worst = max(worst, diff(evaluated(ob).matrix_world, evaluated(twin).matrix_world))
        check(f"single layer at partial influence == blended native delta [{mode}]", worst < TOL, f"{worst:.3g}")

    ob = new_object('XYZ')
    randomize_base(ob)
    add_layer(ob, rand_values())
    add_layer(ob, rand_values())
    for layer in ob.extra_transform.layers:
        stack.write_inputs(ob, layer, {"influence": 0.0})
    update()
    check("influence 0 everywhere leaves an identity delta", stack.delta_snapshot(evaluated(ob)) == stack.IDENTITY)

    ui = ob.id_properties_ui(stack.input_key(ob.extra_transform.layers[0], "influence")).as_dict()
    check("influence is a 0..1 factor", ui["subtype"] == 'FACTOR' and ui["min"] == 0.0 and ui["max"] == 1.0, str(ui))


@test
def layers_from_1_0_gain_an_influence():
    ob = new_object('ZYX')
    add_layer(ob, rand_values())
    add_layer(ob, rand_values())
    for layer in ob.extra_transform.layers:
        del ob[stack.input_key(layer, "influence")]
    update()
    update()
    vals = [stack.read_inputs(ob, l) for l in ob.extra_transform.layers]
    check("missing influence is created at 1",
          all(v["influence"] == 1.0 for v in vals) and diff(evaluated(ob).matrix_basis, reference(ob, vals)) < TOL)


@test
def max_layers_fit_the_driver_expressions():
    ob = new_object('QUATERNION')
    bpy.context.view_layer.objects.active = ob
    for _ in range(stack.MAX_LAYERS):
        bpy.ops.object.extra_transform_add()
    try:
        bpy.ops.object.extra_transform_add()
        refused = False
    except RuntimeError:  # the operator reports an error
        refused = True
    update()
    longest = max(len(fc.driver.expression) for fc in ob.animation_data.drivers)
    valid = all(fc.driver.is_valid and fc.driver.is_simple_expression for fc in ob.animation_data.drivers)
    check(f"{stack.MAX_LAYERS} layers fit, one more is refused",
          len(ob.extra_transform.layers) == stack.MAX_LAYERS and refused and longest <= 255 and valid,
          f"longest expression {longest}")


CHILD = """
import json, os, sys, bpy
if WITH_ADDON:
    sys.path.insert(0, ROOT)
    import extra_transform
    extra_transform.register()
bpy.context.view_layer.update()
dg = bpy.context.evaluated_depsgraph_get()
out = {"autoexec_fail": bpy.app.autoexec_fail,
       "m": {n: [list(r) for r in bpy.data.objects[n].evaluated_get(dg).matrix_world] for n in NAMES}}
print("RESULT " + json.dumps(out))
sys.stdout.flush()
os._exit(0)
"""


@test
def files_open_cleanly_with_auto_execution_off():
    import json
    import subprocess
    import tempfile
    obs = []
    for mode in ('XYZ', 'ZXY', 'QUATERNION'):
        for count in (1, 3):
            ob = new_object(mode)
            randomize_base(ob)
            for _ in range(count):
                add_layer(ob, rand_values())
            obs.append(ob)
    update()
    want = {ob.name: evaluated(ob).matrix_world.copy() for ob in obs}
    path = os.path.join(tempfile.mkdtemp(), "roundtrip.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for with_addon in (True, False):
        label = "with add-on" if with_addon else "without add-on"
        expr = f"WITH_ADDON={with_addon}\nROOT={root!r}\nNAMES={list(want)!r}\n" + CHILD
        run = subprocess.run([bpy.app.binary_path, "-b", "--factory-startup", "--disable-autoexec", path,
                              "--python-expr", expr], capture_output=True, text=True, timeout=120)
        line = next((l for l in run.stdout.splitlines() if l.startswith("RESULT ")), None)
        if line is None:
            check(f"reopened file reports [{label}]", False, run.stdout[-300:] + run.stderr[-300:])
            continue
        out = json.loads(line[7:])
        worst = max(diff(want[n], out["m"][n]) for n in want)
        blocked = out["autoexec_fail"] or "restricted access" in run.stdout + run.stderr
        check(f"no auto-execution warning, same pose [{label}]", not blocked and worst < TOL,
              f"blocked={blocked} max diff {worst:.3g}")


@test
def files_from_1_0_0_lose_the_python_driver():
    ob = new_object('YZX')
    add_layer(ob, rand_values())
    add_layer(ob, rand_values())
    stack.reset_delta(ob)
    sources = [p for l in ob.extra_transform.layers for p in stack.input_paths(l, "rot")]
    for k in range(3):
        stack._drive(ob, "delta_rotation_euler", k, f"{stack.LEGACY_ROTATION_FUNCTION}(self,{k})",
                     [(f"v{n}", p) for n, p in enumerate(sources)], use_self=True)
    check("1.0.0 driver set up", stack.has_legacy_drivers(ob))
    extra_transform._upgrade()
    update()
    vals = [stack.read_inputs(ob, l) for l in ob.extra_transform.layers]
    check("1.0.0 rotation driver is replaced on load",
          not stack.has_legacy_drivers(ob) and diff(evaluated(ob).matrix_basis, reference(ob, vals)) < TOL)


@test
def rendered_frames_use_the_animated_rotation():
    import tempfile
    scene = bpy.context.scene
    ob = new_object('ZXY')
    randomize_base(ob)
    add_layer(ob, rand_values())
    add_layer(ob, rand_values())
    key = stack.input_key(ob.extra_transform.layers[1], "rot")
    for frame, val in ((1, 0.0), (4, 2.5)):
        ob[key] = [val, -val, val * 0.5]
        ob.keyframe_insert(f'["{key}"]', frame=frame)
    scene.frame_set(1)
    rendered = {}

    def grab(scene, depsgraph):
        ev = ob.evaluated_get(depsgraph)
        vals = [layer_values(ev, l) for l in ob.extra_transform.layers]
        if depsgraph.mode == 'RENDER':  # the viewport also returns to the start frame afterwards
            rendered[scene.frame_current] = diff(ev.matrix_basis, reference(ob, vals))

    render = scene.render
    saved = (render.engine, render.resolution_x, render.resolution_y, scene.frame_start, scene.frame_end, render.filepath)
    render.engine = 'BLENDER_WORKBENCH'
    render.resolution_x = render.resolution_y = 8
    scene.frame_start, scene.frame_end = 1, 4
    render.filepath = os.path.join(tempfile.mkdtemp(), "f")
    bpy.app.handlers.frame_change_post.append(grab)
    try:
        bpy.ops.render.render(animation=True)
    finally:
        bpy.app.handlers.frame_change_post.remove(grab)
        (render.engine, render.resolution_x, render.resolution_y,
         scene.frame_start, scene.frame_end, render.filepath) = saved
    worst = max(rendered.values(), default=1.0)
    check("each rendered frame has its own rotation", sorted(rendered) == [1, 2, 3, 4] and worst < TOL,
          f"frames {sorted(rendered)} max diff {worst:.3g}")
    scene.frame_set(1)


print(f"\n{len(failures)} failure(s)" + (f": {failures}" if failures else ""))
sys.stdout.flush()
os._exit(1 if failures else 0)
