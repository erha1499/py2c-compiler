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
    def __init__(self, work_dir):
        os.makedirs(work_dir, exist_ok=True)
        self.work_dir = work_dir

        self.lexer = Lexer()
        self.parser = Parser()
        self.analyzer = Analyzer()
        self.annotator = Annotator()
        self.generator = Generator()
        self.optimizer = Optimizer()

    def compile(self, file_path, execute=False):
        code = read_file(file_path)

        try:
            tokens = self.lexer.lex(code)
            tree = self.parser.parse(tokens)
            tree = self.analyzer.analyze(tree)
            tree = self.annotator.annotate(tree)       # TAC 代码生成 + 回填
            self.annotated_ast = tree  # 保存语义分析后的带注释 AST
        except CompileError as e:
            print(e)

        ir = self.generator.generate(tree)
        ir = self.optimizer.optimize(ir)

        x86 = ir_to_x86(ir, self.work_dir)
        exe = x86_to_exe(x86, self.work_dir)

        if execute:
            result = subprocess.run(exe)
            exit_code = result.returncode if result.returncode <= (2**31 - 1) else result.returncode - 2**32
            print(f'\n进程已结束，退出代码为 {exit_code}')

    def save(self, file_path=''):
        self.lexer.save(file_path)
        self.parser.save(file_path)
        self.generator.save(file_path)
        self.optimizer.save(file_path)
        self._save_annotated_ast(file_path)
        self._draw_trees(file_path)

    def _save_annotated_ast(self, file_path):
        """输出语义分析后的带注释语法树到 05 ast_comment.txt。"""
        if not hasattr(self, 'annotated_ast') or self.annotated_ast is None:
            return
        text = pretty_annotated(self.annotated_ast)
        write_file(text, Path(file_path) / '05 ast_comment.txt')

    @staticmethod
    def _draw_trees(file_path):
        """顺序绘制原始语法树和带注释语法树图片。

        顺序执行并在每次绘图后释放 matplotlib 资源，避免并发绘图冲突。
        """
        path = Path(file_path)
        ast_src = str(path / '03 ast.txt')
        ast_dst = str(path / '03 ast.jpg')
        comment_src = str(path / '05 ast_comment.txt')
        comment_dst = str(path / '05 ast_comment.jpg')

        draw_raw_ast(ast_src, ast_dst)
        plt.close('all')                       # 释放上一轮绘图资源

        draw_annotated_ast(comment_src, comment_dst)
        plt.close('all')                       # 释放全部 matplotlib 资源
