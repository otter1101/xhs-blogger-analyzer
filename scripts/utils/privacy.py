"""
privacy.py — 评论者身份脱敏工具

对应 skill 版本：v2.0（API + 合规改造版）

## 脱敏规则

1. **身份字段完全删除**：userid / userId / nickname / avatar / images / ip_location / ipLocation
2. **身份标识替换**：
   - 博主本人（show_tags / showTags 含 "is_author"）→ speaker = "作者"
   - 其他评论者 → speaker = "读者N"，同一 userid 跨评论保持同一编号
3. **评论正文保留**：content 字段原样保留
4. **交互数据保留**：like_count / likeCount / create_time / 楼层结构
5. **递归脱敏**：sub_comments / subComments / target_comment / targetComment 同规则递归

## 使用

    from scripts.utils.privacy import anonymize_comments, PRIVACY_VERSION

    clean = anonymize_comments(raw_comments)
    # clean 里已不存在任何可识别身份字段

## 设计原则

- **源头脱敏**：在数据写入 raw JSON 之前调用一次，下游看不到原始身份
- **幂等**：对已脱敏数据再次调用不会出错（已含 speaker 字段则跳过）
- **字段双兼容**：snake_case（app/app_v2 端点）和 camelCase（web_v3 端点 + adapters）都支持
"""

from typing import Any

# 合规改造版本号，下游 _meta 字段会引用这个
PRIVACY_VERSION = "v2.0"

# ---- 需要脱敏（完全删除）的身份字段 ----
_IDENTITY_FIELDS = frozenset([
    # snake_case（app/app_v2 原生）
    "userid", "user_id", "nickname", "avatar", "images",
    "ip_location", "location",
    # camelCase（web_v3 + adapters）
    "userId", "ipLocation", "userInfo",
    # 嵌套引用（target_comment 里的 user 也要处理）
    "user",
])

# ---- 作者标记字段（保留用于判定，但统一转成 is_author bool）----
_AUTHOR_TAG_FIELDS = ("show_tags", "showTags")


def _is_author(comment: dict) -> bool:
    """判定一条评论是否来自博主本人。

    规则：show_tags / showTags 里出现 'is_author' 字符串即认为是作者。
    也兼容已经被归一化过的 is_author: True 字段。
    """
    if comment.get("is_author") is True:
        return True
    for field in _AUTHOR_TAG_FIELDS:
        tags = comment.get(field)
        if tags is None:
            continue
        # tags 可能是 list[str] 或 str
        tags_str = str(tags)
        if "is_author" in tags_str:
            return True
    return False


def _extract_userid(comment: dict) -> str | None:
    """从一条评论里抽出 userid，用于跨评论保持同一编号。

    优先顺序：
    1. 顶层 userid / user_id / userId
    2. 嵌套 user.userid / user.user_id / user.userId
    3. 嵌套 userInfo.userid / userInfo.userId
    """
    for key in ("userid", "user_id", "userId"):
        if comment.get(key):
            return str(comment[key])

    user = comment.get("user")
    if isinstance(user, dict):
        for key in ("userid", "user_id", "userId"):
            if user.get(key):
                return str(user[key])

    user_info = comment.get("userInfo")
    if isinstance(user_info, dict):
        for key in ("userid", "user_id", "userId"):
            if user_info.get(key):
                return str(user_info[key])

    return None


def _strip_identity(comment: dict) -> dict:
    """移除评论 dict 里的所有身份字段（浅层），保留其他字段。"""
    return {k: v for k, v in comment.items() if k not in _IDENTITY_FIELDS}


