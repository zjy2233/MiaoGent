"""
ComfyUI 动画生成工作流构建器
─────────────────────────────────────
基于 IPAdapter 风格迁移 + AnimateDiff 运动生成
输入: 参考帧 (temp/mascot-frames/references/)
输出: 带透明背景的 WebP 帧序列

安装 ComfyUI (一次性):
  git clone https://github.com/comfyanonymous/ComfyUI.git
  cd ComfyUI
  pip install -r requirements.txt

必需的自定义节点 (通过 ComfyUI Manager 安装):
  - ComfyUI_IPAdapter_plus        (IPAdapter 风格迁移)
  - ComfyUI-AnimateDiff-Evolved   (AnimateDiff 运动生成)
  - ComfyUI-KJNodes               (工具节点)
  - ComfyUI-VideoHelperSuite      (视频/帧加载)

必需模型 (放入 ComfyUI/models/):
  animate_diff:
    - mm_sd_v15_v2.ckpt            (AnimateDiff 运动模块)
    - v3_sd15_mm.ckpt              (AnimateDiff v3, 更好质量)
  ipadapter:
    - ip-adapter_sd15.safetensors  (IPAdapter 基础模型)
    - ip-adapter-plus_sd15.safetensors (IPAdapter Plus, 更高精度)
  controlnet:
    - control_v11p_sd15_openpose.pth  (姿态控制)
  rembg: (通过 pip 安装: pip install rembg)
    - 用于批量去背景

工作流概览:
  参考图(style_ref_*.png) → IPAdapter (style injection)
  文本prompt → CLIP Text Encode → KSampler → AnimateDiff → VAE Decode
  姿态图(可选) → ControlNet OpenPose → (spatial guidance)
  输出帧 → REMBG 去背景 → 透明 WebP
"""

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REF_DIR = ROOT / "temp" / "mascot-frames" / "references"
OUT_DIR = ROOT / "scripts"


# ── 动画清单与 Prompt ──────────────────────────
# 9 个目标动作，保持完全一致的风格

