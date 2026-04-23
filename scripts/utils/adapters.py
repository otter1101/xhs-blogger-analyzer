"""
Adapter 归一化层 — 把不同 TikHub 端点族的返回数据统一转成内部格式。

内部格式选用 web_v3 的嵌套结构（data.data.items[]），因为下游 crawl_blogger.py
已经有兼容这种格式的解析分支，改动最小。

每个 adapter 函数签名统一：
    def adapter_func(raw: dict, args: dict) -> dict
    - raw:  TikHub HTTP 返回的原始 JSON dict
    - args: 调用参数 dict（如 keyword / user_id 等，某些 adapter 可能需要）
    返回:  归一化后的 dict（保持 TikHub 顶层 code/message 不变，只改 data 部分）
"""

import re

# ============================================================
# 工具函数
# ============================================================

def _normalize_count(v):
    """把 '6.4万' / '13008' / 13008 / None 统一成数字字符串 '64000' / '13008' / '0'"""
    if v is None or v == "":
        return "0"
    if isinstance(v, (int, float)):
        return str(int(v))
    s = str(v).strip()
    if s.endswith("万"):
        try:
            return str(int(float(s[:-1]) * 10000))
        except (ValueError, TypeError):
            return "0"
    if s.endswith("亿"):
        try:
            return str(int(float(s[:-1]) * 100_000_000))
        except (ValueError, TypeError):
            return "0"
    # 去掉逗号 "1,234" → "1234"
    s = s.replace(",", "")
    try:
        return str(int(s))
    except (ValueError, TypeError):
        return "0"


def _pick(d, *keys, default=None):
    """从 dict 中按多个候选 key 取第一个非空值"""
    if not isinstance(d, dict):
        return default
    for k in keys:
        v = d.get(k)
        if v is not None and v != "" and v != []:
            return v
    return default


def _dig(d, *path, default=None):
    """按路径层层取值：_dig(d, 'data', 'data', 'items') 等价于 d['data']['data']['items']"""
    cur = d
    for p in path:
        if isinstance(cur, dict):
            cur = cur.get(p)
        else:
            return default
        if cur is None:
            return default
    return cur


def _unwrap_data(raw):
    """
    TikHub 统一的数据解包：
    raw → raw.get("data", raw) → 如果还有嵌套 .data 则再解一层
    返回 (envelope, inner_data)
      envelope: 顶层（保留 code/message 等元信息）
      inner_data: 最内层的业务数据 dict
    """
    d = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(d, dict) and "data" in d and isinstance(d["data"], dict):
        return raw, d["data"]
    return raw, d


def _is_empty(data):
    """检测归一化后的数据是否为空（用于 Router 判断是否降级）"""
    if not isinstance(data, dict):
        return True
    inner = _dig(data, "data", "data")
    if not isinstance(inner, dict):
        return True
    # 评论端点：有 comments/comment_list 字段且非空 → 不为空
    comments = inner.get("comments") or inner.get("comment_list") or inner.get("list") or []
    if isinstance(comments, dict):
        comments = comments.get("list") or comments.get("comments") or []
    if isinstance(comments, list) and len(comments) > 0:
        return False
    items = inner.get("items") or inner.get("notes") or inner.get("users") or []
    # 对于 user_info，检查 basicInfo
    if inner.get("basicInfo"):
        return False
    if len(items) == 0:
        return True
    # 详情场景防"假成功"：items 结构齐全但 noteCard 实质为空
    # （HTTP 200 + 外壳 OK + title/desc/user/interactInfo 全空 → 视为空，触发降级）
    first = items[0] if isinstance(items[0], dict) else {}
    note_card = first.get("noteCard") or first.get("note_card") or {}
    if isinstance(note_card, dict) and note_card:
        title_val = note_card.get("title") or ""
        desc_val = note_card.get("desc") or ""
        has_title = bool(title_val.strip()) if isinstance(title_val, str) else bool(title_val)
        has_desc = bool(desc_val.strip()) if isinstance(desc_val, str) else bool(desc_val)
        user = note_card.get("user") or {}
        has_user = bool(isinstance(user, dict) and (user.get("nickname") or user.get("userId")))
        interact = note_card.get("interactInfo") or {}
        # 互动数据全 0 且 title/desc/user 都空 → 判定为假成功
        has_interact = False
        if isinstance(interact, dict):
            for k in ("likedCount", "collectedCount", "commentCount"):
                v = str(interact.get(k, "0") or "0")
                if v not in ("", "0"):
                    has_interact = True
                    break
        if not (has_title or has_desc or has_user or has_interact):
            return True
    return False


