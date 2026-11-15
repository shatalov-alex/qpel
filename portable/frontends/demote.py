"""Double precision that is float in disguise, rewritten as float before import.

C promotes a float to double when it meets a double constant: mmcv writes `T hy = 1. - ly;`,
`y < -1.0`, `(T)0.5` and `T v = 0.;` in kernels instantiated for float. qpel has no double (Metal
has none), but these compute exactly what float does:

- A double constant converted to float is that constant rounded once to float (as C does).
- A comparison of two doubles that are floats (promoted floats, or constants exactly
  representable as float) compares the same values as float would.
- One +, -, * or / of two such doubles, rounded back to float, equals the float operation:
  double has more than 2 * 24 + 2 significant bits, so rounding the exact result first to
  double and then to float gives the correctly rounded float result (Figueroa, "When is double
  rounding innocuous?", 1995). Only one operation: a double intermediate kept for a second
  operation is not float arithmetic, and stays double (the importer rejects it).
- `x op= c` with a float x and such a constant c is the float operation.

The rewrite replaces those AST nodes with float-typed ones in place; anything else is left as is.
"""
import struct

FLOAT_OPS = {'+', '-', '*', '/'}
COMPARISONS = {'<', '>', '<=', '>=', '==', '!='}
WRAPPERS = ('ParenExpr',)


def _type(node):
    t = node.get('type', {})
    return t.get('desugaredQualType', t.get('qualType', ''))


def is_float(node):
    return _type(node).replace('const ', '').strip() == 'float'


def is_double(node):
    return _type(node).replace('const ', '').strip() == 'double'


def to_float(value):
    """value rounded to the nearest float (ties to even), as a Python float."""
    return struct.unpack('f', struct.pack('f', value))[0]


def literal(node, value):
    """A float FloatingLiteral of value (negated when below zero, as C writes it), at node's place."""
    base = {k: v for k, v in node.items() if k not in ('inner', 'type', 'value', 'castKind', 'opcode')}
    out = dict(base, kind='FloatingLiteral', type={'qualType': 'float'}, value=repr(abs(value)),
               valueCategory='prvalue', _demoted=True)
    if value < 0 or (value == 0 and struct.pack('>f', value)[0] & 0x80):
        return dict(base, kind='UnaryOperator', opcode='-', type={'qualType': 'float'}, valueCategory='prvalue',
                    inner=[out], _demoted=True)
    return out


def peel(node):
    while node.get('kind') in WRAPPERS and node.get('inner'):
        node = node['inner'][0]
    return node


def constant(node):
    """The value of a double constant expression (a literal, maybe negated, or an integer
    literal converted to double), or None."""
    node = peel(node)
    kind = node.get('kind')
    if kind == 'FloatingLiteral':
        try:
            return float(node['value'])
        except (KeyError, ValueError):
            return None
    if kind == 'UnaryOperator' and node.get('opcode') in ('-', '+') and node.get('inner'):
        inner = constant(node['inner'][0])
        return None if inner is None else (-inner if node['opcode'] == '-' else inner)
    if kind == 'ImplicitCastExpr' and node.get('castKind') == 'IntegralToFloating' and node.get('inner'):
        inner = peel(node['inner'][0])
        if inner.get('kind') == 'IntegerLiteral':
            return float(int(inner['value']))
    return None


def float_value(node):
    """A float-typed node equal to this double expression when it is exactly a float: a promoted
    float, or a constant float represents exactly. None otherwise."""
    peeled = peel(node)
    if peeled.get('kind') == 'ImplicitCastExpr' and peeled.get('castKind') == 'FloatingCast' and \
            peeled.get('inner') and is_float(peeled['inner'][0]):
        return peeled['inner'][0]
    value = constant(peeled)
    if value is not None and to_float(value) == value:
        return literal(peeled, value)
    return None


def single(node):
    """A float-typed node equal to this double expression rounded to float, or None."""
    peeled = peel(node)
    exact = float_value(peeled)
    if exact is not None:
        return exact
    value = constant(peeled)
    if value is not None:
        return literal(peeled, to_float(value))  # a constant rounded once to float, as C converts it
    if peeled.get('kind') == 'BinaryOperator' and peeled.get('opcode') in FLOAT_OPS and is_double(peeled):
        left, right = (float_value(x) for x in peeled['inner'])
        if left is not None and right is not None:
            return dict(peeled, type={'qualType': 'float'}, inner=[left, right], _demoted=True)
    if peeled.get('kind') == 'UnaryOperator' and peeled.get('opcode') == '-' and is_double(peeled):
        inner = float_value(peeled['inner'][0])
        if inner is not None:
            return dict(peeled, type={'qualType': 'float'}, inner=[inner], _demoted=True)
    return None


def rewrite(node):
    inner = node.get('inner')
    if inner:
        for i, child in enumerate(inner):
            if isinstance(child, dict):
                inner[i] = rewrite(child)
    kind = node.get('kind')
    if kind in ('ImplicitCastExpr', 'CStyleCastExpr', 'CXXStaticCastExpr', 'CXXFunctionalCastExpr') and \
            node.get('castKind') == 'FloatingCast' and is_float(node) and node.get('inner') and is_double(node['inner'][0]):
        demoted = single(node['inner'][0])
        if demoted is not None:
            if kind == 'ImplicitCastExpr':
                return demoted
            return dict(node, castKind='NoOp', inner=[demoted])  # keep the explicit (float) the source wrote
    if kind == 'BinaryOperator' and node.get('opcode') in COMPARISONS and node.get('inner') and \
            all(is_double(x) for x in node['inner']):
        left, right = (float_value(x) for x in node['inner'])
        if left is not None and right is not None:
            return dict(node, inner=[left, right], _demoted=True)
    if kind == 'CompoundAssignOperator' and node.get('opcode') in ('+=', '-=', '*=', '/=') and \
            is_float(node['inner'][0]) and node.get('computeLHSType', {}).get('qualType') == 'double':
        right = float_value(node['inner'][1])
        if right is not None:
            return dict(node, inner=[node['inner'][0], right], computeLHSType={'qualType': 'float'},
                        computeResultType={'qualType': 'float'}, _demoted=True)
    return node


def demote_unit(unit):
    """Rewrite unit in place, once (clang_ast caches units)."""
    if unit.get('_qpel_demoted'):
        return
    for node in unit.get('inner', ()):
        rewrite(node)
    unit['_qpel_demoted'] = True
