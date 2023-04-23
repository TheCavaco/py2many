import ast
import copy
import ctypes
from pathlib import Path
from pyclbr import Function
import re
import sys
from typing import Any, Dict, Union, cast

from py2many.clike import class_for_typename

from py2many.exceptions import AstUnsupportedOperation
from py2many.helpers import get_ann_repr
from py2many.inference import InferTypesTransformer
from py2many.scope import ScopeList
from py2many.tracer import find_closest_scope, find_in_body, find_node_by_name_and_type, find_node_by_type, is_class_or_module, is_class_type, is_list
from py2many.analysis import IGNORED_MODULE_SET

from py2many.ast_helpers import copy_attributes, create_ast_node, get_id
from pyjl.clike import JL_IGNORED_MODULE_SET
from pyjl.global_vars import CHANNELS, COMMON_LOOP_VARS, FIX_SCOPE_BOUNDS, FLAG_DEFAULTS, JL_CLASS, LOWER_YIELD_FROM, OBJECT_ORIENTED, OFFSET_ARRAYS, OOP_CLASS, OOP_NESTED_FUNCS, REMOVE_NESTED, REMOVE_NESTED_RESUMABLES, RESUMABLE, SEP, USE_MODULES, USE_RESUMABLES
from pyjl.helpers import fill_attributes, generate_var_name, get_default_val, get_func_def, obj_id
from py2many.helpers import is_dir, is_file
import pyjl.juliaAst as juliaAst


class JuliaMethodCallRewriter(ast.NodeTransformer):
    """Converts Python calls and attribute calls to Julia compatible ones"""
    def __init__(self) -> None:
        super().__init__()
        self._file = None
        self._basedir = None
        self._ignored_module_set = JL_IGNORED_MODULE_SET
        self._imports = []
        self._use_modules = None
        self._oop_nested_funcs = False

    def visit_Module(self, node: ast.Module) -> Any:
        self._file = getattr(node, "__file__", ".")
        self._basedir = getattr(node, "__basedir__", None)
        self._use_modules = getattr(node, USE_MODULES, 
            FLAG_DEFAULTS[USE_MODULES])
        self._imports = list(map(get_id, getattr(node, "imports", [])))
        self._oop_nested_funcs = getattr(node, OOP_NESTED_FUNCS, 
            FLAG_DEFAULTS[OOP_NESTED_FUNCS]) 
        self.generic_visit(node)
        return node

    def visit_Call(self, node: ast.Call):
        self.generic_visit(node)

        # Special attribute used for dispatching
        node.orig_name = get_id(node.func)
        ann = None
        if id := get_id(node.func):
            module_name = id.split(".")
            module_node = node.scopes.find(module_name[1]) \
                if module_name[0] == "self" \
                else node.scopes.find(module_name[0])
            ann = getattr(module_node, "annotation", None)

        # Don't parse annotations and special nodes
        is_module_call = False
        if isinstance(node.func, ast.Attribute):
            is_module_call = \
                get_id(getattr(node.func.value, "annotation", None)) == "Module"
        if getattr(node, "is_annotation", False) or \
                getattr(node, "no_rewrite", False) or \
                getattr(node.func, "no_rewrite", False) or \
                get_id(ann) == "Module" or \
                is_module_call:
            return node

        args = node.args
        fname = node.func
        if isinstance(fname, ast.Attribute):
            val_id = get_id(fname.value)
            # Bypass rewrite when using oop with nested functions
            if val_id and (is_class_type(val_id, node.scopes) or 
                    re.match(r"^self", val_id)) \
                    and self._oop_nested_funcs:
                return node
            # Check if value is module
            is_module = val_id and is_file(val_id, self._basedir)
            # Detect static class access
            class_node = node.scopes.find(val_id)
            is_static_access = is_class_or_module(val_id, node.scopes) and \
                class_node and find_node_by_name_and_type(fname.attr, 
                    ast.FunctionDef, class_node.scopes)[1]
            if (is_module and not self._use_modules) or is_static_access:
                # Handle separate module call when Julia defines no 'module'
                new_func_name = fname.attr
                node.func = ast.Name(
                    id=new_func_name, lineno=node.lineno, ctx=fname.ctx)
            elif not is_class_or_module(val_id, node.scopes):
                args = [fname.value] + args
                new_func_name = fname.attr
                node.func = ast.Name(
                    id=new_func_name, lineno=node.lineno, ctx=fname.ctx)

        node.args = args
        return node

    def visit_Attribute(self, node: ast.Attribute) -> Any:
        self.generic_visit(node)
        # Don't parse annotations or special nodes
        if getattr(node, "is_annotation", False) or \
                getattr(node, "no_rewrite", False):
            return node
        # Get annotation
        annotation = getattr(node, "annotation", None)
        # Adds a dispatch attribute, as functions can be assigned to variables
        node.dispatch = ast.Call(
            func = ast.Name(id=node.attr, ctx=ast.Load(), lineno = node.lineno),
            args=[node.value],
            keywords=[],
            lineno=node.lineno,
            col_offset=node.col_offset,
            annotation = annotation,
            scopes = node.scopes,
            is_attr = True,
            orig_name = get_id(node), # Special attribute used for dispatching
            in_ccall = getattr(node, "in_ccall", None), # Propagate ccall information
        )
        return node


class JuliaAugAssignRewriter(ast.NodeTransformer):
    """Rewrites augmented assignments into compatible 
    Julia operations"""
    def __init__(self) -> None:
        super().__init__()

    def visit_AugAssign(self, node: ast.AugAssign) -> Any:
        node_target = node.target
        is_class = is_class_type(get_id(node.target), node.scopes) or \
            is_class_type(get_id(node.value), node.scopes)
        # Template for call node
        call = ast.Call(
            func = ast.Name(
                id = None,
                lineno = node.lineno,
                col_offset = node.col_offset),
            args = [],
            keywords = [],
            lineno = node.lineno,
            col_offset = node.col_offset,
            scopes = node.target.scopes)

        # New binary operation
        value = ast.BinOp(
                left=node.target,
                op=node.op,
                right=node.value,
                lineno=node.lineno,
                col_offset=node.col_offset,
                scopes = node.value.scopes)

        if isinstance(node.target, ast.Subscript) and \
                isinstance(node.target.slice, ast.Slice):
            call.func.id = "splice!"
            if self._is_number(node.value) and isinstance(node.op, ast.Mult):
                call.args.extend([node_target.value, node_target.slice, value])
                return call
            elif not self._is_number(node.value) and isinstance(node.op, ast.Add):
                lower = node_target.slice.lower
                upper = node_target.slice.upper
                if isinstance(lower, ast.Constant) and isinstance(upper, ast.Constant) and \
                        upper.value >= lower.value:
                    lower_slice = ast.Constant(value = int(upper.value) + 1, scopes = lower.scopes)
                else:
                    lower_slice = ast.Constant(value = lower.value, scopes = lower.scopes)
                new_slice = ast.Slice(
                    lower = lower_slice,
                    upper = ast.Constant(value = upper.value, scopes = upper.scopes)
                )
                call.args.extend([node_target.value, new_slice, node.value])
                return call
        elif isinstance(node.target, ast.Name) and \
                self._is_collection(node.target):
            if isinstance(node.op, ast.Add):
                call.func.id = "append!"
                call.args.append(node.target)
                call.args.append(node.value)
                return call
            elif isinstance(node.op, ast.Mult) and \
                    self._is_number(node.value):
                # append the result of repeating the value
                call.func.id = "append!"
                if isinstance(node.value, ast.Constant):
                    value = ast.Constant(value = node.value.value - 1)
                else:
                    value = ast.BinOp(
                        left = node.value,
                        op = ast.Sub(),
                        right = ast.Constant(value=1)
                    )
                ast.fix_missing_locations(value)
                repeat_arg = ast.Call(
                    func = ast.Name(id="repeat"),
                    args = [node.target, value],
                    keywords = [],
                    scopes = node.scopes
                )
                ast.fix_missing_locations(repeat_arg)
                call.args.append(node.target)
                call.args.append(repeat_arg)
                return call
            elif is_class:
                return ast.Assign(
                    targets=[node_target],
                    value = value,
                    lineno=node.lineno,
                    col_offset=node.col_offset,
                    scopes = node.scopes
                )

        return self.generic_visit(node)

    @staticmethod
    def _is_number(node):
        return isinstance(node, ast.Num) or \
                (isinstance(node, ast.Constant) and node.value.isdigit()) or \
                (get_id(getattr(node, "annotation", None)) in 
                    InferTypesTransformer.FIXED_WIDTH_INTS)

    @staticmethod
    def _is_collection(node):
        ann = getattr(node.scopes.find(get_id(node)), "annotation", None)
        if ann:
            ann_str = ast.unparse(ann)
            return re.match(r"^List|^list|^Dict|^dict|^Set|^set", ann_str) is not None
        return False


class JuliaGeneratorRewriter(ast.NodeTransformer):
    """A Rewriter for Generator functions"""
    SPECIAL_FUNCTIONS = set([
        "islice"
    ])

    def __init__(self):
        super().__init__()
        self._use_resumables = False
        self._lower_yield_from = False
        self._replace_calls: Dict[str, ast.Call] = {}
        self._sweep = False

    def visit_Module(self, node: ast.Module) -> Any:
        # Reset state
        self._replace_calls = {}
        # Get flags
        self._use_resumables = getattr(node, USE_RESUMABLES, 
            FLAG_DEFAULTS[USE_RESUMABLES])
        self._lower_yield_from = getattr(node, LOWER_YIELD_FROM, 
            FLAG_DEFAULTS[LOWER_YIELD_FROM])

        self.generic_visit(node)

        # Sweep phase
        self._sweep = True
        self.generic_visit(node)
        self._sweep = False

        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        if self._sweep:
            body = list(map(lambda x: self.visit(x), node.body))
            node.body = list(filter(lambda x: x is not None, body))
            return node

        body = []
        node.n_body = []
        for n in node.body:
            n_visit = self.visit(n)
            if node.n_body:
                body.extend(node.n_body)
                node.n_body = []
            if n_visit:
                body.append(n_visit)

        # Update body
        node.body = body

        ann_id = get_id(getattr(node, "annotation", None))
        if ann_id == "Generator":
            is_resumable = self._use_resumables or (RESUMABLE in node.parsed_decorators)
            is_channels = CHANNELS in node.parsed_decorators
            if is_resumable and is_channels:
                raise AstUnsupportedOperation(  
                    "Function cannot have both @resumable and @channels decorators", 
                    node)
            elif self._use_resumables and RESUMABLE not in node.parsed_decorators:
                node.parsed_decorators[RESUMABLE] = None
                node.decorator_list.append(ast.Name(id=RESUMABLE))
            elif not is_resumable and not is_channels:
                # Body contains yield and is not resumable function
                node.parsed_decorators[CHANNELS] = None
                node.decorator_list.append(ast.Name(id=CHANNELS))
        return node

    def visit_YieldFrom(self, node: ast.YieldFrom) -> Any:
        if self._sweep:
            return node
        self.generic_visit(node)
        parent = find_closest_scope(node.scopes)
        if isinstance(parent, ast.FunctionDef):
            dec = None
            if CHANNELS in parent.parsed_decorators:
                dec = parent.parsed_decorators[CHANNELS]
            elif RESUMABLE in parent.parsed_decorators:
                dec = parent.parsed_decorators[RESUMABLE]
            lower_yield_from = (dec and dec["lower_yield_from"]) or \
                self._lower_yield_from
            if lower_yield_from:
                val = ast.Name(
                        id = generate_var_name(parent, COMMON_LOOP_VARS),
                        lineno = node.lineno,
                        col_offset = node.col_offset)
                new_node = ast.For(
                    target = val,
                    iter = node.value,
                    body = [
                        ast.Yield(
                            value = val
                        )],
                    orelse = [],
                    lineno = node.lineno,
                    col_offset = node.col_offset,
                    scopes = node.scopes)
                return new_node

        return node

    
    def visit_Call(self, node: ast.Call) -> Any:
        self.generic_visit(node)
        if self._sweep:
            if (id := get_id(node.func)) in self._replace_calls:
                repl_call = self._replace_calls[id]
                repl_call.lineno = node.lineno
                repl_call.col_offset = node.col_offset
                repl_call.scopes = getattr(node, "scopes", ScopeList())
                return repl_call
        else:
            parent = node.scopes[-1] if len(node.scopes) >= 1 else None
            if get_id(node.func) in self.SPECIAL_FUNCTIONS and \
                    isinstance(node.args[0], ast.Call):
                args0 = node.args[0]
                args0_id = get_id(args0.func)
                func_def = get_func_def(node, args0_id)
                if func_def and RESUMABLE in func_def.parsed_decorators and \
                        parent and hasattr(parent, "n_body"):
                    resumable_name = ast.Name(id=f"{args0_id}_")
                    resumable_assign = ast.Assign(
                        targets = [resumable_name],
                        value = ast.Call(
                            func = ast.Name(id = args0_id),
                            args = [arg for arg in args0.args],
                            keywords = [arg for arg in args0.keywords],
                            # annotation = getattr(args0, "annotation", None),
                            scopes = getattr(args0, "scopes", None),
                        ),
                        scopes = getattr(args0, "scopes", None),
                        lineno = node.lineno
                    )
                    node.args[0].func = resumable_name
                    node.args[0].args = []
                    parent.n_body.append(self.visit(resumable_assign))
        return node

    def visit_Assign(self, node: ast.Assign) -> Any:
        self.generic_visit(node)
        if self._sweep:
            return node

        name = get_id(node.value.value) \
            if (isinstance(node.value, ast.Attribute) and
                node.value.attr == "__next__") \
            else get_id(node.value)
        func_def = get_func_def(node, name)
        if func_def and get_id(getattr(func_def, "annotation", None)) == "Generator" and \
                RESUMABLE not in func_def.parsed_decorators:
            self._replace_calls[get_id(node.targets[0])] = ast.Call(
                func = node.value,
                args = [],
                keywords = [],
                annotation = getattr(node.value, "annotation", None),
                scopes = node.scopes,
            )
            return None
        return node


