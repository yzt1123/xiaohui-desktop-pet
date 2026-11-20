# -*- coding: utf-8 -*-
"""把《小灰》里的 11 段 PNGTuber GIF 拆成静态分层图，修掉索引透明的白边。

输出：assets/<state>.png（500x500 RGBA）+ manifest.json
"""
import glob
import json
import os
import re

import numpy as np
from PIL import Image, ImageFilter

SRC = r"C:\Users\Ya\Desktop\小灰"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

NAME_MAP = {
    "Bug": "bug",
    "加载中": "loading",
    "说话 2": "talk",
    "哭 1": "cry",
    "墨镜反光": "glasses",
    "工作(疲倦)": "work",
    "打字(普通)": "typing",
    "打字(生气)": "typing_angry",
    "摸头": "pat",
    "睡觉(准备阶段2)": "sleep",
    "馋(刀叉)": "hungry",
}


def state_id(path):
    base = os.path.splitext(os.path.basename(path))[0]
    base = re.sub(r"_\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}$", "", base)
    if base.startswith("哀_"):
        base = base[2:]
    base = base.replace("PNGTuber ", "").strip()
    return NAME_MAP.get(base, base)


def edge_fix(im, spread=6, feather=0.7):
    """索引透明是硬边：把 RGB 往外扩几圈，再把 alpha 羽化一点，贴深色背景才不露白。"""
    arr = np.array(im).astype(np.float32)
    rgb, a = arr[:, :, :3], arr[:, :, 3]
    known = a > 8
    if not known.any():
        return im

    color = rgb.copy()
    mask = known.copy()
    for _ in range(spread):
        acc = np.zeros_like(color)
        cnt = np.zeros(mask.shape, np.float32)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            sm = np.roll(np.roll(mask, dy, 0), dx, 1)
            sc = np.roll(np.roll(color, dy, 0), dx, 1)
            acc += sc * sm[:, :, None]
            cnt += sm
        grow = (~mask) & (cnt > 0)
        color[grow] = acc[grow] / cnt[grow][:, None]
        mask = mask | grow

    alpha = Image.fromarray(a.astype(np.uint8), "L").filter(ImageFilter.GaussianBlur(feather))
    out = np.dstack([color, np.array(alpha, dtype=np.float32)]).clip(0, 255).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def main():
    os.makedirs(OUT, exist_ok=True)
    manifest = {}
    for path in sorted(glob.glob(os.path.join(SRC, "*.gif"))):
        sid = state_id(path)
        im = Image.open(path)
        n = getattr(im, "n_frames", 1)
        idx = 0 if n < 8 else n // 3  # 跳过起手帧
        im.seek(idx)
        frame = im.convert("RGBA")
        fixed = edge_fix(frame)
        dst = os.path.join(OUT, sid + ".png")
        fixed.save(dst)
        manifest[sid] = {
            "file": sid + ".png",
            "width": frame.width,
            "height": frame.height,
            "frames": n,
            "frame": idx,
            "source": os.path.basename(path),
        }
        print("%-14s frame %3d/%3d -> %s" % (sid, idx, n, dst))

    with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
