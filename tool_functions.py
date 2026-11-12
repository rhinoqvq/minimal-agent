"""内置工具实现；工具声明保存在 tools/ 的 JSON 文件中。"""

import ast
import operator
import re
from decimal import Context, Decimal, DecimalException, DivisionByZero, Underflow, localcontext

from ddgs import DDGS

from todo_tool import TodoTool


_NUMBER = re.compile(r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
_OPERATORS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}


def calculate(expression: str) -> dict:
    """计算十进制数学表达式，最多 28 位有效数字，不执行 Python 代码。"""
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("expression 必须是非空字符串")
    if len(expression) > 500:
        raise ValueError("表达式不能超过 500 个字符")
    source = expression.strip()
    try:
        tree = ast.parse(source, mode="eval")
    except (SyntaxError, ValueError) as error:
        raise ValueError("表达式格式错误，请使用数字、括号和 + - * / ** 运算符") from error
    if sum(1 for _ in ast.walk(tree)) > 128:
        raise ValueError("表达式过于复杂，请拆分计算")

    def evaluate(node, context):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            # 从源文本取数，避免 AST 的 float 丢失原始十进制精度。
            literal = ast.get_source_segment(source, node)
            if not literal or not _NUMBER.fullmatch(literal):
                raise ValueError("只支持十进制数字，可使用科学计数法")
            return context.create_decimal(literal)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = evaluate(node.operand, context)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp):
            operation = _OPERATORS.get(type(node.op))
            if operation is None and not isinstance(node.op, ast.Pow):
                raise ValueError("只支持 + - * / ** 运算符")
            left, right = evaluate(node.left, context), evaluate(node.right, context)
            if isinstance(node.op, ast.Pow):
                if right != right.to_integral_value() or abs(right) > 1000:
                    raise ValueError("幂运算的指数必须是 -1000 到 1000 之间的整数")
                return left ** int(right)
            return operation(left, right)
        raise ValueError("只支持数字和数学运算，不支持变量、函数调用或其他代码")

    try:
        with localcontext(Context(prec=28, Emin=-1000, Emax=1000)) as context:
            context.traps[Underflow] = True
            result = evaluate(tree.body, context)
            if not result.is_finite():
                raise ValueError("结果不是有限数值，请检查表达式")
            text = format(result, "f")
            if "." in text:
                text = text.rstrip("0").rstrip(".")
            if result == Decimal(0):
                text = "0"
    except DivisionByZero as error:
        raise ZeroDivisionError("除数不能为 0") from error
    except DecimalException as error:
        raise ValueError("计算无效或数值超出支持范围，请检查表达式或拆分计算") from error
    return {"expression": expression, "result": text}


def search(query: str) -> list[dict[str, str]]:
    """搜索网页，原样返回 DDGS 字典列表，由 loop 序列化并保存。"""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query 必须是非空字符串")
    return DDGS().text(query.strip(), region="jp-ja", backend="auto", max_results=10)


BUILTIN_TOOL_HANDLERS = {"calculate": calculate, "search": search}


def get_builtin_tool_handlers(session) -> dict:
    """每轮单独绑定会话工具，不把 session_id 或数据库路径交给模型。"""
    return {**BUILTIN_TOOL_HANDLERS, "todolist": TodoTool(session.store, session.session_id)}
