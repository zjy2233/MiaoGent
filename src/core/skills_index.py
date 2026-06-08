"""Skill 安装后端 — npm / pip / git / URL 四种来源。

安装后通过检查 ``.claude-plugin/plugin.json`` 或 ``skill.md`` 验证合法性。
不再维护硬编码的 known_skills 索引；所有安装都基于显式来源。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import ssl
import stat
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from src.core.miaogent_home import get_miaogent_home

logger = logging.getLogger(__name__)


# ── 工具函数 ──────────────────────────────────────────────────────────────


def _rmtree_force(path: str | Path) -> None:
    """强制删除目录树（处理 Windows 只读文件问题，如 .git 目录）。"""
    def _on_error(func, p, exc_info):
        if not os.access(p, os.W_OK):
            os.chmod(p, stat.S_IWRITE)
            try:
                func(p)
                return
            except OSError:
                pass
        import time
        time.sleep(0.1)
        try:
            func(p)
        except OSError:
            pass

    shutil.rmtree(path, onerror=_on_error)


# ── HTTP 工具 ─────────────────────────────────────────────────────────────


def _http_opener():
    """创建 urllib OpenerDirector，自动检测 git 代理配置。"""
    ctx = ssl.create_default_context()
    handlers: list[urllib.request.BaseHandler] = [
        urllib.request.HTTPSHandler(context=ctx),
    ]

    # 从 git config 读取代理设置（支持 git 代理配置的环境）
    import subprocess as _subprocess
    proxy_url = ""
    for key in ("https.proxy", "http.proxy"):
        try:
            r = _subprocess.run(
                ["git", "config", "--global", "--get", key],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                proxy_url = r.stdout.strip()
                break
        except Exception:
            pass

    if proxy_url:
        proxy_support = urllib.request.ProxyHandler({
            "http": proxy_url,
            "https": proxy_url,
        })
        handlers.insert(0, proxy_support)

    return urllib.request.build_opener(*handlers)


def _fetch_json(url: str, timeout: int = 15) -> dict[str, Any] | None:
    """HTTP GET 返回 JSON。"""
    try:
        opener = _http_opener()
        req = urllib.request.Request(url, headers={"User-Agent": "MiaoGent/1.0"})
        with opener.open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("HTTP GET %s failed: %s", url, exc)
        return None


def _download_to_temp(url: str, suffix: str = ".tgz") -> str | None:
    """下载文件到临时路径，返回路径。"""
    try:
        opener = _http_opener()
        req = urllib.request.Request(url, headers={"User-Agent": "MiaoGent/1.0"})
        with opener.open(req, timeout=30) as resp:
            content = resp.read()
    except Exception as exc:
        logger.warning("Download %s failed: %s", url, exc)
        return None

    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp.write(content)
    tmp.close()
    return tmp.name


def _extract_skill_archive(archive_path: str, dest: Path) -> bool:
    """解压 tar/zip 到目标目录，自动寻找 skill.md 并整理。"""
    dest.mkdir(parents=True, exist_ok=True)

    try:
        # 判断类型
        if archive_path.endswith(".zip"):
            with zipfile.ZipFile(archive_path) as zf:
                zf.extractall(dest)
        else:
            with tarfile.open(archive_path, "r:*") as tf:
                tf.extractall(dest)
    except Exception as exc:
        logger.warning("Extract failed: %s", exc)
        return False

    # 如果解压后只有一个顶层目录，把内容提升上来
    children = list(dest.iterdir())
    if len(children) == 1 and children[0].is_dir():
        inner = children[0]
        for item in inner.iterdir():
            shutil.move(str(item), dest / item.name)
        inner.rmdir()

    return True


# ── 安装后端 ──────────────────────────────────────────────────────────────


def _npm_registry_url(package_name: str) -> str:
    """拼装 npm registry API URL。"""
    return f"https://registry.npmjs.org/{package_name}"


def install_npm_skill(package_name: str) -> str:
    """从 npm registry 下载安装 Skill（纯 HTTP，无需 Node.js）。

    Args:
        package_name: npm 包名（如 ``"@miaogent/skill-weather"``）。

    Returns:
        安装结果描述。
    """
    url = _npm_registry_url(package_name)
    data = _fetch_json(url)
    if data is None:
        return f"错误：无法访问 npm registry — {url}"

    # 取 latest 版本
    dist_tags = data.get("dist-tags", {})
    latest_ver = dist_tags.get("latest")
    if not latest_ver:
        return f"错误：包 '{package_name}' 在 registry 中无 latest 版本。"

    versions = data.get("versions", {})
    version_info = versions.get(latest_ver)
    if not version_info:
        return f"错误：包 '{package_name}' 版本 {latest_ver} 信息不存在。"

    tarball_url = (version_info.get("dist") or {}).get("tarball")
    if not tarball_url:
        return f"错误：包 '{package_name}' 无 tarball 下载地址。"

    # 下载
    tmp_path = _download_to_temp(tarball_url, ".tgz")
    if tmp_path is None:
        return f"错误：下载 tarball 失败 — {tarball_url}"

    # 计算 skill 目录名：@scope/name → scope_name
    name_safe = package_name.replace("/", "_").replace("@", "")
    dest = get_miaogent_home() / "skills" / name_safe

    # 解压
    ok = _extract_skill_archive(tmp_path, dest)
    Path(tmp_path).unlink(missing_ok=True)

    if not ok:
        shutil.rmtree(dest, ignore_errors=True)
        return f"错误：解压 tarball 失败。"

    # 验证
    if not _validate_skill_dir(dest):
        shutil.rmtree(dest, ignore_errors=True)
        return (
            f"错误：包 '{package_name}' 不是有效的 Skill 包"
            f"（未找到 skill.md 或 .claude-plugin/plugin.json）。\n"
            f"下载的 tarball 已清理。"
        )

    return f"已从 npm registry 安装 '{package_name}' v{latest_ver} 到 {dest}"


def install_pip_skill(package_name: str) -> str:
    """从 PyPI 下载安装 Skill（纯 HTTP，无需 pip CLI）。

    Args:
        package_name: pip 包名（如 ``"miaogent-skill-weather"``）。

    Returns:
        安装结果描述。
    """
    url = f"https://pypi.org/pypi/{package_name}/json"
    data = _fetch_json(url)
    if data is None:
        return f"错误：无法访问 PyPI — {url}"

    info = data.get("info", {})
    version = info.get("version", "0.0.0")

    # 找到 sdist 包
    releases = data.get("urls") or data.get("releases", {}).get(version, [])
    if not releases:
        # 尝试从 info 直接取
        release_url = info.get("download_url")
        if release_url:
            releases = [{"url": release_url, "packagetype": "sdist"}]
        else:
            return f"错误：包 '{package_name}' 无可下载的 sdist 包。"

    # 优先 sdist，退而求 wheel
    sdist_url = None
    for r in releases:
        if r.get("packagetype") == "sdist":
            sdist_url = r["url"]
            break
    if not sdist_url:
        for r in releases:
            if r.get("url", "").endswith((".tar.gz", ".zip")):
                sdist_url = r["url"]
                break
    if not sdist_url:
        return f"错误：包 '{package_name}' 无可下载的源码包。"

    # 下载
    suffix = ".tar.gz" if sdist_url.endswith(".tar.gz") else ".zip"
    tmp_path = _download_to_temp(sdist_url, suffix)
    if tmp_path is None:
        return f"错误：下载失败 — {sdist_url}"

    # 解压到目标目录
    name_safe = package_name.replace("-", "_").replace(".", "_")
    dest = get_miaogent_home() / "skills" / name_safe

    ok = _extract_skill_archive(tmp_path, dest)
    Path(tmp_path).unlink(missing_ok=True)

    if not ok:
        shutil.rmtree(dest, ignore_errors=True)
        return f"错误：解压 sdist 失败。"

    # 验证
    if not _validate_skill_dir(dest):
        shutil.rmtree(dest, ignore_errors=True)
        return (
            f"错误：包 '{package_name}' 不是有效的 Skill 包"
            f"（未找到 skill.md 或 .claude-plugin/plugin.json）。\n"
            f"下载的 sdist 已清理。"
        )

    return f"已从 PyPI 安装 '{package_name}' v{version} 到 {dest}"


def _validate_skill_dir(skill_dir: Path) -> bool:
    """检查目录是否为有效的 Skill 包。

    支持格式：
    1. ``.claude-plugin/plugin.json``（Claude Code 插件格式，含 skills/*/SKILL.md）
    2. ``skill.md``（MiaoGent 旧格式）

    Returns:
        True 表示目录包含有效 skill。
    """
    if not skill_dir.is_dir():
        return False

    # 格式 1：Claude Code 插件
    if (skill_dir / ".claude-plugin" / "plugin.json").exists():
        skills_root = skill_dir / "skills"
        if skills_root.is_dir():
            # 至少有一个 SKILL.md
            for sub in skills_root.iterdir():
                if sub.is_dir() and (sub / "SKILL.md").exists():
                    return True
        return False

    # 格式 2：旧格式 skill.md
    if (skill_dir / "skill.md").exists():
        return True

    return False


def install_git_skill(repo_url: str, name: str = "", subdir: str = "") -> str:
    """从 git 仓库 clone 安装 Skill。

    支持从 monorepo 的子目录安装（通过 ``subdir`` 参数指定子路径）。

    Args:
        repo_url: git 仓库 URL（如 ``"https://github.com/obra/superpowers"``）。
        name: 目标目录名，空则从 URL 推断。
        subdir: 仓库内的子目录路径（如 ``"plugins/frontend-design"``）。
                为空则直接使用仓库根目录。

    Returns:
        安装结果描述。
    """
    import subprocess
    import tempfile
    import platform

    if not name:
        name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")

    dest = get_miaogent_home() / "skills" / name
    if dest.exists():
        _rmtree_force(dest)

    clone_target = dest
    tmpdir: str | None = None

    # 有 subdir 时先 clone 到临时目录，再提取子目录
    if subdir:
        tmpdir = tempfile.mkdtemp(prefix="miaogent-git-")
        clone_target = Path(tmpdir)

    def _do_clone() -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, str(clone_target)],
            capture_output=True, text=True, timeout=120,
        )

    def _do_clone_with_openssl() -> subprocess.CompletedProcess | None:
        """Windows 上 SChannel SSL 失败时回退到 OpenSSL。"""
        try:
            return subprocess.run(
                ["git", "-c", "http.sslBackend=openssl", "clone", "--depth", "1",
                 repo_url, str(clone_target)],
                capture_output=True, text=True, timeout=120,
            )
        except Exception:
            return None

    try:
        result = _do_clone()
    except FileNotFoundError:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
        return "错误：未找到 git 命令，请确认已安装 Git。"
    except subprocess.TimeoutExpired:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
        return "错误：git clone 超时（120秒）。"
    except Exception as exc:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
        return f"错误：git clone 失败 — {exc}"

    # Windows SChannel SSL 回退
    if result.returncode != 0 and platform.system() == "Windows":
        err_lower = (result.stderr or "").lower()
        if "ssl" in err_lower or "schannel" in err_lower or "handshake" in err_lower:
            fallback = _do_clone_with_openssl()
            if fallback is not None and fallback.returncode == 0:
                result = fallback

    if result.returncode != 0:
        err = result.stderr.strip() or result.stdout.strip()
        if dest.exists():
            _rmtree_force(dest)
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
        return f"错误：git clone 失败\n{err[:500]}"

    # 提取子目录到目标位置
    if tmpdir and subdir:
        src_sub = Path(tmpdir) / subdir
        if not src_sub.is_dir():
            shutil.rmtree(tmpdir, ignore_errors=True)
            return (
                f"错误：仓库中未找到子目录 '{subdir}'。\n"
                f"git 仓库 '{repo_url}' 不包含路径 '{subdir}'。"
            )
        # 将子目录内容复制到目标位置
        shutil.copytree(str(src_sub), str(dest))
        shutil.rmtree(tmpdir, ignore_errors=True)

    if not _validate_skill_dir(dest):
        _rmtree_force(dest)
        return (
            f"错误：仓库 '{repo_url}' 不是有效的 Skill 包。\n"
            f"需要包含 .claude-plugin/plugin.json + skills/*/SKILL.md 或 skill.md。\n"
            f"已清理。"
        )

    return f"已从 git 仓库 '{repo_url}' 安装到 {dest}"
