"""
Phase 1: 通用博主数据采集
输入博主名或user_id，自动爬取全量笔记（主页+多关键词搜索+逐条详情）。
适用于任何领域的博主，不限于特定赛道。

用法：
    python crawl_blogger.py "<博主名>"
    python crawl_blogger.py "<博主名>" --output ./data
    python crawl_blogger.py --user-id <user_id> --output ./data
    python crawl_blogger.py "<博主名>" --self                     # 标记为自己账号
    python crawl_blogger.py "<博主名>" --keywords "烘焙,食谱,探店"  # 指定领域关键词
"""

import json
import os
import sys
import time
import argparse
import re
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils.mcp_client import MCPClient, MCPError
from utils.common import parse_count, safe_filename


# ----------------------------------------------------------
# Step 1: 搜索定位博主
# ----------------------------------------------------------
def find_blogger(client, keyword):
    """通过关键词搜索定位目标博主，返回 (user_id, nickname, xsec_token)
    
    匹配策略（按优先级）：
    1. 昵称精确匹配
    2. 昵称包含关键词
    3. 出现频次最高的作者（兜底）
    """
    print(f"\n🔍 搜索博主: {keyword}")
    data = client.call("search_feeds", {"keyword": keyword})
    
    feeds = data.get("feeds", [])
    if not feeds:
        raise MCPError(f"搜索 '{keyword}' 无结果")

    # 统计各作者出现次数 + 信息
    author_counts = {}
    author_info = {}
    for feed in feeds:
        card = feed.get("noteCard", {})
        user = card.get("user", {})
        uid = user.get("userId", "")
        if uid:
            author_counts[uid] = author_counts.get(uid, 0) + 1
            if uid not in author_info:
                author_info[uid] = {
                    "userId": uid,
                    "nickname": user.get("nickname", ""),
                    "xsecToken": feed.get("xsecToken", ""),
                }

    if not author_info:
        raise MCPError(f"搜索结果中未找到任何作者")

    # 优先级1: 昵称精确匹配
    for uid, info in author_info.items():
        if info["nickname"] == keyword:
            print(f"  ✅ 精确匹配: {info['nickname']} (ID: {uid})")
            return uid, info["nickname"], info["xsecToken"]

    # 优先级2: 昵称包含关键词
    for uid, info in author_info.items():
        if keyword in info["nickname"] or info["nickname"] in keyword:
            print(f"  🔍 模糊匹配: {info['nickname']} (ID: {uid}, 出现{author_counts[uid]}次)")
            return uid, info["nickname"], info["xsecToken"]

    # 优先级3: 按出现次数排序，取最频繁的（兜底）
    sorted_authors = sorted(author_counts.items(), key=lambda x: x[1], reverse=True)
    top_uid = sorted_authors[0][0]
    info = author_info[top_uid]
    
    print(f"  ⚠️ 未找到精确匹配，按频次选择: {info['nickname']} (ID: {top_uid}, 出现{sorted_authors[0][1]}次)")
    return top_uid, info["nickname"], info["xsecToken"]


# ----------------------------------------------------------
# Step 2: 获取主页信息 + 笔记列表
# ----------------------------------------------------------
def get_profile(client, user_id, xsec_token):
    """获取博主主页信息和笔记列表"""
    print(f"\n📋 获取主页信息...")
    data = client.call("user_profile", {"user_id": user_id, "xsec_token": xsec_token})
    
    info = data.get("userBasicInfo", {})
    interactions = data.get("interactions", [])
    
    print(f"  昵称: {info.get('nickname', '?')}")
    for i in interactions:
        print(f"  {i.get('name', '?')}: {i.get('count', '?')}")
    
    # 提取主页笔记
    notes = {}
    for feed in data.get("feeds", []):
        nid = feed.get("id", "")
        card = feed.get("noteCard", {})
        interact = card.get("interactInfo", {})
        if nid:
            notes[nid] = {
                "id": nid,
                "xsecToken": feed.get("xsecToken", ""),
                "title": card.get("displayTitle", ""),
                "type": card.get("type", ""),
                "likedCount": parse_count(interact.get("likedCount", "0")),
                "source": "profile",
            }
    
    print(f"  主页笔记: {len(notes)} 条")
    return data, notes


