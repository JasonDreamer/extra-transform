# Extra Transform

Blender add-on that stacks any number of Delta Transform layers on an object.

Each layer behaves exactly like Blender's own Delta Transform. Layers added later apply further out. Everything stays on the object itself: no Empties, no helper objects.

Requires Blender 5.2 or later.

## Install

1. Download `extra_transform-<version>.zip` from Releases.
2. In Blender, open Edit > Preferences > Get Extensions, choose Install from Disk in the ▾ menu, and pick the zip.

## Use

Object Properties > Transform > Delta Transform. Press **Add Transform** to add a layer. If the object already has a delta, it becomes the first layer.

Each layer has Location, Rotation (in the object's rotation mode) and Scale, and can be keyed. Keyed channels appear in the Graph Editor under Object Transforms as `Extra 1 Location`, `Extra 2 Euler Rotation` and so on, numbered by position in the stack.

The layer menu offers Duplicate, Merge Up, Move and Remove. Below the stack:

- **Apply to Transform** bakes all layers into Location / Rotation / Scale.
- **Bake to Delta** writes the current result into the native Delta and removes the layers.

Native operators such as Ctrl+A, Alt+G/R/S and Transforms to Deltas work as they do with a plain Delta.

## How it works

The layers are composed into the native Delta channels with drivers. Location is a sum, scale is a product. Rotation with one layer is passed through; with several layers it goes through a driver function the add-on registers. A file saved with several rotation layers therefore needs the add-on installed to show the right rotation.

## Tests

```
blender -b --factory-startup --python tests/test_parity.py
```

## License

GPL-3.0-or-later
