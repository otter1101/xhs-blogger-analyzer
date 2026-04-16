"""
Phase 0: 环境自动准备
检查 Python 版本、依赖库、xiaohongshu-mcp 二进制与服务、登录状态。
发现缺失项时**主动修复**：自动安装 Python 依赖、自动下载 MCP 二进制、自动启动 MCP 服务。
用户唯一需要手动做的事情：用手机扫码登录小红书。

用法：
    python check_env.py                   # 全自动检查+修复（默认端口18060）
    python check_env.py --port 18061      # 自定义端口
    python check_env.py --no-auto-fix     # 仅检查，不自动修复
    python check_env.py --find-mcp        # 搜索本机 MCP 二进制位置
"""

import sys
import os
import argparse
import subprocess
import glob
import platform
import json
import zipfile
import tarfile
import stat
import time
import shutil

# 添加上级目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils.mcp_client import MCPClient, MCPError

# ----------------------------------------------------------
# 常量
# ----------------------------------------------------------
GITHUB_API_LATEST = "https://api.github.com/repos/xpzouying/xiaohongshu-mcp/releases/latest"
GITHUB_RELEASES_URL = "https://github.com/xpzouying/xiaohongshu-mcp/releases"
MCP_INSTALL_DIR_NAME = ".xiaohongshu"  # ~/. xiaohongshu/bin/

# 各平台对应的二进制资产名称
PLATFORM_ASSETS = {
    ("Windows", "AMD64"):  "xiaohongshu-mcp-windows-amd64.zip",
    ("Windows", "x86_64"): "xiaohongshu-mcp-windows-amd64.zip",
    ("Darwin", "arm64"):   "xiaohongshu-mcp-darwin-arm64.tar.gz",
    ("Darwin", "x86_64"):  "xiaohongshu-mcp-darwin-amd64.tar.gz",
    ("Linux", "x86_64"):   "xiaohongshu-mcp-linux-amd64.tar.gz",
    ("Linux", "aarch64"):  "xiaohongshu-mcp-linux-arm64.tar.gz",
}

# 各平台对应的二进制可执行文件名
PLATFORM_BINARIES = {
    "Windows": "xiaohongshu-mcp-windows-amd64.exe",
    "Darwin_arm64": "xiaohongshu-mcp-darwin-arm64",
    "Darwin_x86_64": "xiaohongshu-mcp-darwin-amd64",
    "Linux_x86_64": "xiaohongshu-mcp-linux-amd64",
    "Linux_aarch64": "xiaohongshu-mcp-linux-arm64",
}


def _get_binary_name():
    """获取当前平台对应的二进制文件名"""
    system = platform.system()
    if system == "Windows":
        return PLATFORM_BINARIES["Windows"]
    machine = platform.machine()
    key = f"{system}_{machine}"
    return PLATFORM_BINARIES.get(key, f"xiaohongshu-mcp-{system.lower()}-{machine}")


def _get_install_dir():
    """获取 MCP 安装目录: ~/.xiaohongshu/bin/"""
    home = os.path.expanduser("~")
    return os.path.join(home, MCP_INSTALL_DIR_NAME, "bin")


# ----------------------------------------------------------
# 检查项
# ----------------------------------------------------------
def check_python():
    """检查 Python 版本 >= 3.10"""
    v = sys.version_info
    ok = v.major >= 3 and v.minor >= 10
    ver_str = f"{v.major}.{v.minor}.{v.micro}"
    return ok, f"Python {ver_str}", "需要 Python 3.10+，当前版本太低" if not ok else ""


def check_docx_lib(auto_fix=True):
    """检查 python-docx 是否可用，默认自动安装"""
    try:
        import docx
        return True, f"python-docx {docx.__version__}", ""
    except ImportError:
        if auto_fix:
            print("     🔧 自动安装 python-docx...")
            try:
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", "python-docx",
                     "-i", "https://pypi.tuna.tsinghua.edu.cn/simple", "-q"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                import docx
                return True, f"python-docx {docx.__version__}（自动安装成功）", ""
            except (subprocess.CalledProcessError, ImportError, OSError) as e:
                return False, f"自动安装失败: {e}", "请手动运行: pip install python-docx"
        return False, "python-docx 未安装", "运行: pip install python-docx"


