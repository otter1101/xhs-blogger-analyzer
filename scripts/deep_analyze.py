"""
Phase 3.5: AI 深度分析
读取 Phase 3 产出的 MD 骨架和 Phase 2 的分析数据，
生成 AI 深度分析 prompt，输出增强版 MD 和 DOCX。

设计理念：
- 脚本本身不调用外部 AI API（用户可能没有 API key）
- 脚本做两件事：
  1. 生成一份结构化 AI Prompt（.md 文件），让宿主 AI 在对话中完成分析
  2. 基于分析数据做**确定性填充**——不需要 AI 推理就能补全的内容（统计规律、模式识别）

用法：
    python deep_analyze.py ./data/<博主名>_analysis.json "<博主名>" -o ./output
    python deep_analyze.py ./data/<博主名>_analysis.json "<博主名>" -o ./output --details ./data/<博主名>_notes_details.json
"""

import json
import os
import sys
import re
import argparse
from datetime import datetime
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils.common import safe_filename, parse_count
from utils.md_to_docx import md_to_docx
from verify import check_content_completeness, check_output_files


# ----------------------------------------------------------
# 辅助分析函数（确定性分析，不需要 AI）
# ----------------------------------------------------------

def extract_title_patterns(titles):
    """从标题列表中提取常见模式"""
    patterns = {
        "数字型": r"\d+",
        "疑问型": r"[？?]|怎么|如何|为什么|什么",
        "感叹型": r"[！!]|绝了|太|真的|居然|竟然",
        "教程型": r"教程|手把手|保姆级|步骤|方法|攻略",
        "列表型": r"合集|盘点|推荐|必备|top|榜",
        "对比型": r"vs|对比|区别|差异|还是",
        "故事型": r"我|亲身|经历|踩坑|分享|心得",
        "悬念型": r"\.\.\.|…|竟然|没想到|万万|千万",
    }
    results = {}
    for pattern_name, regex in patterns.items():
        count = sum(1 for t in titles if re.search(regex, t, re.IGNORECASE))
        if count > 0:
            pct = round(count / len(titles) * 100, 1)
            examples = [t for t in titles if re.search(regex, t, re.IGNORECASE)][:3]
            results[pattern_name] = {"count": count, "pct": pct, "examples": examples}
    return results


def extract_emoji_patterns(descs):
    """从正文中提取 emoji 使用模式"""
    emoji_pattern = re.compile(
        r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF"
        r"\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF"
        r"\U00002702-\U000027B0\U0001F900-\U0001F9FF"
        r"\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF"
        r"\U00002600-\U000026FF]+"
    )
    emoji_counter = Counter()
    notes_with_emoji = 0
    for desc in descs:
        if not desc:
            continue
        emojis = emoji_pattern.findall(desc)
        if emojis:
            notes_with_emoji += 1
            for e in emojis:
                for char in e:
                    emoji_counter[char] += 1
    return {
        "notes_with_emoji": notes_with_emoji,
        "total_notes": len(descs),
        "emoji_usage_pct": round(notes_with_emoji / len(descs) * 100, 1) if descs else 0,
        "top_emojis": emoji_counter.most_common(10),
    }


def extract_cta_patterns(descs):
    """从正文中提取 CTA（行动号召）模式"""
    cta_patterns = {
        "关注引导": [r"关注", r"点个关注", r"记得关注"],
        "收藏引导": [r"收藏", r"先收藏", r"码住", r"mark"],
        "点赞引导": [r"点赞", r"双击", r"给个赞"],
        "评论引导": [r"评论", r"留言", r"告诉我", r"你们觉得", r"欢迎讨论"],
        "转发引导": [r"转发", r"分享给"],
        "私信引导": [r"私信", r"私我", r"后台回复", r"滴滴"],
    }
    results = {}
    for cta_type, regexes in cta_patterns.items():
        combined = "|".join(regexes)
        count = sum(1 for d in descs if d and re.search(combined, d))
        if count > 0:
            pct = round(count / len(descs) * 100, 1) if descs else 0
            results[cta_type] = {"count": count, "pct": pct}
    return results