# ----------------------------------------------------------
# Step 3: 多关键词搜索补充
# ----------------------------------------------------------
def search_supplement(client, keyword, user_id, existing_notes, extra_keywords=None):
    """通过多个关键词搜索补充遗漏笔记
    
    Args:
        extra_keywords: 用户指定的领域关键词列表（如 ["烘焙", "食谱"]）。
                       未指定时使用通用搜索策略。
    """
    # 生成搜索关键词：博主名 + 领域词组合
    base_keywords = [keyword]
    
    if extra_keywords:
        # 用户指定了领域关键词 → 按用户指定的来
        for ek in extra_keywords:
            base_keywords.append(f"{keyword} {ek}")
    else:
        # 未指定 → 使用通用后缀（适用于任何领域）
        generic_suffixes = ["教程", "推荐", "分享", "测评", "攻略", "合集"]
        for suffix in generic_suffixes:
            base_keywords.append(f"{keyword} {suffix}")
    
    print(f"\n🔎 多关键词搜索补充 (当前 {len(existing_notes)} 条)")
    new_total = 0
    
    for kw in base_keywords:
        try:
            data = client.call("search_feeds", {"keyword": kw})
            feeds = data.get("feeds", [])
            new_count = 0
            
            for feed in feeds:
                nid = feed.get("id", "")
                card = feed.get("noteCard", {})
                user = card.get("user", {})
                interact = card.get("interactInfo", {})
                
                if user.get("userId") == user_id and nid and nid not in existing_notes:
                    existing_notes[nid] = {
                        "id": nid,
                        "xsecToken": feed.get("xsecToken", ""),
                        "title": card.get("displayTitle", ""),
                        "type": card.get("type", ""),
                        "likedCount": parse_count(interact.get("likedCount", "0")),
                        "source": f"search:{kw}",
                    }
                    new_count += 1
            
            if new_count > 0:
                print(f"  '{kw}' → +{new_count} 条新笔记")
                new_total += new_count
            
            time.sleep(1.5)  # 防风控
        except Exception as e:
            print(f"  '{kw}' 出错: {e}")
            time.sleep(2)
    
    print(f"  共新增 {new_total} 条，总计 {len(existing_notes)} 条")
    return existing_notes


# ----------------------------------------------------------
# Step 4: 逐条获取详情
# ----------------------------------------------------------
def get_all_details(client, notes_dict, output_dir, blogger_name):
    """逐条获取笔记详情，每10条checkpoint"""
    notes_list = sorted(notes_dict.values(), key=lambda x: x.get("likedCount", 0), reverse=True)
    total = len(notes_list)
    
    print(f"\n📖 批量获取 {total} 条笔记详情...")
    print("=" * 60)
    
    details = []
    ok_count = 0
    err_count = 0
    checkpoint_path = os.path.join(output_dir, f"{safe_filename(blogger_name)}_details_partial.json")
    
    for i, note in enumerate(notes_list):
        nid = note["id"]
        token = note.get("xsecToken", "")
        title = note.get("title", "N/A")[:30]
        print(f"  [{i+1:3d}/{total}] {title}...", end="", flush=True)
        
        try:
            detail = client.call("get_feed_detail", {
                "feed_id": nid,
                "xsec_token": token,
                "load_all_comments": True,
                "limit": 30,
                "click_more_replies": False,
            }, timeout=90)
            
            detail["_meta"] = {
                "source": note.get("source"),
                "idx": i,
                "list_title": note.get("title"),
            }
            details.append(detail)
            
            # 尝试提取互动数据
            interact = detail.get("interactInfo", {})
            if not interact:
                # 可能在 data.note 里
                note_data = detail.get("data", {}).get("note", {})
                interact = note_data.get("interactInfo", {})
            
            print(f" ✅ L:{interact.get('likedCount','?')} C:{interact.get('collectedCount','?')}")
            ok_count += 1
        except Exception as e:
            err_str = str(e)[:50]
            print(f" ❌ {err_str}")
            details.append({"_feed_id": nid, "_error": str(e), "_title": note.get("title")})
            err_count += 1
        
        time.sleep(3)  # 防风控间隔
        
        # 每10条做一次checkpoint
        if (i + 1) % 10 == 0:
            with open(checkpoint_path, "w", encoding="utf-8") as f:
                json.dump(details, f, ensure_ascii=False, indent=2)
            print(f"  --- checkpoint: {ok_count}✅ {err_count}❌ ---")
    
    print(f"\n完成: {ok_count}✅ {err_count}❌ / 共{total}条")
    
    # 清理checkpoint
    if os.path.exists(checkpoint_path):
        os.remove(checkpoint_path)
    
    return details