class JuliaBoolOpRewriter(ast.NodeTransformer):
    """Rewrites condition checks to Julia compatible ones
    All checks that perform equality checks with the literal '1'
    have to be converted to equality checks with true"""

    def __init__(self) -> None:
        super().__init__()

    def visit_If(self, node: ast.If) -> Any:
        self.generic_visit(node)
        self._generic_test_visit(node)
        return node

    def visit_While(self, node: ast.While) -> Any:
        self.generic_visit(node)
        self._generic_test_visit(node)
        return node

    def _generic_test_visit(self, node):
        # Shortcut if conditions are numbers
        if isinstance(node.test, ast.Constant):
            if node.test.value == 1 or node.test.value == "1":
                node.test.value = True
                return node
            elif node.test.value == 0:
                node.test.value = False
                return node

        annotation = getattr(node.test, "annotation", None)
        ann_id = get_ann_repr(annotation, sep=SEP)
        if not isinstance(node.test, ast.Compare) and \
                not isinstance(node.test, ast.UnaryOp):
            if ann_id:
                if ann_id != "bool":
                    if ann_id == "int" or ann_id == "float":
                        node.test = self._build_compare(node.test, 
                            [ast.NotEq()], [ast.Constant(value=0)])
                    elif re.match(r"^list|^List", ann_id):
                        # Compare with empty list
                        node.test = self._build_compare(node.test, 
                            [ast.IsNot()], [ast.List(elts=[])])
                    elif re.match(r"^tuple|^Tuple", ann_id):
                        # Compare with empty tuple
                        node.test = self._build_compare(node.test, 
                            [ast.IsNot()], [ast.Tuple(elts=[])])
                    elif re.match(r"^set|^Set", ann_id):
                        # Compare with empty tuple
                        node.test = self._build_compare(node.test, 
                            [ast.IsNot()], [ast.Set(elts=[])])
                    elif re.match(r"^Optional", ann_id):
                        # Compare with type None
                        node.test = self._build_compare(node.test, 
                            [ast.IsNot()], [ast.Constant(value=None)])
                    else:
                        node.test = self._build_runtime_comparison(node)
            else:
                node.test = self._build_runtime_comparison(node)

    def _build_compare(self, node, ops, comp_values):
        for comp_value in comp_values:
            ast.fix_missing_locations(comp_value)
            comp_value.scopes = node.scopes
        return ast.Compare(
            left = node,
            ops = ops,
            comparators = comp_values,
            lineno = node.lineno, 
            col_offset = node.col_offset,
            scopes = node.scopes)
    
    def _build_runtime_comparison(self, node):
        # Perform dynamic comparison
        instance_check = lambda args: ast.Call(
            func = ast.Name(id="isinstance"),
            args = args,
            keywords = [],
            scopes = getattr(node, "scopes", None))
        test_node = ast.BoolOp(
            op = ast.Or(),
            values = [
                ast.BoolOp(
                    op = ast.And(),
                    values = [
                        instance_check([node.test, 
                            ast.Tuple(elts=[ast.Name(id="int"), ast.Name(id="float")])]),
                        self._build_compare(node.test, [ast.NotEq()], [ast.Constant(value=0)])]),
                ast.BoolOp(
                    op = ast.And(),
                    values = [
                        instance_check([node.test, ast.Name(id="tuple")]),
                        self._build_compare(node.test, [ast.NotEq()], [ast.Tuple(elts=[])])]),
                ast.BoolOp(
                    op = ast.And(),
                    values = [
                        instance_check([node.test, ast.Name(id="list")]),
                        self._build_compare(node.test, [ast.NotEq()], [ast.List(elts=[])])]),
                ast.BoolOp(
                    op = ast.And(),
                    values = [self._build_compare(node.test, [ast.Is()], [ast.Constant(value=None)])]),
                ast.BoolOp(
                    op = ast.And(),
                    values = [
                        instance_check([node.test, ast.Name(id="bool")]),
                        node.test]),
            ]
        )
        ast.fix_missing_locations(node.test)
        return test_node
    
    def visit_Compare(self, node: ast.Compare) -> Any:
        # Julia comparisons with 'None' use Henry Baker's EGAL predicate
        # https://stackoverflow.com/questions/38601141/what-is-the-difference-between-and-comparison-operators-in-julia
        self.generic_visit(node)
        find_none = lambda x: isinstance(x, ast.Constant) and x.value == None
        comps_none = next(filter(find_none, node.comparators), None)
        if find_none(node.left) or comps_none:
            for i in range(len(node.ops)):
                if isinstance(node.ops[i], ast.Eq):
                    node.ops[i] = ast.Is()
                elif isinstance(node.ops[i], ast.NotEq):
                    node.ops[i] = ast.IsNot()
        return node


