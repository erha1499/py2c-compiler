"""原始语法树图形绘制模块。"""
import os
import re

import matplotlib
matplotlib.use('Agg')  # 非交互后端，支持多线程
import matplotlib.pyplot as plt

AST_NAME = "03 ast"
AST_PATH = "temp/" + AST_NAME + ".txt"
OUT_PATH = "temp/" + AST_NAME + ".jpg"

FONT_SIZE = 20
Y_GAP = 1.8

CHAR_WIDTH = 0.1
BOX_PADDING = 0.5
SIBLING_GAP = 0.5


class Node:
    def __init__(self, text):
        self.text = text
        self.children = []
        self.x = 0
        self.y = 0
        self.subtree_width = 0


def split_node_text(text):
    parts = re.split(r"\s+", text.strip(), maxsplit=1)
    if len(parts) == 1:
        return [parts[0]]
    left, right = parts[0], parts[1].strip()
    if not right:
        return [left]
    return [left, right]


def parse_ast(file_path):
    root = None
    stack = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            indent = len(line) - len(line.lstrip(" "))
            level = indent // 2
            raw_text = line.strip().replace("\t", " ")
            texts = split_node_text(raw_text)
            first_node = Node(texts[0])
            if level == 0:
                root = first_node
                stack = [first_node]
            else:
                while len(stack) > level:
                    stack.pop()
                parent = stack[-1]
                parent.children.append(first_node)
                if len(stack) == level:
                    stack.append(first_node)
                else:
                    stack[level] = first_node
            if len(texts) == 2:
                child_node = Node(texts[1])
                first_node.children.append(child_node)
                if len(stack) <= level + 1:
                    stack.append(child_node)
                else:
                    stack[level + 1] = child_node
    return root


def estimate_node_width(node):
    return len(node.text) * CHAR_WIDTH + BOX_PADDING


def compute_subtree_width(node):
    own_width = estimate_node_width(node)
    if not node.children:
        node.subtree_width = own_width
        return node.subtree_width
    children_width = sum(compute_subtree_width(c) for c in node.children)
    children_width += SIBLING_GAP * (len(node.children) - 1)
    node.subtree_width = max(own_width, children_width)
    return node.subtree_width


def assign_position(node, left, depth):
    node.y = -depth * Y_GAP
    node.x = left + node.subtree_width / 2
    if not node.children:
        return
    total_children_width = sum(child.subtree_width for child in node.children)
    total_gap_width = SIBLING_GAP * (len(node.children) - 1)
    children_block_width = total_children_width + total_gap_width
    child_left = node.x - children_block_width / 2
    for child in node.children:
        assign_position(child, child_left, depth + 1)
        child_left += child.subtree_width + SIBLING_GAP


def compute_layout(root):
    compute_subtree_width(root)
    assign_position(root, 0, 0)


def render_tree(root, out_path):
    fig_width = max(20, root.subtree_width * 1.2)
    fig_height = 20
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))

    def dfs(node):
        for child in node.children:
            ax.plot([node.x, child.x], [node.y, child.y],
                    color="black", linewidth=0.8, zorder=1)
            dfs(child)
        ax.text(node.x, node.y, node.text,
                ha="center", va="center", fontsize=FONT_SIZE, zorder=2,
                bbox=dict(boxstyle="round,pad=0.25",
                          facecolor="white", edgecolor="black"))

    dfs(root)
    ax.axis("off")
    plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    plt.savefig(out_path, dpi=250, bbox_inches="tight")
    plt.close()


def draw_raw_ast(input_path=None, output_path=None):
    """绘制原始语法树图片。

    Args:
        input_path: 语法树文本文件路径，默认 temp/03 ast.txt
        output_path: 输出图片路径，默认 temp/03 ast.jpg
    """
    src = input_path or AST_PATH
    dst = output_path or OUT_PATH
    root = parse_ast(src)
    if root is None:
        print("语法树文件为空")
        return
    compute_layout(root)
    render_tree(root, dst)
    print(f"语法树图片已生成：{dst}")


# ---- 直接执行入口 ----
def main():
    draw_raw_ast()


if __name__ == "__main__":
    main()