# ----------------------------------------------------------
# 主流程
# ----------------------------------------------------------
def crawl_blogger(keyword=None, user_id=None, output_dir=None, port=18060, is_self=False, extra_keywords=None):
    """
    完整爬取一个博主的全量数据。
    
    Args:
        keyword: 博主搜索关键词（和user_id二选一）
        user_id: 直接指定user_id
        output_dir: 数据输出目录
        port: MCP端口
        is_self: 是否标记为自己的账号
        extra_keywords: 领域关键词列表（如 ["烘焙", "食谱"]），用于搜索补充
    
    Returns:
        dict — { profile, notes_list, details, nickname, user_id }
    """
    client = MCPClient(port=port)
    
    # 定位博主
    xsec_token = ""
    nickname = keyword or ""
    
    if user_id:
        # 直接用user_id，但仍需搜索获取xsec_token
        if keyword:
            _, nickname, xsec_token = find_blogger(client, keyword)
        else:
            nickname = user_id[:12]
    else:
        user_id, nickname, xsec_token = find_blogger(client, keyword)
    
    # 设置输出目录
    if not output_dir:
        output_dir = os.getcwd()
    os.makedirs(output_dir, exist_ok=True)
    safe_name = safe_filename(nickname)
    
    print(f"\n{'='*60}")
    print(f"{'👤 自己' if is_self else '🎯 目标'}: {nickname} ({user_id})")
    print(f"{'='*60}")
    
    # 获取主页
    profile, notes = get_profile(client, user_id, xsec_token)
    
    # 搜索补充
    notes = search_supplement(client, keyword or nickname, user_id, notes, extra_keywords)
    
    # 保存笔记列表
    notes_list = sorted(notes.values(), key=lambda x: x.get("likedCount", 0), reverse=True)
    list_path = os.path.join(output_dir, f"{safe_name}_notes_list.json")
    with open(list_path, "w", encoding="utf-8") as f:
        json.dump(notes_list, f, ensure_ascii=False, indent=2)
    print(f"\n💾 笔记列表: {list_path} ({len(notes_list)}条)")
    
    # 保存主页信息
    profile_path = os.path.join(output_dir, f"{safe_name}_profile.json")
    with open(profile_path, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)
    
    # 获取全部详情
    details = get_all_details(client, notes, output_dir, nickname)
    
    # 保存详情
    details_path = os.path.join(output_dir, f"{safe_name}_notes_details.json")
    with open(details_path, "w", encoding="utf-8") as f:
        json.dump(details, f, ensure_ascii=False, indent=2)
    
    ok_count = len([d for d in details if "_error" not in d])
    print(f"\n💾 笔记详情: {details_path} ({ok_count}条有效)")
    
    return {
        "profile": profile,
        "notes_list": notes_list,
        "details": details,
        "nickname": nickname,
        "user_id": user_id,
        "is_self": is_self,
        "output_dir": output_dir,
    }


# ----------------------------------------------------------
# CLI 入口
# ----------------------------------------------------------
if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="小红书博主数据采集（通用，适用于任何领域）")
    parser.add_argument("keyword", nargs="?", help="博主搜索关键词")
    parser.add_argument("--user-id", help="直接指定user_id")
    parser.add_argument("--output", "-o", default=".", help="数据输出目录")
    parser.add_argument("--port", type=int, default=18060, help="MCP端口")
    parser.add_argument("--self", dest="is_self", action="store_true", help="标记为自己账号")
    parser.add_argument("--keywords", help="领域关键词（逗号分隔），用于搜索补充。如：烘焙,食谱,探店")
    args = parser.parse_args()

    if not args.keyword and not args.user_id:
        parser.error("请指定博主关键词或 --user-id")

    # 解析领域关键词
    extra_keywords = None
    if args.keywords:
        extra_keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]

    start = time.time()
    result = crawl_blogger(
        keyword=args.keyword,
        user_id=args.user_id,
        output_dir=args.output,
        port=args.port,
        is_self=args.is_self,
        extra_keywords=extra_keywords,
    )
    elapsed = time.time() - start
    
    print(f"\n{'='*60}")
    print(f"🎉 采集完成! 用时 {elapsed:.0f}秒")
    print(f"   博主: {result['nickname']}")
    print(f"   笔记: {len(result['notes_list'])}条")
    print(f"   详情: {len([d for d in result['details'] if '_error' not in d])}条有效")
    print(f"   输出: {result['output_dir']}")
    print(f"{'='*60}")