class JuliaIndexingRewriter(ast.NodeTransformer):
    """Translates Python's 0-based indexing to Julia's 
    1-based indexing for lists"""

    SPECIAL_FUNCTIONS = set([
        "bisect",
        "bisect_right",
        "bisect_left",
        "find_ge",
        "find_gt",
        "find_le",
        "find_lt",
        "index",
    ])

    RESERVED_FUNCTIONS = set([
        "__dict__"
    ])

    def __init__(self) -> None:
        super().__init__()
        self._curr_slice_val = None
        self._valid_loop_vars = set()
        self._valid_comprehension_vars = set()

    def visit_Module(self, node: ast.Module) -> Any:
        imports = getattr(node, "imports", [])
        self._imports = [get_id(a) for a in imports]
        self.generic_visit(node)
        return node

    def visit_For(self, node: ast.For) -> Any:
        targets = set()
        positive_and_ascending_range = isinstance(node.iter, ast.Call) and  \
                get_id(node.iter.func) == "range" and \
                ((len(node.iter.args) < 3 and isinstance(node.iter.args[0], ast.Constant)) or 
                (len(node.iter.args) == 3 and isinstance(node.iter.args[0], ast.Constant) and
                    isinstance(node.iter.args[2], ast.Constant)))
        if positive_and_ascending_range:
            # Iter is a call to range and has a positive start value and a 
            # positive stepping value. This implies that if there is a USub 
            # operation, that the values will be negative. The transpiler
            # can only ensure that the values are positive if they are constants.
            if isinstance(node.target, (ast.Tuple, ast.List)):
                targets = {get_id(e) for e in node.target.elts}
                self._valid_loop_vars.update(targets)
            elif isinstance(node.target, ast.Name):
                targets = {get_id(node.target)}
                self._valid_loop_vars.add(get_id(node.target))
        self.generic_visit(node)
        self._valid_loop_vars.difference_update(targets)
        return node

    def visit_ListComp(self, node: ast.ListComp) -> Any:
        targets = set()
        
        # Check each generator
        for generator in node.generators:
            gen_targets = set()
            positive_and_ascending_range = isinstance(generator.iter, ast.Call) and  \
                get_id(generator.iter.func) == "range" and \
                ((len(generator.iter.args) < 3 and isinstance(generator.iter.args[0], ast.Constant)) or 
                (len(generator.iter.args) == 3 and isinstance(generator.iter.args[0], ast.Constant) and
                    isinstance(generator.iter.args[2], ast.Constant)))
            
            if positive_and_ascending_range:
                if isinstance(generator, ast.comprehension) and\
                      isinstance(generator.target, ast.Name) and\
                          isinstance(node.elt, ast.Subscript) and\
                              isinstance(node.elt.slice, ast.Name) and\
                                get_id(generator.target) == get_id(node.elt.slice):
                    print("first")
                    
                    gen_targets = {get_id(generator.target)}
                    # TODO: test this update
                    targets.update(gen_targets)
                    self._valid_comprehension_vars.add(get_id(generator.target))
        self.generic_visit(node)
        self._valid_comprehension_vars.difference_update(targets)
        return node

    def visit_Subscript(self, node: ast.Subscript) -> Any:
        # Don't rewrite nodes that are annotations
        if hasattr(node, "is_annotation"):
            return node

        self._curr_slice_val = node.value
        self.generic_visit(node)
        self._curr_slice_val = None

        # Handle negative indexing
        is_usub = lambda x: (isinstance(x, ast.UnaryOp) and 
                isinstance(x.op, ast.USub))
        end_val = ast.Name(
                id = "end",
                annotation = ast.Name(id="int"),
                preserve_keyword = True)
        if is_usub(node.slice):
            if isinstance(node.slice.operand, ast.Constant):
                if node.slice.operand.value == 1:
                    node.slice = end_val
                else:
                    node.slice = ast.BinOp(
                        left = end_val,
                        op = ast.Sub(),
                        right = ast.Constant(value = node.slice.operand.value - 1),
                        annotation = ast.Name(id = "int"),
                        lineno = node.lineno, col_offset = node.col_offset,
                        scopes = node.slice.scopes
                    )
                return node
            elif get_id(node.slice.operand) in self._valid_loop_vars:
                # Node operand is a unary operation, uses USub and is in the valid 
                # loop variables (meaning the loop is in ascending order). 
                # Therefore, the variable will have a negative value.
                node.slice = ast.BinOp(
                    left = end_val,
                    op = ast.Sub(),
                    right = node.slice.operand,
                    annotation = ast.Name(id = "int"),
                    lineno = node.lineno, col_offset = node.col_offset,
                    scopes = node.slice.scopes
                )
        elif isinstance(node.slice, ast.BinOp) and \
                isinstance(node.slice.right, ast.Constant) and \
                is_usub(node.slice.left) and \
                get_id(node.slice.left.operand) in self._valid_loop_vars:
            # Binary operation where the left node is a unary operation, 
            # uses USub and is in the valid loop variables. The variable 
            # will have a negative value.
            node.slice.left = ast.BinOp(
                left = end_val,
                op = ast.Sub(),
                right = node.slice.left.operand,
                annotation = ast.Name(id = "int"),
                lineno = node.lineno, col_offset = node.col_offset,
                scopes = node.slice.scopes
            )

        # Handle non-negative indexing
        if not self._is_dict(node) and \
                not isinstance(node.slice, ast.Slice):
            call_id = None
            if isinstance(node.slice, ast.Call):
                # TODO: handle simple calls
                call_id = self._get_assign_value(node.slice)

            if not getattr(node, "range_optimization", None) and \
                    not getattr(node, "using_offset_arrays", None):
                # Ignore special functions, as they already return the correct indices
                if call_id in self.SPECIAL_FUNCTIONS and \
                        call_id in self._imports:
                    return node
                if isinstance(node.value, ast.Attribute) and \
                        node.value.attr in self.RESERVED_FUNCTIONS:
                    return node

                if isinstance(node.slice, ast.Constant) \
                        and isinstance(node.slice.value, int):
                    # Shortcut if index is a numeric value
                    node.slice.value += 1
                else:
                    # Don't add 1 to string constants
                    if isinstance(node.slice, ast.Constant) and \
                            isinstance(node.slice.value, str):
                        return node
                    
                    if get_id(node.slice) != "end" and \
                        isinstance(node.slice, ast.Name) and\
                              get_id(node.slice) in self._valid_comprehension_vars:
                        # No need to add
                        print("second")

                        return node
                    elif get_id(node.slice) != "end":
                        # Default: add 1
                        node.slice = self._do_bin_op(node.slice, ast.Add(), 1,
                            node.lineno, node.col_offset)
            elif getattr(node, "range_optimization", None) and \
                    not getattr(node, "using_offset_arrays", None):
                # Support nested subscripts. See example at:
                # tests/cases/newman_conway_sequence.py
                for_node = find_node_by_type(ast.For, node.scopes)
                if for_node:
                    target_id = get_id(for_node.target)
                    if isinstance(node.slice, ast.Subscript) or \
                            isinstance(node.slice, ast.BinOp) and \
                            not self._bin_op_contains(node.slice, target_id):
                        node.slice = self._do_bin_op(node.slice, ast.Add(), 1,
                            node.lineno, node.col_offset)
            else:
                if call_id in self.SPECIAL_FUNCTIONS and \
                        call_id in self._imports:
                    # Get corresponding assignment
                    assign_node = find_node_by_name_and_type(get_id(node.value), ast.Assign, node.scopes)[0]
                    if assign_node and isinstance(assign_node.value, ast.Call) and \
                            get_id(assign_node.value.func) == "OffsetArray":
                        dec = assign_node.value.args[1]
                        dec = -dec.value if dec.value < 0 else dec.value
                        node.slice = self._do_bin_op(node.slice, ast.Sub(), dec,
                            node.lineno, node.col_offset)

        return node
    
    def visit_comprehension(self, node: ast.comprehension) -> Any:
        self.generic_visit(node)
        comp_var = get_id(node.target) in self._valid_comprehension_vars and isinstance(node.iter, ast.Call)
        rest = (get_id(node.iter.func) == "range" or get_id(node.iter.func) == "xrange") and\
        (not getattr(node, "range_optimization", None) or \
                    getattr(node, "using_offset_arrays", None))
        
        # remove the - 1
        return node

    def _bin_op_contains(self, bin_op: ast.BinOp, node_id):
        if (get_id(bin_op.left) == node_id) or \
                (get_id(bin_op.right) == node_id):
            return True
        contains = False
        if isinstance(bin_op.left, ast.BinOp):
            contains = self._bin_op_contains(bin_op.left, node_id)
        if not contains and isinstance(bin_op.right, ast.BinOp):
            contains = self._bin_op_contains(bin_op.right, node_id)
        return contains

    def _get_assign_value(self, node: ast.Call):
        """Gets the last assignment value"""
        call_id = obj_id(node.func)
        assign_node = find_node_by_name_and_type(call_id, ast.Assign, node.scopes)[0]
        if assign_node:
            if isinstance(assign_node.value, ast.Call):
                return self._get_assign_value(assign_node.value)
            elif id := obj_id(assign_node.value):
                return id 
        return call_id

    def visit_Slice(self, node: ast.Slice) -> Any:
        self.generic_visit(node)

        # Might need this later
        # elif getattr(node.lower, "splice_increment", None):
        #     # From JuliaAugAssignRewriter
        #     lower = f"({lower} + 2)"

        # Translate negative indexing
        if isinstance(node.upper,  ast.UnaryOp) \
                and isinstance(node.upper.op, ast.USub) and \
                isinstance(node.upper.operand, ast.Constant):
            node.upper = ast.BinOp(
                left = ast.Name(
                    id = "end",
                    annotation = ast.Name(id="int"),
                    preserve_keyword = True),
                op = ast.Sub(),
                right = node.upper.operand,
                lineno = node.upper.lineno,
                col_offset = node.upper.col_offset,
                scopes = node.upper.scopes)
        elif isinstance(node.lower, ast.UnaryOp) \
                and isinstance(node.lower.op, ast.USub) \
                and self._curr_slice_val:
            length = ast.Call(
                    func = ast.Name(
                        id = "length",
                        lineno = node.lineno, col_offset = node.col_offset,
                        annotation = ast.Name(id = "int")),
                    args = [self._curr_slice_val], 
                    keywords = [],
                    annotation = ast.Name(id="int"),
                    lineno = node.lineno, col_offset = node.col_offset,
                    scopes = node.lower.scopes)
            # Account for the fact that Julia indices start from 1
            if isinstance(node.lower.operand, ast.Constant) and \
                    node.lower.operand.value != 1:
                right = self._do_bin_op(node.lower.operand, ast.Add(), 1, 
                    node.lineno, node.col_offset)
                node.lower = ast.BinOp(
                    left = length,
                    op = ast.Sub(),
                    right = right,
                    lineno = node.lineno, col_offset = node.col_offset,
                    scopes = node.lower.scopes)
            else:
                node.lower = length
        elif not getattr(node, "range_optimization", None) and \
                not getattr(node, "using_offset_arrays", None):
            if isinstance(node.lower, ast.Constant) and isinstance(node.lower.value, int):
                node.lower.value += 1
            elif node.lower:
                # Default: add 1
                node.lower = self._do_bin_op(node.lower, ast.Add(), 1,
                    node.lineno, node.col_offset)

        if hasattr(node, "step"):
            # Translate reverse lookup
            if isinstance(node.step, ast.Constant) \
                    and node.step.value == -1:
                if (not node.lower and not node.upper) or \
                        (not node.upper and isinstance(node.lower, ast.Constant) \
                            and node.lower.value == -1):
                    node.lower = ast.Name(id="end", annotation = ast.Name(id = "int"))
                    node.upper = ast.Name(id="begin", annotation = ast.Name(id = "int"))
                elif not node.upper:
                    node.upper = ast.Name(id = "end", annotation = ast.Name(id = "int"))

        return node

    def visit_Call(self, node: ast.Call) -> Any:
        self.generic_visit(node)
        call_id = get_id(node.func)
        if (call_id == "range" or call_id == "xrange"):
            # args order: start, stop, step
            if getattr(node, "range_optimization", None) and \
                    not getattr(node, "using_offset_arrays", None):
                if len(node.args) == 1:
                    # By default, the arrays start at 1
                    node.args.append(node.args[0])
                    node.args[0] = ast.Constant(
                        value=1, 
                        scopes=getattr(node.args[0], "scopes", ScopeList()))
                elif len(node.args) > 1:

                    # increment start
                    node.args[0] = self._do_bin_op(node.args[0], ast.Add(), 1,
                        node.lineno, node.col_offset)
            else:
                # decrement stop
                if len(node.args) == 1:
                    node.args[0] = self._do_bin_op(node.args[0], ast.Sub(), 1,
                        node.lineno, node.col_offset)
                elif len(node.args) > 1:
                    node.args[1] = self._do_bin_op(node.args[1], ast.Sub(), 1,
                        node.lineno, node.col_offset)
            if len(node.args) == 3:
                # Cover reverse lookup
                if isinstance(node.args[2], ast.UnaryOp) and \
                        isinstance(node.args[2].op, ast.USub):
                    node.args[0], node.args[1] = node.args[1], node.args[0]
        return node

    def _do_bin_op(self, node, op, val, lineno, col_offset):
        left = node
        left.annotation = ast.Name(id="int")
        return ast.BinOp(
                    left = left,
                    op = op,
                    right = ast.Constant(
                        value = val, 
                        annotation = ast.Name(id= "int"),
                        scopes = node.scopes),
                    lineno = lineno,
                    col_offset = col_offset,
                    scopes = node.scopes
                )

    def _is_dict(self, node):
        ann = None
        if hasattr(node, "container_type"):
            ann = node.container_type
        elif val_annotation := getattr(node.value, 'annotation', None):
            ann = val_annotation

        # Parse ann
        if id := get_id(ann):
            ann = id
        elif isinstance(ann, tuple):
            ann = ann[0]
        elif isinstance(ann, ast.Subscript):
            ann = get_id(ann.value)
        return ann == "Dict" or ann == "dict"


class JuliaIORewriter(ast.NodeTransformer):
    """Rewrites IO operations into Julia compatible ones"""
    def __init__(self) -> None:
        super().__init__()

    def visit_For(self, node: ast.For) -> Any:
        self.generic_visit(node)
        if isinstance(node.iter, ast.Name):
            iter_node = node.scopes.find(get_id(node.iter))
            iter_ann = getattr(iter_node, "annotation", None)
            if get_id(iter_ann) == "BinaryIO":
                # Julia IOBuffer cannot be read by line
                node.iter = ast.Call(
                    func = ast.Name(id = "readlines"),
                    args = [ast.Name(id = get_id(node.iter))],
                    keywords = [],
                    scopes = node.iter.scopes
                )
        return node

    def visit_Subscript(self, node: ast.Subscript) -> Any:
        # Optimization for sys.argv
        if isinstance(node.value, ast.Attribute) and \
                get_id(node.value) == "sys.argv" and \
                isinstance(node.slice, ast.Constant) and \
                node.slice.value > 0:
            node.value = ast.Name(id="ARGS")
            # Decrement value by 1, as ARGS does not include
            # module name. Optimization Rewriters will optimize
            # redundant binary operations
            node.slice = ast.BinOp(
                left = node.slice,
                op = ast.Sub(),
                right = ast.Constant(value=1)
            )
            ast.fix_missing_locations(node.slice)
            ast.fix_missing_locations(node.value)
        return node

class JuliaOrderedCollectionRewriter(ast.NodeTransformer):
    """Rewrites normal collections into ordered collections. 
    This depends on the JuliaOrderedCollectionTransformer"""
    def __init__(self) -> None:
        super().__init__()
        self._use_ordered_collections = False

    def visit_Module(self, node: ast.Module) -> Any:
        self._use_ordered_collections = getattr(node, "use_ordered_collections", False)
        self.generic_visit(node)
        return node

    def visit_Dict(self, node: ast.Dict) -> Any:
        self.generic_visit(node)
        if getattr(node, "use_ordered_collection", None) or \
                self._use_ordered_collections:
            return juliaAst.OrderedDict(
                keys = node.keys,
                values = node.values,
                annotation = node.annotation
            )
        return node

    def visit_DictComp(self, node: ast.DictComp) -> Any:
        self.generic_visit(node)
        if getattr(node, "use_ordered_collection", None) or \
                self._use_ordered_collections:
            return juliaAst.OrderedDictComp(
                key = node.key,
                value = node.value,
                generators = node.generators,
                annotation = node.annotation
            )
        return node

    def visit_Set(self, node: ast.Set) -> Any:
        self.generic_visit(node)
        if getattr(node, "use_ordered_collection", None) or \
                self._use_ordered_collections:
            return juliaAst.OrderedSet(
                elts = node.elts,
                annotation = node.annotation
            )
        return node


class JuliaMainRewriter(ast.NodeTransformer):
    def __init__(self):
        super().__init__()

    def visit_If(self, node):
        is_main = (isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Name)
                and node.test.left.id == "__name__"
                and isinstance(node.test.ops[0], ast.Eq)
                and isinstance(node.test.comparators[0], ast.Constant)
                and node.test.comparators[0].value == "__main__")
        node.is_python_main = is_main
        if is_main:
            node.python_main = is_main
            node.test.left = ast.Call(
                func = ast.Name(id="abspath", preserve_keyword=True),
                args = [ast.Name(id="PROGRAM_FILE")],
                keywords = [],
                scopes = node.test.left.scopes,
                lineno = node.test.left.lineno,
                col_offset = node.test.left.col_offset)
            node.test.comparators[0] = ast.Name(id="@__FILE__")
            ast.fix_missing_locations(node.test)
        return node

