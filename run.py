"""
博主蒸馏器 — 一键运行入口
串联 Phase 0（环境准备）→ Phase 0.5（前置交互）→ Phase 1（数据采集）
→ Phase 2（数据分析）→ Phase 3（蒸馏 + 产出物生成）

用法：
    python run.py "<博主名>"
    python run.py "<博主名>" --self "<自己昵称>"
    python run.py "<博主名>" --keywords "烘焙,食谱,探店"
    python run.py "<博主名>" --skip-env
"""

import sys
import os
import argparse
import subprocess

# 脚本根目录（run.py 所在位置）
SKILL_ROOT = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(SKILL_ROOT, "scripts")
sys.path.insert(0, SCRIPTS_DIR)

from verify import check_junk_files
from utils.common import safe_filename


MODE_OPTIONS = {"A", "B"}
COUNT_OPTIONS = {"1": 30, "2": 50, "3": 80}


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


def prompt_phase_0_5():
    """展示操作手册要求的前置交互，并返回 (mode, max_notes)。"""
    print()
    print("─────────────────────────────────────")
    print("🎯 欢迎使用博主蒸馏器！")
    print()
    print("  ⚠️  ═══════════════════════════════════════════════════")
    print("  ⚠️  安全提示：")
    print("  ⚠️  1. 建议使用小红书【小号】登录，切勿使用主力账号")
    print("  ⚠️  2. 切勿频繁爬取，以免触发风控导致封号")
    print("  ⚠️  3. 本 Skill 正在进行安全升级，请注意使用风险")
    print("  ⚠️  ═══════════════════════════════════════════════════")
    print()
    print("请选择分析模式：")
    print()
    print("  🔍 A — 拆解对标博主")
    print("     爬取 TA 的笔记 → 提炼内容公式和思维方式")
    print("     → 生成「TA的名字_创作指南.skill/」")
    print("     以后写内容时加载它，相当于随时在线的内容教练")
    print()
    print("  🪞 B — 诊断我的账号")
    print("     爬取你的笔记 → 找到内容基因和增长瓶颈")
    print("     → 生成「你的名字_创作基因.skill/」")
    print("     让 AI 写出的内容像你自己写的，无缝嵌入创作工作流")
    print()
    print("  ⚡ C — 对标 + 借鉴（暂未开放，v2.1 支持）")
    print()

    while True:
        user_mode = input("请输入 A 或 B：\n").strip().upper()
        if user_mode in MODE_OPTIONS:
            break
        if user_mode == "C":
            print("⚡ C — 对标 + 借鉴暂未开放，请先选择 A 或 B。")
        else:
            print("请输入 A 或 B。")

    print()
    print("📊 爬取数量（推荐 50 条）：")
    print("  ① 30 条 — 快速扫描（约 15-25 分钟）")
    print("  ② 50 条 — 推荐档位（约 30-45 分钟）")
    print("  ③ 80 条 — 深度分析（约 45-65 分钟）")
    print()
    print("💡 每 10 条自动存盘，中断了下次继续。")
    print("─────────────────────────────────────")

    while True:
        count_choice = input("请选择 1 / 2 / 3：\n").strip()
        if count_choice in COUNT_OPTIONS:
            max_notes = COUNT_OPTIONS[count_choice]
            break
        print("请输入 1 / 2 / 3。")

    return user_mode, max_notes