def analyze_content_structure(descs):
    """分析正文结构模式"""
    results = {
        "avg_length": 0,
        "short_count": 0,  # <200字
        "medium_count": 0,  # 200-500字
        "long_count": 0,    # >500字
        "has_list_count": 0,  # 包含列表格式
        "has_number_heading": 0,  # 包含数字小标题
    }
    lengths = []
    for desc in descs:
        if not desc:
            continue
        length = len(desc)
        lengths.append(length)
        if length < 200:
            results["short_count"] += 1
        elif length < 500:
            results["medium_count"] += 1
        else:
            results["long_count"] += 1

        if re.search(r"^[\s]*[\-•●]\s", desc, re.MULTILINE):
            results["has_list_count"] += 1
        if re.search(r"[①②③④⑤⑥⑦⑧⑨⑩]|[1-9][.、]", desc):
            results["has_number_heading"] += 1

    results["avg_length"] = round(sum(lengths) / len(lengths)) if lengths else 0
    return results


def detect_posting_frequency(notes_with_time):
    """分析发布频率模式"""
    timestamps = sorted([n["time"] for n in notes_with_time if n.get("time", 0) > 0])
    if len(timestamps) < 2:
        return {"pattern": "数据不足", "avg_days_between": 0}

    # 计算相邻发布间隔
    from datetime import datetime as dt
    intervals = []
    for i in range(1, len(timestamps)):
        try:
            diff = (timestamps[i] - timestamps[i - 1])
            if isinstance(diff, (int, float)):
                # 假设是毫秒时间戳
                days = diff / (1000 * 86400)
            else:
                days = diff.total_seconds() / 86400
            if 0 < days < 365:  # 排除异常值
                intervals.append(days)
        except (TypeError, ValueError):
            continue

    if not intervals:
        return {"pattern": "无法计算", "avg_days_between": 0}

    avg_days = round(sum(intervals) / len(intervals), 1)
    if avg_days <= 1:
        pattern = "日更"
    elif avg_days <= 3:
        pattern = "高频（2-3天/条）"
    elif avg_days <= 7:
        pattern = "周更"
    elif avg_days <= 14:
        pattern = "双周更"
    else:
        pattern = f"低频（约{int(avg_days)}天/条）"

    return {"pattern": pattern, "avg_days_between": avg_days, "total_intervals": len(intervals)}


def find_growth_pattern(notes):
    """分析内容发展趋势（早期 vs 近期的主题变化）"""
    if len(notes) < 6:
        return None

    # 按时间排序（已按赞排序的数据需要重新按时间排）
    time_sorted = sorted([n for n in notes if n.get("time", 0) > 0], key=lambda x: x["time"])
    if len(time_sorted) < 6:
        return None

    # 分成前半和后半
    mid = len(time_sorted) // 2
    early = time_sorted[:mid]
    recent = time_sorted[mid:]

    early_cats = Counter(n.get("category", "其他") for n in early)
    recent_cats = Counter(n.get("category", "其他") for n in recent)

    # 找到增长和衰退的类别
    all_cats = set(list(early_cats.keys()) + list(recent_cats.keys()))
    changes = {}
    for cat in all_cats:
        e_pct = round(early_cats.get(cat, 0) / len(early) * 100, 1) if early else 0
        r_pct = round(recent_cats.get(cat, 0) / len(recent) * 100, 1) if recent else 0
        changes[cat] = {"early_pct": e_pct, "recent_pct": r_pct, "delta": round(r_pct - e_pct, 1)}

    return {
        "early_count": len(early),
        "recent_count": len(recent),
        "category_shifts": changes,
    }


# ----------------------------------------------------------
# 确定性内容填充（替换骨架中的占位符）
# ----------------------------------------------------------