def check_mcp_binary(auto_fix=True):
    """检查 MCP 二进制是否存在，不存在则自动下载"""
    found = find_mcp_binary()
    if found:
        best = found[0]
        return True, f"MCP 二进制已就位 ({best['path']}, {best['size_mb']}MB)", "", best["path"]

    if auto_fix:
        print("     📦 未找到 xiaohongshu-mcp 二进制，正在自动下载...")
        ok, msg, binary_path = download_mcp_binary()
        if ok:
            return True, f"MCP 二进制已自动下载 ({binary_path})", "", binary_path
        else:
            return False, f"自动下载失败: {msg}", (
                f"请手动下载: {GITHUB_RELEASES_URL}\n"
                f"下载后放到 {_get_install_dir()}/ 目录下"
            ), None
    else:
        return False, "MCP 二进制未找到", (
            f"请从 {GITHUB_RELEASES_URL} 下载\n"
            f"推荐放到 {_get_install_dir()}/ 目录下"
        ), None


def check_mcp_service(client, binary_path=None, auto_fix=True):
    """检查 MCP 服务是否可达，不可达则尝试自动启动"""
    alive = client.is_alive()
    if alive:
        return True, f"MCP 服务运行中 ({client.base_url})", ""

    # 尝试自动启动
    if auto_fix and binary_path:
        print(f"     🚀 MCP 服务未运行，正在自动启动...")
        ok, msg = start_mcp_service(binary_path)
        if ok:
            # 等待服务启动
            for i in range(10):
                time.sleep(1)
                if client.is_alive():
                    return True, f"MCP 服务已自动启动 ({client.base_url})", ""
            return False, "MCP 服务启动超时", (
                "进程已拉起但服务未响应，可能是端口冲突\n"
                "请检查终端输出或手动运行二进制查看错误信息"
            )
        else:
            return False, f"自动启动失败: {msg}", "请在终端手动启动 MCP 二进制"

    return False, f"MCP 服务不可达 ({client.base_url})", (
        "请先在终端启动 xiaohongshu-mcp：\n"
        f"  {_get_binary_name()}\n"
        "  macOS被killed? 运行:\n"
        "    xattr -rd com.apple.quarantine . && codesign -fs - --deep <binary>"
    )


def check_login(client):
    """检查小红书登录状态（仅检查，不触发扫码流程）"""
    try:
        logged_in, msg = client.check_login()
        if logged_in:
            return True, "小红书已登录", ""
        else:
            return False, msg, "需要扫码登录"
    except MCPError as e:
        return False, f"登录检查失败: {e}", "MCP 服务可能未正常运行"


# ----------------------------------------------------------
# 登录扫码完整流程（P3-2 + P3-11）
# ----------------------------------------------------------
def _get_qrcode_save_path():
    """获取二维码保存路径：桌面 > 用户主目录"""
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    if os.path.isdir(desktop):
        return os.path.join(desktop, "xiaohongshu_qrcode.png")
    # 桌面目录不存在（服务器/CI），降级到用户主目录
    return os.path.join(os.path.expanduser("~"), "xiaohongshu_qrcode.png")


