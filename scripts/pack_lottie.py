"""
WebP 帧序列 → 带 Marker 的 Lottie JSON 组装工具
─────────────────────────────────────────────
输入: 各动作的 WebP 帧目录
       scripts/comfyui_workflows/_batch_index.json 中的动作定义
输出: frontend/assets/mascot.json (带 markers 的多段动画)

用法:
  python scripts/pack_lottie.py                          # 从 temp/generated-frames/ 组装
  python scripts/pack_lottie.py --frames-dir ./myframes   # 指定帧目录
  python scripts/pack_lottie.py --optimize                # 降分辨率 + 压缩透明区域
  python scripts/pack_lottie.py --validate-only           # 仅校验已有的 mascot.json
"""

import json
import base64
import os
import sys
import io
import argparse
from pathlib import Path
from collections import OrderedDict

# Windows console UTF-8 support
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FRAMES_DIR = ROOT / "temp" / "generated-frames"
OUTPUT_FILE = ROOT / "frontend" / "assets" / "mascot.json"
BACKUP_FILE = ROOT / "frontend" / "assets" / "mascot_backup.json"

# ── 动作配置 ──────────────────────────────────
# 与 MascotController.ACTIONS 对齐
ACTION_CONFIGS = OrderedDict([
    ("idle",    {"desc": "Idle breathing",    "fps": 25, "loop": True}),
    ("walk",    {"desc": "Walking cycle",     "fps": 24, "loop": True}),
    ("sleep",   {"desc": "Sleeping",          "fps": 20, "loop": True}),
    ("sit",     {"desc": "Sitting pose",      "fps": 24, "loop": True}),
    ("stretch", {"desc": "Wake-up stretch",   "fps": 24, "loop": False}),
    ("jump",    {"desc": "Happy jump",        "fps": 24, "loop": False}),
    ("wave",    {"desc": "Paw wave",          "fps": 24, "loop": True}),
    ("think",   {"desc": "Thinking pose",     "fps": 25, "loop": True}),
    ("look",    {"desc": "Looking around",    "fps": 24, "loop": True}),
])


def load_frames_from_dir(frames_dir, action_name):
    """从目录加载 WebP 帧，返回 [(path, bytes), ...]"""
    action_dir = Path(frames_dir) / action_name
    if not action_dir.is_dir():
        # 尝试平铺结构
        action_dir = Path(frames_dir)

    frames = []
    exts = [".webp", ".png", ".jpg", ".jpeg"]

    for fpath in sorted(action_dir.iterdir()):
        if fpath.suffix.lower() in exts:
            frames.append(fpath)

    if not frames:
        raise FileNotFoundError(f"No frames found for action '{action_name}' in {action_dir}")

    return frames


def optimize_frame(data: bytes, src_ext: str, quality: int = 80) -> tuple:
    """优化帧: 降分辨率 + 确保 WebP + 压缩透明区域"""
    from PIL import Image
    import io

    img = Image.open(io.BytesIO(data)).convert("RGBA")

    # 降分辨率: 800×600 → 400×300
    # 140px CSS 渲染尺寸下看不出差异
    target_w, target_h = 400, 300
    if img.width > target_w or img.height > target_h:
        img.thumbnail((target_w, target_h), Image.LANCZOS)

    # 写入 WebP (RGBA 模式保留 alpha)
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=quality, method=6,
             alpha_quality=30,  # 透明区域低质量压缩
             exact=False)
    return buf.getvalue(), img.width, img.height