def _normalize_interact(interact):
    """归一化互动数据 dict"""
    if not isinstance(interact, dict):
        return {
            "likedCount": "0", "collectedCount": "0",
            "commentCount": "0", "sharedCount": "0", "shareCount": "0",
        }
    return {
        "likedCount": _normalize_count(
            _pick(interact, "likedCount", "liked_count", "likes")),
        "collectedCount": _normalize_count(
            _pick(interact, "collectedCount", "collected_count", "collects")),
        "commentCount": _normalize_count(
            _pick(interact, "commentCount", "comment_count", "comments")),
        "shareCount": _normalize_count(
            _pick(interact, "shareCount", "sharedCount", "shared_count", "shares")),
    }


# ============================================================
# search_notes adapters
# ============================================================

def search_notes_web_v3(raw, args):
    """web_v3/fetch_search_notes → 内部格式（本身就是金标准，直接透传）"""
    # web_v3 已经是 { code, data: { ok, data: { hasMore, items: [...] } } }
    # items 里每条: { id, modelType, noteCard: {...}, xsecToken }
    # 这就是我们的金标准格式，直接返回
    return raw


def search_notes_app_v2(raw, args):
    """app_v2/search_notes → 内部格式
    
    app_v2 返回结构（推测，基于同族其他端点）:
    { code, data: { items: [ { note_card: {...}, id, ... } ], has_more, cursor } }
    
    转成 web_v3 格式: { code, data: { data: { items: [{id, noteCard, xsecToken}], hasMore } } }
    """
    envelope, inner = _unwrap_data(raw)
    if not isinstance(inner, dict):
        return raw  # 无法解析，原样返回让 Router 判定

    items_raw = _pick(inner, "items", "notes", "feeds") or []
    items_out = []
    for item in items_raw:
        if not isinstance(item, dict):
            continue
        # app_v2 可能用 note_card / noteCard / 直接就是 note
        nc = _pick(item, "note_card", "noteCard", "note") or item
        user_raw = _pick(nc, "user") or {}
        interact_raw = _pick(nc, "interact_info", "interactInfo") or {}
        cover_raw = _pick(nc, "cover") or {}

        items_out.append({
            "id": _pick(item, "id", "note_id", "noteId") or _pick(nc, "id", "note_id", "noteId") or "",
            "modelType": "note",
            "noteCard": {
                "type": _pick(nc, "type") or "normal",
                "displayTitle": _pick(nc, "display_title", "displayTitle", "title") or "",
                "user": {
                    "userId": _pick(user_raw, "userid", "userId", "user_id", "id") or "",
                    "nickname": _pick(user_raw, "nickname", "nick_name", "name") or "",
                    "nickName": _pick(user_raw, "nickname", "nick_name", "nickName", "name") or "",
                    "avatar": _pick(user_raw, "avatar", "images") or "",
                    "xsecToken": _pick(user_raw, "xsec_token", "xsecToken") or "",
                },
                "interactInfo": _normalize_interact(interact_raw),
                "cover": {
                    "urlDefault": _pick(cover_raw, "urlDefault", "url_default", "url") or "",
                    "urlPre": _pick(cover_raw, "urlPre", "url_pre") or "",
                    "height": _pick(cover_raw, "height") or 0,
                    "width": _pick(cover_raw, "width") or 0,
                },
                "imageList": _pick(nc, "imageList", "image_list") or [],
            },
            "xsecToken": _pick(item, "xsec_token", "xsecToken") or _pick(nc, "xsec_token", "xsecToken") or "",
        })

    has_more = _pick(inner, "has_more", "hasMore") or False
    cursor = _pick(inner, "cursor", "lastCursor") or ""

    return {
        "code": envelope.get("code", 200),
        "message": envelope.get("message", ""),
        "data": {
            "data": {
                "hasMore": has_more,
                "cursor": cursor,
                "items": items_out,
            }
        },
    }


