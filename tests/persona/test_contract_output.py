"""contract 输出侧测试：提取矩阵 + 校验矩阵（合法与非法边界逐条）。"""
import pytest

from fa.persona.contract import (PersonaContractError, extract_json,
                                 validate_output)

GOOD = {"verdict": "downweight", "confidence_delta": -0.05,
        "key_factors": ["a"], "report_md": "x"}


def test_extract_plain():
    assert extract_json('{"verdict":"agree"}') == {"verdict": "agree"}


def test_extract_fenced():
    text = '点评如下：\n```json\n{"verdict": "agree"}\n```\n以上。'
    assert extract_json(text) == {"verdict": "agree"}


def test_extract_noise_with_embedded_json():
    text = '思考……{"verdict": "agree", "report_md": "含 } 花括号"} 结语'
    assert extract_json(text)["verdict"] == "agree"


def test_extract_failure():
    with pytest.raises(PersonaContractError) as ei:
        extract_json("persona 拒绝输出 JSON")
    assert ei.value.reason == "extract"


@pytest.mark.parametrize("obj, why", [
    ({**GOOD, "verdict": "maybe"}, "词表外"),
    ({**GOOD, "confidence_delta": 0.2}, "超上界"),
    ({**GOOD, "confidence_delta": -0.2}, "超下界"),
    ({**GOOD, "confidence_delta": 0.05}, "downweight 须 <0"),
    ({**GOOD, "key_factors": []}, "空数组"),
    ({**GOOD, "key_factors": ["a"] * 6}, "超 5 条"),
    ({**GOOD, "key_factors": ["字" * 51]}, "单条超 50 字"),
    ({**GOOD, "report_md": "字" * 501}, "超 500 字"),
    ({**GOOD, "confidence_delta": "0.05"}, "非数值"),
])
def test_validate_rejects(obj, why):
    with pytest.raises(PersonaContractError):
        validate_output(obj)


@pytest.mark.parametrize("obj", [
    {"verdict": "agree", "confidence_delta": 0.0, "key_factors": ["a"],
     "report_md": "x"},
    {"verdict": "veto", "confidence_delta": 0.1, "key_factors": ["a"],
     "report_md": "x"},                       # veto 的 delta 由 apply 置 0，校验放行
    {**GOOD, "key_factors": ["字" * 50], "report_md": "字" * 500},  # 边界值恰好合法
])
def test_validate_accepts(obj):
    assert validate_output(obj) is None
