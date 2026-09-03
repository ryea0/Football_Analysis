"""dsh headless 调用器（设计 §9）：与 report/telegram.py 同构的 subprocess 触点。

降级契约：任何失败（超时/非零退出/找不到可执行/其它异常）返回
(stdout=None, 中文原因, duration)，绝不向调用方抛出——调用方按 status
落库（§6），run 不中断（诚实降级，设计 §4）。
参数形态以设计文档附录 A（spike 实测）为唯一权威；若附录 A 与本文件的
默认假设不符，改这里并保持测试同步。
"""
import subprocess
import time

_DSH = "dsh"
_TIMEOUT_S = 300          # 单场 headless 会话上限；附录 A 的实测耗时应远小于此
LAST_DSH_ERROR: str | None = None

_PROFILE_LINE = {"A_base": "fa-agent-base", "A_enh": "fa-agent-enh"}

_ENH_SUFFIX = """

# 网络检索（仅增强层）
你可以检索网络获取伤停 / 新闻 / 动机信息。
严禁使用任何比赛开始后产生的信息；检索词必须限定赛前日期（before:{date}）；
sources 中每条的 date 必须早于比赛日 {date}。
"""


def build_prompt(info_set: dict, line: str) -> str:
    import json
    head = (
        "你是职业足球量化分析师。基于以下赛前信息，独立完成对这场比赛的"
        "胜平负与大小球 2.5 预测。\n\n"
        "# 赛前信息集\n" + json.dumps(info_set, ensure_ascii=False) + "\n\n"
        "# 要求\n"
        "- 不约束你的分析方法：自主决定如何使用以上信息，可多轮推理、交叉验证\n"
        "- 最终必须输出且仅输出一个 JSON 对象（不要 markdown 围栏、不要多余文字）：\n"
        '  {"p_home": <0-1>, "p_draw": <0-1>, "p_away": <0-1>, "p_over25": <0-1>,\n'
        '   "confidence": <0-1>, "reasoning_digest": "<=200字", "sources": []}\n'
        "- p_home + p_draw + p_away 之和应接近 1\n"
        "- sources：若使用了信息集之外的信息逐条列出 {title, date, url}；"
        "否则为空数组\n")
    date = info_set["match"]["date"]
    return head + (_ENH_SUFFIX.format(date=date) if line == "A_enh" else "")


def run_headless(prompt: str, profile: str,
                 timeout_s: int | None = None) -> tuple[str | None, str | None, float]:
    """跑一次 headless 会话。返回 (stdout, error, duration_s)，失败不抛。"""
    global LAST_DSH_ERROR
    cmd = [_DSH, "--profile", profile, prompt]    # ← 附录 A 若非此形态，改这里
    t0 = time.monotonic()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout_s or _TIMEOUT_S)
    except subprocess.TimeoutExpired:
        LAST_DSH_ERROR = f"dsh headless 超时（{timeout_s or _TIMEOUT_S}s）"
        return None, LAST_DSH_ERROR, time.monotonic() - t0
    except FileNotFoundError:
        LAST_DSH_ERROR = f"可执行不存在：{_DSH}"
        return None, LAST_DSH_ERROR, time.monotonic() - t0
    except Exception as exc:
        LAST_DSH_ERROR = f"{type(exc).__name__}: {exc}"
        return None, LAST_DSH_ERROR, time.monotonic() - t0
    dur = time.monotonic() - t0
    if proc.returncode != 0:
        LAST_DSH_ERROR = f"dsh 退出码 {proc.returncode}：{proc.stderr[:200]}"
        return None, LAST_DSH_ERROR, dur
    LAST_DSH_ERROR = None
    return proc.stdout, None, dur
