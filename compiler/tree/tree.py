from lark import Tree


# ===============  程 序  ===============

class ASTNode(Tree):
    def __init__(self, data, children, meta=None):
        super().__init__(data, children)
        self.line = meta.line if meta else -1
        self.column = meta.column if meta else -1

        # 传统属性（保持向后兼容，generator / x86 代码直接读取）
        self.ctype = None       # C 类型
        self.symbol = None      # 符号表引用
        self.index = None       # 结构体成员索引

        # 统一属性字典 —— 供带注释语法树输出使用
        # 包含：ctype, symbol, index, value, rule, derived_from, code, truelist, falselist, nextlist
        self.attrs = {}


class Program(ASTNode):
    def __init__(self, decl, meta=None):
        super().__init__('program', [decl], meta)
        self.decl = decl


# ===============  声 明  ===============

class ExternalDeclaration(ASTNode):
    def __init__(self, decls, meta=None):
        super().__init__('declaration', decls, meta)
        self.decls = decls


class Specifier(ASTNode):
    def __init__(self, type, meta=None):
        super().__init__('specifier', [type], meta)
        self.type = type


class Declarator(ASTNode):
    def __init__(self, name, pointer=False, suffix=None, init=None, meta=None):
        super().__init__('declarator', [], meta)
        self.pointer = pointer
        self.name = name
        self.suffix = suffix or []
        self.init = init

        self.update()

    def update(self):
        children = [self.name]
        if self.pointer:
            children.insert(0, '*')
        children.extend(self.suffix)
        if self.init:
            children.append(self.init)
        self.children = children


class Initializer(ASTNode):
    def __init__(self, inits, meta=None):
        super().__init__('initializer', inits, meta)
        self.inits = inits


class ArraySuffix(ASTNode):
    def __init__(self, size=None, meta=None):
        super().__init__('array_suffix', [size] if size else [], meta)
        self.size = size


class ParamSuffix(ASTNode):
    def __init__(self, params=None, meta=None):
        super().__init__('param_suffix', params or [], meta)
        self.params = params


class Parameter(ASTNode):
    def __init__(self, spec, decl, meta=None):
        super().__init__('parameter', [spec, decl], meta)
        self.spec = spec
        self.decl = decl


class FunctionDefinition(ASTNode):
    def __init__(self, spec, decl, body, meta=None):
        super().__init__('func_def', [spec, decl, body], meta)
        self.spec = spec
        self.decl = decl
        self.body = body


class CompoundDefinition(ASTNode):
    def __init__(self, spec, decl, members, meta=None):
        super().__init__('comp_def', [spec, decl] + members, meta)
        self.spec = spec
        self.decl = decl
        self.members = members


class Member(ASTNode):
    def __init__(self, spec, decls, meta=None):
        super().__init__('member', [spec] + decls, meta)
        self.spec = spec
        self.decls = decls


class EnumDefinition(ASTNode):
    def __init__(self, decl, enumerators, meta=None):
        super().__init__('enum_def', [decl] + enumerators, meta)
        self.decl = decl
        self.enumerators = enumerators


class Enumerator(ASTNode):
    def __init__(self, name, value=None, meta=None):
        super().__init__('enumerator', [name, value] if value else [name], meta)
        self.name = name
        self.value = value


class FunctionDeclaration(ASTNode):
    def __init__(self, spec, decls, meta=None):
        super().__init__('func_decl', [spec] + decls, meta)
        self.spec = spec
        self.decls = decls


class VariableDeclaration(ASTNode):
    def __init__(self, spec, decls, meta=None):
        super().__init__('var_decl', [spec] + decls, meta)
        self.spec = spec
        self.decls = decls


class ArrayDeclaration(ASTNode):
    def __init__(self, spec, decls, meta=None):
        super().__init__('arr_decl', [spec] + decls, meta)
        self.spec = spec
        self.decls = decls


# ===============  语 句  ===============

class Statement(ASTNode):
    def __init__(self, stmts, meta=None):
        super().__init__('statement', stmts, meta)
        self.stmts = stmts


class IfStatement(ASTNode):
    def __init__(self, cond, then, orelse=None, meta=None):
        children = [cond, then, orelse] if orelse else [cond, then]
        super().__init__('if_stmt', children, meta)
        self.cond = cond
        self.then = then
        self.orelse = orelse


