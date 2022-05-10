import ast
from typing import Any


def add_annotation_flags(node):
    return AnnotationTransformer().visit(node)


class AnnotationTransformer(ast.NodeTransformer):
    """
    Adds a flag for every type annotation and nested types so they can be differentiated from array
    """

    def __init__(self):
        self.handling_annotation = False

    def visit_arg(self, node):
        if node.annotation:
            self.handling_annotation = True
            self.visit(node.annotation)
            self.handling_annotation = False
        return node

    def visit_FunctionDef(self, node):
        if node.returns:
            self.handling_annotation = True
            self.visit(node.returns)
            self.handling_annotation = False
        self.generic_visit(node)
        return node

    def _visit_record_handling_annotation(self, node) -> ast.AST:
        if self.handling_annotation:
            node.is_annotation = True
        self.generic_visit(node)
        return node

    # without this Dict[x,y] will be translated to HashMap<(x,y)>
    def visit_Tuple(self, node: ast.Tuple) -> ast.Tuple:
        return self._visit_record_handling_annotation(node)

    def visit_List(self, node: ast.List) -> ast.List:
        return self._visit_record_handling_annotation(node)

    def visit_Name(self, node: ast.Name) -> ast.Name:
        return self._visit_record_handling_annotation(node)

    def visit_Subscript(self, node: ast.Subscript) -> ast.Subscript:
        return self._visit_record_handling_annotation(node)

    def visit_Attribute(self, node: ast.Attribute) -> ast.Attribute:
        return self._visit_record_handling_annotation(node)

    def visit_AnnAssign(self, node: ast.AnnAssign):
        self.handling_annotation = True
        # self.visit(node.target)
        self.visit(node.annotation) # Added
        self.handling_annotation = False
        self.generic_visit(node)
        return node