def pack_action(action_name, frames_dir, optimize=False, quality=80):
    """将单个动作的帧序列打包为 Lottie 资产对 (assets + layers)"""
    frames = load_frames_from_dir(frames_dir, action_name)
    config = ACTION_CONFIGS.get(action_name, {"fps": 25})

    assets = []
    layers = []
    w, h = 400, 300  # default after optimization

    for i, fpath in enumerate(frames):
        raw = fpath.read_bytes()
        ext = fpath.suffix.lower()

        if optimize:
            img_data, fw, fh = optimize_frame(raw, ext, quality)
            w, h = fw, fh
        else:
            img_data = raw
            # 尝试读取尺寸
            try:
                from PIL import Image
                import io
                img = Image.open(io.BytesIO(raw))
                w, h = img.size
            except Exception:
                pass

        b64 = base64.b64encode(img_data).decode()

        aid = f"{action_name}_{i}"
        assets.append({
            "id": aid,
            "w": w,
            "h": h,
            "p": f"data:image/webp;base64,{b64}",
            "u": "",
            "e": 1,
        })

        layers.append({
            "ddd": 0,
            "ind": i + 1,
            "ty": 2,
            "nm": f"{action_name}_f{i:03d}",
            "refId": aid,
            "sr": 1,
            "ks": {
                "o": {"a": 0, "k": 100},
                "r": {"a": 0, "k": 0},
                "p": {"a": 0, "k": [w / 2, h / 2, 0]},
                "a": {"a": 0, "k": [w / 2, h / 2, 0]},
                "s": {"a": 0, "k": [100, 100, 100]},
            },
            "ao": 0,
            "ip": i,
            "op": i + 1,
            "st": i,
            "bm": 0,
        })

    return assets, layers, w, h, config["fps"]


def build_lottie(frames_dir, optimize=False, quality=80):
    """
    多动作帧序列 → 单 Lottie JSON (带 markers)

    格式:
    {
      "v": "5.5.2",
      "fr": 25,           # 基础帧率
      "ip": 0,
      "op": <总帧数>,
      "w": 400, "h": 300,
      "markers": [        # ★ 动作分段标记
        {"cm": "idle",  "tm": 0,   "dr": 64},
        {"cm": "walk",  "tm": 64,  "dr": 48},
        ...
      ],
      "assets": [...],
      "layers": [...]
    }
    """
    all_assets = []
    all_layers = []
    markers = []
    frame_offset = 0
    canvas_w, canvas_h = 400, 300

    for action_name, config in ACTION_CONFIGS.items():
        action_dir = Path(frames_dir) / action_name

        if not action_dir.is_dir():
            print(f"  ⚠ Skipping '{action_name}' — directory not found: {action_dir}")
            continue

        try:
            assets, layers, w, h, _ = pack_action(action_name, frames_dir, optimize, quality)
        except FileNotFoundError as e:
            print(f"  ⚠ Skipping '{action_name}' — {e}")
            continue

        canvas_w, canvas_h = w, h

        # 偏移所有层的 in/out 帧
        num_frames = len(layers)
        for layer in layers:
            layer["ip"] += frame_offset
            layer["op"] += frame_offset
            layer["st"] += frame_offset
            layer["ind"] = len(all_layers) + layer["ind"]

        all_assets.extend(assets)
        all_layers.extend(layers)

        markers.append({
            "cm": action_name,
            "tm": frame_offset,
            "dr": num_frames,
        })

        size_kb = sum(len(a["p"]) for a in assets) / 1024
        print(f"  ✓ {action_name:10s}  {num_frames:3d}f × {w}×{h}  "
              f"~{size_kb:.0f}KB  [{config['desc']}]")

        frame_offset += num_frames

    total_frames = frame_offset

    lottie = OrderedDict([
        ("v", "5.5.2"),
        ("fr", 25),
        ("ip", 0),
        ("op", total_frames),
        ("w", canvas_w),
        ("h", canvas_h),
        ("nm", "MiaoGent Mascot"),
        ("ddd", 0),
        ("markers", markers),
        ("assets", all_assets),
        ("layers", all_layers),
        ("_meta", {
            "generator": "pack_lottie.py",
            "total_frames": total_frames,
            "actions": [m["cm"] for m in markers],
            "action_count": len(markers),
        }),
    ])

    return lottie


def validate_lottie(filepath):
    """校验 Lottie 文件结构"""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    issues = []
    warnings = []

    # 基本字段
    for key in ["v", "fr", "ip", "op", "w", "h", "assets", "layers"]:
        if key not in data:
            issues.append(f"Missing required field: {key}")

    total_frames = data.get("op", 0) - data.get("ip", 0)
    if total_frames != len(data.get("layers", [])):
        issues.append(f"Frame count mismatch: op-ip={total_frames}, layers={len(data.get('layers', []))}")

    if total_frames == 0:
        issues.append("Empty animation (0 frames)")

    # Marker 校验
    markers = data.get("markers", [])
    if markers:
        marker_end = 0
        for m in markers:
            if m["tm"] != marker_end:
                warnings.append(f"Marker '{m['cm']}' starts at frame {m['tm']}, expected {marker_end}")
            marker_end = m["tm"] + m["dr"]
        if marker_end != total_frames:
            warnings.append(f"Last marker ends at frame {marker_end}, but total frames is {total_frames}")
    else:
        warnings.append("No markers — single continuous animation")

    # 资产校验
    asset_ids = {a["id"] for a in data.get("assets", [])}
    for layer in data.get("layers", []):
        if layer.get("refId") not in asset_ids:
            issues.append(f"Layer {layer.get('ind')} references unknown asset: {layer.get('refId')}")

    # 尺寸一致性
    sizes = {(a.get("w"), a.get("h")) for a in data.get("assets", []) if a.get("w")}
    if len(sizes) > 1:
        warnings.append(f"Inconsistent asset sizes: {sizes}")

    return issues, warnings