def gen_enhanced_deep_analysis(nickname, stats, top10, category_stats, tag_freq, 
                                title_patterns, comparison=None, notes=None):
    """增强版博主深度拆解（用确定性分析替换占位符）"""
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
    ]

    # 视频 vs 图文对比
    if stats['video_count'] > 0 and stats['normal_count'] > 0 and notes:
        video_notes = [n for n in notes if n.get("type") == "video"]
        normal_notes = [n for n in notes if n.get("type") != "video"]
        v_avg = sum(n["likes"] for n in video_notes) // len(video_notes) if video_notes else 0
        n_avg = sum(n["likes"] for n in normal_notes) // len(normal_notes) if normal_notes else 0
        lines.append(f"\n**形式偏好分析**：")
        if v_avg > n_avg * 1.5:
            lines.append(f"- 视频笔记均赞 {v_avg:,}，图文均赞 {n_avg:,}，**视频表现显著优于图文**（{round(v_avg/n_avg, 1) if n_avg else '∞'}倍）")
        elif n_avg > v_avg * 1.5:
            lines.append(f"- 图文笔记均赞 {n_avg:,}，视频均赞 {v_avg:,}，**图文表现显著优于视频**（{round(n_avg/v_avg, 1) if v_avg else '∞'}倍）")
        else:
            lines.append(f"- 视频均赞 {v_avg:,}，图文均赞 {n_avg:,}，两种形式表现**基本持平**")

    lines.append(f"\n## 二、内容领域分布")
    lines.append(f"\n| 领域 | 数量 | 占比 | 均赞 | 代表作 |")
    lines.append(f"|------|------|------|------|--------|")
    for cat, cs in category_stats.items():
        lines.append(f"| {cat} | {cs['count']} | {cs['pct']}% | {cs['avg_likes']:,} | {cs['top_note'][:25]} |")

    # 领域洞察（确定性分析）
    if category_stats:
        sorted_cats = sorted(category_stats.items(), key=lambda x: x[1]["avg_likes"], reverse=True)
        best_cat = sorted_cats[0]
        most_cat = sorted(category_stats.items(), key=lambda x: x[1]["count"], reverse=True)[0]
        lines.append(f"\n**领域数据洞察**：")
        lines.append(f"- 产量最高领域：「{most_cat[0]}」（{most_cat[1]['count']}条，占{most_cat[1]['pct']}%）")
        lines.append(f"- 均赞最高领域：「{best_cat[0]}」（均赞{best_cat[1]['avg_likes']:,}）")
        if best_cat[0] != most_cat[0]:
            lines.append(f"- ⚡ **发现**：产量最高 ≠ 效果最好。「{best_cat[0]}」均赞更高但产量不是最多，说明该领域内容更受欢迎，值得加大投入。")

    lines.append(f"\n## 三、高赞排行 TOP10")
    lines.append(f"\n| # | 标题 | 类型 | 赞 | 藏 | 评 |")
    lines.append(f"|---|------|------|-----|-----|-----|")
    for i, n in enumerate(top10[:10]):
        lines.append(f"| {i+1} | {n['title'][:30]} | {n['type']} | {n['likes_raw']} | {n['collects_raw']} | {n['comments_raw']} |")

    lines.append(f"\n## 四、TOP10 逐条拆解")
    for i, n in enumerate(top10[:10]):
        lines.append(f"\n### {i+1}. {n['title']}")
        lines.append(f"- **类型**: {n['type']} | **赞**: {n['likes_raw']} | **藏**: {n['collects_raw']} | **评**: {n['comments_raw']}")
        if n.get("tags"):
            lines.append(f"- **标签**: {', '.join('#'+t for t in n['tags'][:5])}")
        lines.append(f"- **内容摘要**: {(n.get('desc', '') or '')[:150]}...")

        # 确定性分析：标题模式
        title = n.get("title", "")
        title_traits = []
        if re.search(r"\d+", title):
            title_traits.append("数字吸引")
        if re.search(r"[？?]|怎么|如何", title):
            title_traits.append("疑问引发好奇")
        if re.search(r"[！!]|绝了|太|真的", title):
            title_traits.append("情绪化表达")
        if re.search(r"教程|手把手|保姆级", title):
            title_traits.append("实用价值承诺")
        if title_traits:
            lines.append(f"- **标题策略**: {' + '.join(title_traits)}")

        if n.get("comment_list"):
            lines.append(f"- **热评洞察**:")
            for c in n["comment_list"][:3]:
                prefix = "[作者] " if c.get("is_author") else ""
                lines.append(f"  - {prefix}{c['user']}: {c['content'][:60]}")

    lines.append(f"\n## 五、标题模式分析")
    if title_patterns:
        lines.append(f"\n| 标题模式 | 使用次数 | 占比 | 示例 |")
        lines.append(f"|----------|---------|------|------|")
        for pattern_name, data in sorted(title_patterns.items(), key=lambda x: x[1]["count"], reverse=True):
            example = data["examples"][0][:20] if data["examples"] else ""
            lines.append(f"| {pattern_name} | {data['count']} | {data['pct']}% | {example} |")
        
        top_pattern = max(title_patterns.items(), key=lambda x: x[1]["count"])
        lines.append(f"\n**核心发现**：该博主最常用的标题策略是「{top_pattern[0]}」（{top_pattern[1]['pct']}%的笔记使用），这是可以直接借鉴的写作范式。")

    lines.append(f"\n## 六、核心标签")
    lines.append(f"\n| 标签 | 出现次数 |")
    lines.append(f"|------|---------|")
    for tag, count in tag_freq[:15]:
        lines.append(f"| #{tag} | {count} |")

    if comparison:
        lines.append(f"\n## 七、与自己账号对比")
        ss = comparison["self_stats"]
        ts = comparison["target_stats"]
        lines.append(f"\n| 指标 | 自己 | 对标博主 | 差距 |")
        lines.append(f"|------|------|---------|------|")
        for key, label in [("total", "笔记数"), ("avg_likes", "均赞"), ("avg_collects", "均收藏")]:
            diff = ts[key] - ss[key]
            lines.append(f"| {label} | {ss[key]:,} | {ts[key]:,} | {diff:+,} |")

    return "\n".join(lines)