def search_notes_app(raw, args):
    """app/search_notes → 内部格式（结构与 app_v2 类似）"""
    return search_notes_app_v2(raw, args)


def search_notes_web_v2(raw, args):
    """web_v2/fetch_search_notes → 内部格式
    
    web_v2 搜索参数用 'keywords'（复数）而非 'keyword'，但返回结构类似 app_v2
    """
    return search_notes_app_v2(raw, args)


# ============================================================
# search_users adapters
# ============================================================

def search_users_app_v2(raw, args):
    """app_v2/search_users → 内部格式
    
    转成: { code, data: { data: { items: [{user_info: {id, name, ...}}], hasMore } } }
    """
    envelope, inner = _unwrap_data(raw)
    if not isinstance(inner, dict):
        return raw

    items_raw = _pick(inner, "items", "users", "user_list") or []
    items_out = []
    for item in items_raw:
        if not isinstance(item, dict):
            continue
        u = _pick(item, "user_info", "user") or item
        items_out.append({
            "user_info": {
                "id": _pick(u, "id", "user_id", "userid", "userId") or "",
                "name": _pick(u, "name", "nickname", "nick_name") or "",
                "red_id": _pick(u, "red_id", "redId") or "",
                "desc": _pick(u, "desc", "description") or "",
                "sub_title": _pick(u, "sub_title", "subTitle") or "",
                "xsec_token": _pick(u, "xsec_token", "xsecToken") or "",
            }
        })

    return {
        "code": envelope.get("code", 200),
        "message": envelope.get("message", ""),
        "data": {
            "data": {
                "items": items_out,
                "hasMore": _pick(inner, "has_more", "hasMore") or False,
            }
        },
    }


def search_users_web_v3(raw, args):
    """web_v3/fetch_search_users → 内部格式（结构类似 app_v2，做轻度归一化）"""
    envelope, inner = _unwrap_data(raw)
    if not isinstance(inner, dict):
        return raw

    items_raw = _pick(inner, "items", "users") or []
    items_out = []
    for item in items_raw:
        if not isinstance(item, dict):
            continue
        u = _pick(item, "user_info", "userInfo", "user") or item
        items_out.append({
            "user_info": {
                "id": _pick(u, "id", "user_id", "userid", "userId") or
                      _pick(item, "id", "user_id", "userid", "userId") or "",
                "name": _pick(u, "name", "nickname", "nick_name", "nickName") or "",
                "red_id": _pick(u, "red_id", "redId") or "",
                "desc": _pick(u, "desc", "description") or "",
                "sub_title": _pick(u, "sub_title", "subTitle") or "",
                "xsec_token": _pick(u, "xsec_token", "xsecToken") or
                              _pick(item, "xsec_token", "xsecToken") or "",
            }
        })

    return {
        "code": envelope.get("code", 200),
        "message": envelope.get("message", ""),
        "data": {
            "data": {
                "items": items_out,
                "hasMore": _pick(inner, "has_more", "hasMore") or False,
            }
        },
    }


def search_users_web_v2(raw, args):
    """web_v2/fetch_search_users → 内部格式（与 web_v3 类似）"""
    return search_users_web_v3(raw, args)


def search_users_app(raw, args):
    """app/search_users → 内部格式（与 app_v2 类似）"""
    return search_users_app_v2(raw, args)


# ============================================================
# fetch_user_info adapters
# ============================================================

def user_info_web_v3(raw, args):
    """web_v3/fetch_user_info → 内部格式（金标准，直接透传）"""
    return raw


