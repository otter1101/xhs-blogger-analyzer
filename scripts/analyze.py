"""
Phase 2: 数据分析
读取爬取的笔记详情JSON，产出结构化分析数据供文档生成使用。
通用设计：内容分类基于笔记实际标签和关键词动态生成，不预设任何领域。

用法：
    python analyze.py ./data/<博主名>_notes_details.json
    python analyze.py ./data/<博主名>_notes_details.json --self ./data/<自己昵称>_notes_details.json
    python analyze.py ./data/<博主名>_notes_details.json -o ./analysis_output
"""

import json
import os
import sys
import re
import argparse
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils.common import parse_count


def extract_tags(desc):
    """从笔记描述中提取话题标签"""
    # 匹配 #标签[话题]# 或 #标签#
    tags = re.findall(r"#([^#\[\]]+?)(?:\[.*?\])?#?(?=\s|#|$)", desc or "")
    return [t.strip() for t in tags if t.strip()]


def classify_content(title, desc, tags, tag_clusters=None):
    """根据标签和内容对笔记分类（动态聚类，不预设领域）
    
    Args:
        title: 笔记标题
        desc: 笔记描述
        tags: 该笔记的标签列表
        tag_clusters: 预计算的标签→类别映射（由 build_tag_clusters 生成）
    
    Returns:
        str — 类别名称
    """
    if tag_clusters and tags:
        for tag in tags:
            if tag in tag_clusters:
                return tag_clusters[tag]
    
    # 通用兜底分类（基于内容模式，不预设领域）
    text = (title + " " + (desc or "")).lower()
    
    generic_patterns = {
        "教程/实操": ["教程", "怎么", "如何", "方法", "步骤", "实操", "实战", "手把手", "保姆级", "攻略"],
        "测评/推荐": ["测评", "推荐", "安利", "种草", "合集", "必备", "宝藏"],
        "经验分享": ["经验", "心得", "感悟", "踩坑", "总结", "复盘", "分享", "干货"],
        "作品展示": ["做了一个", "搞了一个", "上线", "成果", "作品", "完成了"],
        "日常/Vlog": ["日常", "vlog", "一天", "记录", "打卡"],
    }
    
    for cat, keywords in generic_patterns.items():
        for kw in keywords:
            if kw in text:
                return cat
    return "其他"


def build_tag_clusters(all_notes_tags, top_n=8):
    """从全量笔记标签中动态提取内容类别
    
    策略：取最高频的 top_n 个标签作为类别名，
    然后将每条笔记按其包含的最高频标签归类。
    
    Args:
        all_notes_tags: List[List[str]] — 每条笔记的标签列表
        top_n: 取前几个高频标签作为类别
    
    Returns:
        dict — { tag: category_name } 的映射
    """
    # 统计所有标签频次
    tag_counter = Counter()
    for tags in all_notes_tags:
        tag_counter.update(tags)
    
    # 取 top N 作为类别
    top_tags = [tag for tag, _ in tag_counter.most_common(top_n)]
    
    # 构建映射：每个标签映射到它最接近的 top 类别
    # 简单策略：top 标签直接作为类别名
    cluster_map = {}
    for tag in top_tags:
        cluster_map[tag] = tag  # 标签本身就是类别名
    
    return cluster_map


# ----------------------------------------------------------
# 认知层提取函数
# ----------------------------------------------------------

def extract_opinion_sentences(notes):
    """D1：从全量笔记正文中提取观点句候选"""
    opinion_keywords = {
        "判断词": ["我觉得", "我认为", "其实", "本质上", "说白了", "归根结底",
                   "核心是", "关键在于", "真正的", "最重要的"],
        "转折": ["但其实", "然而", "不是…而是", "不是...而是", "与其", "看起来",
                 "实际上", "大家都说", "表面上"],
        "总结": ["所以", "因此", "这说明", "这意味着", "一句话概括",
                 "总结一下", "换句话说"],
    }

    candidates = []
    for note in notes:
        desc = note.get("desc", "") or ""
        if not desc:
            continue
        # 按句子分割
        sentences = re.split(r"[。！？\n]", desc)
        for sent in sentences:
            sent = sent.strip()
            if len(sent) < 8:
                continue
            for match_type, keywords in opinion_keywords.items():
                if any(kw in sent for kw in keywords):
                    candidates.append({
                        "sentence": sent[:120],
                        "source_note_id": note.get("id", ""),
                        "source_title": note.get("title", "")[:30],
                        "source_likes": note.get("likes_raw", "?"),
                        "match_type": match_type,
                    })
                    break  # 每句只记一次

    mode = "script_filtered" if len(candidates) >= 10 else "full_text"
    return candidates, mode