def gen_enhanced_content_formula(nickname, top10, category_stats, title_patterns,
                                  emoji_info, cta_info, structure_info):
    """增强版内容公式总结"""
    lines = [
        f"# {nickname} — 内容公式总结",
        f"\n> 从全量笔记中提取的可复用内容公式",
        f"\n## 一、标题公式",
    ]

    # 确定性分析：标题模式统计
    if title_patterns:
        lines.append(f"\n该博主的标题策略统计：\n")
        for pattern_name, data in sorted(title_patterns.items(), key=lambda x: x[1]["count"], reverse=True):
            lines.append(f"### {pattern_name}标题（{data['count']}条，占{data['pct']}%）")
            lines.append(f"\n示例：")
            for ex in data["examples"][:3]:
                lines.append(f"- 「{ex}」")
            lines.append("")

    lines.append(f"\n**TOP10 高赞标题一览**：\n")
    for i, n in enumerate(top10[:10]):
        lines.append(f"{i+1}. 「{n['title']}」（{n['likes_raw']}赞）")

    # 内容结构分析
    lines.append(f"\n## 二、内容结构模板")
    if structure_info:
        lines.append(f"\n| 指标 | 数据 |")
        lines.append(f"|------|------|")
        lines.append(f"| 平均正文长度 | {structure_info['avg_length']}字 |")
        lines.append(f"| 短文（<200字） | {structure_info['short_count']}条 |")
        lines.append(f"| 中文（200-500字） | {structure_info['medium_count']}条 |")
        lines.append(f"| 长文（>500字） | {structure_info['long_count']}条 |")
        lines.append(f"| 使用列表格式 | {structure_info['has_list_count']}条 |")
        lines.append(f"| 使用数字小标题 | {structure_info['has_number_heading']}条 |")

        # 判断主要结构类型
        total = structure_info['short_count'] + structure_info['medium_count'] + structure_info['long_count']
        if total > 0:
            if structure_info['short_count'] / total > 0.5:
                lines.append(f"\n**结构偏好**：以短文为主，风格简洁直接。适合快速消费的轻量内容。")
            elif structure_info['long_count'] / total > 0.5:
                lines.append(f"\n**结构偏好**：以长文为主，内容详实深入。适合教程、攻略、深度分享类内容。")
            else:
                lines.append(f"\n**结构偏好**：长短结合，不拘一格。")

    # CTA 分析
    lines.append(f"\n## 三、CTA（行动号召）公式")
    if cta_info:
        lines.append(f"\n| CTA类型 | 使用次数 | 使用率 |")
        lines.append(f"|---------|---------|--------|")
        for cta_type, data in sorted(cta_info.items(), key=lambda x: x[1]["count"], reverse=True):
            lines.append(f"| {cta_type} | {data['count']} | {data['pct']}% |")

        if cta_info:
            top_cta = max(cta_info.items(), key=lambda x: x[1]["count"])
            lines.append(f"\n**CTA策略**：最常用的引导方式是「{top_cta[0]}」（{top_cta[1]['pct']}%的笔记使用）。")
    else:
        lines.append(f"\n该博主较少使用显式 CTA 引导，属于**内容驱动互动型**——靠内容质量自然吸引互动。")

    # Emoji / 视觉
    lines.append(f"\n## 四、视觉 / 排版公式")
    if emoji_info:
        lines.append(f"\n| 指标 | 数据 |")
        lines.append(f"|------|------|")
        lines.append(f"| Emoji使用率 | {emoji_info['emoji_usage_pct']}%（{emoji_info['notes_with_emoji']}/{emoji_info['total_notes']}条） |")
        if emoji_info['top_emojis']:
            top_e = " ".join(f"{e[0]}({e[1]})" for e in emoji_info['top_emojis'][:5])
            lines.append(f"| 高频Emoji | {top_e} |")

        if emoji_info['emoji_usage_pct'] > 70:
            lines.append(f"\n**视觉风格**：重度 Emoji 使用者，用表情符号增强可读性和情感表达。建议借鉴其 Emoji 排布节奏。")
        elif emoji_info['emoji_usage_pct'] > 30:
            lines.append(f"\n**视觉风格**：适度使用 Emoji，在关键节点点缀。")
        else:
            lines.append(f"\n**视觉风格**：较少使用 Emoji，偏文字驱动风格。")

    # 各领域公式
    lines.append(f"\n## 五、各领域最佳公式")
    for cat, cs in category_stats.items():
        lines.append(f"\n### {cat}（{cs['count']}条，均赞{cs['avg_likes']:,}）")
        lines.append(f"- 代表作：{cs['top_note'][:30]}")

    return "\n".join(lines)


