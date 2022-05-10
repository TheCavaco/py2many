module ietoolbar
#= 
This sample implements a simple IE Toolbar COM server
supporting Windows XP styles and access to
the IWebBrowser2 interface.

It also demonstrates how to hijack the parent window
to catch WM_COMMAND messages.
 =#
using Printf
using PyCall
win32ui = pyimport("win32ui")
pythoncom = pyimport("pythoncom")
import win32com.server.register

using win32com: universal
using win32com.client: gencache, DispatchWithEvents, Dispatch
using win32com.client: constants, getevents
import win32com

import winreg
using win32com.shell: shell
using win32com.shell.shellcon: *
using win32com.axcontrol: axcontrol
try
    import winxpgui as win32gui
catch exn
    import win32gui
end

import win32con
import commctrl

EnsureModule(win32com.client.gencache, "{EAB22AC0-30C1-11CF-A7EB-0000C05BAE0B}", 0, 1, 1)
abstract type AbstractWIN32STRUCT end
abstract type AbstractTBBUTTON <: AbstractWIN32STRUCT end
abstract type AbstractStub end
abstract type AbstractIEToolbarCtrl end
abstract type AbstractIEToolbar end
IDeskBand_methods = ["GetBandInfo"]
IDockingWindow_methods = ["ShowDW", "CloseDW", "ResizeBorderDW"]
IOleWindow_methods = ["GetWindow", "ContextSensitiveHelp"]
IInputObject_methods = ["UIActivateIO", "HasFocusIO", "TranslateAcceleratorIO"]
IObjectWithSite_methods = ["SetSite", "GetSite"]
IPersistStream_methods = ["GetClassID", "IsDirty", "Load", "Save", "GetSizeMax"]
_ietoolbar_methods_ = append!(
    append!(
        append!(
            append!(append!(IDeskBand_methods, IDockingWindow_methods), IOleWindow_methods),
            IInputObject_methods,
        ),
        IObjectWithSite_methods,
    ),
    IPersistStream_methods,
)
_ietoolbar_com_interfaces_ = [
    shell.IID_IDeskBand,
    axcontrol.IID_IObjectWithSite,
    IID_IPersistStream(pythoncom),
    axcontrol.IID_IOleCommandTarget,
]
mutable struct WIN32STRUCT <: AbstractWIN32STRUCT
    __dict__::Any
    _buffs::Any
    _struct_items_::Any
    full_fmt::String

    WIN32STRUCT(full_fmt::String = "") = begin
        for (name, fmt, default) in self._struct_items_
            self.__dict__[name] = nothing
            if fmt == "z"
                full_fmt += "pi"
            else
                full_fmt += fmt
            end
        end
        for (name, val) in kw.items()
            self.__dict__[name] = val
        end
        new(full_fmt)
    end
end
function __setattr__(self::WIN32STRUCT, attr, val)
    if !startswith(attr, "_") && attr
        not in self.__dict__
        throw(AttributeError(attr))
    end
    self.__dict__[attr] = val
end

function toparam(self::WIN32STRUCT)
    self._buffs = []
    full_fmt = ""
    vals = []
    for (name, fmt, default) in self._struct_items_
        val = self.__dict__[name]
        if fmt == "z"
            fmt = "Pi"
            if val === nothing
                push!(vals, 0)
                push!(vals, 0)
            else
                str_buf = array(array, "c", val + " ")
                push!(vals, buffer_info(str_buf)[1])
                push!(vals, length(val))
                append(self._buffs, str_buf)
            end
        else
            if val === nothing
                val = default
            end
            push!(vals, val)
        end
        full_fmt += fmt
    end
    return pack(struct_, (full_fmt,) + tuple(vals)...)
end

mutable struct TBBUTTON <: AbstractTBBUTTON
    _struct_items_::Vector{Tuple}

    TBBUTTON(
        _struct_items_::Vector{Tuple} = [
            ("iBitmap", "i", 0),
            ("idCommand", "i", 0),
            ("fsState", "B", 0),
            ("fsStyle", "B", 0),
            ("bReserved", "H", 0),
            ("dwData", "I", 0),
            ("iString", "z", nothing),
        ],
    ) = new(_struct_items_)