def analyze_writing_structure(notes):
    """D2：统计开头/结尾类型（追加到现有结构分析）"""
    opening_patterns = {
        "故事开头": ["那天", "记得", "有一次", "上周", "上个月", "去年", "小时候", "从前"],
        "反问开头": ["你有没有", "你是不是", "为什么", "凭什么", "难道", "真的吗", "？"],
        "数据开头": ["%", "万", "个", "次", "元", "块", "倍", "调查", "数据"],
        "自嘲开头": ["我这个", "作为一个", "承认", "说实话", "坦白"],
        "观点直抛": ["我觉得", "我认为", "其实", "本质上", "说白了"],
    }
    ending_patterns = {
        "金句收尾": ["就是", "才是", "而已", "罢了", "本质", "归根"],
        "行动号召": ["关注", "收藏", "点赞", "试试", "去做", "行动"],
        "开放提问": ["你呢", "你觉得", "评论区", "留言", "告诉我", "你们"],
        "总结回顾": ["总结", "所以", "因此", "最后", "希望"],
    }

    opening_counts = {k: 0 for k in opening_patterns}
    ending_counts = {k: 0 for k in ending_patterns}

    for note in notes:
        desc = note.get("desc", "") or ""
        if not desc:
            continue
        head = desc[:50]
        tail = desc[-50:]
        for ptype, keywords in opening_patterns.items():
            if any(kw in head for kw in keywords):
                opening_counts[ptype] += 1
                break
        for ptype, keywords in ending_patterns.items():
            if any(kw in tail for kw in keywords):
                ending_counts[ptype] += 1
                break

    return {
        "opening_types": {k: v for k, v in opening_counts.items() if v > 0},
        "ending_types": {k: v for k, v in ending_counts.items() if v > 0},
    }


def extract_value_words(notes):
    """D3：方案B，从正文提取高频2-4字词，不用预设词库"""
    stopwords = set("的了是在我你他她它们这那有也都就和与或但而被把让给对从到为以及等中上下里外前后左右啊呢吧哦哈嗯")
    stop_phrases = {"时候", "自己", "觉得", "一个", "一些", "一下", "一样", "一直", "一起",
                    "可以", "没有", "什么", "这个", "那个", "这样", "那样", "如果", "因为",
                    "所以", "但是", "然后", "还是", "已经", "非常", "真的", "感觉", "知道",
                    "现在", "时间", "东西", "事情", "问题", "方法", "内容", "大家", "我们",
                    "他们", "她们", "你们", "很多", "一点", "有点", "有些", "其实", "只是"}

    word_counter = Counter()
    for note in notes:
        desc = note.get("desc", "") or ""
        if not desc:
            continue
        # 先删除话题标签（#标签[话题]# 和 #标签 两种格式）
        desc = re.sub(r"#[^#\s]+?(?:\[.*?\])?#?", "", desc)
        # 切词
        tokens = re.split(r"[\s，。！？、；：""''【】《》\(\)（）\[\]…—\-/\\|]", desc)
        for token in tokens:
            token = token.strip()
            if 2 <= len(token) <= 4:
                # 只保留纯汉字，过滤emoji/数字/英文/符号
                if not re.match(r"^[\u4e00-\u9fff]+$", token):
                    continue
                if token in stop_phrases:
                    continue
                word_counter[token] += 1

    return [{"word": w, "count": c} for w, c in word_counter.most_common(15)]


