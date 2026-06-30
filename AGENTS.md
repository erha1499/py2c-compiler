# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## 项目概述

AnanasCC 是广西大学 2026 编译原理课程设计项目，用 Python 实现的 C 语言子集编译器。将 C 源码编译为目标平台的 x86 汇编代码并链接为可执行文件。

## 常用命令

```bash
# 安装依赖 (需系统安装 clang)
pip install -r requirements.txt

# CLI 编译并执行
python -m compiler your_file.c -e

# 快速入口 —— 编译 main.c + 执行 + 保存所有中间产物到 temp/
python main.py

# 打包为独立可执行文件
pyinstaller --onefile --name AnanasCC \
    --add-data "compiler/lexer/lexicon.lark;compiler/lexer" \
    --add-data "compiler/parser/syntax.lark;compiler/parser" \
    compiler/__main__.py
```

## 编译流水线（五阶段分离架构）

```
main.c  →  [1] Lexer.lex()       → Token 流
       →  [2] Parser.parse()     → AST (Program 根节点)
       →  [3] Analyzer.analyze() → AST (带 ctype/symbol 类型标注)
       →  [4] Annotator.annotate() → AST (带 TAC 三地址码 + 回填信息)
       →  [5] Generator.generate() → LLVM IR
       →  [6] Optimizer.optimize()  → 优化后 LLVM IR
       →  [7] clang -S              → x86 汇编 (.s)
       →  [8] clang 链接            → 可执行文件
```

各阶段通过 `Compiler.compiler.py` 串联，`save()` 方法在末尾保存全部中间产物并调用 `draw/` 绘制语法树图片。

## 两条并行中间表示

TAC（Annotator）和 LLVM IR（Generator）是两条并行的中间代码：

- **TAC**: 用于教材展示。由 `Annotator` (Lark Interpreter) 遍历 AST 生成，实现教科书式回填算法（`truelist`/`falselist`/`nextlist`），存储在节点 `attrs` 字典中，通过 `pretty_annotated()` 格式化为 `05 ast_comment.txt`
- **LLVM IR**: 用于实际编译。由 `Generator` (Lark Interpreter) 遍历 AST 生成，读取 Analyzer 填充的 `ctype` 和 `symbol`（不读 Annotator 的 TAC），生成 llvmlite IR Module

两条线独立，互不依赖。修改 TAC 展示逻辑不会影响 IR 生成，反之亦然。

## 核心架构要点

**AST 节点**（`compiler/tree/tree.py`）：`ASTNode(Tree)` 继承 `lark.Tree`，关键属性：
- `ctype` — C 类型 (Type 实例，由 Analyzer 填充)
- `symbol` — 符号表引用 (Symbol 实例)
- `index` — 结构体成员偏移索引
- `attrs: dict` — 统一属性字典，存储 `rule`/`derived_from`/`value`/`code`/`backpatch`/`truelist`/`falselist`/`nextlist`/`labels`，由 Analyzer 和 Annotator 填充

**符号表**（`compiler/semantic/symbol.py`）：`SymbolTable` 使用嵌套作用域栈（`list[dict]`），`enter_scope()`/`leave_scope()` 管理作用域，`lookup()` 从内向外查找。

**类型系统**（`compiler/semantic/type.py`）：类层次 `BasicType` / `PointerType` / `ArrayType` / `FunctionType` / `CompoundType` / `EnumType`。预定义单例 `VOID, INT, FLOAT, CHAR, BOOL, NULL`。

**Annotator 的回填机制**：`_emit()` 返回 `code_buffer` 中的指令索引（从 1 开始，buffer[0] 是 `func name:` 头）。`backpatch(instr_ids, label)` 将指定指令中的 `goto _` 占位符替换为实际标签名。

## 关键注意事项

1. **目标三元组硬编码**：`generator.py` 中 `module.triple = 'x86_64-pc-windows-msvc...'`，实际运行在 macOS 上依赖 clang 自适应，这是已知限制
2. **CompileError 不中断编译**：`compiler.compile()` 中语义错误抛出后只打印，后续阶段会在不一致的 AST 上继续运行
3. **`postfix_expr` 在 `transformer.py` 中被定义了两次**（第 273 行和第 300 行），第二个定义覆盖第一个，修改时需注意
4. **`draw/` 包的绘图在 `save()` 尾部顺序执行**，每轮绘图后调用 `plt.close('all')` 释放资源。使用 `matplotlib.use('Agg')` 非交互后端
5. **`output/` 和 `temp/` 路径在多个模块中硬编码**，重构时需统一
