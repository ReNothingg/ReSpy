from __future__ import annotations

import ast
import math
import operator
import re
from dataclasses import dataclass
from typing import Callable

MAX_EXPRESSION_LENGTH = 300
MAX_AST_NODES = 100
MAX_RESULT_LENGTH = 1000
MAX_ABS_RESULT = 10**300

class CalculatorError(ValueError):
    """Калькулятор"""


@dataclass(frozen=True, slots=True)
class Calculation:
    source: str
    normalized: str
    result: str


def _factorial(value: float | int) -> int:
    if not isinstance(value, int) and not (
        isinstance(value, float) and value.is_integer()
    ):
        raise CalculatorError("Факториал определён только для целых чисел.")
    integer = int(value)
    if integer < 0 or integer > 100:
        raise CalculatorError("Факториал можно считать только от 0 до 100.")
    return math.factorial(integer)


def _round(value: float | int, digits: float | int = 0) -> float | int:
    if not isinstance(digits, int) and not (
        isinstance(digits, float) and digits.is_integer()
    ):
        raise CalculatorError("Количество знаков для round должно быть целым.")
    integer_digits = int(digits)
    if abs(integer_digits) > 15:
        raise CalculatorError("Для round допустимо не больше 15 знаков.")
    return round(value, integer_digits)


FUNCTIONS: dict[str, Callable[..., float | int]] = {
    "abs": abs,
    "sqrt": math.sqrt,
    "cbrt": math.cbrt,
    "round": _round,
    "floor": math.floor,
    "ceil": math.ceil,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "ln": math.log,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "factorial": _factorial,
    "deg": math.degrees,
    "rad": math.radians,
    "min": min,
    "max": max,
}

CONSTANTS: dict[str, float] = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
}

BINARY_OPERATORS: dict[type[ast.operator], Callable[[float | int, float | int], float | int]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
}

UNARY_OPERATORS: dict[type[ast.unaryop], Callable[[float | int], float | int]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _normalize(source: str) -> str:
    expression = source.strip().removeprefix("=").strip().lower()
    if not expression:
        raise CalculatorError("После /cal нужно написать выражение.")
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise CalculatorError(
            f"Выражение слишком длинное — максимум {MAX_EXPRESSION_LENGTH} символов."
        )

    expression = (
        expression.replace("×", "*")
        .replace("✕", "*")
        .replace("÷", "/")
        .replace(":", "/")
        .replace("−", "-")
        .replace("—", "-")
        .replace("^", "**")
    )
    expression = re.sub(r"(?<=\d),(?=\d)", ".", expression)
    expression = expression.replace(";", ",")
    expression = re.sub(
        r"√\s*(\d+(?:\.\d+)?)",
        r"sqrt(\1)",
        expression,
    )
    expression = re.sub(
        r"(?i)(\d+(?:\.\d+)?)\s*%\s*(?:от|of)\s*(\d+(?:\.\d+)?)",
        r"((\1)/100)*(\2)",
        expression,
    )
    expression = re.sub(
        r"(\d+(?:\.\d+)?)\s*([+-])\s*(\d+(?:\.\d+)?)\s*%",
        r"(\1 \2 ((\1)*(\3)/100))",
        expression,
    )
    expression = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"((\1)/100)", expression)
    expression = re.sub(r"(?<=\d)(?=(?:pi|tau|sqrt)\b|\()", "*", expression)
    expression = re.sub(r"(?<=\))(?=\d|[a-z]|\()", "*", expression)
    return expression


def _validate_number(value: object) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CalculatorError("Поддерживаются только обычные числа.")
    if isinstance(value, float) and not math.isfinite(value):
        raise CalculatorError("Результат получился бесконечным.")
    if abs(value) > MAX_ABS_RESULT:
        raise CalculatorError("Результат слишком большой.")
    return value


def _evaluate(node: ast.AST) -> float | int:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)

    if isinstance(node, ast.Constant):
        return _validate_number(node.value)

    if isinstance(node, ast.Name):
        if node.id in CONSTANTS:
            return CONSTANTS[node.id]
        raise CalculatorError(f"Неизвестное имя: {node.id}.")

    if isinstance(node, ast.UnaryOp) and type(node.op) in UNARY_OPERATORS:
        return _validate_number(UNARY_OPERATORS[type(node.op)](_evaluate(node.operand)))

    if isinstance(node, ast.BinOp):
        left = _evaluate(node.left)
        right = _evaluate(node.right)
        if isinstance(node.op, ast.Pow):
            if abs(right) > 1000:
                raise CalculatorError("Слишком большая степень — максимум 1000.")
            if left == 0 and right < 0:
                raise CalculatorError("Ноль нельзя возводить в отрицательную степень.")
            try:
                return _validate_number(operator.pow(left, right))
            except OverflowError as exc:
                raise CalculatorError("Результат слишком большой.") from exc
        operation = BINARY_OPERATORS.get(type(node.op))
        if operation is None:
            raise CalculatorError("Эта операция не поддерживается.")
        try:
            return _validate_number(operation(left, right))
        except ZeroDivisionError as exc:
            raise CalculatorError("На ноль делить нельзя.") from exc

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS:
            raise CalculatorError("Эта функция не поддерживается.")
        if node.keywords:
            raise CalculatorError("Именованные аргументы не поддерживаются.")
        if not 1 <= len(node.args) <= 16:
            raise CalculatorError("У функции должно быть от 1 до 16 аргументов.")
        arguments = [_evaluate(argument) for argument in node.args]
        try:
            return _validate_number(FUNCTIONS[node.func.id](*arguments))
        except CalculatorError:
            raise
        except (TypeError, ValueError, OverflowError) as exc:
            raise CalculatorError(
                f"Неверные аргументы функции {node.func.id}."
            ) from exc

    raise CalculatorError("В выражении есть неподдерживаемая конструкция.")


def _format_result(value: float | int) -> str:
    if isinstance(value, int):
        rendered = str(value)
    elif value == 0:
        rendered = "0"
    elif value.is_integer() and abs(value) < 10**16:
        rendered = str(int(value))
    else:
        rendered = format(value, ".15g")
        if "e" not in rendered.lower():
            rendered = rendered.rstrip("0").rstrip(".")
    if len(rendered) > MAX_RESULT_LENGTH:
        raise CalculatorError("Результат слишком длинный для сообщения.")
    return rendered


def calculate(source: str) -> Calculation:
    normalized = _normalize(source)
    try:
        tree = ast.parse(normalized, mode="eval")
    except (SyntaxError, ValueError) as exc:
        raise CalculatorError("Не получилось разобрать выражение.") from exc
    if sum(1 for _ in ast.walk(tree)) > MAX_AST_NODES:
        raise CalculatorError("В выражении слишком много операций.")
    value = _evaluate(tree)
    return Calculation(
        source=source.strip(),
        normalized=normalized,
        result=_format_result(value),
    )