ANIMATIONS = {
    "idle": {
        "desc": "待机呼吸循环",
        "frames": 64,
        "fps": 25,
        "prompt": (
            "1girl, dark purple cat girl, kawaii style, idle standing, "
            "breathing softly, subtle body sway, hands slightly moving, "
            "dark theme, flat shading, soft outlines, minimalist anime, "
            "purple-blue color scheme, front view, full body, "
            "simple green screen background"
        ),
        "negative": "worst quality, low quality, distorted face, deformed hands, "
                    "blurry, noisy, complex background, bright colors, "
                    "3D, realistic, photorealistic, different character, "
                    "extra limbs, mutated, watermark, text, signature",
        "seed": 42,
        "cfg": 7.0,
    },
    "walk": {
        "desc": "侧面行走循环",
        "frames": 48,
        "fps": 24,
        "prompt": (
            "1girl, dark purple cat girl, kawaii style, walking cycle side view, "
            "bouncy walk, arms swinging, tail swaying, "
            "dark theme, flat shading, soft outlines, minimalist anime, "
            "purple-blue color scheme, full body, looping walk animation, "
            "simple green screen background"
        ),
        "negative": "worst quality, low quality, distorted face, deformed hands, "
                    "blurry, noisy, complex background, bright colors, "
                    "3D, realistic, photorealistic, extra limbs, mutated, "
                    "static pose, floating, sliding feet",
        "seed": 123,
        "cfg": 7.0,
    },
    "sleep": {
        "desc": "卧姿睡眠呼吸",
        "frames": 80,
        "fps": 20,
        "prompt": (
            "1girl, dark purple cat girl, kawaii style, sleeping peacefully, "
            "lying down curled up, gentle breathing, chest rising and falling, "
            "Zzz floating above, dark theme, flat shading, soft outlines, "
            "minimalist anime, purple-blue color scheme, peaceful atmosphere, "
            "simple green screen background"
        ),
        "negative": "worst quality, low quality, distorted face, deformed body, "
                    "blurry, noisy, complex background, bright colors, "
                    "moving around, awake, standing, 3D, realistic",
        "seed": 256,
        "cfg": 7.0,
    },
    "sit": {
        "desc": "蹲坐姿态",
        "frames": 64,
        "fps": 24,
        "prompt": (
            "1girl, dark purple cat girl, kawaii style, sitting on ground, "
            "tail wrapped around, occasional ear twitch, gentle idle motion, "
            "dark theme, flat shading, soft outlines, minimalist anime, "
            "purple-blue color scheme, front view, full body seated, "
            "simple green screen background"
        ),
        "negative": "worst quality, low quality, distorted face, deformed hands, "
                    "blurry, noisy, complex background, bright colors, "
                    "standing, walking, 3D, realistic, extra limbs",
        "seed": 789,
        "cfg": 7.0,
    },
    "stretch": {
        "desc": "醒后伸懒腰",
        "frames": 48,
        "fps": 24,
        "prompt": (
            "1girl, dark purple cat girl, kawaii style, stretching body, "
            "arms reaching up, yawning, waking up from sleep, "
            "full body stretch motion, dark theme, flat shading, soft outlines, "
            "minimalist anime, purple-blue color scheme, front view, "
            "simple green screen background"
        ),
        "negative": "worst quality, low quality, distorted face, deformed hands, "
                    "blurry, noisy, complex background, bright colors, "
                    "static, still, 3D, realistic, extra limbs, mutated",
        "seed": 444,
        "cfg": 7.0,
    },
    "jump": {
        "desc": "开心跳跃",
        "frames": 32,
        "fps": 24,
        "prompt": (
            "1girl, dark purple cat girl, kawaii style, jumping up excitedly, "
            "both feet off ground, arms raised, happy expression, "
            "bouncy jump animation, dark theme, flat shading, soft outlines, "
            "minimalist anime, purple-blue color scheme, front view, "
            "simple green screen background"
        ),
        "negative": "worst quality, low quality, distorted face, deformed body, "
                    "blurry, noisy, complex background, bright colors, "
                    "standing, static, 3D, realistic, extra limbs",
        "seed": 555,
        "cfg": 7.0,
    },
    "wave": {
        "desc": "挥手打招呼",
        "frames": 48,
        "fps": 24,
        "prompt": (
            "1girl, dark purple cat girl, kawaii style, waving hand at viewer, "
            "friendly greeting gesture, slight body sway, smiling, "
            "dark theme, flat shading, soft outlines, minimalist anime, "
            "purple-blue color scheme, front view, upper body focused, "
            "simple green screen background"
        ),
        "negative": "worst quality, low quality, distorted face, deformed hands, "
                    "blurry, noisy, complex background, bright colors, "
                    "both hands still, static, 3D, realistic, extra fingers",
        "seed": 666,
        "cfg": 7.0,
    },
    "think": {
        "desc": "思考姿态",
        "frames": 64,
        "fps": 25,
        "prompt": (
            "1girl, dark purple cat girl, kawaii style, thinking pose, "
            "hand on chin, tilting head, question marks appearing, "
            "gentle head bob, contemplative expression, "
            "dark theme, flat shading, soft outlines, minimalist anime, "
            "purple-blue color scheme, front view, upper body, "
            "simple green screen background"
        ),
        "negative": "worst quality, low quality, distorted face, deformed hands, "
                    "blurry, noisy, complex background, bright colors, "
                    "action, moving fast, 3D, realistic, extra limbs",
        "seed": 777,
        "cfg": 7.0,
    },
    "look": {
        "desc": "左右张望",
        "frames": 48,
        "fps": 24,
        "prompt": (
            "1girl, dark purple cat girl, kawaii style, looking around, "
            "head turning left to right, ears perking up, curious expression, "
            "upper body only, subtle movement, dark theme, flat shading, "
            "soft outlines, minimalist anime, purple-blue color scheme, "
            "simple green screen background"
        ),
        "negative": "worst quality, low quality, distorted face, deformed features, "
                    "blurry, noisy, complex background, bright colors, "
                    "walking, jumping, 3D, realistic, extra limbs, mutated",
        "seed": 888,
        "cfg": 7.0,
    },
}


# ── ComfyUI 工作流模板 ──────────────────────────
# 这是工作流的 JSON 定义，可直接导入 ComfyUI
# 节点 ID 和连线定义

