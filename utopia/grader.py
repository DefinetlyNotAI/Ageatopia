import ast
from dataclasses import dataclass
from enum import StrEnum

import sympy as sp


class GradeKind(StrEnum):
    CORRECT = "correct"
    INCORRECT = "incorrect"
    MALFORMED = "malformed"
    INFRASTRUCTURE = "infrastructure-error"
    UNAVAILABLE = "environment-unavailable"


@dataclass(frozen=True)
class Grade:
    kind: GradeKind
    detail: str


def parse_math(text: str):
    """An AST allowlist, never sympify/eval of untrusted strings."""
    if len(text) > 256:
        raise ValueError("Expression exceeds 256 characters")
    tree = ast.parse(text.replace("^", "**"), mode="eval")
    if len(list(ast.walk(tree))) > 64:
        raise ValueError("Expression is too complex")

    def visit(node):
        match node:
            case ast.Constant(value=value) if type(value) is int and abs(value) <= 10**30:
                return sp.Integer(value)
            case ast.Constant(value=value) if type(value) is float and abs(value) <= 10**30:
                return sp.Rational(str(value))
            case ast.Name(id="x"):
                return sp.Symbol("x")
            case ast.Name(id="pi"):
                return sp.pi
            case ast.UnaryOp(op=ast.USub(), operand=operand):
                return -visit(operand)
            case ast.UnaryOp(op=ast.UAdd(), operand=operand):
                return visit(operand)
            case ast.BinOp(left=left, op=op, right=right):
                a, b = visit(left), visit(right)
                match op:
                    case ast.Add():
                        return a + b
                    case ast.Sub():
                        return a - b
                    case ast.Mult():
                        return a * b
                    case ast.Div():
                        if b == 0:
                            raise ValueError("Division by zero")
                        if b.free_symbols:
                            raise ValueError("Symbolic denominators are not supported")
                        return a / b
                    case ast.Pow():
                        if not b.is_Integer or abs(b) > 20 or sp.count_ops(a) > 8:
                            raise ValueError("Exponent is outside permitted limits")
                        if a.free_symbols and (b < 0 or sp.degree(a, sp.Symbol("x")) * b > 32):
                            raise ValueError("Polynomial degree exceeds 32")
                        result = a**b
                        if len(str(result)) > 2048:
                            raise ValueError("Expression result is too large")
                        return result
            case ast.Call(func=ast.Name(id="sqrt"), args=[arg], keywords=[]):
                value = visit(arg)
                if value.free_symbols:
                    raise ValueError("Square roots require numeric arguments")
                return sp.sqrt(value)
        raise ValueError("Use numeric or polynomial expressions with x, pi and sqrt")

    result = visit(tree.body)
    if result.has(sp.zoo, sp.oo, sp.nan) or sp.count_ops(result) > 64:
        raise ValueError("Expression must be finite and bounded")
    return result


class Grader:
    def grade(self, task, answer, allow_proofs=False) -> Grade:
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 1024:
            return Grade(
                GradeKind.MALFORMED, "Provide a nonempty answer string up to 1024 characters"
            )
        try:
            if task["impossible"]:
                if allow_proofs and answer.strip() == "inconsistent":
                    from utopia.tasks import is_provably_inconsistent

                    if is_provably_inconsistent(task):
                        return Grade(GradeKind.CORRECT, "Verified")
                return Grade(GradeKind.INCORRECT, "Answer not verified")
            if task["category"] == "cybersecurity":
                correct = answer.strip() == task["answer"]
            else:
                from utopia.tasks import expected_math

                try:
                    submitted = parse_math(answer.strip())
                except (ValueError, SyntaxError, TypeError, RecursionError):
                    return Grade(GradeKind.MALFORMED, "Answer expression could not be parsed")
                expected = expected_math(task["validator"])
                correct = sp.simplify(submitted - expected) == 0
                if correct and submitted.free_symbols:
                    # Independent substitution check on symbolic answers.
                    correct = all(
                        submitted.subs("x", n) == expected.subs("x", n) for n in (-2, 0, 3)
                    )
            return Grade(
                GradeKind.CORRECT if correct else GradeKind.INCORRECT,
                "Verified" if correct else "Answer not verified",
            )
        except Exception as error:
            return Grade(GradeKind.INFRASTRUCTURE, type(error).__name__)