class JuliaArbitraryPrecisionRewriter(ast.NodeTransformer):
    def __init__(self) -> None:
        super().__init__()
        self._use_arbitrary_precision = False
        self._arbitrary_precision_vars = set()

    def visit_Module(self, node: ast.Module) -> Any:
        self._use_arbitrary_precision = getattr(node, "use_arbitrary_precision", False)
        self._arbitrary_precision_vars = set()
        self.generic_visit(node)
        return node

    def visit_Name(self, node: ast.Name) -> Any:
        ann = get_id(getattr(node, "annotation", None)) == "int"
        if get_id(node) in self._arbitrary_precision_vars:
            node.is_arbitrary_precision_var = True
            if ann == "int":
                if get_id(node) == "int":
                    node.id = "BigInt"
                elif get_id(node) == "float":
                    node.id = "BigFloat"
        elif self._use_arbitrary_precision and \
                ann == "int":
            if get_id(node) == "int":
                node.id = "BigInt"
            elif get_id(node) == "float":
                node.id = "BigFloat"
        return node

    def visit_Assign(self, node: ast.Assign) -> Any:
        # self.generic_visit(node)
        for t in node.targets:
            self.visit(t)
        self._generic_assign_visit(node, target=node.targets[0])
        return node

    def visit_AnnAssign(self, node: ast.AnnAssign) -> Any:
        # self.generic_visit(node)
        self.visit(node.target)
        self._generic_assign_visit(node, target=node.target)
        return node

    def _generic_assign_visit(self, node: Union[ast.Assign, ast.AnnAssign], target):
        self.generic_visit(node)
        annotation = get_id(getattr(target, "annotation", None))
        if annotation:
            if (annotation == "BigInt" or annotation == "BigFloat" or
                        (self._use_arbitrary_precision and 
                        (annotation == "int" or annotation == "float")))\
                    and not getattr(node.value, "ignore_wrap", None):
                self._arbitrary_precision_vars.add(get_id(target))
                func_name = "BigInt" if annotation == "int" else "BigFloat"
                lineno = getattr(node, "lineno", 0)
                col_offset = getattr(node, "col_offset", 0)
                node.value = ast.Call(
                    func = ast.Name(id=func_name),
                    args = [node.value],
                    keywords = [],
                    lineno = lineno,
                    col_offset = col_offset,
                    annotation = ast.Name(id=annotation),
                    scopes = node.scopes)
                ast.fix_missing_locations(node.value)

    def visit_BinOp(self, node: ast.BinOp) -> Any: 
        self.generic_visit(node)
        node.ignore_wrap = (
            getattr(node.left, "is_arbitrary_precision_var", False) or
            getattr(node.right, "is_arbitrary_precision_var", False))
        return node

###########################################################
############### Removing nested constructs ################
###########################################################

class JuliaNestingRemoval(ast.NodeTransformer):
    def __init__(self) -> None:
        super().__init__()
        self._remove_nested = False
        self._remove_nested_resumables = False
        self._nested_classes = []
        self._nested_generators = []

    def visit_Module(self, node: ast.Module) -> Any:
        self._remove_nested = getattr(node, REMOVE_NESTED, False)
        self._remove_nested_resumables = getattr(node, REMOVE_NESTED_RESUMABLES, 
            FLAG_DEFAULTS[REMOVE_NESTED_RESUMABLES])
        body = []
        # Add nested classes and generator functions to top scope
        for n in node.body:
            b_node = self.visit(n)
            if self._nested_generators:
                for nested in self._nested_generators:
                    nested.scopes = getattr(node, "scopes", ScopeList())
                    body.append(self.visit(nested))
                self._nested_generators = []
            if self._nested_classes:
                # Add nested classes to top scope                 
                for cls in self._nested_classes:
                    cls.scopes = getattr(node, "scopes", ScopeList())
                    body.append(self.visit(cls))
                self._nested_classes = []
            body.append(b_node)

        # Update Body
        node.body = body

        return node
    
    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        is_resumable = lambda x: RESUMABLE in x.parsed_decorators
        is_generator = lambda x: get_id(getattr(x, "annotation", False)) == "Generator"

        body = []
        for n in node.body:
            if isinstance(n, ast.FunctionDef):
                if is_generator(n) and self._remove_nested_resumables:
                    self._nested_generators.append(n)
                elif is_resumable(n):
                    resumable_dec = n.parsed_decorators[RESUMABLE]
                    if resumable_dec and \
                            REMOVE_NESTED in resumable_dec \
                            and resumable_dec[REMOVE_NESTED]:
                        self._nested_generators.append(n)
                else:
                    body.append(n)
            elif isinstance(n, ast.ClassDef) and \
                    (REMOVE_NESTED in n.parsed_decorators or
                    self._remove_nested):
                self._nested_classes.append(n)
            else:
                body.append(n)
        node.body = body
        return node

    def visit_ClassDef(self, node: ast.ClassDef) -> Any:
        class_name = node.name
        body = []
        for n in node.body:
            if isinstance(n, ast.ClassDef):
                n.bases.append(ast.Name(id=f"Abstract{class_name}", ctx=ast.Load()))
                self._nested_classes.append(n)
            else:
                body.append(self.visit(n))
        # Update body
        node.body = body
        return node


class JuliaImportRewriter(ast.NodeTransformer):
    """Removes nested imports and rewrites calls to 
    the __init__ module"""
    def __init__(self) -> None:
        super().__init__()
        # The default module represents all Import nodes.
        # ImportFrom nodes have the module as key.
        self._import_names: Dict[str, list[str]] = {}
        self._nested_imports = []
        self._import_cnt = 0
        self._basedir = None
        self._class_import_funcs = {}
        self._import_rewrite = False

    def visit_Module(self, node: ast.Module) -> Any:
        self._import_names = {}
        self._nested_imports = []
        self._basedir = getattr(node, "__basedir__", None)
        self._import_cnt = 0
        self._class_import_funcs = {}
        self.generic_visit(node)
        node.body = self._nested_imports + node.body
        node.import_cnt = self._import_cnt
        # Update imports
        for imp in self._nested_imports:
            for name in imp.names:
                if name not in node.imports:
                    node.imports.append(name)
        self._import_rewrite = True
        for n in node.body:
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                self.visit(n)
        self._import_rewrite = False
        return node

    def visit_ClassDef(self, node: ast.ClassDef) -> Any:
        self.generic_visit(node)
        # Rewrite module calls to calls to __init__
        func_repr = get_id(node)
        split_repr = func_repr.split(".") if func_repr else []
        if split_repr and is_dir(".".join(split_repr), self._basedir):
            self._insert_init(node)
            return node

        # Bypass imports
        for i in range(1,len(split_repr)):
            if is_dir('.'.join(split_repr[:i]), self._basedir):
                return node
        return node

    def _insert_init(self, node: ast.Attribute):
        if isinstance(node.value, ast.Attribute):
            self._insert_init(node.value)
        else:
            # Avoid referencing the same object (TODO: Is this necessary?)
            value = copy.deepcopy(node.value)
            node.value = ast.Attribute(
                value = value,
                attr = node.attr,
                ctx = ast.Load(),
                scopes = node.scopes,
                no_rewrite = True,
                lineno = node.lineno,
                col_offset = node.col_offset,
            )
            node.attr = "__init__"
            return node.value.value

    def visit_Attribute(self, node: ast.Attribute) -> Any:
        val_id = get_id(node.value)
        val_node = node.scopes.find(val_id)
        assigned_from = val_node.assigned_from \
            if hasattr(val_node, "assigned_from") \
            else None
        find_node = lambda x: isinstance(x, ast.FunctionDef) and x.name == node.attr
        if assigned_from and \
                isinstance(assigned_from, (ast.Assign, ast.AnnAssign)) and \
                isinstance(assigned_from.value, ast.Call) and \
                isinstance(node.scopes.find(get_id(assigned_from.value.func)), ast.ClassDef):
            class_scope = node.scopes.find(get_id(assigned_from.value.func))
            if isinstance(class_scope, ast.ClassDef) and \
                    find_in_body(class_scope.body, find_node):
                self._class_import_funcs[get_id(assigned_from.value.func)] = node.attr
        return node

    def visit_If(self, node: ast.If) -> Any:
        return self._generic_import_scope_visit(node)

    def visit_With(self, node: ast.With) -> Any:
        return self._generic_import_scope_visit(node)

    def _generic_import_scope_visit(self, node):
        if hasattr(node, "imports"):
            del node.imports
        self.generic_visit(node)
        return node
    
    def visit_Import(self, node: ast.Import) -> Any:
        return self._generic_import_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> Any:
        return self._generic_import_visit(node, node.module)

    def _generic_import_visit(self, node, key = "default"):
        if self._import_rewrite:
            new_names = []
            for alias in node.names:
                n = alias.name
                if n in self._class_import_funcs and \
                        self._class_import_funcs[n] not in node.names:
                    new_names.append(ast.alias(name=self._class_import_funcs[n]))
            node.names.extend(new_names)
            return node
        self._import_cnt += 1
        if key not in self._import_names:
            self._import_names[key] = []
        aliases = []
        for alias in node.names:
            name = alias.name
            if name not in self._import_names[key]:
                self._import_names[key].append(name)
                aliases.append(alias)
        if not aliases:
            return None
        node.names = aliases
        # self.generic_visit(node)
        parent = node.scopes[-1] if len(node.scopes) >= 1 else None
        if parent and not isinstance(parent, ast.Module):
            self._nested_imports.append(node)
            return None
        return node


###########################################################
##################### Class Rewriters #####################
###########################################################

class JuliaClassWrapper(ast.NodeTransformer):
    # A hack to support two alternatives of translating 
    # Python classes to Julia
    def __init__(self) -> None:
        super().__init__()
        self._has_dict = False
        self._has_getfield = False

    def visit_Module(self, node: ast.Module) -> Any:
        self._has_dict = False
        self._has_getfield = False
        if hasattr(node, OBJECT_ORIENTED):
            visitor = JuliaClassOOPRewriter()
        else:
            visitor = JuliaClassSubtypingRewriter()
        node = visitor.visit(node)
        body = []
        for n in node.body:
            body.append(self.visit(n))
            if isinstance(n, ast.ClassDef) and self._has_dict \
                    and not self._has_getfield:
                body.append(self._build_get_property_func(n, node.scopes))
                self._has_dict = False
                self._has_getfield = False
        node.body = body
        return node

    def _build_get_property_func(self, class_node: ast.ClassDef, scopes):
        get_property_func = ast.FunctionDef(
            name = "Base.getproperty", # Hack to set the correct name
            args = ast.arguments(
                args = [
                    ast.arg(arg = "self", annotation = ast.Name(id=class_node.name)),
                    ast.arg(arg = "property", annotation = ast.Name(id="Symbol"))],
                defaults = []
            ),
            body = [
                    ast.Assign(
                        targets = [ast.Name(id="__dict__")],
                        value = ast.Call(
                            func = ast.Attribute(
                                value = ast.Name("Base"), 
                                attr = "getattribute", 
                                ctx = ast.Load(),
                                scopes = scopes),
                            args=[ast.Name(id="self"), juliaAst.Symbol(id="__init__")],
                            keywords = [],
                            no_rewrite=True,
                            scopes = scopes
                        ),
                        scopes = scopes,
                    ),
                    ast.If(
                        test = ast.Call(
                            func=ast.Name(id="haskey"), 
                            args = [ast.Name(id="__init__"), ast.Name(id="property")],
                            keywords = [],
                            no_rewrite=True,
                            scopes = scopes),
                        body = [
                            ast.Return(value = 
                                ast.Subscript(
                                    value=ast.Name(id="__dict__"), 
                                    slice=ast.Name(id="property"))
                            )],
                        orelse = []
                    ),
                    ast.Return(value = 
                        ast.Call(
                            func = ast.Attribute(
                                value = ast.Name("Base"), 
                                attr = "getfield", 
                                ctx = ast.Load(),
                                scopes = scopes),
                            args=[ast.Name(id="self"), ast.Name(id="property")],
                            keywords = [],
                            no_rewrite=True,
                            scopes = scopes)
                    ),
                ],
            decorator_list = [],
            scopes = scopes,
            parsed_decorators = {},
        )
        ast.fix_missing_locations(get_property_func)
        return get_property_func

    def visit_ClassDef(self, node: ast.ClassDef) -> Any:
        self.generic_visit(node)
        ann = ast.Subscript(
            value = ast.Name(id="Dict"),
            slice = ast.Tuple(
                elts=[ast.Name(id="Symbol"), ast.Name(id="Any")],
                is_annotation = True),
            is_annotation = True,
        )
        if self._has_dict:
            body = []
            assign = ast.AnnAssign(
                target=ast.Attribute(
                    value = ast.Name(id="self"),
                    attr = "__dict__", 
                    ctx = ast.Load(),
                    scopes = node.scopes),
                value = ast.Dict(keys=[], values=[], annotation=ann),
                annotation = ann
                )
            if isinstance(node.body[0], ast.FunctionDef):
                if node.body[0].name != "__init__":
                    # Build a dunder init to get arround new __dict__ arg
                    init_func = ast.FunctionDef(
                        name="__init__", args = ast.arguments(args=[], defaults=[]), body = [assign], 
                        decorator_list = [], parsed_decorators = {})
                    ast.fix_missing_locations(init_func)
                    body.append(init_func)
                else:
                    ast.fix_missing_locations(assign)
                    node.body[0].body.append(assign)
            node.body = body + node.body
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        self.generic_visit(node)
        if node.name == "__getattr__":
            self._has_getfield = True
        return node

    def visit_Call(self, node: ast.Call) -> Any:
        func_id = ast.unparse(node.func)
        if re.match(r"^super", func_id) is not None:
            # Matches any call starting with super
            # According to C3 MRO, a call to super() will select 
            # the first base class
            class_node = find_node_by_type(ast.ClassDef, node.scopes)
            base = class_node.bases[0] if class_node else None
            node.func = base
        elif isinstance(node.func, ast.Attribute) and \
                re.match(r"^.*__init__$", func_id):
            # Matches any call that ends with init
            node.func = node.func.value
        # Remove self
        if node.args and isinstance(node.args[0], ast.Name) and \
                get_id(node.args[0]) == "self":
            node.args = node.args[1:]
        return node

    def visit_Assign(self, node: ast.Assign) -> Any:
        self.generic_visit(node)
        target = node.targets[0]
        # Detect if objects are being added as fields
        if isinstance(target, ast.Subscript) and \
                get_id(target.value) == "self.__dict__":
            self._has_dict = True
        elif get_id(target) == "self.__dict__":
            self._has_dict = True
        return node

    def visit_Subscript(self, node: ast.Subscript) -> Any:
        self.generic_visit(node)
        if isinstance(node.value, ast.Attribute) and \
                node.value.attr == "__dict__":
            # Wrap __dict__ values into Julia Symbols
            node.slice = ast.Call(
                func=ast.Name(id="Symbol"),
                args = [node.slice], 
                keywords=[],
                scopes = getattr(node, "scopes", None))
            ast.fix_missing_locations(node.slice)
        return node

