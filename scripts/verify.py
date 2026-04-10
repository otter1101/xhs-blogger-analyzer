"""
数据校验模块
被 crawl_blogger.py / deep_analyze.py / run.py 自动 import 调用。
也可通过 CLI 手动运行全部 7 项检查（调试用）。

用法：
    python scripts/verify.py ./data/博主名_notes_details.json --expected-count 50
    python scripts/verify.py ./data/博主名_notes_details.json \
        --expected-count 50 \
        --profile ./data/博主名_profile.json \
        --output-dir ./output \
        --expected-files 博主名_蒸馏报告.html 博主名_创作指南.skill/SKILL.md
"""

import json
import os
import sys
import argparse


def _valid_notes(details):
    """过滤掉爬取失败的条目"""
    return [d for d in details if "_error" not in d]


def _extract_id(detail):
    """从 detail 中提取笔记 ID"""
    return (
        detail.get("feed_id")
        or detail.get("data", {}).get("note", {}).get("noteId")
        or detail.get("data", {}).get("note", {}).get("id")
        or detail.get("id")
    )


def _extract_desc(detail):
    """从 detail 中提取正文"""
    # 路径1：data.note.desc
    desc = detail.get("data", {}).get("note", {}).get("desc", "")
    if desc:
        return desc
    # 路径2：顶层 desc（旧版数据格式）
    desc = detail.get("desc", "")
    if desc:
        return desc
    # 路径3：noteCard.desc（兜底）
    return detail.get("noteCard", {}).get("desc", "")


def _extract_time(detail):
    """从 detail 中提取发布时间"""
    return detail.get("data", {}).get("note", {}).get("time")


# ==========================================
# V1 正文完整率 —— 覆盖 P1-11
# ==========================================
def check_content_completeness(details):
    """
    检查笔记正文完整率。
    返回 (ok: bool, message: str)
    ok=False 时调用方应 sys.exit(1)
    """
    valid = _valid_notes(details)
    if not valid:
        return False, "[V1] 正文完整率: 无有效笔记 🚨 ERROR"

    has_content = [d for d in valid if len(_extract_desc(d).strip()) > 10]
    rate = len(has_content) / len(valid)
    pct = round(rate * 100, 1)

    if rate < 0.8:
        return (
            False,
            f"[V1] 正文完整率: {len(has_content)}/{len(valid)} ({pct}%) "
            f"🚨 ERROR — 大部分笔记缺少正文，请确认是否逐条调用了 get_feed_detail",
        )
    return True, f"[V1] 正文完整率: {len(has_content)}/{len(valid)} ({pct}%) ✅"


# ==========================================
# V2 爬取条数 —— 覆盖 P1-7
# ==========================================
def check_note_count(details, expected_count):
    """
    检查实际爬取条数与目标条数的偏差。
    偏差 > 20% 返回 WARNING，不阻断。
    """
    valid = _valid_notes(details)
    actual = len(valid)
    if expected_count <= 0:
        return f"[V2] 爬取条数: {actual} 条（未设置目标）"

    deviation = abs(actual - expected_count) / expected_count
    pct = round(deviation * 100, 1)

    if deviation > 0.2:
        return (
            f"[V2] 爬取条数: 实际 {actual}/目标 {expected_count} (偏差 {pct}%) "
            f"⚠️ WARNING"
        )
    return f"[V2] 爬取条数: 实际 {actual}/目标 {expected_count} (偏差 {pct}%) ✅"


# ==========================================
# V3 时间字段 —— 覆盖发现B
# ==========================================
def check_time_field(details):
    """
    检查笔记时间字段完整率。
    时间字段只存在于 get_feed_detail 返回中，缺失说明可能跳过了逐条爬取。
    """
    valid = _valid_notes(details)
    if not valid:
        return "[V3] 时间字段: 无有效笔记"

    has_time = [d for d in valid if _extract_time(d)]
    rate = len(has_time) / len(valid)
    pct = round(rate * 100, 1)

    if rate < 0.8:
        return (
            f"[V3] 时间字段: {len(has_time)}/{len(valid)} ({pct}%) "
            f"⚠️ WARNING — 时间字段缺失，发展趋势分析可能不准确"
        )
    return f"[V3] 时间字段: {len(has_time)}/{len(valid)} ({pct}%) ✅"


# ==========================================
# V4 去重检查 —— 覆盖 P1-8
# ==========================================
def check_duplicates(details):
    """
    检查笔记 ID 是否有重复。
    """
    valid = _valid_notes(details)
    ids = [_extract_id(d) for d in valid if _extract_id(d)]
    unique_ids = set(ids)
    dup_count = len(ids) - len(unique_ids)

    if dup_count > 0:
        return f"[V4] 去重检查: 发现 {dup_count} 条重复笔记 ⚠️ WARNING"
    return f"[V4] 去重检查: 无重复 ({len(unique_ids)} 条唯一) ✅"


