"""
编译器主模块 — 编排五趟扫描流水线。

流水线结构:
    Lexer (词法分析) → Parser (语法分析) → Analyzer (语义分析)
    → Annotator (TAC 生成 + 回填) → Generator (IR 生成) → Optimizer (IR 优化)
    → x86 汇编 → 可执行文件

整个过程以 main.py 为入口，Compiler 对象串联所有阶段。
"""

import os
import subprocess
from pathlib import Path

import matplotlib.pyplot as plt

from compiler.error import CompileError
from compiler.ir import Generator
from compiler.ir import Optimizer
from compiler.lexer import Lexer
from compiler.parser import Parser
from compiler.semantic import Analyzer, Annotator
from compiler.tree.tree import pretty_annotated
from compiler.utils import read_file, write_file
from compiler.x86 import ir_to_x86, x86_to_exe
from draw import draw_raw_ast, draw_annotated_ast


class Compiler:
    """编译器主类：串联词法、语法、语义、IR、x86 五个阶段。"""

    def __init__(self, work_dir):
        """初始化所有阶段的处理器。

        Args:
            work_dir: 工作目录，所有中间文件和输出文件都输出到此目录下
        """
        os.makedirs(work_dir, exist_ok=True)
        self.work_dir = work_dir

        # ---------- 编译流水线的各个阶段 ----------
        # 第一趟：词法分析 — 将源码字符串切分成 token 序列
        self.lexer = Lexer()
        # 第二趟：语法分析 — 将 token 序列解析成 CST，再转换为 AST
        self.parser = Parser()
        # 第三趟前半：语义分析 — 类型检查、符号表构建、作用域分析
        self.analyzer = Analyzer()
        # 第三趟后半：TAC 注释器 — 生成三地址码 + 回填（在 AST 节点上打注释）
        self.annotator = Annotator()
        # 第四趟：IR 生成 — 将带注释的 AST 转换为 LLVM IR
        self.generator = Generator()
        # 第四趟后半：IR 优化 — 在 LLVM IR 层面做优化（常量折叠等）
        self.optimizer = Optimizer()

    def compile(self, file_path, execute=False):
        """执行完整的编译流水线。

        Args:
            file_path: 源文件路径（如 main.c）
            execute: 是否在编译成功后直接运行生成的可执行文件
        """
        # 步骤 1: 读取源文件
        code = read_file(file_path)

        try:
            # 步骤 2: 词法分析 — 源码字符串 → token 序列
            tokens = self.lexer.lex(code)

            # 步骤 3: 语法分析 — token 序列 → CST → AST
            tree = self.parser.parse(tokens)

            # 步骤 4: 语义分析 — 类型检查、符号表、作用域
            tree = self.analyzer.analyze(tree)

            # 步骤 5: TAC 注释 — 在 AST 节点上生成三地址码 + 回填信息
            tree = self.annotator.annotate(tree)

            # 保存带注释的 AST，后续 save() 方法会输出到文件
            self.annotated_ast = tree
        except CompileError as e:
            # 编译期错误（语法错误、类型错误等）直接打印，不继续后续阶段
            print(e)

        # 步骤 6: 将带注释的 AST 转换为 LLVM IR（中间表示）
        ir = self.generator.generate(tree)

        # 步骤 7: 优化 LLVM IR（常量折叠、死代码删除等）
        ir = self.optimizer.optimize(ir)

        # 步骤 8: LLVM IR → x86 汇编
        x86 = ir_to_x86(ir, self.work_dir)

        # 步骤 9: x86 汇编 → 可执行文件（调用 clang 汇编 + 链接）
        exe = x86_to_exe(x86, self.work_dir)

        # 步骤 10: 可选 — 执行编译产物
        if execute:
            result = subprocess.run(exe)
            # 兼容有符号和无符号返回码
            exit_code = (result.returncode
                         if result.returncode <= (2 ** 31 - 1)
                         else result.returncode - 2 ** 32)
            print(f'\n进程已结束，退出代码为 {exit_code}')

    def save(self, file_path=''):
        """保存所有中间产物到工作目录。

        包括：
        - 词法分析结果
        - 语法树
        - TAC 三地址码
        - LLVM IR
        - 带注释的语法树（文本 + 图片）

        Args:
            file_path: 输出目录路径
        """
        self.lexer.save(file_path)
        self.parser.save(file_path)
        self.annotator.save(file_path)
        self.generator.save(file_path)
        self.optimizer.save(file_path)
        self._save_annotated_ast(file_path)
        self._draw_trees(file_path)

    def _save_annotated_ast(self, file_path):
        """输出语义分析后的带注释语法树到 05 ast_comment.txt。

        这个文件展示了每个 AST 节点上的类型、TAC 代码、回填信息等注释，
        是理解编译器内部行为的重要文档。
        """
        if not hasattr(self, 'annotated_ast') or self.annotated_ast is None:
            return
        text = pretty_annotated(self.annotated_ast)
        write_file(text, Path(file_path) / '05 ast_comment.txt')

    @staticmethod
    def _draw_trees(file_path):
        """顺序绘制原始语法树和带注释语法树的图片。

        采用顺序执行而非并行，避免 matplotlib 的线程安全问题。
        每次绘图后调用 plt.close('all') 释放资源，防止内存泄漏。
        """
        path = Path(file_path)
        ast_src = str(path / '03 ast.txt')
        ast_dst = str(path / '03 ast.jpg')
        comment_src = str(path / '05 ast_comment.txt')
        comment_dst = str(path / '05 ast_comment.jpg')

        draw_raw_ast(ast_src, ast_dst)
        plt.close('all')  # 释放上一轮绘图资源

        draw_annotated_ast(comment_src, comment_dst)
        plt.close('all')  # 释放全部 matplotlib 资源
