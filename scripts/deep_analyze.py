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
                # v2.0 方案A：作者已回填真实昵称，无需再加 [作者] prefix
                lines.append(f"  - {c['user']}: {c['content'][:60]}")

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
# 数据底稿生成
# ----------------------------------------------------------

def gen_data_draft(nickname, stats, top10, category_stats, tag_freq,
                   title_patterns, emoji_info, cta_info, structure_info,
                   frequency_info, growth_info, notes,
                   opinion_candidates, opinion_mode, writing_structure, value_words,
                   full_notes=None):
    """生成数据底稿.md：所有统计数据 + 认知层原材料，供 AI蒸馏任务.md 使用"""
    lines = [
        f"# {nickname} — 数据底稿",
        f"\n> 由 deep_analyze.py 自动生成 | {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"> ⚠️ 本文件是 AI 蒸馏任务的原材料，不直接给用户看",
        f"\n---",
        f"\n## 基础统计",
        f"\n| 指标 | 数值 |",
        f"|------|------|",
        f"| 笔记总数 | {stats['total']}条 |",
        f"| 视频/图文 | {stats['video_count']}视频 / {stats['normal_count']}图文 |",
        f"| 均赞 | {stats['avg_likes']:,} |",
        f"| 均收藏 | {stats['avg_collects']:,} |",
        f"| 均评论 | {stats['avg_comments']:,} |",
        f"| 总赞 | {stats['total_likes']:,} |",
        f"| 总收藏 | {stats['total_collects']:,} |",
        f"| 总评论 | {stats['total_comments']:,} |",
    ]

    # 发布频率
    if frequency_info and frequency_info.get("avg_days_between"):
        lines.append(f"| 发布频率 | {frequency_info['pattern']}（均{frequency_info['avg_days_between']}天/条） |")

    # 爆款率
    if notes:
        avg = stats["avg_likes"]
        hit_count = sum(1 for n in notes if n.get("likes", 0) > avg * 3)
        super_hit = sum(1 for n in notes if n.get("likes", 0) > avg * 10)
        lines.append(f"| 爆款率（>均赞×3） | {hit_count}条（{round(hit_count/stats['total']*100, 1) if stats['total'] else 0}%） |")
        lines.append(f"| 超级爆款率（>均赞×10） | {super_hit}条 |")

    # 藏赞比
    if stats["total_likes"] > 0:
        ratio = round(stats["total_collects"] / stats["total_likes"] * 100, 1)
        lines.append(f"| 藏赞比 | {ratio}% |")

    lines.append(f"\n## 内容领域分布")
    lines.append(f"\n| 领域 | 数量 | 占比 | 均赞 | 代表作 |")
    lines.append(f"|------|------|------|------|--------|")
    for cat, cs in category_stats.items():
        lines.append(f"| {cat} | {cs['count']} | {cs['pct']}% | {cs['avg_likes']:,} | {cs['top_note'][:25]} |")

    lines.append(f"\n## 标签 TOP20")
    lines.append(f"\n| 标签 | 出现次数 |")
    lines.append(f"|------|---------|")
    for tag, count in tag_freq[:20]:
        lines.append(f"| #{tag} | {count} |")

    lines.append(f"\n## 标题模式")
    if title_patterns:
        lines.append(f"\n| 模式 | 条数 | 占比 | 示例 |")
        lines.append(f"|------|------|------|------|")
        for pname, data in sorted(title_patterns.items(), key=lambda x: x[1]["count"], reverse=True):
            ex = data["examples"][0][:20] if data["examples"] else ""
            lines.append(f"| {pname} | {data['count']} | {data['pct']}% | {ex} |")

    lines.append(f"\n## Emoji 使用")
    if emoji_info:
        lines.append(f"\n- 使用率：{emoji_info['emoji_usage_pct']}%（{emoji_info['notes_with_emoji']}/{emoji_info['total_notes']}条）")
        if emoji_info["top_emojis"]:
            top_e = " ".join(f"{e[0]}({e[1]})" for e in emoji_info["top_emojis"][:10])
            lines.append(f"- 高频 Emoji TOP10：{top_e}")

    lines.append(f"\n## CTA 使用")
    if cta_info:
        lines.append(f"\n| CTA 类型 | 条数 | 使用率 |")
        lines.append(f"|---------|------|--------|")
        for cta_type, data in sorted(cta_info.items(), key=lambda x: x[1]["count"], reverse=True):
            lines.append(f"| {cta_type} | {data['count']} | {data['pct']}% |")
    else:
        lines.append(f"\n较少使用显式 CTA。")

    lines.append(f"\n## 正文结构统计")
    if structure_info:
        lines.append(f"\n| 指标 | 数值 |")
        lines.append(f"|------|------|")
        lines.append(f"| 平均正文长度 | {structure_info['avg_length']}字 |")
        lines.append(f"| 短文（<200字） | {structure_info['short_count']}条 |")
        lines.append(f"| 中文（200-500字） | {structure_info['medium_count']}条 |")
        lines.append(f"| 长文（>500字） | {structure_info['long_count']}条 |")
        lines.append(f"| 使用列表格式 | {structure_info['has_list_count']}条 |")
        lines.append(f"| 使用数字小标题 | {structure_info['has_number_heading']}条 |")

    lines.append(f"\n## 发展趋势")
    if growth_info:
        lines.append(f"\n前半（{growth_info['early_count']}条）vs 后半（{growth_info['recent_count']}条）：\n")
        lines.append(f"| 领域 | 早期占比 | 近期占比 | 变化 |")
        lines.append(f"|------|---------|---------|------|")
        for cat, change in sorted(growth_info["category_shifts"].items(), key=lambda x: abs(x[1]["delta"]), reverse=True):
            arrow = "📈" if change["delta"] > 5 else ("📉" if change["delta"] < -5 else "➡️")
            lines.append(f"| {cat} | {change['early_pct']}% | {change['recent_pct']}% | {arrow} {change['delta']:+.1f}% |")
    else:
        lines.append(f"\n数据不足，无法分析趋势。")

    # 建立完整正文查找表（按笔记ID）
    full_text_map = {}
    if full_notes:
        for fn in full_notes:
            nid = fn.get("noteId", fn.get("_feed_id", ""))
            if nid and fn.get("desc"):
                full_text_map[nid] = fn["desc"]

    lines.append(f"\n## TOP10 数据包")
    for i, n in enumerate(top10[:10]):
        lines.append(f"\n### TOP{i+1}：{n['title']}")
        lines.append(f"赞 {n['likes_raw']} | 藏 {n['collects_raw']} | 评 {n['comments_raw']} | 类型 {n['type']}")
        if n.get("tags"):
            lines.append(f"标签：{' '.join('#'+t for t in n['tags'][:8])}")
        full_desc = full_text_map.get(n.get("id", ""))
        if full_desc:
            lines.append(f"\n完整正文：\n{full_desc}")
        else:
            desc = (n.get("desc", "") or "")[:200]
            if desc:
                lines.append(f"\n正文前200字：\n{desc}...")
        if n.get("comment_list"):
            lines.append(f"\n热评：")
            for c in n["comment_list"][:5]:
                # v2.0 方案A：作者已回填真实昵称，无需再加 [作者] prefix
                lines.append(f"- {c['user']}：{c['content'][:80]}")

    # ---- 认知层数据（新增）----
    lines.append(f"\n---")
    lines.append(f"\n## 认知层数据")

    lines.append(f"\n### 观点句候选（{opinion_mode}，共{len(opinion_candidates)}条）")
    lines.append(f"\n⚠️ 这是 AI 提炼认知层的核心原材料，全量内联，不截断\n")
    if opinion_candidates:
        lines.append(f"| # | 观点句 | 来源笔记 | 匹配类型 |")
        lines.append(f"|---|--------|---------|---------|")
        for i, c in enumerate(opinion_candidates):
            sentence = c["sentence"].replace("|", "｜")
            title = c["source_title"].replace("|", "｜")
            lines.append(f"| {i+1} | {sentence} | 《{title}》({c['source_likes']}赞) | {c['match_type']} |")
    else:
        lines.append(f"⚠️ 未提取到观点句候选，AI 需从全量正文自行提取。")

    lines.append(f"\n### 写作结构统计（供内容层使用，非认知层）")
    opening = writing_structure.get("opening_types", {})
    ending = writing_structure.get("ending_types", {})
    if opening:
        opening_str = " / ".join(f"{k}{v}条" for k, v in sorted(opening.items(), key=lambda x: x[1], reverse=True))
        lines.append(f"\n- 开头类型：{opening_str}")
    if ending:
        ending_str = " / ".join(f"{k}{v}条" for k, v in sorted(ending.items(), key=lambda x: x[1], reverse=True))
        lines.append(f"- 结尾类型：{ending_str}")
    if structure_info:
        total_notes = structure_info['short_count'] + structure_info['medium_count'] + structure_info['long_count']
        list_pct = round(structure_info['has_list_count'] / total_notes * 100, 1) if total_notes else 0
        heading_pct = round(structure_info['has_number_heading'] / total_notes * 100, 1) if total_notes else 0
        lines.append(f"- 平均正文长度：{structure_info['avg_length']}字 | 列表使用率：{list_pct}% | 小标题使用率：{heading_pct}%")

    lines.append(f"\n### 高频词 TOP15（方案B，从正文提取，未经筛选）")
    lines.append(f"\n⚠️ 含通用词，AI 在认知层分析时需自行筛选有价值立场含义的词\n")
    if value_words:
        lines.append(f"| 词 | 出现次数 |")
        lines.append(f"|---|---------|")
        for vw in value_words:
            lines.append(f"| {vw['word']} | {vw['count']} |")
    else:
        lines.append(f"未提取到高频词。")

    return "\n".join(lines)