# ----------------------------------------------------------
# 核心分析逻辑
# ----------------------------------------------------------
def analyze_notes(details_path, self_details_path=None):
    """
    分析笔记数据，返回完整分析结果。
    
    Args:
        details_path: 目标博主的详情JSON路径
        self_details_path: 自己账号的详情JSON路径（可选）
    
    Returns:
        dict — 包含所有分析维度的结构化数据
    """
    with open(details_path, "r", encoding="utf-8") as f:
        raw_details = json.load(f)

    # 解析笔记数据
    notes = []
    errors = []
    restricted_notes = []  # 内容获取受限的笔记（保留标题供选题分析参考）

    for item in raw_details:
        if "_error" in item:
            errors.append(item)
            if item.get("_content_restricted"):
                restricted_notes.append({
                    "id": item.get("_feed_id", ""),
                    "title": item.get("_title", ""),
                    "restricted": True,
                })
            continue
        
        # 兼容两种数据格式
        note = item.get("data", {}).get("note", item)
        interact = note.get("interactInfo", item.get("interactInfo", {}))
        comments_data = item.get("data", {}).get("comments", item.get("comments", {}))
        comment_list = comments_data.get("list", []) if isinstance(comments_data, dict) else []
        
        tags = extract_tags(note.get("desc", ""))
        notes.append({
            "id": note.get("noteId", item.get("_feed_id", "")),
            "title": note.get("title", note.get("displayTitle", "")),
            "desc": note.get("desc", ""),
            "type": note.get("type", "normal"),
            "likes": parse_count(interact.get("likedCount", "0")),
            "likes_raw": str(interact.get("likedCount", "0")),
            "collects": parse_count(interact.get("collectedCount", "0")),
            "collects_raw": str(interact.get("collectedCount", "0")),
            "comments_count": parse_count(interact.get("commentCount", "0")),
            "comments_raw": str(interact.get("commentCount", "0")),
            "shares": parse_count(interact.get("sharedCount", "0")),
            "comment_list": comment_list,
            "tags": tags,
            "category": "",  # 先留空，后面动态分类
            "time": note.get("time", 0),
        })
    
    # 动态构建标签聚类 → 内容分类
    all_notes_tags = [n["tags"] for n in notes]
    tag_clusters = build_tag_clusters(all_notes_tags)
    
    for n in notes:
        n["category"] = classify_content(n["title"], n["desc"], n["tags"], tag_clusters)
    
    # 按赞排序
    notes.sort(key=lambda x: x["likes"], reverse=True)

    # ---- 基础统计 ----
    total = len(notes)
    video_count = sum(1 for n in notes if n["type"] == "video")
    normal_count = total - video_count
    total_likes = sum(n["likes"] for n in notes)
    total_collects = sum(n["collects"] for n in notes)
    total_comments = sum(n["comments_count"] for n in notes)
    
    stats = {
        "total": total,
        "errors": len(errors),
        "restricted": len(restricted_notes),
        "video_count": video_count,
        "normal_count": normal_count,
        "total_likes": total_likes,
        "total_collects": total_collects,
        "total_comments": total_comments,
        "avg_likes": total_likes // total if total else 0,
        "avg_collects": total_collects // total if total else 0,
        "avg_comments": total_comments // total if total else 0,
    }

    # ---- 内容领域分布 ----
    category_dist = Counter(n["category"] for n in notes)
    category_stats = {}
    for cat, count in category_dist.most_common():
        cat_notes = [n for n in notes if n["category"] == cat]
        cat_likes = sum(n["likes"] for n in cat_notes)
        category_stats[cat] = {
            "count": count,
            "pct": round(count / total * 100, 1) if total else 0,
            "avg_likes": cat_likes // len(cat_notes) if cat_notes else 0,
            "top_note": cat_notes[0]["title"] if cat_notes else "",
        }

    # ---- 标签统计 ----
    all_tags = []
    for n in notes:
        all_tags.extend(n["tags"])
    tag_freq = Counter(all_tags).most_common(20)

    # ---- TOP10 + 评论洞察 ----
    top10 = []
    for n in notes[:10]:
        top_comments = []
        for c in n["comment_list"][:5]:
            comment_info = {
                "content": c.get("content", "")[:100],
                "likes": c.get("likeCount", "0"),
                "user": c.get("userInfo", {}).get("nickname", "?"),
                "is_author": "is_author" in str(c.get("showTags", [])),
                "sub_comments": [],
            }
            for sc in (c.get("subComments") or [])[:2]:
                comment_info["sub_comments"].append({
                    "content": sc.get("content", "")[:80],
                    "user": sc.get("userInfo", {}).get("nickname", "?"),
                    "is_author": "is_author" in str(sc.get("showTags", [])),
                })
            top_comments.append(comment_info)
        
        top10.append({
            **n,
            "comment_list": top_comments,  # 替换为精简版
        })

    # ---- 对比分析（如果有自己的数据）----
    comparison = None
    if self_details_path and os.path.exists(self_details_path):
        self_analysis = analyze_notes(self_details_path)
        comparison = {
            "self_stats": self_analysis["stats"],
            "target_stats": stats,
        }

    # ---- 认知层提取 ----
    opinion_candidates, opinion_mode = extract_opinion_sentences(notes)
    writing_structure = analyze_writing_structure(notes)
    value_words = extract_value_words(notes)

    return {
        "notes": notes,
        "stats": stats,
        "category_stats": category_stats,
        "tag_freq": tag_freq,
        "top10": top10,
        "comparison": comparison,
        "errors": errors,
        "restricted_notes": restricted_notes,
        "opinion_candidates": opinion_candidates,
        "opinion_extraction_mode": opinion_mode,
        "writing_structure": writing_structure,
        "value_words": value_words,
    }


