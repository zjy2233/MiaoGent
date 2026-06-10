"""
ComfyUI 环境自动配置 — 安装自定义节点 + 下载必需模型
用法: python scripts/setup_comfyui.py

按优先级处理:
  1. 安装自定义节点 (git clone)
  2. 拷贝参考图到 ComfyUI/input/
  3. 打印模型下载清单 (需手动下载或使用 huggingface-cli)
"""

import os
import sys
import json
import shutil
import subprocess
import urllib.request
from pathlib import Path

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
COMfyUI = Path("D:/AI/ComfyUI")
CUSTOM_NODES = COMfyUI / "custom_nodes"
MODELS = COMfyUI / "models"
INPUT = COMfyUI / "input"
STYLE_SHEET = ROOT / "temp" / "mascot-frames" / "style_reference_sheet.png"
REF_DIR = ROOT / "temp" / "mascot-frames" / "references"

# ── 必需的自定义节点 ──────────────────────────
CUSTOM_NODE_REPOS = {
    "ComfyUI-IPAdapter_plus": {
        "url": "https://github.com/cubiq/ComfyUI_IPAdapter_plus.git",
        "desc": "IPAdapter 风格迁移 (核心)",
    },
    "ComfyUI-AnimateDiff-Evolved": {
        "url": "https://github.com/Kosinkadink/ComfyUI-AnimateDiff-Evolved.git",
        "desc": "AnimateDiff 运动生成 (核心)",
    },
    "ComfyUI-KJNodes": {
        "url": "https://github.com/kijai/ComfyUI-KJNodes.git",
        "desc": "工具节点集",
    },
    "ComfyUI-VideoHelperSuite": {
        "url": "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git",
        "desc": "视频/帧加载",
    },
}

# ── 必需模型清单 ─────────────────────────────
# 格式: (文件名, 保存路径, HuggingFace URL, 大小, 是否必需)
MODELS_NEEDED = [
    {
        "name": "v1-5-pruned-emaonly.safetensors",
        "dest": "checkpoints",
        "url": "https://modelscope.cn/models/AI-ModelScope/stable-diffusion-v1-5/resolve/master/v1-5-pruned-emaonly.safetensors",
        "size": "~4.0 GB",
        "required": True,
        "desc": "SD 1.5 基础模型 (via ModelScope)",
    },
    {
        "name": "ip-adapter-plus_sd15.safetensors",
        "dest": "ipadapter",
        "url": "https://huggingface.co/h94/IP-Adapter/resolve/main/models/ip-adapter-plus_sd15.safetensors",
        "size": "~380 MB",
        "required": True,
        "desc": "IPAdapter Plus 风格注入",
    },
    {
        "name": "mm_sd_v15_v2.ckpt",
        "dest": "animatediff_models",
        "url": "https://huggingface.co/guoyww/animatediff/resolve/main/mm_sd_v15_v2.ckpt",
        "size": "~1.3 GB",
        "required": True,
        "desc": "AnimateDiff v2 运动模块",
    },
    {
        "name": "v3_sd15_mm.ckpt",
        "dest": "animatediff_models",
        "url": "https://huggingface.co/guoyww/animatediff/resolve/main/v3_sd15_mm.ckpt",
        "size": "~1.3 GB",
        "required": False,
        "desc": "AnimateDiff v3 (更高质量)",
    },
    {
        "name": "sd1.5.json",
        "dest": "clip_vision",
        "url": "https://huggingface.co/h94/IP-Adapter/resolve/main/models/image_encoder/config.json",
        "size": "~1 KB",
        "required": True,
        "desc": "CLIP Vision 配置",
        "rename": "config.json",
    },
    {
        "name": "model.safetensors",
        "dest": "clip_vision",
        "url": "https://huggingface.co/h94/IP-Adapter/resolve/main/models/image_encoder/model.safetensors",
        "size": "~1.5 GB",
        "required": True,
        "desc": "CLIP Vision 模型 (IPAdapter 依赖)",
        "rename": "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors",
    },
]


def run_cmd(cmd, desc):
    """运行命令并打印状态"""
    print(f"  [{desc}]")
    try:
        result = subprocess.run(cmd, shell=True, cwd=str(COMfyUI),
                                capture_output=True, text=True, timeout=300)
        if result.returncode == 0:
            print(f"    OK")
        else:
            err = result.stderr.strip()[-200:] if result.stderr else "unknown"
            print(f"    ERR: {err}")
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"    TIMEOUT (5min)")
        return False
    except Exception as e:
        print(f"    FAIL: {e}")
        return False


def install_custom_nodes():
    """git clone 自定义节点"""
    print("\n[1/4] Installing custom nodes...")
    CUSTOM_NODES.mkdir(parents=True, exist_ok=True)

    for name, info in CUSTOM_NODE_REPOS.items():
        target = CUSTOM_NODES / name
        if target.is_dir():
            print(f"  {name}: already installed, skipping")
            continue

        print(f"  {name}: cloning... ({info['desc']})")
        cmd = f'git clone "{info["url"]}" "{target}"'
        run_cmd(cmd, name)

    print("  Done.")


