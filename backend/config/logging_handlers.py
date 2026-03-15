"""自定义日志处理器 — 将 WARNING+ 日志推送到飞书群机器人"""

import json
import logging
import urllib.request


class FeishuHandler(logging.Handler):
    """将日志记录以飞书消息形式推送到 webhook。

    webhook_url 为空时静默跳过，不影响正常运行。
    HTTP 请求失败时同样静默忽略，logging handler 不能让应用崩溃。
    """

    def __init__(self, webhook_url: str = ""):
        super().__init__()
        self.webhook_url = webhook_url

    def emit(self, record: logging.LogRecord) -> None:
        if not self.webhook_url:
            return
        try:
            msg = self.format(record)
            payload = json.dumps({
                "msg_type": "text",
                "content": {"text": msg},
            }).encode("utf-8")
            req = urllib.request.Request(
                self.webhook_url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=3)
        except Exception:
            # 绝不让日志 handler 抛出异常影响主流程
            pass
