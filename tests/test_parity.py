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


def rand_values():
    return {
        "loc": [rng.uniform(-5, 5) for _ in range(3)],
        "rot": [rng.uniform(-7, 7) for _ in range(3)],
        "quat": [rng.uniform(-1, 1) for _ in range(4)],
        "scale": [rng.uniform(-2, 2) or 1.0 for _ in range(3)],
    }


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
    if mode == 'QUATERNION':
        return norm_quat(values["quat"]).to_matrix()
    return Euler(values["rot"], mode).to_matrix()


def reference(ob, layer_values):
    mode = ob.rotation_mode
    base = {"rot": ob.rotation_euler[:], "quat": ob.rotation_quaternion[:]}
    rot = layer_matrix(base, mode)
    loc = ob.location.copy()
    scale = ob.scale.copy()
    for v in layer_values:
        rot = layer_matrix(v, mode) @ rot
        loc += type(loc)(v["loc"])
        scale = type(scale)([a * b for a, b in zip(scale, v["scale"])])
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
                add_layer(ob, rand_values())
            for layer in ob.extra_transform.layers:
                layer.enabled = rng.random() > 0.2
            update()
            vals = [stack.read_inputs(ob, l) for l in ob.extra_transform.layers if l.enabled]
            worst = max(worst, diff(evaluated(ob).matrix_basis, reference(ob, vals)))
        check(f"stack == closed form [{mode}]", worst < TOL, f"max diff {worst:.3g}")


@test
def all_drivers_are_valid_and_single_hop():
    # Runs with Python auto-execution off (factory default), so a namespace-function
    # driver that Blender refused would show up as invalid here.
    bad = []
    for ob in bpy.data.objects:
        if ob.animation_data:
            for fc in ob.animation_data.drivers:
                d = fc.driver
                calls_function = d.expression.startswith(stack.ROTATION_FUNCTION)
                simple = d.type == 'SUM' or d.is_simple_expression or calls_function
                reads_driven = any(f'["{stack.LEGACY_GROUP}"]' in v.targets[0].data_path for v in d.variables)
                if not (d.is_valid and simple and fc.data_path in stack.DELTA_PATHS and not reads_driven):
                    bad.append((ob.name, fc.data_path, d.type, d.expression[:40]))
    check("every driver is valid and writes a native Delta channel", not bad, str(bad[:3]))


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
        vals = [{ch: list(ev[stack.input_key(l, ch)]) for ch in stack.INPUTS} for l in ob.extra_transform.layers]
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
            add_layer(ob, rand_values())
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
                add_layer(ob, rand_values())
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
                    v = rand_values()
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
                stack.write_inputs(ob, layer, rand_values())
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
            for ch in ("loc", "rot", "quat", "scale"):
                key = stack.input_key(layer, ch)
                for frame in (1, 10, 20):
                    stack.write_inputs(ob, layer, {ch: rand_values()[ch]})
                    ob.keyframe_insert(f'["{key}"]', frame=frame)
        obs.append(ob)
    misses = 0
    for frame in list(range(1, 21)) * 5:
        bpy.context.scene.frame_set(frame)
        for ob in obs:
            ev = evaluated(ob)
            vals = [{ch: list(ev[stack.input_key(l, ch)]) for ch in stack.INPUTS} for l in ob.extra_transform.layers]
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


print(f"\n{len(failures)} failure(s)" + (f": {failures}" if failures else ""))
sys.stdout.flush()
os._exit(1 if failures else 0)
