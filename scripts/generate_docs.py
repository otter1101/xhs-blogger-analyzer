"""
Phase 3: 文档生成
读取分析数据，产出4份Markdown文档骨架，并转为Word。
通用设计：适用于任何领域的博主分析。

注意：这个脚本产出的是**模板骨架**。实际内容的深度分析（如逐条拆解、
公式提炼、选题建议）依赖AI的推理能力，由Skill执行时AI在对话中完成。
脚本负责：结构化数据→MD骨架→DOCX 的标准化流程。

用法：
    python generate_docs.py ./analysis.json "<博主名>" --output ./docs
"""

import json
import os
import sys
import re
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils.md_to_docx import md_to_docx
from utils.common import safe_filename


# ----------------------------------------------------------
# 文档模板生成
# ----------------------------------------------------------
def gen_deep_analysis_md(nickname, stats, top10, category_stats, tag_freq, comparison=None):
    """博主深度拆解"""
    lines = [
        f"# {nickname} — 博主深度拆解",
        f"\n> 数据采集时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"\n## 一、账号概览",
        f"\n| 指标 | 数据 |",
        f"|------|------|",
        f"| 笔记总数 | {stats['total']}条 |",
        f"| 视频/图文 | {stats['video_count']}视频 / {stats['normal_count']}图文 |",
        f"| 总赞 | {stats['total_likes']:,} |",
        f"| 总收藏 | {stats['total_collects']:,} |",
        f"| 总评论 | {stats['total_comments']:,} |",
        f"| 均赞 | {stats['avg_likes']:,} |",
        f"| 均收藏 | {stats['avg_collects']:,} |",
        f"| 均评论 | {stats['avg_comments']:,} |",
        f"\n## 二、内容领域分布",
        f"\n| 领域 | 数量 | 占比 | 均赞 | 代表作 |",
        f"|------|------|------|------|--------|",
    ]
    for cat, cs in category_stats.items():
        lines.append(f"| {cat} | {cs['count']} | {cs['pct']}% | {cs['avg_likes']:,} | {cs['top_note'][:25]} |")

    lines.append(f"\n## 三、高赞排行 TOP10")
    lines.append(f"\n| # | 标题 | 类型 | 赞 | 藏 | 评 |")
    lines.append(f"|---|------|------|-----|-----|-----|")
    for i, n in enumerate(top10[:10]):
        lines.append(f"| {i+1} | {n['title'][:30]} | {n['type']} | {n['likes_raw']} | {n['collects_raw']} | {n['comments_raw']} |")

    lines.append(f"\n## 四、TOP10 逐条拆解")
    lines.append(f"\n*（以下为数据骨架，AI执行时会填充深度分析）*\n")
    for i, n in enumerate(top10[:10]):
        lines.append(f"### {i+1}. {n['title']}")
        lines.append(f"- **类型**: {n['type']} | **赞**: {n['likes_raw']} | **藏**: {n['collects_raw']} | **评**: {n['comments_raw']}")
        if n.get("tags"):
            lines.append(f"- **标签**: {', '.join('#'+t for t in n['tags'][:5])}")
        lines.append(f"- **内容摘要**: {(n.get('desc', '') or '')[:100]}...")
        if n.get("comment_list"):
            lines.append(f"- **热评洞察**:")
            for c in n["comment_list"][:3]:
                prefix = "[作者]" if c.get("is_author") else ""
                lines.append(f"  - {prefix}{c['user']}: {c['content'][:60]}")
        lines.append("")

    lines.append(f"\n## 五、核心标签")
    lines.append(f"\n| 标签 | 出现次数 |")
    lines.append(f"|------|---------|")
    for tag, count in tag_freq[:15]:
        lines.append(f"| #{tag} | {count} |")

    if comparison:
        lines.append(f"\n## 六、与自己账号对比")
        ss = comparison["self_stats"]
        ts = comparison["target_stats"]
        lines.append(f"\n| 指标 | 自己 | 对标博主 | 差距 |")
        lines.append(f"|------|------|---------|------|")
        for key, label in [("total", "笔记数"), ("avg_likes", "均赞"), ("avg_collects", "均收藏")]:
            diff = ts[key] - ss[key]
            lines.append(f"| {label} | {ss[key]:,} | {ts[key]:,} | {diff:+,} |")

    return "\n".join(lines)


