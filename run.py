"""
小红书博主拆解 Skill — 一键运行入口
串联 Phase 0（环境准备）→ Phase 1（数据采集）→ Phase 2（数据分析）→ Phase 3（文档骨架）→ Phase 3.5（AI深度分析）

用法：
    python run.py "<博主名>"
    python run.py "<博主名>" --self "<自己昵称>"
    python run.py "<博主名>" --keywords "烘焙,食谱,探店"
    python run.py "<博主名>" --self "<自己昵称>" --keywords "AI,工具,教程"
    python run.py "<博主名>" --skip-env          # 跳过环境检查
    python run.py "<博主名>" --data-dir ./mydata  # 自定义数据目录
    python run.py "<博主名>" --output-dir ./out   # 自定义输出目录
"""

import sys
import os
import argparse
import subprocess

# 脚本根目录（run.py 所在位置）
SKILL_ROOT = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(SKILL_ROOT, "scripts")


def run_phase(phase_name, cmd, cwd=None):
    """运行一个 Phase，失败时退出"""
    print()
    print("=" * 60)
    print(f"▶ {phase_name}")
    print("=" * 60)

    result = subprocess.run(
        cmd,
        cwd=cwd or SKILL_ROOT,
    )

    if result.returncode != 0:
        print(f"\n❌ {phase_name} 失败（退出码 {result.returncode}）")
        print("   请检查上面的错误信息，修复后重新运行。")
        sys.exit(result.returncode)

    print(f"✅ {phase_name} 完成")


def main():
    parser = argparse.ArgumentParser(
        description="小红书博主拆解 Skill — 一键运行",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python run.py "蔡不菜（AI版）"
  python run.py "蔡不菜（AI版）" --self "Aha水濑"
  python run.py "蔡不菜（AI版）" --keywords "AI,工具,教程"
  python run.py "蔡不菜（AI版）" --skip-env
        """,
    )

    parser.add_argument("blogger", help="目标博主名称或小红书号")
    parser.add_argument("--self", dest="self_blogger", help="自己的博主名称（用于对比分析）")
    parser.add_argument("--keywords", help="领域关键词（逗号分隔），用于扩展搜索")
    parser.add_argument("--skip-env", action="store_true", help="跳过 Phase 0 环境检查")
    parser.add_argument("--port", type=int, default=18060, help="MCP 服务端口（默认 18060）")
    parser.add_argument("--data-dir", default="./data", help="数据存放目录（默认 ./data）")
    parser.add_argument("--output-dir", default="./output", help="文档输出目录（默认 ./output）")

    args = parser.parse_args()

    blogger = args.blogger
    python = sys.executable

    print()
    print("🚀 小红书博主拆解 Skill — 一键运行")
    print(f"   目标博主: {blogger}")
    if args.self_blogger:
        print(f"   对比账号: {args.self_blogger}")
    if args.keywords:
        print(f"   领域关键词: {args.keywords}")
    print(f"   数据目录: {args.data_dir}")
    print(f"   输出目录: {args.output_dir}")
    print(f"   MCP 端口: {args.port}")
    print()

    # ----------------------------------------------------------
    # Phase 0: 环境自动准备
    # ----------------------------------------------------------
    if not args.skip_env:
        run_phase(
            "Phase 0: 环境自动准备",
            [python, os.path.join(SCRIPTS_DIR, "check_env.py"), "--port", str(args.port)],
        )
    else:
        print("\n⏭️  跳过 Phase 0（--skip-env）")

    # ----------------------------------------------------------
    # Phase 1: 数据采集 — 目标博主
    # ----------------------------------------------------------
    crawl_cmd = [
        python, os.path.join(SCRIPTS_DIR, "crawl_blogger.py"),
        blogger, "-o", args.data_dir,
    ]
    if args.keywords:
        crawl_cmd.extend(["--keywords", args.keywords])

    run_phase("Phase 1: 数据采集 — 目标博主", crawl_cmd)

    # Phase 1 (可选): 采集自己的数据
    if args.self_blogger:
        self_crawl_cmd = [
            python, os.path.join(SCRIPTS_DIR, "crawl_blogger.py"),
            args.self_blogger, "--self", "-o", args.data_dir,
        ]
        run_phase("Phase 1: 数据采集 — 自己账号", self_crawl_cmd)

    # ----------------------------------------------------------
    # Phase 2: 数据分析
    # ----------------------------------------------------------
    # 构造笔记详情文件路径（crawl_blogger.py 的输出命名规则）
    from scripts.utils.common import safe_filename as _safe
    blogger_safe = _safe(blogger)
    details_file = os.path.join(args.data_dir, f"{blogger_safe}_notes_details.json")

    if not os.path.isfile(details_file):
        print(f"\n❌ 未找到笔记详情文件: {details_file}")
        print("   Phase 1 可能未正确完成，请检查数据目录。")
        sys.exit(1)

    analyze_cmd = [
        python, os.path.join(SCRIPTS_DIR, "analyze.py"),
        details_file, "-o", args.data_dir,
    ]
    if args.self_blogger:
        self_safe = _safe(args.self_blogger)
        self_details = os.path.join(args.data_dir, f"{self_safe}_notes_details.json")
        if os.path.isfile(self_details):
            analyze_cmd.extend(["--self", self_details])
        else:
            print(f"\n⚠️  自己账号的笔记详情未找到 ({self_details})，跳过对比分析")

    run_phase("Phase 2: 数据分析", analyze_cmd)

    # ----------------------------------------------------------
    # Phase 3: 文档生成
    # ----------------------------------------------------------
    analysis_file = os.path.join(args.data_dir, f"{blogger_safe}_analysis.json")

    if not os.path.isfile(analysis_file):
        print(f"\n❌ 未找到分析文件: {analysis_file}")
        print("   Phase 2 可能未正确完成，请检查数据目录。")
        sys.exit(1)

    gendocs_cmd = [
        python, os.path.join(SCRIPTS_DIR, "generate_docs.py"),
        analysis_file, blogger,
        "-o", args.output_dir,
        "--details", details_file,
    ]

    run_phase("Phase 3: 文档生成", gendocs_cmd)

    # ----------------------------------------------------------
    # Phase 3.5: AI 深度分析（增强版文档）
    # ----------------------------------------------------------
    deep_cmd = [
        python, os.path.join(SCRIPTS_DIR, "deep_analyze.py"),
        analysis_file, blogger,
        "-o", args.output_dir,
        "--details", details_file,
    ]

    run_phase("Phase 3.5: AI 深度分析", deep_cmd)

    # ----------------------------------------------------------
    # 完成
    # ----------------------------------------------------------
    print()
    print("=" * 60)
    print("🎉 全部完成！")
    print(f"   文档输出目录: {os.path.abspath(args.output_dir)}")
    print("=" * 60)
    print()
    print("生成的文档：")
    if os.path.isdir(args.output_dir):
        for f in sorted(os.listdir(args.output_dir)):
            if f.endswith(".docx"):
                print(f"  📄 {f}")
    print()


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    main()