# ----------------------------------------------------------
# AI 蒸馏任务生成（E2）
# ----------------------------------------------------------

def gen_distill_task(nickname, stats, top10, category_stats, tag_freq,
                     title_patterns, emoji_info, cta_info, structure_info,
                     frequency_info, growth_info, notes,
                     opinion_candidates, opinion_mode, writing_structure, value_words,
                     full_notes=None, mode="A"):
    """
    生成 AI蒸馏任务.md：自包含文件，内联所有数据原材料 + HTML/Skill 精细规格。
    AI 只读本文件即可完成蒸馏，不需要打开其他文件。

    mode="A"：学习博主，skill 文件夹 {博主名}_创作指南.skill/
    mode="B"：认识自己，skill 文件夹 {博主名}_创作基因.skill/
    mode="C"：v2.1 预留，暂不实现
    """
    if mode == "C":
        raise NotImplementedError("模式C v2.1实现")

    # ---- mode 配置 ----
    if mode == "B":
        skill_dirname = f"{nickname}_创作基因.skill"
        skill_name_field = f"{nickname}-创作基因"
        skill_desc_type = "创作基因"
        belief_header = "你的思考模式"
        strategy_header = "你的运营习惯"
        content_header = "你的写作基因"
        contrast_header = f"对比示例 — 你的风格 vs 优化后风格"
        topic_header = "你还没试过的方向"
        forbidden_header = "你通常不做的（但可以考虑打破）"
        run_rule_text = f"保持你的风格，在薄弱处补强"
        skill_desc_extra = "AI 会用你的内容基因构思选题、用你的风格写作、在你的薄弱处补强"
        role_text = f"当你用这个 skill 写内容时，你是在延续自己的风格基因"
    else:  # mode="A"（默认）
        skill_dirname = f"{nickname}_创作指南.skill"
        skill_name_field = f"{nickname}-创作指南"
        skill_desc_type = "创作指南"
        belief_header = "像 TA 一样思考"
        strategy_header = "像 TA 一样决策"
        content_header = "像 TA 一样写"
        contrast_header = f"对比示例 — 普通风格 vs {nickname}风格"
        topic_header = "选题灵感池"
        forbidden_header = f"创作禁区 — TA 绝不会做的事（反模式）"
        run_rule_text = f"用{nickname}验证过的方法论创作内容"
        skill_desc_extra = f"AI 会用 TA 的思维方式构思选题、用 TA 的内容编排方式写作、用 TA 的运营策略规划发布"
        role_text = f"当你用这个 skill 写内容时，你是一个像{nickname}一样的创作者"

    skill_entry_file = "SKILL.md"
    skill_entry_path = f"{skill_dirname}/{skill_entry_file}"

    total_notes = stats["total"]
    today = datetime.now().strftime("%Y-%m-%d")

    lines = []

    # ================================================================
    # 文件头
    # ================================================================
    lines += [
        f"# AI 蒸馏任务 — {nickname}",
        f"",
        f"> 本文件由 deep_analyze.py 自动生成 | {today}",
        f"> 模式：{'A — 学习TA' if mode == 'A' else 'B — 认识自己'}",
        f"> ⚠️ 本文件自包含：数据原材料 + 生成指令 + 精细规格全部内联。",
        f"> 你只需要读本文件，不需要打开任何其他文件。",
        f"",
        f"---",
    ]

    # ================================================================
    # 第一部分：数据原材料
    # ================================================================
    lines += [
        f"",
        f"# 第一部分：数据原材料",
        f"",
        f"> 以下数据由脚本自动提取，全量内联，供 AI 在生成报告时直接引用。",
        f"",
        f"---",
        f"",
        f"## 基础统计",
        f"",
        f"| 指标 | 数值 |",
        f"|------|------|",
        f"| 博主昵称 | {nickname} |",
        f"| 总笔记数 | {stats['total']}条 |",
        f"| 视频/图文 | {stats['video_count']}视频 / {stats['normal_count']}图文 |",
        f"| 均赞 | {stats['avg_likes']:,} |",
        f"| 均收藏 | {stats['avg_collects']:,} |",
        f"| 均评论 | {stats['avg_comments']:,} |",
        f"| 总赞 | {stats['total_likes']:,} |",
        f"| 总收藏 | {stats['total_collects']:,} |",
        f"| 总评论 | {stats['total_comments']:,} |",
    ]

    if frequency_info and frequency_info.get("avg_days_between"):
        lines.append(f"| 发布频率 | {frequency_info['pattern']}（均{frequency_info['avg_days_between']}天/条） |")

    if notes and stats["total"] > 0:
        avg = stats["avg_likes"]
        hit_count = sum(1 for n in notes if n.get("likes", 0) > avg * 3)
        super_hit = sum(1 for n in notes if n.get("likes", 0) > avg * 10)
        lines.append(f"| 爆款率（>均赞×3） | {hit_count}条（{round(hit_count/stats['total']*100, 1)}%） |")
        lines.append(f"| 超级爆款率（>均赞×10） | {super_hit}条 |")

    if stats["total_likes"] > 0:
        ratio = round(stats["total_collects"] / stats["total_likes"] * 100, 1)
        lines.append(f"| 藏赞比 | {ratio}% |")

    # ---- 内容领域分布 ----
    lines += [
        f"",
        f"## 内容领域分布",
        f"",
        f"| 领域 | 数量 | 占比 | 均赞 | 代表作 |",
        f"|------|------|------|------|--------|",
    ]
    for cat, cs in category_stats.items():
        lines.append(f"| {cat} | {cs['count']} | {cs['pct']}% | {cs['avg_likes']:,} | {cs['top_note'][:25]} |")

    # ---- 标签 TOP20 ----
    lines += [
        f"",
        f"## 标签 TOP20",
        f"",
        f"| 标签 | 出现次数 |",
        f"|------|---------|",
    ]
    for tag, count in tag_freq[:20]:
        lines.append(f"| #{tag} | {count} |")

    # ---- 标题模式 ----
    lines += [f"", f"## 标题模式统计", f""]
    if title_patterns:
        lines += [f"| 模式 | 条数 | 占比 | 示例 |", f"|------|------|------|------|"]
        for pname, data in sorted(title_patterns.items(), key=lambda x: x[1]["count"], reverse=True):
            ex = data["examples"][0][:20] if data["examples"] else ""
            lines.append(f"| {pname} | {data['count']} | {data['pct']}% | {ex} |")
    else:
        lines.append("（无标题模式数据）")

    # ---- CTA ----
    lines += [f"", f"## CTA 使用统计", f""]
    if cta_info:
        lines += [f"| CTA 类型 | 条数 | 使用率 |", f"|---------|------|--------|"]
        for cta_type, data in sorted(cta_info.items(), key=lambda x: x[1]["count"], reverse=True):
            lines.append(f"| {cta_type} | {data['count']} | {data['pct']}% |")
    else:
        lines.append("较少使用显式 CTA。")

    # ---- Emoji ----
    lines += [f"", f"## Emoji 使用统计", f""]
    if emoji_info:
        lines.append(f"- 使用率：{emoji_info['emoji_usage_pct']}%（{emoji_info['notes_with_emoji']}/{emoji_info['total_notes']}条）")
        if emoji_info["top_emojis"]:
            top_e = " ".join(f"{e[0]}({e[1]})" for e in emoji_info["top_emojis"][:10])
            lines.append(f"- 高频 Emoji TOP10：{top_e}")
    else:
        lines.append("（无 Emoji 数据）")

    # ---- 写作结构统计 ----
    lines += [f"", f"## 写作结构统计（供内容层使用）", f""]
    opening = writing_structure.get("opening_types", {})
    ending = writing_structure.get("ending_types", {})
    if opening:
        opening_str = " / ".join(f"{k}{v}条" for k, v in sorted(opening.items(), key=lambda x: x[1], reverse=True))
        lines.append(f"- 开头类型：{opening_str}")
    else:
        lines.append("- 开头类型：（无数据）")
    if ending:
        ending_str = " / ".join(f"{k}{v}条" for k, v in sorted(ending.items(), key=lambda x: x[1], reverse=True))
        lines.append(f"- 结尾类型：{ending_str}")
    else:
        lines.append("- 结尾类型：（无数据）")
    if structure_info:
        total_s = structure_info["short_count"] + structure_info["medium_count"] + structure_info["long_count"]
        list_pct = round(structure_info["has_list_count"] / total_s * 100, 1) if total_s else 0
        heading_pct = round(structure_info["has_number_heading"] / total_s * 100, 1) if total_s else 0
        lines.append(f"- 平均正文长度：{structure_info['avg_length']}字 | 列表使用率：{list_pct}% | 小标题使用率：{heading_pct}%")

    # ---- 观点句候选（全量，认知层核心原材料）----
    lines += [
        f"",
        f"## 观点句候选（{opinion_mode}，共{len(opinion_candidates)}条）",
        f"",
        f"⚠️ 这是 AI 提炼认知层的核心原材料，全量内联，不截断",
        f"",
    ]
    if opinion_candidates:
        lines += [f"| # | 观点句 | 来源笔记 | 匹配类型 |", f"|---|--------|---------|---------|"]
        for i, c in enumerate(opinion_candidates):
            sentence = c["sentence"].replace("|", "｜")
            title_str = c["source_title"].replace("|", "｜")
            lines.append(f"| {i+1} | {sentence} | 《{title_str}》({c.get('source_likes', '?')}赞) | {c['match_type']} |")
    else:
        lines.append(f"⚠️ 未提取到观点句候选（模式：{opinion_mode}），AI 需从 TOP10 正文自行提取。")

    # ---- 高频词 TOP15 ----
    lines += [
        f"",
        f"## 高频词 TOP15（从正文提取，未经筛选）",
        f"",
        f"⚠️ 含通用词，AI 在认知层分析时需自行筛选有价值立场含义的词",
        f"",
    ]
    if value_words:
        lines += [f"| 词 | 出现次数 |", f"|---|---------|"]
        for vw in value_words:
            lines.append(f"| {vw['word']} | {vw['count']} |")
    else:
        lines.append("未提取到高频词。")

    # ---- TOP10 数据包 ----
    full_text_map = {}
    if full_notes:
        for fn in full_notes:
            nid = fn.get("noteId", fn.get("_feed_id", ""))
            if nid and fn.get("desc"):
                full_text_map[nid] = fn["desc"]

    lines += [f"", f"## TOP10 数据包", f""]
    for i, n in enumerate(top10[:10]):
        lines.append(f"### TOP{i+1}：{n['title']}")
        lines.append(f"赞 {n['likes_raw']} | 藏 {n['collects_raw']} | 评 {n['comments_raw']} | 类型 {n['type']}")
        if n.get("tags"):
            lines.append(f"标签：{' '.join('#'+t for t in n['tags'][:8])}")
        full_desc = full_text_map.get(n.get("id", ""))
        if full_desc:
            lines.append(f"\n完整正文：\n{full_desc}")
        else:
            desc = (n.get("desc", "") or "")[:200]
            if desc:
                lines.append(f"\n正文前200字：\n{desc}...")
        if n.get("comment_list"):
            lines.append(f"\n热评：")
            for c in n["comment_list"][:5]:
                # v2.0 方案A：作者已回填真实昵称，无需再加 [作者] prefix
                lines.append(f"- {c['user']}：{c['content'][:80]}")
        lines.append("")

    # ---- 发展趋势 ----
    lines += [f"## 发展趋势", f""]
    if growth_info:
        lines.append(f"前半（{growth_info['early_count']}条）vs 后半（{growth_info['recent_count']}条）：\n")
        lines += [f"| 领域 | 早期占比 | 近期占比 | 变化 |", f"|------|---------|---------|------|"]
        for cat, change in sorted(growth_info["category_shifts"].items(), key=lambda x: abs(x[1]["delta"]), reverse=True):
            arrow = "📈" if change["delta"] > 5 else ("📉" if change["delta"] < -5 else "➡️")
            lines.append(f"| {cat} | {change['early_pct']}% | {change['recent_pct']}% | {arrow} {change['delta']:+.1f}% |")
    else:
        lines.append("数据不足，无法分析趋势。")

    lines += [f"", f"---"]

    # ================================================================
    # 第二部分：任务一 — HTML 蒸馏报告
    # ================================================================
    lines += [
        f"",
        f"# 第二部分：任务一 — 生成 HTML 蒸馏报告",
        f"",
        f"**输出文件名**：`{nickname}_蒸馏报告.html`",
        f"",
        f"---",
        f"",
        f"## 技术要求",
        f"",
        f"### 设计方向：博主档案系统 / Archive Terminal",
        f"",
        f'参考语言是"工业档案"——做一个"博主数据蒸馏终端"的感觉。',
        f"系统层（编号/标签/数字）用等宽字体，内容层（中文正文/标题）用衬线字体，形成双层次感。",
        f"",
        f"#### 技术栈",
        f"",
        f"- 单文件 HTML，所有 CSS 手写内联，**禁止使用 Tailwind CDN**",
        f"- 字体通过 Google Fonts CDN 引入（下方有具体 import）",
        f"- 三个动效全部用原生 JS 实现，无第三方库",
        f"- 折叠面板用 `<details><summary>` 原生 HTML",
        f"- 响应式，移动端断点 768px",
        f"",
        f"#### 色彩系统",
        f"",
        f"| 用途 | 色值 | 说明 |",
        f"|------|------|------|",
        f"| 页面底色 | `#CEC9C0` | 暖石色，整体背景 |",
        f"| 主强调色 | `#8A3926` | 砖红/赤陶，模块编号色、IF→Then 左边框、反转模块背景 |",
        f"| 正文色 | `#1A1211` | 近黑，所有正文和标题 |",
        f"| 辅助/元数据色 | `#7A6E65` | 暖棕灰，字段名、标签、次要文字、边框 |",
        f"| 反转块文字色 | `#FAF6F2` | 仅在砖红底色模块内使用 |",
        f"",
        f"**禁止使用**：`#FAF8F5` 米色 / 白色卡片背景 / 任何蓝紫色 / Inter/Roboto/Arial 等系统默认字体",
        f"",
        f"#### 字体规则",
        f"",
        f"```html",
        f'<link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Noto+Serif+SC:wght@400;500;700&display=swap" rel="stylesheet">',
        f"```",
        f"",
        f"- **Space Mono**：所有模块编号（01/02/...）、系统状态栏、KEY:VALUE 字段名、数据数字",
        f"- **Noto Serif SC**：所有中文正文、模块标题、段落内容",
        f"- 两者不混用：Space Mono = 系统层，Noto Serif SC = 内容层",
        f"",
        f"#### 字号系统",
        f"",
        f"| 层级 | 字号 | 典型元素 |",
        f"|------|------|----------|",
        f"| 系统层主体 / 折叠面板标题 | 13px | 状态栏值、summary 文字、it-kw 关键词 |",
        f"| 标签 / 元数据文字 | 11px | card-label、stat-label、dtable th、ttable th、it-label、conc-clabel、fg-label、tg-label、sys-label |",
        f"| 数据注释 / 辅助行 | 12px | hero-eyebrow、data-note、bsource、it-ev、t-meta、meta-foot、exp-body |",
        f"| 辅助正文 / 卡片次级文字 | 14px | card-val.dim、fg-body、tg-body、top10-body、bapply |",
        f"| 主正文 / 卡片主文字 | 15px | card-val、dtable 主体、it-rule、t-title、conc-list |",
        f"| 页面基础正文 / Hero 描述 | 16px | body base、hero-desc |",
        f"| 数据统计大数字 | 20px | stat-val（模块8核心数字格） |",
        f"",
        f"**原则**：系统层（标签/注释）控制在 11-13px，正文层（内容/解析）控制在 14-16px，不得将两者混淆。",
        f"",
        f"#### 布局系统",
        f"",
        f"**顶部系统状态栏**（position: sticky, top: 0）：",
        f"- 黑底（`#0A0806`），Space Mono，字号 13px，letter-spacing 0.05em",
        f"- 格式：`SUBJECT: {{博主名}} | NOTES_ANALYZED: {total_notes}/62 | GENERATED: {today} | STATUS: DISTILLED`",
        f"- STATUS 字段值用微弱 blink 动画（opacity 1→0.6 循环，2s）",
        f"",
        f"**模块布局**：",
        f"- 最大宽度 1000px 居中，水平内边距 32px",
        f"- 每个模块：`display: grid; grid-template-columns: 100px 1fr; gap: 48px`",
        f"- 左列：装饰性大数字（Space Mono，约 80-96px，color: rgba(26,18,17,0.09)）",
        f"- 右列：模块标签 + 标题 + 内容",
        f"- 模块之间用 `border-bottom: 1px solid #1A1211` 分隔，**不用卡片包裹**",
        f"",
        f"**反转模块**（砖红底 + 白字）：模块1、模块8、模块10",
        f"- `background: #8A3926; color: #FAF6F2`",
        f"- 通过 `margin: 0 -32px; padding: 56px 32px` 实现全宽出血效果",
        f"- 反转模块内的装饰数字：`color: rgba(250,246,242,0.15)`",
        f"",
        f"**硬性约束**：",
        f"- `border-radius: 0`（全站禁止圆角）",
        f"- `box-shadow: none`（全站禁止阴影）",
        f"- 所有边框用 `1px solid #7A6E65` 或 `1px solid #1A1211`",
        f"",
        f"#### 三个必须实现的动效",
        f"",
        f"**1. 滚动进入 fadeInUp**",
        f"- 所有 `.module` 初始状态：`opacity: 0; transform: translateY(20px)`",
        f"- IntersectionObserver（threshold: 0.1）触发时加 `.visible` 类",
        f"- `.visible` 状态：`opacity: 1; transform: translateY(0); transition: opacity .6s ease, transform .6s ease`",
        f"",
        f"**2. 数字 counter 动画**（仅模块8）",
        f"- 目标数字用 `data-target` 属性存储，`data-decimals` 控制小数位",
        f"- 进入视口时从 0 动画到目标值，ease-out cubic，时长 1800ms",
        f"- 需要动画的数字：均赞 2518、均藏 2213、均评 70、藏赞比 87.9%、爆款率 3.1%",
        f"",
        f"**3. 分割线 draw-in**",
        f"- 每个模块标题下方放一个 `.divider-wrap`，内含 `.divider-line`",
        f"- `.divider-line` 初始：`width: 0`；模块进入视口后延迟 200ms：`width: 100%; transition: width .8s ease`",
        f"- 反转模块内的分割线颜色改为 `rgba(250,246,242,0.4)`",
        f"",
        f"#### 禁止事项（V1/V2 已犯过的错误）",
        f"",
        f"- **禁止**：内层容器背景色与页面底色相同（导致卡片消失在背景里）",
        f"- **禁止**：h2 超过 22px，正文 line-height 超过 1.8（导致内容撑满整屏）",
        f"- **禁止**：IF→Then 内容用纯 `<p>` 标签（必须用 `border-left: 4px solid #8A3926` 的容器包裹）",
        f'- **禁止**：模块标题混入"先看结论："等语气词',
        f"- **禁止**：全站只用单一品牌色（必须使用上方完整五色系统）",
        f"- **禁止**：白色卡片 / 圆角 / box-shadow",
        f"- **禁止**：`prefers-reduced-motion` 未处理（必须加 @media 禁用所有动效）",
        f"",
        f"---",
        f"",
        f"## 报告结构 — 10 个模块逐一规格",
        f"",
        f"⚠️ 以下规格必须严格执行，不能简化。每个模块的字段、格式、写作要求、数据来源、禁止事项全部在此列明。",
        f"",
        f"### 模块 1：🎯 一眼看清（摘要卡片）",
        f"",
        f"**布局**：反转模块（砖红底 `#8A3926` + 白字 `#FAF6F2`），`margin: 0 -32px; padding: 56px 32px` 全宽出血",
        f"",
        f"**必须包含的字段**：",
        f"```",
        f"昵称 | 小红书号 | 粉丝数 | 获赞与收藏数",
        f"定位：一句话总结（AI 根据内容归纳，不超过 20 字）",
        f'底层公式：用 × 连接（如"反常识观点 × 真实经历 × 数字对比"）',
        f"📊 采样范围：{total_notes}/（博主总笔记数）条",
        f"⚠️ 基于 {total_notes} 条笔记蒸馏 | 生成时间：{today}",
        f"```",
        f"",
        f"**数据来源**：上方基础统计",
        f'**不允许**：不能写"该博主表现优秀"等空话，每个字段必须有具体数字',
        f"",
        f"### 模块 2：👤 人设拆解（默认展开）",
        f"",
        f"**必须包含**：",
        f"```",
        f"1. 人设三板斧（3 个关键词 + 每个 1 句话解释）",
        f'   格式："行动派 — 不说教，用亲身经历证明可行性"',
        f"",
        f"2. 粉丝追 TA 的本质原因（1-2 句话）",
        f'   必须回答"粉丝跟 TA 的关系是什么"',
        f'   格式："粉丝把 TA 当作先行者——TA 替他们试过了，他们只需要跟"',
        f"",
        f"3. 护城河分析（1 段话，3-5 句）",
        f"   什么是别人模仿不了的？",
        f'   格式：先判断（"TA 的护城河不是技巧，而是..."），再给证据（引用具体笔记数据）',
        f"```",
        f"",
        f"**写作要求**：",
        f'- 必须有具体判断，禁止"博主表现不错"式空话',
        f"- 每个论点必须跟一个数据或笔记引用",
        f"- 人设标签必须是从内容中提炼的，不是从简介复制的",
        f"",
        f"**数据来源**：TOP10 数据包（标题 + 正文 + 评论）+ 标签 TOP20",
        f"",
        f"### 模块 3：🧠 认知层 — TA 怎么想（默认展开）",
        f"",
        f"**必须包含**：",
        f"```",
        f"1. 核心信念精华（5-8 条）",
        f"   ③ AI 必须从上方观点句候选全量列表中归纳，不能凭空编造。",
        f"   每条信念须在 ≥3 条不同笔记中出现过（列出笔记标题作为出处）。",
        f'   信念必须有辨识度，不能是"好好学习""保持乐观"等通用废话。',
        f"   每条格式：",
        f'   "信念内容"',
        f"   — 出现在《笔记A》《笔记B》《笔记C》中 | 验证：跨主题复现 ✅",
        f"",
        f"2. 原文观点摘录（20-30 条，可折叠展开）",
        f"   ① 数据来源：上方观点句候选全量列表",
        f"   <details><summary>查看全部 XX 条原文观点 →</summary>",
        f'   每条格式："原文观点句" — 来源：《笔记标题》(赞数)',
        f"   </details>",
        f"",
        f"3. 认知框架（AI 提炼，不是写作格式）",
        f"   ④ AI 从观点句候选中提炼这个博主解读世界、解读事件的框架。",
        f'   不是"TA 喜欢用反问开头"，而是"TA 倾向于用成本收益视角解读人生选择"。',
        f"   须举出 2-3 个具体笔记作为证据。",
        f"",
        f"4. 观点张力（≥1 对矛盾）",
        f"   ⑤ AI 从观点句候选中找出博主在不同笔记里表达过的相互矛盾的观点。",
        f"   矛盾不是 bug，是真实的标志。一个人的观点完全自洽 = 太假了。",
        f"   格式：观点A（出处）vs 观点B（出处）+ 如何理解这个矛盾",
        f"",
        f"5. 写作切入方式统计（脚本数据，直接填数字，供参考）",
        f"   数据来源：上方写作结构统计",
    ]

    # 写入实际数字
    opening_display = " / ".join(f"{k}{v}条" for k, v in sorted(opening.items(), key=lambda x: x[1], reverse=True)) if opening else "（无数据）"
    ending_display = " / ".join(f"{k}{v}条" for k, v in sorted(ending.items(), key=lambda x: x[1], reverse=True)) if ending else "（无数据）"
    lines += [
        f"   - 切入方式：{opening_display}",
        f"   - 收尾方式：{ending_display}",
        f"",
        f"6. 价值立场",
        f'   ⑥ AI 从上方高频词 TOP15 中筛选，过滤"时候""自己""觉得"等通用词，',
        f"   保留有价值立场含义的词 5-10 个。",
        f'   格式：高频价值词（保留词 + 出现次数）',
        f'   一句话总结："TA 的内容底色是___"',
        f"```",
        f"",
        f'**⚠️ 置信度标注**：模块顶部标注"基于 {total_notes} 条笔记提取，样本有限仅供参考"',
        f"",
        f"**写作要求**：",
        f"- 核心信念必须有辨识度，不能是通用废话",
        f"- 原文观点必须是博主正文原句，不能是 AI 改写",
        f"- 认知框架是世界观层面，不是写作格式",
        f"- 写作切入方式统计只是辅助数字，不是认知层的核心",
        f"",
        f"### 模块 4：⚡ 策略层 — TA 怎么运营（默认展开）",
        f"",
        f"**必须包含**：",
        f"```",
        f"1. 系列内容规划（最重要）",
        f'   - TA 有没有固定系列？（从标签聚类判断，如"下班玩AI"系列）',
        f"   - 系列节奏是什么？（每周几条？集中还是分散？）",
        f"   - 系列之间的关系？（独立并行？递进？）",
        f"",
        f"2. 蹭热点节奏",
        f"   - 近期是否有明显蹭热点的笔记？（从发布时间+内容判断）",
        f"   - 热点笔记 vs 常规笔记的数据对比",
        f"   - 时效策略推断",
        f"",
        f'3. 运营习惯（3-5 条，格式："If X, then Y"）',
        f"   例如：",
        f'   - "如果评论区有争议 → 博主会追加置顶回复澄清"',
        f'   - "如果笔记 48 小时内赞数 < 均赞的 30% → 可能会删除或重发"',
        f'   - "每条笔记必带 3-5 个固定标签 + 1-2 个热点标签"',
        f"```",
        f"",
        f"**写作要求**：",
        f"- 运营习惯必须是从数据推断的，不能是通用建议",
        f"- 每条习惯附一个具体的笔记/数据作为证据",
        f"",
        f"**数据来源**：内容领域分布 + 标签 TOP20 + TOP10 数据包评论",
        f"",
        f"### 模块 5：🏆 TOP10 爆款拆解（默认展开，详情可折叠）",
        f"",
        f"**TOP1-5 深度拆解，每条格式**：",
        f"```",
        f"#{{排名}} {{标题}}",
        f"赞 {{X}} | 藏 {{X}} | 评 {{X}} | 藏赞比 {{X%}}",
        f"",
        f"▎为什么爆：（2-3 句因果分析，不是贴标签）",
        f'  "这条之所以爆，核心原因是..."，引用正文片段作为证据',
        f"",
        f"▎可借鉴点：（1-2 条具体可操作的建议）",
        f'  "你可以用同样的结构——先 xxx，再 xxx，最后 xxx"',
        f"",
        f"▎评论区洞察：（从 TOP5 评论中提取）",
        f"  最高赞评论是'xxx'(Y赞) → 说明读者最在意的是...",
        f"",
        f"[可折叠：正文前 200 字摘要]",
        f"```",
        f"",
        f"**TOP6-10 快速拆解，每条格式**：",
        f"```",
        f"#{{排名}} {{标题}} | 赞{{X}} 藏{{X}} | 一句话：为什么这条能火",
        f"```",
        f"",
        f"**写作要求**：",
        f'- "为什么爆"必须是因果分析（"因为...所以..."），不能是"表现优秀""引起共鸣"',
        f'- 可借鉴点必须具体到操作层面，不能是"多写好内容"',
        f"- 评论洞察必须引用真实评论原文",
        f"",
        f"**数据来源**：上方 TOP10 数据包",
        f"",
        f"### 模块 6：📝 内容公式速查（默认展开，精华版）",
        f"",
        f"**必须包含**：",
        f"```",
        f"标题公式 TOP5：",
        f"| # | 公式名 | 使用率 | 模板 | 博主原标题示例 |",
        f"|---|--------|--------|------|-------------|",
        f'| 1 | 数字型 | 35% | "N个XXX，第X个..." | "5个让我后悔的决定" |',
        f"",
        f"开头公式 TOP3（一句话 + 示例）",
        f"CTA 公式 TOP3（一句话 + 示例）",
        f"标签策略：固定标签 + 热点搭配规则",
        f"",
        f"→ 完整公式详见蒸馏出的 skill 文件",
        f"```",
        f"",
        f"**写作要求**：",
        f'- 使用率必须是真实统计数字，不是"较多使用"',
        f"- 示例必须是博主的真实标题/开头/CTA，不是 AI 编的",
        f"",
        f"**数据来源**：标题模式统计 + CTA 统计 + 标签 TOP20",
        f"",
        f"### 模块 7：💡 选题灵感 TOP15（默认展开）",
        f"",
        f"**格式**：",
        f"```",
        f"| # | 选题方向 | 难度 | 预估潜力 | 参考爆款 | 为什么值得做 |",
        f"|---|---------|------|---------|---------|------------|",
        f"| 1 | xxx | ⭐ | 🔥🔥🔥 | 《标题》(赞数) | 一句话理由 |",
        f"```",
        f"",
        f"**排序逻辑**：优先级 = 预估潜力 × (1/难度)",
        f"**预估潜力判断**：该方向现有笔记的均赞 vs 博主整体均赞（{stats['avg_likes']:,}）",
        f"**难度判断**：需要的专业度/素材/时间",
        f"",
        f"**数据来源**：内容领域分布 + TOP10 数据交叉分析",
        f"",
        f"### 模块 8：📊 数据面板",
        f"",
        f"**基础区（默认展开）**：",
        f"```",
    ]

    collect_like_ratio = round(stats["total_collects"] / stats["total_likes"] * 100, 1) if stats["total_likes"] > 0 else 0
    lines += [
        f"均赞 {stats['avg_likes']:,} | 均藏 {stats['avg_collects']:,} | 均评 {stats['avg_comments']:,} | 藏赞比 {collect_like_ratio}%",
    ]
    if notes and stats["total"] > 0:
        avg = stats["avg_likes"]
        hit_count = sum(1 for n in notes if n.get("likes", 0) > avg * 3)
        super_hit = sum(1 for n in notes if n.get("likes", 0) > avg * 10)
        lines.append(f"爆款率（>均赞×3）{round(hit_count/stats['total']*100, 1)}% | 超级爆款率（>均赞×10）{super_hit}条")
    lines += [
        f"视频 {stats['video_count']}条 vs 图文 {stats['normal_count']}条：（AI 从 TOP10 数据计算均赞对比）",
        f"```",
        f"",
        f"**详细区（默认折叠）**：",
        f"```",
        f"<details><summary>查看详细数据 →</summary>",
        f"发布频率：{frequency_info.get('pattern', '未知') if frequency_info else '未知'} | 平均{frequency_info.get('avg_days_between', '?') if frequency_info else '?'}天/条",
        f"标签 TOP20（见上方表格）",
        f"Emoji 使用率 {emoji_info.get('emoji_usage_pct', 0) if emoji_info else 0}% + TOP10 高频 emoji",
        f"正文平均长度 {structure_info.get('avg_length', 0) if structure_info else 0}字 | 列表使用率 | 小标题使用率",
        f"</details>",
        f"```",
        f"",
        f"### 模块 9：📈 发展趋势（默认折叠）",
        f"",
        f"```",
        f"<details><summary>📈 发展趋势（基于 {total_notes} 条笔记，仅供参考）→</summary>",
        f"",
        f"早期（前 50% 笔记按时间）vs 近期（后 50%）：",
        f"（从上方发展趋势数据填入：内容重心变化、数据变化）",
        f"",
        f"[如果时间跨度 > 6 个月且笔记 ≥ 30 条]",
        f"转型路径分析：阶段1（时间段）→ 阶段2 → 阶段3，每个阶段的核心标签 + 均赞",
        f"",
        f"</details>",
        f"```",
        f"",
        f"**数据来源**：上方发展趋势数据",
        f"",
        f"### 模块 10：🔑 核心结论（默认展开）",
        f"",
        f"```",
        f"✅ 3 件可以立刻复制的事：",
        f"1. xxx（附具体操作步骤）",
        f"2. xxx",
        f"3. xxx",
        f"",
        f"⚠️ 3 件要避免的坑：",
        f"1. xxx（附博主翻车的笔记作为反面教材）",
        f"2. xxx",
        f"3. xxx",
        f"",
        f'💡 底层公式（一句话）："{nickname}的底层公式 = xxx × xxx × xxx"',
        f"```",
        f"",
        f"---",
        f"",
        f"## 8 条写作准则（HTML 和 Skill 共用）",
        f"",
        f"⚠️ 以下准则必须贯穿整份 HTML 报告，违反任一条则视为不合格输出。",
        f"",
        f"| # | 准则 | 违反示例 | 正确示例 |",
        f"|---|------|---------|---------|",
        f'| 1 | **有观点不骑墙** | "该博主表现不错" | "TA 的护城河不是技巧，而是真实经历积累的信任感——这是无法模仿的" |',
        f'| 2 | **有对比有洞察** | "视频表现好" | "视频均赞 453 vs 图文均赞 151，视频是图文的 3 倍——但 TOP3 爆款全是图文，说明极端爆款靠内容不靠形式" |',
        f'| 3 | **有针对性建议** | "建议多发优质内容" | "你可以用同样的结构：先抛一个反常识观点，再用3个亲身经历做论据，最后用\'你觉得呢\'收尾引导评论" |',
        f'| 4 | **有因果解释** | "这条笔记引起共鸣" | "这条之所以爆，核心原因是\'算账型内容\'天然有传播力——读者看完会想\'我也算算我的\'" |',
        f'| 5 | **有层次感** | TOP1-10 全部同等篇幅 | TOP1-5 深度拆（每条 8-10 行），TOP6-10 快速拆（每条 2 行） |',
        f'| 6 | **有金句记忆点** | 没有总结性金句 | "TA 的底层公式 = 反常识选题 × 亲身经历论证 × 数字锚定说服力" |',
        f'| 7 | **有数据锚点** | "表现较好""热度较高" | "均赞 {stats["avg_likes"]:,}，藏赞比 {collect_like_ratio}%，爆款率见数据面板" |',
        f'| 8 | **格式有节奏** | 全文纯文字段落 | 数据=表格，分析=段落+加粗，结论=引用块，列表=要点式 |',
        f"",
        f"---",
    ]

    # ================================================================
    # 第三部分：任务二 — Skill 文件夹
    # ================================================================

    # 标题模式实际数据供模板参考
    sorted_patterns = sorted(title_patterns.items(), key=lambda x: x[1]["count"], reverse=True)[:5] if title_patterns else []
    total_s = structure_info["short_count"] + structure_info["medium_count"] + structure_info["long_count"] if structure_info else 0
    list_pct = round(structure_info["has_list_count"] / total_s * 100, 1) if (structure_info and total_s) else 0
    heading_pct = round(structure_info["has_number_heading"] / total_s * 100, 1) if (structure_info and total_s) else 0

    lines += [
        f"",
        f"# 第三部分：任务二 — 生成创作指南 Skill 文件夹",
        f"",
        f"**输出文件夹**：`{skill_dirname}/`",
        f"",
        f"---",
        f"",
        f"## 格式要求",
        f"",
        f"- 产出一个可安装的 Skill 文件夹，不是单个 `.skill.md` 文件",
        f"- 文件夹命名必须为：`{skill_dirname}/`",
        f"- 文件夹中必须包含入口文件：`{skill_entry_path}`",
        f"- 当前 E2 阶段采用最小结构：先只要求 `SKILL.md`，不强制 `agents/openai.yaml`",
        f"- `SKILL.md` 采用标准格式（YAML 头 + Markdown 正文）",
        f"- 置信度标注：⚠️ 基于 {total_notes} 条笔记蒸馏",
        f"- 所有统计数字必须来自上方数据原材料，不能编造",
        f"- 所有原文引用必须来自 TOP10 数据包，不能改写",
        f"- 按以下完整模板填写，不允许偷工减料",
        f"",
        f"---",
        f"",
        f"## Skill 文件夹结构",
        f"",
        f"```text",
        f"{skill_dirname}/",
        f"└── {skill_entry_file}",
        f"```",
        f"",
        f"## `{skill_entry_file}` 完整模板",
        f"",
        f"````markdown",
        f"---",
        f"name: {skill_name_field}",
        f"description: >",
        f"  基于{nickname}的 {total_notes} 条小红书笔记蒸馏而成的{skill_desc_type}。",
        f"  五层蒸馏：认知层（{belief_header}）→ 策略层（{strategy_header}）→ 内容层（{content_header}）→ 对比示例 → 局限性。",
        f"  当你需要创作小红书内容时，加载此 skill，{skill_desc_extra}。",
        f"---",
        f"",
        f"# {nickname} {skill_desc_type}",
        f"",
        f"> ⚠️ 基于 {total_notes} 条小红书笔记蒸馏 | 生成时间：{today}",
        f"",
        f"---",
        f"",
        f"## 使用说明（运行规则）",
        f"",
        f"**{run_rule_text}**",
        f"",
        f'1. 用户说"写一篇关于 XXX 的笔记"时：',
        f"   - 先查**认知层**：这个话题{'TA' if mode == 'A' else '你'}会持什么立场？",
        f"   - 再查**策略层**：这个话题适合放在哪个系列？要蹭热点吗？",
        f"   - 最后查**内容层**：用哪个标题公式？哪个开头模板？",
        f'2. 用户说"帮我优化这篇笔记"时：',
        f"   - 用内容层的公式对照检查标题/开头/CTA",
        f"   - 用认知层的思维模式检查论证结构",
        f'3. 用户说"给我选题建议"时：',
        f"   - 从策略层的系列规划出发",
        f"   - 结合选题灵感池推荐",
        f"",
        f"**硬性规则（优先级最高）：**",
        f"- 不能编造{nickname}从未表达过的观点",
        f'- 不能把通用写作建议包装成"{nickname}的独特方法"',
        f"- 所有公式和模板必须有原始笔记作为来源",
        f'- 当用户问的话题超出采样范围时，明确说"这个方向在蒸馏数据中没有覆盖"',
        f"",
        f"---",
        f"",
        f"## 一、认知层 — {belief_header}",
        f"",
        f"> ⚠️ 基于 {total_notes} 条笔记提取，样本有限仅供参考",
        f"",
        f"### 1.1 核心信念（5-8条，每条在≥3条不同笔记中验证通过）",
        f"",
        f"③ AI 从上方观点句候选全量列表中归纳，不能凭空编造，必须有辨识度",
        f"",
        f'**1. "{{信念内容——必须有辨识度，不是所有博主都会说的话}}"**',
        f"- 📍 出处：《{{笔记A标题}}》({{赞数}})、《{{笔记B标题}}》({{赞数}})、《{{笔记C标题}}》({{赞数}})",
        f"- 🎯 应用场景：当用户写{{某类话题}}时，用这个信念作为底层立场",
        f"- ⚠️ 局限：这个信念在{{某种情况}}下可能不适用",
        f"- ✅ 验证：跨{{X}}个不同主题复现",
        f"",
        f'**2. "{{信念内容}}"**',
        f"- 📍 出处：...",
        f"- 🎯 应用场景：...",
        f"- ⚠️ 局限：...",
        f"- ✅ 验证：...",
        f"",
        f"（共5-8条，每条格式相同）",
        f"",
        f"### 1.2 观点张力（博主自身的矛盾之处，≥1对）",
        f"",
        f"> 矛盾不是Bug，是真实的标志。一个人的观点完全自洽 = 太假了。",
        f"⑤ AI 从观点句候选中找出博主在不同笔记里表达过的相互矛盾的观点。",
        f"",
        f'**张力 1："{{观点A}}" vs "{{观点B}}"**',
        f'- 观点A出处：《{{笔记标题}}》— "{{原文片段}}"',
        f'- 观点B出处：《{{笔记标题}}》— "{{原文片段}}"',
        f"- 如何理解这个矛盾：{{AI的分析——可能是不同阶段的想法变化，可能是不同情境下的不同策略}}",
        f"- 创作建议：{{在写什么类型内容时用观点A，什么时候用观点B}}",
        f"",
        f"### 1.3 思维模式",
        f"",
        f"> 分两部分：写作切入方式（脚本统计数字）+ 认知框架（AI 从观点句提炼）",
        f"",
        f"**写作切入方式统计（来自脚本数据）：**",
        f"| 类型 | 条数 | 示例（博主原文前50字） |",
        f"|------|------|---------------------|",
    ]

    if opening:
        for k, v in sorted(opening.items(), key=lambda x: x[1], reverse=True):
            lines.append(f'| {k} | {v}条 | "{{从TOP10数据包中找该类型开头的原文}}" |')
    else:
        for typ in ["故事开头", "反问开头", "数据开头", "观点直抛"]:
            lines.append(f'| {typ} | {{X}}条 | "{{博主该类型开头原文}}" |')

    lines += [
        f"",
        f"**收尾方式统计：**",
        f"| 类型 | 条数 | 示例（博主原文最后50字） |",
        f"|------|------|----------------------|",
    ]

    if ending:
        for k, v in sorted(ending.items(), key=lambda x: x[1], reverse=True):
            lines.append(f'| {k} | {v}条 | "{{从TOP10数据包中找该类型结尾的原文}}" |')
    else:
        for typ in ["金句收尾", "开放提问", "行动号召", "总结回顾"]:
            lines.append(f'| {typ} | {{X}}条 | "{{博主该类型结尾原文}}" |')

    lines += [
        f"",
        f"**认知框架（④ AI 从观点句候选提炼，不是写作格式）：**",
        f"",
        f"> 这个博主解读世界、解读事件的底层框架是什么？",
        f'> 不是"TA 喜欢用反问开头"，而是"TA 倾向于用成本收益视角解读人生选择"。',
        f"",
        f"框架1：{{名称}}",
        f"- 描述：{{AI 提炼的认知框架，1-2句话}}",
        f'- 证据：《{{笔记标题}}》— "{{原文片段}}"；《{{笔记标题}}》— "{{原文片段}}"',
        f"",
        f"框架2：{{名称}}（如有）",
        f"- 描述：...",
        f"- 证据：...",
        f"",
        f"### 1.4 价值立场",
        f"",
        f"- **核心价值词**（按出现频次排序）：",
        f'  ⑥ AI 从上方高频词 TOP15 中筛选，过滤"时候""自己""觉得"等通用词，保留有价值立场含义的词',
        f"  {{词1}}({{X次}}) / {{词2}}({{X次}}) / {{词3}}({{X次}}) / ... （TOP10）",
        f'- **一句话总结**："{nickname}的内容底色是{{XXX}}"',
        f'- **写作时的态度基调**：{{具体描述，如"真诚但不讨好，分享但不说教，有观点但不攻击"}}',
        f"",
        f"### 1.5 与读者的关系",
        f"",
        f"- **关系类型**：{{学姐型 / 朋友型 / 导师型 / 陪伴型 / 同行者型}}",
        f'- **称呼方式**：{{TA怎么称呼读者——"姐妹""宝子""各位""你们"...}}',
        f"- **互动风格**：{{TA在评论区的典型回复风格——逐条回复?只回高赞?玩梗?认真解答?}}",
        f"- **创作时代入的角色**：",
        f'  "{role_text}——{{补充说明}}。你说话的方式是{{具体描述}}，你不会{{禁止事项}}。"',
        f"",
        f"---",
        f"",
        f"## 二、策略层 — {strategy_header}",
        f"",
        f"### 2.1 系列内容规划（最重要）",
        f"",
        f"- **固定系列**：",
        f"  | 系列名 | 条数 | 均赞 | 发布节奏 | 状态 |",
        f"  |--------|------|------|---------|------|",
        f'  | {{系列名，如"下班玩AI"}} | {{X}}条 | {{X}} | 每{{X}}天一篇 | 持续中/已停 |',
        f"",
        f"- **系列之间的关系**：{{独立并行？递进？交替？}}",
        f'- **非系列内容策略**：{{描述，如"每3条系列穿插1条日常/热点"}}',
        f"",
        f"### 2.2 蹭热点策略",
        f"",
        f"- **热点内容占比**：约{{X}}%（{{Y}}条疑似蹭热点/共{total_notes}条）",
        f'- **时效要求**：{{描述，如"24小时内发布" 或 "这个博主几乎不蹭热点"}}',
        f"- **热点 vs 常规数据对比**：",
        f"  | 类型 | 条数 | 均赞 | 均藏 |",
        f"  |------|------|------|------|",
        f"  | 热点内容 | {{X}} | {{X}} | {{X}} |",
        f"  | 常规内容 | {{X}} | {{X}} | {{X}} |",
        f"- **热点策略建议**：{{基于数据得出的结论}}",
        f"",
        f"### 2.3 运营决策准则（3-5条，If-Then 格式）",
        f"",
        f"**1. If {{具体可观测的条件}} → Then {{具体行动}}**",
        f"   证据：《{{笔记标题}}》中观察到{{具体行为}}",
        f"   解读：{{为什么这个准则有效}}",
        f"",
        f"**2. If {{条件}} → Then {{行动}}**",
        f"   证据：...",
        f"",
        f"**3. If {{条件}} → Then {{行动}}**",
        f"   证据：...",
        f"",
        f"> 注意：运营准则是从**行为数据推断**的，非博主本人确认。",
        f"",
        f"---",
        f"",
        f"## 三、内容层 — {content_header}",
        f"",
        f"### 3.1 标题公式（TOP5 可直接套用）",
        f"",
        f"| # | 公式名称 | 使用率 | 模板（可填空） | 博主原标题 | 你的改编建议 |",
        f"|---|---------|--------|-------------|----------|------------|",
    ]

    if sorted_patterns:
        for i, (pname, data) in enumerate(sorted_patterns):
            ex = data["examples"][0][:20] if data["examples"] else "（示例）"
            lines.append(f'| {i+1} | {pname} | {data["pct"]}% | "{{填空模板}}" | "{ex}" | {{改编建议}} |')
        for i in range(len(sorted_patterns), 5):
            lines.append(f'| {i+1} | {{公式名称}} | {{X}}% | "{{模板}}" | "{{博主原标题}}" | {{改编建议}} |')
    else:
        for i in range(5):
            lines.append(f'| {i+1} | {{公式名称}} | {{X}}% | "{{模板}}" | "{{博主原标题}}" | {{改编建议}} |')

    lines += [
        f"",
        f"**标题创作规则：**",
        f"- 首选公式 #1 和 #2（使用率最高 + 效果最好）",
        f"- 标题长度控制在{{X}}-{{Y}}字",
        f'- 必须包含的元素：{{具体元素，如"数字""情绪词""反差"}}',
        f'- 禁忌：{{具体禁忌，如"不用问号结尾"}}',
        f"",
        f"### 3.2 开头模板（TOP3）",
        f"",
        f'**模板 1：{{类型名，如"反常识炸弹"}}（使用率 {{X}}%）**',
        f"- 结构：{{第一句干什么}} → {{第二句干什么}} → {{第三句干什么}}",
        f'- 博主原文示例："{{前80字原文}}"',
        f"  来源：《{{标题}}》({{赞数}})",
        f"- 仿写模板：",
        f"  ```",
        f"  {{第一句模板——可填空}}",
        f"  {{第二句模板}}",
        f"  {{第三句模板}}",
        f"  ```",
        f"",
        f"**模板 2：{{类型名}}（使用率 {{X}}%）**",
        f"- 结构：...",
        f"- 博主原文示例：...",
        f"- 仿写模板：...",
        f"",
        f"**模板 3：{{类型名}}（使用率 {{X}}%）**",
        f"- ...",
        f"",
        f"### 3.3 正文骨架",
        f"",
        f"**典型结构（{{X}}% 的笔记使用此结构）：**",
        f"```",
        f"{{hook/开头}}（约{{X}}字）",
        f"    ↓ 作用：{{抓注意力/制造好奇}}",
        f"{{主体段1}}（约{{X}}字）",
        f"    ↓ 作用：{{展开论点/讲故事/列数据}}",
        f"{{主体段2}}（约{{X}}字）",
        f"    ↓ 作用：{{转折/深化/补充案例}}",
        f"{{收尾/CTA}}（约{{X}}字）",
        f"    ↓ 作用：{{引导互动/留钩子}}",
        f"```",
        f"",
        f"**文本统计（创作时作为参考基线）：**",
        f"| 指标 | 数值 | 说明 |",
        f"|------|------|------|",
        f"| 平均正文长度 | {structure_info.get('avg_length', '?') if structure_info else '?'}字 | 短于{{Y}}字可能信息量不足，长于{{Z}}字可能冗余 |",
        f"| 列表使用率 | {list_pct}% | 高→TA偏好要点式 / 低→TA偏好叙述式 |",
        f"| 小标题使用率 | {heading_pct}% | |",
        f"",
        f"### 3.4 CTA 策略",
        f"",
        f"| CTA 类型 | 使用率 | 典型话术（博主原文） |",
        f"|---------|--------|-------------------|",
    ]

    if cta_info:
        for cta_type, data in sorted(cta_info.items(), key=lambda x: x[1]["count"], reverse=True)[:3]:
            lines.append(f'| {cta_type} | {data["pct"]}% | "{{博主该CTA类型的原文}}" |')
    else:
        lines.append(f'| {{CTA类型}} | {{X}}% | "{{博主原文}}" |')

    lines += [
        f"",
        f"**CTA 组合规则：**",
        f"- 每篇{{必须/建议}}包含{{X}}个CTA",
        f"- 最有效组合：{{类型A}} + {{类型B}}（数据对比证据：含此组合的笔记均赞{{X}} vs 不含的均赞{{Y}}）",
        f"- 放置位置：{{末尾/正文中/开头}}",
        f"",
        f"### 3.5 视觉规则",
        f"",
    ]

    if emoji_info:
        lines.append(f"- **Emoji 使用率**：{emoji_info['emoji_usage_pct']}%的笔记使用 emoji，平均每段{{X}}个")
        if emoji_info["top_emojis"]:
            top_e = " ".join(e[0] for e in emoji_info["top_emojis"][:5])
            lines.append(f"- **高频 Emoji TOP5**：{top_e}")
    else:
        lines.append(f"- **Emoji 使用率**：{{X}}%")

    lines += [
        f'- **排版风格**：{{描述，如"短段落+emoji分隔" / "长段落+小标题分层"}}',
        f"- **图文类型偏好**：视频{stats['video_count']}条 / 图文{stats['normal_count']}条",
        f"  - 数据对比：视频均赞{{X}} vs 图文均赞{{Y}}（从 TOP10 数据计算）",
        f"  - 建议：{{基于数据的建议}}",
        f"",
        f"### 3.6 标签策略",
        f"",
    ]

    if tag_freq:
        top3_tags = " ".join(f"#{t}" for t, c in tag_freq[:3])
        lines.append(f"- **固定标签（每篇必带）**：{top3_tags}（出现在 ≥80% 的笔记中）")
    else:
        lines.append(f"- **固定标签（每篇必带）**：#{{tag1}} #{{tag2}} #{{tag3}}")

    lines += [
        f"- **领域标签（按内容选）**：#{{tag4}} #{{tag5}} #{{tag6}}（出现在 30%-80% 的笔记中）",
        f"- **热点标签规则**：每篇加{{1-2}}个当前热点标签",
        f"- **标签总数**：每篇{{X}}-{{Y}}个",
        f"",
        f"### 3.7 发布节奏",
        f"",
    ]

    if frequency_info and frequency_info.get("avg_days_between"):
        lines.append(f"- **发布频率**：平均每{frequency_info['avg_days_between']}天一篇（{frequency_info['pattern']}）")
    else:
        lines.append(f"- **发布频率**：平均每{{X}}天一篇")

    lines += [
        f'- **最活跃时段**：{{时间段，如"工作日晚上8-10点"}}',
        f'- **连发策略**：{{描述，如"同一话题连发2-3条效果好" 或 "间隔发布，避免刷屏"}}',
        f"",
        f"---",
        f"",
        f"## 四、{forbidden_header}",
        f"",
        f'> 这些是从内容分析中推断的"底线"。创作时触碰这些等于"出戏"。',
        f"",
        f"1. **{{禁止事项1的具体描述}}**",
        f'   // 示例："绝不会用\'震惊！\'\'点赞收藏一起来\'等低质CTA"',
        f"   证据：{total_notes}条笔记中 0 次出现此类表达",
        f"",
        f"2. **{{禁止事项2的具体描述}}**",
        f'   // 示例："不会无脑追热点——没有纯蹭热点的笔记"',
        f"   证据：...",
        f"",
        f"3. **{{禁止事项3的具体描述}}**",
        f'   // 示例："不会说教式语气——从不用\'你应该\'\'你必须\'"',
        f"   证据：...",
        f"",
        f"（3-5条，每条有证据支持）",
        f"",
        f"---",
        f"",
        f"## 五、{contrast_header}",
        f"",
        f"### 示例 1：选题方向「{{从TOP3爆款选题方向1}}」",
        f"",
        f"**❌ 普通风格：**",
        f"```",
        f"标题：{{一个平庸的标题}}",
        f"开头：{{普通开头——50字}}",
        f"正文结构：{{简述}}",
        f"```",
        f"",
        f"**✅ {nickname}风格：**",
        f"```",
        f"标题：{{用TA的标题公式#1改写}}",
        f"开头：{{用TA的开头模板#1改写——50字}}",
        f"正文结构：{{用TA的正文骨架改写}}",
        f"```",
        f"",
        f"**→ 关键区别**：{{2-3句话解释差异核心——",
        f'  普通版在陈述事实，{nickname}版用{{具体手法}}切入制造{{具体效果}}，',
        f"  再用{{具体手法}}增加{{具体效果}}}}",
        f"",
        f"### 示例 2：选题方向「{{选题方向2}}」",
        f"（同样格式，另一个选题方向）",
        f"",
        f"### 示例 3：选题方向「{{选题方向3}}」",
        f"（同样格式，第三个选题方向）",
        f"",
        f"---",
        f"",
        f"## 六、{topic_header}",
        f"",
        f"> 基于{nickname}的内容数据，以下选题方向值得尝试。",
        f"",
        f"| # | 选题方向 | 难度 | 预估潜力 | 参考爆款 | 为什么值得做 |",
        f"|---|---------|------|---------|---------|------------|",
        f"| 1 | {{方向}} | ⭐ | 🔥🔥🔥 | 《{{标题}}》({{赞数}}) | {{一句话理由}} |",
        f"| 2 | ... | ... | ... | ... | ... |",
        f"| ... | ... | ... | ... | ... | ... |",
        f"| 10 | ... | ... | ... | ... | ... |",
        f"",
        f"> 排序逻辑：优先级 = 预估潜力 × (1/难度)",
        f"",
        f"---",
        f"",
        f"## 七、⚠️ 局限性说明",
        f"",
        f"- 本 skill 基于 {total_notes} 条小红书笔记蒸馏，样本覆盖率 {total_notes}/（博主总笔记数）",
        f"- 无法反映博主未公开发表的想法和私下运营策略",
        f"- 无法实时追踪博主最新内容变化，建议定期重新蒸馏（推荐频率：每1-3个月）",
        f"- 公开发表的内容可能经过修饰，不完全等于真实思维方式",
        f'- 认知层核心信念经过"≥3条笔记出现"验证，但仍可能存在过度归纳',
        f"- 运营策略层为 AI 从行为数据推断，非博主本人确认",
        f"- 当用户要求的创作方向超出蒸馏数据覆盖范围时，应主动告知",
        f"",
        f"---",
        f"",
        f"## 八、自检清单（AI 生成后必须对照）",
        f"",
        f"| # | 检查项 | 通过标准 | 失败信号 |",
        f"|---|--------|---------|---------|",
        f"| 1 | 核心信念数量 | 5-8条，均有≥3条笔记出处 | <3条 或 >10条 或 无出处 |",
        f'| 2 | 信念辨识度 | 读完能区分是{nickname}而不是随便一个博主 | 全是"好好学习""保持乐观"等通用废话 |',
        f"| 3 | 观点张力 | ≥1对矛盾记录 | 观点完全自洽（太假） |",
        f"| 4 | 标题公式 | 5条，均有使用率+模板+原标题+改编建议 | 只有公式名没有示例 |",
        f'| 5 | 开头模板 | 3条，均有原文80字+仿写模板 | 只写"可以用故事开头"没有模板 |',
        f'| 6 | 对比示例 | 3组，每组有普通vs{nickname}+关键区别 | 两种风格看不出区别 |',
        f"| 7 | 创作禁区 | 3-5条，每条有证据 | 没有反模式 或 写的是通用禁忌 |",
        f'| 8 | 所有数据有来源 | 统计数字来自上方数据原材料，原文来自笔记正文 | 出现"约""大概"等模糊量词 |',
        f"| 9 | 运行规则存在 | 使用说明告诉agent怎么用这个skill | 没有使用说明，agent不知道干什么 |",
        f'| 10 | 局限性说明 | ≥5条具体局限 | 只有"不能替代本人" |',
        f"````",
        f"",
        f"---",
        f"",
        f"*本文件由 deep_analyze.py 自动生成 | 模式：{'A — 学习TA' if mode == 'A' else 'B — 认识自己'} | {today}*",
        f"*内联了所有数据和规格，AI 无需读取其他文件。*",
    ]

    return "\n".join(lines)


