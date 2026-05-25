"""
语义分析器 — 编译流水线的第三趟扫描（前半部分）。

功能: 在 AST 上做语义分析，包括:
1. 类型检查 — 确保每个表达式的类型合法
2. 符号表管理 — 构建和维护作用域嵌套的符号表
3. 类型推导 — 为每个 AST 节点标注其类型 (ctype)
4. 规则记录 — 在 attrs 中记录推导规则 (rule)，用于教学展示

这是一个基于 Lark Interpreter 的树遍历器，从根节点 program 开始，
深度优先访问整棵 AST，在每个节点上执行语义检查和类型标注。
"""

from lark.visitors import Interpreter

from compiler.error import SemanticError
from compiler.tree import *
from .symbol import Symbol, SymbolKind, SymbolTable
from .type import *


class Analyzer(Interpreter):
    """语义分析器：类型检查 + 符号表 + 类型推导。

    analyze(tree) 是入口方法，遍历 AST 并标注 ctype、symbol 等属性。
    """

    def __init__(self):
        super().__init__()
        # ---------- 核心数据结构 ----------

        # 符号表 — 支持嵌套作用域的类型化符号表
        # 每个作用域是一个 dict，新作用域通过 enter_scope() 创建
        self.table = SymbolTable()

        # 当前正在分析的函数类型（用于 return 语句的类型检查）
        self.curr_func = None

        # 当前所处的循环嵌套深度（用于 break/continue 的合法性检查）
        self.loop_depth = 0

        # ---------- 预定义基本类型 ----------
        # 将 C 语言的内置类型预先注册到符号表中
        for name, type in [('void', VOID), ('int', INT), ('float', FLOAT),
                           ('char', CHAR), ('bool', BOOL)]:
            symbol = Symbol(type, name, SymbolKind.TYPE)
            self.table.define(symbol)

        # ---------- 预定义标准库函数 ----------
        # printf 和 scanf 是 C 标准库函数，需要提前声明以便类型检查
        self.table.define(Symbol(FunctionType(INT, None), 'printf', SymbolKind.FUNC))
        self.table.define(Symbol(FunctionType(INT, None), 'scanf', SymbolKind.FUNC))

    # ===============  基础方法  ===============

    @staticmethod
    def raise_error(msg, node):
        """语义错误报告 — 抛出携带行列号的 SemanticError。

        Args:
            msg: 错误描述
            node: 携带行列信息的 AST 节点
        """
        raise SemanticError(msg, node.line, node.column)

    def analyze(self, tree):
        """语义分析入口：从根节点开始遍历整棵 AST，标注类型信息。

        Args:
            tree: 语法分析产生的 AST 根节点 (Program)

        Returns:
            标注了类型信息的 AST（原地修改）
        """
        self.visit(tree)
        return tree

    # ===============  辅助方法  ===============

    @staticmethod
    def _set_attrs(node, **kwargs):
        """填充节点的 attrs 字典，同时同步传统属性（ctype, symbol, index）。

        这个方法是语义分析器写入注释信息的统一入口。
        写入的数据最终会出现在 05 ast_comment.txt 中。

        Args:
            node: AST 节点
            **kwargs: 要设置的属性，如 rule='...', derived_from=[...]
        """
        for key, val in kwargs.items():
            if val is not None:
                node.attrs[key] = val
        # 同步传统属性到 attrs（保持数据一致性）
        if node.ctype is not None and 'ctype' not in node.attrs:
            node.attrs['ctype'] = node.ctype
        if node.symbol is not None and 'symbol' not in node.attrs:
            node.attrs['symbol'] = node.symbol
        if node.index is not None and 'index' not in node.attrs:
            node.attrs['index'] = node.index

    def parse_type(self, spec, decl=None):
        """将类型说明符 + 声明符解析为完整的类型对象。

        例如:
        - int → BasicType('int')
        - int* → PointerType(BasicType('int'))
        - int[10] → ArrayType(BasicType('int'), 10)
        - int[] → ArrayType(BasicType('int'), None)  # 大小未确定

        Args:
            spec: 类型说明符（Specifier 节点）
            decl: 声明符（Declarator 节点，可选），包含指针标记和数组后缀

        Returns:
            Type 对象（BasicType / PointerType / ArrayType 等）
        """
        # 从符号表中查找基础类型（int, float, char 等）
        symbol = self.table.lookup(spec.type)
        if not symbol or symbol.kind != SymbolKind.TYPE:
            self.raise_error(f"未声明的类型 '{spec.type}'", spec)
            return None

        type = symbol.type
        if not decl:
            return type

        # 处理指针修饰（*）
        if decl.pointer:
            type = PointerType(type)

        # 处理数组后缀（从右到左处理，构建嵌套类型）
        # 例如 int[3][4] 从内向外: BasicType → ArrayType(..., 4) → ArrayType(..., 3)
        for suffix in reversed(decl.suffix):
            if isinstance(suffix, ArraySuffix):
                size = None
                if suffix.size:
                    # 数组大小必须是 int 类型的常量表达式
                    self.visit(suffix.size)
                    if suffix.size.ctype == INT:
                        size = self.parse_constexpr(suffix.size)
                        if size is None:
                            self.raise_error("数组大小必须是常量表达式", suffix.size)
                    else:
                        self.raise_error("数组大小必须是 'int' 类型", suffix.size)
                type = ArrayType(type, size)
        return type

    def parse_constexpr(self, node):
        """编译期常量表达式求值。

        支持的节点类型:
        - 整数字面量
        - 枚举常量标识符
        - 二元运算（+, -, *, /, %, ==, !=, <, >, <=, >=, &&, ||）
        - 一元运算（+, -, !）

        Args:
            node: 常量表达式 AST 节点

        Returns:
            求值结果（int），如果不是编译期可求值的表达式则返回 None
        """
        if isinstance(node, Integer):
            return int(node.value, 0)  # 支持十进制、十六进制等

        if isinstance(node, Identifier):
            # 标识符可能是枚举常量
            symbol = self.table.lookup(node.value)
            if symbol and isinstance(symbol.type, EnumType):
                return symbol.type.enumerators[symbol.name]

        if isinstance(node, BinaryOp):
            left = self.parse_constexpr(node.left)
            right = self.parse_constexpr(node.right)
            if left is None or right is None:
                return None
            if node.op == '+':
                return left + right
            if node.op == '-':
                return left - right
            if node.op == '*':
                return left * right
            if node.op == '/':
                if right == 0:
                    self.raise_error("常量表达式中不能除以0", node.right)
                return left // right
            if node.op == '%':
                if right == 0:
                    self.raise_error("常量表达式中不能对0取模", node.right)
                return left % right
            if node.op == '==':
                return int(left == right)
            if node.op == '!=':
                return int(left != right)
            if node.op == '<':
                return int(left < right)
            if node.op == '>':
                return int(left > right)
            if node.op == '<=':
                return int(left <= right)
            if node.op == '>=':
                return int(left >= right)
            if node.op == '&&':
                return int(bool(left) and bool(right))
            if node.op == '||':
                return int(bool(left) or bool(right))

        if isinstance(node, UnaryOp):
            operand = self.parse_constexpr(node.operand)
            if operand is None:
                return None
            if node.op == '+':
                return operand
            if node.op == '-':
                return -operand
            if node.op == '!':
                return int(not operand)

        return None

    def parse_init(self, node, type, context):
        """处理复合初始化列表的类型检查和大小推断。

        递归处理数组和结构体的嵌套初始化列表。
        例如 int arr[] = {1, 2, 3} → 推导 arr 大小为 3。

        Args:
            node: Initializer 初始化列表节点
            type:  目标类型
            context: 用于错误报告的上下文节点
        """
        if isinstance(type, ArrayType):
            # 数组初始化列表
            if type.size is not None and len(node.inits) > type.size:
                msg = f"数组 '{context.name.value}' 的初始化项长度 '{len(node.inits)}' 超出数组大小 '{type.size}'"
                self.raise_error(msg, context)
            elif type.size is None:
                # 未指定大小的数组，从初始化列表推导大小
                type.size = len(node.inits)

            # 递归检查每个元素类型
            type = type.type
            for init in node.inits:
                if isinstance(init, Initializer):
                    self.parse_init(init, type, context)
                else:
                    self.visit(init)
                    if not self.is_assignable(type, init.ctype):
                        self.raise_error(f"无法将 '{init.ctype}' 初始化为 '{type}'", init)

        elif isinstance(type, CompoundType):
            # 结构体初始化列表
            types = list(type.members.values())

            if len(node.inits) > len(types):
                self.raise_error(f"类型 '{type}' 的初始化项过多", node)

            for init, member_type in zip(node.inits, types):
                if isinstance(init, Initializer):
                    self.parse_init(init, member_type, context)
                else:
                    self.visit(init)
                    if not self.is_assignable(member_type, init.ctype):
                        self.raise_error(f"无法将 '{init.ctype}' 初始化为 '{member_type}'", init)
        else:
            self.raise_error(f"初始化列表不能用于类型 '{type}'", node)

    @staticmethod
    def parse_op(op, ltype, rtype):
        """二元运算符的类型推导。

        根据操作符和左右类型，推导出运算结果的类型。
        这是 C 语言类型系统的一个核心规则集。

        Args:
            op:    操作符 (+, -, *, /, %, <, >, ==, !=, &&, || 等)
            ltype: 左操作数类型
            rtype: 右操作数类型

        Returns:
            运算结果的类型，如果不合法则返回 None
        """
        if op in ('+', '-', '*', '/'):
            # 指针运算: ptr + int → ptr, ptr - ptr → int
            if isinstance(ltype, PointerType) and rtype == INT:
                return ltype
            if op == '+' and ltype == INT and isinstance(rtype, PointerType):
                return rtype
            if op == '-' and isinstance(ltype, PointerType) and isinstance(rtype, PointerType) and ltype == rtype:
                return INT
            # 数值运算: int/float 混合 → float, int+int → int
            if ltype == FLOAT or rtype == FLOAT:
                return FLOAT
            if ltype == INT and rtype == INT:
                return INT
        elif op == '%':
            # 取模运算只能用于 int
            if ltype == INT and rtype == INT:
                return INT
        elif op in ('<', '>', '<=', '>=', '==', '!=', '&&', '||'):
            # 比较和逻辑运算 → 返回布尔类型
            is_arith = lambda t: t in [INT, FLOAT, CHAR, BOOL]
            if is_arith(ltype) and is_arith(rtype):
                return BOOL
            # 指针比较
            elif isinstance(ltype, (PointerType, ArrayType)) and ltype == rtype:
                return BOOL
            # 空指针比较
            elif isinstance(ltype, PointerType) and rtype == NULL:
                return BOOL
            elif ltype == NULL and isinstance(rtype, PointerType):
                return BOOL
        return None

    @staticmethod
    def is_assignable(ltype, rtype):
        """检查 rtype 类型的值能否赋值给 ltype 类型的变量。

        这是 C 语言类型兼容性规则的核心实现：
        - int/float/char/bool 之间可以互相转换
        - 枚举和 int 可以互换
        - 指针可接收同类型指针或 null
        - void* 可以接收任何指针

        Args:
            ltype: 左值类型（赋值目标）
            rtype: 右值类型（赋值来源）

        Returns:
            True 表示可以赋值
        """
        if ltype == rtype:
            return True
        # 枚举 ↔ int
        if (isinstance(ltype, EnumType) and rtype == INT) or (ltype == INT and isinstance(rtype, EnumType)):
            return True
        # 布尔可接收任何数值/指针
        if ltype == BOOL and (isinstance(rtype, (BasicType, PointerType, ArrayType))):
            return True
        # 数值类型之间可以隐式转换
        if ltype in (INT, FLOAT, CHAR, BOOL) and rtype in (INT, FLOAT, CHAR, BOOL):
            return True
        # 空指针赋值给指针
        if isinstance(ltype, PointerType) and rtype == NULL:
            return True
        # 数组退化为指针
        if isinstance(ltype, PointerType) and isinstance(rtype, ArrayType) and ltype.type == rtype.type:
            return True
        # void* 万能指针
        if isinstance(ltype, PointerType) and ltype.type == VOID and isinstance(rtype, (PointerType, ArrayType)):
            return True
        return False

    def is_lvalue(self, node):
        """判断一个表达式节点是否是左值（可以放在赋值号左边）。

        左值的条件:
        1. 解引用 (*ptr)
        2. 标识符（变量名，但不能是 const 常量）
        3. 数组元素访问 (arr[i])
        4. 成员访问 (obj.field, ptr->field)

        Args:
            node: 表达式节点

        Returns:
            True 表示是左值
        """
        if isinstance(node, UnaryOp) and node.op == '*':
            return True
        if isinstance(node, (Identifier, ArrayAccess, MemberAccess)):
            if isinstance(node, Identifier):
                # const 常量不能作为左值
                symbol = self.table.lookup(node.value)
                if symbol and symbol.kind == SymbolKind.CONST:
                    return False
            return True
        return False

    # ===============  访问方法  ===============

    def program(self, tree):
        """访问程序根节点 → 遍历所有声明"""
        self.visit(tree.decl)

    def declaration(self, tree):
        """访问声明块 → 逐个处理声明节点"""
        for decl in tree.decls:
            self.visit(decl)

    def func_def(self, tree):
        """函数定义的语义分析（核心方法）。

        工作流程:
        1. 解析返回类型和参数类型，构造 FunctionType
        2. 检查是否为重复定义或类型冲突
        3. 创建函数符号并注册到符号表
        4. 进入新作用域，注册形参
        5. 分析函数体
        6. 离开作用域
        """
        # 步骤 1: 解析返回类型
        return_type = self.parse_type(tree.spec)
        func_name = tree.decl.name.value

        # 解析参数类型列表
        suffix = next(i for i in tree.decl.suffix if isinstance(i, ParamSuffix))
        param_types, param_names = [], []
        if suffix.params and suffix.params != ['void']:
            for param in suffix.params:
                param_type = self.parse_type(param.spec, param.decl)
                param_name = param.decl.name.value
                param_types.append(param_type)
                param_names.append((param_type, param_name, param))

        # 步骤 2: 构造函数类型，检查重复定义
        func_type = FunctionType(return_type, param_types)
        symbol = self.table.lookup(func_name)
        if symbol:
            if symbol.defined:
                self.raise_error(f"函数 '{func_name}' 重复定义", tree.decl.name)
                return
            # 前向声明已存在，检查类型是否一致
            if symbol.type != func_type:
                self.raise_error(f"函数 '{func_name}' 类型冲突", tree.decl.name)
                return
            symbol.node = tree
            symbol.defined = True
        else:
            symbol = Symbol(func_type, func_name, SymbolKind.FUNC, tree.decl.name)
            if not self.table.define(symbol):
                self.raise_error(f"函数 '{func_name}' 重复定义", tree.decl.name)

        # 记录推导规则
        tree.decl.name.symbol = symbol
        tree.ctype = func_type
        self._set_attrs(tree, rule=f"define_func({func_name}, [{', '.join(str(t) for t in param_types)}]) → {return_type}")
        self.curr_func = func_type

        # 步骤 3: 进入函数作用域，注册形参
        self.table.enter_scope()
        for param_type, param_name, node in param_names:
            symbol = Symbol(param_type, param_name, SymbolKind.VAR, node.decl.name)
            if not self.table.define(symbol):
                self.raise_error(f"形参 '{param_name}' 重复定义", node)

        # 步骤 4: 分析函数体
        self.visit(tree.body)

        # 步骤 5: 离开作用域
        self.table.leave_scope()
        self.curr_func = None

    def comp_def(self, tree):
        """结构体/联合体定义的语义分析。

        工作流程:
        1. 先用 incomplete 类型占位注册（支持自引用）
        2. 分析所有成员类型
        3. 用完整的类型替换占位类型
        """
        comp_name = tree.decl.name.value
        is_union = False if tree.spec.type == 'struct' else True

        # 先用不完整的类型占位，允许结构体包含指向自身的指针
        temp_type = CompoundType(comp_name, None, is_union)
        symbol = Symbol(temp_type, comp_name, SymbolKind.TYPE, tree.decl.name, defined=False)
        if not self.table.define(symbol):
            self.raise_error(f"类型 '{comp_name}' 重复定义", tree.decl.name)

        # 分析成员
        members = {}
        for member in tree.members:
            for member_decl in member.decls:
                member_name = member_decl.name.value
                if member_name in members:
                    self.raise_error(f"成员变量 '{member_name}' 重复定义", member_decl)
                member_type = self.parse_type(member.spec, member_decl)
                members[member_name] = member_type
                member_decl.ctype = member_type

        # 用完整类型替换占位
        comp_type = CompoundType(comp_name, members, is_union)
        symbol.type = comp_type
        symbol.defined = True

    def enum_def(self, tree):
        """枚举定义的语义分析。

        工作流程:
        1. 检查枚举名是否重复
        2. 解析枚举项的值（支持自动递增和显式赋值）
        3. 注册枚举类型和枚举常量到符号表
        """
        enum_name = tree.decl.name.value

        if self.table.lookup(enum_name):
            self.raise_error(f"枚举 '{enum_name}' 重复定义", tree.name)
            return

        # 解析枚举项
        enumerators, cnt = {}, 0
        for enumerator in tree.enumerators:
            imm_name = enumerator.name.value
            if self.table.lookup(imm_name) or imm_name in enumerators:
                self.raise_error(f"枚举常量 '{imm_name}' 重复定义", enumerator.name)
                return

            if enumerator.value:
                # 显式赋值: enum { A = 5 }
                self.visit(enumerator.value)
                value = self.parse_constexpr(enumerator.value)
                if value is None:
                    self.raise_error(f"枚举常量 '{imm_name}' 必须是常量表达式", enumerator.value)
                    return
                cnt = value

            enumerators[imm_name] = cnt
            cnt += 1  # 自动递增

        # 注册枚举类型
        enum_type = EnumType(enum_name, enumerators)
        symbol = Symbol(enum_type, enum_name, SymbolKind.TYPE, tree.decl.name)
        self.table.define(symbol)

        # 注册每个枚举常量（作为 const 符号）
        for enumerator in tree.enumerators:
            imm_name = enumerator.name.value
            symbol = Symbol(enum_type, imm_name, SymbolKind.CONST, enumerator.name)
            self.table.define(symbol)

    def func_decl(self, tree):
        """前向函数声明的语义分析。

        与 func_def 类似，但:
        - 不定义函数体
        - 符号标记为 defined=False（等待后续定义）
        - 允许重复声明（只要类型一致）
        """
        return_type = self.parse_type(tree.spec)

        for decl in tree.decls:
            func_name = decl.name.value

            suffix = next((i for i in decl.suffix if isinstance(i, ParamSuffix)), None)
            params_types = []
            if suffix.params and suffix.params != ['void']:
                for param in suffix.params:
                    param_type = self.parse_type(param.spec, param.decl)
                    if param_type:
                        params_types.append(param_type)

            func_type = FunctionType(return_type, params_types)
            symbol = self.table.lookup(func_name)
            if symbol:
                if symbol.type != func_type:
                    self.raise_error(f"函数 '{func_name}' 类型冲突", decl.name)
                else:
                    self.raise_error(f"函数 '{func_name}' 重复定义", decl.name)
            else:
                symbol = Symbol(func_type, func_name, SymbolKind.FUNC, decl.name, defined=False)
                self.table.define(symbol)
            decl.ctype = func_type

    def var_decl(self, tree):
        """变量声明的语义分析。

        检查项:
        - 不能声明 void 类型变量
        - 不能在同一作用域重复声明
        - 初始化表达式的类型必须兼容
        - 全局变量初始化必须是常量表达式
        """
        for decl in tree.decls:
            var_type = self.parse_type(tree.spec, decl)
            var_name = decl.name.value
            var_init = decl.init

            decl.ctype = var_type
            self._set_attrs(decl, rule=f"decl_var({var_name}) = {var_type}")

            # void 不能声明变量
            if var_type == VOID:
                self.raise_error(f"变量 '{var_name}' 不能被声明为 'void' 类型", decl.name)
                return

            # 注册符号
            symbol = Symbol(var_type, var_name, SymbolKind.VAR, decl.name)
            if not self.table.define(symbol):
                self.raise_error(f"变量 '{var_name}' 重复定义", decl.name)

            # 检查初始化表达式
            if var_init:
                self.visit(var_init)
                if isinstance(var_init, Initializer):
                    # 复合初始化（数组/结构体）
                    self.parse_init(var_init, var_type, decl)
                elif self.curr_func is None and self.parse_constexpr(var_init) is None:
                    # 全局变量初始化必须是常量
                    self.raise_error(f"全局变量 '{var_name}' 的初始化项必须是常量表达式", var_init)
                elif not self.is_assignable(var_type, var_init.ctype):
                    self.raise_error(f"无法将 '{var_init.ctype}' 初始化为 '{var_type}'", var_init)

    def arr_decl(self, tree):
        """数组声明的语义分析。

        与 var_decl 类似，但额外处理:
        - 数组维度（大小或从初始化列表推导）
        - 不能是 void 数组
        - 不完整的数组（无大小且无初始化）报错
        """
        for decl in tree.decls:
            arr_type = self.parse_type(tree.spec, decl)
            arr_name = decl.name.value
            arr_init = decl.init

            decl.ctype = arr_type
            self._set_attrs(decl, rule=f"decl_array({arr_name}) = {arr_type}")

            # 不完整的数组声明
            if arr_type.size is None and arr_init is None:
                self.raise_error(f"数组 '{arr_name}' 没有初始化列表", decl)
                return
            if arr_type.type == VOID:
                self.raise_error(f"数组 '{arr_name}' 不能被声明为 'void' 类型", decl.name)
                return

            # 注册符号
            symbol = Symbol(arr_type, decl.name.value, SymbolKind.VAR, decl.name)
            if not self.table.define(symbol):
                self.raise_error(f"数组 '{arr_name}' 重复定义", decl.name)

            # 检查初始化列表
            if arr_init:
                self.visit(arr_init)
                if isinstance(arr_init, Initializer):
                    self.parse_init(arr_init, arr_type, decl)
                elif not self.is_assignable(arr_type, arr_init.ctype):
                    self.raise_error(f"无法将 '{arr_init.ctype}' 初始化为 '{arr_type}'", arr_init)

    def statement(self, tree):
        """复合语句（花括号块）→ 创建新的嵌套作用域。

        每个 {} 块都创建一个新的作用域，其中声明的变量在离开作用域后失效。
        """
        self.table.enter_scope()
        for stmt in tree.stmts:
            self.visit(stmt)
        self.table.leave_scope()

    def if_stmt(self, tree):
        """if 语句的类型检查。

        检查项:
        - 条件表达式必须能转换为布尔类型
        """
        self.visit(tree.cond)
        if not self.is_assignable(BOOL, tree.cond.ctype):
            self.raise_error("条件表达式的类型必须能转换为布尔型", tree.cond)
        self.visit(tree.then)
        if tree.orelse:
            self.visit(tree.orelse)

    def while_stmt(self, tree):
        """while 循环的类型检查。

        除条件类型检查外，还需要跟踪循环深度（供 break/continue 使用）。
        """
        self.loop_depth += 1
        self.visit(tree.cond)
        if not self.is_assignable(BOOL, tree.cond.ctype):
            self.raise_error("条件表达式的类型必须能转换为布尔型", tree.cond)
        self.visit(tree.body)
        self.loop_depth -= 1

    def for_stmt(self, tree):
        """for 循环的类型检查。

        特殊之处: for 循环的初始化部分在自己的作用域中
        （例如 for (int i=0; ...) 中的 i 只在循环内可见）。
        """
        self.table.enter_scope()
        self.loop_depth += 1
        if tree.init:
            self.visit(tree.init)
        if tree.cond:
            self.visit(tree.cond)
            if not self.is_assignable(BOOL, tree.cond.ctype):
                self.raise_error("条件表达式的类型必须能转换为布尔型", tree.cond)
        if tree.post:
            self.visit(tree.post)
        self.visit(tree.body)
        self.loop_depth -= 1
        self.table.leave_scope()

    def return_stmt(self, tree):
        """return 语句的类型检查。

        检查项:
        - 必须在函数内部
        - void 函数不能有返回值
        - 非 void 函数必须返回兼容类型
        """
        if tree.expr:
            self.visit(tree.expr)

        if not self.curr_func:
            self.raise_error("'return' 语句只能出现在函数内部", tree)
            return

        type = self.curr_func.type
        if tree.expr:
            if type == VOID:
                self.raise_error("void 函数不能有返回值", tree)
            elif not self.is_assignable(type, tree.expr.ctype):
                self.raise_error(f"函数期望返回 '{type}' 而非 '{tree.expr.ctype}'", tree.expr)
        elif type != VOID:
            self.raise_error(f"函数期望返回 '{type}'", tree)

    def break_stmt(self, tree):
        """break 语句 → 必须在循环内部"""
        if self.loop_depth == 0:
            self.raise_error("'break' 语句只能出现在循环内部", tree)

    def continue_stmt(self, tree):
        """continue 语句 → 必须在循环内部"""
        if self.loop_depth == 0:
            self.raise_error("'continue' 语句只能出现在循环内部", tree)

    def empty_stmt(self, tree):
        """空语句 (;) → 什么都不做"""
        pass

    def expr_stmt(self, tree):
        """表达式语句 → 分析表达式即可"""
        if tree.expr:
            self.visit(tree.expr)

    def expression(self, tree):
        """逗号表达式：分析每个子表达式，类型取最后一个。

        例如 a = 1, b = 2 的类型是 b = 2 的类型。
        """
        for expr in tree.exprs:
            self.visit(expr)
        if tree.exprs:
            tree.ctype = tree.exprs[-1].ctype
            self._set_attrs(tree, rule=f"expr_seq → {tree.ctype}",
                derived_from=[f'expr[{len(tree.exprs)-1}].ctype={tree.ctype}'])

    def assign_op(self, tree):
        """赋值表达式的类型检查。

        支持:
        - 普通赋值: x = 5
        - 复合赋值: x += 3, x *= 2 等

        检查项:
        - 左值必须是可赋值的（变量、数组元素、解引用等）
        - 类型必须兼容
        """
        self.visit(tree.left)
        self.visit(tree.right)
        if not self.is_lvalue(tree.left):
            self.raise_error("表达式无法赋值", tree.left)
            return
        if hasattr(tree.left, 'ctype') and hasattr(tree.right, 'ctype'):
            ltype, rtype = tree.left.ctype, tree.right.ctype
            if tree.op == '=':
                # 普通赋值
                if not self.is_assignable(ltype, rtype):
                    self.raise_error(f"无法将 '{rtype}' 赋值给 '{ltype}'", tree)
            else:
                # 复合赋值: x += y → 等价于 x = x + y，先算 x+y 的类型
                op = tree.op[:-1]  # 去掉 '=' 得到纯操作符
                ctype = self.parse_op(op, ltype, rtype)
                if not ctype:
                    self.raise_error(f"运算符 '{tree.op}' 无效", tree)
                elif not self.is_assignable(ltype, ctype):
                    self.raise_error(f"无法将 '{rtype}' 赋值给 '{ltype}'", tree)
            tree.ctype = ltype
        self._set_attrs(tree,
            rule=f"infer_assign({tree.op}, {tree.left.ctype}, {tree.right.ctype}) → {tree.ctype}",
            derived_from=[f'lhs.ctype={tree.left.ctype}', f'rhs.ctype={tree.right.ctype}'])

    def binary_op(self, tree):
        """二元运算的类型推导。

        例如: a + b → 根据 a 和 b 的类型推导 a+b 的类型。
        int + float → float
        """
        self.visit(tree.left)
        self.visit(tree.right)
        ctype = self.parse_op(tree.op, tree.left.ctype, tree.right.ctype)
        if ctype is None:
            self.raise_error(f"运算符 '{tree.op}' 无效", tree)
        else:
            tree.ctype = ctype
        self._set_attrs(tree,
            rule=f"infer_binary({tree.op}, {tree.left.ctype}, {tree.right.ctype}) → {tree.ctype}",
            derived_from=[f'lhs.ctype={tree.left.ctype}', f'rhs.ctype={tree.right.ctype}'])

    def unary_op(self, tree):
        """一元运算的类型推导。

        支持:
        - +expr: 正号（数值类型）
        - -expr: 负号（数值类型）
        - !expr: 逻辑非（可转布尔的类型 → bool）
        - *expr: 解引用（指针/数组 → 元素类型）
        - &expr: 取地址（左值 → 指针类型）
        - ++expr / --expr: 前置自增/自减
        """
        self.visit(tree.operand)
        ctype, op = tree.operand.ctype, tree.op
        if op in ('+', '-'):
            if ctype in (INT, FLOAT):
                tree.ctype = ctype
            else:
                self.raise_error(f"运算符 '{op}' 的操作数必须是数值类型而非 '{ctype}'", tree.operand)
        elif op == '!':
            if self.is_assignable(BOOL, ctype):
                tree.ctype = BOOL
            else:
                self.raise_error(f"运算符 '{op}' 的操作数能转换为 'bool' 而非 '{ctype}'", tree.operand)
        elif op == '*':
            # 解引用: *ptr → ptr 指向的类型
            if isinstance(ctype, PointerType):
                tree.ctype = ctype.type
            elif isinstance(ctype, ArrayType):
                tree.ctype = ctype.type
            else:
                self.raise_error(f"运算符 '{op}' 只能用于指针或数组类型而非 '{ctype}'", tree.operand)
        elif op == '&':
            # 取地址: &x → PointerType(x的类型)
            if self.is_lvalue(tree.operand):
                tree.ctype = PointerType(ctype)
            else:
                self.raise_error(f"运算符 '{op}' 只能用于可修改的左值", tree.operand)
        elif op in ('++', '--'):
            # 前置自增/自减
            if self.is_lvalue(tree.operand) and (ctype in (INT, FLOAT) or isinstance(ctype, PointerType)):
                tree.ctype = ctype
            else:
                self.raise_error(f"运算符 '{op}' 的操作数必须是可修改的值或指针左值", tree.operand)
        self._set_attrs(tree,
            rule=f"infer_unary({op}, {ctype}) → {tree.ctype}",
            derived_from=[f'operand.ctype={ctype}'])

    def postfix_op(self, tree):
        """后置自增/自减的类型检查（i++, i--）。

        操作数必须是可修改的左值，且类型为 int/float/pointer。
        """
        self.visit(tree.operand)
        ctype = tree.operand.ctype
        if self.is_lvalue(tree.operand) and (ctype in (INT, FLOAT) or isinstance(ctype, PointerType)):
            tree.ctype = ctype
        else:
            self.raise_error(f"运算符 '{tree.op}' 的操作数必须是可修改的值或指针左值", tree.operand)

    def func_call(self, tree):
        """函数调用的类型检查。

        工作流程:
        1. 分析函数表达式和实参
        2. Special case: printf / scanf 是内置函数，跳过参数检查
        3. 检查实参数量是否匹配
        4. 逐一检查每个实参是否能赋值给对应形参类型
        5. 推导调用结果的类型 = 函数的返回类型
        """
        self.visit(tree.func)
        for arg in tree.args:
            self.visit(arg)

        ctype = tree.func.ctype
        # printf 和 scanf 是变参函数，跳过严格检查
        if tree.func.value in ("printf", "scanf"):
            tree.ctype = ctype.type
            self._set_attrs(tree, rule=f"builtin_call({tree.func.value}) → {tree.ctype}")
            return

        if not isinstance(ctype, FunctionType):
            self.raise_error("无法调用表达式", tree.func)

        args = [arg.ctype for arg in tree.args]
        if len(args) != len(ctype.params):
            self.raise_error(f"函数调用参数数量期望 '{len(ctype.params)}' 个而非 '{len(args)}' 个", tree)
        else:
            for i, (arg, param) in enumerate(zip(args, ctype.params)):
                if not self.is_assignable(param, arg):
                    self.raise_error(f"函数调用第 {i + 1} 个参数类型期望 '{param}' 而非 {arg}", tree.args[i])
        tree.ctype = ctype.type
        self._set_attrs(tree,
            rule=f"func_call({tree.func.value}, [{', '.join(str(a) for a in args)}]) → {tree.ctype}",
            derived_from=[f'func.type={ctype}'])

    def array_access(self, tree):
        """数组元素访问的类型检查（arr[index]）。

        - 索引必须是 int 类型
        - 数组或指针类型 → 元素类型
        """
        self.visit(tree.array)
        self.visit(tree.index)
        array_type, index_type = tree.array.ctype, tree.index.ctype
        if index_type != INT:
            self.raise_error("数组下标必须是整数类型", tree.index)
        if isinstance(array_type, ArrayType):
            tree.ctype = array_type.type
        elif isinstance(array_type, PointerType):
            tree.ctype = array_type.type
        else:
            self.raise_error(f"运算符 '[]' 只能用于指针或数组类型而非 '{array_type}'", tree)
        self._set_attrs(tree,
            rule=f"array_access({array_type}[{index_type}]) → {tree.ctype}",
            derived_from=[f'array.ctype={array_type}', f'index.ctype={index_type}'])

    def member_access(self, tree):
        """结构体/联合体成员访问的类型检查。

        支持两种形式:
        - obj.field: 直接成员访问（obj 是结构体）
        - ptr->field: 指针成员访问（ptr 是指向结构体的指针）

        检查项:
        - 对象类型必须是结构体/联合体（或指针形式）
        - 成员名必须存在于该类型中
        - 推导成员的类型
        """
        self.visit(tree.object)

        obj_type, member_name = tree.object.ctype, tree.member.value
        if tree.arrow:
            # ptr->field: ptr 必须是指向结构体的指针
            if isinstance(obj_type, PointerType) and isinstance(obj_type.type, CompoundType):
                comp_type = obj_type.type
            else:
                self.raise_error(f"运算符 '->' 必须用于指向结构体或联合体的指针而非 '{obj_type}'", tree.object)
                return
        else:
            # obj.field: obj 必须是结构体或联合体
            if isinstance(obj_type, CompoundType):
                comp_type = obj_type
            else:
                self.raise_error(f"运算符 '.' 必须用于结构体或联合体而非 '{obj_type}'", tree.object)
                return

        if member_name in comp_type.members:
            tree.ctype = comp_type.members[member_name]
            tree.member.ctype = tree.ctype
            tree.index = list(comp_type.members.keys()).index(member_name)
            self._set_attrs(tree,
                rule=f"member_access({comp_type.name}.{member_name}) → {tree.ctype}",
                derived_from=[f'object.ctype={obj_type}', f'member={member_name}'])
            # 成员标识符标注字段来源
            self._set_attrs(tree.member,
                rule=f"field({comp_type.name}, {member_name}) → {tree.ctype}")
        else:
            self.raise_error(f"类型 '{comp_type}' 中不存在名为 '{member_name}' 的成员", tree.member)

    def identifier(self, tree):
        """标识符的语义分析 → 符号表查找。

        这是最常用的操作之一。每当代码中出现变量名/函数名/枚举常量等，
        都需要在符号表中查找，获取其类型和符号信息。

        检查项:
        - 标识符是否已声明
        - 标注类型和符号信息
        """
        symbol = self.table.lookup(tree.value)
        if not symbol:
            self.raise_error(f"未声明的标识符 '{tree.value}'", tree)
            return
        tree.ctype = symbol.type
        tree.symbol = symbol
        self._set_attrs(tree,
            rule=f"lookup({tree.value}) → {symbol.kind.name}:{symbol.type}",
            derived_from=[f'symbol_table["{tree.value}"]'])

    # ===============  字面量 → 类型标注  ===============
    # 每个字面量节点在语义分析阶段被标注上对应的 C 类型

    @staticmethod
    def integer(tree):
        """整数字面量 → int 类型，值为 Python int"""
        tree.ctype = INT
        tree.attrs['value'] = int(tree.value, 0)  # 支持 0x, 0b 等进制
        tree.attrs['rule'] = 'literal(int)'
        tree.attrs['ctype'] = INT

    @staticmethod
    def decimal(tree):
        """浮点字面量 → float 类型，值为 Python float"""
        tree.ctype = FLOAT
        tree.attrs['value'] = float(tree.value)
        tree.attrs['rule'] = 'literal(float)'
        tree.attrs['ctype'] = FLOAT

    @staticmethod
    def character(tree):
        """字符字面量 → char 类型，值为 ASCII 码 (int)"""
        tree.ctype = CHAR
        tree.attrs['value'] = ord(tree.value)
        tree.attrs['rule'] = 'literal(char)'
        tree.attrs['ctype'] = CHAR

    @staticmethod
    def string(tree):
        """字符串字面量 → char[] 类型（ArrayType(CHAR)）"""
        tree.ctype = ArrayType(CHAR)
        tree.attrs['value'] = tree.value
        tree.attrs['rule'] = 'literal(string)'
        tree.attrs['ctype'] = tree.ctype

    @staticmethod
    def bool(tree):
        """布尔字面量 → bool 类型"""
        tree.ctype = BOOL
        tree.attrs['value'] = tree.value
        tree.attrs['rule'] = 'literal(bool)'
        tree.attrs['ctype'] = BOOL

    @staticmethod
    def nullptr(tree):
        """nullptr 字面量 → NULL 类型（特殊的空指针类型）"""
        tree.ctype = NULL
        tree.attrs['value'] = None
        tree.attrs['rule'] = 'literal(nullptr)'
        tree.attrs['ctype'] = NULL

    def __default__(self, tree):
        """默认处理方法：对于未明确定义 visit 方法的节点类型，
        递归访问所有子节点。"""
        for child in tree.children:
            if isinstance(child, Tree):
                self.visit(child)
