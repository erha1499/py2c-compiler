"""带注释语法树图形绘制模块。"""
import os
import re

import matplotlib
matplotlib.use('Agg')  # 非交互后端，支持多线程
import matplotlib.pyplot as plt

AST_NAME = "05 ast_comment"
AST_PATH = "temp/" + AST_NAME + ".txt"
OUT_PATH = "temp/" + AST_NAME + ".jpg"

FONT_SIZE = 8
Y_GAP = 2.8

CHAR_WIDTH = 0.048
BOX_PADDING = 0.3
SIBLING_GAP = 0.6


def _is_annotation_line(stripped_line):
    return stripped_line.lstrip(' ').startswith('.')


class Node:
    def __init__(self, text):
        self.text = text
        self.annotations = []
        self.children = []
        self.x = 0
        self.y = 0
        self.subtree_width = 0

    def display_text(self):
        if not self.annotations:
            return self.text
        return self.text + '\n' + '\n'.join(self.annotations)

    def display_lines(self):
        return [self.text] + self.annotations

    def max_line_length(self):
        return max((len(l) for l in self.display_lines()), default=0)


# ---- 解析 ----
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
            stripped = line.rstrip('\n')
            raw_text = stripped.strip()
            if _is_annotation_line(stripped):
                ann_indent = len(stripped) - len(stripped.lstrip(" "))
                ann_level = ann_indent // 2
                target = ann_level - 1
                if 0 <= target < len(stack):
                    stack[target].annotations.append(raw_text)
                elif stack:
                    stack[-1].annotations.append(raw_text)
                continue
            indent = len(stripped) - len(stripped.lstrip(" "))
            level = indent // 2
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


# ---- 布局 ----
def estimate_node_width(node):
    return node.max_line_length() * CHAR_WIDTH + BOX_PADDING


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


# ---- 绘制 ----
def render_tree(root, out_path):
    fig_width = max(30, root.subtree_width * 1.15)
    fig_height = 30
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))

    def dfs(node):
        for child in node.children:
            ax.plot([node.x, child.x], [node.y, child.y],
                    color="black", linewidth=0.4, zorder=1)
            dfs(child)
        ax.text(node.x, node.y, node.display_text(),
                ha="center", va="center", fontsize=FONT_SIZE,
                family="monospace", zorder=2,
                bbox=dict(boxstyle="round,pad=0.25",
                          facecolor="lightyellow", edgecolor="black",
                          alpha=0.95))

    dfs(root)
    ax.axis("off")
    plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    plt.savefig(out_path, dpi=300, bbox_inches="tight")
    plt.close()


def draw_annotated_ast(input_path=None, output_path=None):
    """绘制带注释语法树图片。

    Args:
        input_path: 带注释语法树文本文件路径，默认 temp/05 ast_comment.txt
        output_path: 输出图片路径，默认 temp/05 ast_comment.png
    """
    src = input_path or AST_PATH
    dst = output_path or OUT_PATH
    root = parse_ast(src)
    if root is None:
        print("语法树文件为空")
        return
    compute_layout(root)
    render_tree(root, dst)
    print(f"带注释语法树图片已生成：{dst}")


# ---- 直接执行入口 ----
def main():
    draw_annotated_ast()


if __name__ == "__main__":
    main()