class JuliaClassOOPRewriter(ast.NodeTransformer):
    """Adds decorators to OOP classes and differentiate 
    functions within OOP classes"""
    def __init__(self) -> None:
        super().__init__()
        self._is_oop = False
        self._oop_nested_funcs = False

    def visit_Module(self, node: ast.Module) -> Any:
        self._oop_nested_funcs = getattr(node, OOP_NESTED_FUNCS, 
            FLAG_DEFAULTS[OOP_NESTED_FUNCS])
        self.generic_visit(node)
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        self.generic_visit(node)
        if self._is_oop:
            node.oop = True
            node.oop_nested_func = self._oop_nested_funcs
        return node
    
    def visit_ClassDef(self, node: ast.ClassDef) -> Any:
        # Add OO decorator
        decorator = ast.Call(
            func=ast.Name(id=OOP_CLASS), 
            args=[], 
            keywords=[],
            scopes = node.scopes)
        keywords = None
        if self._oop_nested_funcs:
            decorator.keywords.append(ast.keyword(
                arg = "oop_nested_funcs", 
                value = ast.Constant(value=True)))
            keywords = {"oop_nested_funcs": True}
        node.decorator_list.append(decorator)
        node.parsed_decorators["oop_class"] = keywords
        node.jl_bases = node.bases
        # Visit OOP class
        self._is_oop = True
        for n in node.body:
            if isinstance(n, ast.ClassDef):
                n.is_nested = True
            self.visit(n)
        if not getattr(node, "is_nested", False):
            self._is_oop = False
        return node

class JuliaClassSubtypingRewriter(ast.NodeTransformer):
    """Simple Rewriter that transforms Python classes using Julia's subtyping"""

    IGNORE_EXTENDS_SET = set([
        "IntEnum",
        "IntFlag",
        "Enum",
    ])

    IGNORE_ABSTRACT_SET = set([
        "unittest.TestCase",
        "object",
        "Object",
    ])

    SPECIAL_EXTENDS_MAP = {
        "Exception": "Exception",
        "Error": "Exception",
    }

    def __init__(self) -> None:
        super().__init__()
        self._ignored_module_set = \
            self._ignored_module_set = IGNORED_MODULE_SET.copy()\
                .union(JL_IGNORED_MODULE_SET.copy())
        self._hierarchy_map = {}
        self._class_scopes = []

    def visit_Module(self, node: ast.Module) -> Any:
        self._hierarchy_map = {}
        self._class_scopes = []
        node.lineno = 0
        node.col_offset = 0

        self.generic_visit(node)

        # Visit body nodes
        body = node.body

        # Create abstract types
        abstract_types = []
        l_no = node.import_cnt
        for class_name, extends in self._hierarchy_map.items():
            nameVal = ast.Name(id=class_name)
            extends_node = None
            core_module = extends.split(".")[0] \
                if extends else None
            if extends and core_module not in self._ignored_module_set and \
                    extends not in self.IGNORE_ABSTRACT_SET and \
                    extends in self._hierarchy_map:
                # Ignore all classes that are not supported
                extends_node = ast.Name(id=f"Abstract{extends}")
                ast.fix_missing_locations(extends_node)
            abstract_types.append(juliaAst.AbstractType(value=nameVal, extends = extends_node,
                                    ctx=ast.Load(), lineno=l_no, col_offset=0))
            # increment linenumber
            l_no += 1

        if abstract_types:
            body = body[:node.import_cnt] + \
                abstract_types + body[node.import_cnt:]

        node.body = body

        return node

    def visit_ClassDef(self, node: ast.ClassDef) -> Any:
        self.generic_visit(node)

        node.jl_bases = []
        # Certain classes do not need a hierarchy
        base_ids = set(map(get_id, node.bases))
        if base_ids.intersection(self.IGNORE_EXTENDS_SET):
            return node

        base = get_id(node.bases[0]) if node.bases else None
        if base in self.SPECIAL_EXTENDS_MAP:
            # As Julia only supports single inheritance, we only analyse the first class
            node.jl_bases.append(ast.Name(id=self.SPECIAL_EXTENDS_MAP[base]))
            return node

        class_name: str = get_id(node)

        decorator_list = list(map(get_id, node.decorator_list))
        if JL_CLASS in decorator_list:
            node.jl_bases = node.bases
            return node

        extends = None
        # Change bases to support Abstract Types
        node.jl_bases = [
            ast.Name(id=f"Abstract{class_name}", ctx=ast.Load)]
        if len(node.bases) == 1:
            extends = get_id(node.bases[0])
        elif len(node.bases) > 1:
            raise Exception("Multiple inheritance is only supported with ObjectOriented.jl")

        self._hierarchy_map[class_name] = extends

        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        self.generic_visit(node)

        args = node.args
        for arg in args.args:
            if ((annotation := getattr(arg, "annotation", None)) and
                    is_class_or_module(annotation, node.scopes) and 
                    not get_id(annotation) in self.SPECIAL_EXTENDS_MAP):
                setattr(annotation, "id", f"Abstract{get_id(annotation)}")

        parent_node = find_node_by_type(ast.ClassDef, node.scopes)
        if (hasattr(node, "self_type") and
                is_class_or_module(node.self_type, node.scopes) and 
                not (parent_node.bases and \
                get_id(parent_node.bases[0]) in self.SPECIAL_EXTENDS_MAP)):
            node.self_type = f"Abstract{node.self_type}"

        return node


###########################################################
################## Conditional Rewriters ##################
###########################################################

class VariableScopeRewriter(ast.NodeTransformer):
    """Rewrites variables in case they are defined within one 
    of Julia's local hard/soft scopes but used outside of their scopes. 
    This has to be executed after the JuliaVariableScopeAnalysis transformer"""
    def __init__(self) -> None:
        super().__init__()
        self._variables_out_of_scope: dict[str, Any] = {}
    
    def visit_Module(self, node: ast.Module) -> Any:
        self._variables_out_of_scope: dict[str, Any] = {}
        if getattr(node, FIX_SCOPE_BOUNDS, FLAG_DEFAULTS[FIX_SCOPE_BOUNDS]):
            self._generic_scope_visit(node)
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        self._generic_scope_visit(node)
        return node

    def visit_With(self, node: ast.With) -> Any:
        # Retrieve nested variables that are out of scope
        self._generic_scope_visit(node)
        parent = node.scopes[-2] \
            if hasattr(node, "scopes") and len(node.scopes) >= 2 \
            else None
        variables_out_of_scope = getattr(parent, "variables_out_of_scope", {})
        target_ids = [get_id(it.optional_vars) for it in node.items
            if get_id(it.optional_vars) in variables_out_of_scope]
        if target_ids:
            # Because do-blocks behaves like anonymous functions, 
            # we have to return the elements if they are used 
            # outside their scope (This is not a permanent solution)
            variable_vals = ast.Name(id=target_ids[0]) \
                if len(target_ids) == 1 \
                else ast.Tuple(elts=[ast.Name(id=target) for target in target_ids])
            # Return nodes that are required in outer scopes
            node.body.append(ast.Return(value=variable_vals))
            assign = ast.Assign(
                targets = [variable_vals],
                value = node,
                scopes = node.scopes,
            )
            ast.fix_missing_locations(assign)
            return assign

        return node

    def visit_For(self, node: ast.For) -> Any:
        self._generic_scope_visit(node)
        # Consider all variables out of scope from parent scopes
        all_variables_out_of_scope = {}
        for sc in node.scopes:
            if hasattr(sc, "variables_out_of_scope"):
                all_variables_out_of_scope |= sc.variables_out_of_scope
        target_id = get_id(node.target)
        if target_id in all_variables_out_of_scope:
            annotation = getattr(node.scopes.find(target_id), "annotation", None)
            target = ast.Name(
                id = target_id,
                annotation = annotation)
            new_loop_id = f"_{target_id}"
            new_var_assign = ast.Assign(
                targets=[target],
                value = ast.Name(
                    id = new_loop_id,
                    annotation = annotation),
                # annotation = annotation,
                lineno = node.lineno + 1,
                col_offset = node.col_offset,
                scopes = node.scopes)
            node.target.id = new_loop_id
            ast.fix_missing_locations(new_var_assign)
            node.body.insert(0, new_var_assign)
        return node

    def visit_If(self, node: ast.If) -> Any:
        return self._generic_scope_visit(node)

    def visit_While(self, node: ast.While) -> Any:
        return self._generic_scope_visit(node)
    
    def _generic_scope_visit(self, node):
        # Save current variables out of scope
        prev = self._variables_out_of_scope
        # Set new variables
        # self._variables_out_of_scope = getattr(node, "variables_out_of_scope", {})
        body, vars = [], []
        for n in node.body:
            if targets := self._get_variables_out_of_scope(n):
                vars.extend(self._build_assignments(targets)) 
            body.append(self.visit(n))

        # Update node body
        node.body = vars + body
        # Revert variables out of scope
        self._variables_out_of_scope = prev
        return node

    def _get_variables_out_of_scope(self, node):
        # Retrieve nested variables that are out of scope
        parent = node.scopes[-2] \
            if hasattr(node, "scopes") and len(node.scopes) >= 2 \
            else None
        if not parent:
            return []
        variables_out_of_scope = getattr(parent, "variables_out_of_scope", {})
        vars = None
        node_vars = getattr(node, "vars", [])
        if nested_variables := getattr(node, "nested_variables", None):
            assert isinstance(nested_variables, dict)
            vars = list(nested_variables.values()) + node_vars
        else:
            vars = node_vars
        vars_out_of_scope = []
        for var in vars:
            if get_id(var) in variables_out_of_scope:
                v, ann = variables_out_of_scope[get_id(var)]
                vars_out_of_scope.append((var, ann))
        return vars_out_of_scope

    def _build_assignments(self, targets):
        # Retrieve assignment variables 
        assign_nodes = []
        for (target, ann) in targets:
            new_target = ast.Name(id=get_id(target)) 
            default = get_default_val(target, ann)
            assign = ast.AnnAssign(
                target = new_target,
                annotation = ann,
                value = default,
                scopes = getattr(target, "scopes", ScopeList()))
            ast.fix_missing_locations(assign)
            assign_nodes.append(assign)
        return assign_nodes


