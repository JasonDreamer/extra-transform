import bpy
from bpy.app.handlers import persistent

from . import ops, props, stack, ui

modules = (props, ops, ui)


def _layered_objects():
    return [ob for ob in bpy.data.objects if ob.extra_transform.layers and stack.is_editable(ob)]


def _remember_evaluated_delta(depsgraph, skip=()):
    # Blender does not always flush driver results back to the original object, and the
    # outside-write check compares against the original. So copy the evaluated result
    # over (a no-op almost always) before remembering it.
    for ob in _layered_objects():
        if ob.session_uid in skip:
            continue
        try:
            ev = ob.evaluated_get(depsgraph)
        except RuntimeError:
            continue  # not part of this depsgraph's view layer
        if not ev.is_evaluated:
            continue
        snap = stack.delta_snapshot(ev)
        if stack.delta_snapshot(ob) != snap:
            ob.delta_location, ob.delta_rotation_euler, ob.delta_rotation_quaternion, ob.delta_scale = snap
        stack.expected[ob.session_uid] = snap


def _needs_rebuild(ob):
    # Layer rotations are read in the object's rotation mode, like the native Delta,
    # so the drivers have to be regenerated whenever that mode changes. Layers whose
    # property names lag behind their position (files from 0.1.0) are caught here too.
    settings = ob.extra_transform
    return settings.built_mode != ob.rotation_mode or any(
        l.slot != i + 1 for i, l in enumerate(settings.layers))


# Objects changed from depsgraph_update_pre. A tag set there is not guaranteed to make
# the evaluation that follows, so post re-tags them and leaves them out of `expected`
# until an evaluation has really used the new values.
_changed_in_pre = set()


@persistent
def _on_depsgraph_update_pre(scene, depsgraph):
    for ob in _layered_objects():
        seen = stack.expected.get(ob.session_uid)
        if seen is None and not _needs_rebuild(ob):
            continue
        now = stack.delta_snapshot(ob)
        if _needs_rebuild(ob):
            stack.rebuild(ob)
            _changed_in_pre.add(ob.session_uid)
        if seen is not None and now != seen:
            stack.absorb_delta_write(ob, seen, now)
            stack.expected.pop(ob.session_uid, None)
            _changed_in_pre.add(ob.session_uid)


@persistent
def _on_depsgraph_update_post(scene, depsgraph):
    stale = set(_changed_in_pre)
    _changed_in_pre.clear()
    for ob in _layered_objects():
        if _needs_rebuild(ob):
            stack.rebuild(ob)
            stale.add(ob.session_uid)
        elif ob.session_uid in stale:
            ob.update_tag()
        else:
            # Keyframes inserted from the UI land outside any group.
            stack.group_fcurves(ob)
    _remember_evaluated_delta(depsgraph, skip=stale)


@persistent
def _on_frame_change_post(scene, depsgraph):
    _remember_evaluated_delta(depsgraph)


@persistent
def _forget(*args):
    # Undo and file loads replace the original data wholesale; nothing seen before is valid.
    stack.expected.clear()


@persistent
def _install_driver_function(*args):
    bpy.app.driver_namespace[stack.ROTATION_FUNCTION] = stack.evaluate_rotation


HANDLERS = (
    ("depsgraph_update_pre", _on_depsgraph_update_pre),
    ("depsgraph_update_post", _on_depsgraph_update_post),
    ("frame_change_post", _on_frame_change_post),
    ("undo_pre", _forget),
    ("redo_pre", _forget),
    ("undo_post", _forget),
    ("redo_post", _forget),
    ("load_pre", _forget),
    ("load_post", _install_driver_function),
)


def register():
    for m in modules:
        m.register()
    _install_driver_function()
    for name, fn in HANDLERS:
        getattr(bpy.app.handlers, name).append(fn)


def unregister():
    for name, fn in HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if fn in handlers:
            handlers.remove(fn)
    bpy.app.driver_namespace.pop(stack.ROTATION_FUNCTION, None)
    stack.expected.clear()
    for m in reversed(modules):
        m.unregister()
