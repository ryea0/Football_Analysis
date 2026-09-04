"""hermes -z headless 子进程调用（spec §6.2）——persona 唯一触网点。

C1（设计文档 §2）：mock 与实跑同一代码路径——测试把 ``HERMES_BIN`` 指向
fixture 脚本，超时 / exit code / stdout 噪声全部真实。

命令形态由 Task 2 探针（T2）钉死：``hermes -z <prompt> -t search``——
argparse 要求 ``-z`` 紧跟 prompt（``-z -t search <prompt>`` 形态 exit 2）；
``-z`` 无条件启用 YOLO 自动授权，故尾部的 ``-t search``（toolset 名，其工具集
恰为 {web_search}，spec §6.1 白名单）是唯一真实围栏，必须带。
"""
from __future__ import annotations

import math
import subprocess

from fa.config import PERSONA_TIMEOUT_S, hermes_bin, persona_timeout
from fa.persona import PersonaError

# spec §6.1：白名单仅 web_search，落地为 hermes toolset 名 ``search``（探针 T2）
_TOOLSET = "search"


class HermesCallError(PersonaError):
    """子进程失败：``reason`` 为 "timeout"（超时）或 "exit"（非 0 退出）。"""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


def build_command(prompt: str) -> list[str]:
    """命令形态唯一事实源：``[hermes, "-z", <prompt>, "-t", "search"]``（T2）。"""
    return [hermes_bin(), "-z", prompt, "-t", _TOOLSET]


def _resolve_timeout(explicit: float | None) -> float:
    """生效超时：显式参数优先，否则读 env。

    T3 遗留 Minor 的处置：``persona_timeout()`` 只挡非法字符串，inf/nan/非正
    会穿透——``subprocess.run`` 把 inf 当无限等待，等于静默禁用超时，故在此
    统一回退 :data:`~fa.config.PERSONA_TIMEOUT_S`。
    """
    value = persona_timeout() if explicit is None else explicit
    if not math.isfinite(value) or value <= 0:
        return PERSONA_TIMEOUT_S
    return value


def call_hermes(prompt: str, timeout: float | None = None) -> str:
    """调一次 hermes，返回 stdout；超时 / 非 0 exit → :class:`HermesCallError`。

    persona 唯一触网点（spec §6.2）：本函数不做 JSON 校验——围栏噪声照常返回，
    提取与契约校验交下游层。
    """
    try:
        proc = subprocess.run(build_command(prompt), capture_output=True,
                              text=True, timeout=_resolve_timeout(timeout))
    except subprocess.TimeoutExpired as exc:
        raise HermesCallError("timeout", f"{exc.timeout}s") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:]
        raise HermesCallError("exit", f"code {proc.returncode} {tail}")
    return proc.stdout