end

mutable struct Stub <: AbstractStub
    #= 
        this class serves as a method stub,
        outputting debug info whenever the object
        is being called.
         =#
    name::Any
end
function __call__(self::Stub)
    println("STUB: ", self.name, args)
end

mutable struct IEToolbarCtrl <: AbstractIEToolbarCtrl
    #= 
        a tiny wrapper for our winapi-based
        toolbar control implementation.
         =#
    hwnd::Any
    styles::Any

    IEToolbarCtrl(
        hwndparent,
        hwnd = CreateWindow(
            win32gui,
            "ToolbarWindow32",
            nothing,
            styles,
            0,
            0,
            100,
            100,
            hwndparent,
            0,
            win32gui.dllhandle,
            nothing,
        ),
        styles = (
            (
                (
                    (
                        (
                            (
                                (
                                    (
                                        (win32con.WS_CHILD | win32con.WS_VISIBLE) |
                                        win32con.WS_CLIPSIBLINGS
                                    ) | win32con.WS_CLIPCHILDREN
                                ) | commctrl.TBSTYLE_LIST
                            ) | commctrl.TBSTYLE_FLAT
                        ) | commctrl.TBSTYLE_TRANSPARENT
                    ) | commctrl.CCS_TOP
                ) | commctrl.CCS_NODIVIDER
            ) | commctrl.CCS_NORESIZE
        ) | commctrl.CCS_NOPARENTALIGN,
    ) = begin
        win32gui.SendMessage(self.hwnd, commctrl.TB_BUTTONSTRUCTSIZE, 20, 0)
        new(hwndparent, hwnd, styles)
    end
end
function ShowWindow(self::IEToolbarCtrl, mode)
    ShowWindow(win32gui, self.hwnd, mode)
end

function AddButtons(self::IEToolbarCtrl)
    tbbuttons = ""
    for button in buttons
        tbbuttons += toparam(button)
    end
    return SendMessage(
        win32gui,
        self.hwnd,
        commctrl.TB_ADDBUTTONS,
        length(buttons),
        tbbuttons,
    )
end

function GetSafeHwnd(self::IEToolbarCtrl)::IEToolbarCtrl
    return self.hwnd
end

mutable struct IEToolbar <: AbstractIEToolbar
    #= 
        The actual COM server class
         =#
    _command_map::Any
    toolbar::Any
    toolbar_command_handler::Any
    webbrowser::Any
    _com_interfaces_::Vector
    _public_methods_::Vector{String}
    _reg_clsctx_::Any
    _reg_clsid_::String
    _reg_desc_::String
    _reg_progid_::String

    IEToolbar(
        _com_interfaces_::Vector = _ietoolbar_com_interfaces_,
        _public_methods_::Vector{String} = _ietoolbar_methods_,
        _reg_clsctx_ = CLSCTX_INPROC_SERVER(pythoncom),
        _reg_clsid_::String = "{F21202A2-959A-4149-B1C3-68B9013F3335}",
        _reg_desc_::String = "PyWin32 IE Toolbar",
        _reg_progid_::String = "PyWin32.IEToolbar",
    ) = begin
        for method in self._public_methods_
            if !hasattr(self, method)
                @printf("providing default stub for %s", method)
                setattr(self, method, Stub(method))
            end
        end
        new(
            _com_interfaces_,
            _public_methods_,
            _reg_clsctx_,
            _reg_clsid_,
            _reg_desc_,
            _reg_progid_,
        )
    end
end
function GetWindow(self::IEToolbar)
    return GetSafeHwnd(self.toolbar)
end

function Load(self::IEToolbar, stream)
    #= pass =#
end

function Save(self::IEToolbar, pStream, fClearDirty)
    #= pass =#
end

function CloseDW(self::IEToolbar, dwReserved)
    #Delete Unsupported
    del(self.toolbar)
end

function ShowDW(self::IEToolbar, bShow)
    if bShow
        ShowWindow(self.toolbar, win32con.SW_SHOW)
    else
        ShowWindow(self.toolbar, win32con.SW_HIDE)
    end
end

function on_first_button(self::IEToolbar)
    println("first!")
    Navigate2(self.webbrowser, "http://starship.python.net/crew/mhammond/")
