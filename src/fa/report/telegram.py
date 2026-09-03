"""Telegram 推送——全程序唯一的 subprocess 触点（spec §7.1 / 计划 Task 8）。

参数形态以本机实测为准（2026-09-03 `hermes send --help`）：

    usage: hermes send [-h] [-t TARGET] [-f PATH] [-s LINE] [-l] [-q] [--json] [message]
    positional arguments:
      message   Message text. If omitted, read from --file or stdin.
      -t TARGET, --to TARGET   'platform' / 'platform:chat_id' / ...
    Exit codes: 0 ok, 1 delivery/backend error, 2 usage error.

计划草稿里的 `--message` 形参并不存在（会落进位置参数 message，恰好也能用，
但依赖 argparse 容错而非真实接口）。故定参为 `hermes send --to telegram` +
正文走 stdin：报告是长中文多行 markdown，stdin 规避 ARG_MAX 与转义问题，
也是 hermes 官方推荐的管道用法。

降级契约：任何失败（超时 / 非零退出 / 找不到可执行 / 其它任意异常）一律返回
False，绝不向调用方抛出——由调用方把失败写进 runs.summary（spec §9.5
「降级不中断」）。
"""
import subprocess

_HERMES = "hermes"
_TIMEOUT_S = 60

# 最近一次推送失败原因（成功即清空）。send_telegram 本身只回 bool 不抛，
# T9/T10 需要把「为什么失败」写进 runs.summary 时读这里。
# 仅进程内诊断用，非线程安全（fa 当前单线程调度，够用）。
LAST_TELEGRAM_ERROR: str | None = None


def send_telegram(text: str) -> bool:
    """把 text 推到 Telegram home channel；成功 True，任何失败 False（不抛）。

    失败原因写入模块级 LAST_TELEGRAM_ERROR（成功时置 None），供调用方落日志。
    注意：text 非 str 属编程期误用，编码在 try 之外故会外抛——不得把它吞成
    「推送失败」而掩盖 bug。
    """
    global LAST_TELEGRAM_ERROR
    payload = text.encode("utf-8")          # 在 try 外：TypeError/AttributeError 外抛
    try:
        proc = subprocess.run(
            [_HERMES, "send", "--to", "telegram"],
            input=payload,
            capture_output=True,
            timeout=_TIMEOUT_S,
            check=False,            # 退出码自判，让非零也走 False 分支
        )
    except subprocess.TimeoutExpired:
        LAST_TELEGRAM_ERROR = f"hermes send 超时（{_TIMEOUT_S}s）"
        return False
    except FileNotFoundError:
        LAST_TELEGRAM_ERROR = f"可执行不存在：{_HERMES}"
        return False
    except Exception as exc:                # OSError/ValueError/其它：降级不中断
        LAST_TELEGRAM_ERROR = f"{type(exc).__name__}: {exc}"
        return False

    if proc.returncode != 0:
        raw = proc.stderr or b""
        # capture_output=True 时为 bytes；容忍 str（测试替身 / 未来 text=True）
        stderr = (raw.decode("utf-8", "replace")
                  if isinstance(raw, bytes) else str(raw)).strip()
        LAST_TELEGRAM_ERROR = f"exit {proc.returncode}: {stderr[-200:]}"
        return False

    LAST_TELEGRAM_ERROR = None
    return True
