"""进程内简易限流(单实例阶段够用;多实例前需换共享存储——见 CLAUDE.md §3.2 的演进原则)。

只用于 auth 等敏感端点的基础防刷;边缘限流由 Cloudflare WAF 承担(docs/deployment)。
"""

import threading
import time


class RateLimiter:
    def __init__(self):
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: float) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if now - t < window_seconds]
            if len(hits) >= limit:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            # 防泄漏:键太多时清理过期桶
            if len(self._hits) > 10000:
                self._hits = {
                    k: [t for t in v if now - t < window_seconds]
                    for k, v in self._hits.items()
                    if any(now - t < window_seconds for t in v)
                }
            return True


limiter = RateLimiter()
