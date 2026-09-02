"""测试进程级环境隔离（须在 fa.cli / typer 被 import 前生效，故用模块级语句）。"""
import os

# FORCE_COLOR 在部分环境非空时，typer 于 import 时（rich_utils.FORCE_TERMINAL）把
# rich console 设为 force_terminal，--help 的选项名被高亮 span 拆断
# （--half-life → `-` + `-half` + `-life`），纯文本断言因此匹配不到。
# setdefault：显式覆盖仍可生效。
os.environ.setdefault("_TYPER_FORCE_DISABLE_TERMINAL", "1")