# ----------------------------------------------------------
# CLI
# ----------------------------------------------------------
if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="笔记数据分析")
    parser.add_argument("details_path", help="笔记详情JSON路径")
    parser.add_argument("--self", dest="self_path", help="自己账号的详情JSON路径")
    parser.add_argument("-o", "--output", default=".", help="输出目录")
    args = parser.parse_args()

    print("📊 开始分析...")
    result = analyze_notes(args.details_path, args.self_path)
    
    # 打印摘要
    s = result["stats"]
    print(f"\n{'='*60}")
    print(f"  总计: {s['total']}条 | 视频:{s['video_count']} 图文:{s['normal_count']} | 失败:{s['errors']} | 受限(仅标题):{s.get('restricted', 0)}")
    print(f"  总赞: {s['total_likes']:,} | 总收藏: {s['total_collects']:,} | 总评论: {s['total_comments']:,}")
    print(f"  均赞: {s['avg_likes']:,} | 均收藏: {s['avg_collects']:,} | 均评论: {s['avg_comments']:,}")
    
    print(f"\n  内容领域分布:")
    for cat, cs in result["category_stats"].items():
        print(f"    {cat}: {cs['count']}条 ({cs['pct']}%) 均赞{cs['avg_likes']:,}")
    
    print(f"\n  TOP5 标签: {', '.join(f'#{t[0]}({t[1]})' for t in result['tag_freq'][:5])}")
    
    print(f"\n  TOP5 笔记:")
    for i, n in enumerate(result["top10"][:5]):
        print(f"    {i+1}. [{n['likes_raw']}赞] {n['title'][:40]}")
    print(f"{'='*60}")
    
    # 保存分析数据
    out_name = os.path.splitext(os.path.basename(args.details_path))[0].replace("_notes_details", "_analysis")
    out_path = os.path.join(args.output, f"{out_name}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        # 保存精简版 notes（含 category，但去掉 comment_list 和 desc 减小体积）
        save_notes = []
        for n in result["notes"]:
            save_notes.append({
                "id": n["id"],
                "title": n["title"],
                "type": n["type"],
                "likes": n["likes"],
                "likes_raw": n["likes_raw"],
                "collects": n["collects"],
                "collects_raw": n["collects_raw"],
                "comments_count": n["comments_count"],
                "comments_raw": n["comments_raw"],
                "shares": n["shares"],
                "tags": n["tags"],
                "category": n["category"],
                "time": n["time"],
            })
        save_data = {k: v for k, v in result.items() if k != "notes"}
        save_data["notes"] = save_notes
        save_data["notes_count"] = len(save_notes)
        json.dump(save_data, f, ensure_ascii=False, indent=2)

    print(f"\n💾 分析数据: {out_path}")

    # ---- 终端打印认知层数据供人工筛选 ----
    candidates = result["opinion_candidates"]
    mode = result["opinion_extraction_mode"]
    print(f"\n{'='*60}")
    print(f"  观点句候选（共 {len(candidates)} 条，模式：{mode}）")
    print(f"{'='*60}")
    for i, c in enumerate(candidates):
        print(f"  {i+1:3d}. [{c['match_type']}] {c['sentence']}")
        print(f"       — 《{c['source_title']}》({c['source_likes']}赞)")

    print(f"\n{'='*60}")
    print(f"  高频词 TOP15（未筛选，含通用词）")
    print(f"{'='*60}")
    vw = result["value_words"]
    print("  " + " / ".join(f"{v['word']}({v['count']}次)" for v in vw))
    print(f"{'='*60}\n")