end

function on_second_button(self::IEToolbar)
    println("second!")
end

function on_third_button(self::IEToolbar)
    println("third!")
end

function toolbar_command_handler(self::IEToolbar, args)
    hwnd, message, wparam, lparam, time, point = args
    if lparam == GetSafeHwnd(self.toolbar)
        self._command_map[wparam+1]()
    end
end

function SetSite(self::IEToolbar, unknown)
    if unknown
        olewindow = QueryInterface(unknown, IID_IOleWindow(pythoncom))
        hwndparent = GetWindow(olewindow)
        cmdtarget = QueryInterface(unknown, axcontrol.IID_IOleCommandTarget)
        serviceprovider = QueryInterface(cmdtarget, IID_IServiceProvider(pythoncom))
        self.webbrowser = Dispatch(
            win32com.client,
            QueryService(
                serviceprovider,
                "{0002DF05-0000-0000-C000-000000000046}",
                IID_IDispatch(pythoncom),
            ),
        )
        self.toolbar = IEToolbarCtrl(hwndparent)
        buttons = [
            ("Visit PyWin32 Homepage", self.on_first_button),
            ("Another Button", self.on_second_button),
            ("Yet Another Button", self.on_third_button),
        ]
        self._command_map = Dict()
        window = CreateWindowFromHandle(win32ui, hwndparent)
        for i = 0:length(buttons)-1
            button = TBBUTTON()
            name, func = buttons[i+1]
            id = 17476 + i
            button.iBitmap = -2
            button.idCommand = id
            button.fsState = commctrl.TBSTATE_ENABLED
            button.fsStyle = commctrl.TBSTYLE_BUTTON
            button.iString = name
            self._command_map[17476+i+1] = func
            AddButtons(self.toolbar)
            HookMessage(window, self.toolbar_command_handler, win32con.WM_COMMAND)
        end
    else
        self.webbrowser = nothing
    end
end

function GetClassID(self::IEToolbar)::IEToolbar
    return self._reg_clsid_
end

function GetBandInfo(self::IEToolbar, dwBandId, dwViewMode, dwMask)::Tuple
    ptMinSize = (0, 24)
    ptMaxSize = (2000, 24)
    ptIntegral = (0, 0)
    ptActual = (2000, 24)
    wszTitle = "PyWin32 IE Toolbar"
    dwModeFlags = DBIMF_VARIABLEHEIGHT
    crBkgnd = 0
    return (ptMinSize, ptMaxSize, ptIntegral, ptActual, wszTitle, dwModeFlags, crBkgnd)
end

function DllInstall(bInstall, cmdLine)
    comclass = IEToolbar
end

function DllRegisterServer()
    comclass = IEToolbar
    try
        println("Trying to register Toolbar.\n")
        hkey = CreateKey(
            winreg,
            HKEY_LOCAL_MACHINE(winreg),
            "SOFTWARE\\Microsoft\\Internet Explorer\\Toolbar",
        )
        subKey = SetValueEx(winreg, hkey, _reg_clsid_(comclass), 0, REG_BINARY(winreg), " ")
    catch exn
        if exn isa WindowsError
            @printf(
                "Couldn\'t set registry value.\nhkey: %d\tCLSID: %s\n",
                (hkey, _reg_clsid_(comclass))
            )
        end
    end
end

function DllUnregisterServer()
    comclass = IEToolbar
    try
        println("Trying to unregister Toolbar.\n")
        hkey = CreateKey(
            winreg,
            HKEY_LOCAL_MACHINE(winreg),
            "SOFTWARE\\Microsoft\\Internet Explorer\\Toolbar",
        )
        DeleteValue(winreg, hkey, _reg_clsid_(comclass))
    catch exn
        if exn isa WindowsError
            @printf(
                "Couldn\'t delete registry value.\nhkey: %d\tCLSID: %s\n",
                (hkey, _reg_clsid_(comclass))
            )
        end
    end
end

function main()
    UseCommandLine(win32com.server.register, IEToolbar)
    if "--unregister" in append!([PROGRAM_FILE], ARGS)
        DllUnregisterServer()
    else
        DllRegisterServer()
    end
end

main()
end
