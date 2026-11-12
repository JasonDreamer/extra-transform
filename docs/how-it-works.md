# How it works

English | [日本語](how-it-works.ja.md)

## 1. Overview

Drivers collect each layer's values into Blender's Delta Transform (`delta_location` and the rest). With a single layer, the result is identical to Blender's built-in Delta.

## 2. Composition rules

Each channel is composed independently. Rotating an outer (upper) layer does not affect an inner (lower) layer's location (unlike an Empty parent hierarchy).

| Channel | Composition | Notes |
| --- | --- | --- |
| Location | Sum | |
| Scale | Product | Per axis |
| Rotation | Product of matrices (or quaternions) | Outer layers multiply from the left |

Each layer's influence (0 to 1) moves the layer's values linearly from their identity values (location 0, rotation 0, scale 1) before they are composed. This works like the influence of an NLA strip in Replace mode.

| Channel | Value at influence w |
| --- | --- |
| Location | value × w |
| Scale | 1 − w + value × w |
| Euler rotation | each angle × w |
| Quaternion rotation | (1 − w + W × w, X × w, Y × w, Z × w), normalized |

At influence 1 the result is identical to a layer without influence.

- **Rotation mode**: Follows the object's rotation mode. Changing the mode regenerates the drivers.
- **Axis Angle**: The Delta has no rotation for this mode, so only location and scale are composed.

## 3. Data storage

- Layer values are stored as custom properties on the object (e.g. `Extra 1 Location`, `Extra 1 Influence`).
- The number matches the stack order. Reordering reassigns the property names and keyframe paths.
- `bpy.props` is not used because drivers reading it cause an evaluation loop (circular dependency).

## 4. Drivers and multithreading

Every driver is a single-stage simple expression: it reads layer values directly and writes straight to the Delta. No Python expressions are used, so there is no warning when Python auto-execution is off. With multiple stages, Blender 5.2's multithreaded evaluation drops writes.

- **Location**: `a*A+b*B+...` expression (lowercase is the value, uppercase the influence)
- **Scale**: `(1-A+a*A)*(1-B+b*B)*...` expression (the 20-layer limit comes from the expression's length limit)
- **Rotation (1 layer)**: `a*A` expression (`1-A+a*A` for the quaternion's W)
- **Rotation (2+ layers)**: Too complex for a simple expression, so there is no driver. The add-on's handlers compute it and write it to the Delta

Rotation with two or more layers is computed when layer values change and right before each frame change (playback, rendering, and the in-between frames of motion blur). Keyframed values are read from their F-Curves at that frame. NLA strips and drivers on layer values can't be read from F-Curves, so they lag one frame or are not picked up.

## 5. Built-in operators (Ctrl+A / Alt+G, R, S)

Built-in transform operators write directly to the Delta, so the add-on writes the change back to the layers as follows.

1. Cache the Delta values on every evaluation and detect changes.
2. A channel that was cleared (0, or 1 for scale) is reset in every layer.
3. Any other change replaces the outermost layer with nonzero influence with the new value minus the inner layers' values.
4. Reset the cache on Undo and file load.

## 6. Notes

- **Existing Delta values**: If the Delta has values, animation or drivers when a layer is added, they move into an automatically created "Delta" layer.
- **Without the add-on installed**:
  - Location and scale: Work normally.
  - Rotation (2+ layers): Stays at its saved value and does not animate.
- **Layers made with 1.0.x**: They have no influence property, so one is added at influence 1 when the file is opened.
- **Files saved with 1.0.0**: Rotation drivers used a Python function, so opening these files shows a warning. Opening them with the add-on replaces the drivers, and after saving again the warning no longer appears.