class WhileStatement(ASTNode):
    def __init__(self, cond, body, meta=None):
        super().__init__('while_stmt', [cond, body], meta)
        self.cond = cond
        self.body = body


class ForStatement(ASTNode):
    def __init__(self, init, cond, post, body, meta=None):
        children = [i for i in [init, cond, post, body] if i]
        super().__init__('for_stmt', children, meta)
        self.init = init
        self.cond = cond
        self.post = post
        self.body = body


class ExpressionStatement(ASTNode):
    def __init__(self, expr=None, meta=None):
        children = expr if isinstance(expr, list) else [expr]
        children = [i for i in children if i is not None]
        super().__init__('expr_stmt', children, meta)
        self.expr = expr


class ReturnStatement(ASTNode):
    def __init__(self, expr=None, meta=None):
        super().__init__('return_stmt', [expr] if expr else [], meta)
        self.expr = expr


class BreakStatement(ASTNode):
    def __init__(self, meta=None):
        super().__init__('break_stmt', [], meta)


class ContinueStatement(ASTNode):
    def __init__(self, meta=None):
        super().__init__('continue_stmt', [], meta)


class EmptyStatement(ASTNode):
    def __init__(self, meta=None):
        super().__init__('empty_stmt', [], meta)


# ===============  表达式  ===============

class Expression(ASTNode):
    def __init__(self, exprs, meta=None):
        super().__init__('expression', exprs, meta)
        self.exprs = exprs


class AssignOp(ASTNode):
    def __init__(self, op, left, right, meta=None):
        super().__init__('assign_op', [left, op, right], meta)
        self.op = op
        self.left = left
        self.right = right


class BinaryOp(ASTNode):
    def __init__(self, op, left, right, meta=None):
        super().__init__('binary_op', [left, op, right], meta)
        self.op = op
        self.left = left
        self.right = right


class UnaryOp(ASTNode):
    def __init__(self, op, operand, meta=None):
        super().__init__('unary_op', [op, operand], meta)
        self.op = op
        self.operand = operand


class PostfixOp(ASTNode):
    def __init__(self, op, operand, meta=None):
        super().__init__('postfix_op', [operand, op], meta)
        self.op = op
        self.operand = operand


class FunctionCall(ASTNode):
    def __init__(self, func, args, meta=None):
        super().__init__('func_call', [func] + args, meta)
        self.func = func
        self.args = args


class ArrayAccess(ASTNode):
    def __init__(self, array, index, meta=None):
        super().__init__('array_access', [array, index], meta)
        self.array = array
        self.index = index


class MemberAccess(ASTNode):
    def __init__(self, object, member, arrow=False, meta=None):
        children = [object, '.', member] if not arrow else [object, '->', member]
        super().__init__('member_access', children, meta)
        self.object = object
        self.arrow = arrow
        self.member = member


# ===============  标识符  ===============

class Identifier(ASTNode):
    def __init__(self, value, meta=None):
        super().__init__('identifier', [value], meta)
        self.value = value


# ===============  常 量  ===============

class Integer(ASTNode):
    def __init__(self, value, meta=None):
        super().__init__('integer', [value], meta)
        self.value = value


class Decimal(ASTNode):
    def __init__(self, value, meta=None):
        super().__init__('decimal', [value], meta)
        self.value = value


class Character(ASTNode):
    def __init__(self, value, meta=None):
        super().__init__('character', [value], meta)
        self.value = value[1:-1]


class String(ASTNode):
    def __init__(self, value, meta=None):
        super().__init__('string', [value], meta)
        self.value = value[1:-1]


class Bool(ASTNode):
    def __init__(self, value, meta=None):
        super().__init__('bool', [value], meta)
        self.value = (value == 'true')


class NullPtr(ASTNode):
    def __init__(self, meta=None):
        super().__init__('nullptr', [], meta)
        self.value = None


# ===============  带注释的语法树输出  ===============

# attrs 中需要按教材风格展示的属性及其显示顺序
_ATTR_ORDER = ['ctype', 'rule', 'derived_from', 'value', 'code', 'backpatch', 'truelist', 'falselist', 'nextlist', 'labels', 'symbol', 'index']


