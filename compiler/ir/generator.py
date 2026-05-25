"""
LLVM IR 生成器 — 编译流水线的第四趟扫描（前半部分）。

功能: 将带 TAC 注释的 AST 转换为 LLVM IR (中间表示)。

LLVM IR 是一种 SSA (静态单赋值) 形式的中间语言，广泛用于工业级编译器。
本模块将 AST 映射为 LLVM IR 指令，然后由 LLVM 后端生成目标机器的汇编代码。

核心技术:
- llvmlite: Python 绑定到 LLVM C API
- SSA (Static Single Assignment): 每个变量只能赋值一次
- IRBuilder: 提供方便的 IR 构建 API
- 类型映射: C 类型 → LLVM IR 类型 (int → i32, float → float, ...)
"""

import codecs
from pathlib import Path

from lark.visitors import Interpreter
from llvmlite import ir, binding

from compiler.semantic.symbol import *
from compiler.semantic.type import *
from compiler.tree import *
from compiler.utils import write_file

# ---------- 初始化 LLVM 后端 ----------
binding.initialize()
binding.initialize_native_target()
binding.initialize_native_asmprinter()


class Generator(Interpreter):
    """LLVM IR 代码生成器。

    遍历已做类型标注和 TAC 注释的 AST，生成等价语义的 LLVM IR。
    generate(tree) 是入口方法，返回 IR 字符串。
    """

    def __init__(self):
        super().__init__()
        # ---------- LLVM 核心数据结构 ----------

        # LLVM Module — 整个编译单元的容器，包含所有函数和全局变量
        self.module = ir.Module(name='main_module')

        # IRBuilder — 用于在当前基本块中插入 IR 指令的工具
        self.builder = None

        # 最终生成的 IR 文本
        self.ir = None

        # ---------- 上下文状态 ----------
        self.curr_func = None       # 当前正在生成的 LLVM 函数
        self.loop_stack = []        # 循环栈: [(end_block, continue_target), ...]

        # ---------- 缓存 ----------
        self.strings = {}           # 字符串常量池 (去重)
        self.structs = {}           # 结构体类型缓存

        # ---------- 预定义标准库函数 ----------
        # printf / scanf 是 C 变参函数，签名: i32 printf(i8*, ...)
        void_type = ir.IntType(8).as_pointer()
        func_type = ir.FunctionType(ir.IntType(32), [void_type], var_arg=True)
        ir.Function(self.module, func_type, name="printf")
        ir.Function(self.module, func_type, name="scanf")

        # 目标平台三元组 (Windows MSVC 格式)
        self.module.triple = 'x86_64-pc-windows-msvc19.44.35209'

    # ===============  基础方法  ===============

    def generate(self, tree):
        """IR 生成入口：遍历 AST 生成 LLVM IR，然后验证。

        Args:
            tree: 带语义标注和 TAC 注释的 AST

        Returns:
            LLVM IR 文本字符串（.ll 格式）
        """
        self.visit(tree)
        self.ir = str(self.module)
        try:
            # 调用 LLVM 的 IR 验证器确认生成的 IR 是合法的
            mod = binding.parse_assembly(self.ir)
            mod.verify()
        except RuntimeError as e:
            print("IR报错了！！！！不！！！！！！！！！")
            raise e
        return self.ir

    def save(self, file_path=''):
        """保存原始 IR 到 04 org_ir.txt 文件。

        Args:
            file_path: 输出目录路径
        """
        write_file(self.ir, Path(file_path) / '04 org_ir.txt')

    # ===============  辅助方法  ===============

    def get_type(self, ctype):
        """将 C 类型对象映射为 LLVM IR 类型。

        类型映射关系:
        - void → VoidType()
        - int  → IntType(32)   (i32)
        - float→ FloatType()
        - char → IntType(8)    (i8)
        - bool → IntType(1)    (i1)
        - NULL → i8 指针
        - int* → i32*
        - int[10] → [10 x i32]
        - 结构体 → 命名结构体类型 (惰性创建)

        Args:
            ctype: C 类型对象 (BasicType / PointerType / ArrayType / ...)

        Returns:
            LLVM IR 类型对象
        """
        if isinstance(ctype, BasicType):
            if ctype == VOID:
                return ir.VoidType()
            elif ctype == INT:
                return ir.IntType(32)
            elif ctype == FLOAT:
                return ir.FloatType()
            elif ctype == CHAR:
                return ir.IntType(8)
            elif ctype == BOOL:
                return ir.IntType(1)
            elif ctype == NULL:
                return ir.IntType(8).as_pointer()
        elif isinstance(ctype, PointerType):
            return self.get_type(ctype.type).as_pointer()
        elif isinstance(ctype, ArrayType):
            elem_type = self.get_type(ctype.type)
            size = ctype.size if ctype.size is not None else 0
            return ir.ArrayType(elem_type, size)
        elif isinstance(ctype, FunctionType):
            return_type = self.get_type(ctype.type)
            param_types = [self.get_type(p) for p in ctype.params]
            return ir.FunctionType(return_type, param_types)
        elif isinstance(ctype, CompoundType):
            # 结构体类型惰性创建（支持前向引用）
            if ctype.name in self.structs:
                return self.structs[ctype.name]
            struct_type = self.module.context.get_identified_type(ctype.name)
            self.structs[ctype.name] = struct_type
            member_types = [self.get_type(m) for m in ctype.members.values()]
            if ctype.union:
                # 联合体取最大成员的大小
                largest_member = max(member_types,
                                     key=lambda m: m.get_abi_size(self.module.data_layout))
                struct_type.set_body(largest_member)
            else:
                struct_type.set_body(*member_types)
            return struct_type
        elif isinstance(ctype, EnumType):
            # 枚举在 LLVM IR 中表示为 i32
            return ir.IntType(32)
        raise Exception

    def get_address(self, node):
        """递归获取表达式节点的内存地址。

        用于赋值、取地址 (&)、成员访问等场景。
        例如:
        - 变量 x → symbol.value (alloca 返回的指针)
        - 数组元素 arr[i] → gep arr, 0, i
        - 成员 st.name → gep st, 0, field_index
        - 解引用 *ptr → ptr 的值
        - 取地址 &x → x 的地址

        Args:
            node: AST 表达式节点

        Returns:
            LLVM IR 值（指针类型）
        """
        if isinstance(node, Identifier):
            # 变量 → 符号表中存储的指针
            return node.symbol.value
        elif isinstance(node, ArrayAccess):
            # arr[i] → gep arr, 0, i
            arr_addr = self.get_address(node.array)
            arr_idx = self.visit(node.index)
            indices = [ir.Constant(ir.IntType(32), 0), arr_idx]
            return self.builder.gep(arr_addr, indices, inbounds=True)
        elif isinstance(node, MemberAccess):
            # st.field → gep st, 0, field_index
            obj_addr = self.get_address(node.object)
            obj_idx = node.index  # 字段在结构体中的序号
            indices = [ir.Constant(ir.IntType(32), 0),
                       ir.Constant(ir.IntType(32), obj_idx)]
            return self.builder.gep(obj_addr, indices, inbounds=True)
        if isinstance(node, UnaryOp) and node.op == '*':
            # *ptr → ptr 指向的值（即 ptr 本身的地址 = ptr 的值）
            return self.visit(node.operand)
        elif isinstance(node, UnaryOp) and node.op == '&':
            # &x → x 的地址
            return self.get_address(node.operand)
        raise Exception

    def parse_string(self, node):
        """将字符串字面量转换为 LLVM 全局常量。

        功能:
        1. 创建以 null 结尾的 UTF-8 字符串常量
        2. 自动去重（相同内容的字符串只创建一份）
        3. 返回指向该常量的 GlobalVariable

        Args:
            node: String 字面量节点

        Returns:
            LLVM GlobalVariable（指向字符串常量的指针）
        """
        if node.value in self.strings:
            return self.strings[node.value]

        # 解码转义序列 (如 \\n → 换行符)
        unescaped_value = codecs.decode(node.value, 'unicode_escape')
        terminated_value = unescaped_value + '\0'

        # 创建字节数组常量
        str_arr = bytearray(terminated_value.encode('utf8'))
        str_type = ir.ArrayType(ir.IntType(8), len(str_arr))
        str_name = f".str.{len(self.strings)}"
        str_val = ir.GlobalVariable(self.module, str_type, name=str_name)
        str_val.initializer = ir.Constant(str_type, str_arr)
        str_val.global_constant = True  # 标记为只读
        str_val.linkage = 'private'     # 模块内部可见

        self.strings[node.value] = str_val
        return str_val

    def parse_constant(self, node):
        """将常量 AST 节点转换为 LLVM IR 常量值。

        Args:
            node: 字面量 / Initializer 节点

        Returns:
            LLVM IR Constant 对象
        """
        if isinstance(node, Integer):
            return ir.Constant(ir.IntType(32), int(node.value, 0))
        if isinstance(node, Decimal):
            return ir.Constant(ir.FloatType(), float(node.value))
        if isinstance(node, Character):
            char_val = codecs.decode(node.value, 'unicode_escape')
            return ir.Constant(ir.IntType(8), ord(char_val))
        if isinstance(node, Bool):
            return ir.Constant(ir.IntType(1), 1 if node.value else 0)
        if isinstance(node, NullPtr):
            return ir.Constant(ir.IntType(8).as_pointer(), None)
        if isinstance(node, String):
            return self.parse_string(node)
        if isinstance(node, Initializer):
            # 复合初始化 → 递归处理
            constants = [self.parse_constant(init) for init in node.inits]
            return constants
        raise Exception

    def parse_cast(self, value, tgt_type, signed=True):
        """生成类型转换 IR 指令。

        支持的类型转换:
        - float → int: fptosi / fptoui
        - int → float: sitofp / uitofp
        - int → int (不同宽度): sext / zext / trunc
        - pointer → pointer: bitcast
        - 其他 → int/pointer: bitcast

        Args:
            value:    要转换的 IR 值
            tgt_type: 目标类型
            signed:   是否使用有符号转换

        Returns:
            转换后的 IR 值
        """
        src_type = value.type
        if src_type == tgt_type:
            return value
        elif isinstance(src_type, ir.FloatType) and isinstance(tgt_type, ir.IntType):
            return self.builder.fptosi(value, tgt_type) if signed else self.builder.fptoui(value, tgt_type)
        elif isinstance(src_type, ir.IntType) and isinstance(tgt_type, ir.FloatType):
            return self.builder.sitofp(value, tgt_type) if signed else self.builder.uitofp(value, tgt_type)
        elif isinstance(src_type, ir.IntType) and isinstance(tgt_type, ir.IntType):
            if src_type.width < tgt_type.width:
                # 扩展: i8 → i32 (sext/zext)
                return self.builder.sext(value, tgt_type) if signed else self.builder.zext(value, tgt_type)
            else:
                # 截断: i32 → i8 (trunc)
                return self.builder.trunc(value, tgt_type)
        elif isinstance(src_type, ir.PointerType) and isinstance(tgt_type, ir.PointerType):
            return self.builder.bitcast(value, tgt_type)
        elif isinstance(tgt_type, (ir.IntType, ir.PointerType)):
            return self.builder.bitcast(value, tgt_type)
        return value

    def parse_binary(self, tree, left, right):
        """二元运算的 IR 生成。

        处理各种运算:
        - 数值运算: add/sub/mul/div (浮点时用 fadd/fsub/...)
        - 指针运算: gep (getelementptr)
        - 比较运算: icmp/fcmp
        - 逻辑运算: and/or

        Args:
            tree:  BinaryOp 节点
            left:  左操作数的 LLVM IR 值
            right: 右操作数的 LLVM IR 值

        Returns:
            运算结果的 LLVM IR 值
        """
        op = tree.op
        left_type, right_type = tree.left.ctype, tree.right.ctype

        # 混合运算的隐式转换: int + float → si2fp(int), 然后 fadd
        if isinstance(left_type, BasicType) and isinstance(right_type, BasicType):
            if left_type == FLOAT or right_type == FLOAT:
                if left_type == INT:
                    left = self.builder.sitofp(left, ir.FloatType())
                if right_type == INT:
                    right = self.builder.sitofp(right, ir.FloatType())

        # 指针运算
        if op == '+' and isinstance(left_type, PointerType) and right_type == INT:
            return self.builder.gep(left, [right], inbounds=False)
        if op == '+' and left_type == INT and isinstance(right_type, PointerType):
            return self.builder.gep(right, [left], inbounds=False)
        if op == '-' and isinstance(left_type, PointerType) and right_type == INT:
            neg_val = self.builder.neg(right)
            return self.builder.gep(left, [neg_val], inbounds=False)
        if op == '-' and isinstance(left_type, PointerType) and isinstance(right_type, PointerType):
            # ptr - ptr → 元素个数差 (i32)
            diff1 = self.builder.ptrtoint(left, ir.IntType(64))
            diff2 = self.builder.ptrtoint(right, ir.IntType(64))
            res_val = self.builder.sub(diff1, diff2)
            return self.builder.trunc(res_val, ir.IntType(32))

        # 数值运算
        is_float = isinstance(left.type, ir.FloatType)
        if op in ('+', '-'):
            return self.builder.fadd(left, right) if is_float else self.builder.add(left, right)
        if op == '*':
            return self.builder.fmul(left, right) if is_float else self.builder.mul(left, right)
        if op == '/':
            return self.builder.fdiv(left, right) if is_float else self.builder.sdiv(left, right)
        if op == '%':
            return self.builder.srem(left, right)

        # 比较运算
        if op in ('==', '!=', '<', '>', '<=', '>='):
            return self.builder.fcmp_ordered(op, left, right) if is_float else self.builder.icmp_signed(op, left, right)
        if op == '&&':
            return self.builder.and_(left, right)
        if op == '||':
            return self.builder.or_(left, right)

        raise Exception

    def parse_init(self, values, tgt_addr):
        """将初始化列表的值初始化到目标地址（数组/结构体的递归初始化）。

        Args:
            values:   LLVM IR 值列表（可能嵌套）
            tgt_addr: 目标内存地址
        """
        for i, item_val in enumerate(values):
            # 计算第 i 个元素/成员的地址
            indices = [ir.Constant(ir.IntType(32), 0),
                       ir.Constant(ir.IntType(32), i)]
            elem_addr = self.builder.gep(tgt_addr, indices, inbounds=True)

            if isinstance(item_val, list):
                # 嵌套初始化（如 {{1, 2}, {3, 4}}）
                self.parse_init(item_val, elem_addr)
            else:
                tgt_type = elem_addr.type.pointee

                # 处理特殊转换场景
                cond_decay = (isinstance(item_val, ir.GlobalVariable) and
                              isinstance(item_val.type.pointee, ir.ArrayType) and
                              isinstance(elem_addr.type.pointee, ir.PointerType))
                cond_null = (isinstance(item_val, ir.Constant) and
                             item_val.type.is_pointer and
                             str(item_val).endswith('null'))

                if cond_decay:
                    # 数组名退化为指针
                    zero = ir.Constant(ir.IntType(32), 0)
                    item_val = self.builder.gep(item_val, [zero, zero], inbounds=True)
                elif cond_null:
                    if isinstance(tgt_type, ir.PointerType):
                        item_val = ir.Constant(tgt_type, None)
                    else:
                        raise Exception
                elif item_val.type != tgt_type:
                    # 类型不匹配 → 插入转换指令
                    item_val = self.parse_cast(item_val, tgt_type)

                self.builder.store(item_val, elem_addr)

    # ===============  访问方法  ===============

    def program(self, tree: Program):
        """程序根节点 → 遍历所有声明"""
        self.visit(tree.decl)

    def declaration(self, tree):
        """声明块 → 逐个处理"""
        for decl in tree.decls:
            self.visit(decl)

    def func_def(self, tree):
        """函数定义 → 生成 LLVM IR 函数。

        工作流程:
        1. 从 Module 中获取或创建 LLVM Function 对象
        2. 创建基本块 entry，初始化 IRBuilder
        3. 为每个参数分配栈空间 (alloca)，处理传参
        4. 遍历函数体生成指令
        5. 如果函数体末尾未终止，补充 ret 或 unreachable
        """
        func_name = tree.decl.name.value

        # 获取或创建 LLVM Function
        if func_name in self.module.globals:
            self.curr_func = self.module.globals[func_name]
        else:
            func_type = self.get_type(tree.ctype)
            self.curr_func = ir.Function(self.module, func_type, name=func_name)
        tree.decl.name.symbol.value = self.curr_func

        # 创建入口基本块
        block = self.curr_func.append_basic_block(name="entry")
        self.builder = ir.IRBuilder(block)

        # 处理参数: alloca → store → 符号表记录地址
        for i, arg in enumerate(self.curr_func.args):
            param = tree.decl.suffix[0].params[i]
            arg.name = param.decl.name.value

            # 为参数分配本地变量空间
            param_addr = self.builder.alloca(arg.type, name=f"{arg.name}.addr")
            param.decl.name.symbol.value = param_addr
            self.builder.store(arg, param_addr)

        # 生成函数体 IR
        self.visit(tree.body)

        # 如果函数体没有正确终止，补充终止指令
        if not self.builder.block.is_terminated:
            if tree.ctype.type == VOID:
                self.builder.ret_void()
            else:
                self.builder.unreachable()

        self.curr_func = None

    def comp_def(self, tree):
        """结构体定义 → 已经在 get_type 中处理（惰性创建）"""
        pass

    def enum_def(self, tree):
        """枚举定义 → 在 identifier 中按常量处理"""
        pass

    def var_decl(self, tree):
        """变量声明 → 分配内存空间，处理初始化。

        局部变量: alloca + store
        全局变量: GlobalVariable + initializer
        """
        for decl in tree.decls:
            var_name = decl.name.value
            var_type = self.get_type(decl.ctype)

            if self.curr_func:
                # 局部变量 — 在当前函数栈帧上分配
                var_addr = self.builder.alloca(var_type, name=var_name)
                decl.name.symbol.value = var_addr

                if decl.init:
                    init_val = self.visit(decl.init)
                    if isinstance(decl.init, Initializer):
                        # 复合初始化 ({1, 2, 3})
                        self.parse_init(init_val, var_addr)
                    else:
                        # 简单初始化 + 类型转换
                        casted_val = self.parse_cast(init_val, var_type)
                        self.builder.store(casted_val, var_addr)
            else:
                # 全局变量
                var_val = ir.GlobalVariable(self.module, var_type, name=var_name)
                decl.name.symbol.value = var_val

                if decl.init:
                    const_val = self.parse_constant(decl.init)
                    if isinstance(const_val, list):
                        var_val.initializer = ir.Constant(var_type, const_val)
                    else:
                        var_val.initializer = const_val
                else:
                    var_val.initializer = ir.Constant(var_type, None)

    def arr_decl(self, tree):
        """数组声明 → 委托给 var_decl（数组类型已在 get_type 中处理）"""
        return self.var_decl(tree)

    def func_decl(self, tree: FunctionDeclaration):
        """前向函数声明 → 声明 LLVM Function（不定义函数体）"""
        for decl in tree.decls:
            func_name = decl.name.value
            if not self.module.globals.get(func_name):
                func_type = self.get_type(decl.ctype)
                self.curr_func = ir.Function(self.module, func_type, name=func_name)
                decl.name.symbol.value = self.curr_func

    def statement(self, tree):
        """复合语句 → 顺序生成子语句 IR"""
        for stmt in tree.stmts:
            self.visit(stmt)

    def if_stmt(self, tree: IfStatement):
        """if 语句 → 使用 LLVM 的 if_else / if_then 工具生成分支 IR。

        生成模式:
        ```
            %cond = icmp ne %val, 0
            br i1 %cond, label %then, label %else  (或 merge)
        then:
            ... then body ...
            br merge
        else:
            ... else body ...
            br merge
        merge:
            (继续)
        ```
        """
        cond_val = self.visit(tree.cond)

        # i1 以外的类型需要与 0 比较转换为 i1
        if cond_val.type != ir.IntType(1):
            cond_val = self.builder.icmp_signed('!=', cond_val,
                                                 ir.Constant(cond_val.type, 0))

        if tree.orelse:
            # if-else 双分支
            with self.builder.if_else(cond_val) as (then, orelse):
                with then:
                    self.visit(tree.then)
                with orelse:
                    self.visit(tree.orelse)
        else:
            # if 单分支
            with self.builder.if_then(cond_val):
                self.visit(tree.then)

    def while_stmt(self, tree):
        """while 循环 → 创建三个基本块: cond / body / end。

        LLVM IR 块结构:
        ```
            br while.cond
        while.cond:
            %cond = ...
            br i1 %cond, while.body, while.end
        while.body:
            ... body ...
            br while.cond
        while.end:
            (继续)
        ```
        """
        cond_block = self.curr_func.append_basic_block('while.cond')
        loop_block = self.curr_func.append_basic_block('while.body')
        end_block = self.curr_func.append_basic_block('while.end')

        # 记录循环信息供 break/continue 使用
        self.loop_stack.append((end_block, cond_block))
        self.builder.branch(cond_block)

        # 条件块
        self.builder.position_at_end(cond_block)
        cond_val = self.visit(tree.cond)
        if cond_val.type != ir.IntType(1):
            cond_val = self.builder.icmp_signed('!=', cond_val,
                                                 ir.Constant(cond_val.type, 0))
        self.builder.cbranch(cond_val, loop_block, end_block)

        # 循环体
        self.builder.position_at_end(loop_block)
        self.visit(tree.body)
        if not self.builder.block.is_terminated:
            self.builder.branch(cond_block)  # 跳回条件判断

        self.builder.position_at_end(end_block)
        self.loop_stack.pop()

    def for_stmt(self, tree):
        """for 循环 → 四个基本块: cond / body / post / end。

        LLVM IR 块结构:
        ```
            init (if any)
            br for.cond
        for.cond:
            cond → br body or br end
        for.body:
            ... body ...
            br for.post
        for.post:
            post (++i etc.)
            br for.cond
        for.end:
            (继续)
        ```
        """
        cond_block = self.curr_func.append_basic_block('for.cond')
        loop_block = self.curr_func.append_basic_block('for.body')
        post_block = self.curr_func.append_basic_block('for.post')
        end_block = self.curr_func.append_basic_block('for.end')

        self.loop_stack.append((end_block, post_block))

        # 初始化
        if tree.init:
            self.visit(tree.init)
        self.builder.branch(cond_block)

        # 条件判断
        self.builder.position_at_end(cond_block)
        if tree.cond:
            cond_val = self.visit(tree.cond)
            if cond_val.type != ir.IntType(1):
                cond_val = self.builder.icmp_signed('!=', cond_val,
                                                     ir.Constant(cond_val.type, 0))
            self.builder.cbranch(cond_val, loop_block, end_block)
        else:
            self.builder.branch(loop_block)

        # 循环体
        self.builder.position_at_end(loop_block)
        self.visit(tree.body)
        if not self.builder.block.is_terminated:
            self.builder.branch(post_block)

        # 后处理
        self.builder.position_at_end(post_block)
        if tree.post:
            self.visit(tree.post)
        if not self.builder.block.is_terminated:
            self.builder.branch(cond_block)

        self.builder.position_at_end(end_block)
        self.loop_stack.pop()

    def return_stmt(self, tree):
        """return 语句 → 发出 ret 或 ret_void 指令"""
        if tree.expr:
            return_val = self.visit(tree.expr)
            return_val = self.parse_cast(return_val, self.curr_func.return_value)
            self.builder.ret(return_val)
        else:
            self.builder.ret_void()

    def break_stmt(self, _):
        """break → 跳转到循环的 end 块"""
        break_target = self.loop_stack[-1][0]
        self.builder.branch(break_target)

    def continue_stmt(self, _):
        """continue → 跳转到循环的 cond/post 块"""
        continue_target = self.loop_stack[-1][1]
        self.builder.branch(continue_target)

    def empty_stmt(self, _):
        pass

    def expr_stmt(self, tree):
        """表达式语句 → 生成表达式 IR"""
        if tree.expr:
            self.visit(tree.expr)

    def expression(self, tree: Expression):
        """逗号表达式 → 顺序执行每个子表达式，返回最后一个的值"""
        expr_val = None
        for expr in tree.exprs:
            expr_val = self.visit(expr)
        return expr_val

    def assign_op(self, tree):
        """赋值表达式 → 计算右值，存储到左值地址。

        支持:
        - 普通赋值: x = expr
        - 复合赋值: x += expr → 先 load 旧值，计算新值，store 回去
        """
        left_addr = self.get_address(tree.left)
        right_val = self.visit(tree.right)

        if tree.op == '=':
            # 普通赋值
            res_val = self.parse_cast(right_val, left_addr.type.pointee)
            self.builder.store(res_val, left_addr)
            return res_val
        else:
            # 复合赋值: x += y → x = x + y
            op = tree.op[:-1]  # 去掉 '='
            left_val = self.builder.load(left_addr)

            # 构造虚拟节点用于 parse_binary
            fake_node = BinaryOp(op, None, None)
            fake_node.left = type("Fake", (), {"ctype": tree.left.ctype})()
            fake_node.right = type("Fake", (), {"ctype": tree.right.ctype})()

            res_val = self.parse_binary(fake_node, left_val, right_val)
            casted_val = self.parse_cast(res_val, left_addr.type.pointee)
            self.builder.store(casted_val, left_addr)
            return res_val

    def binary_op(self, tree):
        """二元运算 → 生成算术/比较/逻辑 IR 指令。

        特殊处理 && 和 ||：
        实现短路求值（short-circuit evaluation），
        对于 a && b，如果 a 为 false 就不计算 b；
        对于 a || b，如果 a 为 true 就不计算 b。
        """
        if tree.op in ('&&', '||'):
            # 短路求值实现
            is_and = (tree.op == '&&')

            # 求值左操作数
            left_cond = self.visit(tree.left)
            if left_cond.type != ir.IntType(1):
                left_cond = self.builder.icmp_ne(left_cond,
                                                  ir.Constant(left_cond.type, 0))

            # alloca 存放最终结果
            res_addr = self.builder.alloca(ir.IntType(1), name='logic.res')
            self.builder.store(left_cond, res_addr)

            next_block = self.curr_func.append_basic_block('logic.next')
            end_block = self.curr_func.append_basic_block('logic.end')

            # &&: 左为真 → 跳到 next 计算右操作数; 左为假 → 跳到 end
            # ||: 左为真 → 跳到 end（短路）; 左为假 → 跳到 next 计算右操作数
            self.builder.cbranch(left_cond,
                                 next_block if is_and else end_block,
                                 end_block if is_and else next_block)

            self.builder.position_at_end(next_block)
            right_cond = self.visit(tree.right)
            if right_cond.type != ir.IntType(1):
                right_cond = self.builder.icmp_ne(right_cond,
                                                  ir.Constant(right_cond.type, 0))
            self.builder.store(right_cond, res_addr)
            self.builder.branch(end_block)

            self.builder.position_at_end(end_block)
            return self.builder.load(res_addr)
        else:
            left_val = self.visit(tree.left)
            right_val = self.visit(tree.right)
            return self.parse_binary(tree, left_val, right_val)

    def unary_op(self, tree):
        """一元运算 → 生成相应 IR 指令。

        支持: +expr, -expr, !expr, &expr, *expr, ++expr, --expr
        """
        old_val = self.visit(tree.operand)
        if tree.op == '+':
            return old_val
        elif tree.op == '-':
            if isinstance(old_val.type, ir.FloatType):
                return self.builder.fneg(old_val)
            else:
                return self.builder.neg(old_val)
        elif tree.op == '!':
            zero = ir.Constant(old_val.type, 0)
            return self.builder.icmp_signed('==', old_val, zero)
        elif tree.op == '&':
            # 取地址
            return self.get_address(tree.operand)
        elif tree.op == '*':
            # 解引用
            return self.builder.load(old_val)
        elif tree.op in ('++', '--'):
            # 前置自增/自减
            operand_addr = self.get_address(tree.operand)
            old_val = self.builder.load(operand_addr)
            one = ir.Constant(old_val.type, 1)
            new_val = (self.builder.add(old_val, one)
                       if tree.op == '++'
                       else self.builder.sub(old_val, one))
            self.builder.store(new_val, operand_addr)
            return new_val  # 前置返回新值
        raise Exception

    def postfix_op(self, tree):
        """后置自增/自减 → 返回旧值，更新变量。

        LLVM IR: old = load x; new = add/sub old, 1; store new, x; use old
        注意返回的是旧值，区别于 unary_op（前置返回新值）。
        """
        operand_addr = self.get_address(tree.operand)
        old_val = self.builder.load(operand_addr)
        one = ir.Constant(old_val.type, 1)
        new_val = (self.builder.add(old_val, one)
                   if tree.op == '++'
                   else self.builder.sub(old_val, one))
        self.builder.store(new_val, operand_addr)
        return old_val  # 后置返回旧值

    def func_call(self, tree):
        """函数调用 → 生成 call 指令。

        printf/scanf 特殊处理:
        - printf 的 float 参数需扩展为 double
        - scanf 的参数需要传递地址（自动 bitcast 为 i8*）
        """
        func_name = tree.func.value
        if func_name in ("printf", "scanf"):
            func_val = self.module.globals.get(func_name)
            format_str_val = self.visit(tree.args[0])
            arg_vals = [format_str_val]

            for arg_node in tree.args[1:]:
                if func_name == 'printf':
                    arg_val = self.visit(arg_node)
                    # float → double (C 语言变参的默认提升)
                    if isinstance(arg_val.type, ir.FloatType):
                        arg_val = self.builder.fpext(arg_val, ir.DoubleType())
                    arg_vals.append(arg_val)
                elif func_name == 'scanf':
                    arg_addr = self.get_address(arg_node)
                    # 统一转为 i8*（scanf 的参数类型）
                    casted_addr = self.builder.bitcast(arg_addr,
                                                       ir.IntType(8).as_pointer())
                    arg_vals.append(casted_addr)
            return self.builder.call(func_val, arg_vals)
        else:
            # 普通函数调用
            func_val = self.visit(tree.func)
            arg_vals = []
            for i, arg_node in enumerate(tree.args):
                arg_type = func_val.type.pointee.args[i]
                arg_val = self.visit(arg_node)
                # 隐式类型转换
                arg_val = self.parse_cast(arg_val, arg_type)
                arg_vals.append(arg_val)
            return self.builder.call(func_val, arg_vals)

    def array_access(self, tree):
        """数组元素访问 → 获取地址后 load 值"""
        elem_addr = self.get_address(tree)
        return self.builder.load(elem_addr)

    def member_access(self, tree):
        """结构体成员访问 → 获取地址后 load 值。

        特殊处理: 联合体成员访问需要 bitcast 到目标类型。
        """
        member_addr = self.get_address(tree)
        if isinstance(tree.object.ctype, CompoundType) and tree.object.ctype.union:
            # 联合体 → 按目标成员类型重新解释内存
            target_ptr_type = self.get_type(tree.ctype).as_pointer()
            member_addr = self.builder.bitcast(member_addr, target_ptr_type)
        return self.builder.load(member_addr)

    def identifier(self, tree):
        """标识符 → 返回符号表中存储的值。

        函数/数组名: 直接返回指针（不 load）
        enum 常量: 返回 i32 常量
        普通变量: load 返回当前值
        """
        symbol = tree.symbol
        if isinstance(symbol.type, (FunctionType, ArrayType)):
            # 函数名/数组名 → 直接使用其指针
            return symbol.value
        if symbol.kind == SymbolKind.CONST and isinstance(symbol.type, EnumType):
            # 枚举常量 → i32 字面量
            val = symbol.type.enumerators[symbol.name]
            return ir.Constant(ir.IntType(32), val)
        # 普通变量 → load
        return self.builder.load(symbol.value)

    # ===============  字面量 → LLVM IR 常量  ===============

    @staticmethod
    def integer(tree):
        """整数字面量 → i32 常量"""
        return ir.Constant(ir.IntType(32), int(tree.value, 0))

    @staticmethod
    def decimal(tree):
        """浮点字面量 → float 常量"""
        return ir.Constant(ir.FloatType(), float(tree.value))

    @staticmethod
    def character(tree):
        """字符字面量 → i8 常量 (ASCII 码)"""
        char_val = codecs.decode(tree.value, 'unicode_escape')
        return ir.Constant(ir.IntType(8), ord(char_val))

    def string(self, tree):
        """字符串字面量 → 退化为 i8* 指针返回"""
        str_val = self.parse_string(tree)
        zero = ir.Constant(ir.IntType(32), 0)
        return self.builder.gep(str_val, [zero, zero],
                                inbounds=True, name=f"{str_val.name}.decay")

    @staticmethod
    def bool(tree):
        """布尔字面量 → i1 常量"""
        return ir.Constant(ir.IntType(1), tree.value)

    @staticmethod
    def nullptr(_):
        """nullptr → i8* null 常量"""
        return ir.Constant(ir.IntType(8).as_pointer(), None)

    def initializer(self, tree):
        """初始化列表 → 递归求值"""
        return [self.visit(init) for init in tree.inits]

    def __default__(self, tree):
        raise Exception
