"""persona 层（spec §6）：hermes 调用、契约校验、判决应用。agent 只消费/产出
结构化 JSON，不碰库、不发明数字（§1.4/§2.2）。"""


class PersonaError(RuntimeError):
    """persona 层基类：所有可降级失败（超时/exit/提取/校验/文件缺失）。"""
