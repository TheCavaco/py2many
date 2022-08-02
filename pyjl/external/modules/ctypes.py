import ast
from builtins import WindowsError
import ctypes
from typing import Callable, Dict, Tuple, Union

FuncType = Union[Callable, str]

class JuliaExternalModulePlugins():
    def visit_load_library(self, node, vargs):
        self._usings.add("Libdl")
        return f"Libdl.dlopen({vargs[0]})" if vargs else "Libdl.dlopen"

    def visit_cast(self, node, vargs):
        self._usings.add("Libdl")
        return f"cconvert({vargs[0]}, {self._map_type(vargs[1])})"

    def visit_wintypes(self, node, vargs):
        self._usings.add("WinTypes")
        return "WinTypes"

    def visit_create_unicode_buffer(self, node, vargs):
        # TODO: Change to ccall
        JuliaExternalModulePlugins._pycall_import(self, node, "ctypes")
    
    def _pycall_import(self, node: ast.Call, mod_name: str):
        self._usings.add("PyCall")
        import_stmt = f'{mod_name} = pyimport("{mod_name}")'
        self._globals.add(import_stmt)


FUNC_DISPATCH_TABLE: Dict[FuncType, Tuple[Callable, bool]] = {
    ctypes.cdll.LoadLibrary: (JuliaExternalModulePlugins.visit_load_library, True),
    ctypes.CDLL: (JuliaExternalModulePlugins.visit_load_library, True),
    ctypes.cast: (JuliaExternalModulePlugins.visit_cast, True),
    ctypes.byref: (lambda self, node, vargs: f"pointer_from_objref({vargs[0]})", True),
    ctypes.create_unicode_buffer: (JuliaExternalModulePlugins.visit_create_unicode_buffer, True), # TODO: Calling ctypes 
    ctypes.memset: (lambda self, node, vargs: 
        f"ccall('memset', Ptr{{Cvoid}}, (Ptr{{Cvoid}}, Cint, Csize_t), {vargs[0]}, {vargs[1]}, {vargs[2]})", True),
    ctypes.POINTER: (lambda self, node, vargs: f"pointer({', '.join(vargs)})", True),
    # Hard to map
    ctypes.pythonapi: (lambda self, node, vargs: f"", True),
    # Windows-specific
    ctypes.WINFUNCTYPE: (JuliaExternalModulePlugins.visit_wintypes, True),
    ctypes.WinDLL: (JuliaExternalModulePlugins.visit_load_library, True),
    ctypes.wintypes: (JuliaExternalModulePlugins.visit_wintypes, True),
    WindowsError: (lambda self, node, vargs: f"windowserror({', '.join(vargs)})", True),
}

EXTERNAL_TYPE_MAP = {
    ctypes.c_int: "Cint",
    ctypes.c_int8: "Cint",
    ctypes.c_int16: "Cint",
    ctypes.c_int32: "Cint",
    ctypes.c_int64: "Cint",
    ctypes.c_uint8: "Cuint",
    ctypes.c_uint16: "Cuint",
    ctypes.c_uint32: "Cuint",
    ctypes.c_uint64: "Cuint",
    ctypes.c_bool: "Cbool",
    ctypes.c_float: "Cfloat",
    ctypes.c_double: "Cdouble",
    ctypes.c_short: "Cshort",
    ctypes.c_ushort: "Cushort",
    ctypes.c_long: "Clong",
    ctypes.c_ulong: "Culong",
    ctypes.c_longlong: "Clonglong",
    ctypes.c_ulonglong: "Culonglong",
    # ctypes.c_longdouble: "", # No mapping
    ctypes.c_byte: "Cuint", # TODO: Check this
    ctypes.c_ubyte: "Cuint", # TODO: Check this
    ctypes.c_char: "Cchar",
    ctypes.c_size_t: "Csize_t",
    ctypes.c_ssize_t: "Cssize_t",
    # Pointers
    ctypes.c_char_p: "Ptr{Cchar}",
    ctypes.c_wchar_p: "Ptr{Cwchar_t}",
    ctypes.c_void_p: "Ptr{Cvoid}",
    ctypes.CDLL: "", # TODO: Temporary
}


FUNC_TYPE_MAP = {
    ctypes.cdll.LoadLibrary: lambda self, node, vargs: "ctypes.CDLL",
    ctypes.CDLL: lambda self, node, vargs: "ctypes.CDLL",
    # Why invalid syntax???
    ctypes.cast: lambda self, node, vargs: ast.unparse(vargs[1]) if vargs else "ctypes.cast",
}
