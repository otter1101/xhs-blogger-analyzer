"""
verify.py — 采集数据质量校验模块

数据结构约定（crawl_blogger.py 输出的 notes_details.json）：
  每个条目顶层字段：
    _feed_id   — 笔记 ID
    note       — 笔记主体 dict
    comments   — { list: [...] }
    _meta      — 内部元数据

  note 内部关键字段：
    desc       — 正文（视频和图文均在此字段）
    time       — 发布时间戳（unix int）
    interactInfo — { likedCount, collectedCount, commentCount, shareCount }
    title      — 标题

校验函数返回值约定：
  check_content_completeness → (bool, str)  阻断型，False 时调用方应 sys.exit(1)
  check_note_count / check_time_field / check_duplicates / get_sample_watermark → str  警告型
  check_output_files → (bool, str)  阻断型
"""

import os


def _get_note_obj(item):
    """从一个条目里取出 note 对象，兼容两种结构：
      - 标准结构：{ note: {...}, ... }
      - 降级结构：item 本身就是 note（极少见）
    """
    return item.get("note") or item


# ──────────────────────────────────────────
# V1：正文完整性（阻断型）
# ──────────────────────────────────────────

def check_content_completeness(details, threshold=0.5):
    """检查正文完整率，低于 threshold 则返回 False（调用方应中止）。

    正文字段：note.desc（视频和图文均用此字段）。
    threshold 默认 0.5，低于 50% 认为数据采集严重不足。
    """
    if not details:
        return False, "❌ V1 正文完整性：无有效笔记数据"

    ok = 0
    for item in details:
        note = _get_note_obj(item)
        desc = note.get("desc") or ""
        if len(str(desc).strip()) > 10:
            ok += 1

    ratio = ok / len(details)
    label = f"{ok}/{len(details)} ({ratio:.0%})"

    if ratio < threshold:
        return False, f"❌ V1 正文完整性：{label}，低于阈值 {threshold:.0%}"
    return True, f"✅ V1 正文完整性：{label}"


# ──────────────────────────────────────────
# V2：采集数量（警告型）
# ──────────────────────────────────────────

def check_note_count(details, max_notes):
    """检查实际采集数量是否达到目标的 70%。"""
    n = len(details)
    target_70 = int(max_notes * 0.7)
    if n >= target_70:
        return f"✅ V2 采集数量：{n} 条（目标 {max_notes} 条）"
    return f"⚠️ V2 采集数量：{n} 条（目标 {max_notes}，仅达 {n/max_notes:.0%}）"


# ──────────────────────────────────────────
# V3：时间字段（警告型）
# ──────────────────────────────────────────

def check_time_field(details):
    """检查 note.time 字段覆盖率。"""
    if not details:
        return "⚠️ V3 时间字段：无数据"

    has_time = 0
    for item in details:
        note = _get_note_obj(item)
        if note.get("time") or note.get("create_time") or note.get("publish_time"):
            has_time += 1

    ratio = has_time / len(details)
    label = f"{has_time}/{len(details)} ({ratio:.0%})"
    if ratio >= 0.8:
        return f"✅ V3 时间字段：{label}"
    return f"⚠️ V3 时间字段：{label}（覆盖率偏低）"


# ──────────────────────────────────────────
# V4：去重检查（警告型）
# ──────────────────────────────────────────

def check_duplicates(details):
    """检查笔记是否有重复（以 _feed_id 去重）。"""
    ids = []
    for item in details:
        fid = item.get("_feed_id") or _get_note_obj(item).get("id") or _get_note_obj(item).get("note_id")
        if fid:
            ids.append(str(fid))

    if not ids:
        return "⚠️ V4 去重检查：未找到笔记 ID 字段"

    unique = len(set(ids))
    total = len(ids)
    if unique == total:
        return f"✅ V4 去重检查：{total} 条，无重复"
    return f"⚠️ V4 去重检查：{total} 条中有 {total - unique} 条重复 ID"


# ──────────────────────────────────────────
# V5：数据水印（信息型）
# ──────────────────────────────────────────

def get_sample_watermark(details, profile):
    """生成一行数据水印，供日志追踪用。"""
    nickname = (
        profile.get("nickname") or profile.get("name")
        or profile.get("userInfo", {}).get("nickname") or "未知博主"
    )
    fans = (
        profile.get("fans") or profile.get("follower_count")
        or profile.get("fansCount") or "N/A"
    )
    return f"✅ V5 数据水印：博主={nickname}，粉丝={fans}，采集={len(details)}条"


# ──────────────────────────────────────────
# V6：产出文件完整性（阻断型）
# ──────────────────────────────────────────

def check_output_files(output_dir, expected_files):
    """检查 output_dir 下是否存在所有 expected_files（相对路径列表）。"""
    missing = []
    for rel_path in expected_files:
        if not os.path.exists(os.path.join(output_dir, rel_path)):
            missing.append(rel_path)

    if missing:
        return False, f"⚠️ V6 产出文件缺失: {', '.join(missing)}"
    return True, f"✅ V6 产出文件校验通过（{len(expected_files)} 个文件）"