def build_workflow(action_name, config):
    """
    构建单个动作的 ComfyUI 工作流 JSON。
    使用 IPAdapter + AnimateDiff 生成风格一致的动画。
    """
    wf = {
        "last_node_id": 200,
        "last_link_id": 300,
        "nodes": [],
        "links": [],
        "groups": [],
        "config": {},
        "extra": {
            "action_name": action_name,
            "prompt": config["prompt"],
            "negative_prompt": config["negative"],
            "seed": config["seed"],
            "frames": config["frames"],
            "fps": config["fps"],
        },
        "version": 0.4,
    }

    # ── 辅助函数 ──
    nid = [0]

    def next_id():
        nid[0] += 1
        return nid[0]

    def node(cls_type, inputs, pos=None):
        id_ = next_id()
        return {
            "id": id_,
            "type": cls_type,
            "pos": pos or [0, 0],
            "size": [1, 1],
            "flags": {},
            "order": id_,
            "mode": 0,
            "inputs": [
                {"name": k, "type": v[0], "link": v[1]} if isinstance(v, list) else
                {"name": k, "type": "STRING", "widget": {"name": k}, "value": v}
                for k, v in inputs.items()
            ],
            "outputs": [],
            "properties": {"Node name for S&R": cls_type},
            "widgets_values": [],
        }

    def link(from_id, from_slot, to_id, to_slot):
        id_ = next_id()
        return {
            "id": id_,
            "origin_id": from_id,
            "origin_slot": from_slot,
            "target_id": to_id,
            "target_slot": to_slot,
            "type": "MODEL",
        }

    nodes = wf["nodes"]
    links_list = wf["links"]

    # ── 1. 加载参考图 ──
    ref_loader = node(
        "LoadImage",
        {"image": "style_reference_sheet.png"},
        [50, 100],
    )
    nodes.append(ref_loader)

    # ── 2. 模型加载 (MODEL + CLIP + VAE) ──
    model_loader = node(
        "CheckpointLoaderSimple",
        {"ckpt_name": "v1-5-pruned-emaonly.safetensors"},
        [50, 250],
    )
    nodes.append(model_loader)

    # ── 3. CLIP 文本编码 (使用 checkpoint 自带 CLIP 输出) ──
    pos_prompt = node(
        "CLIPTextEncode",
        {"text": config["prompt"], "clip": ["CLIP", 0]},
        [300, 200],
    )
    nodes.append(pos_prompt)

    neg_prompt = node(
        "CLIPTextEncode",
        {"text": config["negative"], "clip": ["CLIP", 0]},
        [300, 350],
    )
    nodes.append(neg_prompt)

    # 从 CheckpointLoaderSimple CLIP 输出 (slot 1) 连线到 text encoder
    links_list.append(link(model_loader["id"], 1, pos_prompt["id"], 1))
    links_list.append(link(model_loader["id"], 1, neg_prompt["id"], 1))

    # ── 4. CLIP Vision 加载 (IPAdapter 必需) ──
    clip_vision_loader = node(
        "CLIPVisionLoader",
        {"clip_name": "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"},
        [50, 400],
    )
    nodes.append(clip_vision_loader)

    # ── 5. IPAdapter 风格注入 ──
    ipa_loader = node(
        "IPAdapterModelLoader",
        {"ipadapter_file": "ip-adapter-plus_sd15.safetensors"},
        [50, 550],
    )
    nodes.append(ipa_loader)

    ipa_apply = node(
        "IPAdapterApply",
        {
            "ipadapter": ["IPADAPTER", 0],
            "clip_vision": ["CLIP_VISION", 0],
            "image": ["IMAGE", 0],
            "model": ["MODEL", 0],
            "weight": 0.85,
            "noise": 0.0,
            "weight_type": "style transfer",
        },
        [350, 500],
    )
    nodes.append(ipa_apply)

    links_list.append(link(ipa_loader["id"], 0, ipa_apply["id"], 0))
    links_list.append(link(clip_vision_loader["id"], 0, ipa_apply["id"], 1))
    links_list.append(link(ref_loader["id"], 0, ipa_apply["id"], 2))
    links_list.append(link(model_loader["id"], 0, ipa_apply["id"], 3))

    # ── 5. AnimateDiff 运动模块 ──
    ad_loader = node(
        "AnimateDiffLoaderV1",
        {
            "model": ["MODEL", 0],
            "motion_module": "mm_sd_v15_v2.ckpt",
            "context_length": 16,
        },
        [600, 400],
    )
    nodes.append(ad_loader)
    links_list.append(link(ipa_apply["id"], 0, ad_loader["id"], 0))

    # ── 6. 空 Latent (批次 = 帧数) ──
    empty_latent = node(
        "EmptyLatentImage",
        {
            "width": 512,
            "height": 512,
            "batch_size": config["frames"],
        },
        [600, 100],
    )
    nodes.append(empty_latent)

    # ── 7. KSampler ──
    sampler = node(
        "KSampler",
        {
            "model": ["MODEL", 0],
            "positive": ["CONDITIONING", 0],
            "negative": ["CONDITIONING", 0],
            "latent_image": ["LATENT", 0],
            "seed": config["seed"],
            "steps": 25,
            "cfg": config["cfg"],
            "sampler_name": "euler_ancestral",
            "scheduler": "normal",
            "denoise": 1.0,
        },
        [900, 300],
    )
    nodes.append(sampler)
    links_list.append(link(ad_loader["id"], 0, sampler["id"], 0))
    links_list.append(link(pos_prompt["id"], 0, sampler["id"], 1))
    links_list.append(link(neg_prompt["id"], 0, sampler["id"], 2))
    links_list.append(link(empty_latent["id"], 0, sampler["id"], 3))

    # ── 8. VAE 解码 ──
    vae_decode = node(
        "VAEDecode",
        {
            "samples": ["LATENT", 0],
            "vae": ["VAE", 0],
        },
        [1150, 300],
    )
    nodes.append(vae_decode)
    links_list.append(link(sampler["id"], 0, vae_decode["id"], 0))
    links_list.append(link(model_loader["id"], 2, vae_decode["id"], 1))

    # ── 9. 保存帧 ──
    save_frames = node(
        "SaveImage",
        {
            "images": ["IMAGE", 0],
            "filename_prefix": f"mascot_{action_name}",
        },
        [1400, 300],
    )
    nodes.append(save_frames)
    links_list.append(link(vae_decode["id"], 0, save_frames["id"], 0))

    return wf