def print_validation(filepath, issues, warnings):
    size_kb = os.path.getsize(filepath) / 1024

    print(f"\n{'='*60}")
    print(f"  Validation: {filepath}")
    print(f"  File size: {size_kb:.0f} KB")
    print(f"{'='*60}")

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    markers = data.get("markers", [])
    print(f"  Version: {data.get('v')},  FPS: {data.get('fr')}")
    print(f"  Canvas: {data.get('w')}×{data.get('h')}")
    print(f"  Total frames: {data.get('op') - data.get('ip')}")
    print(f"  Duration: {(data.get('op') - data.get('ip')) / data.get('fr', 25):.1f}s")
    print(f"  Markers: {len(markers)}")
    for m in markers:
        dur = m["dr"] / data.get("fr", 25)
        print(f"    [{m['tm']:>4d} → {m['tm'] + m['dr']:>4d}]  {m['cm']:10s}  "
              f"{m['dr']}f  {dur:.1f}s")

    if issues:
        print(f"\n  ❌ ISSUES ({len(issues)}):")
        for i in issues:
            print(f"     - {i}")

    if warnings:
        print(f"\n  ⚠ WARNINGS ({len(warnings)}):")
        for w in warnings:
            print(f"     - {w}")

    if not issues and not warnings:
        print(f"\n  ✅ All checks passed!")

    print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description="Pack WebP frames into Lottie JSON with markers")
    parser.add_argument("--frames-dir", default=str(DEFAULT_FRAMES_DIR),
                        help="Directory containing per-action frame subdirectories")
    parser.add_argument("--output", default=str(OUTPUT_FILE),
                        help="Output Lottie JSON path")
    parser.add_argument("--optimize", action="store_true",
                        help="Downscale and compress frames")
    parser.add_argument("--quality", type=int, default=80,
                        help="WebP quality (1-100, default: 80)")
    parser.add_argument("--validate-only", action="store_true",
                        help="Only validate existing file, don't pack")
    parser.add_argument("--no-backup", action="store_true",
                        help="Don't create backup of existing mascot.json")

    args = parser.parse_args()

    if args.validate_only:
        issues, warnings = validate_lottie(OUTPUT_FILE)
        print_validation(str(OUTPUT_FILE), issues, warnings)
        return

    print(f"\n  📦 Packing frames from: {args.frames_dir}")
    print(f"  📍 Output: {args.output}")
    if args.optimize:
        print(f"  🔧 Optimize: YES (downscale + compress, quality={args.quality})")

    lottie = build_lottie(args.frames_dir, args.optimize, args.quality)

    if not lottie["layers"]:
        print("\n  ❌ No frames found! Aborting.")
        sys.exit(1)

    # 备份现有文件
    output_path = Path(args.output)
    if output_path.exists() and not args.no_backup:
        backup_path = output_path.with_suffix(".json.bak")
        import shutil
        shutil.copy2(output_path, backup_path)
        print(f"\n  💾 Backup: {backup_path}")

    # 写入
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(lottie, f, ensure_ascii=False)

    total_size = os.path.getsize(output_path) / 1024
    print(f"\n  ✅ Packed {len(lottie['markers'])} actions → {output_path}")
    print(f"     Total size: {total_size:.0f} KB")
    print(f"     Total frames: {lottie['op']} @ {lottie['fr']}fps")

    # 自动校验
    issues, warnings = validate_lottie(str(output_path))
    print_validation(str(output_path), issues, warnings)

    if issues:
        sys.exit(1)


if __name__ == "__main__":
    main()