def user_info_app_v2(raw, args):
    """app_v2/get_user_info → 内部格式
    
    app_v2 返回可能: { code, data: { user: { nickname, desc, red_id, fans, ... } } }
    转成 web_v3 格式: { code, data: { data: { basicInfo: {...}, interactions: [...], tags: [...] } } }
    """
    envelope, inner = _unwrap_data(raw)
    if not isinstance(inner, dict):
        return raw

    # app_v2 可能已经有 basicInfo 或者用 user 包裹
    basic_raw = _pick(inner, "basicInfo", "basic_info", "user") or inner
    interactions_raw = _pick(inner, "interactions", "interaction") or []
    tags_raw = _pick(inner, "tags") or []

    # 如果 interactions 不是 list，尝试从 basic_raw 提取
    if not isinstance(interactions_raw, list):
        interactions_raw = []
        # 从 user 对象的顶层字段提取
        fans = _pick(basic_raw, "fans", "fansCount", "fans_count")
        follows = _pick(basic_raw, "follows", "followsCount", "follows_count", "follow_count")
        liked = _pick(basic_raw, "liked", "likedCount", "liked_and_collected", "interaction")
        if fans is not None:
            interactions_raw.append({"type": "fans", "name": "粉丝", "count": str(fans)})
        if follows is not None:
            interactions_raw.append({"type": "follows", "name": "关注", "count": str(follows)})
        if liked is not None:
            interactions_raw.append({"type": "interaction", "name": "获赞与收藏", "count": str(liked)})

    basic_out = {
        "nickname": _pick(basic_raw, "nickname", "nick_name", "name") or "",
        "redId": _pick(basic_raw, "redId", "red_id") or "",
        "gender": _pick(basic_raw, "gender") or 0,
        "ipLocation": _pick(basic_raw, "ipLocation", "ip_location") or "",
        "desc": _pick(basic_raw, "desc", "description") or "",
        "images": _pick(basic_raw, "images", "avatar", "imageb") or "",
        "imageb": _pick(basic_raw, "imageb", "images", "avatar") or "",
    }

    return {
        "code": envelope.get("code", 200),
        "message": envelope.get("message", ""),
        "data": {
            "data": {
                "basicInfo": basic_out,
                "interactions": interactions_raw,
                "tags": tags_raw,
            }
        },
    }


def user_info_app(raw, args):
    """app/get_user_info → 内部格式（结构与 app_v2 类似）"""
    return user_info_app_v2(raw, args)


def user_info_web_v2(raw, args):
    """web_v2/fetch_user_info → 内部格式（结构与 app_v2 类似）"""
    return user_info_app_v2(raw, args)


# ============================================================
# fetch_user_notes adapters
# ============================================================

def user_notes_web_v3(raw, args):
    """web_v3/fetch_user_notes → 内部格式（金标准，直接透传）"""
    return raw


def user_notes_app_v2(raw, args):
    """app_v2/get_user_posted_notes → 内部格式
    
    转成: { code, data: { data: { hasMore, cursor, notes: [...] } } }
    notes 里每条结构与 web_v3 对齐
    """
    envelope, inner = _unwrap_data(raw)
    if not isinstance(inner, dict):
        return raw

    items_raw = _pick(inner, "notes", "items", "feeds") or []
    notes_out = []
    for item in items_raw:
        if not isinstance(item, dict):
            continue
        nc = _pick(item, "note_card", "noteCard") or item
        user_raw = _pick(nc, "user") or _pick(item, "user") or {}
        interact_raw = _pick(nc, "interact_info", "interactInfo") or _pick(item, "interact_info", "interactInfo") or {}
        cover_raw = _pick(nc, "cover") or _pick(item, "cover") or {}

        notes_out.append({
            "noteId": _pick(item, "note_id", "noteId", "id") or _pick(nc, "note_id", "noteId", "id") or "",
            "type": _pick(nc, "type") or _pick(item, "type") or "",
            "displayTitle": _pick(nc, "display_title", "displayTitle", "title") or _pick(item, "display_title", "title") or "",
            "user": {
                "userId": _pick(user_raw, "userid", "userId", "user_id", "id") or "",
                "nickname": _pick(user_raw, "nickname", "nick_name") or "",
                "nickName": _pick(user_raw, "nickname", "nick_name", "nickName") or "",
                "avatar": _pick(user_raw, "avatar") or "",
            },
            "interactInfo": _normalize_interact(interact_raw),
            "cover": cover_raw,
            "xsecToken": _pick(item, "xsec_token", "xsecToken") or "",
        })

    return {
        "code": envelope.get("code", 200),
        "message": envelope.get("message", ""),
        "data": {
            "data": {
                "hasMore": _pick(inner, "has_more", "hasMore") or False,
                "cursor": _pick(inner, "cursor", "lastCursor") or "",
                "notes": notes_out,
            }
        },
    }