def build_all_workflows():
    """生成所有动作的 ComfyUI 工作流"""
    workflows_dir = OUT_DIR / "comfyui_workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)

    for name, config in ANIMATIONS.items():
        wf = build_workflow(name, config)
        wf_path = workflows_dir / f"mascot_{name}.json"
        with open(wf_path, "w", encoding="utf-8") as f:
            json.dump(wf, f, indent=2, ensure_ascii=False)
        print(f"  [OK] {wf_path}  ({config['desc']}, {config['frames']}f @ {config['fps']}fps)")

    # 生成批量运行的索引文件
    index = {
        "description": "MiaoGent 吉祥物动画批量生成工作流",
        "reference_image": "temp/mascot-frames/style_reference_sheet.png",
        "style_guide": "temp/mascot-frames/style_report.json",
        "output_dir": "ComfyUI/output/",
        "actions": {
            name: {
                "desc": cfg["desc"],
                "frames": cfg["frames"],
                "fps": cfg["fps"],
                "seed": cfg["seed"],
            }
            for name, cfg in ANIMATIONS.items()
        },
    }
    index_path = workflows_dir / "_batch_index.json"
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)

    print(f"\n  [OK] Generated {len(ANIMATIONS)} workflows -> {workflows_dir}")
    return workflows_dir


def print_human_readable_guide():
    """输出人类可读的操作指南"""
    guide = f"""
================================================================
       MiaoGent Mascot Animation AI Generation Guide
================================================================

  Goal: Generate 9 cat-girl animations matching existing style
  Method: ComfyUI + IPAdapter(style lock) + AnimateDiff(motion)

================================================================

  [Step 1] Install ComfyUI
  --------------------------------------------
  git clone https://github.com/comfyanonymous/ComfyUI.git
  cd ComfyUI && pip install -r requirements.txt

  [Step 2] Install custom nodes (via ComfyUI Manager)
  --------------------------------------------
  - ComfyUI_IPAdapter_plus
  - ComfyUI-AnimateDiff-Evolved
  - ComfyUI-VideoHelperSuite

  [Step 3] Download models (put in ComfyUI/models/)
  --------------------------------------------
  animate_diff/:
    mm_sd_v15_v2.ckpt  (huggingface.co/guoyww/animatediff)
  ipadapter/:
    ip-adapter-plus_sd15.safetensors
  checkpoints/:
    dreamshaper_8.safetensors  (or any SD1.5 model)
  clip/:
    sd_v1-5.safetensors  (CLIP model)

  [Step 4] Prepare reference image
  --------------------------------------------
  Reference: temp/mascot-frames/references/
  Style sheet: temp/mascot-frames/style_reference_sheet.png
  Copy style sheet to ComfyUI/input/ directory

  [Step 5] Import workflow and generate
  --------------------------------------------
  1. Start ComfyUI: python main.py
  2. Open http://127.0.0.1:8188 in browser
  3. Load -> scripts/comfyui_workflows/mascot_<action>.json
  4. Verify reference image path is correct
  5. Queue Prompt -> wait for generation
  6. Generated frames are in ComfyUI/output/

  [Step 6] Remove background + pack Lottie
  --------------------------------------------
  python scripts/process_frames.py   (background removal + optimize)
  python scripts/pack_lottie.py      (assemble Lottie JSON)

================================================================

  TIP: If you don't have a GPU or ComfyUI setup is difficult,
  use online AI video tools as alternatives:

  - Kling (kling.kuaishou.com)    image-to-video, supports start/end frames
  - Jimeng (jimeng.jianying.com)  ByteDance video generation
  - Runway Gen-4 (runwayml.com)   professional AI video

  Online workflow:
  1. Upload temp/mascot-frames/references/ref_000.webp as reference
  2. Copy prompt from scripts/comfyui_workflows/_batch_index.json
  3. Download generated mp4
  4. ffmpeg -i video.mp4 -vf "fps=25,scale=512:512" frames/%04d.png
  5. python scripts/process_frames.py && python scripts/pack_lottie.py

================================================================
"""
    print(guide)


if __name__ == "__main__":
    build_all_workflows()
    print_human_readable_guide()
