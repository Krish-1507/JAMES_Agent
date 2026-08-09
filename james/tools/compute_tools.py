"""Computation tools: a safe arithmetic evaluator for the agent.

``calculate`` evaluates plain math expressions (no code execution) using an
AST allowlist: only numbers, basic operators, and a fixed set of math
functions/constants can appear. Anything else — attribute access, subscripts,
imports, comprehensions, lambdas — is rejected before evaluation.
"""

from __future__ import annotations

import ast
import math
import operator
from typing import Any

from .base import ToolResult, tool

_ALLOWED_CONSTANTS = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
}

_ALLOWED_FUNCTIONS = {
    name: getattr(math, name)
    for name in (
        "sqrt",
        "log",
        "log2",
        "log10",
        "exp",
        "sin",
        "cos",
        "tan",
        "asin",
        "acos",
        "atan",
        "atan2",
        "sinh",
        "cosh",
        "tanh",
        "degrees",
        "radians",
        "hypot",
        "dist",
        "isclose",
        "copysign",
        "pow",
    )
    if hasattr(math, name)
}

_ALLOWED_FUNCTIONS.update(
    {
        "abs": abs,
        "min": min,
        "max": max,
        "round": round,
        "floor": math.floor,
        "ceil": math.ceil,
        "trunc": math.trunc,
        "factorial": math.factorial,
        "gcd": math.gcd,
        "sum": sum,
        "len": len,
    }
)

# Binary operators permitted inside expressions.
_ALLOWED_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

# Unary operators permitted.
_ALLOWED_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


class UnsafeExpression(ValueError):
    """Raised when an expression uses constructs outside the math sandbox."""


def _validate(node: ast.AST) -> None:
    for child in ast.walk(node):
        if isinstance(child, ast.Constant):
            if not isinstance(child.value, int | float | complex | bool):
                raise UnsafeExpression(f"Unsupported constant: {type(child.value).__name__}")
            continue
        if isinstance(child, ast.Name):
            if child.id not in _ALLOWED_CONSTANTS and child.id not in _ALLOWED_FUNCTIONS:
                raise UnsafeExpression(f"Unknown name: {child.id}")
            continue
        if isinstance(child, ast.Call):
            if not (isinstance(child.func, ast.Name) and child.func.id in _ALLOWED_FUNCTIONS):
                raise UnsafeExpression("Only whitelisted math functions can be called")
            continue
        if isinstance(child, ast.BinOp):
            if type(child.op) not in _ALLOWED_BIN_OPS:
                raise UnsafeExpression(f"Operator not allowed: {type(child.op).__name__}")
            continue
        if isinstance(child, ast.UnaryOp):
            if type(child.op) not in _ALLOWED_UNARY_OPS:
                raise UnsafeExpression(f"Operator not allowed: {type(child.op).__name__}")
            continue
        # ast.walk() also yields the operator and context nodes.
        if isinstance(child, ast.operator):
            if type(child) not in _ALLOWED_BIN_OPS:
                raise UnsafeExpression(f"Operator not allowed: {type(child).__name__}")
            continue
        if isinstance(child, ast.unaryop):
            if type(child) not in _ALLOWED_UNARY_OPS:
                raise UnsafeExpression(f"Operator not allowed: {type(child).__name__}")
            continue
        if isinstance(child, ast.expr_context | ast.Load):
            continue
        if isinstance(child, ast.Expression):
            continue
        raise UnsafeExpression(
            f"Construct not allowed: {type(child).__name__}. "
            "Only arithmetic expressions are supported."
        )


def evaluate_expression(expression: str) -> float | int:
    """Evaluate a safe arithmetic expression. Raises :class:`UnsafeExpression`."""
    expression = (expression or "").strip()
    if not expression:
        raise UnsafeExpression("Empty expression")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise UnsafeExpression(f"Invalid expression: {exc}") from exc
    _validate(tree)
    env: dict[str, Any] = {"__builtins__": {}}
    env.update(_ALLOWED_CONSTANTS)
    env.update(_ALLOWED_FUNCTIONS)
    value = eval(compile(tree, "<calc>", "eval"), env)  # nosec B307 - sandboxed AST allowlist above
    if isinstance(value, bool) or value is None:
        raise UnsafeExpression("Expression must produce a number")
    if isinstance(value, complex):
        raise UnsafeExpression("Complex results are not supported")
    return value


@tool(
    "calculate",
    "Evaluate a mathematical expression and return the result. "
    "Supports + - * / // % ** parentheses, and math functions like sqrt(), log(), "
    "sin(), floor(), min(), max(), factorial(). Examples: '2**10', '(3+5)*7', "
    "'sqrt(144) + log2(8)', 'sin(pi/2)'. Only plain numbers and these functions "
    "are allowed — no variables or code.",
    {
        "expression": {
            "type": "string",
            "description": "The arithmetic expression to evaluate.",
        },
    },
    required=["expression"],
)
def calculate(expression: str) -> ToolResult:
    try:
        value = evaluate_expression(expression)
    except UnsafeExpression as exc:
        return ToolResult(ok=False, output=f"Expression rejected: {exc}")
    except ZeroDivisionError:
        return ToolResult(ok=False, output="Division by zero")
    except OverflowError:
        return ToolResult(ok=False, output="Result too large")
    except Exception as exc:  # nosec B110 - evaluation errors become model-visible text
        return ToolResult(ok=False, output=f"Evaluation failed: {exc}")
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return ToolResult(ok=True, output=str(int(value)))
    return ToolResult(ok=True, output=f"{value:.10g}")