class JuliaOffsetArrayRewriter(ast.NodeTransformer):
    """Converts array calls to OffsetArray calls. It is still
    a preliminary feature"""

    SUPPORTED_OPERATIONS = set([
        "append", 
        "clear",
        "extend",
        "len",
        "range"
    ])

    def __init__(self) -> None:
        super().__init__()
        # Scoping information
        self._list_assigns = []
        self._subscript_vals = []
        self._list_assign_idxs: list[int] = []
        self._subscript_val_idxs: list[int] = []
        self._last_scopes: list[ScopeList] = []
        self._current_scope = ScopeList()
        # Flags
        self._use_offset_array = False
        self._using_offset_arrays = False
        self._is_assign_val = False

    ##########################################
    ############## Visit Scopes ##############
    ##########################################

    def visit_Module(self, node: ast.Module) -> Any:
        if getattr(node, "offset_arrays", False):
            self._using_offset_arrays = True
        self._enter_scope(node)
        self.generic_visit(node)
        self._leave_scope(node)
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        parsed_decorators: Dict[str, Dict[str, str]] = node.parsed_decorators
        if not ((OFFSET_ARRAYS in parsed_decorators) or self._using_offset_arrays):
            return node

        self._enter_scope(node)

        # Visit body 
        return_node: ast.Return = None
        self._use_offset_array = True

        body_range = None
        if isinstance(node.body[-1], ast.Return):
            return_node = node.body[-1]
            body_range = node.body[:-1]
        else:
            body_range = node.body

        body = []
        func_assingments = []
        for n in body_range:
            self.visit(n)
            if return_node:
                if isinstance(n, ast.Assign):
                    target_ids = [get_id(t) for t in n.targets]
                    if get_id(return_node.value) in target_ids:
                        func_assingments.append(n)
                        continue
                elif isinstance(n, ast.AnnAssign):
                    if get_id(n.target) == get_id(return_node.value):
                        func_assingments.append(n)
                        continue
            body.append(n)

        if return_node:
            self.visit(return_node)

        self._use_offset_array = False

        # Visit args
        let_assignments = []
        for arg in node.args.args:
            arg_id = arg.arg
            annotation_id = get_ann_repr(arg.annotation, sep=SEP)
            is_list = re.match(r"^List|^list", annotation_id)
            if not hasattr(arg, "annotation") or \
                    (hasattr(arg, "annotation") and not is_list) or \
                    arg_id not in self._subscript_vals:
                continue
            arg_name = ast.Name(id=arg_id)
            val = self._build_offset_array_call(
                        arg_name, arg.annotation, node.lineno, 
                        node.col_offset, node.scopes)
            assign = ast.Assign(
                    targets = [arg_name],
                    value = val,
                    annotation = arg.annotation, 
                    lineno = node.lineno + 1,
                    col_offset = node.col_offset,
                    scopes = node.scopes # TODO: Remove the return statement form scopes
                )
            ast.fix_missing_locations(assign)
            let_assignments.append(assign)
        
        # Construct new body
        if let_assignments:
            let_stmt = juliaAst.LetStmt(
                    args = let_assignments,
                    body = body,
                    ctx = ast.Load(),
                    lineno = node.lineno + 1,
                    col_offset = node.col_offset
                )
            node.body = []
            if func_assingments:
                node.body.extend(func_assingments)
            node.body.append(let_stmt)
            if return_node:
                node.body.append(return_node)

        # Add to decorators
        if not (OFFSET_ARRAYS in parsed_decorators) and (let_assignments or self._list_assigns):
            node.decorator_list.append(ast.Name(id="offset_arrays"))
            parsed_decorators["offset_arrays"] = {}

        self._leave_scope(node)

        return node

    def visit_For(self, node: ast.For) -> Any:
        self._last_scopes.append(self._current_scope.copy())
        self._current_scope = node.scopes
        self.generic_visit(node)
        if self._use_offset_array:
            node.iter.using_offset_arrays = True
            node.iter.require_parent = False
        self._current_scope = ScopeList(self._last_scopes.pop())
        return node

    def visit_With(self, node: ast.With) -> Any:
        self._last_scopes.append(self._current_scope.copy())
        self._current_scope = node.scopes
        self.generic_visit(node)
        self._current_scope = ScopeList(self._last_scopes.pop())
        return node

    def visit_If(self, node: ast.If) -> Any:
        self._last_scopes.append(self._current_scope.copy())
        self._current_scope = node.scopes
        self.generic_visit(node)
        self._current_scope = ScopeList(self._last_scopes.pop())
        return node

    def _enter_scope(self, node):
        self._list_assign_idxs.append(len(self._list_assigns))
        self._subscript_val_idxs.append(len(self._subscript_vals))
        self._last_scopes.append(self._current_scope.copy())
        self._current_scope = node.scopes

    def _leave_scope(self, node):
        del self._list_assigns[self._list_assign_idxs.pop():]
        del self._subscript_vals[self._subscript_val_idxs.pop():]
        self._current_scope = ScopeList(self._last_scopes.pop())

    ##########################################
    ##########################################
    ##########################################

    def visit_List(self, node: ast.List) -> Any:
        self.generic_visit(node)
        if self._use_offset_array:
            if self._is_assign_val:
                return self._build_offset_array_call(
                    node, node.annotation,  node.lineno, 
                    node.col_offset, node.scopes)
        return node

    def visit_Assign(self, node: ast.Assign) -> Any:
        for t in node.targets:
            t.require_parent = False
        ann = getattr(node.value, "annotation", None)
        if self._use_offset_array and is_list(node.value):
            for n in node.targets:
                if id := get_id(n):
                    self._list_assigns.append(id)
            node.value = self._build_offset_array_call(
                node.value, ann, node.lineno, 
                node.col_offset, node.scopes)
            self.generic_visit(node)
        else:
            self._is_assign_val = True
            self.generic_visit(node)
            self._is_assign_val = False
        return node

    def visit_AnnAssign(self, node: ast.AnnAssign) -> Any:
        node.target.require_parent = False
        if self._use_offset_array and is_list(node.value):
            # node.annotation = ast.Name(id="OffsetArray")
            self._list_assigns.append(get_id(node.target))
            node.value = self._build_offset_array_call(
                node.value, node.annotation, node.lineno, 
                node.col_offset, node.scopes)
            self.generic_visit(node)
        else:
            self._is_assign_val = True
            self.generic_visit(node)
            self._is_assign_val = False
        return node

    def visit_Subscript(self, node: ast.Subscript) -> Any:
        node.value.require_parent = False
        self.generic_visit(node)

        # Cover nested subscripts
        if isinstance(node.value, ast.Subscript):
            node.using_offset_arrays = getattr(node.value, "using_offset_arrays", False)

        container_type = getattr(node, "container_type", None)
        is_list = container_type and re.match(r"^list|List", container_type[0])
        if self._use_offset_array and (id := get_id(node.value)) and \
                is_list:
            self._subscript_vals.append(id)
            node.using_offset_arrays = True
            if isinstance(node.slice, ast.Slice):
                node.slice.using_offset_arrays = True

        return node

    def visit_Call(self, node: ast.Call) -> Any:
        # Accounts for JuliaMethodCallRewriter
        args = getattr(node, "args", None)
        if (args and get_id(args[0]) == "sys" 
                and get_id(node.func) == "argv"):
            curr_val = self._use_offset_array
            self._use_offset_array = False
            self.generic_visit(node)
            self._use_offset_array = curr_val
            return node
        if get_id(node.func) in self.SUPPORTED_OPERATIONS:
            for arg in node.args:
                arg.require_parent = False

        self.generic_visit(node)
        return node

    def visit_Name(self, node: ast.Name) -> Any:
        self.generic_visit(node)
        require_parent = getattr(node, "require_parent", True)
        if require_parent and get_id(node) in self._list_assigns:
            return self._create_parent(node, node.lineno, 
                getattr(node, "col_offset", None))
        return node

    def _build_offset_array_call(self, list_arg, annotation, lineno, col_offset, scopes):
        return ast.Call(
            func = ast.Name(id="OffsetArray", lineno=lineno, col_offset=col_offset),
            args = [list_arg, ast.Constant(value=-1, scopes=scopes)],
            keywords = [],
            annotation = annotation,
            lineno = lineno,
            col_offset = col_offset, 
            scopes = scopes)

    def _create_parent(self, node, lineno, col_offset):
        # TODO: Unreliable annotations when calling parent
        arg_id = get_id(node)
        new_annotation = getattr(self._current_scope.find(arg_id), "annotation", None)
        return ast.Call(
            func=ast.Name(id = "parent", lineno = lineno, col_offset = col_offset),
            args = [node],
            keywords = [],
            annotation = new_annotation,
            lineno = lineno,
            col_offset = col_offset if col_offset else 0,
            scopes = self._current_scope)


class JuliaModuleRewriter(ast.NodeTransformer):
    """Wraps Python's modules into Julia Modules."""
    def __init__(self) -> None:
        super().__init__()

    def visit_Module(self, node: ast.Module) -> Any:
        if getattr(node, USE_MODULES, FLAG_DEFAULTS[USE_MODULES]):
            name = node.__file__.name.split(".")[0]
            julia_module = juliaAst.JuliaModule(
                body = node.body,
                name = ast.Name(id = name),
                context = ast.Load(),
                scopes = node.scopes,
                lineno = 0,
                col_offset = 0,
                vars = getattr(node, "vars", None),
                __basedir__ = getattr(node, "__basedir__", None)
            )
            ast.fix_missing_locations(julia_module)

            # Populate remaining fields
            copy_attributes(node, julia_module)

            return julia_module
        return node


###########################################################
######################### ctypes ##########################
###########################################################