def gen_enhanced_topic_library(nickname, top10, category_stats, tag_freq, notes=None):
    """增强版选题素材库"""
    lines = [
        f"# {nickname} — 选题素材库",
        f"\n> 基于 {nickname} 全量笔记提炼的可借鉴选题",
        f"\n## 一、已验证的爆款选题",
        f"\n| # | 选题 | 赞数 | 领域 |",
        f"|---|------|------|------|",
    ]
    for i, n in enumerate(top10[:10]):
        lines.append(f"| {i+1} | {n['title'][:30]} | {n['likes_raw']} | {n.get('category', '其他')} |")

    # 各领域选题
    lines.append(f"\n## 二、各领域选题库")
    for cat, cs in category_stats.items():
        lines.append(f"\n### {cat}（{cs['count']}条，均赞{cs['avg_likes']:,}）")
        lines.append(f"- 代表作：{cs['top_note'][:30]}")
        # 找该类别的所有笔记标题
        if notes:
            cat_notes = [n for n in notes if n.get("category") == cat]
            cat_notes.sort(key=lambda x: x.get("likes", 0), reverse=True)
            for cn in cat_notes[:5]:
                lines.append(f"- 「{cn['title'][:35]}」（{cn.get('likes_raw', '?')}赞）")

    # 标签热度矩阵
    lines.append(f"\n## 三、热门标签参考")
    lines.append(f"\n| 标签 | 使用次数 |")
    lines.append(f"|------|---------| ")
    for tag, count in tag_freq[:15]:
        lines.append(f"| #{tag} | {count} |")

    # 差异化分析（基于确定性数据）
    lines.append(f"\n## 四、差异化赛道分析")
    if category_stats:
        # 找出"低竞争高回报"领域
        sorted_cats = sorted(category_stats.items(), key=lambda x: x[1]["avg_likes"], reverse=True)
        for cat, cs in sorted_cats:
            if cs["count"] <= 3 and cs["avg_likes"] > (sum(c["avg_likes"] for c in category_stats.values()) / len(category_stats)):
                lines.append(f"\n- ⭐ **「{cat}」是潜力赛道**：仅{cs['count']}条但均赞{cs['avg_likes']:,}，超过整体均值，说明受众需求旺盛但供给不足。")

    lines.append(f"\n## 五、选题优先级参考")
    lines.append(f"\n基于数据的选题优先级评估：\n")
    lines.append(f"| 优先级 | 领域 | 理由 |")
    lines.append(f"|--------|------|------|")
    if category_stats:
        sorted_by_roi = sorted(category_stats.items(), key=lambda x: x[1]["avg_likes"], reverse=True)
        for i, (cat, cs) in enumerate(sorted_by_roi[:5]):
            priority = "🔴 高" if i < 2 else ("🟡 中" if i < 4 else "🟢 低")
            lines.append(f"| {priority} | {cat} | 均赞{cs['avg_likes']:,}，{cs['count']}条内容 |")

    return "\n".join(lines)


