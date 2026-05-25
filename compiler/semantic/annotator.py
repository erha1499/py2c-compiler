"""
后处理注释器 — 编译流水线的第三趟扫描（后半部分）。

功能: 在语义分析之后的 AST 上，生成教学用的三地址码 (TAC) 和回填 (backpatching) 信息。

核心概念:
- TAC (Three-Address Code): 每条指令最多包含三个地址（如 t1 = a + b），
  是编译原理教学中常用的中间表示形式。
- 回填 (Backpatching): 先发出跳转指令但暂时不知道跳转目标（用 goto _ 占位），
  等目标确定后再把占位符替换为真实的标签编号。

实现原理:
- 代码以指令列表形式存储在每个函数节点的 code_buffer 中
- truelist / falselist: 条件跳转指令的编号列表（等待回填）
- nextlist: break / continue 对应的跳转指令编号列表
- 回填函数 _backpatch 将 goto _ 替换为真实的 goto Ln
"""

from pathlib import Path

from lark import Tree
from lark.visitors import Interpreter

from compiler.tree import *
from compiler.utils import write_file


class Annotator(Interpreter):
    """TAC 生成器 + 回填处理器。

    在执行完 Analyzer 之后，在已类型标注的 AST 上生成三地址码，
    并将代码和回填信息作为 attrs 注释到 AST 节点上。

    annotate(tree) 是入口方法。
    """

    def __init__(self):
        super().__init__()
        # ---------- 计数器 ----------
        self.temp_cnt = 0         # 临时变量计数器 → t1, t2, t3, ...
        self.label_cnt = 0        # 标签计数器 → L1, L2, L3, ...
        self.instr_cnt = 0        # 指令序号计数器 → .001, .002, .003, ...

        # ---------- 当前函数的代码缓冲区 ----------
        # 每条指令格式: "NNN: instr"，如 "001: t1 = a + b"
        # 第一位 (索引 0) 是 "func name:" 头部，所以 NNN 对应 buffer[NNN]
        self.code_buffer = []

    # ===============  基础工具方法  ===============

    def _next_tmp(self):
        """分配一个新的临时变量名 → t1, t2, t3, ..."""
        self.temp_cnt += 1
        return f't{self.temp_cnt}'

    def _next_label(self):
        """分配一个新的标签名 → L1, L2, L3, ..."""
        self.label_cnt += 1
        return f'L{self.label_cnt}'

    def _next_instr(self):
        """分配一个新的指令序号 → 1, 2, 3, ..."""
        self.instr_cnt += 1
        return self.instr_cnt

    def _emit(self, instr):
        """发射一条 TAC 指令到当前函数的代码缓冲区。

        自动分配指令序号并格式化为 "NNN: instr" 格式。
        返回该指令的序号，供回填机制使用。

        Args:
            instr: TAC 指令字符串（如 't1 = a + b', 'if t1 goto _', 'L1:'）

        Returns:
            指令序号（int）
        """
        idx = self._next_instr()
        self.code_buffer.append(f'{idx:03d}: {instr}')
        return idx

    def _makelist(self, idx):
        """创建一个包含单个指令序号的列表。

        Args:
            idx: 指令序号，若为 None 则返回空列表

        Returns:
            指令序号列表
        """
        return [idx] if idx is not None else []

    @staticmethod
    def _merge(a, b):
        """合并两个列表（用于 truelist / falselist 的聚合）。

        Args:
            a, b: 两个列表

        Returns:
            合并后的列表
        """
        return (a or []) + (b or [])

    def _backpatch(self, instr_ids, label):
        """回填操作：将占位的 goto _ 替换为真实的 goto 标签。

        工作流程:
        1. 遍历需要回填的指令序号列表
        2. 找到 code_buffer 中对应位置的指令
        3. 将 'goto _' 字符串替换为 'goto Ln'

        注意: func_def 在 buffer 开头预填了 'func name:'（索引 0），
        所以指令 N 位于 buffer[N]，不需要 -1 偏移。

        Args:
            instr_ids: 需要回填的指令序号列表
            label:     回填的目标标签（如 'L3'）
        """
        for idx in instr_ids:
            i = idx               # buffer[1] = .001 号指令, buffer[7] = .007 号指令
            self.code_buffer[i] = self.code_buffer[i].replace('goto _', f'goto {label}')

    # ===============  入口方法  ===============

    def annotate(self, tree):
        """TAC 生成的入口方法。

        重置所有计数器和代码缓冲区，然后遍历 AST 开始生成三地址码。

        Args:
            tree: 语义分析后的 AST（已标注类型）

        Returns:
            带 TAC 注释的 AST（原地修改）
        """
        self.temp_cnt = 0
        self.label_cnt = 0
        self.instr_cnt = 0
        self.code_buffer = []
        self.visit(tree)
        self._annotated_tree = tree
        return tree

    def save(self, file_path=''):
        """将三地址码（四元式）输出到 05 tac.txt 文件。

        从已标注的 AST 中提取每个函数的 TAC 代码块，
        按指定格式编排后写入文件，方便学习对照。

        输出格式:
            .func main:
                    .001: i = 0
                    .002: count = 0
                    ...
            .ret

        Args:
            file_path: 输出目录路径
        """
        if not hasattr(self, '_annotated_tree') or self._annotated_tree is None:
            return
        tac_text = self._format_tac(self._annotated_tree)
        write_file(tac_text, Path(file_path) / '06 tac.txt')

    @staticmethod
    def _format_tac(tree):
        """递归遍历 AST，收集每个函数定义的三地址码并格式化。

        转换规则:
        - 函数头 'func xxx:' → '.func xxx:'
        - TAC 指令 'NNN: xxx' → '        .NNN: xxx'   (8 空格缩进)
        - 函数尾 'ret' → '.ret'

        Args:
            tree: AST 根节点或子节点

        Returns:
            格式化后的 TAC 文本
        """
        lines = []

        # 先检查 ASTNode（ASTNode 继承自 Tree，需要优先匹配）
        if isinstance(tree, ASTNode):
            if isinstance(tree, FunctionDefinition):
                if 'code' in tree.attrs:
                    code = tree.attrs['code']
                    for entry in code:
                        if entry.startswith('func '):
                            lines.append('.' + entry)
                        elif entry == 'ret':
                            lines.append('.ret')
                        else:
                            lines.append(f'        .{entry}')
                    lines.append('')

            for child in tree.children:
                if isinstance(child, (ASTNode, Tree)):
                    result = Annotator._format_tac(child)
                    if result:
                        lines.append(result)

        # Lark Tree 层面（仅处理未被 ASTNode 覆盖的纯 Tree 节点）
        elif isinstance(tree, Tree):
            # 纯 Lark Tree 节点 → 递归遍历子节点查找 func_def
            for child in tree.children:
                if isinstance(child, (Tree, ASTNode)):
                    result = Annotator._format_tac(child)
                    if result:
                        lines.append(result)

        return '\n'.join(lines)

    # ---- 程序层 ----

    def program(self, tree):
        """程序根节点 → 遍历所有声明"""
        self.visit(tree.decl)

    def declaration(self, tree):
        """声明块 → 逐个遍历"""
        for decl in tree.decls:
            self.visit(decl)

    # ---- 声明层 ----

    def func_def(self, tree):
        """函数定义 → 生成该函数的 TAC 代码。

        这是 TAC 生成的起点。每个函数独立生成代码块:
        1. 在 buffer 头部写入 "func name:"
        2. 将 self.code_buffer 指向该函数的代码列表
        3. 重置计数器（每个函数的临时变量和标签从 1 开始编号）
        4. 遍历函数体生成 TAC 指令
        5. 如果函数末尾没有 ret 指令，自动补一条
        """
        func_name = tree.decl.name.value
        # 初始化函数的代码缓冲区，索引 0 是函数头
        tree.attrs['code'] = [f'func {func_name}:']
        self.code_buffer = tree.attrs['code']

        # 每个函数的临时变量和标签独立编号
        self.temp_cnt = 0
        self.label_cnt = 0
        self.instr_cnt = 0

        self.visit(tree.body)

        # 如果函数体末尾没有显式 return，补一条
        if not any('ret' in s for s in self.code_buffer):
            self.code_buffer.append('ret')

    def comp_def(self, _):
        """结构体定义 → 不产生 TAC 代码"""
        pass

    def enum_def(self, _):
        """枚举定义 → 不产生 TAC 代码"""
        pass

    def func_decl(self, _):
        """前向函数声明 → 不产生 TAC 代码"""
        pass

    def var_decl(self, tree):
        """变量声明 → 如果有初始化表达式，生成赋值 TAC。

        例如 int x = 5; → 生成: 001: x = 5
        对于隐式类型转换（如 float x = 3 → cast(int → float)），
        也会记录转换规则。
        """
        for decl in tree.decls:
            var_name = decl.name.value if decl.name else '?'
            var_type = str(decl.ctype) if decl.ctype else '?'

            if decl.init and not isinstance(decl.init, Initializer):
                self.visit(decl.init)

                # 隐式类型转换: float var = int literal → cast(int → float)
                if decl.ctype and str(decl.ctype) == 'float' and isinstance(decl.init, Integer):
                    decl.init.attrs['rule'] = 'cast(int → float)'
                    if 'value' in decl.init.attrs:
                        decl.init.attrs['value'] = float(decl.init.attrs['value'])

                # 生成赋值指令: var_name = rhs
                rhs = decl.init.attrs.get('value')
                if rhs is not None:
                    # 直接使用字面量值
                    self._emit(f'{var_name} = {rhs}')
                else:
                    # 使用表达式结果临时变量
                    tmp = decl.init.attrs.get('code_var')
                    self._emit(f'{var_name} = {tmp or "?"}')

            # 设置默认值
            decl.attrs.setdefault('value', None)
            if decl.ctype:
                decl.attrs.setdefault('ctype', decl.ctype)

    def arr_decl(self, tree):
        """数组声明 → 设置类型信息"""
        for decl in tree.decls:
            if decl.ctype:
                decl.attrs.setdefault('ctype', decl.ctype)
            if decl.init and isinstance(decl.init, Initializer):
                self.visit(decl.init)

    def member(self, tree):
        """结构体成员 → 设置类型信息"""
        for d in tree.decls:
            if d.ctype:
                d.attrs.setdefault('ctype', d.ctype)

    # ---- 语句层 ----

    def statement(self, tree):
        """复合语句 → 逐个分析子语句"""
        for stmt in tree.stmts:
            self.visit(stmt)

    def if_stmt(self, tree):
        """if 语句的 TAC 生成 + 回填（核心方法之一）。

        TAC 生成模式:
        ```
            cond_code          # 条件表达式代码
            if t goto _        # 条件为真 → 跳转到 then（占位）
            goto _             # 条件为假 → 跳转到 else（占位）
        L_then:
            then_code
            goto _             # then 结束后跳转到 end（占位）
        L_else:
            else_code
        L_end:
        ```

        回填步骤:
        1. 条件表达式的 truelist → 回填到 L_then
        2. 条件表达式的 falselist → 回填到 L_else
        3. then 分支结束后的 goto _ → 回填到 L_end
        """
        # 步骤 1: 求值条件表达式 → 产生 truelist / falselist
        self.visit(tree.cond)
        cond_tl = tree.cond.attrs.get('truelist', [])
        cond_fl = tree.cond.attrs.get('falselist', [])

        # 分配三个标签
        then_label = self._next_label()      # Ltrue
        else_label = self._next_label()      # Lfalse
        end_label = self._next_label()       # Lend

        # 步骤 2: 回填条件跳转目标（将 truelist 的 goto _ 换成 goto L_then）
        if cond_tl:
            self._backpatch(cond_tl, then_label)
        if cond_fl:
            self._backpatch(cond_fl, else_label)

        # 步骤 3: 发射 then 标签，生成 then 分支代码
        then_line = self._emit(f'{then_label}:')
        self.visit(tree.then)

        # 步骤 4: then 分支结束后跳转到 end（先占位，等确认 end 位置后回填）
        next_id = self._emit('goto _')
        nextlist = [next_id]

        # 步骤 5: 发射 else 标签，生成 else 分支代码
        else_line = self._emit(f'{else_label}:')
        if tree.orelse:
            self.visit(tree.orelse)

        # 步骤 6: 回填 nextlist → end 标签
        self._backpatch(nextlist, end_label)
        end_line = self._emit(f'{end_label}:')

        # 用具体的指令行号构造回填描述（用于教学展示）
        bp_entries = []
        if cond_tl:
            tl_formatted = ', '.join(f'.{x:03d}' for x in cond_tl)
            bp_entries.append(f'backpatch([{tl_formatted}], .{then_line:03d})')
        if cond_fl:
            fl_formatted = ', '.join(f'.{x:03d}' for x in cond_fl)
            bp_entries.append(f'backpatch([{fl_formatted}], .{else_line:03d})')
        nl_formatted = ', '.join(f'.{x:03d}' for x in nextlist)
        bp_entries.append(f'backpatch([{nl_formatted}], .{end_line:03d})')

        # 记录回填信息与属性到 AST 节点
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
        """while 循环的 TAC 生成 + 回填。

        TAC 生成模式:
        ```
        L_begin:
            cond_code
            if t goto _        # truelist → L_body
            goto _             # falselist → L_end
        L_body:
            body_code
            goto L_begin       # 跳回条件判断
        L_end:
        ```
        """
        begin_label = self._next_label()
        body_label = self._next_label()
        end_label = self._next_label()

        bp_entries = []

        # 发射 begin 标签
        self._emit(f'{begin_label}:')
        self.visit(tree.cond)
        cond_tl = tree.cond.attrs.get('truelist', [])
        cond_fl = tree.cond.attrs.get('falselist', [])

        # 回填: 条件为真 → 进入循环体
        if cond_tl:
            self._backpatch(cond_tl, body_label)
            bp_entries.append(f'backpatch(truelist, {body_label})')
        # 回填: 条件为假 → 跳出循环
        if cond_fl:
            self._backpatch(cond_fl, end_label)
            bp_entries.append(f'backpatch(falselist, {end_label})')

        tree.attrs['truelist'] = cond_tl
        tree.attrs['falselist'] = cond_fl

        # 发射 body 标签，生成循环体代码
        self._emit(f'{body_label}:')
        self.visit(tree.body)

        # 循环体执行完 → 无条件跳回条件判断
        self._emit(f'goto {begin_label}')
        self._emit(f'{end_label}:')

        tree.attrs['nextlist'] = []
        if bp_entries:
            tree.attrs['backpatch'] = bp_entries
        tree.attrs['code'] = [f'while: cond@[{begin_label}], body@[{body_label}], end@[{end_label}]']
        tree.attrs.setdefault('value', None)

    def for_stmt(self, tree):
        """for 循环的 TAC 生成 + 回填。

        TAC 生成模式:
        ```
            init_code          # for (init; ...)
        L_begin:
            cond_code          # for (; cond; ...)
            if t goto _        # truelist → L_body
            goto _             # falselist → L_end
        L_body:
            body_code
        L_post:
            post_code          # for (; ; post)
            goto L_begin
        L_end:
        ```
        """
        begin_label = self._next_label()
        body_label = self._next_label()
        post_label = self._next_label()
        end_label = self._next_label()

        bp_entries = []

        # 初始化部分（在 begin 标签之前）
        if tree.init:
            self.visit(tree.init)

        self._emit(f'{begin_label}:')

        # 条件判断
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
            # 无条件的 for(;;) → 直接进入循环体
            self._emit(f'goto {body_label}')
            tree.attrs['truelist'] = []
            tree.attrs['falselist'] = []

        tree.attrs['nextlist'] = []

        self._emit(f'{body_label}:')
        self.visit(tree.body)

        # 后处理部分
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
        """return 语句 → 发出 ret 指令。

        如果有返回值，先求值表达式，再发出 ret value 指令。
        """
        if tree.expr:
            self.visit(tree.expr)
            val = tree.expr.attrs.get('code_var', tree.expr.attrs.get('value', '?'))
            self._emit(f'ret {val}')
        else:
            self._emit('ret')

    def break_stmt(self, tree):
        """break 语句 → 发出占位 goto _（后续由外层循环回填到 end 标签）"""
        self._emit('goto _')
        tree.attrs['nextlist'] = self._makelist(self.instr_cnt)

    def continue_stmt(self, tree):
        """continue 语句 → 发出占位 goto _（后续由外层循环回填到 begin 标签）"""
        self._emit('goto _')
        tree.attrs['nextlist'] = self._makelist(self.instr_cnt)

    def expr_stmt(self, tree):
        """表达式语句 → 如果有关联表达式则分析它"""
        if tree.expr:
            self.visit(tree.expr)

    def empty_stmt(self, _):
        """空语句 (;) → 什么都不做"""
        pass

    # ---- 表达式层 ----

    def expression(self, tree):
        """逗号表达式 → 逐个分析，取最后一个表达式的结果作为整体的 code_var。

        同时传播回填列表（truelist / falselist）到父节点，
        这样才能让 if/while 的条件回填正确工作。
        """
        for expr in tree.exprs:
            self.visit(expr)
        last = tree.exprs[-1] if tree.exprs else None
        if last:
            # 传播最后一个表达式的结果变量
            tree.attrs['code_var'] = last.attrs.get('code_var', '?')
            tree.attrs['value'] = last.attrs.get('value')
            tree.attrs.setdefault('ctype', tree.ctype)
            # 传播回填列表（比较 / 逻辑运算的结果）
            if 'truelist' in last.attrs:
                tree.attrs['truelist'] = last.attrs['truelist']
            if 'falselist' in last.attrs:
                tree.attrs['falselist'] = last.attrs['falselist']

    def assign_op(self, tree):
        """赋值表达式 → 生成赋值 TAC: left_var = right_val

        例如 x = a + b 会先生成 a+b 的计算指令，再生成 x = tN 的赋值指令。
        """
        self.visit(tree.left)
        self.visit(tree.right)

        left_var = self._ident_name(tree.left) or '?'

        # 获取右值的表示
        rhs_val = tree.right.attrs.get('value')
        rhs_tmp = tree.right.attrs.get('code_var')

        # 优先使用字面量值，否则使用 code_var
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
        """二元运算 → 生成 TAC: tN = left op right

        特殊处理:
        - 比较运算符 (<, >, == 等) 除了产生结果临时变量外，
          还会产生 truelist 和 falselist，供 if/while 等语句回填使用。
          模式: t = a < b; if t goto _; goto _;
        - 逻辑与/或 (&&, ||) 也产生类似的三地址码
        """
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

            # true_id: 条件为真时跳转 → 属于 truelist
            true_id = self._emit(f'if {t} goto _')
            # false_id: 条件为假时跳转 → 属于 falselist
            false_id = self._emit(f'goto _')

            tree.attrs['truelist'] = [true_id]
            tree.attrs['falselist'] = [false_id]

        elif tree.op in ('&&', '||'):
            # 逻辑运算符
            t = self._next_tmp()
            self._emit(f'{t} = {ltmp} {tree.op} {rtmp}')
        else:
            # 算术运算符 (+, -, *, /, %)
            t = self._next_tmp()
            self._emit(f'{t} = {ltmp} {tree.op} {rtmp}')

        # 常量折叠 — 如果左右都是常量，在编译期直接计算
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
        """一元运算 → 生成 TAC: tN = op operand

        例如 -x → t1 = - x
        常量折叠: 如果操作数是常量，直接计算并存入 value
        """
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
        """后置自增/自减 → 生成 TAC: tN = x; x = x +/- 1

        例如 i++ → t1 = i; i = i + 1
        返回的 code_var 是旧值 t1（自增前的值）
        """
        self.visit(tree.operand)
        t = self._next_tmp()
        optmp = tree.operand.attrs.get('code_var', '?')
        self._emit(f'{t} = {optmp}')                    # 保存旧值
        self._emit(f'{optmp} = {optmp} {tree.op[0]} 1') # 更新原值
        tree.attrs['code_var'] = t
        tree.attrs.setdefault('ctype', tree.ctype)

    def func_call(self, tree):
        """函数调用 → 生成 TAC: param arg; call func, N

        例如 printf("x=%d", x) 生成:
            param "x=%d"
            param x
            call printf, 2
        """
        for arg in tree.args:
            self.visit(arg)

        func_name = tree.func.value if hasattr(tree.func, 'value') else '?'

        # 逐一生成 param 指令（将实参压入参数栈）
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
        """数组元素访问 → 生成 TAC: tN = arr[index]

        例如 sts[i] → t1 = sts[i]
        """
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
        """结构体成员访问 → 生成 TAC: tN = object.member

        例如 st.name → t1 = st.name
        """
        self.visit(tree.object)
        obj_tmp = tree.object.attrs.get('code_var', '?')
        member_name = tree.member.value if hasattr(tree.member, 'value') else '?'
        t = self._next_tmp()
        self._emit(f'{t} = {obj_tmp}.{member_name}')
        tree.attrs['code_var'] = t
        tree.attrs.setdefault('ctype', tree.ctype)

    def identifier(self, tree):
        """标识符 → 设置 code_var 为变量名"""
        tree.attrs['code_var'] = tree.value
        tree.attrs.setdefault('ctype', tree.ctype)

    def initializer(self, tree):
        """初始化列表 → 递归处理嵌套初始化"""
        for init in tree.inits:
            if isinstance(init, Initializer):
                self.visit(init)
            elif isinstance(init, Tree):
                self.visit(init)

    @staticmethod
    def _ident_name(node):
        """递归提取标识符/数组访问/成员访问/解引用的名称表示。

        用于 assign_op 中确定赋值目标:
        - x → "x"
        - arr[i] → "arr[i]"
        - obj.field → "obj.field"
        - *ptr → "*ptr"

        Args:
            node: AST 表达式节点

        Returns:
            名称字符串，如果无法提取则返回 None
        """
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
        """默认处理方法：递归访问所有子节点。"""
        for child in tree.children:
            if isinstance(child, Tree):
                self.visit(child)
