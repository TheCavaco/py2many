import ast
from py2many.astx import ASTxIf
from typing import cast

try:
    from ast import unparse  # noqa: introduced in py3.9
except ImportError:
    from astor import to_source as unparse

    # Note ast-compat and astunparse packages fail internally with:
    # AttributeError: 'Constant' object has no attribute 'kind'

    # https://github.com/isidentical/backports.ast_unparse/commit/e7b1aea is broken


unparse  # ignore pyflakes


def get_id(var):
    if isinstance(var, ast.alias):
        return var.name
    elif isinstance(var, ast.arg):
        return var.arg
    elif isinstance(var, ast.Name):
        return var.id
    elif isinstance(var, ast.FunctionDef):
        return var.name
    elif isinstance(var, ast.ClassDef):
        return var.name
    elif isinstance(var, ast.Attribute):
        value_id = get_id(var.value)
        return f"{value_id}.{var.attr}"
    else:
        # print(f"warning: {var}"")
        return None


def create_ast_node(code, at_node=None):
    new_node = ast.parse(code).body[0]
    if at_node:
        new_node.lineno = at_node.lineno
        new_node.col_offset = at_node.col_offset
    return new_node


def create_ast_block(body, at_node=None) -> ASTxIf:
    block = cast(ASTxIf, ast.If(test=ast.Constant(value=True), body=body, orelse=[]))
    block.rewritten = True  # noqa
    if at_node:
        block.lineno = at_node.lineno
    ast.fix_missing_locations(block)
    return block


def copy_attributes(node1, node2):
    """Copy attributes from node1 to node2"""
    for key, val in node1.__dict__.items():
        if key not in node2.__dict__:
            node2.__dict__[key] = val
