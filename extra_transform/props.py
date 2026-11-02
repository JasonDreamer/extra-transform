import bpy
from bpy.props import BoolProperty, CollectionProperty, IntProperty, PointerProperty, StringProperty

from . import stack


def _on_enabled(self, context):
    stack.rebuild(self.id_data)


class ExtraTransformLayer(bpy.types.PropertyGroup):
    # Only list bookkeeping lives here. The values are custom properties (see stack.py).
    name: StringProperty(name="Name", default="Transform")
    uid: StringProperty()
    # Number in the layer's custom property names ("Extra 2 Location"), 0 while unnumbered
    slot: IntProperty()
    enabled: BoolProperty(name="Enabled", default=True, update=_on_enabled)
    show_expanded: BoolProperty(name="Expanded", default=True)


class ExtraTransformSettings(bpy.types.PropertyGroup):
    layers: CollectionProperty(type=ExtraTransformLayer)
    active_index: IntProperty()
    # rotation_mode the drivers were generated for
    built_mode: StringProperty()


classes = (ExtraTransformLayer, ExtraTransformSettings)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Object.extra_transform = PointerProperty(type=ExtraTransformSettings)


def unregister():
    del bpy.types.Object.extra_transform
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
