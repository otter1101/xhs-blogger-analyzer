"""
EndpointRouter — 端点池路由 + 自动降级引擎

职责：
  1. 从 endpoints.json 加载端点池配置
  2. 按优先级顺序尝试端点
  3. 失败时自动降级到下一个端点
  4. 会话内缓存死链，避免重复碰壁
  5. 调用 Adapter 对返回数据做归一化

用法（仅由 TikHubClient 内部调用）：
    router = EndpointRouter(request_func)
    result = router.call("search_notes", {"keyword": "xxx", "page": 1})
"""

import json
import os
import re
import time

from .adapters import ADAPTERS, _is_empty


class EndpointRouter:
    """端点池路由器"""

    # 这些 HTTP 状态码触发降级（跳到下一个端点）
    DEGRADABLE_CODES = {400, 404, 500, 502, 503, 504}
    # 这些不降级（认证/权限/余额问题，降级也没用）
    NON_DEGRADABLE_CODES = {401, 402, 403}

    def __init__(self, request_func, config_path=None):
        """
        Args:
            request_func: HTTP 请求函数，签名为 request(method, path, params, retries, delay) -> dict
            config_path: endpoints.json 路径（默认与本文件同目录）
        """
        self._request = request_func
        self._dead_endpoints = {}  # key = "group:path" → True（会话内精确死链缓存）
        self._dead_category_groups = {}  # key = "category:group" → True（跨池、同类型 group 死链）
        self._soft_fail_counts = {}  # key = "group:path" → int（软失败计数）
        self._http400_counts = {}  # key = "group:path" → int（连续 HTTP 400 计数）

        # 池名 → 类别映射（同类别的池共享 group 级别死链缓存）
        self._pool_categories = {
            # 搜索类
            "search_notes": "search",
            "search_users": "search",
            # 用户信息类
            "fetch_user_info": "user",
            "fetch_user_notes": "user",
            # 笔记详情类
            "fetch_note_detail_image": "detail",
            "fetch_note_detail_video": "detail",
            # 评论类（独立，不跟其他类型共享死链）
            "fetch_note_comments": "comments",
        }

        if config_path is None:
            config_path = os.path.join(os.path.dirname(__file__), "endpoints.json")

        self._pools = self._load_config(config_path)

    def _load_config(self, path):
        """加载并校验 endpoints.json"""
        if not os.path.isfile(path):
            raise FileNotFoundError(f"端点配置文件不存在: {path}")
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"端点配置文件 JSON 语法错误: {e}")

        pools = cfg.get("pools", {})
        if not pools:
            raise ValueError("端点配置文件中 pools 为空")

        # 校验每个 pool 内的 endpoint 必填字段
        required_fields = {"group", "path", "params", "adapter"}
        for pool_name, endpoints in pools.items():
            if not isinstance(endpoints, list) or not endpoints:
                raise ValueError(f"pool '{pool_name}' 必须是非空数组")
            for i, ep in enumerate(endpoints):
                missing = required_fields - set(ep.keys())
                if missing:
                    raise ValueError(f"pool '{pool_name}'[{i}] 缺少字段: {missing}")
                if ep["adapter"] not in ADAPTERS:
                    raise ValueError(
                        f"pool '{pool_name}'[{i}] 的 adapter '{ep['adapter']}' "
                        f"未在 adapters.py 中注册"
                    )

        return pools

    def _render_params(self, template, args):
        """渲染参数模板：把 '${key}' 替换为 args[key] 的值"""
        rendered = {}
        for k, v in template.items():
            if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
                arg_key = v[2:-1]
                arg_val = args.get(arg_key)
                # 如果调用方没传这个参数，跳过（不把 None 放进去）
                if arg_val is not None and arg_val != "":
                    rendered[k] = arg_val
            else:
                # 静态值（如 "sort": "general"）
                rendered[k] = v
        return rendered

    def _ep_key(self, ep):
        """生成端点唯一标识"""
        return f"{ep['group']}:{ep['path']}"

    def _is_dead(self, ep, pool_name=""):
        """检查端点是否在死链缓存中（精确匹配 or 同类别 group 级别）"""
        if self._dead_endpoints.get(self._ep_key(ep), False):
            return True
        # 同类别 group 级别死链（web_v3 在详情端点400了 → 其他详情端点也跳过，但不影响评论端点）
        category = self._pool_categories.get(pool_name, pool_name)
        cat_key = f"{category}:{ep['group']}"
        if self._dead_category_groups.get(cat_key, False):
            return True
        return False

    def _mark_dead(self, ep, reason="", pool_name=""):
        """标记端点为死链（同时标记同类别 group 级别）"""
        key = self._ep_key(ep)
        self._dead_endpoints[key] = True
        # 同类别 group 级别死链
        category = self._pool_categories.get(pool_name, pool_name)
        cat_key = f"{category}:{ep['group']}"
        self._dead_category_groups[cat_key] = True
        print(f"  ⛔ 标记死链: [{ep['group']}] {ep['path']} ({reason}) [类别:{category}]")

    def _mark_soft_fail(self, ep, pool_name=""):
        """标记软失败（HTTP 200 但数据空）"""
        key = self._ep_key(ep)
        count = self._soft_fail_counts.get(key, 0) + 1
        self._soft_fail_counts[key] = count
        # 评论类端点阈值更高（间歇性空数据是正常的，某条笔记可能真的没评论）
        category = self._pool_categories.get(pool_name, pool_name)
        threshold = 5 if category == "comments" else 2
        if count >= threshold:
            self._mark_dead(ep, "连续空数据", pool_name)

    def call(self, pool_name, args, retries=1, delay=2, skip_endpoints=None):
        """
        按端点池优先级调用 API，自动降级。

        Args:
            pool_name: 池名称（如 "search_notes"）
            args: 调用参数 dict（如 {"keyword": "xxx", "page": 1}）
            retries: 每个端点的重试次数
            delay: 重试间隔
            skip_endpoints: 要跳过的端点标识列表（用于补调时排除已用端点）

        Returns:
            归一化后的 dict（包含 _endpoint_used 和 _endpoint_group 元信息）

        Raises:
            TikHubError: 所有端点均失败
        """
        # 延迟导入避免循环引用
        from .tikhub_client import TikHubError

        pool = self._pools.get(pool_name)
        if not pool:
            raise TikHubError(f"未知的端点池: {pool_name}")

        skip_set = set(skip_endpoints or [])

        errors = []
        actually_tried = 0  # 实际尝试 HTTP 请求的次数
        for i, ep in enumerate(pool):
            if self._is_dead(ep, pool_name):
                continue

            # 补调时跳过已用端点
            if self._ep_key(ep) in skip_set:
                continue

            # 渲染参数
            params = self._render_params(ep["params"], args)
            method = ep.get("method", "GET")
            adapter_name = ep["adapter"]
            adapter_func = ADAPTERS[adapter_name]

            group_tag = f"[{ep['group']}]"
            if actually_tried > 0:
                print(f"  🔄 降级到 {group_tag} {ep['path']}")
            actually_tried += 1

            try:
                raw = self._request(method, ep["path"], params, retries=retries, delay=delay)
                normalized = adapter_func(raw, args)

                # 检查"假成功"（HTTP 200 但数据为空）
                if _is_empty(normalized):
                    # 评论端点特殊处理：HTTP 200 + 空评论 = 正常（该笔记确实无评论），
                    # 直接返回空结果，不标记软失败、不降级
                    category = self._pool_categories.get(pool_name, pool_name)
                    if category == "comments":
                        normalized["_endpoint_used"] = self._ep_key(ep)
                        normalized["_endpoint_group"] = ep["group"]
                        return normalized
                    self._mark_soft_fail(ep, pool_name)
                    errors.append(f"{group_tag} 返回空数据")
                    continue

                # 成功！如果是降级后成功的，打印提示
                if actually_tried > 1:
                    print(f"  ✅ {group_tag} 降级成功")

                # 成功时重置该端点的连续 400 计数
                self._http400_counts[self._ep_key(ep)] = 0

                # 注入端点来源标识（供轮次2补调时排除已用端点）
                normalized["_endpoint_used"] = self._ep_key(ep)
                normalized["_endpoint_group"] = ep["group"]

                return normalized

            except Exception as e:
                # 从异常中提取 status_code
                status_code = getattr(e, "status_code", None)

                if status_code in self.NON_DEGRADABLE_CODES:
                    # 认证/权限问题，直接抛出，不降级
                    raise

                if status_code == 429:
                    # 限速：不标记死链，但记录错误继续降级
                    errors.append(f"{group_tag} 限速(429)")
                    continue

                if status_code in self.DEGRADABLE_CODES or status_code is None:
                    if status_code == 400:
                        # 400 可能只是单条笔记被限制（私密/删除），不立即标死链
                        # 同端点连续 3 次 400 才标死链，且只标个体（不走 _mark_dead 避免类别级联）
                        ep_key = self._ep_key(ep)
                        count = self._http400_counts.get(ep_key, 0) + 1
                        self._http400_counts[ep_key] = count
                        if count >= 3:
                            self._dead_endpoints[ep_key] = True
                            print(f"  ⛔ 标记死链: [{ep['group']}] {ep['path']} (连续{count}次 HTTP 400)")
                        errors.append(f"{group_tag} HTTP 400 (第{count}次)")
                    else:
                        # 500/502/503/504/404 → 端点真的挂了，立即标死链
                        reason = f"HTTP {status_code}" if status_code else str(e)[:60]
                        self._mark_dead(ep, reason, pool_name)
                        errors.append(f"{group_tag} {reason}")
                    continue

                # 其他未知错误也降级
                self._mark_dead(ep, str(e)[:60], pool_name)
                errors.append(f"{group_tag} {str(e)[:60]}")
                continue

        # 所有端点都失败了
        error_detail = " → ".join(errors) if errors else "所有端点在死链缓存中"
        raise TikHubError(
            f"{pool_name} 所有 {len(pool)} 个端点均失败: {error_detail}"
        )

    def health_check(self, request_func=None):
        """
        对所有端点池做健康检查，返回可用性报告。

        Returns:
            dict: {pool_name: [(group, path, status, latency_ms), ...]}
        """
        from .tikhub_client import TikHubError
        report = {}
        func = request_func or self._request

        for pool_name, endpoints in self._pools.items():
            pool_report = []
            for ep in endpoints:
                group = ep["group"]
                path = ep["path"]
                start = time.time()
                try:
                    # 用最简参数做探测
                    test_params = {}
                    for k, v in ep["params"].items():
                        if isinstance(v, str) and v.startswith("${"):
                            # 填充测试值
                            if "keyword" in k:
                                test_params[k] = "test"
                            elif "user_id" in k:
                                test_params[k] = "5e5e19e7000000000100373e"
                            elif "note_id" in k:
                                test_params[k] = "6804b80b000000001b03b372"
                            elif "page" in k:
                                test_params[k] = 1
                            # cursor 等可选参数不填
                        else:
                            test_params[k] = v

                    method = ep.get("method", "GET")
                    func(method, path, test_params, retries=0, delay=0)
                    latency = int((time.time() - start) * 1000)
                    pool_report.append((group, path, "✅", latency))
                except TikHubError as e:
                    latency = int((time.time() - start) * 1000)
                    status = f"❌ {e.status_code or 'ERR'}"
                    pool_report.append((group, path, status, latency))
                except Exception as e:
                    latency = int((time.time() - start) * 1000)
                    pool_report.append((group, path, f"❌ {str(e)[:30]}", latency))

            report[pool_name] = pool_report

        return report

    def get_pool_names(self):
        """返回所有池名称列表"""
        return list(self._pools.keys())

    def auto_probe_and_reorder(self):
        """
        启动时端点自动探测 + 动态排序。
        
        按 category 独立探测（search/user/detail/comments 各自用自己类别的端点）：
          每个 category 选一个代表池发真实请求，结果只影响该类别的池。
          避免 detail 端点 400 误杀 search 端点的问题。
        
        根据探测结果动态重排序所有池，不可用的预标记死链。
        """
        from .tikhub_client import TikHubError
        
        print(f"\n🔍 启动时端点自动探测...")
        
        def _probe_pool(pool_endpoints, label):
            """探测一组端点，返回 {group: (is_alive, latency_ms)}"""
            result = {}
            probed = set()
            for ep in pool_endpoints:
                group = ep["group"]
                if group in probed:
                    continue
                probed.add(group)
                
                test_params = {}
                for k, v in ep["params"].items():
                    if isinstance(v, str) and v.startswith("${"):
                        if "keyword" in k:
                            test_params[k] = "test"
                        elif "user_id" in k:
                            test_params[k] = "5e5e19e7000000000100373e"
                        elif "note_id" in k:
                            test_params[k] = "6804b80b000000001b03b372"
                        elif "page" in k:
                            test_params[k] = 1
                    else:
                        test_params[k] = v
                
                method = ep.get("method", "GET")
                start = time.time()
                try:
                    self._request(method, ep["path"], test_params, retries=0, delay=0, timeout=10)
                    latency = int((time.time() - start) * 1000)
                    result[group] = (True, latency)
                    print(f"  ✅ {group:8s} | {latency:4d}ms | {label}")
                except TikHubError as e:
                    latency = int((time.time() - start) * 1000)
                    result[group] = (False, latency)
                    code = e.status_code or "ERR"
                    print(f"  ❌ {group:8s} | {latency:4d}ms | HTTP {code} | {label}")
                except Exception as e:
                    latency = int((time.time() - start) * 1000)
                    result[group] = (False, latency)
                    print(f"  ❌ {group:8s} | {latency:4d}ms | {str(e)[:30]} | {label}")
                
                time.sleep(0.2)
            return result
        
        # ---- 按 category 独立探测（每个类别用自己的端点探测，互不影响）----
        # 每个 category 选一个代表池探测，避免 detail 400 误杀 search
        _cat_repr = {}  # category → 代表池的端点列表
        for pn, eps in self._pools.items():
            cat = self._pool_categories.get(pn, pn)
            if cat not in _cat_repr and eps:
                _cat_repr[cat] = eps
        
        cat_status = {}  # category → {group: (is_alive, latency_ms)}
        for cat, eps in _cat_repr.items():
            print(f"  --- {cat} ---")
            cat_status[cat] = _probe_pool(eps, cat)
        
        if not cat_status:
            print(f"  ⚠️ 无可探测端点")
            return {}
        
        # ---- 检测 402 余额不足（用所有类别的合并结果）----
        all_merged = {}
        for st in cat_status.values():
            all_merged.update(st)
        alive_count = sum(1 for g, (ok, _) in all_merged.items() if ok)
        if alive_count == 0 and all_merged:
            print(f"\n  ⚠️ 所有端点均不可用！如果看到 HTTP 402，请检查 TikHub 账户余额。")
        
        # 统计结果
        for cat, st in cat_status.items():
            alive = [g for g, (ok, _) in st.items() if ok]
            dead = [g for g, (ok, _) in st.items() if not ok]
            print(f"  📊 {cat}: 可用={', '.join(alive) or '无'}" + (f" 不可用={', '.join(dead)}" if dead else ""))
        
        # ---- 每个池按自己 category 的探测结果排序 ----
        for pool_name, endpoints in self._pools.items():
            category = self._pool_categories.get(pool_name, pool_name)
            status = cat_status.get(category, {})
            if not status:
                continue
            
            def sort_key(ep, _status=status):
                g = ep["group"]
                is_alive, latency = _status.get(g, (False, 99999))
                return (0 if is_alive else 1, latency)
            
            self._pools[pool_name] = sorted(endpoints, key=sort_key)
        
        # ---- 预标记死链（按各自 category 的探测结果）----
        for pool_name, endpoints in self._pools.items():
            category = self._pool_categories.get(pool_name, pool_name)
            status = cat_status.get(category, {})
            dead = [g for g, (ok, _) in status.items() if not ok]
            
            for ep in endpoints:
                if ep["group"] in dead:
                    key = self._ep_key(ep)
                    self._dead_endpoints[key] = True
                    cat_key = f"{category}:{ep['group']}"
                    self._dead_category_groups[cat_key] = True
        
        print(f"  ✅ 所有端点池已按各自类别探测结果重排序\n")
        return all_merged

    def reset_dead_cache(self):
        """清空死链缓存（手动重置）"""
        self._dead_endpoints.clear()
        self._dead_category_groups.clear()
        self._soft_fail_counts.clear()
        self._http400_counts.clear()

    def reset_category_cache(self, category):
        """重置某个类别的死链缓存（如 'comments'），不影响其他类别"""
        # 清除 category:group 级别的死链
        to_remove = [k for k in self._dead_category_groups if k.startswith(f"{category}:")]
        for k in to_remove:
            del self._dead_category_groups[k]
        # 清除对应 pool 的精确端点死链
        cat_pools = [pn for pn, cat in self._pool_categories.items() if cat == category]
        for pool_name in cat_pools:
            pool = self._pools.get(pool_name, [])
            for ep in pool:
                key = self._ep_key(ep)
                self._dead_endpoints.pop(key, None)
                self._soft_fail_counts.pop(key, None)
                self._http400_counts.pop(key, None)
