from __future__ import annotations

import ast
import logging
import operator
from typing import Any

from core.tools.base import Tool, ToolError

LOGGER = logging.getLogger(__name__)


class CalculatorTool(Tool):
    """Safe arithmetic calculator."""

    name = "calculator"
    description = (
        "Evaluate a mathematical expression. "
        "Supports basic arithmetic: +, -, *, /, **, (, ). "
        "Args: expression (str)"
    )
    parameters = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "The mathematical expression to evaluate (e.g., '12 * 7').",
            }
        },
        "required": ["expression"],
    }

    def __init__(self) -> None:
        self.operators: dict[Any, Any] = {
            ast.Add: operator.add,
            ast.Sub: operator.sub,
            ast.Mult: operator.mul,
            ast.Div: operator.truediv,
            ast.Pow: operator.pow,
            ast.USub: operator.neg,
            ast.UAdd: operator.pos,
        }

    async def run(self, expression: str) -> str:
        LOGGER.info(f"Calculator evaluating: {expression}")
        try:
            # Safe evaluation using AST
            result = self._eval_expr(expression)
            return str(result)
        except Exception as exc:
            raise ToolError(f"Calculation failed: {exc}") from exc

    def _eval_expr(self, expr: str) -> float | int:
        return self._eval(ast.parse(expr, mode="eval").body)

    def _eval(self, node: Any) -> Any:
        if isinstance(node, ast.Num):  # <number>
            return node.n
        elif isinstance(node, ast.Constant):  # <number> (Python 3.8+)
            if isinstance(node.value, int | float):
                return node.value
            raise ValueError(f"Unsupported constant type: {type(node.value)}")
        elif isinstance(node, ast.BinOp):  # <left> <operator> <right>
            return self.operators[type(node.op)](self._eval(node.left), self._eval(node.right))
        elif isinstance(node, ast.UnaryOp):  # <operator> <operand> e.g., -1
            return self.operators[type(node.op)](self._eval(node.operand))
        else:
            raise TypeError(f"Unsupported expression node: {type(node)}")