def user_notes_web_v2(raw, args):
    """web_v2/fetch_home_notes → 内部格式"""
    # web_v2 结构与 app_v2 类似，复用 adapter 逻辑
    return user_notes_app_v2(raw, args)


def user_notes_app(raw, args):
    """app/get_user_notes → 内部格式（结构与 app_v2 类似）"""
    return user_notes_app_v2(raw, args)


# ============================================================
# fetch_note_detail adapters
# ============================================================

def note_detail_web_v3(raw, args):
    """web_v3/fetch_note_detail → 内部格式（金标准，直接透传）"""
    return raw


def note_detail_app_v2(raw, args):
    """app_v2/get_image_note_detail 或 get_video_note_detail → 内部格式
    
    app_v2 返回: { code, data: { note: {noteId, desc, interactInfo, ...}, comments: {list: [...]} } }
    转成 web_v3 格式: { code, data: { data: { items: [{id, noteCard: {...}}] } } }
    """
    envelope, inner = _unwrap_data(raw)
    
    # 兼容 data.data 是 list 或者需要再挖一层的情况
    if isinstance(inner, dict) and "data" in inner:
        deeper = inner["data"]
        if isinstance(deeper, list):
            inner = deeper[0] if deeper else {}
        elif isinstance(deeper, dict):
            inner = deeper
    elif isinstance(inner, list):
        inner = inner[0] if inner else {}
    
    if not isinstance(inner, dict):
        return raw

    # app_v2 的 note 对象
    note_raw = _pick(inner, "note", "noteData") or {}
    comments_raw = _pick(inner, "comments") or {}
    comment_list = []
    if isinstance(comments_raw, dict):
        comment_list = _pick(comments_raw, "list", "comments") or []
    elif isinstance(comments_raw, list):
        comment_list = comments_raw

    if not note_raw and not inner.get("items"):
        # 可能 inner 本身就是 note
        if inner.get("noteId") or inner.get("note_id") or inner.get("desc"):
            note_raw = inner

    # 如果 inner 已经有 items（已是类 web_v3 格式），直接返回
    if isinstance(inner.get("items"), list) and inner["items"]:
        return raw

    user_raw = _pick(note_raw, "user") or {}
    video_raw = _pick(note_raw, "video") or {}

    note_card = {
        "type": _pick(note_raw, "type") or "normal",
        "title": _pick(note_raw, "title", "display_title", "displayTitle") or "",
        "desc": _pick(note_raw, "desc", "description", "content") or "",
        "time": _pick(note_raw, "time", "createTime", "create_time", "timestamp") or 0,
        "user": {
            "userId": _pick(user_raw, "userid", "userId", "user_id", "id") or "",
            "nickname": _pick(user_raw, "nickname", "nick_name") or "",
            "avatar": _pick(user_raw, "avatar") or "",
            "xsecToken": _pick(user_raw, "xsec_token", "xsecToken") or "",
        },
        "interactInfo": _extract_interact_flat(note_raw),
        "tagList": _extract_tags(note_raw),
        "imageList": _extract_image_list(note_raw),
        "video": video_raw,
        "atUserList": _pick(note_raw, "atUserList", "at_user_list", "ats") or [],
        # 保留评论到外部便于下游提取
        "_comments": {"list": comment_list},
    }

    note_id = _pick(note_raw, "noteId", "note_id", "id") or args.get("note_id", "")

    return {
        "code": envelope.get("code", 200),
        "message": envelope.get("message", ""),
        "data": {
            "data": {
                "items": [
                    {
                        "id": note_id,
                        "modelType": "note",
                        "noteCard": note_card,
                    }
                ],
            }
        },
    }


