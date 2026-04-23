"""
数据质量分级 + 自愈合并 — 用于决策哪些 note 需要轮次2补调

核心逻辑：
  check_note_quality(entry)  → 分级（complete / partial / failed）
  merge_note_supplement(existing, supplement) → 把补调结果非空字段合并到原记录

设计原则：
  - 轮次1 宽松：拿到正文就算成功（不强求互动数据）
  - 轮次2 针对：只补缺失字段，已有非空字段不覆盖
"""


def check_note_quality(note_entry):
    """
    对单条 note_entry 做数据质量分级。

    Args:
        note_entry: crawl_blogger 里 get_all_details 产出的单条记录
                    结构: {note: {...}, comments: {...}, _meta: {...}, _feed_id, _content_restricted}

    Returns:
        dict:
            - level: "complete" | "partial" | "failed"
            - missing: [str]  缺失的字段类别列表
            - has_content: bool
            - has_author: bool
            - has_interact: bool
            - has_time: bool
            - has_comments: bool
    """
    result = {
        "level": "failed",
        "missing": [],
        "has_content": False,
        "has_author": False,
        "has_interact": False,
        "has_time": False,
        "has_comments": False,
    }

    # 已标记为受限的笔记，直接判 failed
    if note_entry.get("_content_restricted") or note_entry.get("_error"):
        result["missing"] = ["*all*"]
        return result

    note = note_entry.get("note", {})
    if not isinstance(note, dict):
        result["missing"] = ["*all*"]
        return result

    missing = []

    # ---- 基础正文 ----
    title = (note.get("title") or "").strip()
    desc = (note.get("desc") or "").strip()
    has_content = bool(title or desc)
    result["has_content"] = has_content
    if not has_content:
        result["missing"] = ["content"]
        result["level"] = "failed"
        return result

    # ---- 作者 ----
    user = note.get("user") or {}
    has_author = bool(isinstance(user, dict) and (user.get("nickname") or user.get("userId")))
    result["has_author"] = has_author
    if not has_author:
        missing.append("author")

    # ---- 互动数据 ----
    interact = note.get("interactInfo") or note.get("interact_info") or {}
    has_interact = False
    if isinstance(interact, dict):
        for k in ("likedCount", "collectedCount", "commentCount",
                   "liked_count", "collected_count", "comment_count"):
            v = str(interact.get(k, "0") or "0").replace(",", "")
            if v not in ("", "0", "None"):
                has_interact = True
                break
    result["has_interact"] = has_interact
    if not has_interact:
        missing.append("interact")

    # ---- 时间 ----
    time_val = note.get("time") or note.get("createTime") or note.get("create_time") or 0
    has_time = bool(time_val and time_val != 0)
    result["has_time"] = has_time
    if not has_time:
        missing.append("time")

    # ---- 评论 ----
    comments_obj = note_entry.get("comments") or {}
    if isinstance(comments_obj, dict):
        comment_list = comments_obj.get("list") or comments_obj.get("comments") or []
    elif isinstance(comments_obj, list):
        comment_list = comments_obj
    else:
        comment_list = []
    has_comments = len(comment_list) > 0
    result["has_comments"] = has_comments
    if not has_comments:
        missing.append("comments")

    # ---- 最终分级 ----
    result["missing"] = missing
    result["level"] = "complete" if not missing else "partial"
    return result


def merge_note_supplement(existing, supplement):
    """
    把 supplement 里的非空字段合并到 existing 中，已有非空字段不动。

    Args:
        existing: 轮次1 的 note_entry 结构 {note:{}, comments:{}, _meta:{}, ...}
        supplement: 轮次2 补调拿到的 note_entry（同结构）

    Returns:
        合并后的 note_entry（不修改原 dict，返回新 copy）
    """
    result = {}
    for k, v in existing.items():
        result[k] = v
    result["note"] = dict(existing.get("note") or {})

    sup_note = supplement.get("note") or {}

    # 顶层字段合并（title / desc / type / time / tagList / imageList / video 等）
    for key, val in sup_note.items():
        if key in ("user", "interactInfo", "interact_info", "_comments"):
            continue  # 这些需要特殊处理
        if _is_empty_value(result["note"].get(key)) and not _is_empty_value(val):
            result["note"][key] = val

    # interactInfo 特殊处理：逐字段补
    sup_interact = sup_note.get("interactInfo") or sup_note.get("interact_info") or {}
    if sup_interact and isinstance(sup_interact, dict):
        existing_interact = result["note"].get("interactInfo") or result["note"].get("interact_info") or {}
        new_interact = dict(existing_interact)
        for k, v in sup_interact.items():
            if _is_empty_value(new_interact.get(k)) and not _is_empty_value(v):
                new_interact[k] = v
        result["note"]["interactInfo"] = new_interact

    # user 特殊处理：逐字段补
    sup_user = sup_note.get("user") or {}
    if sup_user and isinstance(sup_user, dict):
        existing_user = result["note"].get("user") or {}
        new_user = dict(existing_user)
        for k, v in sup_user.items():
            if _is_empty_value(new_user.get(k)) and not _is_empty_value(v):
                new_user[k] = v
        result["note"]["user"] = new_user

    # comments 补充（仅在原来为空时覆盖）
    existing_comments_obj = existing.get("comments") or {}
    if isinstance(existing_comments_obj, dict):
        existing_comments = existing_comments_obj.get("list") or existing_comments_obj.get("comments") or []
    elif isinstance(existing_comments_obj, list):
        existing_comments = existing_comments_obj
    else:
        existing_comments = []

    sup_comments_obj = supplement.get("comments") or {}
    if isinstance(sup_comments_obj, dict):
        sup_comments = sup_comments_obj.get("list") or sup_comments_obj.get("comments") or []
    elif isinstance(sup_comments_obj, list):
        sup_comments = sup_comments_obj
    else:
        sup_comments = []

    if not existing_comments and sup_comments:
        result["comments"] = supplement["comments"]

    # 标记已补调
    meta = dict(result.get("_meta") or {})
    meta["repaired"] = True
    sup_ep = supplement.get("_meta", {}).get("source_endpoint", "")
    sup_group = supplement.get("_meta", {}).get("source_group", "")
    if sup_ep:
        meta["repair_endpoint"] = sup_ep
    if sup_group:
        meta["repair_group"] = sup_group
    result["_meta"] = meta

    return result


def _is_empty_value(v):
    """判断一个值是否算"空"（需要被补调覆盖的）"""
    if v is None:
        return True
    if isinstance(v, str):
        return v.strip() in ("", "0")
    if isinstance(v, (list, dict)):
        return len(v) == 0
    if isinstance(v, (int, float)):
        return v == 0
    return False