def main():
    parser = argparse.ArgumentParser(
        description="博主蒸馏器 — 一键运行",
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
    parser.add_argument("--self", dest="self_blogger", help="自己的博主名称（用于额外对比分析）")
    parser.add_argument("--keywords", help="领域关键词（逗号分隔），用于扩展搜索")
    parser.add_argument("--skip-env", action="store_true", help="跳过 Phase 0 环境检查")
    parser.add_argument("--port", type=int, default=18060, help="MCP 服务端口（默认 18060）")
    parser.add_argument("--data-dir", default="./data", help="数据存放目录（默认 ./data）")
    parser.add_argument("--output-dir", default="./output", help="产出目录（默认 ./output）")

    args = parser.parse_args()

    blogger = args.blogger
    python = sys.executable

    print()
    print("🚀 博主蒸馏器 — 一键运行")
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
    # Phase 0.5: 前置交互
    # ----------------------------------------------------------
    user_mode, max_notes = prompt_phase_0_5()

    print()
    print(f"✅ 模式选择: {user_mode}")
    print(f"✅ 爬取数量: {max_notes} 条")

    # ----------------------------------------------------------
    # Phase 1: 数据采集 — 目标博主
    # ----------------------------------------------------------
    crawl_cmd = [
        python, os.path.join(SCRIPTS_DIR, "crawl_blogger.py"),
        blogger, "-o", args.data_dir,
        "--max-notes", str(max_notes),
    ]
    if args.keywords:
        crawl_cmd.extend(["--keywords", args.keywords])

    run_phase("Phase 1: 数据采集 — 目标博主", crawl_cmd)

    # Phase 1 (可选): 采集自己的数据
    if args.self_blogger:
        self_crawl_cmd = [
            python, os.path.join(SCRIPTS_DIR, "crawl_blogger.py"),
            args.self_blogger, "--self", "-o", args.data_dir,
            "--max-notes", str(max_notes),
        ]
        run_phase("Phase 1: 数据采集 — 自己账号", self_crawl_cmd)

    # ----------------------------------------------------------
    # Phase 2: 数据分析 + 认知层提取
    # ----------------------------------------------------------
    blogger_safe = safe_filename(blogger)
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
        self_safe = safe_filename(args.self_blogger)
        self_details = os.path.join(args.data_dir, f"{self_safe}_notes_details.json")
        if os.path.isfile(self_details):
            analyze_cmd.extend(["--self", self_details])
        else:
            print(f"\n⚠️  自己账号的笔记详情未找到 ({self_details})，跳过对比分析")

    run_phase("Phase 2: 数据分析 + 认知层提取", analyze_cmd)

    # ----------------------------------------------------------
    # Phase 3: 蒸馏 + 产出物生成（Step A）
    # ----------------------------------------------------------
    analysis_file = os.path.join(args.data_dir, f"{blogger_safe}_analysis.json")

    if not os.path.isfile(analysis_file):
        print(f"\n❌ 未找到分析文件: {analysis_file}")
        print("   Phase 2 可能未正确完成，请检查数据目录。")
        sys.exit(1)

    deep_cmd = [
        python, os.path.join(SCRIPTS_DIR, "deep_analyze.py"),
        analysis_file, blogger,
        "-o", args.output_dir,
        "--details", details_file,
        "--mode", user_mode,
    ]

    run_phase("Phase 3: 蒸馏 + 产出物生成（Step A）", deep_cmd)

    # ----------------------------------------------------------
    # 完成
    # ----------------------------------------------------------
    print()
    print("=" * 60)
    print("🎉 Step A 已完成！")
    print(f"   产出目录: {os.path.abspath(args.output_dir)}")
    print("=" * 60)
    print()

    task_path = os.path.join(
        args.output_dir,
        "_过程文件",
        "原始素材",
        f"{blogger_safe}_AI蒸馏任务.md",
    )

    if user_mode == "A":
        expected_skill = f"{blogger_safe}_创作指南.skill/SKILL.md"
    else:
        expected_skill = f"{blogger_safe}_创作基因.skill/SKILL.md"

    print("接下来由宿主 AI 读取 AI蒸馏任务，继续完成最终产物：")
    print(f"  📋 AI蒸馏任务: {task_path}")
    print(f"  🌐 HTML 报告: {blogger_safe}_蒸馏报告.html")
    print(f"  🧠 Skill 文件夹: {expected_skill}")
    print()

    # === V7 垃圾文件检测（自动运行）===
    v7_msg = check_junk_files(SKILL_ROOT)
    if "WARNING" in v7_msg:
        print(v7_msg)
        print("   建议清理上述文件，避免污染工作目录。")


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    main()
