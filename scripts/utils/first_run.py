"""
first_run.py — 首次运行合规提示

对应 skill 版本：v2.0（API + 合规改造版）

## 作用

在 run.py 入口处调用一次 ensure_first_run_ack()：
- 如果 data/.first_run_ack 已存在 → 什么都不做，直接放行
- 如果不存在 → 打印一次性横幅提示使用边界，然后创建 .first_run_ack 文件

## 设计原则（对应方案 v3.2 哲学第 3 条 "不搞仪式感合规"）

- 不阻塞用户（不要求按 Enter 确认），打印后直接继续
- 一次性展示，后续运行无感
- 标记文件放在 data/ 目录下（跟本地数据同一层级，删 data 目录即重置）

## 使用

    from scripts.utils.first_run import ensure_first_run_ack

    if __name__ == "__main__":
        ensure_first_run_ack()
        # ... 原有主逻辑
"""

from pathlib import Path

# 标记文件路径：固定在项目根目录下的 data/.first_run_ack
# 项目根 = 本文件所在目录的上两级（scripts/utils/first_run.py → 项目根）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ACK_FILE = _PROJECT_ROOT / "data" / ".first_run_ack"


BANNER = """
================================================================================
  博主蒸馏器 v2.0  ·  合规使用提示
================================================================================

  本工具仅供「学习研究」使用，首次运行请确认以下要点：

  🔹 数据来源：TikHub 公开 REST API 获取小红书公开页面数据
              （不模拟登录、不注入 Cookie、不破解加密接口）
  🔹 适用范围：仅用于研究公开发布的内容
              （公开账号 / 公开笔记 / 公开评论）
  🔹 评论者隐私：评论者身份自动脱敏为「读者1 / 读者2 / 作者」
              （不保留昵称、userId、头像、IP 属地；评论正文保留用于研究）
  🔹 第三方费用：TikHub API 调用费用由你自行承担
  🔹 风险自担：使用产生的一切后果由使用者承担

  完整条款见项目根目录：
    ├── DISCLAIMER.md    免责声明与使用边界
    └── SECURITY.md      数据安全与隐私处理

  如不同意上述条款，请按 Ctrl+C 退出。继续运行视为已阅读并同意。

================================================================================
"""


def ensure_first_run_ack() -> bool:
    """首次运行检查，返回 True 表示本次是首次运行（已打印 banner 并写 ack 文件），
    False 表示已经 ack 过。

    标记文件：data/.first_run_ack
    首次运行时自动创建 data/ 目录（如果不存在）。
    """
    if _ACK_FILE.exists():
        return False

    # 打印 banner（stdout，不用 print 的 flush=True 也能看到因为横幅足够长）
    print(BANNER)

    # 写入标记文件
    try:
        _ACK_FILE.parent.mkdir(parents=True, exist_ok=True)
        _ACK_FILE.write_text(
            "Blogger Distiller v2.0 first-run acknowledged.\n"
            "Delete this file to show the banner again on next run.\n",
            encoding="utf-8",
        )
    except OSError as e:
        # 写失败不阻塞用户，只是下次启动还会再看一次 banner
        print(f"[first_run] 提示标记文件写入失败：{e}（不影响使用，下次启动会再次显示提示）")

    return True


__all__ = ["ensure_first_run_ack"]