class JuliaCtypesRewriter(ast.NodeTransformer):
    """Translate ctypes to Julia. Must run before JuliaClassWrapper and 
    JuliaMethodCallRewriter"""

    CTYPES_CONVERSION_MAP = {
        "int": "Cint",
        "float": "Cfloat",
        "bool": "Cbool",
    }
    # The idea is to keep the ctypes module in pyjl/external/modules isolated
    # from any rewriters
    CTYPES_LIST = [
        ctypes.c_int, ctypes.c_int8, ctypes.c_int16, ctypes.c_int32,
        ctypes.c_int64, ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32,
        ctypes.c_uint64, ctypes.c_bool, ctypes.c_float, ctypes.c_double,
        ctypes.c_short, ctypes.c_ushort, ctypes.c_long, ctypes.c_ulong,
        ctypes.c_longlong, ctypes.c_ulonglong, ctypes.c_longdouble,
        ctypes.c_byte, ctypes.c_ubyte, ctypes.c_char, ctypes.c_size_t,
        ctypes.c_ssize_t, ctypes.c_char_p, ctypes.c_wchar_p, ctypes.c_void_p,
    ]

    # SPECIAL_CALLS = {
    #     "ctypes.WINFUNCTYPE"
    # }

    NONE_TYPES = {"c_void_p", "HANDLE", "HMODULE"}

    WRAP_TYPES = {
        "c_void_p": lambda arg: f"Ptr[Cvoid]({arg})", # if isa({arg}, Union[int, None]) else Ref[Cvoid]({arg})
        "HANDLE": lambda arg: f"Ptr[Cvoid]({arg})", 
        "HMODULE": lambda arg: f"Ptr[Cvoid]({arg})",
        # "LPCWSTR": lambda arg: f"transcode(Cwchar_t, {arg})",
    }

    def __init__(self) -> None:
        super().__init__()
        self._module = None
        self._imported_names = {}
        # Mapped dll calls as: {module_name: {named_func: {argtypes: [], restype: <return_type>}}
        self._ext_modules = {}
        # Mapps factory functions to their respective types
        self._factory_funcs = {}
        # Temporarily holds factory function types from calls
        self._ctypes_func_types = None
        # Mapps assignment target id's to ctypes call types
        self._assign_ctypes_funcs = {}
        # Mapps special assignment target ids to their respective values
    
    def visit_Module(self, node: ast.Module) -> Any:
        self._imported_names = getattr(node, "imported_names", None)
        self._use_modules = getattr(node, USE_MODULES, 
            FLAG_DEFAULTS[USE_MODULES])
        # Get current module name
        filename = getattr(node, "__file__", None)
        if filename:
            self._module = Path(filename).stem
        self.generic_visit(node)
        return node

    def visit_Assign(self, node: ast.Assign) -> Any:
        target = node.targets[0]
        return self._ctypes_assign_visit(node, target)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> Any:
        return self._ctypes_assign_visit(node, node.target)

    def _ctypes_assign_visit(self, node, target) -> Any:
        self.generic_visit(node)

        if self._ctypes_func_types:
            # If there are any types from a call to ctypes
            self._assign_ctypes_funcs[get_id(target)] = self._ctypes_func_types
            self._ctypes_func_types = None

        # Check if the target is what we are looking for
        admissible_args = re.match(r".*argtypes$|.*restype$|.*errcheck$", get_id(target)) \
            if get_id(target) else False
        t_node = None
        if isinstance(target, ast.Attribute):
            attr_lst = get_id(target).split(".")
            if attr_lst[0] == "self":
                class_node = find_node_by_type(ast.ClassDef, node.scopes)
                t_node = class_node.scopes.find(attr_lst[1])
            else:
                t_node = node.scopes.find(attr_lst[0])
        else:
            t_node = target
        annotation = getattr(t_node, "annotation", None)
        if not annotation or \
                not admissible_args:
            return node

        if get_id(annotation) in {"ctypes.CDLL", "CDLL", 
                    "ctypes.WinDLL","WinDLL"} and \
                isinstance(target, ast.Attribute) and \
                isinstance(target.value, ast.Attribute):
            # Adding information about modules and named functions
            module_name = None
            if isinstance(target.value.value, ast.Attribute) and \
                    get_id(target.value.value.value) == "self":
                module_name = target.value.value.attr
            elif isinstance(target.value.value, ast.Name):
                module_name = get_id(target.value.value)
            else:
                return node
            named_func = target.value.attr
            attr = target.attr

            if module_name not in self._ext_modules:
                self._ext_modules[module_name] = {named_func: {attr: node.value}}
            else:
                if named_func not in self._ext_modules[module_name]:
                    self._ext_modules[module_name][named_func] = {attr: node.value}
                else:
                    if attr not in self._ext_modules[module_name][named_func]:
                        self._ext_modules[module_name][named_func][attr] = node.value

            # We want to discard such nodes, as they are not used in Julia
            return None

        # Otherwise, just return the node
        return node


    def visit_Call(self, node: ast.Call) -> Any:
        node.func.is_call_func = True
        self.generic_visit(node)

        func = node.func
        mod_name = ""
        # Retrieve module name
        if (m_name := get_id(node.func).split(".")[0]) in self._imported_names:
            imp = self._imported_names[m_name]
            if isinstance(imp, tuple):
                mod_name = imp[0]
            elif hasattr(imp, "__name__"):
                mod_name = imp.__name__

        # Handle module calls
        if isinstance(node.func, ast.Attribute) and mod_name:
            if isinstance(node.func.value, ast.Attribute):
                func = ast.Attribute(
                    value = ast.Name(id=node.func.value.attr),
                    attr = node.func.attr)
            else:
                func = ast.Name(id=node.func.attr)

        # Ignore calls that Load the library
        if class_for_typename(get_id(func), None, self._imported_names) \
                is ctypes.cdll.LoadLibrary:
            return node

        # Prepare args
        ccall_func, argtypes, restype, errcheck = None, None, None, None
        is_factory = False
        module_name = None
        dll_node = None
        if isinstance(func, ast.Attribute):
            # Check if call is to a dll
            module_name = None
            if isinstance(func.value, ast.Name):
                dll_node = node.scopes.find(get_id(func.value))
                module_name = get_id(func.value)
            elif isinstance(func.value, ast.Attribute) and \
                    get_id(func.value.value) == "self":
                class_node = find_node_by_type(ast.ClassDef, node.scopes)
                dll_node = class_node.scopes.find(func.value.attr)
                module_name = func.value.attr

        if get_id(getattr(dll_node, "annotation", None)) in {"ctypes.CDLL", "CDLL", 
                "ctypes.WinDLL","WinDLL"}:
            # Attempt to use the stored information
            named_func = func.attr
            if module_name in self._ext_modules and \
                    named_func in self._ext_modules[module_name]:
                args = self._ext_modules[module_name][named_func]
                (argtypes, restype, errcheck) = self._parse_args(node, args)
            # Insert call to Libdl.dlsym
            ccall_func = ast.Call(
                func = self._build_libdl_call(node.scopes),
                args = [ast.Name(id=module_name), juliaAst.Symbol(id=named_func)],
                keywords = [],
                scopes = node.scopes)
        elif get_id(getattr(node.scopes.find(get_id(func)), "returns", None)) in \
                {"ctypes._NamedFuncPointer", "_NamedFuncPointer"}:
            # Detected factory function that populates named functions with fields
            func_factory = None
            if self._use_modules and \
                    (id := f"{mod_name}.{get_id(func)}") in self._factory_funcs:
                func_factory = self._factory_funcs[id]
            elif (id := get_id(func)) in self._factory_funcs:
                func_factory = self._factory_funcs[id]
            else:
                return node
            # Retrieve args
            get_field = lambda x: node.args[func_factory[x]] \
                if func_factory[x] < len(node.args) \
                else None
            ccall_func = get_field(0)
            argtypes = get_field(1)
            if argtypes and hasattr(argtypes, "elts"):
                argtypes = ast.Tuple(elts = argtypes.elts)
            restype = get_field(2)
            errcheck = get_field(3)
            is_factory = True
        else:
            return node

        if ccall_func is not None and argtypes is not None:
            # Fill in remaining fields
            # lineno and col_offset added for debugging
            if not hasattr(ccall_func, "lineno"):
                ccall_func.lineno = node.lineno 
            if not hasattr(ccall_func, "col_offset"):
                ccall_func.col_offset = node.col_offset
            ccall_func.scopes = node.scopes
            ccall_func.in_ccall = True
            ast.fix_missing_locations(ccall_func)
            # Handle cases where there is no return type 
            if restype is None:
                restype = ast.Name(id="Cvoid")
            if isinstance(argtypes, ast.Constant) and \
                    argtypes.value is None:
                argtypes = ast.Tuple(elts = [argtypes])
            # Replace unwanted argtypes with Ptr{void}
            ptr_node = self._make_ptr("Cvoid")
            replace_cond = lambda x: get_id(getattr(x, "annotation", None)) in \
                {"PyObject", "ctypes._FuncPointer", "_FuncPointer", "ctypes.POINTER"}
            # Save old argument types
            old_argtypes = argtypes.elts
            argtypes.elts = list(map(lambda x: ptr_node if replace_cond(x) else x, argtypes.elts))
            # Set all as annotation
            for arg in argtypes.elts:
                arg.is_annotation = True
            restype.is_annotation = True
            # Aggregate factory_func_types
            self._ctypes_func_types = [restype] + argtypes.elts
            # Create call to Julia's ccall
            ccall = ast.Call(
                func = ast.Name(id="ccall"),
                args = [],
                keywords = node.keywords,
                lineno = node.lineno + 1,
                col_offset = node.col_offset,
                scopes = node.scopes,
                no_rewrite = True, # Special attribute not to rewite call
            )
            if sys.platform.startswith('win32'):
                # Add calling convention for windows
                stdcall = ast.Name(id="stdcall")
                ast.fix_missing_locations(stdcall)
                ccall.args.extend([ccall_func, stdcall, restype, argtypes])
            else:
                ccall.args.extend([ccall_func, restype, argtypes])
            ccall_assign = ast.Assign(
                targets=[ast.Name(id="res")], value=ccall)
            # Build error check call
            errcheck_call: ast.Call = self._build_errcheck_call(node, errcheck, 
                ccall_func, restype) if errcheck else None
            func_name = get_id(ccall_func.args[1]) \
                if isinstance(ccall_func, ast.Call) \
                else get_id(ccall_func)
            errcheck_func = juliaAst.InlineFunction(
                name=func_name, 
                args = ast.arguments(args = [], defaults = [],
                    posonlyargs = [], kwonlyargs=[],
                    lineno = 0, col_offset = 0),
                body=[ccall_func])
            if is_factory:
                # If it is a factory, build a lamdba expression
                var_list: list[str] = [f"a{i}" for i in range(len(argtypes.elts))]
                args = ast.arguments(args=[ast.arg(arg=var) for var in var_list], defaults=[])
                annotations = list(map(lambda x: getattr(x, "annotation", None), old_argtypes))
                for var, typ, ann in zip(var_list, argtypes.elts, annotations):
                    mapped_arg = None
                    if get_id(typ) in self.WRAP_TYPES:
                        mapped_arg = self.WRAP_TYPES[get_id(typ)](var)
                    # elif get_id(ann) in {"ctypes._FuncPointer", "_FuncPointer"}:
                    #     mapped_arg = f"Ptr[Cvoid]({var})"
                    if mapped_arg:
                        arg_node = cast(ast.Expr, create_ast_node(mapped_arg)).value
                        fill_attributes(arg_node, node.scopes, no_rewrite=True, 
                            preserve_keyword=True, is_annotation=True)
                        ccall.args.append(arg_node)
                    else:
                        ccall.args.append(ast.Name(id=var))
                if errcheck_call:
                    # Pass the arguments to the errcheck function 
                    errcheck_call.args.append(
                        ast.Tuple(elts=[ast.Name(id=var) for var in var_list]))
                    ret_stmt = ast.Return(value=errcheck_call)
                    ccall_builder = juliaAst.JuliaLambda(
                        name="",
                        args = args,
                        body=[ccall_assign, errcheck_func, ret_stmt],
                        scopes = node.scopes)
                    ast.fix_missing_locations(ccall_builder)
                    return ccall_builder
                else:
                    ccall_builder = ast.Lambda(args = args, body=ccall)
                    ast.fix_missing_locations(ccall_builder)
                    return ccall_builder
            else:
                # Assign the respective arguments
                ccall.args.extend(node.args)
                # Build call depending on error check
                if errcheck_call:
                    # Pass the arguments to the errcheck function
                    errcheck_call.args.append(ast.Tuple(elts=node.args))
                    # TODO: ccall with error check not yet supported with 
                    # non-factory expressions
                    return ccall
                else:
                    return ccall

        return node

    def _make_ptr(self, ptr_type: str):
        ptr_node = ast.Subscript(value = ast.Name(id="Ptr"),
            slice=ast.Name(id=ptr_type), is_annotation = True)
        ast.fix_missing_locations(ptr_node)
        return ptr_node

    def _build_errcheck_call(self, node, errcheck, ccall_func: ast.Call, restype) -> ast.Call:
        """Builds the call to the errcheck function"""
        errcheck_call = errcheck
        if isinstance(errcheck, (ast.Name, ast.Attribute)):
            # Follow errcheck calling conventions 
            # https://docs.python.org/3/library/ctypes.html#ctypes._FuncPtr.errcheck
            # Build error check call
            func_name = get_id(ccall_func.args[1])
            res_var = ast.Name(id="res")
            res = None
            if get_id(restype) in self.NONE_TYPES:
                # If call returns c_void_p, we need to check if 
                # the returned value is C_NULL
                res = ast.IfExp(
                    test = ast.Compare(
                        left = res_var,
                        ops = [ast.Eq()],
                        comparators = [ast.Name(id="C_NULL", preserve_keyword = True)]
                    ),
                    body = ast.Constant(value=None),
                    orelse = res_var)
            else:
                res = res_var
            errcheck_call = ast.Call(
                func = errcheck,
                args = [res, ast.Name(id=func_name, preserve_keyword=True)], 
                keywords = [], 
                scopes = getattr(node, "scopes", ScopeList()))
        ast.fix_missing_locations(errcheck_call)
        return errcheck_call

    def _parse_args(self, node, args):
        argtypes = None
        restype = None
        errcheck = None
        # Attempt to get ccall fields
        if "argtypes" in args:
            t_argtypes = args["argtypes"]
            # Elements are all annotations
            if isinstance(t_argtypes, ast.List):
                argtypes = ast.Tuple(elts = t_argtypes.elts)
            elif isinstance(t_argtypes, ast.Tuple):
                argtypes = t_argtypes
            else:
                argtypes = ast.Tuple(elts = [t_argtypes])
        if "restype" in args:
            restype = args["restype"]
        # Assign defaults if necessary
        if not argtypes:
            # Try to get the types from type casts or from 
            # type annotations
            argtypes_lst = []
            for arg in node.args:
                if getattr(arg, "annotation", None):
                    if (id := get_id(arg.annotation)) in self.CTYPES_CONVERSION_MAP:
                        converted_type = self.CTYPES_CONVERSION_MAP[id]
                        ann = create_ast_node(converted_type, node)
                        ann = ann.value if isinstance(ann, ast.Expr) else ann
                        ann.scopes = node.scopes
                        argtypes_lst.append(ann)
                    else:
                        argtypes_lst.append(arg.annotation)
                elif isinstance(arg, ast.Call) and \
                        class_for_typename(get_id(arg.func), None, 
                            self._imported_names) in self.CTYPES_LIST:
                    argtypes_lst.append(arg.func)
                elif isinstance(arg, ast.Call) and \
                        get_id(arg.func) == "ctypes.cast":
                    argtypes_lst.append(arg.args[1])
                else:
                    argtypes_lst.append(ast.Subscript(
                        value = ast.Name(id="Ptr"),
                        slice=ast.Name(id="Cvoid"),
                        lineno=node.lineno,
                        col_offset=node.col_offset))

            argtypes = ast.Tuple(elts = argtypes_lst)
            
        if not restype:
            restype = ast.Name(id="Cvoid")
        
        ast.fix_missing_locations(restype)
        ast.fix_missing_locations(argtypes)

        return (argtypes, restype, errcheck)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        self.generic_visit(node)
        # Initialize position variables
        func_ptr = None
        argtypes = None
        restype = None
        errcheck = None
        named_func_ptr_types = {"ctypes._NamedFuncPointer", "_NamedFuncPointer"}
        # Identify factory functions that populate function pointers
        for i in range(len(node.args.args)):
            arg = node.args.args[i]
            ann = get_ann_repr(getattr(arg, "annotation", None))
            if ann in named_func_ptr_types:
                func_ptr = i
            elif ann in {"list[_CData]", "Optional[list[_CData]]"}:
                argtypes = i
            elif ann in {"bool", "Optional[bool]"}:
                errcheck = i
            elif ann in {"_Cdata", "Optional[_CData]"}:
                restype = i
        return_type = get_ann_repr(node.returns)
        if return_type in named_func_ptr_types and \
                func_ptr is not None and argtypes is not None and restype is not None:
            val = (func_ptr, argtypes, restype, errcheck)
            if self._use_modules:
                self._factory_funcs[f"{self._module}.{node.name}"] = val
            else:
                self._factory_funcs[node.name] = val
            return None

        return node

    def visit_Attribute(self, node: ast.Attribute) -> Any:
        node.value.is_attr = True
        self.generic_visit(node)
        # Avoid rewriting calls
        if getattr(node, "is_call_func", False):
            return node
        # Translate calls to WinDLL (These return _NamedFuncPointer, 
        # which is not supported in Julia)
        if isinstance(node.value, ast.Attribute) and \
                get_id(getattr(node.value, "annotation", None)) \
                    in {"WinDLL", "ctypes.WinDLL"}:
            libdl_call = ast.Call(
                func = self._build_libdl_call(node.scopes),
                args = [node.value, juliaAst.Symbol(id = node.attr)],
                keywords = [],
                scopes = node.scopes,
                no_rewrite = True)
            ast.fix_missing_locations(libdl_call)
            return libdl_call
        return node

    def _build_libdl_call(self, scopes):
        libdl_call = ast.Attribute(
            value = ast.Name(id = "Libdl"),
            attr = "dlsym",
            ctx = ast.Load(),
            scopes = scopes)
        ast.fix_missing_locations(libdl_call)
        return libdl_call
    
    def visit_Import(self, node: ast.Import) -> Any:
        self._remove_deleted_funcs(node)
        return node
    
    def visit_ImportFrom(self, node: ast.ImportFrom) -> Any:
        mod = node.module.split(".")[-1] if node.module else ""
        self._remove_deleted_funcs(node, mod)
        return node

    def _remove_deleted_funcs(self, node, module=None):
        remove_del_funcs = lambda x: f"{module}.{x.name}" not in self._factory_funcs \
            if module else x.name not in self._factory_funcs
        node.names = list(filter(remove_del_funcs, node.names))


