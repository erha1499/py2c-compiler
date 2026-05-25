"""
语法分析器模块 — 编译流水线的第二趟扫描。

功能: 将 token 序列转换为抽象语法树 (AST)。
工作流程: token 流 → LALR(1) 解析 → CST (具体语法树) → ASTTransformer 转换 → AST (抽象语法树)

技术实现: 基于 Lark 库的 LALR(1) 解析引擎，
语法规则定义在同目录下的 syntax.lark 文件中。
"""

from pathlib import Path

from lark import Lark, UnexpectedToken
from tabulate import tabulate

from compiler.error import SyntaxError
from compiler.tree import ASTTransformer
from compiler.utils import read_file, write_file


class Parser:
    """语法分析器：将 token 序列解析为抽象语法树。

    parse(tokens) 是入口方法，返回 AST 根节点 (Program)。
    内部经历两步转换:
    1. token → CST (Lark 内部的具体语法树)
    2. CST → AST (通过 ASTTransformer 转换为自定义节点)
    """

    def __init__(self, file_path='syntax.lark'):
        """初始化 Lark LALR(1) 语法分析器。

        Args:
            file_path: 语法规则文件，默认同目录下的 syntax.lark
                       该文件定义了 C 语言的 BNF 产生式规则
        """
        self.file_path = Path(__file__).parent / file_path
        # 读取语法规则文件
        self.syntax = read_file(self.file_path)
        # 创建 Lark 语法分析器
        # parser='lalr' — 使用 LALR(1) 算法，自底向上分析，自动构建 LR 分析表
        # propagate_positions=True — 在 AST 节点上保留行号/列号信息
        self.parser = Lark(self.syntax, parser='lalr', propagate_positions=True)
        # 保存最近一次解析的结果
        self.cst = None  # 具体语法树 (Concrete Syntax Tree)
        self.ast = None  # 抽象语法树 (Abstract Syntax Tree)

    def parse(self, tokens):
        """对 token 序列执行语法分析，生成抽象语法树。

        使用交互式解析 (interactive parsing) 逐 token 喂入解析器，
        这样做的好处是可以支持实时的错误报告。

        工作流程:
        1. 创建交互式解析器实例
        2. 逐个 token 喂入 feed_token()
        3. 最后调用 feed_eof() 结束输入，获得 CST
        4. 用 ASTTransformer 将 CST 转换为 AST

        Args:
            tokens: 词法分析产生的 token 列表

        Returns:
            AST 根节点 (Program 对象)

        Raises:
            SyntaxError: 当 token 序列不符合语法规则时抛出
        """
        # 交互式解析 — 逐 token 输入
        ip = self.parser.parse_interactive()

        try:
            for token in tokens:
                ip.feed_token(token)
            # 输入结束，Lark 返回完整的 CST
            self.cst = ip.feed_eof(tokens[-1])
        except UnexpectedToken as e:
            # 例如 if 后面缺括号、表达式不完整等语法错误
            raise SyntaxError('无法识别的单词', e.line, e.column)

        # 将 Lark 原生的 Tree 对象转换为自定义的 ASTNode 子类
        # 这一步完成了"具体语法树 → 抽象语法树"的转换
        transformer = ASTTransformer()
        self.ast = transformer.transform(self.cst)
        return self.ast

    @property
    def table(self):
        """获取 LALR 分析表的内部数据结构。

        分析表包含两个子表:
        - Action 表: 给定状态和输入符号，决定 SHIFT（移进）还是 REDUCE（归约）
        - Goto 表: 归约后状态跳转的目标

        这个表是 LALR 分析器的核心，parser 依据它来做决策。
        """
        # Lark 内部的结构是: parser.parser.parser._parse_table
        inner_parser = self.parser.parser.parser
        inner_table = getattr(inner_parser, '_parse_table', None)
        return inner_table

    @staticmethod
    def tabular(table):
        """将 LALR 分析表格式化为可读的表格字符串。

        生成两张表:
        - Action 表: 状态 x 终结符 → SHIFT/REDUCE 动作
        - Goto 表: 状态 x 非终结符 → 跳转目标状态

        Args:
            table: Lark 内部的 _parse_table 对象

        Returns:
            (action_table, goto_table) 两个格式化字符串
        """
        # 构建 Action 表
        action_rows = []
        for state, actions in table.states.items():
            for token, (action, arg) in actions.items():
                if getattr(action, '__name__', str(action)) == "Shift":
                    action_rows.append([state, token, "SHIFT", arg])
                elif getattr(action, '__name__', str(action)) == "Reduce":
                    action_rows.append([state, token, "REDUCE",
                                        getattr(arg, 'origin', arg)])
                else:
                    action_rows.append([state, token, str(action), arg])
        action_table = tabulate(action_rows,
                                headers=["State", "Token", "Action", "Arg"],
                                tablefmt="simple_grid",
                                numalign="center",
                                stralign="center")

        # 构建 Goto 表（非终结符的 SHIFT 实际上就是 Goto 动作）
        goto_rows = []
        for state, actions in table.states.items():
            for token, (action, arg) in actions.items():
                # Goto 的标记: action 是 Shift + token 是非终结符（大写）
                if getattr(action, '__name__', str(action)) == "Shift" and not token.islower():
                    goto_rows.append([state, token, "GOTO", arg])
        goto_table = tabulate(goto_rows,
                              headers=["State", "Token", "Action", "Arg"],
                              tablefmt="simple_grid",
                              numalign="center",
                              stralign="center")
        return action_table, goto_table

    def save(self, file_path=''):
        """保存语法分析阶段的所有中间产物。

        输出文件:
        - 02 action_table.txt: LALR Action 分析表
        - 02 goto_table.txt: LALR Goto 分析表
        - 03 cst.txt: 具体语法树（Lark 原始格式）
        - 03 ast.txt: 抽象语法树（自定义节点格式）

        Args:
            file_path: 输出目录路径
        """
        # 保存分析表
        action_table, goto_table = self.tabular(self.table)
        write_file(action_table, Path(file_path) / '02 action_table.txt')
        write_file(goto_table, Path(file_path) / '02 goto_table.txt')

        # 保存语法树
        write_file(self.cst.pretty(), Path(file_path) / '03 cst.txt')
        write_file(self.ast.pretty(), Path(file_path) / '03 ast.txt')
