"""只对接 Qwen Chat Completions，密钥从本地 .env 或环境变量读取。"""

import os
from pathlib import Path

from dotenv import dotenv_values
from openai import OpenAI


ENV_PATH = Path(__file__).resolve().parent / ".env"


class QwenClient:
    def __init__(self, env_path: str | Path = ENV_PATH):
        config = {**dotenv_values(env_path), **os.environ}
        api_key = config.get("QWEN_API_KEY")
        if not api_key:
            raise ValueError("请在 .env 中设置 QWEN_API_KEY")
        self.client = OpenAI(
            api_key=api_key,
            base_url=config.get("QWEN_BASE_URL") or "https://maas.qianwenaiapi.com/compatible-mode/v1",
            max_retries=0,
        )

    def __call__(self, request: dict) -> dict:
        body = dict(request)
        enable_thinking = body.pop("enable_thinking")
        result = self.client.chat.completions.create(
            **body, extra_body={"enable_thinking": enable_thinking},
        )
        return result.model_dump()

    def close(self):
        self.client.close()
