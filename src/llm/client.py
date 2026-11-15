"""可插拔 LLM 客户端。默认 MockLLM：确定性、可复现、无需密钥。

接入真实模型时实现 LLMClient 接口并通过工厂函数注入；
密钥只从环境变量读取，任何密钥不得写入仓库（见 config/workflow.yaml data_policy）。
"""
from __future__ import annotations

import os
from typing import Protocol


class LLMClient(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class MockLLM:
    """基于模板的确定性输出，用于无密钥环境下的端到端演示与 golden 测试。"""

    def __init__(self, responses: dict[str, str] | None = None):
        self.responses = responses or {}

    def complete(self, system: str, user: str) -> str:
        for key, resp in self.responses.items():
            if key in user:
                return resp
        # 默认回声式输出：把结构化输入转成中文段落占位，保证流程可跑通
        return f"[MockLLM 输出]\n{user[:500]}\n（以上内容为模板化占位输出，接入真实模型后替换）"


def get_client() -> LLMClient:
    """工厂：配置了环境变量时返回真实客户端（TODO），否则 MockLLM。"""
    if os.environ.get("FINCLOSE_LLM_API_KEY"):
        # TODO(Kimi Code): 实现真实客户端（OpenAI 兼容接口），从 env 读 base_url/model/key
        raise NotImplementedError("真实 LLM 客户端待实现；当前请使用 MockLLM")
    return MockLLM()
