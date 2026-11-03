# How it works

English | [日本語](how-it-works.ja.md)

## Composition

Drivers feed the layer values into the object's own Delta (`delta_location` and friends). Blender builds the final matrix from the Delta as usual, so a single layer gives the same result as the built-in Delta.

With more layers, location adds up, scale multiplies per axis, and rotation multiplies as rotation matrices, with later layers applied from the outside (on the left). In Quaternion mode the quaternions are multiplied instead.

Each channel is composed separately, so rotating an outer layer does not swing an inner layer's location around. Stacking parented Empties would give a different result here.

Rotation follows the object's rotation mode, and changing the mode rebuilds the drivers. Axis Angle has no delta rotation, so only location and scale are composed in that mode.

## Where values are stored

Layer values are custom properties on the object, named `Extra 1 Location`, `Extra 2 Euler Rotation` and so on. The number is the layer's position in the stack; reordering renames the properties and their keyframe paths.

They are not add-on properties (`bpy.props`) because a driver reading one of those causes a dependency cycle.

## Drivers

Each driver reads layer values and writes a Delta channel, nothing more. Chaining drivers through custom properties is avoided because Blender 5.2's threaded evaluation now and then drops a driver's write to a custom property.

Location uses a SUM driver and scale an `a*b*c...` expression; the 100-layer limit comes from that expression's length limit. Rotation with one layer is passed straight through. With two or more, the add-on registers an `extra_transform_rotation` function in `bpy.app.driver_namespace` and the drivers call it.

Without the add-on that function doesn't exist, so a file with two or more rotation layers shows the wrong rotation. Location and scale still work.

## Ctrl+A and other built-in operators

Ctrl+A, Alt+G/R/S and Transforms to Deltas write to the Delta directly, and the drivers would overwrite that on the next evaluation. So the add-on notes the Delta values after every evaluation, and if they have changed by the next one, it writes the difference back into the layers.

A channel set to 0 (1 for scale) is reset in every layer. Any other value goes into the outermost layer, minus what the inner layers contribute.

Undo and opening a file throw away the noted values.

## Objects that already use Delta

Adding a layer to an object whose Delta already has values, animation or drivers moves all of that into a first layer called "Delta".
