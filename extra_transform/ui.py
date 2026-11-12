import bpy

from . import stack


class OBJECT_MT_extra_transform_layer(bpy.types.Menu):
    bl_label = "Transform Layer"

    def draw(self, context):
        layout = self.layout
        layer = context.extra_transform_layer
        index = next(i for i, l in enumerate(context.object.extra_transform.layers) if l == layer)
        for idname, icon in (
            ("object.extra_transform_duplicate", 'DUPLICATE'),
            ("object.extra_transform_merge_up", 'TRIA_UP_BAR'),
        ):
            layout.operator(idname, icon=icon).index = index
        layout.separator()
        for direction, icon in (('UP', 'TRIA_UP'), ('DOWN', 'TRIA_DOWN')):
            op = layout.operator("object.extra_transform_move", text=f"Move {direction.title()}", icon=icon)
            op.index = index
            op.direction = direction


def draw_layer(layout, ob, layer):
    layout.use_property_split = True
    col = layout.column()
    col.active = layer.enabled
    col.prop(ob, stack.input_path(layer, "loc"), text="Location")
    mode = ob.rotation_mode
    if mode == 'QUATERNION':
        col.prop(ob, stack.input_path(layer, "quat"), text="Rotation")
    elif mode != 'AXIS_ANGLE':
        col.prop(ob, stack.input_path(layer, "rot"), text="Rotation")
    col.prop(ob, stack.input_path(layer, "scale"), text="Scale")
    col.prop(ob, stack.input_path(layer, "influence"), text="Influence", slider=True)


def draw_stack(layout, ob):
    for layer in ob.extra_transform.layers:
        header, body = layout.panel_prop(layer, "show_expanded")
        row = header.row(align=True)
        row.prop(layer, "enabled", text="")
        row.prop(layer, "name", text="")
        row.context_pointer_set("extra_transform_layer", layer)
        row.menu("OBJECT_MT_extra_transform_layer", text="", icon='DOWNARROW_HLT')
        row.operator("object.extra_transform_remove", text="", icon='X', emboss=False).index = (
            list(ob.extra_transform.layers).index(layer))
        if body:
            draw_layer(body, ob, layer)


# The layers *are* the object's Delta Transform, so they are drawn in its native panel.
_native_draw = None


def draw_delta_panel(self, context):
    layout = self.layout
    ob = context.object
    has_layers = bool(ob.extra_transform.layers)
    if has_layers:
        draw_stack(layout, ob)
    else:
        _native_draw(self, context)

    layout.use_property_split = False
    col = layout.column(align=True)
    col.enabled = stack.is_editable(ob)
    col.operator("object.extra_transform_add", icon='ADD')
    if has_layers:
        row = col.row(align=True)
        row.operator("object.extra_transform_apply")
        row.operator("object.extra_transform_bake_delta")


def register():
    global _native_draw
    bpy.utils.register_class(OBJECT_MT_extra_transform_layer)
    panel = bpy.types.OBJECT_PT_delta_transform
    _native_draw = panel.draw
    panel.draw = draw_delta_panel


def unregister():
    bpy.types.OBJECT_PT_delta_transform.draw = _native_draw
    bpy.utils.unregister_class(OBJECT_MT_extra_transform_layer)