def copy_reference_images():
    """拷贝参考图到 ComfyUI input"""
    print("\n[2/4] Copying reference images...")
    INPUT.mkdir(parents=True, exist_ok=True)

    files_copied = 0
    # 风格参考表
    if STYLE_SHEET.exists():
        shutil.copy2(STYLE_SHEET, INPUT / "style_reference_sheet.png")
        print(f"  style_reference_sheet.png -> ComfyUI/input/")
        files_copied += 1

    # 单独参考帧
    if REF_DIR.is_dir():
        for ref_file in sorted(REF_DIR.glob("ref_*.webp")):
            shutil.copy2(ref_file, INPUT / ref_file.name)
        files_copied += 1
        print(f"  {len(list(REF_DIR.glob('ref_*.webp')))} reference frames -> ComfyUI/input/")

    print(f"  Copied {files_copied} files.")


def create_model_dirs():
    """创建必需的模型目录"""
    print("\n[3/4] Creating model directories...")
    dirs = ["ipadapter", "animatediff_models", "clip_vision"]
    for d in dirs:
        (MODELS / d).mkdir(parents=True, exist_ok=True)
        print(f"  {MODELS / d}")
    print("  Done.")


def download_models():
    """下载模型 (使用 huggingface_hub 或手动)"""
    print("\n[4/4] Model download status:")

    missing_required = []
    missing_optional = []

    for m in MODELS_NEEDED:
        dest_dir = MODELS / m["dest"]
        fname = m.get("rename", m["name"])
        fpath = dest_dir / fname

        if fpath.exists():
            size_mb = os.path.getsize(fpath) / (1024 * 1024)
            print(f"  [OK] {m['dest']}/{fname}  ({size_mb:.0f} MB)  — {m['desc']}")
        else:
            if m["required"]:
                missing_required.append(m)
                print(f"  [MISSING*] {m['dest']}/{fname}  ({m['size']})  — {m['desc']}")
            else:
                missing_optional.append(m)
                print(f"  [MISSING]  {m['dest']}/{fname}  ({m['size']})  — {m['desc']}")

    if missing_required:
        total_size_gb = sum(float(str(m['size']).replace('~','').replace(' GB','').replace(' MB','').replace(' KB','').strip()) *
                           (0.001 if 'MB' in str(m['size']) else 1 if 'GB' in str(m['size']) else 0.000001)
                           for m in missing_required)
        print(f"\n{'='*60}")
        print(f"  MISSING {len(missing_required)} REQUIRED models")
        print(f"{'='*60}")
        print(f"  You can download them via:\n")
        print(f"  1. Browser: open each URL below and save to the listed path")
        print(f"  2. huggingface-cli (recommended):\n")
        for m in missing_required:
            dest = MODELS / m["dest"] / m.get("rename", m["name"])
            print(f"     huggingface-cli download {m['url'].split('/resolve/')[0].replace('https://huggingface.co/', '')} \\")
            print(f"       {m['name']} --local-dir {MODELS / m['dest']}")
            if m.get("rename"):
                print(f"       # then rename to: {m.get('rename')}")
        print(f"\n  3. Or use the mirror for faster download in China:")
        print(f"     hf-mirror.com")
        print(f"\n  Total download: ~{total_size_gb:.1f} GB")
        print(f"{'='*60}\n")

    if missing_optional:
        print(f"  Optional (can skip for now):")
        for m in missing_optional:
            print(f"    - {m['name']} ({m['size']}) — {m['desc']}")

    return len(missing_required) == 0


def check_git():
    """检查 git 是否可用"""
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
        return True
    except Exception:
        return False


def main():
    print("=" * 60)
    print("  ComfyUI Environment Setup for MiaoGent Mascot")
    print(f"  ComfyUI path: {COMfyUI}")
    print("=" * 60)

    if not COMfyUI.is_dir():
        print(f"\n  ERROR: ComfyUI not found at {COMfyUI}")
        print(f"  Please install ComfyUI first or specify the correct path.")
        sys.exit(1)

    # 检查 git
    if not check_git():
        print("\n  WARNING: git not found. Custom node installation will fail.")
        print("  Please install Git: https://git-scm.com/download/win")
        print("  Or manually clone the repos listed in this script.")
        if input("\n  Continue anyway? (y/n): ").lower() != 'y':
            sys.exit(1)

    # 1. 安装自定义节点
    install_custom_nodes()

    # 2. 拷贝参考图
    copy_reference_images()

    # 3. 创建模型目录
    create_model_dirs()

    # 4. 检查模型
    all_ok = download_models()

    # ── 总结 ──
    print("\n" + "=" * 60)
    print("  Setup Summary")
    print("=" * 60)
    print(f"  ComfyUI:     {COMfyUI}")
    print(f"  Custom nodes: {len([d for d in CUSTOM_NODES.iterdir() if d.is_dir() and not d.name.startswith('.')])} installed")
    print(f"  Reference images: copied to ComfyUI/input/")
    print(f"  Models: {'ALL PRESENT' if all_ok else 'SOME MISSING'}")
    print()

    if not all_ok:
        print("  Next step: download the missing models (see list above), then run:")
        print("    cd D:/AI/ComfyUI && python main.py")
    else:
        print("  Run ComfyUI:")
        print("    cd D:/AI/ComfyUI && python main.py")
        print()
        print("  Then in browser: http://127.0.0.1:8188")
        print("  Load workflow from: scripts/comfyui_workflows/mascot_idle.json")
    print("=" * 60)


if __name__ == "__main__":
    main()