class JuliaCtypesCallbackRewriter(ast.NodeTransformer):
    CONVERSION_MAP = {
        "BOOL": "Clong",
    }

    def __init__(self) -> None:
        super().__init__()
        self._is_callback = False
        self._restype = None


    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        if not getattr(node, "_is_callback", False):
            self.generic_visit(node)
            return node
        # Handle callback functions
        self._is_callback = True
        self._restype = getattr(node, "restype", None)
        # Visit the function
        self.generic_visit(node)
        self._is_callback = False
        self._restype = None
        # body.extend(node.body)
        # node.body = body
        node.returns = None # Returns are unreliable, as functions are used as callbacks
        return node


    def visit_Return(self, node: ast.Return) -> Any:
        self.generic_visit(node)
        if self._is_callback:
            return_node_ann = get_ann_repr(getattr(node.value, "annotation", None))
            if not return_node_ann and self._restype:
                return_node_ann = get_ann_repr(self._restype)
            if return_node_ann in self.CONVERSION_MAP:
                conv_type = ast.Name(id=self.CONVERSION_MAP[return_node_ann], preserve_keyword=True)
                # If we can convert it, then 
                node.value = ast.Call(
                    func = ast.Attribute(
                        value=ast.Name(id="Base", preserve_keyword = True),
                        attr = "cconvert",
                        scopes = node.value.scopes, ctx = ast.Load(),
                        preserve_keyword = True),
                    args = [conv_type,node.value],
                    keywords = [],
                    scopes = node.value.scopes,
                    no_rewrite = True)
                fill_attributes(node.value, node.value.scopes)
        return node


    def visit_Call(self, node: ast.Call) -> Any:
        self.generic_visit(node)
        if self._is_callback:
            func_node = node.scopes.find(get_id(node.func))
            if isinstance(func_node, ast.FunctionDef):
                func_args = func_node.args.args
                for i in range(len(func_args)):
                    f_arg = func_args[i]
                    if get_id(f_arg.annotation) == "ctypes.c_uint64":
                        # Convert type to uint
                        node.args[i] = ast.Call(
                            func = ast.Name("UInt64", preserve_keyword=True),
                            args = [node.args[i]],
                            keywords = [],
                            scopes = node.scopes)
                        ast.fix_missing_locations(node.args[i])
        return node


###########################################################
##################### Argument Parser #####################
###########################################################

class JuliaArgumentParserRewriter(ast.NodeTransformer):
    def __init__(self) -> None:
        super().__init__()
        # Maps {arg_settings_inst: {
        #       "-arg_name"
        #           arg_type = Int
        #           default = 0
        #       ...
        #   }
        self._args = {}

    def visit_Module(self, node: ast.Module) -> Any:
        self._args = {}
        body = []
        for n in node.body:
            n = self.visit(n)
            if n:
                body.append(n)
        for (arg_settings_inst, (lineno, arg_vals)) in self._args.items():
            arg_node = juliaAst.Block(
                name = arg_settings_inst,
                body = arg_vals,
                vars = [],
                decorator_list = [ast.Name(id = "add_arg_table", ctx=ast.Load())],
                scopes = ScopeList(),
                block_type = "named",
            )
            ast.fix_missing_locations(arg_node)
            idx = 0
            for i in range(len(node.body)):
                n = node.body[i]
                if n.lineno > lineno:
                    idx = i
                    break
            # Insert is innefficient. However, there should only be one call
            # to ArgumentParser
            body.insert(idx, arg_node)
        node.body = body
        return node

    def visit_Assign(self, node: ast.Assign) -> Any:
        if isinstance(node.value, ast.Call) and \
                get_id(node.value.func) == "argparse.ArgumentParser":
            # Avoid visiting the call that sets up the ArgumentParser instance
            self._args[get_id(node.targets[0])] = (node.lineno, [])
        else:
            node = self.generic_visit(node)
        return node

    def visit_Expr(self, node: ast.Expr) -> Any:
        node = self.generic_visit(node)
        if not hasattr(node, "value"):
            return None 
        if node.value == None:
            return None
        return node

    def visit_Call(self, node: ast.Call) -> Any:
        if isinstance(node.func, ast.Attribute) and \
                isinstance(node.func.value, ast.Name) and \
                node.func.attr == "add_argument":
            arg_settings_inst = get_id(node.func.value)
            argparse_node = node.scopes.find(arg_settings_inst)
            if get_id(getattr(argparse_node, "annotation", None)) == \
                    "argparse.ArgumentParser":
                if arg_settings_inst in self._args:
                    self._args[arg_settings_inst][1].append(node.args[0])
                    for keyword in node.keywords:
                        self._args[arg_settings_inst][1].append(
                            ast.Assign(
                                targets=[ast.Name(id=keyword.arg)],
                                value = keyword.value, 
                                lineno = node.lineno,
                                col_offset = node.col_offset,
                                scopes = node.scopes))
                return None
        return node


###########################################################
#################### Context Managers #####################
###########################################################

class JuliaContextManagerRewriter(ast.NodeTransformer):
    """Rewrites calls to context manager nodes. This rewriter 
    assumes the use of the DataTypesBasic package """
    def __init__(self) -> None:
        super().__init__()

    def visit_Call(self, node: ast.Call) -> Any:
        self.generic_visit(node)
        func_node = node.scopes.find(get_id(node.func))
        if isinstance(func_node, ast.FunctionDef) and \
                "contextlib.contextmanager" in func_node.parsed_decorators:
            return self._build_run_call(node)
        return node
    
    def _build_run_call(self, node):
        run_call = ast.Call(
            func = ast.Name(id="run"),
            args = [node], keywords = [],
            scopes = node.scopes,
        )
        ast.fix_missing_locations(run_call)
        return run_call

class JuliaExceptionRewriter(ast.NodeTransformer):
    ERROR_FUNCTIONS = {
        "WindowsError": ["function", "winerror"] # "strerror"
    }

    def __init__(self) -> None:
        super().__init__()
        # Mapps exception calls as: {node_name: 
        #   {err_name: <err_name>, winerror: <err>, function_: <func>, strerror: <str_err>}}
        self._exceptions: dict[str, Any] = {}

    def visit_Module(self, node: ast.Module) -> Any:
        self._exceptions = {}
        self.generic_visit(node)
        return node

    def visit_Name(self, node: ast.Name) -> Any:
        node_id = node.id
        if node_id in self._exceptions and \
                not getattr(node, "is_attr", None):
            exception = self._exceptions[node_id]
            err_name = exception["err_name"]
            # We only add functions that are in ERROR_FUNCTIONS, 
            # so no need to check if key is in dictionary
            args = []
            for arg in self.ERROR_FUNCTIONS[err_name]:
                exc = exception[arg]
                if exc:
                    args.append(exc)
            if len(args) == len(self.ERROR_FUNCTIONS[err_name]):
                exception_call = ast.Call(
                    func=ast.Name(id = err_name), 
                    args = args,
                    keywords = [],
                    scopes = getattr(node, "scopes", ScopeList()))
                ast.fix_missing_locations(exception_call)
                return exception_call
        return node

    def visit_Assign(self, node: ast.Assign) -> Any:
        target = node.targets[0]
        return self._generic_assign_visit(node, target)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> Any:
        return self._generic_assign_visit(node, node.target)

    def _generic_assign_visit(self, node, target):
        # Error functions
        if isinstance(node.value, ast.Call) and \
                get_id(node.value.func) in self.ERROR_FUNCTIONS:
            if isinstance(target, ast.Name):
                err_name = get_id(node.value.func)
                self._exceptions[get_id(target)] = \
                    {"err_name": err_name}
                for arg in self.ERROR_FUNCTIONS[err_name]:
                    self._exceptions[get_id(target)][arg] = None
                return None
        elif isinstance(target, ast.Attribute) and \
                get_id(target.value) in self._exceptions:
            if target.attr in self._exceptions[get_id(target.value)]:
                self._exceptions[get_id(target.value)][target.attr] = node.value
            return None

        return node

    def visit_Expr(self, node: ast.Expr) -> Any:
        self.generic_visit(node)
        if not getattr(node, "value", None):
            return None
        return node

    def visit_Call(self, node: ast.Call) -> Any:
        # Avoid calls to Exception constructor
        parent = node.scopes[-1] \
            if len(node.scopes) >= 1 else None
        if get_id(node.func) == "Exception" and \
                isinstance(parent, ast.FunctionDef) and \
                parent.name == "__init__":
            return None
        self.generic_visit(node)
        return node

class JuliaUnittestRewriter(ast.NodeTransformer):
    def __init__(self) -> None:
        super().__init__()
        self._is_pytest = False

    def visit_With(self, node: ast.With) -> Any:
        # Rewrites with statements with pytest.raises
        ctx = node.items[0].context_expr
        opt = node.items[0].optional_vars
        if isinstance(ctx, ast.Call) and \
                get_id(ctx.func) == "pytest.raises":
            block = juliaAst.Block(
                name = "",
                block_expr = ctx,
                body = node.body,
                vars = [],
                decorator_list = [],
                scopes = ScopeList(),
                parsed_decorators = [],
                block_type = "expression_block",
            )
            ast.fix_missing_locations(block)
            return block
        elif isinstance(ctx, ast.Call) and \
                get_id(ctx.func) == "requests_mock.mock":
            let = juliaAst.LetStmt(
                args = [ast.Assign(
                    targets=[opt],
                    value = ctx)],
                body = node.body,
                ctx = ast.Load(),
            )
            ast.fix_missing_locations(let)
            return let
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        decorators = getattr(node, "parsed_decorators", [])
        if "pytest.mark.parametrize" in decorators:
            self._is_pytest = True
            body = []
            for n in node.body:
                body.append(self.visit(n))
            node.body = body
            self._is_pytest = False
            return node
        self.generic_visit(node)
        return node

    def visit_Assert(self, node: ast.Assert) -> Any:
        self.generic_visit(node)
        if self._is_pytest:
            # Change assert calls for tests
            test_call = ast.Call(
                func = ast.Name(id="@test"),
                args = [node.test],
                keywords = [],
                scopes = getattr(node, "scopes", ScopeList())
            )
            ast.fix_missing_locations(test_call)
            return test_call
        return node
