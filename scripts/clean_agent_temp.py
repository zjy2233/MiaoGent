"""
Agent 临时文件清理工具
用途：清理 AI 助手产生的临时文件

清理范围：
  1. 项目目录中的临时文件（temp_*.py、__pycache__ 等）
  2. ~/.miaogent/temp/ 中的 Agent 生成脚本

运行方式：
  python scripts/clean_agent_temp.py              # 清理项目 + miaogent temp（24h 以上）
  python scripts/clean_agent_temp.py --dry-run     # 预览模式
  python scripts/clean_agent_temp.py --all         # 清理所有（包括 24h 内的 miaogent temp）
  python scripts/clean_agent_temp.py --max-age 1   # 清理 1 小时以上的 miaogent temp
"""

import os
import shutil
import argparse
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIAOGENT_TEMP = Path.home() / ".miaogent" / "temp"

# ── 项目目录清理规则 ──
CLEANUP_RULES = [
    ("temp_*.py", "file",   "Agent 临时 Python 文件"),
    (".superpowers", "dir", "Agent brainstorm 产物目录"),
    ("__pycache__", "dir",  "Python 字节码缓存目录（递归）"),
    (".pytest_cache", "dir","pytest 缓存目录"),
    (".coverage", "file",   "覆盖率数据文件"),
    ("htmlcov", "dir",      "覆盖率报告目录"),
    (".mypy_cache", "dir",  "mypy 类型检查缓存"),
    (".ruff_cache", "dir",  "ruff 检查缓存"),
]

EXCLUDE_DIRS = {".venv", ".git", "node_modules", ".idea", ".superpowers"}


def collect_project_items() -> list[tuple[Path, str, str]]:
    """收集项目目录中所有待清理条目"""
    items = []
    for pattern, kind, desc in CLEANUP_RULES:
        if kind == "file":
            for f in PROJECT_ROOT.glob(pattern):
                if any(parent.name in EXCLUDE_DIRS for parent in f.relative_to(PROJECT_ROOT).parents):
                    continue
                items.append((f, "file", desc))
        else:
            for d in PROJECT_ROOT.rglob(pattern):
                if any(parent.name in EXCLUDE_DIRS for parent in d.relative_to(PROJECT_ROOT).parents):
                    continue
                if d.parent == PROJECT_ROOT or d.parent.name not in EXCLUDE_DIRS:
                    items.append((d, "dir", desc))
    return items


def collect_miaogent_temp(max_age_hours: float) -> list[tuple[Path, str, str]]:
    """收集 ~/.miaogent/temp/ 中待清理文件"""
    items = []
    if not MIAOGENT_TEMP.exists():
        return items

    now = time.time()
    for f in MIAOGENT_TEMP.iterdir():
        if f.is_file():
            age_hours = (now - f.stat().st_mtime) / 3600
            if age_hours >= max_age_hours:
                items.append((f, "file", f"miaogent 临时脚本"))
        elif f.is_dir() and f.name != ".":
            # 也清理子目录
            age_hours = (now - f.stat().st_mtime) / 3600
            if age_hours >= max_age_hours:
                items.append((f, "dir", f"miaogent 临时目录"))
    return items


def delete_item(path: Path, kind: str) -> str | None:
    """删除单个条目，成功返回 None，失败返回错误信息"""
    try:
        if kind == "dir":
            shutil.rmtree(path)
        else:
            path.unlink()
        return None
    except Exception as e:
        return str(e)


def clean_orphan_parents(items: list[tuple[Path, str, str]]) -> int:
    """清理空的 __pycache__ 父目录"""
    cleaned = 0
    for path, kind, _ in items:
        if kind == "dir" and path.name == "__pycache__":
            parent = path.parent
            try:
                if parent.exists() and not any(parent.iterdir()):
                    parent.rmdir()
                    cleaned += 1
            except OSError:
                pass
    return cleaned


def main():
    parser = argparse.ArgumentParser(description="清理 Agent 临时文件")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不实际删除")
    parser.add_argument("--all", action="store_true",
                        help="清理所有 miaogent temp 文件（包括刚生成的）")
    parser.add_argument("--max-age", type=float, default=24,
                        help="miaogent temp 保留时长（小时），默认 24")
    args = parser.parse_args()

    # 用 --all 覆盖 max_age
    max_age = 0 if args.all else max(args.max_age, 0)

    total_items = []
    total_items.extend(collect_project_items())
    total_items.extend(collect_miaogent_temp(max_age))

    if not total_items:
        print("✓ 没有需要清理的临时文件")
        return

    # ── 执行清理 ──
    if args.dry_run:
        print(f"将清理 {len(total_items)} 项（--dry-run 模式）：\n")
    else:
        print(f"正在清理 {len(total_items)} 项...\n")

    success = 0
    fail = 0
    for path, kind, desc in total_items:
        if args.dry_run:
            status = "[待删除]"
        else:
            err = delete_item(path, kind)
            if err:
                status = f"[失败] {err}"
                fail += 1
            else:
                status = "[已删除]"
                success += 1

        # 显示相对路径
        try:
            rel = path.relative_to(PROJECT_ROOT)
        except ValueError:
            try:
                rel = path.relative_to(Path.home())
                rel = Path("~") / rel
            except ValueError:
                rel = path
        print(f"  {status} {desc}: {rel}")

    # ── 清理空父目录 ──
    if not args.dry_run:
        cleaned = clean_orphan_parents(total_items)
        extra = f"，清理 {cleaned} 个空目录" if cleaned else ""
        print(f"\n✓ 清理完成：成功 {success}，失败 {fail}{extra}")


if __name__ == "__main__":
    main()
