"""后处理注释器：在语义分析之后执行，为 AST 节点生成值传播、TAC 中间代码和回填信息。"""
from lark import Tree
from lark.visitors import Interpreter

from compiler.tree import *


class Annotator(Interpreter):
    def __init__(self):
        super().__init__()
        self.temp_cnt = 0         # 临时变量计数器
        self.label_cnt = 0        # 标签计数器
        self.instr_cnt = 0        # 指令序号计数器
        self.code_buffer = []     # 当前函数的三地址码缓冲区

    def _next_tmp(self):
        self.temp_cnt += 1
        return f't{self.temp_cnt}'

    def _next_label(self):
        self.label_cnt += 1
        return f'L{self.label_cnt}'

    def _next_instr(self):
        self.instr_cnt += 1
        return self.instr_cnt

    def _emit(self, instr):
        idx = self._next_instr()
        self.code_buffer.append(f'{idx:03d}: {instr}')
        return idx

    def _makelist(self, idx):
        return [idx] if idx is not None else []

    @staticmethod
    def _merge(a, b):
        return (a or []) + (b or [])

    def _backpatch(self, instr_ids, label):
        """回填：定位 code_buffer 中对应指令，将 goto _ 的占位符替换为实际标签。

        注意：func_def 在 buffer 开头预填了 'func name:'（索引 0），
        所以指令 N 位于 buffer[N]，不需要 -1 偏移。
        """
        for idx in instr_ids:
            i = idx               # buffer[1] = .001 号指令, buffer[7] = .007 号指令
            self.code_buffer[i] = self.code_buffer[i].replace('goto _', f'goto {label}')

    def annotate(self, tree):
        self.temp_cnt = 0
        self.label_cnt = 0
        self.instr_cnt = 0
        self.code_buffer = []
        self.visit(tree)
        return tree

    # ---- 程序层 ----

    def program(self, tree):
        self.visit(tree.decl)

    def declaration(self, tree):
        for decl in tree.decls:
            self.visit(decl)

    # ---- 声明层 ----

    def func_def(self, tree):
        func_name = tree.decl.name.value
        tree.attrs['code'] = [f'func {func_name}:']
        self.code_buffer = tree.attrs['code']
        self.temp_cnt = 0
        self.label_cnt = 0
        self.instr_cnt = 0
        self.visit(tree.body)
        if not any('ret' in s for s in self.code_buffer):
            self.code_buffer.append('ret')

    def comp_def(self, _):
        pass

    def enum_def(self, _):
        pass

    def func_decl(self, _):
        pass

    def var_decl(self, tree):
        for decl in tree.decls:
            var_name = decl.name.value if decl.name else '?'
            var_type = str(decl.ctype) if decl.ctype else '?'
            if decl.init and not isinstance(decl.init, Initializer):
                self.visit(decl.init)

                # 隐式类型转换：float var = int literal → cast(int → float)
                if decl.ctype and str(decl.ctype) == 'float' and isinstance(decl.init, Integer):
                    decl.init.attrs['rule'] = 'cast(int → float)'
                    if 'value' in decl.init.attrs:
                        decl.init.attrs['value'] = float(decl.init.attrs['value'])

                rhs = decl.init.attrs.get('value')
                if rhs is not None:
                    self._emit(f'{var_name} = {rhs}')
                else:
                    tmp = decl.init.attrs.get('code_var')
                    self._emit(f'{var_name} = {tmp or "?"}')
            decl.attrs.setdefault('value', None)
            if decl.ctype:
                decl.attrs.setdefault('ctype', decl.ctype)

    def arr_decl(self, tree):
        for decl in tree.decls:
            if decl.ctype:
                decl.attrs.setdefault('ctype', decl.ctype)
            if decl.init and isinstance(decl.init, Initializer):
                self.visit(decl.init)

    def member(self, tree):
        for d in tree.decls:
            if d.ctype:
                d.attrs.setdefault('ctype', d.ctype)

    # ---- 语句层 ----

    def statement(self, tree):
        for stmt in tree.stmts:
            self.visit(stmt)

    def if_stmt(self, tree):
        # 步骤 1：求值条件表达式 → 产生 truelist / falselist
        self.visit(tree.cond)
        cond_tl = tree.cond.attrs.get('truelist', [])
        cond_fl = tree.cond.attrs.get('falselist', [])

        then_label = self._next_label()      # Ltrue
        else_label = self._next_label()      # Lfalse
        end_label = self._next_label()       # Lend

        # 步骤 2：回填条件跳转目标
        if cond_tl:
            self._backpatch(cond_tl, then_label)
        if cond_fl:
            self._backpatch(cond_fl, else_label)

        # 步骤 3：true 分支 —— 记录标签所在行号
        then_line = self._emit(f'{then_label}:')
        self.visit(tree.then)

        # 步骤 4：then 分支结束后跳转到 end（nextlist 占位）
        next_id = self._emit('goto _')
        nextlist = [next_id]

        # 步骤 5：false 分支 —— 记录标签所在行号
        else_line = self._emit(f'{else_label}:')
        if tree.orelse:
            self.visit(tree.orelse)

        # 步骤 6：回填 nextlist → 结束标签
        self._backpatch(nextlist, end_label)
        end_line = self._emit(f'{end_label}:')

        # 用具体的中间代码行号代替抽象名称
        bp_entries = []
        if cond_tl:
            tl_formatted = ', '.join(f'.{x:03d}' for x in cond_tl)
            bp_entries.append(f'backpatch([{tl_formatted}], .{then_line:03d})')
        if cond_fl:
            fl_formatted = ', '.join(f'.{x:03d}' for x in cond_fl)
            bp_entries.append(f'backpatch([{fl_formatted}], .{else_line:03d})')
        nl_formatted = ', '.join(f'.{x:03d}' for x in nextlist)
        bp_entries.append(f'backpatch([{nl_formatted}], .{end_line:03d})')

        # 记录回填信息与属性
        tree.attrs['backpatch'] = bp_entries
        tree.attrs['truelist'] = cond_tl
        tree.attrs['falselist'] = cond_fl
        tree.attrs['nextlist'] = nextlist
        tree.attrs['labels'] = {
            then_label: then_line,
            else_label: else_line,
            end_label: end_line
        }
        tree.attrs['code'] = [f'if-else: truelist@[.{then_line:03d}], falselist@[.{else_line:03d}], end@[.{end_line:03d}]']
        tree.attrs.setdefault('value', None)

    def while_stmt(self, tree):
        begin_label = self._next_label()
        body_label = self._next_label()
        end_label = self._next_label()

        bp_entries = []

        self._emit(f'{begin_label}:')
        self.visit(tree.cond)
        cond_tl = tree.cond.attrs.get('truelist', [])
        cond_fl = tree.cond.attrs.get('falselist', [])

        if cond_tl:
            self._backpatch(cond_tl, body_label)
            bp_entries.append(f'backpatch(truelist, {body_label})')
        if cond_fl:
            self._backpatch(cond_fl, end_label)
            bp_entries.append(f'backpatch(falselist, {end_label})')

        tree.attrs['truelist'] = cond_tl
        tree.attrs['falselist'] = cond_fl

        self._emit(f'{body_label}:')
        self.visit(tree.body)

        # 循环回到条件判断
        self._emit(f'goto {begin_label}')
        self._emit(f'{end_label}:')

        tree.attrs['nextlist'] = []
        if bp_entries:
            tree.attrs['backpatch'] = bp_entries
        tree.attrs['code'] = [f'while: cond@[{begin_label}], body@[{body_label}], end@[{end_label}]']
        tree.attrs.setdefault('value', None)

    def for_stmt(self, tree):
        begin_label = self._next_label()
        body_label = self._next_label()
        post_label = self._next_label()
        end_label = self._next_label()

        bp_entries = []

        if tree.init:
            self.visit(tree.init)
        self._emit(f'{begin_label}:')
        if tree.cond:
            self.visit(tree.cond)
            cond_tl = tree.cond.attrs.get('truelist', [])
            cond_fl = tree.cond.attrs.get('falselist', [])

            if cond_tl:
                self._backpatch(cond_tl, body_label)
                bp_entries.append(f'backpatch(truelist, {body_label})')
            if cond_fl:
                self._backpatch(cond_fl, end_label)
                bp_entries.append(f'backpatch(falselist, {end_label})')

            tree.attrs['truelist'] = cond_tl
            tree.attrs['falselist'] = cond_fl
        else:
            self._emit(f'goto {body_label}')
            tree.attrs['truelist'] = []
            tree.attrs['falselist'] = []

        tree.attrs['nextlist'] = []

        self._emit(f'{body_label}:')
        self.visit(tree.body)
        self._emit(f'{post_label}:')
        if tree.post:
            self.visit(tree.post)
        self._emit(f'goto {begin_label}')
        self._emit(f'{end_label}:')

        if bp_entries:
            tree.attrs['backpatch'] = bp_entries
        tree.attrs['code'] = [f'for: cond@[{begin_label}], body@[{body_label}], post@[{post_label}], end@[{end_label}]']
        tree.attrs.setdefault('value', None)

    def return_stmt(self, tree):
        if tree.expr:
            self.visit(tree.expr)
            val = tree.expr.attrs.get('code_var', tree.expr.attrs.get('value', '?'))
            self._emit(f'ret {val}')
        else:
            self._emit('ret')

    def break_stmt(self, tree):
        self._emit('goto _')
        tree.attrs['nextlist'] = self._makelist(self.instr_cnt)

    def continue_stmt(self, tree):
        self._emit('goto _')
        tree.attrs['nextlist'] = self._makelist(self.instr_cnt)

    def expr_stmt(self, tree):
        if tree.expr:
            self.visit(tree.expr)

    def empty_stmt(self, _):
        pass

    # ---- 表达式层 ----

    def expression(self, tree):
        for expr in tree.exprs:
            self.visit(expr)
        last = tree.exprs[-1] if tree.exprs else None
        if last:
            tree.attrs['code_var'] = last.attrs.get('code_var', '?')
            tree.attrs['value'] = last.attrs.get('value')
            tree.attrs.setdefault('ctype', tree.ctype)
            # 传播回填列表（比较 / 逻辑运算的结果）
            if 'truelist' in last.attrs:
                tree.attrs['truelist'] = last.attrs['truelist']
            if 'falselist' in last.attrs:
                tree.attrs['falselist'] = last.attrs['falselist']

    def assign_op(self, tree):
        self.visit(tree.left)
        self.visit(tree.right)

        left_var = self._ident_name(tree.left) or '?'
        rhs_val = tree.right.attrs.get('value')
        rhs_tmp = tree.right.attrs.get('code_var')

        # 优先使用字面量值，否则使用 code_var
        # 但如果 rhs 表达式已经有计算值且无临时变量，直接使用值（如 unary_op 的 -1）
        rhs = '?'
        if rhs_val is not None:
            rhs = str(rhs_val)
        if rhs_tmp and rhs_val is None:
            rhs = rhs_tmp
        self._emit(f'{left_var} = {rhs}')

        tree.attrs['code_var'] = left_var
        tree.attrs['value'] = rhs_val
        tree.attrs.setdefault('ctype', tree.ctype)

    def binary_op(self, tree):
        self.visit(tree.left)
        self.visit(tree.right)

        lval = tree.left.attrs.get('value')
        rval = tree.right.attrs.get('value')
        ltmp = tree.left.attrs.get('code_var') or (str(lval) if lval is not None else '?')
        rtmp = tree.right.attrs.get('code_var') or (str(rval) if rval is not None else '?')

        # 比较运算符 → 产生 truelist / falselist（等待上层语句回填）
        if tree.op in ('<', '>', '<=', '>=', '==', '!='):
            t = self._next_tmp()
            self._emit(f'{t} = {ltmp} {tree.op} {rtmp}')
            true_id = self._emit(f'if {t} goto _')    # truelist: 条件为真时跳转
            false_id = self._emit(f'goto _')           # falselist: 条件为假时跳转
            tree.attrs['truelist'] = [true_id]
            tree.attrs['falselist'] = [false_id]

        elif tree.op in ('&&', '||'):
            t = self._next_tmp()
            self._emit(f'{t} = {ltmp} {tree.op} {rtmp}')
        else:
            t = self._next_tmp()
            self._emit(f'{t} = {ltmp} {tree.op} {rtmp}')

        # 常量折叠
        if lval is not None and rval is not None:
            try:
                ops = {'+': lambda a, b: a + b, '-': lambda a, b: a - b,
                       '*': lambda a, b: a * b, '/': lambda a, b: a // b,
                       '%': lambda a, b: a % b,
                       '<': lambda a, b: int(a < b), '>': lambda a, b: int(a > b),
                       '<=': lambda a, b: int(a <= b), '>=': lambda a, b: int(a >= b),
                       '==': lambda a, b: int(a == b), '!=': lambda a, b: int(a != b)}
                if tree.op in ops:
                    tree.attrs['value'] = ops[tree.op](lval, rval)
            except (TypeError, ZeroDivisionError):
                pass

        tree.attrs['code_var'] = t
        tree.attrs.setdefault('ctype', tree.ctype)

    def unary_op(self, tree):
        self.visit(tree.operand)
        t = self._next_tmp()
        oval = tree.operand.attrs.get('value')
        optmp = tree.operand.attrs.get('code_var') or str(oval) if oval is not None else '?'
        if oval is not None and tree.op == '-':
            optmp = str(oval)
        self._emit(f'{t} = {tree.op} {optmp}')

        # 常量折叠
        oval = tree.operand.attrs.get('value')
        if oval is not None:
            try:
                if tree.op == '-':
                    tree.attrs['value'] = -oval
                elif tree.op == '!':
                    tree.attrs['value'] = int(not oval)
                elif tree.op == '+':
                    tree.attrs['value'] = oval
            except TypeError:
                pass

        tree.attrs['code_var'] = t
        tree.attrs.setdefault('ctype', tree.ctype)

    def postfix_op(self, tree):
        self.visit(tree.operand)
        t = self._next_tmp()
        optmp = tree.operand.attrs.get('code_var', '?')
        self._emit(f'{t} = {optmp}')
        self._emit(f'{optmp} = {optmp} {tree.op[0]} 1')
        tree.attrs['code_var'] = t
        tree.attrs.setdefault('ctype', tree.ctype)

    def func_call(self, tree):
        for arg in tree.args:
            self.visit(arg)
        func_name = tree.func.value if hasattr(tree.func, 'value') else '?'

        # 逐一生成 param 指令
        for arg in tree.args:
            v = arg.attrs.get('value')
            t = arg.attrs.get('code_var')
            if t:
                self._emit(f'param {t}')
            elif v is not None:
                if isinstance(arg, String):
                    self._emit(f'param "{v}"')
                else:
                    self._emit(f'param {v}')
            else:
                self._emit('param ?')

        self._emit(f'call {func_name}, {len(tree.args)}')
        tree.attrs['code_var'] = func_name
        tree.attrs.setdefault('ctype', tree.ctype)

    def array_access(self, tree):
        self.visit(tree.array)
        self.visit(tree.index)
        arr_tmp = tree.array.attrs.get('code_var', '?')
        idx_val = tree.index.attrs.get('value')
        idx_tmp = str(idx_val) if idx_val is not None else tree.index.attrs.get('code_var', '?')
        t = self._next_tmp()
        self._emit(f'{t} = {arr_tmp}[{idx_tmp}]')
        tree.attrs['code_var'] = t
        tree.attrs.setdefault('ctype', tree.ctype)

    def member_access(self, tree):
        self.visit(tree.object)
        obj_tmp = tree.object.attrs.get('code_var', '?')
        member_name = tree.member.value if hasattr(tree.member, 'value') else '?'
        t = self._next_tmp()
        self._emit(f'{t} = {obj_tmp}.{member_name}')
        tree.attrs['code_var'] = t
        tree.attrs.setdefault('ctype', tree.ctype)

    def identifier(self, tree):
        tree.attrs['code_var'] = tree.value
        tree.attrs.setdefault('ctype', tree.ctype)

    def initializer(self, tree):
        for init in tree.inits:
            if isinstance(init, Initializer):
                self.visit(init)
            elif isinstance(init, Tree):
                self.visit(init)

    @staticmethod
    def _ident_name(node):
        if isinstance(node, Identifier):
            return node.value
        if isinstance(node, ArrayAccess):
            arr_name = Annotator._ident_name(node.array)
            idx = node.attrs.get('value') or node.index if hasattr(node, 'index') else '?'
            return f'{arr_name}[{idx}]'
        if isinstance(node, MemberAccess):
            obj = Annotator._ident_name(node.object)
            mem = node.member.value if hasattr(node.member, 'value') else '?'
            return f'{obj}.{mem}'
        if isinstance(node, UnaryOp) and node.op == '*':
            inner = Annotator._ident_name(node.operand)
            return f'*{inner}' if inner else '?'
        return None

    # ---- catch-all ----
    def __default__(self, tree):
        for child in tree.children:
            if isinstance(child, Tree):
                self.visit(child)