def gen_content_formula_md(nickname, top10, category_stats):
    """内容公式总结"""
    lines = [
        f"# {nickname} — 内容公式总结",
        f"\n> 从 TOP10 爆款笔记中提取的可复用公式",
        f"\n## 一、标题公式",
        f"\n*（AI执行时基于 TOP10 标题提炼具体公式）*\n",
    ]
    for i, n in enumerate(top10[:10]):
        lines.append(f"{i+1}. 「{n['title']}」({n['likes_raw']}赞)")
    
    lines.extend([
        f"\n## 二、开头公式",
        f"\n*（AI执行时分析 TOP10 正文开头提炼）*\n",
        f"\n## 三、内容结构模板",
        f"\n*（AI执行时按内容类型归纳结构模板）*\n",
        f"\n## 四、CTA（行动号召）公式",
        f"\n*（AI执行时提取高互动笔记的CTA模式）*\n",
        f"\n## 五、视觉/排版公式",
        f"\n*（AI执行时归纳封面、排版、emoji使用模式）*\n",
        f"\n## 六、各领域最佳公式",
    ])
    for cat, cs in category_stats.items():
        lines.append(f"\n### {cat} ({cs['count']}条, 均赞{cs['avg_likes']:,})")
        lines.append(f"- 代表作: {cs['top_note'][:30]}")
        lines.append(f"- 公式: *（AI填充）*")
    
    return "\n".join(lines)


def gen_topic_library_md(nickname, top10, category_stats, tag_freq):
    """选题素材库"""
    lines = [
        f"# {nickname} — 选题素材库",
        f"\n> 基于 {nickname} 全量笔记提炼的可借鉴选题",
        f"\n## 一、已验证的爆款选题",
        f"\n| # | 选题 | 赞数 | 领域 | 可改编方向 |",
        f"|---|------|------|------|-----------|",
    ]
    for i, n in enumerate(top10[:10]):
        lines.append(f"| {i+1} | {n['title'][:30]} | {n['likes_raw']} | {n['category']} | *AI填充* |")

    lines.extend([
        f"\n## 二、各领域可借鉴选题",
    ])
    for cat, cs in category_stats.items():
        lines.append(f"\n### {cat} ({cs['count']}条)")
        lines.append(f"- 代表作: {cs['top_note'][:30]}")
        lines.append(f"- 改编建议: *（AI填充）*")

    lines.extend([
        f"\n## 三、差异化赛道建议",
        f"\n*（AI执行时基于标签和领域空白分析）*\n",
        f"\n## 四、热门标签参考",
        f"\n| 标签 | 使用次数 |",
        f"|------|---------|",
    ])
    for tag, count in tag_freq[:15]:
        lines.append(f"| #{tag} | {count} |")

    lines.extend([
        f"\n## 五、选题优先级排序",
        f"\n*（AI执行时综合难度、流量、差异化打分排序）*",
        f"\n## 六、系列IP建议",
        f"\n*（AI执行时基于内容模式提出系列化建议）*",
    ])
    
    return "\n".join(lines)


def gen_structured_analysis_md(nickname, stats, notes_summary, category_stats, tag_freq):
    """全量笔记结构化分析"""
    lines = [
        f"# {nickname} — 全量笔记结构化分析",
        f"\n> {stats['total']}条笔记的完整数据视角",
        f"\n## 一、数据总览",
        f"\n| 指标 | 数值 |",
        f"|------|------|",
        f"| 总笔记 | {stats['total']} |",
        f"| 视频 | {stats['video_count']} ({round(stats['video_count']/stats['total']*100)}%) |",
        f"| 图文 | {stats['normal_count']} ({round(stats['normal_count']/stats['total']*100)}%) |",
        f"| 总赞 | {stats['total_likes']:,} |",
        f"| 总收藏 | {stats['total_collects']:,} |",
        f"| 总评论 | {stats['total_comments']:,} |",
        f"\n## 二、内容领域分布",
        f"\n| 领域 | 数量 | 占比 | 均赞 |",
        f"|------|------|------|------|",
    ]
    for cat, cs in category_stats.items():
        lines.append(f"| {cat} | {cs['count']} | {cs['pct']}% | {cs['avg_likes']:,} |")

    lines.extend([
        f"\n## 三、全量笔记列表",
        f"\n| # | 标题 | 类型 | 赞 | 藏 | 评 | 领域 |",
        f"|---|------|------|-----|-----|-----|------|",
    ])
    for i, n in enumerate(notes_summary[:100]):  # 最多100条
        lines.append(
            f"| {i+1} | {n['title'][:25]} | {n['type']} | "
            f"{n['likes_raw']} | {n['collects_raw']} | {n['comments_raw']} | {n['category']} |"
        )

    lines.extend([
        f"\n## 四、发展趋势分析",
        f"\n*（AI执行时基于时间序列分析内容迁移和增长规律）*",
        f"\n## 五、爆款公式拆解",
        f"\n*（AI执行时从高赞笔记中提取可复制的成功模式）*",
        f"\n## 六、竞争格局与机会",
        f"\n*（AI执行时分析该赛道的竞争态势和切入点）*",
    ])
    
    return "\n".join(lines)


