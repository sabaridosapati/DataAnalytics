"""Evaluate arithmetic expressions from the LLM without eval/exec (AST whitelist)."""
import ast
import operator

import numpy as np

MAX_LEN = 500
MAX_EXPONENT = 100
BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}
UNARY = {ast.USub: operator.neg, ast.UAdd: operator.pos}
FUNCS = {
    "abs": np.abs,
    "round": np.round,
    "sqrt": np.sqrt,
    "log": np.log,
    "exp": np.exp,
    "min": np.minimum,
    "max": np.maximum,
}


class UnsafeExpressionError(ValueError):
    pass


def safe_eval(expr: str, names: dict[str, object]):
    if len(expr) > MAX_LEN:
        raise UnsafeExpressionError("Expression is too long.")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise UnsafeExpressionError(f"Invalid expression: {exc.msg}") from exc
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        try:
            result = _eval(tree.body, names)
        except OverflowError as exc:
            raise UnsafeExpressionError("Result is too large.") from exc
    if np.isscalar(result) and not np.isfinite(result):
        raise UnsafeExpressionError("Result is not a finite number.")
    return result


def _as_float(value):
    # Floats overflow to an error/inf instantly; Python ints would grow without bound (CPU/memory DoS).
    if isinstance(value, bool):
        raise UnsafeExpressionError("Boolean values are not allowed.")
    if isinstance(value, int):
        return float(value)
    return value


def _eval(node: ast.AST, names: dict[str, object]):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise UnsafeExpressionError("Only numeric constants are allowed.")
        return float(node.value)
    if isinstance(node, ast.Name):
        if node.id not in names:
            raise UnsafeExpressionError(f"Unknown name '{node.id}'.")
        return _as_float(names[node.id])
    if isinstance(node, ast.BinOp) and type(node.op) in BINOPS:
        left = _eval(node.left, names)
        right = _eval(node.right, names)
        if isinstance(node.op, ast.Pow) and np.any(np.abs(np.asarray(right, dtype=float)) > MAX_EXPONENT):
            raise UnsafeExpressionError("Exponent too large.")
        return BINOPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in UNARY:
        return UNARY[type(node.op)](_eval(node.operand, names))
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in FUNCS
        and not node.keywords
        and 1 <= len(node.args) <= 2
    ):
        args = [_eval(a, names) for a in node.args]
        if node.func.id == "round" and len(args) == 2:
            args[1] = int(args[1])
        return FUNCS[node.func.id](*args)
    raise UnsafeExpressionError(f"Disallowed expression element: {type(node).__name__}.")