def _extract_interact_flat(note_raw):
    """
    从扁平字段中提取互动数据（web_v2/app 的 note_list 里互动字段是顶层的）。
    
    优先取嵌套的 interactInfo/interact_info，
    如果嵌套字段为空或全0，回退到顶层扁平字段 liked_count/collected_count 等。
    """
    # 先尝试嵌套结构
    interact_raw = _pick(note_raw, "interactInfo", "interact_info") or {}
    result = _normalize_interact(interact_raw)
    
    # 检查嵌套结果是否全为 "0"（说明嵌套字段不存在或为空）
    all_zero = all(v == "0" for v in result.values())
    
    if all_zero:
        # 回退到顶层扁平字段（web_v2/app 的原始格式）
        flat_interact = {
            "likedCount": _normalize_count(
                _pick(note_raw, "liked_count", "likedCount", "likes")),
            "collectedCount": _normalize_count(
                _pick(note_raw, "collected_count", "collectedCount", "collects")),
            "commentCount": _normalize_count(
                _pick(note_raw, "comments_count", "comment_count", "commentCount")),
            "shareCount": _normalize_count(
                _pick(note_raw, "shared_count", "shareCount", "sharedCount", "shares")),
        }
        # 如果扁平字段也全为 "0"，还是返回（但至少试过了）
        return flat_interact
    
    return result


def _extract_tags(note_raw):
    """
    提取标签列表。
    
    优先取 tagList/tag_list/tags，
    如果为空，回退到 hash_tag/hashtag（web_v2/app 的原始格式）。
    """
    tags = _pick(note_raw, "tagList", "tag_list", "tags") or []
    if tags:
        return tags
    
    # web_v2/app 用 hash_tag 字段
    hash_tags = _pick(note_raw, "hash_tag", "hashtag", "foot_tags") or []
    if isinstance(hash_tags, list) and hash_tags:
        return hash_tags
    
    return []


def _extract_image_list(note_raw):
    """
    提取图片列表。
    
    优先取 imageList/image_list，
    如果为空，回退到 images_list（web_v2 的原始格式）。
    """
    images = _pick(note_raw, "imageList", "image_list") or []
    if images:
        return images
    
    # web_v2 用 images_list
    images_list = _pick(note_raw, "images_list") or []
    if isinstance(images_list, list) and images_list:
        return images_list
    
    return []


def note_detail_app(raw, args):
    """app/get_note_info → 内部格式
    
    app 返回结构: { data: { code, data: [{note_list: [...], comment_list: [...]}] } }
    注意: data.data 可能是 list 而非 dict！
    """
    envelope, inner = _unwrap_data(raw)
    
    # _unwrap_data 可能只解了一层（当 data.data 是 list 时条件不满足）
    # 需要手动再往下挖
    if isinstance(inner, dict) and "data" in inner:
        deeper = inner["data"]
        if isinstance(deeper, list):
            inner = deeper[0] if deeper else {}
        elif isinstance(deeper, dict):
            inner = deeper
    elif isinstance(inner, list):
        inner = inner[0] if inner else {}
    
    if not isinstance(inner, dict):
        return raw

    # app 可能用 note_list 包裹
    note_list = _pick(inner, "note_list") or []
    note_raw = {}
    comment_list = _pick(inner, "comment_list") or []

    if isinstance(note_list, list) and note_list:
        note_raw = note_list[0] or {}
    else:
        note_raw = _pick(inner, "note", "noteData") or {}
        if not note_raw and (inner.get("noteId") or inner.get("note_id") or inner.get("desc")):
            note_raw = inner

    # 如果 inner 已经有 items（已是类 web_v3 格式），直接返回
    if isinstance(inner.get("items"), list) and inner["items"]:
        return raw

    user_raw = _pick(note_raw, "user") or {}

    note_card = {
        "type": _pick(note_raw, "type") or "normal",
        "title": _pick(note_raw, "title", "display_title", "displayTitle") or "",
        "desc": _pick(note_raw, "desc", "description", "content") or "",
        "time": _pick(note_raw, "time", "createTime", "create_time", "timestamp") or 0,
        "user": {
            "userId": _pick(user_raw, "userid", "userId", "user_id", "id") or "",
            "nickname": _pick(user_raw, "nickname", "nick_name") or "",
            "avatar": _pick(user_raw, "avatar") or "",
            "xsecToken": _pick(user_raw, "xsec_token", "xsecToken") or "",
        },
        "interactInfo": _extract_interact_flat(note_raw),
        "tagList": _extract_tags(note_raw),
        "imageList": _extract_image_list(note_raw),
        "video": _pick(note_raw, "video") or {},
        "atUserList": _pick(note_raw, "atUserList", "at_user_list", "ats") or [],
        "_comments": {"list": comment_list},
    }

    note_id = _pick(note_raw, "noteId", "note_id", "id") or args.get("note_id", "")

    return {
        "code": envelope.get("code", 200),
        "message": envelope.get("message", ""),
        "data": {
            "data": {
                "items": [
                    {
                        "id": note_id,
                        "modelType": "note",
                        "noteCard": note_card,
                    }
                ],
            }
        },
    }