def gen_enhanced_structured_analysis(nickname, stats, notes, category_stats, tag_freq,
                                      frequency_info, growth_info):
    """增强版全量笔记结构化分析"""
    lines = [
        f"# {nickname} — 全量笔记结构化分析",
        f"\n> {stats['total']}条笔记的完整数据视角",
        f"\n## 一、数据总览",
        f"\n| 指标 | 数值 |",
        f"|------|------|",
        f"| 总笔记 | {stats['total']} |",
        f"| 视频 | {stats['video_count']} ({round(stats['video_count']/stats['total']*100) if stats['total'] else 0}%) |",
        f"| 图文 | {stats['normal_count']} ({round(stats['normal_count']/stats['total']*100) if stats['total'] else 0}%) |",
        f"| 总赞 | {stats['total_likes']:,} |",
        f"| 总收藏 | {stats['total_collects']:,} |",
        f"| 总评论 | {stats['total_comments']:,} |",
    ]

    # 发布频率
    if frequency_info and frequency_info.get("pattern") != "数据不足":
        lines.append(f"\n**发布频率**：{frequency_info['pattern']}（平均{frequency_info['avg_days_between']}天/条）")

    lines.append(f"\n## 二、内容领域分布")
    lines.append(f"\n| 领域 | 数量 | 占比 | 均赞 |")
    lines.append(f"|------|------|------|------|")
    for cat, cs in category_stats.items():
        lines.append(f"| {cat} | {cs['count']} | {cs['pct']}% | {cs['avg_likes']:,} |")

    # 全量笔记列表
    lines.append(f"\n## 三、全量笔记列表")
    lines.append(f"\n| # | 标题 | 类型 | 赞 | 藏 | 评 | 领域 |")
    lines.append(f"|---|------|------|-----|-----|-----|------|")
    for i, n in enumerate(notes[:100]):
        lines.append(
            f"| {i+1} | {n['title'][:25]} | {n.get('type', 'normal')} | "
            f"{n.get('likes_raw', '?')} | {n.get('collects_raw', '?')} | {n.get('comments_raw', '?')} | {n.get('category', '其他')} |"
        )

    # 发展趋势
    lines.append(f"\n## 四、发展趋势分析")
    if growth_info:
        lines.append(f"\n将{stats['total']}条笔记按时间分为前半（{growth_info['early_count']}条）和后半（{growth_info['recent_count']}条）：\n")
        lines.append(f"| 领域 | 早期占比 | 近期占比 | 变化 |")
        lines.append(f"|------|---------|---------|------|")
        for cat, change in sorted(growth_info["category_shifts"].items(), key=lambda x: abs(x[1]["delta"]), reverse=True):
            arrow = "📈" if change["delta"] > 5 else ("📉" if change["delta"] < -5 else "➡️")
            lines.append(f"| {cat} | {change['early_pct']}% | {change['recent_pct']}% | {arrow} {change['delta']:+.1f}% |")

        # 找显著变化
        growing = [c for c, d in growth_info["category_shifts"].items() if d["delta"] > 10]
        declining = [c for c, d in growth_info["category_shifts"].items() if d["delta"] < -10]
        if growing:
            lines.append(f"\n**内容转型趋势**：近期「{'、'.join(growing)}」占比明显增加，说明博主正在向这个方向转型。")
        if declining:
            lines.append(f"\n**内容收缩方向**：「{'、'.join(declining)}」占比下降，博主可能在这些领域遇到了瓶颈或主动收缩。")
    else:
        lines.append(f"\n笔记数量不足或缺少时间数据，无法分析发展趋势。")

    # 爆款分析
    lines.append(f"\n## 五、爆款规律总结")
    if notes:
        avg_likes = stats["avg_likes"]
        hits = [n for n in notes if n.get("likes", 0) > avg_likes * 3]
        lines.append(f"\n定义爆款：赞数超过均值3倍（>{avg_likes * 3:,}赞）的笔记。\n")
        lines.append(f"- **爆款数量**：{len(hits)}条（占总数{round(len(hits)/len(notes)*100, 1) if notes else 0}%）")
        if hits:
            hit_cats = Counter(n.get("category", "其他") for n in hits)
            top_hit_cat = hit_cats.most_common(1)[0] if hit_cats else ("其他", 0)
            lines.append(f"- **爆款集中领域**：「{top_hit_cat[0]}」（{top_hit_cat[1]}条爆款）")
            hit_types = Counter(n.get("type", "normal") for n in hits)
            lines.append(f"- **爆款形式**：{', '.join(f'{t}({c}条)' for t, c in hit_types.most_common())}")

    return "\n".join(lines)


