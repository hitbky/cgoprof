from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence


class ConditionOperator(str, Enum):
    EQ = "eq"
    NE = "ne"
    BIT_SET = "bit_set"
    IS_NULL = "is_null"
    NOT_NULL = "not_null"


class ConditionResult(str, Enum):
    MATCH = "match"
    NO_MATCH = "no_match"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ArgumentCondition:
    argument: int
    operator: ConditionOperator
    value: Any = None

    def __post_init__(self) -> None:
        if self.argument < 0:
            raise ValueError("condition argument index must be non-negative")
        if self.operator == ConditionOperator.BIT_SET and not isinstance(self.value, int):
            raise ValueError("bit_set conditions require an integer mask")
        if (
            self.operator in {ConditionOperator.IS_NULL, ConditionOperator.NOT_NULL}
            and self.value is not None
        ):
            raise ValueError(f"{self.operator.value} conditions do not accept a value")

    def evaluate(self, arguments: Mapping[int, Any]) -> ConditionResult:
        if self.argument not in arguments:
            return ConditionResult.UNKNOWN
        actual = arguments[self.argument]
        if self.operator == ConditionOperator.EQ:
            return ConditionResult.MATCH if actual == self.value else ConditionResult.NO_MATCH
        if self.operator == ConditionOperator.NE:
            return ConditionResult.MATCH if actual != self.value else ConditionResult.NO_MATCH
        if self.operator == ConditionOperator.IS_NULL:
            return ConditionResult.MATCH if actual is None else ConditionResult.NO_MATCH
        if self.operator == ConditionOperator.NOT_NULL:
            return ConditionResult.MATCH if actual is not None else ConditionResult.NO_MATCH
        if self.operator == ConditionOperator.BIT_SET:
            if not isinstance(actual, int):
                return ConditionResult.UNKNOWN
            return ConditionResult.MATCH if actual & self.value == self.value else ConditionResult.NO_MATCH
        raise AssertionError(f"unsupported condition operator: {self.operator}")


def evaluate_conditions(
    conditions: Sequence[ArgumentCondition], arguments: Mapping[int, Any]
) -> ConditionResult:
    saw_unknown = False
    for condition in conditions:
        result = condition.evaluate(arguments)
        if result == ConditionResult.NO_MATCH:
            return ConditionResult.NO_MATCH
        if result == ConditionResult.UNKNOWN:
            saw_unknown = True
    return ConditionResult.UNKNOWN if saw_unknown else ConditionResult.MATCH