def note_detail_web_v2(raw, args):
    """web_v2/fetch_feed_notes_v2 → 内部格式
    
    web_v2 feed_notes 返回多条笔记（目标 + 推荐），只取第一条
    结构类似 app，复用 note_detail_app 逻辑
    """
    envelope, inner = _unwrap_data(raw)
    if not isinstance(inner, dict):
        return raw

    # web_v2 可能返回 items / note_list
    items = _pick(inner, "items", "note_list", "notes") or []
    if isinstance(items, list) and items:
        # 找到目标 note_id
        target_id = args.get("note_id", "")
        target = None
        for it in items:
            it_id = _pick(it, "id", "note_id", "noteId") or ""
            # 可能嵌套在 noteCard / note 里
            if not it_id:
                nc = _pick(it, "noteCard", "note_card", "note") or {}
                it_id = _pick(nc, "id", "noteId", "note_id") or ""
            if it_id == target_id:
                target = it
                break
        if not target:
            target = items[0]

        # 构建临时 raw 给 note_detail_app 处理
        temp_raw = dict(envelope)
        temp_raw["data"] = {"data": target}
        return note_detail_app(temp_raw, args)

    return note_detail_app(raw, args)


# ============================================================
# fetch_note_comments adapters
# ============================================================

def note_comments_app_v2(raw, args):
    """app_v2/get_note_comments → 内部格式（透传，结构下游自行解析）"""
    return raw


def note_comments_web_v3(raw, args):
    """web_v3/fetch_note_comments → 内部格式（透传）"""
    return raw


def note_comments_web_v2(raw, args):
    """web_v2/fetch_note_comments → 内部格式（透传）"""
    return raw


def note_comments_app(raw, args):
    """app/get_note_comments → 内部格式（透传）"""
    return raw


# ============================================================
# 注册表 — adapter_name → function
# ============================================================

ADAPTERS = {
    # search_notes
    "search_notes_app_v2": search_notes_app_v2,
    "search_notes_web_v3": search_notes_web_v3,
    "search_notes_app": search_notes_app,
    "search_notes_web_v2": search_notes_web_v2,
    # search_users
    "search_users_app_v2": search_users_app_v2,
    "search_users_web_v3": search_users_web_v3,
    "search_users_web_v2": search_users_web_v2,
    "search_users_app": search_users_app,
    # fetch_user_info
    "user_info_app_v2": user_info_app_v2,
    "user_info_web_v3": user_info_web_v3,
    "user_info_app": user_info_app,
    "user_info_web_v2": user_info_web_v2,
    # fetch_user_notes
    "user_notes_app_v2": user_notes_app_v2,
    "user_notes_web_v3": user_notes_web_v3,
    "user_notes_web_v2": user_notes_web_v2,
    "user_notes_app": user_notes_app,
    # fetch_note_detail
    "note_detail_app_v2": note_detail_app_v2,
    "note_detail_web_v3": note_detail_web_v3,
    "note_detail_app": note_detail_app,
    "note_detail_web_v2": note_detail_web_v2,
    # fetch_note_comments
    "note_comments_app_v2": note_comments_app_v2,
    "note_comments_web_v3": note_comments_web_v3,
    "note_comments_web_v2": note_comments_web_v2,
    "note_comments_app": note_comments_app,
}