def _save_qrcode_png(qr_data, save_path):
    """
    从 MCP 返回数据中提取二维码并保存为 PNG。
    尝试顺序: qrBase64 → qrUrl 下载 → _raw_text 中提取 base64
    返回 (ok, method_used)
    """
    import base64
    import urllib.request as _urlreq

    # 尝试1: base64 字段
    qr_b64 = qr_data.get("qrBase64", "") or qr_data.get("qr_base64", "")
    if qr_b64:
        try:
            # 去掉 data:image/png;base64, 前缀（如有）
            if "," in qr_b64:
                qr_b64 = qr_b64.split(",", 1)[1]
            img_bytes = base64.b64decode(qr_b64)
            with open(save_path, "wb") as f:
                f.write(img_bytes)
            return True, "base64"
        except (ValueError, OSError) as e:
            print(f"     ⚠️  base64 解码失败: {e}")

    # 尝试2: URL 下载
    qr_url = qr_data.get("qrUrl", "") or qr_data.get("qr_url", "")
    if qr_url and qr_url.startswith("http"):
        try:
            _urlreq.urlretrieve(qr_url, save_path)
            return True, "url_download"
        except (urllib.error.URLError, OSError) as e:
            print(f"     ⚠️  二维码下载失败: {e}")

    # 尝试3: _raw_text 中可能包含 base64 数据
    raw = qr_data.get("_raw_text", "")
    if raw:
        # 提取可能的 base64 图片数据
        import re
        m = re.search(r'data:image/[^;]+;base64,([A-Za-z0-9+/=]+)', raw)
        if m:
            try:
                img_bytes = base64.b64decode(m.group(1))
                with open(save_path, "wb") as f:
                    f.write(img_bytes)
                return True, "raw_text_base64"
            except (ValueError, OSError):
                pass

    return False, "none"