# ==========================================
# V5 采样水印 —— 覆盖 P1-8 报告标注
# ==========================================
def get_sample_watermark(details, profile=None):
    """
    生成采样范围水印字符串，供写入报告头部。
    注意：xiaohongshu-mcp 的 user_profile 不返回博主总笔记数，
    只能显示本次采样条数。
    返回水印字符串。
    """
    valid = _valid_notes(details)
    actual = len(valid)
    watermark = f"[V5] 采样水印: 📊 本次采样 {actual} 条 ✅"
    return watermark


# ==========================================
# V6 产出文件数 —— 覆盖 P1-10
# ==========================================
def check_output_files(output_dir, expected_files):
    """
    检查产出文件是否存在且非空。
    expected_files 支持嵌套路径，例如 `灵均Kikky_创作指南.skill/SKILL.md`。
    返回 (ok: bool, message: str)
    """
    if not output_dir or not expected_files:
        return True, "[V6] 产出文件数: 未指定，跳过"

    missing = []
    empty = []
    for fname in expected_files:
        fpath = os.path.join(output_dir, fname)
        if not os.path.exists(fpath):
            missing.append(fname)
        elif os.path.getsize(fpath) == 0:
            empty.append(fname)

    total = len(expected_files)
    issues = missing + empty
    if issues:
        return (
            False,
            f"[V6] 产出文件数: {total - len(issues)}/{total} "
            f"🚨 ERROR — 缺失或为空: {', '.join(issues)}",
        )
    return True, f"[V6] 产出文件数: {total}/{total} ✅"


def build_expected_output_files(nickname, mode="A"):
    """
    根据模式生成 V6 的预期产出文件列表。
    mode="A"：HTML + 创作指南 skill 文件夹入口
    mode="B"：HTML + 创作基因 skill 文件夹入口
    """
    if mode == "B":
        skill_entry = f"{nickname}_创作基因.skill/SKILL.md"
    else:
        skill_entry = f"{nickname}_创作指南.skill/SKILL.md"

    return [
        f"{nickname}_蒸馏报告.html",
        skill_entry,
    ]


# ==========================================
# V7 垃圾文件检测 —— 覆盖 P1-5 + P3-1
# ==========================================
def check_junk_files(work_dir):
    """
    检查工作目录根层是否有 AI 自行创建的 .py 文件（scripts/ 之外的）。
    返回 warning 字符串。
    """
    if not work_dir or not os.path.isdir(work_dir):
        return "[V7] 垃圾文件: 目录不存在，跳过"

    scripts_dir = os.path.join(work_dir, "scripts")
    junk = []
    for fname in os.listdir(work_dir):
        fpath = os.path.join(work_dir, fname)
        if fname.endswith(".py") and os.path.isfile(fpath):
            junk.append(fname)

    if junk:
        return f"[V7] 垃圾文件: 发现疑似 AI 自建脚本 ⚠️ WARNING — {', '.join(junk)}"
    return "[V7] 垃圾文件: 无 ✅"


# ==========================================
# CLI 入口（调试用）
# ==========================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="数据校验工具（调试用）")
    parser.add_argument("details_json", help="笔记详情 JSON 路径")
    parser.add_argument("--expected-count", type=int, default=50, help="目标爬取条数")
    parser.add_argument("--profile", help="博主 profile JSON 路径（用于采样水印）")
    parser.add_argument("--output-dir", help="产出目录（用于 V6/V7 检查）")
    parser.add_argument("--expected-files", nargs="+", help="预期产出文件名列表")
    args = parser.parse_args()

    # 加载数据
    with open(args.details_json, "r", encoding="utf-8") as f:
        details = json.load(f)

    profile = None
    if args.profile and os.path.exists(args.profile):
        with open(args.profile, "r", encoding="utf-8") as f:
            profile = json.load(f)

    print("\n" + "=" * 60)
    print("📋 数据校验报告")
    print("=" * 60)

    errors = 0
    warnings = 0

    v1_ok, v1_msg = check_content_completeness(details)
    print(v1_msg)
    if not v1_ok:
        errors += 1

    v2_msg = check_note_count(details, args.expected_count)
    print(v2_msg)
    if "WARNING" in v2_msg:
        warnings += 1

    v3_msg = check_time_field(details)
    print(v3_msg)
    if "WARNING" in v3_msg:
        warnings += 1

    v4_msg = check_duplicates(details)
    print(v4_msg)
    if "WARNING" in v4_msg:
        warnings += 1

    v5_msg = get_sample_watermark(details, profile)
    print(v5_msg)

    v6_ok, v6_msg = check_output_files(args.output_dir, args.expected_files)
    print(v6_msg)
    if not v6_ok:
        errors += 1

    v7_msg = check_junk_files(args.output_dir)
    print(v7_msg)
    if "WARNING" in v7_msg:
        warnings += 1

    print("=" * 60)
    print(f"通过: {7 - errors - warnings}项  警告: {warnings}项  错误: {errors}项")
    if errors > 0:
        print("结论: 🚨 数据不可用，请补爬")
        sys.exit(1)
    elif warnings > 0:
        print("结论: ⚠️ 请确认警告后继续")
    else:
        print("结论: ✅ 数据可用")
    print("=" * 60)
