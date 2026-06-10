"""
生成帧后处理: 去背景 + 透明通道优化 + 尺寸归一化
─────────────────────────────────────────────
用途: ComfyUI / AI 视频工具输出 → 干净透明 WebP 帧
依赖: pip install rembg pillow

用法:
  python scripts/process_frames.py                          # 处理 temp/generated-frames/
  python scripts/process_frames.py --input ./myframes        # 指定输入目录
  python scripts/process_frames.py --no-rembg                # 跳过去背景
"""

import os
import sys
import argparse
from pathlib import Path

# Windows console UTF-8 support
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "temp" / "generated-frames"
OUTPUT_CANVAS = (400, 300)  # 目标画布尺寸


def remove_background_rembg(input_path: Path, output_path: Path):
    """使用 rembg 去除背景，保留角色 + 透明通道"""
    from rembg import remove

    with open(input_path, "rb") as f:
        input_data = f.read()

    output_data = remove(input_data,
                         alpha_matting=True,
                         alpha_matting_foreground_threshold=240,
                         alpha_matting_background_threshold=10)

    output_path.write_bytes(output_data)
    return output_path


def remove_background_color_key(input_path: Path, output_path: Path,
                                 color: tuple = (0, 255, 0), tolerance: int = 60):
    """
    色键抠图 (无需 GPU 的备选方案)
    适用于绿幕/蓝幕背景的 AI 生成帧
    """
    from PIL import Image
    import numpy as np

    img = Image.open(input_path).convert("RGBA")
    arr = np.array(img)

    r, g, b, a = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2], arr[:, :, 3]

    # 检测接近目标颜色的像素
    cr, cg, cb = color
    mask = (
        (abs(r.astype(int) - cr) < tolerance) &
        (abs(g.astype(int) - cg) < tolerance) &
        (abs(b.astype(int) - cb) < tolerance)
    )

    # 将匹配像素设为透明
    a[mask] = 0

    # 边缘羽化
    from scipy import ndimage
    edge = ndimage.binary_dilation(mask, iterations=1) & ~mask
    a[edge] = (a[edge] * 0.5).astype(np.uint8)

    result = Image.fromarray(arr, "RGBA")
    result.save(output_path, "WEBP", quality=85, method=6, alpha_quality=30)
    return output_path


def process_frame(input_path: Path, output_dir: Path, action_name: str,
                  frame_idx: int, use_rembg: bool = True,
                  target_size: tuple = OUTPUT_CANVAS):
    """处理单帧: 去背景 → 缩放到目标尺寸 → 输出 WebP"""
    from PIL import Image

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{action_name}_f{frame_idx:03d}.webp"

    # 去背景
    if use_rembg:
        remove_background_rembg(input_path, output_path)
    else:
        # 直接复制
        import shutil
        shutil.copy2(input_path, output_path)

    # 缩放
    img = Image.open(output_path).convert("RGBA")
    if img.width > target_size[0] or img.height > target_size[1]:
        img.thumbnail(target_size, Image.LANCZOS)

    # 最终输出
    img.save(output_path, "WEBP", quality=85, method=6, alpha_quality=30)

    # 统计
    size_kb = os.path.getsize(output_path) / 1024
    return size_kb


def process_action(action_dir: Path, output_base: Path, action_name: str,
                   use_rembg: bool = True):
    """处理一个动作的所有帧"""
    output_dir = output_base / action_name

    # 查找所有图片文件
    exts = {".png", ".webp", ".jpg", ".jpeg"}
    frames = sorted([f for f in action_dir.iterdir() if f.suffix.lower() in exts])

    if not frames:
        # 也许帧文件在 action_dir 的平铺结构里
        # ComfyUI 输出结构: ComfyUI/output/mascot_{action}_00001_.png
        parent = action_dir.parent
        prefix = f"mascot_{action_name}"
        frames = sorted([f for f in parent.iterdir()
                        if f.name.startswith(prefix) and f.suffix.lower() in exts])

    if not frames:
        print(f"  ⚠ No frames for '{action_name}' — skipping")
        return 0, 0

    total_size = 0
    for i, fpath in enumerate(frames):
        try:
            size_kb = process_frame(fpath, output_dir, action_name, i, use_rembg)
            total_size += size_kb
        except Exception as e:
            print(f"  ✗ Frame {i} failed: {e}")
            continue

    avg_kb = total_size / len(frames) if frames else 0
    return len(frames), avg_kb


def main():
    parser = argparse.ArgumentParser(description="Post-process generated mascot frames")
    parser.add_argument("--input", default=str(DEFAULT_INPUT),
                        help="Input directory with per-action frame subdirectories")
    parser.add_argument("--no-rembg", action="store_true",
                        help="Skip background removal")
    parser.add_argument("--action", default=None,
                        help="Process only one specific action (default: all)")
    args = parser.parse_args()

    input_dir = Path(args.input)
    if not input_dir.is_dir():
        print(f"  ❌ Input directory not found: {input_dir}")
        print(f"  💡 If you used ComfyUI, check ComfyUI/output/ directory")
        print(f"     and copy frames to {DEFAULT_INPUT}/ first.")
        sys.exit(1)

    use_rembg = not args.no_rembg

    if use_rembg:
        try:
            import rembg
        except ImportError:
            print("  ⚠ rembg not installed. Install with: pip install rembg")
            print("  ⚠ Falling back to no background removal.")
            use_rembg = False

    output_base = input_dir.parent / "processed-frames"

    print(f"\n  🔧 Processing frames from: {input_dir}")
    print(f"  📍 Output: {output_base}")
    print(f"  🎨 Background removal: {'rembg (AI)' if use_rembg else 'SKIP'}")
    print(f"  📐 Target canvas: {OUTPUT_CANVAS[0]}×{OUTPUT_CANVAS[1]}")
    print()

    actions = [args.action] if args.action else sorted(
        d.name for d in input_dir.iterdir() if d.is_dir()
    )

    results = {}
    for action_name in actions:
        action_dir = input_dir / action_name
        if not action_dir.is_dir():
            print(f"  ⚠ '{action_name}' not found — skipping")
            continue

        n_frames, avg_kb = process_action(action_dir, output_base, action_name, use_rembg)
        results[action_name] = (n_frames, avg_kb)
        print(f"  ✓ {action_name:10s}  {n_frames:3d} frames  "
              f"avg {avg_kb:.1f}KB/frame  total ~{n_frames * avg_kb:.0f}KB")

    if not results:
        print("\n  ❌ No frames processed!")
        sys.exit(1)

    total_frames = sum(r[0] for r in results.values())
    total_est_size = sum(r[0] * r[1] for r in results.values())

    print(f"\n  ✅ Done! {total_frames} frames across {len(results)} actions")
    print(f"     Estimated packed Lottie size: ~{total_est_size:.0f} KB")
    print(f"\n  Next step:")
    print(f"    python scripts/pack_lottie.py --frames-dir {output_base} --optimize")


if __name__ == "__main__":
    main()