def _try_open_file(filepath):
    """尝试用系统默认程序打开文件（跨平台）"""
    try:
        system = platform.system()
        if system == "Windows":
            os.startfile(filepath)
        elif system == "Darwin":
            subprocess.Popen(["open", filepath], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", filepath], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (OSError, AttributeError):
        return False


def _cleanup_qrcode(qrcode_path):
    """清理二维码文件"""
    if qrcode_path and os.path.isfile(qrcode_path):
        try:
            os.remove(qrcode_path)
            print(f"     🧹 已清理桌面二维码文件")
        except OSError:
            pass


def login_flow(client):
    """
    完整登录流程：检查状态 → 获取二维码 → 降级展示 → 轮询 → 超时
    
    降级展示策略（按优先级）：
    1. 保存 PNG 到桌面并自动打开（最直观）
    2. 输出 base64 数据让 AI 环境渲染（WorkBuddy 等支持图片渲染的环境）
    3. 输出 URL 让用户手动打开（最终兜底）
    
    Returns: (ok, detail, fix_hint)
    """
    POLL_INTERVAL = 3    # 轮询间隔（秒）
    POLL_TIMEOUT = 120   # 总超时（秒）
    qrcode_path = None

    # Step 1: 检查是否已登录
    try:
        logged_in, msg = client.check_login()
        if logged_in:
            return True, "小红书已登录", ""
    except MCPError as e:
        return False, f"登录检查失败: {e}", "MCP 服务可能未正常运行"

    # Step 2: 获取二维码
    print()
    print("  📱 小红书未登录，正在获取登录二维码...")
    try:
        qr_data = client.get_login_qrcode()
    except MCPError as e:
        return False, f"获取二维码失败: {e}", (
            "MCP 服务可能不支持 get_login_qrcode\n"
            "请手动在浏览器中完成登录，或升级 xiaohongshu-mcp 到最新版"
        )

    if not qr_data:
        return False, "获取二维码返回空数据", "请检查 MCP 服务版本"

    # Step 3: 降级展示策略
    display_ok = False
    qr_url = qr_data.get("qrUrl", "") or qr_data.get("qr_url", "")
    qr_b64 = qr_data.get("qrBase64", "") or qr_data.get("qr_base64", "")

    # 策略1: 保存 PNG 到桌面并自动打开
    qrcode_path = _get_qrcode_save_path()
    save_ok, method = _save_qrcode_png(qr_data, qrcode_path)
    if save_ok:
        print(f"  📱 二维码已保存到: {qrcode_path}")
        opened = _try_open_file(qrcode_path)
        if opened:
            print(f"  📱 已自动打开二维码图片，请用手机小红书 APP 扫码")
        else:
            print(f"  📱 请打开上面的文件，用手机小红书 APP 扫码")
        display_ok = True

    # 策略2: 输出 base64 供 AI 环境渲染
    if qr_b64 and not display_ok:
        if "," in qr_b64:
            print(f"  📱 二维码 base64 数据（AI环境可能会自动渲染）：")
            print(f"  ![二维码]({qr_b64})")
        else:
            print(f"  📱 二维码 base64 数据（AI环境可能会自动渲染）：")
            print(f"  ![二维码](data:image/png;base64,{qr_b64})")
        display_ok = True

    # 策略3: 输出 URL 兜底
    if not display_ok:
        if qr_url:
            print(f"  📱 请在浏览器打开以下链接，然后用手机小红书扫码：")
            print(f"     {qr_url}")
        else:
            # 检查 _raw_text 里是否有有用信息
            raw = qr_data.get("_raw_text", "")
            if raw:
                print(f"  📱 MCP 返回的登录信息：")
                print(f"     {raw[:500]}")
            else:
                _cleanup_qrcode(qrcode_path)
                return False, "获取二维码成功但无法展示（无 URL 也无 base64 数据）", (
                    "请手动在浏览器中访问小红书完成登录\n"
                    "或升级 xiaohongshu-mcp 到最新版"
                )

    # Step 4: 轮询等待扫码
    print()
    print(f"  ⏳ 等待扫码登录（{POLL_TIMEOUT}秒超时）...")
    print(f"     每{POLL_INTERVAL}秒检查一次登录状态，扫码后请稍等片刻")
    print()

    start_time = time.time()
    dots = 0
    while time.time() - start_time < POLL_TIMEOUT:
        time.sleep(POLL_INTERVAL)
        elapsed = int(time.time() - start_time)
        dots = (dots + 1) % 4
        sys.stdout.write(f"\r     {'.' * (dots + 1):<4s} 已等待 {elapsed}s / {POLL_TIMEOUT}s")
        sys.stdout.flush()

        try:
            logged_in, msg = client.check_login()
            if logged_in:
                sys.stdout.write("\r" + " " * 60 + "\r")  # 清除等待行
                print(f"  ✅ 登录成功！")
                _cleanup_qrcode(qrcode_path)
                return True, "小红书登录成功（扫码）", ""
        except MCPError:
            # MCP 偶尔可能闪断，继续轮询
            continue

    # 超时
    sys.stdout.write("\r" + " " * 60 + "\r")
    _cleanup_qrcode(qrcode_path)
    return False, f"扫码登录超时（{POLL_TIMEOUT}秒）", (
        "请重新运行环境检查以获取新的二维码\n"
        "提示：二维码通常有60-120秒有效期，请在获取后尽快扫码"
    )


# ----------------------------------------------------------
# 自动下载 MCP 二进制
# ----------------------------------------------------------
def download_mcp_binary():
    """
    从 GitHub Releases 自动下载最新版 xiaohongshu-mcp 二进制。
    返回 (ok, message, binary_path)
    """
    import urllib.request
    import urllib.error

    system = platform.system()
    machine = platform.machine()
    asset_key = (system, machine)

    asset_name = PLATFORM_ASSETS.get(asset_key)
    if not asset_name:
        return False, f"不支持的平台: {system}/{machine}，请手动下载", None

    # Step 1: 获取最新 release tag
    print(f"     📡 查询最新版本...")
    try:
        req = urllib.request.Request(
            GITHUB_API_LATEST,
            headers={"Accept": "application/vnd.github.v3+json", "User-Agent": "xiaohongshu-skill"}
        )
        resp = urllib.request.urlopen(req, timeout=30)
        release_info = json.loads(resp.read().decode())
        tag = release_info["tag_name"]
        print(f"     📋 最新版本: {tag}")
    except Exception as e:
        return False, f"获取版本信息失败: {e}", None

    # Step 2: 构造下载 URL
    download_url = f"https://github.com/xpzouying/xiaohongshu-mcp/releases/download/{tag}/{asset_name}"

    # Step 3: 创建安装目录
    install_dir = _get_install_dir()
    os.makedirs(install_dir, exist_ok=True)
    archive_path = os.path.join(install_dir, asset_name)

    # Step 4: 下载
    print(f"     ⬇️  下载 {asset_name}...")
    try:
        urllib.request.urlretrieve(download_url, archive_path)
        file_size = os.path.getsize(archive_path) / (1024 * 1024)
        print(f"     ✅ 下载完成 ({round(file_size, 1)} MB)")
    except Exception as e:
        return False, f"下载失败: {e}", None

    # Step 5: 解压
    print(f"     📂 解压中...")
    try:
        if asset_name.endswith(".zip"):
            with zipfile.ZipFile(archive_path, "r") as zf:
                zf.extractall(install_dir)
        elif asset_name.endswith(".tar.gz"):
            with tarfile.open(archive_path, "r:gz") as tf:
                # 安全过滤：防止路径遍历攻击
                if hasattr(tarfile, "data_filter"):
                    # Python 3.12+ 原生支持 filter 参数
                    tf.extractall(install_dir, filter="data")
                else:
                    # Python < 3.12: 手动检查路径安全性
                    for member in tf.getmembers():
                        member_path = os.path.join(install_dir, member.name)
                        if not os.path.abspath(member_path).startswith(os.path.abspath(install_dir)):
                            raise tarfile.TarError(f"路径遍历风险: {member.name}")
                    tf.extractall(install_dir)
        else:
            return False, f"未知压缩格式: {asset_name}", None
    except Exception as e:
        return False, f"解压失败: {e}", None

    # Step 6: 设置可执行权限（macOS/Linux）
    binary_name = _get_binary_name()
    binary_path = os.path.join(install_dir, binary_name)

    if not os.path.isfile(binary_path):
        # 可能解压到了子目录，搜索一下
        for root, dirs, files in os.walk(install_dir):
            for f in files:
                if f == binary_name or (f.startswith("xiaohongshu-mcp") and not f.endswith((".zip", ".tar.gz", ".md"))):
                    candidate = os.path.join(root, f)
                    if candidate != archive_path:
                        binary_path = candidate
                        break

    if os.path.isfile(binary_path):
        if system != "Windows":
            os.chmod(binary_path, os.stat(binary_path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

            # macOS: 自动解除 quarantine
            if system == "Darwin":
                print(f"     🍎 macOS: 解除 quarantine 限制...")
                try:
                    subprocess.run(
                        ["xattr", "-rd", "com.apple.quarantine", install_dir],
                        capture_output=True, timeout=10
                    )
                    subprocess.run(
                        ["codesign", "-fs", "-", "--deep", binary_path],
                        capture_output=True, timeout=10
                    )
                except (subprocess.SubprocessError, OSError) as e:
                    print(f"     ⚠️  quarantine 解除可能未完成 ({e})，如被系统拦截请手动运行:")
                    print(f"        xattr -rd com.apple.quarantine {install_dir}")
                    print(f"        codesign -fs - --deep {binary_path}")

        # 清理压缩包
        try:
            os.remove(archive_path)
        except OSError:
            pass

        print(f"     ✅ 安装完成: {binary_path}")
        return True, "安装成功", binary_path
    else:
        return False, f"解压后未找到可执行文件，请检查 {install_dir}", None


# ----------------------------------------------------------
# 检测本机 Chrome 路径（供 Rod 使用）
# ----------------------------------------------------------
def _find_chrome_path():
    """
    按优先级检测本机 Chrome 可执行文件路径。
    找到返回路径字符串，找不到返回 None。
    Rod 通过 ROD 环境变量的 bin= 字段接受此路径。
    """
    system = platform.system()

    if system == "Darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            os.path.expanduser(
                "~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
            ),
        ]
    elif system == "Windows":
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(
                r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
            ),
        ]
    else:  # Linux
        candidates = [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium-browser",
            "/usr/bin/chromium",
        ]

    for path in candidates:
        if os.path.isfile(path):
            return path

    # 最后尝试 PATH 里有没有
    return shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chrome")


