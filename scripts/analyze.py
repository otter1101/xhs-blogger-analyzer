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
    
    for item in raw_details:
        if "_error" in item:
            errors.append(item)
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

    return {
        "notes": notes,
        "stats": stats,
        "category_stats": category_stats,
        "tag_freq": tag_freq,
        "top10": top10,
        "comparison": comparison,
        "errors": errors,
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
    print(f"  总计: {s['total']}条 | 视频:{s['video_count']} 图文:{s['normal_count']} | 失败:{s['errors']}")
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
