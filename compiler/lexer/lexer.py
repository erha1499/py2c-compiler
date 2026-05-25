"""
词法分析器模块 — 编译流水线的第一趟扫描。

功能: 将源码字符串(字符流)切分成 token 序列。
例如: int x = 5; → [TYPE('int'), IDENT('x'), OP('='), INTEGER(5), SEMICOLON(';')]

技术实现: 基于 Lark 库的 basic 词法引擎，
词法规则定义在同目录下的 lexicon.lark 文件中。
"""

from pathlib import Path

from lark import Lark, UnexpectedCharacters
from tabulate import tabulate

from compiler.error import LexicalError
from compiler.utils import read_file, write_file


class Lexer:
    """词法分析器：将源代码字符串分割为 token 序列。

    lexer.lex(code) 是入口方法，返回 token 列表供后续 parser 使用。
    """

    def __init__(self, file_path='lexicon.lark'):
        """初始化 Lark 词法引擎。

        Args:
            file_path: 词法规则文件，默认同目录下的 lexicon.lark
                       该文件定义了 C 语言的关键字、操作符、字面量等词法规则
        """
        self.file_path = Path(__file__).parent / file_path
        # 读取词法规则文件内容
        self.lexicon = read_file(self.file_path)
        # 创建 Lark 词法分析器，lexer='basic' 表示使用基础词法引擎
        # basic 模式采用正则匹配，速度快但功能简单，适合教学用
        self.lexer = Lark(self.lexicon, lexer='basic')
        # 保存最近一次词法分析的结果
        self.tokens = None

    def lex(self, code):
        """对源代码字符串执行词法分析。

        工作流程:
        1. 调用 Lark 引擎的 lex() 方法对 code 进行扫描
        2. Lark 根据 lexicon.lark 中的正则规则，识别出每个 token 的类型和值
        3. 返回 Token 对象列表，每个 Token 包含 type、value、line、column 信息

        Args:
            code: 源代码字符串

        Returns:
            token 列表，每个 token 是一个 Lark Token 对象

        Raises:
            LexicalError: 当遇到无法识别的字符时抛出
        """
        try:
            # Lark 的 lex() 返回一个生成器，转为 list 保存
            self.tokens = list(self.lexer.lex(code))
        except UnexpectedCharacters as e:
            # 例如源码中出现了 lexicon.lark 未定义的符号（如中文标点）
            raise LexicalError('无法识别的字符', e.line, e.column)
        return self.tokens

    @staticmethod
    def tabular(tokens):
        """将 token 列表格式化为表格字符串（用于人类阅读和调试）。

        输出格式:
        ┌──────────┬───────┬──────┬────────┐
        │   Type   │ Value │ Line │ Column │
        ├──────────┼───────┼──────┼────────┤
        │   TYPE   │  int  │  1   │   1    │
        └──────────┴───────┴──────┴────────┘

        Args:
            tokens: token 列表

        Returns:
            格式化的表格字符串
        """
        rows = []
        for token in tokens:
            rows.append([token.type, token.value, token.line, token.column])
        table = tabulate(rows, ["Type", "Value", "Line", "Column"],
                         "simple_grid", numalign="center", stralign="center")
        return table

    def save(self, file_path=''):
        """将最近一次词法分析结果保存为 01 tokens.txt 文件。

        Args:
            file_path: 输出目录路径
        """
        write_file(self.tabular(self.tokens), Path(file_path) / '01 tokens.txt')