# ----------------------------------------------------------
# AI Prompt 生成
# ----------------------------------------------------------

def gen_ai_prompt(nickname, analysis_data, notes_details=None):
    """生成 AI 深度分析 Prompt 文件"""
    stats = analysis_data["stats"]
    top10 = analysis_data["top10"]

    lines = [
        f"# AI 深度分析任务 — {nickname}",
        f"\n> 本文件由 deep_analyze.py 自动生成，供宿主 AI（WorkBuddy / Claude Code）参考",
        f"> 脚本已完成确定性分析（数据统计、模式匹配），以下是需要 AI 推理能力补充的部分",
        f"\n---",
        f"\n## 📋 AI 需要补充的内容",
        f"\n### 1. 博主深度拆解 — TOP10 逐条深度拆解",
        f"\n请基于以下 TOP10 笔记数据，为每条笔记写 2-3 句话的深度拆解，分析：",
        f"- 这条笔记为什么能成为爆款？",
        f"- 标题/内容/评论区有什么可复制的技巧？",
        f"- 对我（初期创作者）有什么可借鉴的？",
        f"\nTOP10 数据：\n",
    ]

    for i, n in enumerate(top10[:10]):
        lines.append(f"**{i+1}. {n['title']}**")
        lines.append(f"- 赞:{n['likes_raw']} 藏:{n['collects_raw']} 评:{n['comments_raw']} 类型:{n['type']}")
        desc = (n.get("desc", "") or "")[:200]
        if desc:
            lines.append(f"- 正文摘要: {desc}...")
        if n.get("comment_list"):
            lines.append(f"- 热评: {'; '.join(c['content'][:40] for c in n['comment_list'][:3])}")
        lines.append("")

    lines.extend([
        f"\n### 2. 内容公式总结 — 具体公式提炼",
        f"\n请基于标题模式和内容数据，提炼出 3-5 个具体的**可直接套用的标题公式**。",
        f'格式示例：「数字 + 痛点 + 解决方案」→ "3个方法让你XX"',
        f"\n### 3. 选题素材库 — 改编方向",
        f"\n请为 TOP10 每条选题提供一个**针对初期创作者**的改编方向。",
        f"重点考虑：创作难度低、不需要大量粉丝基础、能展示真实体验。",
        f"\n### 4. 全量结构化分析 — 竞争格局与机会",
        f"\n请基于该博主的内容领域分布，分析：",
        f"- 这个赛道的竞争态势",
        f"- 新人切入的机会点",
        f"- 建议的差异化方向",
    ])

    return "\n".join(lines)


# ----------------------------------------------------------
# 主函数
# ----------------------------------------------------------

