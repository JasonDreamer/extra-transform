# How it works

English | [日本語](how-it-works.ja.md)

## Composition

Drivers write the layer values into the object's own Delta Transform (`delta_location`, `delta_rotation_euler`, `delta_rotation_quaternion`, `delta_scale`). Blender computes the final transform from those as usual, so with one layer the result is identical to the built-in Delta.

With several layers, each channel is composed on its own:

| Channel | Composition |
| --- | --- |
| Location | Sum of all layers |
| Scale | Product of all layers, per axis |
| Rotation | Product of rotation matrices (quaternions in Quaternion mode). Later layers multiply from the left |

Because composition is per channel, an outer layer's rotation or scale does not affect an inner layer's location. This is where it differs from stacking parented Empties.

Rotation is read in the object's rotation mode, and the drivers are rebuilt when the mode changes. Axis Angle mode has no delta rotation, so rotation is not composed there (location and scale still are).

Disabled layers are left out.

## Where the data lives

Layer values are custom properties on the object, named like `Extra 1 Location` or `Extra 2 Euler Rotation`. The number is the layer's position in the stack. Reordering renames the properties and remaps their keyframe paths. Keyed channels go into the Object Transforms group in the Graph Editor.

The values are custom properties because drivers that read add-on properties (`bpy.props`) create a dependency cycle.

## Drivers

Every driver reads the layer values directly and writes a native Delta channel. No driver reads another driver's result: in Blender 5.2, a driver that writes a custom property occasionally loses its result under threaded evaluation.

- Location: a SUM driver
- Scale: an `a*b*c...` expression (the 100-layer limit comes from this expression's length)
- Rotation, one layer: the input is passed through
- Rotation, two or more layers: a function, `extra_transform_rotation`, that the add-on registers in `bpy.app.driver_namespace`

A file with two or more rotation layers shows the wrong rotation when opened without the add-on, because that function is missing. Location and scale are plain expressions and work without it.

## Built-in operators

Ctrl+A, Alt+G/R/S, Transforms to Deltas and similar operators write the native Delta directly, and the drivers would overwrite that on the next evaluation. So after each evaluation the add-on remembers the Delta values. If they changed before the next evaluation, it treats that as an outside write and folds it back into the layers:

- A channel written to its identity value (location 0, rotation 0, scale 1) resets that channel in every layer
- Any other value goes into the outermost layer, with the composition of the inner layers taken out

Undo and file loads discard the remembered values.

## Existing Delta

When you add a layer to an object whose Delta already has values, animation or drivers, they move into a first layer named "Delta".
