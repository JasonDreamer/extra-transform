# Extra Transform

English | [日本語](README.ja.md)

A Blender add-on that stacks up to 20 Delta Transforms on an object.

## Features

- Each layer is computed exactly like the built-in Delta Transform. Layers added later apply further out
- No Empties or helper objects
- Adjust each layer's influence from 0 to 1
- Duplicate, merge into the layer above, reorder, and disable layers
- Bake all layers into the Transform or into the built-in Delta
- Built-in operators such as Ctrl+A keep working

## Requirements

Blender 5.2 or later

## Installation

1. Download the zip from [Releases](https://github.com/JasonDreamer/extra-transform/releases)
2. In Blender, open Edit > Preferences > Get Extensions, then choose Install from Disk in the ▾ menu at the top right and select the zip

## Usage

Press Add Transform under Object Properties > Transform > Delta Transform to add a layer. If the Delta already has values, they become the first layer.

## Note

If a file with two or more rotation layers is opened without the add-on, the rotation stays at its saved value and does not animate.

## How it works

See [docs/how-it-works.md](docs/how-it-works.md).

## License

GPL-3.0-or-later
