"""
AST 转换器 — 将 Lark 的原生 CST (具体语法树) 转换为自定义 AST 节点。

这是语法分析的子阶段。Lark 的 LALR 解析器输出的是 "v_args" 格式的 Tree 对象，
通过 ASTTransformer 将每个产生式映射为 compiler.tree 中定义的自定义 ASTNode 子类。

核心机制:
- @v_args(inline=True, meta=True): 子节点作为独立参数传入, meta 携带行列号
- 每个静态方法对应 syntax.lark 中的一个产生式规则名
- 返回自定义 ASTNode 子类实例 (如 Program, FunctionDefinition, Integer, ...)
"""

from types import SimpleNamespace as Meta

from lark import Transformer, Token
from lark import v_args

from .tree import *


@v_args(inline=True, meta=True)
class ASTTransformer(Transformer):
    """将 Lark CST 节点转换为自定义 AST 节点。

    转换规则：syntax.lark 中的每个产生式都对应此类中的一个静态方法，
    方法名与产生式名相同，返回值是 compiler.tree 中对应的 ASTNode 子类。
    """

    # ==================== 程序顶层结构 ====================

    @staticmethod
    def program(meta, *args):
        """program → (definition | declaration)+

        整个程序的根节点。过滤掉 None，剩余的声明和定义组成 Program。
        """
        args = [i for i in args if i is not None]
        decl = ExternalDeclaration(args, meta)
        return Program(decl, meta)

    @staticmethod
    def unit(_, child):
        """unit → (definition | declaration) — 直接把子节点向上传递"""
        return child

    @staticmethod
    def definition(_, child):
        """definition → func_def | comp_def | enum_def — 直接把子节点向上传递"""
        return child

    @staticmethod
    def declaration(_, child):
        """declaration → func_decl | var_decl | arr_decl — 直接把子节点向上传递"""
        return child

    # ==================== 类型声明组件 ====================

    @staticmethod
    def specifier(meta, type):
        """specifier → TYPE — 类型说明符（int, float, char 等）

        将 token 的 value 包装为 Specifier 节点。
        """
        return Specifier(type.value, meta)

    @staticmethod
    def declarator(meta, *args):
        """declarator → IDENT | "*" IDENT — 声明符（变量名 + 可选的指针标记）

        - 1 个参数: 普通变量 (args[0] = IDENT)
        - 2 个参数: 指针变量 (args[0] = '*', args[1] = IDENT)
        """
        name, pointer = None, False
        if len(args) == 1:
            name = args[0]
        else:
            name = args[1]
            pointer = True
        return Declarator(name, pointer, meta=meta)

    @staticmethod
    def initializer(meta, *args):
        """initializer → expr | "{" initializer ("," initializer)* "}"

        - 单参数: 简单初始化表达式 (如 = 5)
        - 多参数: 复合初始化列表 (如 = {1, 2, 3})，过滤掉逗号 token
        """
        if len(args) == 1:
            return args[0]
        else:
            inits = [i for i in args if not isinstance(i, Token)]
            return Initializer(inits, meta)

    @staticmethod
    def array_suffix(meta, *args):
        """array_suffix → "[" "]" | "[" INTEGER "]" — 数组维度后缀

        - "[]": 不指定大小的数组 (size=None)
        - "[10]": 指定大小的数组 (size=10)
        """
        size = None
        if len(args) == 3:
            size = args[1]
        return ArraySuffix(size, meta)

    @staticmethod
    def param_suffix(meta, *args):
        """param_suffix → "(" ")" | "(" param_list ")" — 函数参数后缀

        - "()": 无参 (params=['void'])
        - "(int x, float y)": 有参 (params=[Parameter, Parameter])
        """
        params = []
        if len(args) == 3:
            if isinstance(args[1], list):
                params = args[1]
            else:
                params = ['void']
        return ParamSuffix(params, meta)

    @staticmethod
    def param_list(_, *args):
        """param_list → param_item ("," param_item)*

        取偶数索引项（跳过逗号 token），返回参数列表。
        """
        return list(args[::2])

    @staticmethod
    def param_item(meta, spec, decl):
        """param_item → specifier declarator — 单个函数参数"""
        return Parameter(spec, decl, meta)

    # ==================== 函数定义 ====================

    @staticmethod
    def func_def(meta, spec, decl, suffix, body):
        """func_def → specifier declarator param_suffix comp_stmt

        函数定义的完整表示:
        - spec: 返回类型 (如 int)
        - decl: 函数名 (如 main)
        - suffix: 参数列表 (ParamSuffix)
        - body: 函数体 (Statement)
        """
        decl.suffix.append(suffix)
        decl.update()
        return FunctionDefinition(spec, decl, body, meta)

    # ==================== 结构体定义 (struct) ====================

    @staticmethod
    def comp_def(meta, spec, decl, _, members, __, ___):
        """comp_def → "struct" IDENT "{" comp_list "}" ";" — 结构体定义"""
        spec = Specifier(spec, meta)
        decl = Declarator(decl, meta=meta)
        return CompoundDefinition(spec, decl, members, meta)

    @staticmethod
    def comp_list(_, *args):
        """comp_list → comp_item+ — 结构体成员列表"""
        return list(args)

    @staticmethod
    def comp_item(meta, spec, decls, _):
        """comp_item → specifier declarator_list ";" — 单个结构体成员"""
        return Member(spec, decls, meta)

    @staticmethod
    def member_list(_, *args):
        """member_list → member_item ("," member_item)*"""
        return list(args[::2])

    @staticmethod
    def member_item(_, decl, *suffix):
        """member_item → declarator (array_suffix)* — 成员声明，可能含数组后缀"""
        decl.suffix.extend(suffix)
        decl.update()
        return decl

    # ==================== 枚举定义 (enum) ====================

    @staticmethod
    def enum_def(meta, _, decl, __, enumerators, ___, ____):
        """enum_def → "enum" IDENT "{" enum_list "}" ";" — 枚举定义"""
        decl = Declarator(decl, meta=meta)
        return EnumDefinition(decl, enumerators, meta)

    @staticmethod
    def enum_list(_, *args):
        """enum_list → enum_item ("," enum_item)*"""
        return list(args[::2])

    @staticmethod
    def enum_item(meta, name, _=None, value=None):
        """enum_item → IDENT ["=" INTEGER] — 枚举项，可选的整数值"""
        return Enumerator(name, value, meta)

    # ==================== 函数声明 / 变量声明 ====================

    @staticmethod
    def func_decl(meta, spec, decls, _):
        """func_decl → specifier func_list ";" — 前向函数声明"""
        return FunctionDeclaration(spec, decls, meta)

    @staticmethod
    def func_list(_, *args):
        """func_list → func_item ("," func_item)*"""
        return list(args[::2])

    @staticmethod
    def func_item(_, decl, suffix):
        """func_item → declarator param_suffix — 函数声明项"""
        decl.suffix.append(suffix)
        decl.update()
        return decl

    @staticmethod
    def var_decl(meta, spec, decls, _):
        """var_decl → specifier var_list ";" — 变量声明"""
        return VariableDeclaration(spec, decls, meta)

    @staticmethod
    def var_list(_, *args):
        """var_list → var_item ("," var_item)*"""
        return list(args[::2])

    @staticmethod
    def var_item(_, decl, __=None, init=None):
        """var_item → declarator ["=" initializer] — 变量声明项（可含初始化值）"""
        if init is not None:
            decl.init = init
            decl.update()
        return decl

    @staticmethod
    def arr_decl(meta, spec, decls, _):
        """arr_decl → specifier arr_list ";" — 数组声明"""
        return ArrayDeclaration(spec, decls, meta)

    @staticmethod
    def arr_list(_, *args):
        """arr_list → arr_item ("," arr_item)*"""
        return list(args[::2])

    @staticmethod
    def arr_item(_, *args):
        """arr_item → declarator (array_suffix)* ["=" initializer] — 数组项"""
        decl = args[0]
        i = 1
        # 收集所有数组后缀（支持多维数组）
        while i < len(args) and isinstance(args[i], ArraySuffix):
            decl.suffix.append(args[i])
            i += 1
        if i < len(args):
            decl.init = args[i + 1]
        decl.update()
        return decl

    # ==================== 语句层 ====================

    @staticmethod
    def statement(_, child):
        """statement → ... — 直接把子节点向上传递"""
        return child

    @staticmethod
    def comp_stmt(meta, _, *args):
        """comp_stmt → "{" statement* "}" — 复合语句（花括号块）"""
        stmts = list(args[:-1])  # 去掉最后的 "}"
        return Statement(stmts, meta)

    @staticmethod
    def expr_stmt(meta, *args):
        """expr_stmt → expression ";" | ";" — 表达式语句或空语句"""
        if len(args) == 2:
            expr = args[0]
            return ExpressionStatement(expr, meta)
        return EmptyStatement(meta)

    @staticmethod
    def select_stmt(meta, _, __, cond, ___, then, ____=None, orelse=None):
        """select_stmt → "if" "(" expression ")" statement ["else" statement]

        对应 if/if-else 语句。
        参数位置: IF, LPAREN, cond, RPAREN, then, ELSE?, orelse?
        """
        return IfStatement(cond, then, orelse, meta)

    @staticmethod
    def iter_stmt(meta, *args):
        """iter_stmt → while_loop | for_loop

        根据第一个 token 类型判断:
        - WHILE: while (cond) body
        - FOR: for (init; cond; post) body
        """
        if args[0].type == 'WHILE':
            # while ( expression ) statement
            cond = args[2]
            body = args[4]
            return WhileStatement(cond, body, meta)
        else:
            # for ( init? ; cond? ; post? ) statement
            init = args[2]
            body = args[-1]
            try:
                # 找到分号的位置来确定 cond 和 post
                semi = args.index(
                    next(i for i in args
                         if isinstance(i, Token) and i.type == 'SEMICOLON'))
                cond = args[3] if semi > 3 else None
                post = args[semi + 1] if semi + 1 < len(args) - 2 else None
            except StopIteration:
                cond, post = None, None
            return ForStatement(init, cond, post, body, meta)

    @staticmethod
    def jump_stmt(meta, *args):
        """jump_stmt → "continue" ";" | "break" ";" | "return" expression? ";"

        三种跳转语句:
        - continue: 跳到循环体开头
        - break: 跳出循环
        - return: 从函数返回（可选返回值）
        """
        if args[0].type == 'CONTINUE':
            return ContinueStatement(meta)

        elif args[0].type == 'BREAK':
            return BreakStatement(meta)
        else:
            # return [expression] ";"
            expr = args[1] if len(args) == 3 else None
            return ReturnStatement(expr, meta=meta)

    # ==================== 表达式层 ====================

    @staticmethod
    def expression(meta, *args):
        """expression → assign_expr ("," assign_expr)* — 逗号表达式"""
        exprs = list(args[::2])  # 跳过逗号
        return Expression(exprs, meta)

    @staticmethod
    def assign_expr(meta, *args):
        """assign_expr → lor_expr | unary_expr assign_op assign_expr — 赋值表达式

        单参数: 纯条件表达式
        多参数: "x = 5" 或 "x += 3" 等赋值运算
        """
        if len(args) == 1:
            return args[0]
        else:
            # args[0]=左值, args[1]=操作符, args[2]=右值
            op, left, right = args[1].value, args[0], args[2]
            return AssignOp(op, left, right, meta)

    @staticmethod
    def assign(_, child):
        """assign → "=" | "+=" | "-=" | ... — 赋值操作符，直接向上传递"""
        return child

    # ==================== 二元运算符 → 统一的左结合处理 ====================

    @staticmethod
    def process_binary_op(meta, *args):
        """二元表达式的通用处理：处理左结合的多运算符链。

        例如 a + b + c 解析为 (a + b) + c，递归构建左结合的 AST。

        Args:
            meta: 行列号元信息
            *args: 交替出现的 [操作数, 操作符, 操作数, 操作符, 操作数, ...]
        """
        left = args[0]
        i = 1
        while i < len(args):
            op, right = args[i], args[i + 1]
            left = BinaryOp(op.value, left, right, meta)
            i += 2
        return left

    def lor_expr(self, meta, *args):
        """lor_expr → land_expr ("||" land_expr)* — 逻辑或 (短路求值)"""
        return self.process_binary_op(meta, *args)

    def land_expr(self, meta, *args):
        """land_expr → equal_expr ("&&" equal_expr)* — 逻辑与 (短路求值)"""
        return self.process_binary_op(meta, *args)

    def equal_expr(self, meta, *args):
        """equal_expr → rel_expr (("==" | "!=") rel_expr)* — 等值比较"""
        return self.process_binary_op(meta, *args)

    def rel_expr(self, meta, *args):
        """rel_expr → add_expr (("<" | ">" | "<=" | ">=") add_expr)* — 关系比较"""
        return self.process_binary_op(meta, *args)

    def add_expr(self, meta, *args):
        """add_expr → mul_expr (("+" | "-") mul_expr)* — 加减运算"""
        return self.process_binary_op(meta, *args)

    def mul_expr(self, meta, *args):
        """mul_expr → unary_expr (("*" | "/" | "%") unary_expr)* — 乘除运算"""
        return self.process_binary_op(meta, *args)

    # ==================== 一元运算符 ====================

    @staticmethod
    def unary_expr(meta, *args):
        """unary_expr → postfix_expr | ("+" | "-" | "!" | "*" | "&") unary_expr

        - 单参数: 直接后置表达式
        - 双参数: 一元操作符 + 操作数（如 -x, !flag, *ptr）
        """
        if len(args) == 1:
            return args[0]
        else:
            op = args[0].value
            operand = args[1]
            return UnaryOp(op, operand, meta)

    # ==================== 后置表达式 → 函数调用/数组下标/成员访问 ====================

    @staticmethod
    def postfix_expr(meta, *args):
        """postfix_expr → primary_expr (后缀操作)*

        后缀操作类型:
        - "(" ... ")": 函数调用 → FunctionCall
        - "[" expr "]": 数组下标 → ArrayAccess
        - "." IDENT: 成员访问 → MemberAccess
        - "->" IDENT: 指针成员访问 → MemberAccess(arrow=True)
        - "++" / "--": 后置自增/自减 → PostfixOp
        """
        node = args[0]
        i = 1
        while i < len(args):
            op = args[i]
            if op.type == 'LPAREN':
                # 函数调用: func(args) 或 func()
                if isinstance(args[i + 1], Token) and args[i + 1].type == 'RPAREN':
                    node = FunctionCall(node, [], meta)
                    i += 2
                else:
                    node = FunctionCall(node, args[i + 1], meta)
                    i += 3
            elif op.type == 'LBRACK':
                # 数组访问: arr[expr]
                node = ArrayAccess(node, args[i + 1], meta)
                i += 2
            elif op.type in ('DOT', 'ARROW'):
                # 成员访问: obj.member 或 ptr->member
                arrow = (op.type == 'ARROW')
                member = args[i + 1]
                node = MemberAccess(node, member, arrow, meta)
                i += 2
            elif op.type in ('INCREMENT', 'DECREMENT'):
                # 后置自增/自减: i++ 或 i--
                node = PostfixOp(op.value, node, meta)
                i += 1
            else:
                i += 1
        return node

    @staticmethod
    def postfix_expr(meta, *args):
        """postfix_expr → primary_expr (后缀操作)* （第二版实现）

        后缀操作类型:
        - "(" ... ")": 函数调用 → FunctionCall
        - "[" expr "]": 数组下标 → ArrayAccess
        - "." IDENT: 成员访问 → MemberAccess
        - "->" IDENT: 指针成员访问 → MemberAccess(arrow=True)
        - "++" / "--": 后置自增/自减 → PostfixOp
        """
        node = args[0]

        i = 1
        while i < len(args):
            op_or_arg = args[i]
            if op_or_arg.type == 'LPAREN':
                # 函数调用处理 — 区分无参调用和带参调用
                next_item = args[i + 1]
                if isinstance(next_item, Token) and next_item.type == 'RPAREN':
                    node = FunctionCall(node, [], meta)
                    i += 2  # 跳过 LPAREN 和 RPAREN
                else:
                    arg_list = next_item
                    # 如果参数是逗号表达式，args[i+1] 是 Expression 节点
                    # 如果只有一个参数，args[i+1] 是单个节点
                    if not isinstance(arg_list, list):
                        arg_list = [arg_list]
                    node = FunctionCall(node, arg_list, meta)
                    i += 3
            elif op_or_arg.type == 'LBRACK':
                # 数组访问: arr[expr]
                expression = args[i + 1]
                node = ArrayAccess(node, expression, meta)
                i += 3
            elif op_or_arg.type in ('DOT', 'ARROW'):
                # 成员访问: obj.member 或 ptr->member
                arrow = (op_or_arg.type == 'ARROW')
                member = args[i + 1]
                node = MemberAccess(node, member, arrow, meta)
                i += 2

            elif op_or_arg.type in ('INCREMENT', 'DECREMENT'):
                # 后置自增/自减: i++ 或 i--
                node = PostfixOp(op_or_arg.value, node, meta)
                i += 1
            else:
                i += 1

        return node

    # ==================== 参数 / 基本表达式 / 字面量 ====================

    @staticmethod
    def argument(_, *args):
        """argument → assign_expr ("," assign_expr)* — 函数实参列表"""
        return list(args[::2])

    @staticmethod
    def const_expr(_, child):
        """const_expr → lor_expr — 常量表达式，直接向上传递"""
        return child

    @staticmethod
    def primary_expr(_, *args):
        """primary_expr → IDENT | const | "(" expression ")" — 基本表达式

        - 单参数: 标识符或字面量
        - 双参数: 括号内的表达式 (去括号)
        """
        if len(args) == 1:
            return args[0]
        else:
            return args[1]

    @staticmethod
    def const(_, child):
        """const → INTEGER | DECIMAL | CHAR | STRING | TRUE | FALSE — 常量"""
        return child

    # ==================== 字面量 Token → AST 节点 ====================

    @staticmethod
    def INTEGER(token):
        """INTEGER token → Integer 节点（int 字面量）"""
        meta = Meta(line=token.line, column=token.column)
        return Integer(token.value, meta)

    @staticmethod
    def DECIMAL(token):
        """DECIMAL token → Decimal 节点（float 字面量）"""
        meta = Meta(line=token.line, column=token.column)
        return Decimal(token.value, meta)

    @staticmethod
    def CHARACTER(token):
        """CHARACTER token → Character 节点（char 字面量）"""
        meta = Meta(line=token.line, column=token.column)
        return Character(token.value, meta)

    @staticmethod
    def STRING(token):
        """STRING token → String 节点（字符串字面量）"""
        meta = Meta(line=token.line, column=token.column)
        return String(token.value, meta)

    @staticmethod
    def TRUE(token):
        """TRUE token → Bool 节点（true 关键字）"""
        meta = Meta(line=token.line, column=token.column)
        return Bool(token.value, meta)

    @staticmethod
    def FALSE(token):
        """FALSE token → Bool 节点（false 关键字）"""
        meta = Meta(line=token.line, column=token.column)
        return Bool(token.value, meta)

    @staticmethod
    def NULLPTR(token):
        """NULLPTR token → NullPtr 节点（nullptr 关键字）"""
        meta = Meta(line=token.line, column=token.column)
        return NullPtr(meta)

    @staticmethod
    def IDENT(token):
        """IDENT token → Identifier 节点（变量名/函数名）"""
        meta = Meta(line=token.line, column=token.column)
        return Identifier(token.value, meta)

    @staticmethod
    def TYPE(token):
        """TYPE token → Identifier 节点（类型关键字 int/float/char/void 等）"""
        meta = Meta(line=token.line, column=token.column)
        return Identifier(token.value, meta)

    @staticmethod
    def IMM(token):
        """IMM token → Identifier 节点（立即数？特殊用途）"""
        meta = Meta(line=token.line, column=token.column)
        return Identifier(token.value, meta)
