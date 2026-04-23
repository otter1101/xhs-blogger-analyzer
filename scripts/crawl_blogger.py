import sys as _sys, io as _io  # noqa: E402  — Windows GBK 终端 emoji 兼容
if _sys.stdout and hasattr(_sys.stdout, 'buffer') and getattr(_sys.stdout, 'encoding', '').lower() not in ('utf-8', 'utf8'):
    try:
        _sys.stdout = _io.TextIOWrapper(_sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
    except (ValueError, AttributeError):
        pass
if _sys.stderr and hasattr(_sys.stderr, 'buffer') and getattr(_sys.stderr, 'encoding', '').lower() not in ('utf-8', 'utf8'):
    try:
        _sys.stderr = _io.TextIOWrapper(_sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True)
    except (ValueError, AttributeError):
        pass

"""
Phase 1: 通用博主数据采集（TikHub API 版）
输入博主名或user_id，自动爬取全量笔记（主页+多关键词搜索+逐条详情）。
适用于任何领域的博主，不限于特定赛道。

数据源：TikHub REST API（https://api.tikhub.io）
认证：Bearer Token（环境变量 TIKHUB_API_TOKEN 或 --token 参数）

用法：
    python crawl_blogger.py "<博主名>"
    python crawl_blogger.py "<博主名>" --output ./data
    python crawl_blogger.py --user-id <user_id> --output ./data
    python crawl_blogger.py "<博主名>" --self                     # 标记为自己账号
    python crawl_blogger.py "<博主名>" --keywords "烘焙,食谱,探店"  # 指定领域关键词
    python crawl_blogger.py "<博主名>" --token "你的TikHub Token"  # 指定 Token
"""

import json
import os
import sys
import time
import argparse
import re
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils.tikhub_client import TikHubClient, TikHubError
from utils.common import parse_count, safe_filename
from utils.privacy import PRIVACY_VERSION, anonymize_note_comments_inplace
from verify import (check_content_completeness, check_note_count,
                    check_time_field, check_duplicates, get_sample_watermark)


# ----------------------------------------------------------
# Step 1: 搜索定位博主
# ----------------------------------------------------------
def _extract_feeds_from_search(data):
    """从 TikHub web/search_notes 响应中提取 feeds 列表
    
    响应结构（adapter 归一化后统一为 web_v3 金标准格式）:
      { data: { data: { items: [ { id, noteCard: { user: {...}, ... }, xsecToken } ] } } }
    
    也兼容旧格式:
      { data: { data: { items: [ { model_type: "note", note: {...} } ] } } }
    """
    d = data.get("data", data)
    if isinstance(d, dict) and "data" in d and isinstance(d["data"], dict):
        d = d["data"]
    items = d.get("items") or d.get("notes") or d.get("feeds") or []
    if not isinstance(items, list):
        return []
    # 展开嵌套结构：noteCard（web_v3 金标准）或 note（旧格式）
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        # web_v3 归一化格式：{ id, noteCard: {...}, xsecToken }
        note_card = item.get("noteCard") or item.get("note_card")
        if isinstance(note_card, dict) and note_card:
            # 把 item 顶层的 id/xsecToken 注入到 noteCard 中，方便下游提取
            feed = dict(note_card)
            if "id" not in feed and item.get("id"):
                feed["id"] = item["id"]
            if not feed.get("xsec_token") and not feed.get("xsecToken"):
                feed["xsecToken"] = item.get("xsecToken") or item.get("xsec_token") or ""
            result.append(feed)
        elif "note" in item:
            result.append(item["note"])
        else:
            result.append(item)
    return result


def _extract_user_from_feed(feed):
    """从单条搜索结果中提取 (user_id, nickname, xsec_token)
    
    兼容两种结构：
      1. 展开后的 noteCard 内容（user 在顶层）: { user: { userid, nickname }, ... }
      2. 原始 item（user 在 noteCard 内）:       { noteCard: { user: {...} }, ... }
    """
    user = feed.get("user") or {}
    # 如果顶层 user 为空或没有 uid，尝试从 noteCard/note_card 中提取
    if not (isinstance(user, dict) and (user.get("userid") or user.get("userId") or user.get("user_id"))):
        note_card = feed.get("noteCard") or feed.get("note_card") or {}
        if isinstance(note_card, dict):
            user = note_card.get("user") or user
    uid = user.get("userid") or user.get("userId") or user.get("user_id") or ""
    nickname = user.get("nickname") or user.get("nick_name") or user.get("nickName") or ""
    xsec_token = feed.get("xsec_token") or feed.get("xsecToken") or ""
    return uid, nickname, xsec_token


def _extract_interact_from_feed(feed):
    """从单条搜索结果中提取互动信息
    
    兼容两种结构：
      1. 展开后的 noteCard 内容: { interactInfo: { likedCount, ... }, liked_count, ... }
      2. 原始 item:              { noteCard: { interactInfo: {...} } }
    """
    # 先尝试 interactInfo 嵌套结构（noteCard 展开后的格式）
    interact = feed.get("interactInfo") or feed.get("interact_info") or {}
    if isinstance(interact, dict) and interact:
        return {
            "liked_count": interact.get("likedCount") or interact.get("liked_count") or 0,
            "collected_count": interact.get("collectedCount") or interact.get("collected_count") or 0,
            "shared_count": interact.get("shareCount") or interact.get("sharedCount") or interact.get("shared_count") or 0,
        }
    # 再尝试从 noteCard 内部取
    note_card = feed.get("noteCard") or feed.get("note_card") or {}
    if isinstance(note_card, dict):
        interact = note_card.get("interactInfo") or note_card.get("interact_info") or {}
        if isinstance(interact, dict) and interact:
            return {
                "liked_count": interact.get("likedCount") or interact.get("liked_count") or 0,
                "collected_count": interact.get("collectedCount") or interact.get("collected_count") or 0,
                "shared_count": interact.get("shareCount") or interact.get("sharedCount") or interact.get("shared_count") or 0,
            }
    # 最后回退到顶层扁平字段
    return {
        "liked_count": feed.get("liked_count") or feed.get("likedCount") or 0,
        "collected_count": feed.get("collected_count") or feed.get("collectedCount") or 0,
        "shared_count": feed.get("shared_count") or feed.get("sharedCount") or 0,
    }


def _extract_users_from_search_users(data):
    """从 TikHub web/search_users 响应中提取用户列表
    
    响应结构: { data: { data: { items: [ { user_info: { ... } } ] } } }
    或者:     { data: { data: { users: [ ... ] } } }
    """
    d = data.get("data", data)
    if isinstance(d, dict) and "data" in d and isinstance(d["data"], dict):
        d = d["data"]
    # 尝试多种字段
    items = d.get("items") or d.get("users") or d.get("user_list") or []
    if not isinstance(items, list):
        return []
    return items


def find_blogger(client, keyword):
    """通过搜索用户精准定位目标博主，返回 (user_id, nickname, xsec_token)
    
    匹配策略（按优先级）：
    1. 【首选】调用 search_users 端点，直接搜用户名精准匹配
    2. 【兜底】若 search_users 失败，回退到 search_notes 交叉定位
    """
    print(f"\n🔍 搜索博主: {keyword}")
    
    # ========== 首选：search_users 精准匹配 ==========
    try:
        user_data = client.search_users(keyword)
        users = _extract_users_from_search_users(user_data)
        
        if users:
            print(f"  📋 搜索用户端点返回 {len(users)} 个结果")
            
            # 提取用户信息（兼容多种字段结构）
            # web/search_users 返回: { name, id, red_id, desc, sub_title("粉丝 80.7万"), ... }
            candidates = []
            for item in users:
                u = item.get("user_info") or item.get("user") or item
                uid = (u.get("id") or u.get("user_id") or u.get("userid") or u.get("userId") or "")
                nick = (u.get("name") or u.get("nickname") or u.get("nick_name") or "")
                xsec = (u.get("xsec_token") or u.get("xsecToken") or "")
                # 解析粉丝数（从 sub_title "粉丝 80.7万" 中提取）
                sub_title = u.get("sub_title") or ""
                fans = parse_count(sub_title.replace("粉丝", "").strip()) if "粉丝" in sub_title else 0
                if uid:
                    candidates.append({"uid": uid, "nickname": nick, "xsec": xsec, "fans": fans})
            
            if candidates:
                # 精确匹配昵称的候选（可能有多个同名）
                exact = [c for c in candidates if c["nickname"] == keyword]
                if exact:
                    # 多个同名 → 取粉丝最多的（真正的博主）
                    best = max(exact, key=lambda x: x["fans"])
                    print(f"  ✅ 精确匹配用户: {best['nickname']} (ID: {best['uid']}, 粉丝≈{best['fans']})")
                    return best["uid"], best["nickname"], best["xsec"]
                
                # 模糊匹配
                fuzzy = [c for c in candidates if keyword in c["nickname"] or c["nickname"] in keyword]
                if fuzzy:
                    best = max(fuzzy, key=lambda x: x["fans"])
                    print(f"  🔍 模糊匹配用户: {best['nickname']} (ID: {best['uid']}, 粉丝≈{best['fans']})")
                    return best["uid"], best["nickname"], best["xsec"]
                
                # 取粉丝最多的
                best = max(candidates, key=lambda x: x["fans"])
                print(f"  ⚠️ 无精确匹配，取粉丝最多: {best['nickname']} (ID: {best['uid']}, 粉丝≈{best['fans']})")
                return best["uid"], best["nickname"], best["xsec"]
        else:
            print(f"  ℹ️ search_users 无结果，回退到 search_notes")
    except Exception as e:
        print(f"  ⚠️ search_users 调用失败({e})，回退到 search_notes")
    
    # ========== 兜底：search_notes 交叉定位 ==========
    data = client.search_notes(keyword)
    feeds = _extract_feeds_from_search(data)
    if not feeds:
        raise TikHubError(f"搜索 '{keyword}' 无结果（search_users 和 search_notes 均无结果）")

    # 统计各作者出现次数 + 信息
    author_counts = {}
    author_info = {}
    for feed in feeds:
        uid, nickname, xsec_token = _extract_user_from_feed(feed)
        if uid:
            author_counts[uid] = author_counts.get(uid, 0) + 1
            if uid not in author_info:
                author_info[uid] = {
                    "userId": uid,
                    "nickname": nickname,
                    "xsecToken": xsec_token,
                }

    if not author_info:
        raise TikHubError(f"搜索结果中未找到任何作者")

    # 优先级1: 昵称精确匹配
    for uid, info in author_info.items():
        if info["nickname"] == keyword:
            print(f"  ✅ 精确匹配(笔记): {info['nickname']} (ID: {uid})")
            return uid, info["nickname"], info["xsecToken"]

    # 优先级2: 昵称包含关键词
    for uid, info in author_info.items():
        if keyword in info["nickname"] or info["nickname"] in keyword:
            print(f"  🔍 模糊匹配(笔记): {info['nickname']} (ID: {uid}, 出现{author_counts[uid]}次)")
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
    """获取博主主页信息和笔记列表（TikHub Web API）"""
    print(f"\n📋 获取主页信息...")
    
    # --- 获取用户基础信息 ---
    user_data = {}  # 提前初始化，防止 except 分支里引用未定义变量
    try:
        user_raw = client.fetch_user_info(user_id)
        user_data = user_raw.get("data", user_raw)
        if isinstance(user_data, dict) and "data" in user_data:
            user_data = user_data["data"]
        
        # web/get_user_info 可能返回 { result: { success, code, message }, ... }
        # 或者直接返回用户数据
        if isinstance(user_data, dict) and "result" in user_data:
            result_obj = user_data["result"]
            if isinstance(result_obj, dict) and not result_obj.get("success", True):
                print(f"  ⚠️ 用户信息获取受限: {result_obj.get('message', '未知原因')}")
                user_data = {}

        basic = user_data.get("basic_info") or user_data.get("basicInfo") or user_data.get("user") or user_data
        interactions_raw = user_data.get("interactions") or user_data.get("interaction") or []
    except TikHubError as e:
        print(f"  ⚠️ 获取用户信息失败: {e}")
        basic = {"nickname": "?", "user_id": user_id}
        interactions_raw = []
    
    nickname = basic.get("nickname") or basic.get("nick_name") or "?"
    print(f"  昵称: {nickname}")
    
    if isinstance(interactions_raw, list):
        for i in interactions_raw:
            name = i.get("name") or i.get("type") or "?"
            count = i.get("count") or i.get("value") or "?"
            print(f"  {name}: {count}")
    
    # 组装兼容原 MCP 格式的 profile 数据
    profile = {
        "userBasicInfo": basic,
        "interactions": interactions_raw if isinstance(interactions_raw, list) else [],
        "tags": user_data.get("tags") or [],
        "_source": "tikhub",
    }

    # --- 获取用户笔记列表（多页，可能失败则跳过靠搜索补充） ---
    notes = {}
    cursor = ""
    page = 0
    max_pages = 5  # 最多取 5 页，防止死循环
    
    time.sleep(1)  # 用户信息和笔记列表之间加间隔，避免 TikHub 限速 400
    
    try:
        while page < max_pages:
            notes_raw = client.fetch_user_notes(user_id, cursor=cursor)
            notes_data = notes_raw.get("data", notes_raw)
            if isinstance(notes_data, dict) and "data" in notes_data:
                notes_data = notes_data["data"]
            
            # web_v2/fetch_home_notes 返回: { has_more, notes: [...], tags: [...] }
            note_list = (notes_data.get("notes") or notes_data.get("items")
                         or notes_data.get("feeds") or [])
            
            last_cursor = ""
            for feed in note_list:
                nid = feed.get("id") or feed.get("note_id") or feed.get("noteId") or ""
                title = feed.get("display_title") or feed.get("title") or feed.get("displayTitle") or ""
                # web_v2 用 likes / collected_count 等顶层字段
                liked = feed.get("likes") or feed.get("liked_count") or feed.get("likedCount") or 0
                if nid:
                    notes[nid] = {
                        "id": nid,
                        "xsecToken": feed.get("xsec_token") or feed.get("xsecToken") or "",
                        "title": title,
                        "type": feed.get("type") or "",
                        "likedCount": parse_count(liked),
                        "source": "profile",
                    }
                # web_v2 的分页游标在每条笔记的 cursor 字段里
                c = feed.get("cursor") or ""
                if c:
                    last_cursor = c
            
            # 检查是否有下一页
            has_more = notes_data.get("has_more") or notes_data.get("hasMore") or False
            next_cursor = last_cursor or notes_data.get("cursor") or notes_data.get("lastCursor") or ""
            
            if not has_more or not next_cursor or not note_list:
                break
            
            cursor = next_cursor
            page += 1
            time.sleep(0.3)
    except TikHubError as e:
        print(f"  ⚠️ 获取用户笔记列表失败: {e}")
        print(f"     将通过搜索补充获取笔记")
    
    # ---- Fallback：多层补充笔记列表 ----
    # 当 fetch_user_notes 全挂时，尝试多个来源补充
    if len(notes) == 0:
        # 来源1：从 fetch_user_info 返回的数据中提取 feeds
        feeds_from_info = []
        try:
            feeds_from_info = user_data.get("feeds") or user_data.get("notes") or []
        except NameError:
            pass
        
        if feeds_from_info:
            for feed in feeds_from_info:
                nid = (feed.get("id") or feed.get("note_id") or feed.get("noteId") or "")
                if nid and nid not in notes:
                    notes[nid] = {
                        "id": nid,
                        "xsecToken": feed.get("xsec_token") or feed.get("xsecToken") or "",
                        "title": feed.get("display_title") or feed.get("title") or "",
                        "type": feed.get("type") or "",
                        "likedCount": parse_count(feed.get("likes") or feed.get("liked_count") or feed.get("likedCount") or 0),
                        "source": "profile_feeds",
                    }
            if notes:
                print(f"  📦 从用户信息接口补充到 {len(notes)} 条笔记")
        
        # 来源2：读取之前保存的旧 profile 文件中的 feeds
        if len(notes) == 0:
            safe_nick = safe_filename(nickname)
            # 向上找 output_dir 或当前目录
            for search_dir in [os.getcwd(), os.path.join(os.getcwd(), "..", "data"), os.path.join(os.getcwd(), "data")]:
                old_profile_path = os.path.join(search_dir, f"{safe_nick}_profile.json")
                if os.path.exists(old_profile_path):
                    try:
                        with open(old_profile_path, "r", encoding="utf-8") as f:
                            old_profile = json.load(f)
                        old_feeds = old_profile.get("feeds") or []
                        for feed in old_feeds:
                            nid = feed.get("id") or ""
                            if nid and nid not in notes:
                                notes[nid] = feed
                        if notes:
                            print(f"  📦 从旧 profile 文件补充到 {len(notes)} 条笔记 ({old_profile_path})")
                            break
                    except Exception:
                        pass
        
        # 来源3：读取之前保存的旧 notes_list 文件
        if len(notes) == 0:
            safe_nick = safe_filename(nickname)
            for search_dir in [os.getcwd(), os.path.join(os.getcwd(), "..", "data"), os.path.join(os.getcwd(), "data")]:
                old_list_path = os.path.join(search_dir, f"{safe_nick}_notes_list.json")
                if os.path.exists(old_list_path):
                    try:
                        with open(old_list_path, "r", encoding="utf-8") as f:
                            old_notes = json.load(f)
                        for item in old_notes:
                            nid = item.get("id") or ""
                            if nid and nid not in notes:
                                notes[nid] = item
                        if notes:
                            print(f"  📦 从旧笔记列表文件补充到 {len(notes)} 条笔记 ({old_list_path})")
                            break
                    except Exception:
                        pass
    
    # 将笔记列表也保存到 profile 中（兼容下游使用）
    profile["feeds"] = list(notes.values())
    
    print(f"  主页笔记: {len(notes)} 条")
    return profile, notes


# ----------------------------------------------------------
# Step 3: 多关键词搜索补充
# ----------------------------------------------------------
def search_supplement(client, keyword, user_id, existing_notes, extra_keywords=None, max_notes=80):
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
    consecutive_fails = 0
    
    for kw in base_keywords:
        try:
            data = client.search_notes(kw)
            feeds = _extract_feeds_from_search(data)
            new_count = 0
            consecutive_fails = 0  # 成功则重置连续失败计数
            
            for feed in feeds:
                nid = feed.get("id") or feed.get("note_id") or feed.get("noteId") or ""
                uid, _, _ = _extract_user_from_feed(feed)
                interact = _extract_interact_from_feed(feed)
                
                if uid == user_id and nid and nid not in existing_notes:
                    existing_notes[nid] = {
                        "id": nid,
                        "xsecToken": feed.get("xsec_token") or feed.get("xsecToken") or "",
                        "title": feed.get("display_title") or feed.get("displayTitle") or feed.get("title") or "",
                        "type": feed.get("type") or "",
                        "likedCount": parse_count(interact.get("liked_count") or interact.get("likedCount") or "0"),
                        "source": f"search:{kw}",
                    }
                    new_count += 1
            
            if new_count > 0:
                print(f"  '{kw}' → +{new_count} 条新笔记")
                new_total += new_count
            
            time.sleep(0.5)  # TikHub API 限速（比 MCP 的 1.5s 更快）
        except Exception as e:
            print(f"  '{kw}' 出错: {e}")
            consecutive_fails += 1
            # 连续失败 2 次后，尝试重置搜索死链缓存给后续关键词一次新机会
            if consecutive_fails == 2:
                client._router.reset_category_cache("search")
                print(f"  🔄 连续失败 {consecutive_fails} 次，已重置搜索端点缓存")
            time.sleep(1)

        if len(existing_notes) >= max_notes + 10:
            print(f"  已达 {max_notes + 10} 条缓冲上限，停止搜索补充")
            break

    print(f"  共新增 {new_total} 条，总计 {len(existing_notes)} 条")
    return existing_notes


# ----------------------------------------------------------
# Step 4: 逐条获取详情
# ----------------------------------------------------------
def get_all_details(client, notes_dict, output_dir, blogger_name):
    """逐条获取笔记详情，每10条checkpoint，支持断点恢复"""
    notes_list = sorted(notes_dict.values(), key=lambda x: x.get("likedCount", 0), reverse=True)
    total = len(notes_list)
    checkpoint_path = os.path.join(output_dir, f"{safe_filename(blogger_name)}_details_partial.json")

    # 🔄 断点恢复：加载已有存档，跳过已爬条目
    details = []
    already_done_ids = set()
    ok_count = 0
    err_count = 0
    if os.path.exists(checkpoint_path):
        try:
            with open(checkpoint_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            details = saved
            for d in saved:
                fid = (d.get("_feed_id")
                       or d.get("data", {}).get("note", {}).get("noteId")
                       or d.get("data", {}).get("note", {}).get("id"))
                if fid:
                    already_done_ids.add(fid)
            ok_count = len([d for d in saved if "_error" not in d])
            err_count = len([d for d in saved if "_error" in d])
            print(f"\n🔄 发现断点文件，已恢复 {len(already_done_ids)} 条，继续未完成部分...")
        except Exception as e:
            print(f"\n⚠️ 断点文件读取失败，从头开始: {e}")
            details = []
            already_done_ids = set()
            ok_count = 0
            err_count = 0

    print(f"\n📖 批量获取 {total} 条笔记详情（已有 {len(already_done_ids)} 条）...")
    print("=" * 60)

    for i, note in enumerate(notes_list):
        nid = note["id"]

        # 跳过已爬的
        if nid in already_done_ids:
            print(f"  [{i+1:3d}/{total}] ⏭️  已爬取，跳过")
            continue

        token = note.get("xsecToken", "")
        note_type = note.get("type", "") or ""
        title = note.get("title", "N/A")[:30]
        type_tag = "🎬" if str(note_type).lower() == "video" else "📷"
        print(f"  [{i+1:3d}/{total}] {type_tag} {title}...", end="", flush=True)
        
        try:
            # App-V2 端点分图文/视频，按 note_type 路由
            raw_detail = client.fetch_note_detail(nid, xsec_token=token, note_type=note_type)
            
            # 提取内层数据（App-V2 / 旧 v7 多种嵌套结构兼容）
            detail = raw_detail.get("data", raw_detail)
            if isinstance(detail, dict) and "data" in detail:
                detail = detail["data"]

            # 旧 v7 可能返回 list[0]；App-V2 通常是 dict
            if isinstance(detail, list):
                detail = detail[0] if detail else {}

            # 提取 note 对象：兼容以下几种结构
            #   A. App-V2 get_image/video_note_detail: { note: {...}, comments: {list: [...]} }
            #   B. 旧 v7 get_note_info_v7:           { note_list: [{...}], comment_list: [...] }
            #   C. 直接 note 对象（极少数兼容分支）:     { noteId, desc, ... }
            #   D. Router 归一化后的 web_v3 格式:      { items: [{id, noteCard: {...}, _comments: {list: [...]}}] }
            note_obj = {}
            comment_list_raw = []
            if isinstance(detail, dict):
                # D 结构（Router 归一化后的 web_v3 格式）：items[0].noteCard
                items_raw = detail.get("items") or []
                if isinstance(items_raw, list) and items_raw:
                    first_item = items_raw[0] or {}
                    if isinstance(first_item, dict):
                        note_obj = first_item.get("noteCard") or first_item.get("note_card") or first_item.get("note") or {}
                        # 评论可能挂在 noteCard._comments 或 item._comments
                        inner_comments = {}
                        if isinstance(note_obj, dict):
                            inner_comments = note_obj.get("_comments") or {}
                        if not inner_comments:
                            inner_comments = first_item.get("_comments") or first_item.get("comments") or {}
                        if isinstance(inner_comments, dict):
                            comment_list_raw = inner_comments.get("list") or inner_comments.get("comments") or []
                        elif isinstance(inner_comments, list):
                            comment_list_raw = inner_comments
                # B 结构：note_list / comment_list
                if not note_obj:
                    note_list_raw = detail.get("note_list") or []
                    if isinstance(note_list_raw, list) and note_list_raw:
                        note_obj = note_list_raw[0] or {}
                        comment_list_raw = detail.get("comment_list") or []
                # A 结构：note / comments.list
                if not note_obj:
                    note_obj = detail.get("note") or detail.get("noteData") or {}
                    comments_obj = detail.get("comments") or {}
                    if isinstance(comments_obj, dict):
                        comment_list_raw = comments_obj.get("list") or comments_obj.get("comments") or []
                # C 结构：detail 本身就是 note
                if not note_obj and (detail.get("noteId") or detail.get("note_id") or detail.get("desc")):
                    note_obj = detail

            # 检测笔记是否已删除/隐藏
            raw_text = str(detail.get("message", "")) + str(detail.get("msg", ""))
            if "not found" in raw_text.lower() or not note_obj:
                print(f" ⚠️ 内容获取受限（API限制，非删除）")
                details.append({"_feed_id": nid, "_error": "笔记内容获取受限（API限制，非删除）", "_title": note.get("title"), "_content_restricted": True})
                err_count += 1
            else:
                # 按热度排序评论（liked_count 降序）
                if comment_list_raw:
                    comment_list_raw = sorted(
                        comment_list_raw,
                        key=lambda c: int(c.get("liked_count", 0) or c.get("likeCount", 0) or c.get("like_count", 0) or 0),
                        reverse=True
                    )

                # 构建统一输出结构（兼容下游分析脚本）
                unified = {
                    "note": note_obj,
                    "comments": {"list": comment_list_raw},
                    "_meta": {
                        "source": note.get("source"),
                        "idx": i,
                        "list_title": note.get("title"),
                        "note_type": note_type,
                        "source_endpoint": raw_detail.get("_endpoint_used", ""),
                        "source_group": raw_detail.get("_endpoint_group", ""),
                        "xsec_token": token,
                    },
                    "_feed_id": nid,
                }
                details.append(unified)

                # 提取互动数据（兼容 v7 扁平结构 + App-V2 interact_info 嵌套结构）
                interact = note_obj.get("interactInfo") or note_obj.get("interact_info") or {}
                liked = (note_obj.get("liked_count") or note_obj.get("likedCount")
                         or interact.get("liked_count") or interact.get("likedCount") or "?")
                collected = (note_obj.get("collected_count") or note_obj.get("collectedCount")
                             or interact.get("collected_count") or interact.get("collectedCount") or "?")

                print(f" ✅ L:{liked} C:{collected}")
                ok_count += 1
        except Exception as e:
            err_str = str(e)[:50]
            print(f" ❌ {err_str}")
            details.append({"_feed_id": nid, "_error": str(e), "_title": note.get("title")})
            err_count += 1
        
        time.sleep(0.3)  # TikHub API 限速（比 MCP 的 3s 快得多）
        
        # 每10条做一次checkpoint（按实际处理条数触发，不受跳过影响）
        if len(details) % 10 == 0 and len(details) > 0:
            with open(checkpoint_path, "w", encoding="utf-8") as f:
                json.dump(details, f, ensure_ascii=False, indent=2)
            print(f"  --- checkpoint: {ok_count}✅ {err_count}❌ ---")
    
    print(f"\n完成: {ok_count}✅ {err_count}❌ / 共{total}条")
    
    # 清理checkpoint
    if os.path.exists(checkpoint_path):
        os.remove(checkpoint_path)
    
    return details


# ----------------------------------------------------------
# Step 4.5: 轮次2 — 数据质量自愈（自动补调）
# ----------------------------------------------------------
def repair_incomplete_notes(details, client):
    """
    轮次2：对质量不完整的笔记做定向补调。
    
    流程：
      1. 扫描所有 details，按 quality.check_note_quality 分级
      2. 对 partial（有正文但缺互动/作者/时间）的笔记做补调
      3. 补调时跳过轮次1 已用的端点，强制换端点
      4. 用 quality.merge_note_supplement 非空覆盖合并
    
    Args:
        details: 轮次1 产出的 details 列表
        client: TikHubClient 实例
    
    Returns:
        (修复后的 details 列表, 质量统计 dict)
    """
    from utils.quality import check_note_quality, merge_note_supplement

    # ---- 扫描 ----
    to_repair = []
    stats = {"complete": 0, "partial": 0, "failed": 0}
    for i, entry in enumerate(details):
        q = check_note_quality(entry)
        stats[q["level"]] += 1
        if q["level"] == "partial":
            to_repair.append((i, entry, q))

    print(f"\n{'='*60}")
    print(f"📊 数据质量扫描")
    print(f"{'='*60}")
    print(f"  complete（完全齐全）: {stats['complete']} 条")
    print(f"  partial （缺部分）  : {stats['partial']} 条")
    print(f"  failed  （完全失败）: {stats['failed']} 条")

    if not to_repair:
        print(f"\n  ✅ 数据完整，无需补调")
        return details, stats

    # ---- 过滤：仅缺 comments 的不需要补调（detail 端点不返回评论，交给轮次3） ----
    to_repair_filtered = []
    skipped_comments_only = 0
    for item in to_repair:
        _, _, q = item
        if q["missing"] == ["comments"]:
            skipped_comments_only += 1
        else:
            to_repair_filtered.append(item)
    
    if skipped_comments_only > 0:
        print(f"\n  ℹ️ {skipped_comments_only} 条仅缺评论，跳过轮次2（交给轮次3独立评论端点）")
    
    if not to_repair_filtered:
        print(f"\n  ✅ 无需补调（仅缺评论的由轮次3处理）")
        return details, stats

    print(f"\n🔧 轮次 2 · 自动补调（共 {len(to_repair_filtered)} 条）")
    print("-" * 60)

    repaired_count = 0
    for idx, (i, entry, q) in enumerate(to_repair_filtered, 1):
        note_id = entry.get("_feed_id") or entry.get("note", {}).get("noteId") or ""
        if not note_id:
            continue

        # 跳过轮次1 已用的端点
        meta = entry.get("_meta") or {}
        used_ep = meta.get("source_endpoint", "")
        skip = [used_ep] if used_ep else []
        used_group = meta.get("source_group", "")

        note_type = meta.get("note_type", "") or ""
        xsec_token = meta.get("xsec_token", "")

        short_id = note_id[:8] if len(note_id) > 8 else note_id
        print(f"  [{idx}/{len(to_repair_filtered)}] note={short_id}... 缺{q['missing']} 跳过{used_group}", end="", flush=True)

        try:
            raw_supplement = client.fetch_note_detail(
                note_id,
                xsec_token=xsec_token,
                note_type=note_type,
                skip_endpoints=skip,
            )

            # 解析补调结果为 note_entry 结构
            sup_entry = _extract_supplement_entry(raw_supplement, note_id)

            if sup_entry:
                # 补调拿到的端点信息写入 supplement 的 _meta
                sup_entry["_meta"] = {
                    "source_endpoint": raw_supplement.get("_endpoint_used", ""),
                    "source_group": raw_supplement.get("_endpoint_group", ""),
                }

                # 合并
                merged = merge_note_supplement(entry, sup_entry)
                details[i] = merged

                # 重新扫描质量
                q2 = check_note_quality(merged)
                fixed_fields = set(q["missing"]) - set(q2["missing"])
                if fixed_fields:
                    repaired_count += 1
                    print(f" ✅ 补齐{fixed_fields}")
                else:
                    print(f" ⚠️ 端点{raw_supplement.get('_endpoint_group', '?')}也无此数据")
            else:
                print(f" ⚠️ 补调返回空")

        except Exception as e:
            print(f" ❌ {str(e)[:40]}")
            continue

        time.sleep(0.3)

    stats["repaired"] = repaired_count
    print(f"\n轮次2 完成：✅ {repaired_count}/{len(to_repair_filtered)} 条补齐了部分字段")
    return details, stats


def _extract_supplement_entry(raw, note_id):
    """
    把 Router 归一化输出解析为 {note, comments, _feed_id} 结构。
    与 get_all_details 中的 D 结构解析逻辑相同。
    """
    if not isinstance(raw, dict):
        return None

    detail = raw.get("data", raw)
    if isinstance(detail, dict) and "data" in detail:
        detail = detail["data"]

    if isinstance(detail, list):
        detail = detail[0] if detail else {}

    if not isinstance(detail, dict):
        return None

    note_obj = {}
    comment_list_raw = []

    # D 结构（Router 归一化后的 web_v3 格式）：items[0].noteCard
    items_raw = detail.get("items") or []
    if isinstance(items_raw, list) and items_raw:
        first_item = items_raw[0] or {}
        if isinstance(first_item, dict):
            note_obj = first_item.get("noteCard") or first_item.get("note_card") or first_item.get("note") or {}
            inner_comments = {}
            if isinstance(note_obj, dict):
                inner_comments = note_obj.get("_comments") or {}
            if not inner_comments:
                inner_comments = first_item.get("_comments") or first_item.get("comments") or {}
            if isinstance(inner_comments, dict):
                comment_list_raw = inner_comments.get("list") or inner_comments.get("comments") or []
            elif isinstance(inner_comments, list):
                comment_list_raw = inner_comments

    # B 结构：note_list / comment_list
    if not note_obj:
        note_list_raw = detail.get("note_list") or []
        if isinstance(note_list_raw, list) and note_list_raw:
            note_obj = note_list_raw[0] or {}
            comment_list_raw = detail.get("comment_list") or []

    # A 结构：note / comments.list
    if not note_obj:
        note_obj = detail.get("note") or detail.get("noteData") or {}
        comments_obj = detail.get("comments") or {}
        if isinstance(comments_obj, dict):
            comment_list_raw = comments_obj.get("list") or comments_obj.get("comments") or []

    # C 结构：detail 本身就是 note
    if not note_obj and (detail.get("noteId") or detail.get("note_id") or detail.get("desc")):
        note_obj = detail

    if not note_obj:
        return None

    return {
        "note": note_obj,
        "comments": {"list": comment_list_raw},
        "_feed_id": note_id,
    }


def fetch_comments_batch(details, client, max_comments_per_note=20):
    """
    独立评论采集：对每条有效笔记调用 fetch_note_comments 端点。
    
    仅采集缺评论的笔记（comments.list 为空且 commentCount > 0 的）。
    评论端点独立于详情端点，不影响正文和互动数据。
    
    Args:
        details: 详情列表
        client: TikHubClient 实例
        max_comments_per_note: 每条笔记最多采集的评论数（防止超大帖子消耗太多 Token）
    
    Returns:
        (修改后的 details, 成功采集评论的数量)
    """
    # 筛选需要采集评论的笔记
    to_fetch = []
    for i, entry in enumerate(details):
        if entry.get("_error") or entry.get("_content_restricted"):
            continue
        
        # 检查是否已有评论
        existing_comments = entry.get("comments", {})
        if isinstance(existing_comments, dict):
            existing_list = existing_comments.get("list", [])
        elif isinstance(existing_comments, list):
            existing_list = existing_comments
        else:
            existing_list = []
        
        if existing_list:
            continue  # 已有评论，跳过
        
        # 检查 commentCount 是否 > 0（有评论但未采集的）
        note_obj = entry.get("note", {})
        interact = note_obj.get("interactInfo") or note_obj.get("interact_info") or {}
        comment_count_str = str(
            interact.get("commentCount") or interact.get("comment_count") or 
            note_obj.get("commentCount") or note_obj.get("comment_count") or 
            note_obj.get("comments_count") or "0"
        )
        # 归一化万单位
        comment_count = 0
        try:
            if comment_count_str.endswith("万"):
                comment_count = int(float(comment_count_str[:-1]) * 10000)
            else:
                comment_count = int(comment_count_str.replace(",", ""))
        except (ValueError, TypeError):
            comment_count = 0
        
        # 兜底：即使 commentCount=0，如果 likedCount 较高也尝试采集（防止互动数据缺失导致评论漏采）
        liked_count_str = str(
            interact.get("likedCount") or interact.get("liked_count") or 
            note_obj.get("likedCount") or note_obj.get("liked_count") or "0"
        )
        liked_count = 0
        try:
            liked_count = int(liked_count_str.replace(",", ""))
        except (ValueError, TypeError):
            liked_count = 0
        
        should_fetch = comment_count > 0 or liked_count >= 10  # likedCount>=10 大概率有评论
        
        note_id = entry.get("_feed_id") or note_obj.get("noteId") or note_obj.get("note_id") or note_obj.get("id") or ""
        if note_id and should_fetch:
            to_fetch.append((i, entry, note_id, comment_count or liked_count))
    
    if not to_fetch:
        print(f"\n💬 评论采集：无需采集（所有笔记已有评论或 commentCount=0）")
        return details, 0
    
    print(f"\n{'='*60}")
    print(f"💬 独立评论采集（共 {len(to_fetch)} 条笔记有评论待采集）")
    print(f"{'-'*60}")
    
    # 重置评论端点的死链缓存（评论端点不稳定是间歇性的，不应被前面的 detail 阶段污染）
    if hasattr(client, '_router') and hasattr(client._router, 'reset_category_cache'):
        client._router.reset_category_cache("comments")
    
    success_count = 0
    fail_streak = 0  # 连续失败计数（用于自适应延时）
    for idx, (i, entry, note_id, comment_count) in enumerate(to_fetch, 1):
        short_id = note_id[:8] if len(note_id) > 8 else note_id
        print(f"  [{idx}/{len(to_fetch)}] note={short_id}... 评论约{comment_count}条", end="", flush=True)
        
        # 自适应延时：连续失败后增加等待（疑似限速）
        if fail_streak >= 3:
            wait = min(fail_streak * 2, 15)
            print(f" ⏳等待{wait}s", end="", flush=True)
            time.sleep(wait)
            # 连续失败太多次，尝试重置评论缓存给端点再次机会
            if hasattr(client, '_router') and hasattr(client._router, 'reset_category_cache'):
                client._router.reset_category_cache("comments")
                fail_streak = 0  # 重置后重新计数
        
        try:
            raw_comments = client.fetch_note_comments(note_id, cursor="")
            
            # 解析评论数据
            comment_list = _extract_comments_from_response(raw_comments)
            
            if comment_list:
                # 按热度排序（liked_count 降序）
                comment_list = sorted(
                    comment_list,
                    key=lambda c: int(c.get("liked_count", 0) or c.get("likeCount", 0) or c.get("like_count", 0) or 0),
                    reverse=True
                )
                # 截断到 max_comments_per_note
                comment_list = comment_list[:max_comments_per_note]
                
                # 写入 details
                details[i]["comments"] = {"list": comment_list}
                success_count += 1
                fail_streak = 0  # 成功，重置连续失败计数
                print(f" ✅ {len(comment_list)}条评论")
            else:
                fail_streak += 1
                print(f" ⚠️ 端点返回空")
        except Exception as e:
            fail_streak += 1
            print(f" ❌ {str(e)[:40]}")
        
        time.sleep(0.5)  # 基础间隔提高到 0.5s（评论端点对频率敏感）
    
    print(f"\n评论采集完成：✅ {success_count}/{len(to_fetch)} 条笔记拿到了评论")
    return details, success_count


def _extract_comments_from_response(raw):
    """
    从 fetch_note_comments 的响应中提取评论列表。
    
    兼容多种返回结构：
    - web_v3: { data: { data: { comments: [...], cursor, hasMore } } }
    - app/app_v2: { data: { data: { comments: [...] } } } 或 { data: { comments: [...] } }
    """
    if not isinstance(raw, dict):
        return []
    
    # 层层解包
    d = raw.get("data", raw)
    if isinstance(d, dict) and "data" in d and isinstance(d["data"], dict):
        d = d["data"]
    
    if not isinstance(d, dict):
        return []
    
    # 尝试多种字段名
    comments = (
        d.get("comments") or 
        d.get("comment_list") or 
        d.get("list") or 
        d.get("items") or 
        []
    )
    
    if isinstance(comments, dict):
        # 可能是 { list: [...] } 结构
        comments = comments.get("list") or comments.get("comments") or []
    
    if not isinstance(comments, list):
        return []
    
    return comments


def _print_final_quality_report(details, stats):
    """打印最终的数据质量报告"""
    from utils.quality import check_note_quality

    # 重新扫描最终质量
    final = {"complete": 0, "partial": 0, "failed": 0}
    interact_ok = 0
    comments_ok = 0
    total_valid = 0

    for entry in details:
        if entry.get("_error") or entry.get("_content_restricted"):
            final["failed"] += 1
            continue
        total_valid += 1
        q = check_note_quality(entry)
        final[q["level"]] += 1
        if q["has_interact"]:
            interact_ok += 1
        if q["has_comments"]:
            comments_ok += 1

    print(f"\n{'='*60}")
    print(f"📊 最终数据质量报告")
    print(f"{'='*60}")
    if total_valid > 0:
        print(f"  正文完整率: {total_valid}/{len(details)} ({total_valid*100//len(details)}%)")
        print(f"  互动数据率: {interact_ok}/{total_valid} ({interact_ok*100//total_valid}%)")
        print(f"  评论覆盖率: {comments_ok}/{total_valid} ({comments_ok*100//total_valid}%)")
    else:
        print(f"  ⚠️ 无有效数据")
    if stats.get("repaired"):
        print(f"  轮次2补齐 : {stats['repaired']} 条")
    if stats.get("comments_fetched"):
        print(f"  评论补采  : {stats['comments_fetched']} 条笔记")
    print(f"{'='*60}")


# ----------------------------------------------------------
# 主流程
# ----------------------------------------------------------
def crawl_blogger(keyword=None, user_id=None, output_dir=None, token=None, is_self=False, extra_keywords=None, max_notes=80):
    """
    完整爬取一个博主的全量数据。
    
    Args:
        keyword: 博主搜索关键词（和user_id二选一）
        user_id: 直接指定user_id
        output_dir: 数据输出目录
        token: TikHub API Token（也可通过环境变量 TIKHUB_API_TOKEN 设置）
        is_self: 是否标记为自己的账号
        extra_keywords: 领域关键词列表（如 ["烘焙", "食谱"]），用于搜索补充
    
    Returns:
        dict — { profile, notes_list, details, nickname, user_id }
    """
    client = TikHubClient(token=token)
    
    # 定位博主
    xsec_token = ""
    nickname = keyword or ""
    
    if user_id:
        # 直接用user_id
        if keyword:
            try:
                _, nickname, xsec_token = find_blogger(client, keyword)
            except Exception as e:
                print(f"  ⚠️ 搜索博主失败({e})，将通过 user_id 直接获取信息")
                nickname = keyword  # 保留用户传入的关键词作为 nickname（用于搜索补充）
        else:
            # 没传 keyword：先通过 fetch_user_info 获取真实昵称，不再用 user_id[:12]
            nickname = ""  # 后面 get_profile 会填充
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
    
    # 用 profile 中的真实昵称回填（修复只传 user_id 时 nickname 为空的问题）
    real_nickname = (profile.get("userBasicInfo", {}).get("nickname") or "").strip()
    if real_nickname and (not nickname or nickname == user_id[:12]):
        nickname = real_nickname
        print(f"  📝 昵称已回填: {nickname}")
    
    # 更新 safe_name（昵称可能刚刚改变）
    safe_name = safe_filename(nickname)
    
    # 搜索补充：主页笔记数 < max_notes 时自动触发
    # 用户传了 --keywords 就用用户指定的，没传就用通用后缀
    search_keyword = keyword or nickname  # 搜索补充一定用博主昵称，而不是 user_id 前缀
    
    if len(notes) >= max_notes:
        print(f"\n✅ 主页已获取 {len(notes)} 条笔记（≥目标 {max_notes} 条），无需搜索补充")
    else:
        print(f"\n📊 主页仅获取 {len(notes)} 条笔记（目标 {max_notes} 条），启动多关键词搜索补充...")
        # 搜索前重置搜索类别的死链缓存（前面探测/获取阶段可能误标了搜索端点）
        client._router.reset_category_cache("search")
        print(f"  🔄 已重置搜索端点死链缓存，给搜索补充一次全新机会")
        notes = search_supplement(client, search_keyword, user_id, notes, extra_keywords, max_notes=max_notes)

    # 🔒 硬上限截断：按赞数排序后取前 max_notes+10 条（含10条缓冲，应对详情获取失败）
    buffer = max_notes + 10
    if len(notes) > buffer:
        sorted_notes = sorted(notes.values(), key=lambda x: x.get("likedCount", 0), reverse=True)
        notes = {n["id"]: n for n in sorted_notes[:buffer]}
        print(f"\n⚙️ 已截断至 {buffer} 条（原 {len(sorted_notes)} 条，含10条缓冲）")

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
    
    # 获取全部详情（轮次1）
    details = get_all_details(client, notes, output_dir, nickname)
    
    # 轮次2：数据质量自愈（自动补调缺失字段）
    details, quality_stats = repair_incomplete_notes(details, client)
    
    # 轮次3：独立评论采集（对缺评论但 commentCount>0 的笔记调独立评论端点）
    details, comments_fetched = fetch_comments_batch(details, client)
    quality_stats["comments_fetched"] = comments_fetched
    
    _print_final_quality_report(details, quality_stats)

    # === 合规改造 v2.0：评论者身份脱敏（源头单点注入）===
    # 规则：读者昵称/userid/头像/IP 全部删除，替换为「读者1 / 读者2 / 作者」
    #      评论正文 content 保留用于研究。详见 scripts/utils/privacy.py
    anonymized_count = 0
    for d in details:
        comments_obj = d.get("comments")
        if isinstance(comments_obj, dict) and isinstance(comments_obj.get("list"), list):
            before = len(comments_obj["list"])
            anonymize_note_comments_inplace(d)
            if before > 0:
                anonymized_count += before
        # 在每条 note 的 _meta 标记合规版本，方便下游审计
        meta = d.setdefault("_meta", {})
        meta["privacy_version"] = PRIVACY_VERSION

    if anonymized_count > 0:
        print(f"🔒 评论者身份已脱敏：{anonymized_count} 条评论 → 读者N / 作者（privacy_version={PRIVACY_VERSION}）")

    # 保存详情
    details_path = os.path.join(output_dir, f"{safe_name}_notes_details.json")
    with open(details_path, "w", encoding="utf-8") as f:
        json.dump(details, f, ensure_ascii=False, indent=2)
    
    ok_count = len([d for d in details if "_error" not in d])
    restricted_count = len([d for d in details if d.get("_content_restricted")])
    print(f"\n💾 笔记详情: {details_path} ({ok_count}条有效)")
    if restricted_count > 0:
        print(f"   ⚠️ {restricted_count} 条笔记内容获取受限（API限制，非删除），标题已保留供分析参考")

    # === 数据校验（自动运行，不依赖 AI 调用）===
    print("\n" + "=" * 60)
    print("📋 数据校验")
    print("=" * 60)
    valid_details = [d for d in details if "_error" not in d]

    v1_ok, v1_msg = check_content_completeness(valid_details)
    print(v1_msg)
    if not v1_ok:
        print("\n🚨 正文数据不完整，无法进行可靠的深度分析。")
        print("   请确认是否逐条调用了 fetch_note_detail。")
        sys.exit(1)

    # V2-V5：警告类，不阻断
    print(check_note_count(valid_details, max_notes))
    print(check_time_field(valid_details))
    print(check_duplicates(valid_details))
    print(get_sample_watermark(valid_details, profile))
    print("=" * 60)

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

    parser = argparse.ArgumentParser(description="小红书博主数据采集（TikHub API 版，适用于任何领域）")
    parser.add_argument("keyword", nargs="?", help="博主搜索关键词")
    parser.add_argument("--user-id", help="直接指定user_id")
    parser.add_argument("--output", "-o", default=".", help="数据输出目录")
    parser.add_argument("--token", help="TikHub API Token（也可用环境变量 TIKHUB_API_TOKEN）")
    parser.add_argument("--self", dest="is_self", action="store_true", help="标记为自己账号")
    parser.add_argument("--keywords", help="领域关键词（逗号分隔），用于搜索补充。如：烘焙,食谱,探店")
    parser.add_argument("--max-notes", type=int, default=80,
                        help="最大爬取条数上限（默认80，用户可根据需要调大，如 --max-notes 100）")
    args = parser.parse_args()

    if not args.keyword and not args.user_id:
        parser.error("请指定博主关键词或 --user-id")

    # 解析领域关键词
    extra_keywords = None
    if args.keywords:
        extra_keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]

    # Token: 优先命令行 > 环境变量 > 配置文件（三级加载，与 TikHubClient 对齐）
    token = TikHubClient._resolve_api_key(args.token)
    if not token:
        print("❌ 请设置 TikHub API Token:")
        print("   方式1: set TIKHUB_API_TOKEN=你的token   (Windows)")
        print("   方式2: export TIKHUB_API_TOKEN=你的token (macOS/Linux)")
        print("   方式3: python crawl_blogger.py \"博主名\" --token 你的token")
        print("   方式4: python scripts/check_env.py 进行交互式设置（保存到配置文件）")
        sys.exit(1)

    start = time.time()
    result = crawl_blogger(
        keyword=args.keyword,
        user_id=args.user_id,
        output_dir=args.output,
        token=token,
        is_self=args.is_self,
        extra_keywords=extra_keywords,
        max_notes=args.max_notes,
    )
    elapsed = time.time() - start
    
    print(f"\n{'='*60}")
    print(f"🎉 采集完成! 用时 {elapsed:.0f}秒")
    print(f"   博主: {result['nickname']}")
    print(f"   笔记: {len(result['notes_list'])}条")
    print(f"   详情: {len([d for d in result['details'] if '_error' not in d])}条有效")
    print(f"   输出: {result['output_dir']}")
    print(f"{'='*60}")