def pretty_annotated(node, indent=0, indent_str='  '):
    """生成带注释的语法树文本，教材级多行属性展示。
    节点名称不变；每个语义属性以 `.attr = val` 形式追加在子节点之前。
    """
    data = str(node.data) if isinstance(node, Tree) else str(node)

    # 收集注释行
    attr_lines = _format_attrs(node, indent + 1, indent_str)

    # 非 Tree 节点（普通字符串）直接返回
    if not isinstance(node, Tree):
        return indent_str * indent + data + '\n'

    # 空子节点
    if len(node.children) == 0:
        if attr_lines:
            return indent_str * indent + data + '\n' + '\n'.join(attr_lines) + '\n'
        return indent_str * indent + data + '\n'

    # 所有子节点都是非 Tree 的终值 → 同行输出
    if all(not isinstance(c, Tree) for c in node.children):
        line = indent_str * indent + data + '\t' + ' '.join(str(c) for c in node.children)
        if attr_lines:
            return line + '\n' + '\n'.join(attr_lines) + '\n'
        return line + '\n'

    # 有 Tree 子节点 → 递归缩进
    result = indent_str * indent + data + '\n'
    if attr_lines:
        result += '\n'.join(attr_lines) + '\n'
    for child in node.children:
        if isinstance(child, Tree):
            result += pretty_annotated(child, indent + 1, indent_str)
        else:
            result += indent_str * (indent + 1) + str(child) + '\n'
    return result


def _format_attrs(node, indent, indent_str):
    """将节点 attrs 格式化为多行注释列表。"""
    lines = []
    prefix = indent_str * indent
    # 同时收集直接属性（兼容未同步到 attrs 的情况）
    if hasattr(node, 'ctype') and node.ctype is not None and 'ctype' not in node.attrs:
        node.attrs['ctype'] = node.ctype
    if hasattr(node, 'symbol') and node.symbol is not None and 'symbol' not in node.attrs:
        sym = node.symbol
        node.attrs['symbol'] = f'{sym.name}:{sym.kind.name}'
    if hasattr(node, 'index') and node.index is not None and isinstance(node.index, int) and 'index' not in node.attrs:
        node.attrs['index'] = node.index

    attrs = getattr(node, 'attrs', {})
    if not attrs:
        return lines

    for key in _ATTR_ORDER:
        val = attrs.get(key)
        if val is None:
            continue

        if key == 'symbol':
            # Symbol 对象格式化为 name:KIND
            if hasattr(val, 'name') and hasattr(val, 'kind'):
                lines.append(f'{prefix}.symbol = {val.name}:{val.kind.name}')
            else:
                lines.append(f'{prefix}.symbol = {val}')
        elif key == 'index':
            # 只显示整型成员索引（过滤 ArrayAccess 的结构属性）
            if isinstance(val, int):
                lines.append(f'{prefix}.index = {val}')
        elif key == 'derived_from':
            if isinstance(val, list):
                items = ', '.join(val)
                lines.append(f'{prefix}.derived_from = [{items}]')
            else:
                lines.append(f'{prefix}.derived_from = {val}')
        elif key == 'value':
            if val is not None:
                lines.append(f'{prefix}.value = {val}')
        elif key == 'code':
            if isinstance(val, list):
                lines.append(f'{prefix}.code')
                for instr in val:
                    lines.append(f'{prefix}{indent_str}.{instr}')
            else:
                lines.append(f'{prefix}.code = {val}')
        elif key == 'backpatch':
            if isinstance(val, list):
                for bp in val:
                    lines.append(f'{prefix}.{bp}')
        elif key == 'truelist':
            if isinstance(val, list) and val:
                items = ', '.join(f'.{x:03d}' for x in val)
                lines.append(f'{prefix}.truelist = [{items}]')
        elif key == 'falselist':
            if isinstance(val, list) and val:
                items = ', '.join(f'.{x:03d}' for x in val)
                lines.append(f'{prefix}.falselist = [{items}]')
        elif key == 'nextlist':
            if isinstance(val, list) and val:
                items = ', '.join(f'.{x:03d}' for x in val)
                lines.append(f'{prefix}.nextlist = [{items}]')
        elif key == 'labels':
            if isinstance(val, dict):
                for lbl_name, lbl_line in val.items():
                    lines.append(f'{prefix}.label {lbl_name} = .{lbl_line:03d}')
        else:
            lines.append(f'{prefix}.{key} = {val}')
    return lines