def _anonymize_one(
    comment: dict,
    reader_map: dict[str, str],
    reader_counter: list[int],
) -> dict:
    """脱敏单条评论（不处理嵌套子评论，由外层循环递归处理）。

    reader_map: {userid: "读者N"} 跨评论保持一致
    reader_counter: [int] 单元素列表当 mutable counter 用
    """
    # 已脱敏幂等保护
    if "speaker" in comment and comment.get("speaker") in (None, "",):
        pass  # speaker 为空还是当未脱敏处理
    elif "speaker" in comment:
        return comment  # 已经脱敏过，直接返回

    is_author = _is_author(comment)
    userid = _extract_userid(comment)

    # 分配 speaker
    if is_author:
        speaker = "作者"
    elif userid is None:
        # 拿不到 userid：退化为按出现顺序编号，不进映射表
        reader_counter[0] += 1
        speaker = f"读者{reader_counter[0]}"
    elif userid in reader_map:
        speaker = reader_map[userid]
    else:
        reader_counter[0] += 1
        speaker = f"读者{reader_counter[0]}"
        reader_map[userid] = speaker

    # 脱敏后重建 dict
    clean = _strip_identity(comment)
    clean["speaker"] = speaker
    clean["is_author"] = is_author

    # 处理 target_comment / targetComment（被 @ 的评论者）
    for tc_key in ("target_comment", "targetComment"):
        tc = comment.get(tc_key)
        if isinstance(tc, dict):
            tc_userid = _extract_userid(tc)
            tc_is_author = _is_author(tc)
            if tc_is_author:
                reply_to = "作者"
            elif tc_userid and tc_userid in reader_map:
                reply_to = reader_map[tc_userid]
            elif tc_userid:
                reader_counter[0] += 1
                reply_to = f"读者{reader_counter[0]}"
                reader_map[tc_userid] = reply_to
            else:
                reply_to = "某读者"
            clean["reply_to"] = reply_to
            # 原始 target_comment 整体丢弃
            clean.pop(tc_key, None)

    return clean


def anonymize_comments(comments: list[dict]) -> list[dict]:
    """对一个评论列表整体做脱敏，返回新列表（不修改原对象）。

    评论结构（支持 snake_case 和 camelCase 两种风格）：
      - 顶层：id / content / show_tags|showTags / user|userInfo / sub_comments|subComments
      - 子评论递归同规则处理

    返回结构（统一）：
      {
        "id": "...",
        "content": "...",
        "speaker": "读者1" | "作者",
        "is_author": bool,
        "like_count": int,           # 如果原始有则保留
        "create_time": ...,          # 如果原始有则保留
        "reply_to": "读者N" | "作者" | "某读者",  # 仅 sub_comments 里有 target_comment 时
        "sub_comments": [ ... ],     # 递归脱敏后的子评论
      }

    身份字段（userid / nickname / avatar / ip_location 等）全部删除。
    """
    if not comments:
        return []

    # 一个博主维度的全局映射表：同一 userid 跨评论保持同一编号
    reader_map: dict[str, str] = {}
    reader_counter = [0]

    def _recurse(lst: list[dict]) -> list[dict]:
        result = []
        for c in lst:
            if not isinstance(c, dict):
                continue
            clean = _anonymize_one(c, reader_map, reader_counter)

            # 递归处理子评论（支持两种字段名）
            for sub_key in ("sub_comments", "subComments"):
                subs = c.get(sub_key)
                if isinstance(subs, list) and subs:
                    clean[sub_key] = _recurse(subs)

            result.append(clean)
        return result

    return _recurse(comments)


def anonymize_note_comments_inplace(note: dict) -> None:
    """便捷方法：对单条 note 的评论做原地脱敏。

    note 结构假设：
      note["comments"]["list"] = [ ... ]  # 评论列表

    如果 note 没有 comments.list 则什么都不做。
    """
    comments_obj = note.get("comments")
    if not isinstance(comments_obj, dict):
        return
    comment_list = comments_obj.get("list")
    if not isinstance(comment_list, list):
        return
    comments_obj["list"] = anonymize_comments(comment_list)


__all__ = [
    "PRIVACY_VERSION",
    "anonymize_comments",
    "anonymize_note_comments_inplace",
]
