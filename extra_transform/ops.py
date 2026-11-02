import bpy
from bpy.props import EnumProperty, IntProperty
from mathutils import Euler

from . import stack


class _ObjectOp:
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return stack.is_editable(context.object)


class _LayerOp(_ObjectOp):
    index: IntProperty(default=-1, options={'SKIP_SAVE'})

    def layer_index(self, context):
        settings = context.object.extra_transform
        i = self.index if self.index >= 0 else settings.active_index
        return i if 0 <= i < len(settings.layers) else None


class OBJECT_OT_extra_transform_add(_ObjectOp, bpy.types.Operator):
    bl_idname = "object.extra_transform_add"
    bl_label = "Add Transform"
    bl_description = "Stack a new Delta Transform layer on the object"

    def execute(self, context):
        ob = context.object
        if len(ob.extra_transform.layers) >= stack.MAX_LAYERS:
            self.report({'ERROR'}, f"An object can hold up to {stack.MAX_LAYERS} transforms")
            return {'CANCELLED'}
        if not ob.extra_transform.layers and stack.delta_is_used(ob):
            stack.migrate_delta(ob)
        stack.new_layer(ob, "Transform")
        stack.rebuild(ob)
        return {'FINISHED'}


class OBJECT_OT_extra_transform_remove(_LayerOp, bpy.types.Operator):
    bl_idname = "object.extra_transform_remove"
    bl_label = "Remove Transform"
    bl_description = "Remove the layer and its animation"

    def execute(self, context):
        i = self.layer_index(context)
        if i is None:
            return {'CANCELLED'}
        stack.remove_layer(context.object, i)
        stack.rebuild(context.object)
        return {'FINISHED'}


class OBJECT_OT_extra_transform_move(_LayerOp, bpy.types.Operator):
    bl_idname = "object.extra_transform_move"
    bl_label = "Move Transform"
    bl_description = "Move the layer inward (up) or outward (down) in the stack"

    direction: EnumProperty(items=(('UP', "Up", ""), ('DOWN', "Down", "")))

    def execute(self, context):
        settings = context.object.extra_transform
        i = self.layer_index(context)
        if i is None:
            return {'CANCELLED'}
        j = i - 1 if self.direction == 'UP' else i + 1
        if not 0 <= j < len(settings.layers):
            return {'CANCELLED'}
        settings.layers.move(i, j)
        settings.active_index = j
        stack.rebuild(context.object)
        return {'FINISHED'}


class OBJECT_OT_extra_transform_duplicate(_LayerOp, bpy.types.Operator):
    bl_idname = "object.extra_transform_duplicate"
    bl_label = "Duplicate Transform"
    bl_description = "Copy the layer's current values into a new layer right after it"

    def execute(self, context):
        ob = context.object
        i = self.layer_index(context)
        if i is None:
            return {'CANCELLED'}
        if len(ob.extra_transform.layers) >= stack.MAX_LAYERS:
            self.report({'ERROR'}, f"An object can hold up to {stack.MAX_LAYERS} transforms")
            return {'CANCELLED'}
        src = ob.extra_transform.layers[i]
        # Read everything first: adding to the collection may reallocate it and invalidate `src`.
        name, values, enabled = src.name, stack.read_inputs(ob, src), src.enabled
        layer = stack.new_layer(ob, name, values, index=i + 1)
        layer.enabled = enabled
        stack.rebuild(ob)
        return {'FINISHED'}


class OBJECT_OT_extra_transform_merge_up(_LayerOp, bpy.types.Operator):
    bl_idname = "object.extra_transform_merge_up"
    bl_label = "Merge Up"
    bl_description = "Fold this layer into the one above it. The result is identical"

    @classmethod
    def poll(cls, context):
        return super().poll(context) and len(context.object.extra_transform.layers) > 1

    def execute(self, context):
        ob = context.object
        layers = ob.extra_transform.layers
        i = self.layer_index(context)
        if i is None or i == 0:
            self.report({'ERROR'}, "There is no layer above to merge into")
            return {'CANCELLED'}
        outer, inner = layers[i], layers[i - 1]
        if not (outer.enabled and inner.enabled):
            self.report({'ERROR'}, "Both layers must be enabled")
            return {'CANCELLED'}
        if stack.layer_is_animated(ob, outer) or stack.layer_is_animated(ob, inner):
            self.report({'ERROR'}, "Animated layers can't be merged")
            return {'CANCELLED'}
        merged = stack.compose(stack.read_inputs(ob, outer), stack.read_inputs(ob, inner), ob.rotation_mode)
        stack.write_inputs(ob, inner, merged)
        stack.remove_layer(ob, i)
        ob.extra_transform.active_index = i - 1
        stack.rebuild(ob)
        return {'FINISHED'}


class OBJECT_OT_extra_transform_apply(_ObjectOp, bpy.types.Operator):
    bl_idname = "object.extra_transform_apply"
    bl_label = "Apply to Transform"
    bl_description = "Bake all layers into Location / Rotation / Scale and remove them"

    @classmethod
    def poll(cls, context):
        return super().poll(context) and len(context.object.extra_transform.layers) > 0

    def execute(self, context):
        ob = context.object
        ev = ob.evaluated_get(context.evaluated_depsgraph_get())
        loc = [a + b for a, b in zip(ev.location, ev.delta_location)]
        scale = [a * b for a, b in zip(ev.scale, ev.delta_scale)]
        mode = ob.rotation_mode
        if mode == 'QUATERNION':
            rot = ev.delta_rotation_quaternion.normalized() @ ev.rotation_quaternion.normalized()
        elif mode != 'AXIS_ANGLE':
            base = Euler(ev.rotation_euler, mode)
            rot = (Euler(ev.delta_rotation_euler, mode).to_matrix() @ base.to_matrix()).to_euler(mode, base)

        stack.remove_all(ob)
        ob.location = loc
        ob.scale = scale
        if mode == 'QUATERNION':
            ob.rotation_quaternion = rot
        elif mode != 'AXIS_ANGLE':
            ob.rotation_euler = rot
        if ob.animation_data and ob.animation_data.action:
            self.report({'WARNING'}, "Base transform animation is not changed")
        return {'FINISHED'}


class OBJECT_OT_extra_transform_bake_delta(_ObjectOp, bpy.types.Operator):
    bl_idname = "object.extra_transform_bake_delta"
    bl_label = "Bake to Delta"
    bl_description = "Write the current result into the native Delta Transform and remove all layers"

    @classmethod
    def poll(cls, context):
        return super().poll(context) and len(context.object.extra_transform.layers) > 0

    def execute(self, context):
        ob = context.object
        if any(stack.layer_is_animated(ob, l) for l in ob.extra_transform.layers):
            self.report({'WARNING'}, "Layer animation was dropped, only the current frame was baked")
        ev = ob.evaluated_get(context.evaluated_depsgraph_get())
        values = (ev.delta_location.copy(), ev.delta_rotation_euler.copy(),
                  ev.delta_rotation_quaternion.copy(), ev.delta_scale.copy())
        stack.remove_all(ob)
        ob.delta_location, ob.delta_rotation_euler, ob.delta_rotation_quaternion, ob.delta_scale = values
        return {'FINISHED'}


classes = (
    OBJECT_OT_extra_transform_add,
    OBJECT_OT_extra_transform_remove,
    OBJECT_OT_extra_transform_move,
    OBJECT_OT_extra_transform_duplicate,
    OBJECT_OT_extra_transform_merge_up,
    OBJECT_OT_extra_transform_apply,
    OBJECT_OT_extra_transform_bake_delta,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