# ----------------------------------------------------------
# 主函数
# ----------------------------------------------------------
def generate_docs(analysis_path, nickname, output_dir, notes_details_path=None):
    """
    生成4份分析文档（MD + DOCX）。
    
    Args:
        analysis_path: analyze.py 产出的分析JSON路径
        nickname: 博主昵称
        output_dir: 最终docx输出目录
        notes_details_path: 原始详情JSON（用于结构化分析的完整笔记列表）
    """
    os.makedirs(output_dir, exist_ok=True)
    
    with open(analysis_path, "r", encoding="utf-8") as f:
        analysis = json.load(f)
    
    stats = analysis["stats"]
    top10 = analysis["top10"]
    category_stats = analysis["category_stats"]
    tag_freq = analysis["tag_freq"]
    comparison = analysis.get("comparison")
    
    # 获取完整笔记列表（优先从 analysis.json 中读取已分类的 notes）
    notes_summary = []
    if "notes" in analysis and analysis["notes"]:
        # analysis.json 已包含完整笔记列表（含 category），直接使用
        notes_summary = analysis["notes"]
    elif notes_details_path and os.path.exists(notes_details_path):
        # 兜底：从原始详情 JSON 重新解析（category 可能不准确）
        import logging
        logging.warning("analysis.json 中无 notes 列表，从原始详情重新解析（category 可能不准确）")
        from utils.common import parse_count as _parse_count
        with open(notes_details_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        for item in raw:
            if "_error" in item:
                continue
            note = item.get("data", {}).get("note", item)
            interact = note.get("interactInfo", item.get("interactInfo", {}))
            notes_summary.append({
                "title": note.get("title", note.get("displayTitle", "")),
                "type": note.get("type", "normal"),
                "likes_raw": str(interact.get("likedCount", "0")),
                "collects_raw": str(interact.get("collectedCount", "0")),
                "comments_raw": str(interact.get("commentCount", "0")),
                "category": "其他",
            })
    else:
        # 用top10凑
        notes_summary = top10

    safe_name = safe_filename(nickname)

    # 生成4份MD
    docs = {
        "博主深度拆解": gen_deep_analysis_md(nickname, stats, top10, category_stats, tag_freq, comparison),
        "内容公式总结": gen_content_formula_md(nickname, top10, category_stats),
        "选题素材库": gen_topic_library_md(nickname, top10, category_stats, tag_freq),
        "全量笔记结构化分析": gen_structured_analysis_md(nickname, stats, notes_summary, category_stats, tag_freq),
    }

    # 过程文件目录
    process_dir = os.path.join(output_dir, "_过程文件", "原始素材")
    os.makedirs(process_dir, exist_ok=True)

    results = []
    for doc_type, md_content in docs.items():
        md_name = f"{safe_name}_{doc_type}.md"
        docx_name = f"{safe_name}_{doc_type}.docx"
        
        # MD → 过程文件
        md_path = os.path.join(process_dir, md_name)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        
        # DOCX → 根目录
        docx_path = os.path.join(output_dir, docx_name)
        try:
            md_to_docx(md_path, docx_path)
            size_kb = os.path.getsize(docx_path) / 1024
            print(f"  ✅ {docx_name} ({size_kb:.0f}KB)")
            results.append({"name": docx_name, "path": docx_path, "size_kb": size_kb, "ok": True})
        except Exception as e:
            print(f"  ❌ {docx_name}: {e}")
            results.append({"name": docx_name, "path": docx_path, "ok": False, "error": str(e)})

    return results


# ----------------------------------------------------------
# CLI
# ----------------------------------------------------------
if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="生成分析文档")
    parser.add_argument("analysis_path", help="分析数据JSON路径")
    parser.add_argument("nickname", help="博主昵称")
    parser.add_argument("-o", "--output", default=".", help="输出目录")
    parser.add_argument("--details", help="原始详情JSON路径（用于完整列表）")
    args = parser.parse_args()

    print(f"\n📝 生成文档: {args.nickname}")
    print("=" * 50)
    results = generate_docs(args.analysis_path, args.nickname, args.output, args.details)
    
    ok = sum(1 for r in results if r["ok"])
    print(f"\n完成: {ok}/{len(results)} 份文档生成成功")
