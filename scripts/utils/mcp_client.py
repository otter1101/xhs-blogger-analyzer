"""
MCP Client — xiaohongshu-mcp 通用HTTP调用封装
仅依赖 Python 标准库（urllib），兼容所有平台。

用法：
    from utils.mcp_client import MCPClient
    client = MCPClient()              # 默认 localhost:18060
    client = MCPClient(port=18061)    # 自定义端口
    
    # 单次调用
    data = client.call("search_feeds", {"keyword": "<博主名>"})
    
    # 带重试
    data = client.call("get_feed_detail", {"feed_id": "xxx"}, retries=2)
"""

import json
import urllib.request
import urllib.error
import time
import sys
import os

# 默认配置
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 18060
DEFAULT_TIMEOUT = 120
PROTOCOL_VERSION = "2024-11-05"
CLIENT_INFO = {"name": "xhs-skill", "version": "0.4.0"}


class MCPError(Exception):
    """MCP 调用异常"""
    pass


class MCPClient:
    """xiaohongshu-mcp HTTP JSON-RPC 客户端"""

    def __init__(self, host=None, port=None, timeout=None):
        self.host = host or os.environ.get("XHS_MCP_HOST", DEFAULT_HOST)
        self.port = int(port or os.environ.get("XHS_MCP_PORT", DEFAULT_PORT))
        self.timeout = timeout or DEFAULT_TIMEOUT
        self.base_url = f"http://{self.host}:{self.port}/mcp"

    # ----------------------------------------------------------
    # 核心：三步调用协议
    # ----------------------------------------------------------
    def _raw_call(self, tool_name, tool_args, timeout=None):
        """执行一次完整的 init → notify → call 三步调用"""
        timeout = timeout or self.timeout
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

        # Step 1: Initialize
        init_payload = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
        }).encode()

        req1 = urllib.request.Request(self.base_url, data=init_payload, headers=headers)
        resp1 = urllib.request.urlopen(req1, timeout=30)
        session_id = resp1.headers.get("Mcp-Session-Id", "")
        if not session_id:
            raise MCPError("MCP 服务未返回 Session ID，请检查服务是否正常运行")

        # Step 2: Notify initialized
        headers["Mcp-Session-Id"] = session_id
        notify_payload = json.dumps({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        }).encode()
        req2 = urllib.request.Request(self.base_url, data=notify_payload, headers=headers, method="POST")
        urllib.request.urlopen(req2, timeout=30)

        # Step 3: Tool call
        call_payload = json.dumps({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": tool_args or {},
            },
        }).encode()
        req3 = urllib.request.Request(self.base_url, data=call_payload, headers=headers, method="POST")
        resp3 = urllib.request.urlopen(req3, timeout=timeout)
        return json.loads(resp3.read().decode())

    # ----------------------------------------------------------
    # 公开 API
    # ----------------------------------------------------------
    def call(self, tool_name, tool_args=None, retries=1, delay=3, timeout=None):
        """
        调用 MCP 工具，返回解析后的数据（dict）。
        
        Args:
            tool_name: 工具名（search_feeds / user_profile / get_feed_detail 等）
            tool_args: 参数字典
            retries: 失败重试次数（默认1次，即总共最多尝试2次）
            delay: 重试间隔秒数
            timeout: 本次调用超时（秒）
        
        Returns:
            dict — 解析后的工具返回数据
        
        Raises:
            MCPError — 调用失败
        """
        last_error = None
        for attempt in range(1 + retries):
            try:
                raw = self._raw_call(tool_name, tool_args or {}, timeout)
                return self._extract_data(raw)
            except urllib.error.URLError as e:
                last_error = e
                if attempt < retries:
                    time.sleep(delay)
            except Exception as e:
                last_error = e
                if attempt < retries:
                    time.sleep(delay)

        raise MCPError(f"MCP 调用 {tool_name} 失败（重试{retries}次后）: {last_error}")

    def call_raw(self, tool_name, tool_args=None, timeout=None):
        """调用 MCP 工具，返回原始 JSON-RPC 响应"""
        return self._raw_call(tool_name, tool_args or {}, timeout)

    def is_alive(self):
        """检查 MCP 服务是否可达"""
        try:
            req = urllib.request.Request(self.base_url, method="GET")
            urllib.request.urlopen(req, timeout=5)
            return True
        except (urllib.error.URLError, OSError, Exception):
            # MCP 可能不支持 GET，试 POST init
            try:
                self._raw_call("check_login_status", {}, timeout=10)
                return True
            except (urllib.error.URLError, OSError, MCPError, Exception):
                return False

    def check_login(self):
        """
        检查小红书登录状态。
        Returns: (bool, str) — (是否已登录, 状态描述)
        """
        try:
            data = self.call("check_login_status")
            text = str(data)
            if "已登录" in text or "logged" in text.lower() or "true" in text.lower():
                return True, "已登录"
            return False, f"未登录: {text[:200]}"
        except MCPError as e:
            return False, f"检查失败: {e}"

    def get_login_qrcode(self):
        """
        获取小红书登录二维码。
        Returns: dict — 包含 qrUrl / qrBase64 等字段
        Raises: MCPError
        """
        return self.call("get_login_qrcode")

    # ----------------------------------------------------------
    # 内部工具
    # ----------------------------------------------------------
    @staticmethod
    def _extract_data(raw_response):
        """从 JSON-RPC 响应中提取有效数据"""
        result = raw_response.get("result", {})
        contents = result.get("content", [])

        texts = []
        for c in contents:
            if c.get("type") == "text":
                texts.append(c["text"])

        combined = "\n".join(texts)
        if not combined:
            return raw_response

        # 尝试解析为 JSON
        try:
            return json.loads(combined)
        except (json.JSONDecodeError, ValueError):
            return {"_raw_text": combined}

    def __repr__(self):
        return f"MCPClient({self.base_url})"


# ----------------------------------------------------------
# 命令行入口（调试用）
# ----------------------------------------------------------
if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    client = MCPClient()

    if len(sys.argv) < 2:
        print("用法: python mcp_client.py <tool_name> [args_json]")
        print("示例: python mcp_client.py search_feeds '{\"keyword\":\"<博主名>\"}'")
        print(f"\n当前配置: {client}")
        print(f"服务状态: {'✅ 可达' if client.is_alive() else '❌ 不可达'}")
        sys.exit(0)

    tool = sys.argv[1]
    args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    
    print(f"调用: {tool}({args})")
    try:
        data = client.call(tool, args)
        print(json.dumps(data, ensure_ascii=False, indent=2))
    except MCPError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)