# ----------------------------------------------------------
# 主函数
# ----------------------------------------------------------

def _restore_author_identity(analysis, nickname):
    """合规改造 v2.0 · 方案 A 的作者身份回填

    中间产物（raw / analysis.json）里作者被脱敏为 "作者"。
    本函数在生成最终产出物前，把 "作者" 回填为博主真实昵称。
    读者保持 "读者N" 不动。

    遍历范围：top10 列表 + notes 列表里每条笔记的 comment_list 及其子评论。

    修改方式：原地修改 analysis dict 里的字符串字段。
    """
    if not nickname:
        return

    def _patch_comment(c):
        # 主评论
        if c.get("user") == "作者":
            c["user"] = nickname
        # 子评论
        for sc in c.get("sub_comments", []) or []:
            if sc.get("user") == "作者":
                sc["user"] = nickname
            # reply_to 如果指向作者，也回填
            if sc.get("reply_to") == "作者":
                sc["reply_to"] = nickname
        # 主评论的 reply_to（少见但兼容）
        if c.get("reply_to") == "作者":
            c["reply_to"] = nickname

    for bucket in ("top10", "notes"):
        for note in analysis.get(bucket, []) or []:
            for c in note.get("comment_list", []) or []:
                _patch_comment(c)


def deep_analyze(analysis_path, nickname, output_dir, notes_details_path=None, mode="A"):
    """
    执行确定性深度分析，生成增强版文档 + AI蒸馏任务.md。

    mode="A"：学习博主（默认），skill 文件夹 {博主名}_创作指南.skill/
    mode="B"：认识自己，skill 文件夹 {博主名}_创作基因.skill/
    mode="C"：v2.1 预留，raise NotImplementedError

    Returns:
        dict — { "docs": [...], "task_path": str }
    """
    os.makedirs(output_dir, exist_ok=True)

    with open(analysis_path, "r", encoding="utf-8") as f:
        analysis = json.load(f)

    # 合规改造 v2.0 · 方案 A：作者身份回填
    # raw JSON 和 analysis.json 中间产物里，作者被脱敏成 "作者"（匿名占位符），
    # 读者被脱敏成 "读者N"。到了最终产出物（HTML 蒸馏报告 / 创作指南 skill / 创作基因 skill）
    # 这一层，将 "作者" 回填为博主真实昵称，让用户能在产出物里看到是谁的内容。
    # 读者继续保持 "读者N" 匿名。
    _restore_author_identity(analysis, nickname)

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

    # ---- 读取认知层字段（D 批次新增）----
    opinion_candidates = analysis.get("opinion_candidates", [])
    opinion_mode = analysis.get("opinion_extraction_mode", "unknown")
    writing_structure = analysis.get("writing_structure", {})
    value_words = analysis.get("value_words", [])

    # ---- 生成数据底稿.md ----
    process_dir = os.path.join(output_dir, "_过程文件", "原始素材")
    os.makedirs(process_dir, exist_ok=True)

    draft_content = gen_data_draft(
        nickname, stats, top10, category_stats, tag_freq,
        title_patterns, emoji_info, cta_info, structure_info,
        frequency_info, growth_info, notes,
        opinion_candidates, opinion_mode, writing_structure, value_words,
        full_notes=full_notes
    )
    draft_path = os.path.join(process_dir, f"{safe_name}_数据底稿.md")
    with open(draft_path, "w", encoding="utf-8") as f:
        f.write(draft_content)
    print(f"  📄 数据底稿: {draft_path}")

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

    for doc_type, md_content in docs.items():
        md_name = f"{safe_name}_{doc_type}.md"

        # MD → 过程文件
        md_path = os.path.join(process_dir, md_name)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        print(f"  📄 {md_name}")

    # ---- 生成 AI蒸馏任务 ----
    task_content = gen_distill_task(
        nickname, stats, top10, category_stats, tag_freq,
        title_patterns, emoji_info, cta_info, structure_info,
        frequency_info, growth_info, notes,
        opinion_candidates, opinion_mode, writing_structure, value_words,
        full_notes=full_notes, mode=mode
    )
    task_rel_path = os.path.join("_过程文件", "原始素材", f"{safe_name}_AI蒸馏任务.md")
    task_path = os.path.join(output_dir, task_rel_path)
    with open(task_path, "w", encoding="utf-8") as f:
        f.write(task_content)
    print(f"  📋 AI蒸馏任务: {task_path}")

    # === 产出文件校验 ===
    expected_files = [task_rel_path]
    v6_ok, v6_msg = check_output_files(output_dir, expected_files)
    print(v6_msg)
    if not v6_ok:
        print("\n🚨 产出文件不完整，请检查上方错误信息并重试。")
        sys.exit(1)

    return {"task_path": task_path}


