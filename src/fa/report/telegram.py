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


def send_telegram(text: str) -> bool:
    """把 text 推到 Telegram home channel；成功 True，任何失败 False（不抛）。"""
    try:
        proc = subprocess.run(
            [_HERMES, "send", "--to", "telegram"],
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=_TIMEOUT_S,
            check=False,            # 退出码自判，让非零也走 False 分支
        )
    except (subprocess.TimeoutExpired, FileNotFoundError,
            OSError, ValueError):
        return False
    except Exception:
        # 兜底：推送失败不得中断管线（降级由调用方记录）
        return False
    return proc.returncode == 0
