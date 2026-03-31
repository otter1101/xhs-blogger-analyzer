"""通用工具函数"""

import re


def parse_count(s):
    """解析 '1.2万' / '1,234' / '12' 等格式为整数"""
    if not s:
        return 0
    s = str(s).strip().replace(",", "")
    if not s:
        return 0
    try:
        if "万" in s:
            return int(float(s.replace("万", "")) * 10000)
        return int(s)
    except (ValueError, TypeError):
        return 0


def safe_filename(name):
    """将字符串转为安全文件名"""
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()
