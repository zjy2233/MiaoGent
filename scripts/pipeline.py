#!/usr/bin/env python
"""
MiaoGent 吉祥物动画批量生成管线
══════════════════════════════════════════════════════════

完整流程: 现有素材 → 参考帧 → AI生成 → 后处理 → Lottie组装

用法:
  # 生成 mock 帧测试管线（无需 AI）
  python scripts/pipeline.py --mock

  # 检查管线就绪状态
  python scripts/pipeline.py --check

  # 从 ComfyUI 输出目录处理后组装
  python scripts/pipeline.py --process-from <comfyui_output_dir>

  # 全自动: 检查 → 处理 → 组装 → 校验
  python scripts/pipeline.py --auto --frames-dir <dir>
"""

import os
import io
import sys
import json
import shutil
import argparse
from pathlib import Path
from datetime import datetime

# Windows console UTF-8 support
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── 路径定义 ──────────────────────────────────────
FRAMES_DIR = ROOT / "temp" / "generated-frames"
PROCESSED_DIR = ROOT / "temp" / "processed-frames"
OUTPUT_LOTTIE = ROOT / "frontend" / "assets" / "mascot.json"
BACKUP_LOTTIE = ROOT / "frontend" / "assets" / "mascot_old.json"
STYLE_SHEET = ROOT / "temp" / "mascot-frames" / "style_reference_sheet.png"
STYLE_REPORT = ROOT / "temp" / "mascot-frames" / "style_report.json"
COMfYUI_DIR = ROOT / "scripts" / "comfyui_workflows"

# ── 动作清单 ──────────────────────────────────────
ACTIONS = [
    ("idle",    64, 25, True,  "Idle breathing"),
    ("walk",    48, 24, True,  "Walking cycle"),
    ("sleep",   80, 20, True,  "Sleeping loop"),
    ("sit",     64, 24, True,  "Sitting pose"),
    ("stretch", 48, 24, False, "Wake-up stretch"),
    ("jump",    32, 24, False, "Happy jump"),
    ("wave",    48, 24, True,  "Paw wave"),
    ("think",   64, 25, True,  "Thinking pose"),
    ("look",    48, 24, True,  "Looking around"),
]


