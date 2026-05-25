"""
目标代码生成模块 — 编译流水线的第五趟扫描。

功能: 将 LLVM IR 转换为 x86 汇编，再汇编/链接为可执行文件。

本模块不直接生成 x86 汇编，而是委托给 LLVM 工具链:
1. ir_to_x86(): LLVM IR (.ll) → x86 汇编 (.s) — 调用 clang -S
2. x86_to_exe(): x86 汇编 (.s) → 可执行文件 — 调用 clang 汇编+链接

技术说明:
- clang 是 LLVM 的 C 编译器前端，但其 -S 参数同样可以处理 .ll 文件
- clang 内部调用了 LLVM 的代码生成器和汇编器
- 因此本模块本质上是 LLVM 后端的封装层
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from compiler.utils import is_file


def ir_to_x86(ir, file_path='.', file_name='output'):
    """将 LLVM IR 编译为 x86 汇编文件。

    工作流程:
    1. 如果 ir 是字符串 → 写入临时 .ll 文件
    2. 执行 clang -S input.ll -o output.s
    3. 清理临时文件
    4. 返回输出 .s 文件路径

    Args:
        ir:        LLVM IR 文本字符串或 .ll 文件路径
        file_path: 输出目录
        file_name: 输出文件名（不含扩展名）

    Returns:
        .s 汇编文件路径
    """
    temp_file_name = None
    # 如果传入的是 IR 字符串而非文件路径 → 写入临时文件
    if not is_file(ir):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ll',
                                         delete=False, encoding='utf-8') as temp_file:
            temp_file.write(ir)
            ir = temp_file.name
            temp_file_name = temp_file.name

    output_path = Path(file_path) / (file_name + '.s')
    # clang -S: 只编译到汇编，不链接
    # -o output.s: 指定输出文件
    command = ['clang', '-S', ir, '-o', output_path]
    subprocess.run(command, check=True)

    # 清理临时 IR 文件
    if not is_file(ir) and os.path.exists(temp_file_name):
        os.remove(temp_file_name)
    return output_path


def x86_to_exe(x86, file_path='.', file_name='output'):
    """将 x86 汇编文件编译为可执行文件。

    工作流程:
    1. 如果 x86 是字符串 → 写入临时 .s 文件
    2. 执行 clang input.s -o output
    3. 清理临时文件
    4. 返回可执行文件路径

    Args:
        x86:       x86 汇编文本字符串或 .s 文件路径
        file_path: 输出目录
        file_name: 输出文件名（Windows 自动加 .exe 后缀）

    Returns:
        可执行文件路径
    """
    temp_file_name = None
    # 如果传入的是汇编字符串而非文件路径 → 写入临时文件
    if not is_file(x86):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.s',
                                         delete=False, encoding='utf-8') as temp_file:
            temp_file.write(x86)
            x86 = temp_file.name
            temp_file_name = temp_file.name

    # Windows 上可执行文件需要 .exe 后缀
    suffix = '.exe' if sys.platform.startswith('win') else ''
    output_path = Path(file_path) / (file_name + suffix)
    command = ['clang', x86, '-o', output_path]

    # Windows MSVC 兼容：需要显式链接运行时库
    # macOS/Linux 下 clang 会自动处理，不需传这些参数
    if sys.platform.startswith('win'):
        command += [
            '-Xlinker', '/defaultlib:libcmt',         # C 运行时库
            '-Xlinker', '/defaultlib:oldnames',
            '-Xlinker', '/defaultlib:libucrt',        # 通用 C 运行时库
            '-Xlinker', '/defaultlib:libvcruntime',   # VC 运行时库
            '-Xlinker', '/defaultlib:legacy_stdio_definitions',
        ]
    subprocess.run(command, check=True)

    # 清理临时汇编文件
    if not is_file(x86) and os.path.exists(temp_file_name):
        os.remove(temp_file_name)
    return output_path
