"""Small numeric expression language; no Python evaluation or object access."""
import ast
import math
import operator
import re
from functools import lru_cache

_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
_CMP = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge}

@lru_cache(maxsize=1024)
def _parse(source, predicate=False):
    if not isinstance(source, str) or not source or len(source) > 4096:
        raise ValueError('invalid expression')
    if predicate:
        source = re.sub(r'(?<![<>=!])=(?!=)', '==', source)
    try:
        root = ast.parse(source, mode='eval').body
    except (SyntaxError, RecursionError) as exc:
        raise ValueError('invalid expression') from exc
    nodes = list(ast.walk(root))
    if len(nodes) > 512:
        raise ValueError('expression too complex')
    def check(n):
        if isinstance(n, ast.Constant):
            if type(n.value) not in (int, float) or not math.isfinite(n.value):
                raise ValueError('numeric literals only')
        elif isinstance(n, ast.Name):
            if n.id.startswith('_'):
                raise ValueError('private identifier')
        elif isinstance(n, ast.BinOp) and type(n.op) in _BIN:
            check(n.left); check(n.right)
        elif isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.UAdd, ast.USub)):
            check(n.operand)
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ('min', 'max', 'clamp'):
            if n.keywords or len(n.args) != (3 if n.func.id == 'clamp' else 2):
                raise ValueError('invalid call arity')
            for arg in n.args: check(arg)
        elif predicate and isinstance(n, ast.Compare) and all(type(op) in _CMP for op in n.ops):
            check(n.left)
            for arg in n.comparators: check(arg)
        elif predicate and isinstance(n, ast.BoolOp) and isinstance(n.op, (ast.And, ast.Or)):
            for arg in n.values: check(arg)
        else:
            raise ValueError('forbidden expression node: ' + type(n).__name__)
    check(root)
    return root

def identifiers(source, predicate=False):
    root = _parse(source, predicate)
    calls = {id(n.func) for n in ast.walk(root) if isinstance(n, ast.Call)}
    return {n.id for n in ast.walk(root) if isinstance(n, ast.Name) and id(n) not in calls}

def evaluate(source, context, predicate=False):
    root = _parse(source, predicate)
    # Validate all inputs even in short-circuited predicates: unknown is never safe.
    for name in identifiers(source, predicate):
        value = context.get(name)
        if type(value) not in (int, float, bool) or not math.isfinite(value):
            raise ValueError('unknown/non-numeric identifier: ' + name)
    def visit(n):
        if isinstance(n, ast.Constant): return n.value
        if isinstance(n, ast.Name): return context[n.id]
        if isinstance(n, ast.BinOp): return _BIN[type(n.op)](visit(n.left), visit(n.right))
        if isinstance(n, ast.UnaryOp): return (-1 if isinstance(n.op, ast.USub) else 1)*visit(n.operand)
        if isinstance(n, ast.Call):
            args = [visit(a) for a in n.args]
            if n.func.id == 'clamp':
                if args[1] > args[2]: raise ValueError('reversed clamp')
                return max(args[1], min(args[0], args[2]))
            return (min if n.func.id == 'min' else max)(args)
        if isinstance(n, ast.Compare):
            vals = [visit(n.left)] + [visit(a) for a in n.comparators]
            return all(_CMP[type(op)](a, b) for a, op, b in zip(vals, n.ops, vals[1:]))
        vals = [bool(visit(a)) for a in n.values]
        return all(vals) if isinstance(n.op, ast.And) else any(vals)
    try:
        result = visit(root)
        if not math.isfinite(result): raise ValueError('non-finite result')
        return bool(result) if predicate else result
    except (ArithmeticError, OverflowError) as exc:
        raise ValueError('invalid arithmetic') from exc
