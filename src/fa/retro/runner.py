"""hermes -z headless 调用器（设计 §11 runner；降级契约同 telegram.py）。

参数形态以本机实测为准（2026-09-04 `hermes --help`）：
    hermes [-z PROMPT] [-m MODEL] [-t TOOLSETS] ...
-z 吃完整 prompt（非 stdin）。工具集 web 默认启用（`hermes tools list` 实测）。
退出码：0 ok / 2 usage error / 其它 backend error。
E2E 校准（计划 Task 8）若发现 -z 需要附加旗标（如工具集收敛），只改本模块
的 _ARGV 常量与 docstring，不改调用方。
"""
import json
import subprocess
import time

from fa.retro.contract import TAGS

HARNESS = "hermes"
TIMEOUT_S = 300            # agent 可能带 web 检索，比 send_telegram 宽
_ARGV = [HARNESS, "-z"]    # prompt 作为 -z 的参数追加

ROLE_PROMPT = """你是足球量化研究的赛后复盘分析师。给你一场比赛的：赛前信息摘要、\
模型概率、市场（Pinnacle 收盘）隐含概率、赛果、以及两者分歧的量化摘要。\
你的任务：解释这场比赛中模型相对市场为什么错（或为什么对）。

规则：
1. 只依据给你的信息与足球领域知识判断；引用外部信息必须给证据\
（title/date/url），赛前成因（伤停/轮换/动机/赛程密度/新闻）的证据日期\
必须早于比赛日
2. 不做反事实推演（不得写「若无伤停则……」）
3. miss_tags 只能从给定枚举中选；不确定时宁可选 variance 或 model_limitation
4. 只输出一个 JSON 对象，不输出任何其他文字"""

_OUTPUT_CONTRACT = """{
  "miss_tags": ["从枚举中多选"],
  "primary_tag": "miss_tags 中的主因（单选）",
  "tags_confidence": 0.0到1.0,
  "model_vs_market": "model_wrong|market_wrong|both_off|variance",
  "evidence": [{"title": "...", "date": "YYYY-MM-DD", "url": "..."}],
  "digest": "≤200字中文摘要"
}"""


def build_prompt(pack: dict) -> str:
    return (f"{ROLE_PROMPT}\n\n标签枚举：{list(TAGS)}\n\n信息集 JSON：\n"
            f"{json.dumps(pack, ensure_ascii=False, indent=1)}\n\n"
            f"输出契约（只输出该 JSON）：\n{_OUTPUT_CONTRACT}")


def run_headless(prompt: str) -> dict:
    """调 hermes -z；恒不抛。返回 {ok, output, error, duration_s}。"""
    if not isinstance(prompt, str):        # 编程期误用外抛，不吞成调用失败
        raise TypeError(f"prompt 须为 str，收到 {type(prompt).__name__}")
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            _ARGV + [prompt], capture_output=True, timeout=TIMEOUT_S,
            check=False)
        duration = time.monotonic() - t0
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "",
                "error": f"hermes -z 超时（{TIMEOUT_S}s）",
                "duration_s": TIMEOUT_S}
    except FileNotFoundError:
        return {"ok": False, "output": "",
                "error": f"可执行不存在：{HARNESS}", "duration_s": 0.0}
    except Exception as exc:               # 降级不中断批
        return {"ok": False, "output": "",
                "error": f"{type(exc).__name__}: {exc}",
                "duration_s": time.monotonic() - t0}
    # capture_output=True 时为 bytes；容忍 str（测试替身 / 未来 text=True）
    raw_out = proc.stdout or b""
    stdout = (raw_out.decode("utf-8", "replace")
              if isinstance(raw_out, bytes) else str(raw_out))
    raw = proc.stderr or b""
    stderr = (raw.decode("utf-8", "replace")
              if isinstance(raw, bytes) else str(raw)).strip()
    if proc.returncode != 0:
        return {"ok": False, "output": "",
                "error": f"exit {proc.returncode}: {stderr[-200:]}",
                "duration_s": duration}
    return {"ok": True, "output": stdout,
            "error": None, "duration_s": duration}