# ----------------------------------------------------------
# CLI
# ----------------------------------------------------------
if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Phase 3.5: AI 深度分析（增强版文档 + AI蒸馏任务生成）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python deep_analyze.py ./data/analysis.json "蔡不菜"
  python deep_analyze.py ./data/analysis.json "蔡不菜" -o ./output
  python deep_analyze.py ./data/analysis.json "蔡不菜" -o ./output --details ./data/notes_details.json
  python deep_analyze.py ./data/analysis.json "蔡不菜" -o ./output --mode B
        """,
    )
    parser.add_argument("analysis_path", help="分析数据JSON路径（Phase 2 输出）")
    parser.add_argument("nickname", help="博主昵称")
    parser.add_argument("-o", "--output", default=".", help="输出目录")
    parser.add_argument("--details", help="原始详情JSON路径（可选，提供更深入分析）")
    parser.add_argument("--mode", choices=["A", "B", "C"], default="A", help="蒸馏模式：A=学习TA，B=认识自己，C=v2.1预留")
    args = parser.parse_args()

    print(f"\n🔍 Phase 3.5: AI 深度分析 — {args.nickname}")
    print("=" * 50)
    print("  执行确定性分析（标题模式/CTA/Emoji/发布频率/发展趋势）...")
    print(f"  蒸馏模式：{args.mode}")
    print("  生成增强版文档（用数据洞察替换占位符）...")
    print()

    result = deep_analyze(args.analysis_path, args.nickname, args.output, args.details, mode=args.mode)

    # 兼容新旧返回结构：新版只返回 task_path，旧版含 docs
    if "docs" in result:
        ok = sum(1 for r in result["docs"] if r["ok"])
        print(f"\n完成: {ok}/{len(result['docs'])} 份增强版文档生成成功")
    print(f"\n💡 提示: 查看 {result['task_path']} 获取 AI 可补充的蒸馏任务")