def deep_analyze(analysis_path, nickname, output_dir, notes_details_path=None):
    """
    执行确定性深度分析，生成增强版文档 + AI Prompt。
    
    Returns:
        dict — { "docs": [...], "prompt_path": str }
    """
    os.makedirs(output_dir, exist_ok=True)

    with open(analysis_path, "r", encoding="utf-8") as f:
        analysis = json.load(f)

    stats = analysis["stats"]
    top10 = analysis["top10"]
    category_stats = analysis["category_stats"]
    tag_freq = analysis["tag_freq"]
    comparison = analysis.get("comparison")
    notes = analysis.get("notes", [])

    # 加载原始详情（如有）用于更深入分析
    full_notes = None
    if notes_details_path and os.path.exists(notes_details_path):
        with open(notes_details_path, "r", encoding="utf-8") as f:
            raw_details = json.load(f)
        full_notes = []
        for item in raw_details:
            if "_error" in item:
                continue
            note = item.get("data", {}).get("note", item)
            full_notes.append(note)

        # === 数据校验（自动运行）===
        valid_details = [d for d in raw_details if "_error" not in d]
        v1_ok, v1_msg = check_content_completeness(valid_details)
        print(v1_msg)
        if not v1_ok:
            print("\n🚨 正文数据不完整（< 80%），拒绝生成分析报告。")
            print("   请先补爬正文数据：重新运行 crawl_blogger.py。")
            sys.exit(1)

    # ---- 确定性分析 ----
    titles = [n["title"] for n in (notes or top10) if n.get("title")]
    descs = []
    if full_notes:
        descs = [n.get("desc", "") for n in full_notes]
    elif top10:
        descs = [n.get("desc", "") for n in top10]

    title_patterns = extract_title_patterns(titles) if titles else {}
    emoji_info = extract_emoji_patterns(descs) if descs else {}
    cta_info = extract_cta_patterns(descs) if descs else {}
    structure_info = analyze_content_structure(descs) if descs else {}
    frequency_info = detect_posting_frequency(notes) if notes else {}
    growth_info = find_growth_pattern(notes) if notes else None

    safe_name = safe_filename(nickname)

    # ---- 生成增强版文档 ----
    docs = {
        "博主深度拆解": gen_enhanced_deep_analysis(
            nickname, stats, top10, category_stats, tag_freq,
            title_patterns, comparison, notes
        ),
        "内容公式总结": gen_enhanced_content_formula(
            nickname, top10, category_stats, title_patterns,
            emoji_info, cta_info, structure_info
        ),
        "选题素材库": gen_enhanced_topic_library(
            nickname, top10, category_stats, tag_freq, notes
        ),
        "全量笔记结构化分析": gen_enhanced_structured_analysis(
            nickname, stats, notes or top10, category_stats, tag_freq,
            frequency_info, growth_info
        ),
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

    # ---- 生成 AI Prompt ----
    prompt_content = gen_ai_prompt(nickname, analysis)
    prompt_path = os.path.join(process_dir, f"{safe_name}_AI深度分析Prompt.md")
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(prompt_content)
    print(f"  📋 AI Prompt: {prompt_path}")

    # === V6 产出文件数校验（自动运行）===
    expected_files = [r["name"] for r in results]
    v6_ok, v6_msg = check_output_files(output_dir, expected_files)
    print(v6_msg)
    if not v6_ok:
        print("\n🚨 产出文件不完整，请检查上方错误信息并重试。")
        sys.exit(1)

    return {"docs": results, "prompt_path": prompt_path}


# ----------------------------------------------------------
# CLI
# ----------------------------------------------------------
if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Phase 3.5: AI 深度分析（增强版文档生成）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python deep_analyze.py ./data/analysis.json "蔡不菜"
  python deep_analyze.py ./data/analysis.json "蔡不菜" -o ./output
  python deep_analyze.py ./data/analysis.json "蔡不菜" -o ./output --details ./data/notes_details.json
        """,
    )
    parser.add_argument("analysis_path", help="分析数据JSON路径（Phase 2 输出）")
    parser.add_argument("nickname", help="博主昵称")
    parser.add_argument("-o", "--output", default=".", help="输出目录")
    parser.add_argument("--details", help="原始详情JSON路径（可选，提供更深入分析）")
    args = parser.parse_args()

    print(f"\n🔍 Phase 3.5: AI 深度分析 — {args.nickname}")
    print("=" * 50)
    print("  执行确定性分析（标题模式/CTA/Emoji/发布频率/发展趋势）...")
    print("  生成增强版文档（用数据洞察替换占位符）...")
    print()

    result = deep_analyze(args.analysis_path, args.nickname, args.output, args.details)

    ok = sum(1 for r in result["docs"] if r["ok"])
    print(f"\n完成: {ok}/{len(result['docs'])} 份增强版文档生成成功")
    print(f"\n💡 提示: 查看 {result['prompt_path']} 获取 AI 可补充的深度分析任务")