class Pipeline:
    def __init__(self, args):
        self.args = args
        self.start_time = datetime.now()
        self.steps_completed = []
        self.errors = []

    def log(self, msg, level="INFO"):
        prefix = {"INFO": "  >>", "OK": "  [OK]", "ERR": "  [ERR]", "WARN": "  [WARN]"}.get(level, "  --")
        print(f"{prefix} {msg}")

    def check_prerequisites(self):
        """检查环境依赖"""
        self.log("Checking prerequisites...", "INFO")

        checks = []

        # Python 库
        for lib, desc in [
            ("PIL", "Pillow (image processing)"),
            ("json", "JSON (built-in)"),
        ]:
            try:
                __import__(lib.lower() if lib != "PIL" else "PIL")
                checks.append((desc, True, ""))
            except ImportError:
                checks.append((desc, False, f"pip install {lib}"))

        # 可选: rembg
        try:
            __import__("rembg")
            checks.append(("rembg (background removal)", True, ""))
        except ImportError:
            checks.append(("rembg (background removal)", False, "pip install rembg (optional)"))

        # 文件检查
        for fpath, desc in [
            (STYLE_SHEET, "Style reference sheet"),
            (STYLE_REPORT, "Style report"),
            (COMfYUI_DIR, "ComfyUI workflows"),
        ]:
            if fpath.exists():
                checks.append((desc, True, ""))
            else:
                checks.append((desc, False, f"Run: python scripts/extract_frames.py (for ref sheet)"))

        all_ok = True
        for desc, ok, hint in checks:
            status = "OK" if ok else "MISSING"
            extra = f" -> {hint}" if not ok else ""
            self.log(f"{desc}: {status}{extra}", "OK" if ok else "WARN")
            if not ok:
                all_ok = False

        return all_ok

    def generate_mock_frames(self):
        """生成测试用的占位帧（用于验证管线，无需 AI）"""
        self.log("Generating mock frames for pipeline testing...", "INFO")

        from PIL import Image, ImageDraw, ImageFont

        FRAMES_DIR.mkdir(parents=True, exist_ok=True)

        # 基于原素材的色板
        colors = {
            "idle":    (75, 76, 108),
            "walk":    (68, 71, 104),
            "sleep":   (62, 58, 97),
            "sit":     (73, 75, 107),
            "stretch": (80, 78, 112),
            "jump":    (90, 80, 115),
            "wave":    (78, 79, 110),
            "think":   (75, 76, 108),
            "look":    (70, 74, 106),
        }

        for action_name, n_frames, fps, loop, desc in ACTIONS:
            action_dir = FRAMES_DIR / action_name
            action_dir.mkdir(parents=True, exist_ok=True)

            base_color = colors.get(action_name, (75, 76, 108))

            for i in range(n_frames):
                # 每帧稍有变化模拟动画
                offset = int(5 * (i / n_frames - 0.5) * 2)  # -5 ~ +5
                r = min(255, max(0, base_color[0] + offset))
                g = min(255, max(0, base_color[1] + offset))
                b = min(255, max(0, base_color[2] + offset))

                img = Image.new("RGBA", (400, 300), (0, 0, 0, 0))
                draw = ImageDraw.Draw(img)

                # 简单的圆形代表角色
                cx, cy = 200, 150
                radius = 60 + offset * 2
                draw.ellipse(
                    [cx - radius, cy - radius, cx + radius, cy + radius],
                    fill=(r, g, b, 255),
                    outline=(r + 20, g + 20, b + 20, 200),
                    width=2,
                )

                # 标签
                label = f"{action_name} f{i}"
                bbox = draw.textbbox((0, 0), label)
                tw = bbox[2] - bbox[0]
                draw.text((200 - tw // 2, 10), label, fill=(255, 255, 255, 180))

                fpath = action_dir / f"frame_{i:03d}.webp"
                img.save(fpath, "WEBP", quality=60, alpha_quality=30)
                img.close()

            size_kb = sum(os.path.getsize(str(f)) for f in action_dir.iterdir()) / 1024
            self.log(f"  {action_name:10s} {n_frames:3d}f ~{size_kb:.0f}KB [{desc}]", "OK")

        self.log(f"Mock frames generated -> {FRAMES_DIR}", "OK")
        return FRAMES_DIR

    def process_frames(self, input_dir=None):
        """后处理: 去背景 + 优化"""
        input_dir = input_dir or FRAMES_DIR
        self.log(f"Processing frames from {input_dir}...", "INFO")

        # Simple copy + optional resize (rembg handled by separate process_frames.py if needed)
        results = {}
        for action_name, n_frames, fps, loop, desc in ACTIONS:
            action_path = Path(input_dir) / action_name
            if not action_path.is_dir():
                self.log(f"  Skip '{action_name}' — not found", "WARN")
                continue

            try:
                dest = PROCESSED_DIR / action_name
                dest.mkdir(parents=True, exist_ok=True)
                copied = 0
                for f in sorted(action_path.iterdir()):
                    if f.suffix.lower() in ('.webp', '.png', '.jpg', '.jpeg'):
                        shutil.copy2(f, dest / f.name)
                        copied += 1

                if copied > 0:
                    size_kb = sum(os.path.getsize(str(x)) for x in dest.iterdir()) / 1024
                    results[action_name] = (copied, size_kb / copied)
                    self.log(f"  {action_name:10s} {copied:3d}f ~{size_kb:.0f}KB", "OK")
            except Exception as e:
                self.log(f"  {action_name:10s} FAILED: {e}", "ERR")

        self.steps_completed.append("process")
        return results

    def pack_lottie(self, frames_dir=None):
        """组装 Lottie JSON"""
        frames_dir = frames_dir or PROCESSED_DIR
        self.log(f"Packing Lottie from {frames_dir}...", "INFO")

        import importlib.util
        spec = importlib.util.spec_from_file_location("pack_lottie", str(ROOT / "scripts" / "pack_lottie.py"))
        pack_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pack_mod)

        if not Path(frames_dir).is_dir():
            self.log(f"Frames dir not found: {frames_dir}", "ERR")
            return False

        # 备份现有文件
        if OUTPUT_LOTTIE.exists():
            shutil.copy2(OUTPUT_LOTTIE, BACKUP_LOTTIE)
            self.log(f"Backup: {OUTPUT_LOTTIE} -> {BACKUP_LOTTIE}", "INFO")

        lottie = pack_mod.build_lottie(str(frames_dir), optimize=True, quality=80)

        if not lottie.get("layers"):
            self.log("No layers — nothing to pack!", "ERR")
            return False

        with open(OUTPUT_LOTTIE, "w", encoding="utf-8") as f:
            json.dump(lottie, f, ensure_ascii=False)

        total_kb = os.path.getsize(OUTPUT_LOTTIE) / 1024
        self.log(
            f"Packed {len(lottie['markers'])} actions, {lottie['op']} frames, "
            f"{total_kb:.0f}KB -> {OUTPUT_LOTTIE}",
            "OK",
        )

        # 校验
        issues, warnings = pack_mod.validate_lottie(str(OUTPUT_LOTTIE))
        pack_mod.print_validation(str(OUTPUT_LOTTIE), issues, warnings)

        if issues:
            self.errors.extend(issues)
            return False

        self.steps_completed.append("pack")
        return True

    def print_summary(self):
        """打印管线执行摘要"""
        elapsed = (datetime.now() - self.start_time).total_seconds()
        print(f"\n{'='*60}")
        print(f"  Pipeline Summary")
        print(f"{'='*60}")
        print(f"  Steps completed: {', '.join(self.steps_completed) or '(none)'}")
        print(f"  Errors: {len(self.errors)}")
        if self.errors:
            for e in self.errors:
                print(f"    - {e}")
        print(f"  Duration: {elapsed:.1f}s")
        print(f"  Output: {OUTPUT_LOTTIE}")
        print(f"{'='*60}\n")

    def run(self):
        """主流程"""
        print(f"\n{'='*60}")
        print(f"  MiaoGent Mascot Animation Pipeline")
        print(f"  Started: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}\n")

        # 1. 检查
        if not self.args.no_check:
            ok = self.check_prerequisites()
            if not ok and not self.args.mock:
                self.log("Some prerequisites missing — proceed with caution", "WARN")

        # 2. 生成/加载帧
        if self.args.mock:
            self.generate_mock_frames()
            self.steps_completed.append("mock")

        if self.args.process_from:
            process_input = Path(self.args.process_from)
            self.process_frames(process_input)
        elif self.args.mock:
            self.process_frames(FRAMES_DIR)

        # 3. 组装
        if self.args.auto or self.args.mock:
            if PROCESSED_DIR.is_dir() and any(PROCESSED_DIR.iterdir()):
                self.pack_lottie()
            elif FRAMES_DIR.is_dir() and any(FRAMES_DIR.iterdir()):
                self.pack_lottie(FRAMES_DIR)
            else:
                self.log("No frames to pack. Generate frames first.", "WARN")

        if self.args.frames_dir:
            self.pack_lottie(Path(self.args.frames_dir))

        # 4. 摘要
        self.print_summary()


def main():
    parser = argparse.ArgumentParser(
        description="MiaoGent Mascot Animation Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/pipeline.py --check           Check pipeline readiness
  python scripts/pipeline.py --mock            Test pipeline with placeholder frames
  python scripts/pipeline.py --process-from <dir>  Process AI-generated frames
  python scripts/pipeline.py --auto --frames-dir <dir>  Full auto pipeline
        """,
    )

    parser.add_argument("--check", action="store_true",
                        help="Only check prerequisites, don't run pipeline")
    parser.add_argument("--mock", action="store_true",
                        help="Generate mock placeholder frames to test pipeline")
    parser.add_argument("--auto", action="store_true",
                        help="Run full pipeline automatically")
    parser.add_argument("--process-from", default=None,
                        help="Path to directory with AI-generated frames")
    parser.add_argument("--frames-dir", default=None,
                        help="Path to processed frames for packing")
    parser.add_argument("--no-check", action="store_true",
                        help="Skip prerequisite checks")

    args = parser.parse_args()

    pipeline = Pipeline(args)

    if args.check:
        pipeline.check_prerequisites()
        return

    pipeline.run()


if __name__ == "__main__":
    main()