# ----------------------------------------------------------
# 自动启动 MCP 服务
# ----------------------------------------------------------
def start_mcp_service(binary_path):
    """
    后台启动 MCP 服务进程。
    返回 (ok, message)
    """
    try:
        # 继承当前环境，并尝试注入 Chrome 路径给 Rod
        env = os.environ.copy()
        chrome_path = _find_chrome_path()
        if chrome_path:
            env["ROD"] = f"bin={chrome_path}"
            print(f"     🌐 检测到 Chrome: {chrome_path}")
        else:
            print(f"     ⚠️  未检测到 Chrome，Rod 将使用默认路径（如遇登录卡住请安装 Chrome）")

        system = platform.system()
        if system == "Windows":
            # Windows: 使用 subprocess.CREATE_NEW_PROCESS_GROUP 在后台启动
            proc = subprocess.Popen(
                [binary_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
            )
        else:
            # macOS/Linux: nohup 后台启动
            proc = subprocess.Popen(
                [binary_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                start_new_session=True,
            )

        print(f"     🔄 MCP 进程已启动 (PID: {proc.pid})，等待服务就绪...")
        return True, f"PID: {proc.pid}"

    except PermissionError:
        return False, (
            "权限不足，无法启动。请手动运行:\n"
            f"  {binary_path}"
        )
    except Exception as e:
        return False, str(e)


# ----------------------------------------------------------
# 搜索本机 MCP 二进制
# ----------------------------------------------------------
def find_mcp_binary():
    """在常见位置搜索 xiaohongshu-mcp 二进制文件"""
    system = platform.system()
    home = os.path.expanduser("~")

    if system == "Windows":
        bin_names = ["xiaohongshu-mcp-windows-amd64.exe", "xiaohongshu-mcp*.exe"]
        search_dirs = [
            os.path.join(home, MCP_INSTALL_DIR_NAME, "bin"),
            os.path.join(home, MCP_INSTALL_DIR_NAME),
            os.path.join(home, "Downloads"),
            os.path.join(home, "Desktop"),
            os.getcwd(),
        ]
    elif system == "Darwin":
        bin_names = ["xiaohongshu-mcp-darwin-arm64", "xiaohongshu-mcp-darwin-amd64", "xiaohongshu-mcp*"]
        search_dirs = [
            os.path.join(home, MCP_INSTALL_DIR_NAME, "bin"),
            os.path.join(home, MCP_INSTALL_DIR_NAME),
            os.path.join(home, "Downloads"),
            os.path.join(home, "Desktop"),
            "/usr/local/bin",
            os.getcwd(),
        ]
    else:
        bin_names = ["xiaohongshu-mcp-linux-amd64", "xiaohongshu-mcp-linux-arm64", "xiaohongshu-mcp*"]
        search_dirs = [
            os.path.join(home, MCP_INSTALL_DIR_NAME, "bin"),
            os.path.join(home, MCP_INSTALL_DIR_NAME),
            os.path.join(home, "Downloads"),
            "/usr/local/bin",
            os.getcwd(),
        ]

    found = []
    seen_paths = set()
    for d in search_dirs:
        if not os.path.isdir(d):
            continue
        for pattern in bin_names:
            matches = glob.glob(os.path.join(d, pattern))
            for m in matches:
                real_path = os.path.realpath(m)
                if os.path.isfile(m) and real_path not in seen_paths:
                    # 排除压缩包
                    if m.endswith((".zip", ".tar.gz", ".tar", ".gz")):
                        continue
                    size_mb = os.path.getsize(m) / (1024 * 1024)
                    found.append({"path": m, "size_mb": round(size_mb, 1)})
                    seen_paths.add(real_path)

    return found


# ----------------------------------------------------------
# 主流程
# ----------------------------------------------------------
def run_checks(port=18060, host="localhost", auto_fix=True):
    """
    运行所有环境检查。
    auto_fix=True（默认）时，遇到问题自动修复：
      - python-docx 缺失 → 自动 pip install
      - MCP 二进制不存在 → 自动从 GitHub 下载
      - MCP 服务未运行 → 自动启动
      - 小红书未登录 → 提示用户扫码（这是唯一需要用户手动做的）
    """
    client = MCPClient(host=host, port=port)

    print("=" * 60)
    print("🔍 小红书博主拆解 Skill — 环境自动准备")
    print(f"   MCP 地址: {client.base_url}")
    print(f"   模式: {'全自动修复' if auto_fix else '仅检查（不修复）'}")
    print("=" * 60)

    all_ok = True
    results = []
    mcp_binary_path = None
    mcp_service_ok = False

    # ① Python 版本
    ok, detail, fix = check_python()
    _print_result("Python 版本", ok, detail, fix)
    results.append({"name": "Python 版本", "ok": ok, "detail": detail, "fix": fix})
    if not ok:
        all_ok = False

    # ② python-docx
    ok, detail, fix = check_docx_lib(auto_fix=auto_fix)
    _print_result("python-docx", ok, detail, fix)
    results.append({"name": "python-docx", "ok": ok, "detail": detail, "fix": fix})
    if not ok:
        all_ok = False

    # ③ MCP 二进制
    ok, detail, fix, mcp_binary_path = check_mcp_binary(auto_fix=auto_fix)
    _print_result("MCP 二进制", ok, detail, fix)
    results.append({"name": "MCP 二进制", "ok": ok, "detail": detail, "fix": fix})
    if not ok:
        all_ok = False

    # ④ MCP 服务
    if mcp_binary_path or not auto_fix:
        ok, detail, fix = check_mcp_service(client, binary_path=mcp_binary_path, auto_fix=auto_fix)
        _print_result("MCP 服务", ok, detail, fix)
        results.append({"name": "MCP 服务", "ok": ok, "detail": detail, "fix": fix})
        if not ok:
            all_ok = False
        else:
            mcp_service_ok = True
    else:
        print(f"  ⏭️  MCP 服务: 跳过（MCP 二进制不可用）")
        results.append({"name": "MCP 服务", "ok": False, "detail": "MCP 二进制不可用", "fix": ""})
        all_ok = False

    # ⑤ 小红书登录（含扫码流程）
    if mcp_service_ok:
        ok, detail, fix = login_flow(client)
        _print_result("小红书登录", ok, detail, fix)
        results.append({"name": "小红书登录", "ok": ok, "detail": detail, "fix": fix})
        if not ok:
            all_ok = False
    else:
        print(f"  ⏭️  小红书登录: 跳过（MCP 服务不可达）")
        results.append({"name": "小红书登录", "ok": False, "detail": "MCP 服务不可达，跳过", "fix": ""})
        all_ok = False

    # 总结
    print("=" * 60)
    if all_ok:
        print("✅ 环境就绪，可以开始拆解！")
    else:
        failed = [r for r in results if not r["ok"]]
        login_only = len(failed) == 1 and failed[0]["name"] == "小红书登录"
        if login_only:
            print("⚠️  登录未完成。请重新运行环境检查获取新的二维码。")
        else:
            print(f"❌ 有 {len(failed)} 项未通过，请查看上面的提示修复。")
    print()

    return all_ok, results


def _print_result(name, ok, detail, fix):
    """格式化输出单条检查结果"""
    status = "✅" if ok else "❌"
    print(f"  {status} {name}: {detail}")
    if not ok and fix:
        for line in fix.split("\n"):
            print(f"     💡 {line}")


# ----------------------------------------------------------
# CLI 入口
# ----------------------------------------------------------
if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="小红书博主拆解 Skill — 环境自动准备")
    parser.add_argument("--port", type=int, default=18060, help="MCP端口（默认18060）")
    parser.add_argument("--host", default="localhost", help="MCP主机（默认localhost）")
    parser.add_argument("--no-auto-fix", action="store_true", help="仅检查，不自动修复")
    parser.add_argument("--find-mcp", action="store_true", help="搜索本机 MCP 二进制位置")
    args = parser.parse_args()

    auto_fix = not args.no_auto_fix

    # 搜索 MCP 二进制
    if args.find_mcp:
        print("\n🔍 搜索 xiaohongshu-mcp 二进制...")
        found = find_mcp_binary()
        if found:
            print(f"  找到 {len(found)} 个文件:")
            for f in found:
                print(f"    📁 {f['path']} ({f['size_mb']} MB)")
        else:
            print(f"  ❌ 未找到。运行不带 --no-auto-fix 会自动下载。")
        print()

    ok, _ = run_checks(port=args.port, host=args.host, auto_fix=auto_fix)
    sys.exit(0 if ok else 1)
