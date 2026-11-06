# Extra Transform

[English](README.md) | 日本語

オブジェクトに Delta Transform を何層でも重ねられる Blender アドオンです。

## 機能

- 各レイヤーは標準の Delta Transform と同じ計算で動きます。後から追加したレイヤーほど外側にかかります
- Empty などの補助オブジェクトは作りません
- 複製、上のレイヤーとの統合、並べ替え、無効化ができます
- 全レイヤーを Transform や標準の Delta に焼き込めます
- Ctrl+A などの標準の操作はそのまま使えます

## 動作環境

Blender 5.2 以降

## インストール

1. [Releases](https://github.com/JasonDreamer/extra-transform/releases) から zip をダウンロードします
2. Blender の 編集 > プリファレンス > エクステンションを入手 を開き、右上の ▾ から「ディスクからインストール」で zip を選びます

## 使い方

オブジェクトプロパティ > トランスフォーム > デルタトランスフォーム にある Add Transform を押すと、レイヤーが追加されます。すでに Delta に値が入っている場合は、その値が1枚目のレイヤーになります。

## 注意

回転のレイヤーが2枚以上あるファイルをアドオンなしで開くと、回転は保存したときの値で止まり、アニメーションしません。

## 仕組み

[docs/how-it-works.ja.md](docs/how-it-works.ja.md) を参照してください。

## ライセンス

GPL-3.0-or-later
