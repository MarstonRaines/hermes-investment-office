# =====================================================================
# backend/app/valuation/errors.py —— Valuation Engine typed 错误（TS-06 §3，冻结）
#
# 错误码与 MCP 层一一对应（ts07 §4.2）：
#   MISSING_VALUATION_INPUT / INVALID_INPUT / UNSUPPORTED_MODEL_TYPE /
#   INPUT_DATA_QUALITY_BLOCKED / TERMINAL_VALUE_UNDEFINED /
#   TERMINAL_VALUE_CROSSCHECK_FAILED / INVALID_ASSUMPTION / INVALID_THESIS_TRANSITION
# =====================================================================
from __future__ import annotations

__all__ = [
    "ValuationError",
    "MissingValuationInputError",
    "InvalidAssumptionError",
    "InvalidInputError",
    "UnsupportedModelError",
    "TerminalValueUndefinedError",
    "TerminalValueCrossCheckError",
    "InputDataQualityBlockedError",
]


class ValuationError(Exception):
    """Valuation 域 typed 错误基类。"""

    code = "ENGINE_INTERNAL_ERROR"


class MissingValuationInputError(ValuationError):
    """必需输入缺失（假设/事实）。→ status=BLOCKED_MISSING_INPUT。"""

    code = "MISSING_VALUATION_INPUT"

    def __init__(self, missing_fields: list[str]) -> None:
        super().__init__(f"missing valuation inputs: {missing_fields}")
        self.missing_fields = missing_fields


class InvalidAssumptionError(ValuationError):
    """假设值非法（wacc<=0、概率和≠1、unit 非法等）。"""

    code = "INVALID_ASSUMPTION"


class InvalidInputError(ValuationError):
    """输入非法（负价格/负倍数/无 basis 的裸 float 等）。"""

    code = "INVALID_INPUT"


class UnsupportedModelError(ValuationError):
    """model_type 不受支持或标的不适用（CN_ETF 走 ETF Engine）。"""

    code = "UNSUPPORTED_MODEL_TYPE"


class TerminalValueUndefinedError(ValuationError):
    """terminal_growth >= wacc：终值未定义（绝非"很大"）。"""

    code = "TERMINAL_VALUE_UNDEFINED"


class TerminalValueCrossCheckError(ValuationError):
    """终值双方法交叉验证失败（配置为 FAILED 语义时抛出）。"""

    code = "TERMINAL_VALUE_CROSSCHECK_FAILED"


class InputDataQualityBlockedError(ValuationError):
    """输入携带 CONFLICT/REJECTED/STALE provenance → 门禁拒绝。"""

    code = "INPUT_DATA_QUALITY_BLOCKED"
