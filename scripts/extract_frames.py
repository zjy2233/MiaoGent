"""
从现有 mascot.json 提取 WebP 帧 → PNG 参考图 + 风格参考表
用于后续 AI 生成新动作时保持风格一致
"""
import json
import base64
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT / "frontend" / "assets"
OUT_DIR = ROOT / "temp" / "mascot-frames"

# ── 最佳参考帧选择策略 ──────────────────────────
# 从 64 帧动画中选取最具代表性的帧：
#   - 首帧: 角色标准姿态
#   - 动作极值帧: 最大的姿态变化（用于展示角色部件）
#   - 帧间差异最大的帧: 整体动作范围
#
# 当前动画推测为 idle 呼吸循环（颜色一致，姿态微动）
# 策略：均匀采样 8 帧 + 首帧作为主参考
REF_FRAME_INDICES = [0, 8, 16, 24, 32, 40, 48, 56, 63]


def extract_ref_frames():
    src = ASSETS_DIR / "mascot.json"
    with open(src, "r", encoding="utf-8") as f:
        data = json.load(f)

    assets = data["assets"]
    layers = data["layers"]

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 建立 refId → asset 映射
    asset_map = {a["id"]: a for a in assets}

    # ── 1. 提取所有帧为 WebP ─────────────────
    all_frames_dir = OUT_DIR / "all_frames"
    all_frames_dir.mkdir(exist_ok=True)

    for i, layer in enumerate(layers):
        ref_id = layer["refId"]
        asset = asset_map[ref_id]
        data_url = asset["p"]
        _, b64 = data_url.split(",", 1)
        img_bytes = base64.b64decode(b64)

        fpath = all_frames_dir / f"frame_{i:03d}.webp"
        fpath.write_bytes(img_bytes)

    print(f"[1/4] Extracted {len(layers)} frames → {all_frames_dir}")

    # ── 2. 选参考帧 + 转 PNG ─────────────────
    ref_dir = OUT_DIR / "references"
    ref_dir.mkdir(exist_ok=True)

    for idx in REF_FRAME_INDICES:
        if idx >= len(layers):
            continue
        ref_id = layers[idx]["refId"]
        asset = asset_map[ref_id]
        _, b64 = asset["p"].split(",", 1)

        webp_path = ref_dir / f"ref_{idx:03d}.webp"
        webp_path.write_bytes(base64.b64decode(b64))

    print(f"[2/4] {len(REF_FRAME_INDICES)} reference frames → {ref_dir}")

    # ── 3. 生成风格参考表 ─────────────────
    # 供 ComfyUI IPAdapter / Kling 参考图模式使用
    analyze_style(data, asset_map, layers)

    # ── 4. 生成多帧合成图（供 AI 工具一次性参考） ──
    try:
        from PIL import Image

        thumb_size = (200, 150)
        thumbs = []
        for idx in REF_FRAME_INDICES:
            if idx >= len(layers):
                continue
            ref_id = layers[idx]["refId"]
            asset = asset_map[ref_id]
            _, b64 = asset["p"].split(",", 1)

            tmp = ref_dir / f"_tmp_ref_{idx}.webp"
            tmp.write_bytes(base64.b64decode(b64))
            img = Image.open(tmp).convert("RGBA")
            img.thumbnail(thumb_size)
            thumbs.append(img)
            tmp.unlink()

        # 拼成 3×3 网格
        cols = 3
        rows = (len(thumbs) + cols - 1) // cols
        grid_w = thumb_size[0] * cols
        grid_h = thumb_size[1] * rows
        grid = Image.new("RGBA", (grid_w, grid_h), (0, 0, 0, 0))

        for i, thumb in enumerate(thumbs):
            x = (i % cols) * thumb_size[0]
            y = (i // cols) * thumb_size[1]
            grid.paste(thumb, (x, y))

        grid_path = OUT_DIR / "style_reference_sheet.png"
        grid.save(grid_path, "PNG")
        print(f"[3/4] Style reference sheet → {grid_path}")

    except ImportError:
        print("[3/4] Pillow not available — skipping grid generation")

    print(f"[4/4] Done! Reference frames ready at {OUT_DIR}")
    return OUT_DIR


def analyze_style(data, asset_map, layers):
    """分析颜色、透明度和动画特征，输出风格参考表"""
    report = {
        "source": "frontend/assets/mascot.json",
        "format_version": data.get("v"),
        "fps": data.get("fr"),
        "total_frames": data.get("op", 0) - data.get("ip", 0),
        "canvas": f"{data.get('w')}x{data.get('h')}",
        "render_size": "140x140 (CSS scaled)",
        "frame_count": len(layers),
        "image_type": "WebP (base64 inlined)",
    }

    # 采样颜色分析
    try:
        from PIL import Image

        samples = {}
        for idx in [0, 16, 32, 48, 63]:
            if idx >= len(layers):
                continue
            ref_id = layers[idx]["refId"]
            asset = asset_map[ref_id]
            _, b64 = asset["p"].split(",", 1)

            tmp = OUT_DIR / "references" / f"_color_sample_{idx}.webp"
            tmp.write_bytes(base64.b64decode(b64))
            img = Image.open(tmp).convert("RGBA")

            pixels = [(r, g, b, a) for r, g, b, a in img.getdata() if a > 128]
            if pixels:
                avg_r = sum(p[0] for p in pixels) // len(pixels)
                avg_g = sum(p[1] for p in pixels) // len(pixels)
                avg_b = sum(p[2] for p in pixels) // len(pixels)
                samples[f"frame_{idx}"] = {
                    "avg_color": f"rgb({avg_r}, {avg_g}, {avg_b})",
                    "opaque_pixels": len(pixels),
                    "transparency_ratio": f"{(1 - len(pixels) / (img.width * img.height)) * 100:.1f}%",
                }
            tmp.unlink()

        report["color_samples"] = samples
    except ImportError:
        report["color_samples"] = "Pillow not available"

    # 关键特征描述（供 AI prompt 使用）
    report["style_guide"] = {
        "character": "2D cartoon cat, dark theme, minimalist",
        "color_palette": "dark purple-blue (rgb ~75, 76, 108), cool tones",
        "background": "FULLY TRANSPARENT (alpha channel required)",
        "style": "flat shading, soft outlines, kawaii proportions",
        "scale_note": "Source 800x600 but rendered at 140x140 — keep detail level moderate",
        "compositing": "Must be RGBA with clean alpha edges — no halo, no matte lines",
    }

    report_path = OUT_DIR / "style_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"  Style report → {report_path}")


if __name__ == "__main__":
    extract_ref_frames()
