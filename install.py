"""
小红书博主拆解 Skill — 自动安装脚本
检测宿主AI平台，将 Skill 复制到对应的 skills 目录。

支持平台：
  - WorkBuddy (CodeBuddy): ~/.workbuddy/skills/xhs-blogger-analyzer/
  - Claude Code:            ~/.claude/skills/xhs-blogger-analyzer/
  - 通用 fallback:          当前目录（手动引用）

用法：
    python install.py                  # 自动检测平台
    python install.py --target workbuddy   # 指定安装到 WorkBuddy
    python install.py --target claude      # 指定安装到 Claude Code
    python install.py --target <path>      # 安装到自定义目录
    python install.py --dry-run            # 仅预览，不实际复制
"""

import sys
import os
import shutil
import argparse
import platform

# 脚本所在的 Skill 根目录
SKILL_ROOT = os.path.dirname(os.path.abspath(__file__))
SKILL_NAME = "xhs-blogger-analyzer"

# 需要复制的文件/目录（相对于 SKILL_ROOT）
INSTALL_FILES = [
    "SKILL.md",
    "run.py",
    "scripts/",
    "references/",
]

# 平台对应的 Skills 目录
PLATFORM_DIRS = {
    "workbuddy": os.path.join(os.path.expanduser("~"), ".workbuddy", "skills"),
    "claude": os.path.join(os.path.expanduser("~"), ".claude", "skills"),
}


def detect_platform():
    """
    自动检测当前可用的AI平台。
    返回 (platform_name, skills_dir) 或 (None, None)
    """
    detected = []

    for name, base_dir in PLATFORM_DIRS.items():
        parent = os.path.dirname(base_dir)  # ~/.workbuddy or ~/.claude
        if os.path.isdir(parent):
            detected.append((name, base_dir))

    if len(detected) == 1:
        return detected[0]
    elif len(detected) > 1:
        # 多个平台都存在，优先 WorkBuddy
        print(f"  ℹ️  检测到多个AI平台: {', '.join(d[0] for d in detected)}")
        print(f"  → 默认安装到 WorkBuddy（可用 --target 指定其他平台）")
        return detected[0]
    else:
        return None, None


def copy_skill(src_root, dest_dir, dry_run=False):
    """
    将 Skill 文件复制到目标目录。
    Returns: list of copied files
    """
    copied = []

    for item in INSTALL_FILES:
        src = os.path.join(src_root, item)
        dst = os.path.join(dest_dir, item)

        if not os.path.exists(src):
            print(f"  ⚠️  跳过（不存在）: {item}")
            continue

        if os.path.isdir(src):
            if dry_run:
                # 统计目录下的文件数
                file_count = sum(len(files) for _, _, files in os.walk(src))
                print(f"  📁 {item} （{file_count} 个文件）→ {dst}")
                copied.append(item)
            else:
                if os.path.exists(dst):
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
                file_count = sum(len(files) for _, _, files in os.walk(dst))
                print(f"  📁 {item} （{file_count} 个文件）✅")
                copied.append(item)
        else:
            if dry_run:
                size_kb = os.path.getsize(src) / 1024
                print(f"  📄 {item} （{size_kb:.1f} KB）→ {dst}")
                copied.append(item)
            else:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                print(f"  📄 {item} ✅")
                copied.append(item)

    return copied


def main():
    parser = argparse.ArgumentParser(
        description="小红书博主拆解 Skill — 自动安装",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python install.py                    # 自动检测平台并安装
  python install.py --target workbuddy # 安装到 WorkBuddy
  python install.py --target claude    # 安装到 Claude Code
  python install.py --target /path/to  # 安装到自定义目录
  python install.py --dry-run          # 仅预览
        """,
    )

    parser.add_argument(
        "--target",
        help="目标平台（workbuddy / claude）或自定义绝对路径",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅预览安装内容，不实际复制文件",
    )

    args = parser.parse_args()

    print()
    print("=" * 60)
    print("📦 小红书博主拆解 Skill — 安装向导")
    print(f"   Skill 名称: {SKILL_NAME}")
    print(f"   源目录: {SKILL_ROOT}")
    print(f"   系统: {platform.system()} {platform.machine()}")
    print("=" * 60)
    print()

    # 确定目标目录
    if args.target:
        if args.target.lower() in PLATFORM_DIRS:
            platform_name = args.target.lower()
            skills_dir = PLATFORM_DIRS[platform_name]
        elif os.path.isabs(args.target):
            platform_name = "自定义路径"
            skills_dir = args.target
        else:
            print(f"❌ 无法识别目标: {args.target}")
            print(f"   可选值: {', '.join(PLATFORM_DIRS.keys())}，或绝对路径")
            sys.exit(1)
    else:
        platform_name, skills_dir = detect_platform()
        if not platform_name:
            print("❌ 未检测到已安装的AI平台（WorkBuddy 或 Claude Code）")
            print()
            print("你可以：")
            print("  1. 指定平台:  python install.py --target workbuddy")
            print("  2. 指定路径:  python install.py --target /path/to/skills")
            print("  3. 手动复制整个 Skill 文件夹到你的 AI 工具的 skills 目录")
            sys.exit(1)

    dest_dir = os.path.join(skills_dir, SKILL_NAME)

    print(f"📍 目标平台: {platform_name}")
    print(f"📁 安装路径: {dest_dir}")
    if args.dry_run:
        print(f"🔍 模式: 预览（dry-run）")
    print()

    # 检查是否已安装
    if os.path.exists(dest_dir) and not args.dry_run:
        print(f"⚠️  检测到已有安装，将覆盖更新...")
        print()

    # 创建目标目录
    if not args.dry_run:
        os.makedirs(dest_dir, exist_ok=True)

    # 复制文件
    print("复制文件：")
    copied = copy_skill(SKILL_ROOT, dest_dir, dry_run=args.dry_run)

    if not copied:
        print("\n❌ 没有文件被复制，请检查源目录。")
        sys.exit(1)

    print()
    if args.dry_run:
        print("=" * 60)
        print("🔍 预览完成（未实际复制文件）")
        print("   去掉 --dry-run 执行实际安装。")
        print("=" * 60)
    else:
        print("=" * 60)
        print(f"✅ 安装完成！共 {len(copied)} 项")
        print()
        print("使用方法：")
        if platform_name == "workbuddy":
            print("  在 WorkBuddy 中直接说「拆解博主 XXX」即可触发")
        elif platform_name == "claude":
            print("  在 Claude Code 中使用 /xhs-blogger-analyzer 或说「拆解博主 XXX」")
        else:
            print(f"  将 {dest_dir} 配置为你的 AI 工具的 Skill 路径")
        print("=" * 60)
    print()


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    main()
