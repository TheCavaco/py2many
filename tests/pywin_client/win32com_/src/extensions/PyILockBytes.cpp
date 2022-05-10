// This file implements the ILockBytes Interface and Gateway for Python.
// Generated by makegw.py

#include "stdafx.h"
#include "PythonCOM.h"
#include "PythonCOMServer.h"
#include "PyILockBytes.h"

// @doc - This file contains autoduck documentation
// ---------------------------------------------------
//
// Interface Implementation

PyILockBytes::PyILockBytes(IUnknown *pdisp) : PyIUnknown(pdisp) { ob_type = &type; }

PyILockBytes::~PyILockBytes() {}

/* static */ ILockBytes *PyILockBytes::GetI(PyObject *self) { return (ILockBytes *)PyIUnknown::GetI(self); }

// @pymethod string|PyILockBytes|ReadAt|Reads a specified number of bytes starting at a specified offset from the
// beginning of the byte array object.
PyObject *PyILockBytes::ReadAt(PyObject *self, PyObject *args)
{
    ILockBytes *pILB = GetI(self);
    if (pILB == NULL)
        return NULL;
    // @pyparm <o ULARGE_INTEGER>|ulOffset||Offset to start reading
    // @pyparm int|cb||Number of bytes to read
    ULONG cb;
    ULARGE_INTEGER ulOffset;
    if (!PyArg_ParseTuple(args, "Kk:ReadAt", &ulOffset.QuadPart, &cb))
        return NULL;

    PyObject *pyretval = PyBytes_FromStringAndSize(NULL, cb);
    if (pyretval == NULL)
        return NULL;
    ULONG cbRead;
    PY_INTERFACE_PRECALL;
    HRESULT hr = pILB->ReadAt(ulOffset, PyBytes_AS_STRING(pyretval), cb, &cbRead);
    PY_INTERFACE_POSTCALL;
    if (FAILED(hr)) {
        Py_DECREF(pyretval);
        return PyCom_BuildPyException(hr, pILB, IID_ILockBytes);
    }
    // @comm The result is a binary buffer returned in a string.
    _PyBytes_Resize(&pyretval, cbRead);
    return pyretval;
}

// @pymethod int|PyILockBytes|WriteAt|Writes the specified number of bytes starting at a specified offset from the
// beginning of the byte array.
PyObject *PyILockBytes::WriteAt(PyObject *self, PyObject *args)
{
    ILockBytes *pILB = GetI(self);
    if (pILB == NULL)
        return NULL;
    // @pyparm <o ULARGE_INTEGER>|ulOffset||Offset to write at.
    // @pyparm string|data||Data to write
    PyObject *obulOffset, *obpv;
    if (!PyArg_ParseTuple(args, "OO:WriteAt", &obulOffset, &obpv))
        return NULL;
    ULARGE_INTEGER ulOffset;
    if (!PyWinObject_AsULARGE_INTEGER(obulOffset, &ulOffset))
        return NULL;
    PyWinBufferView pybuf(obpv);
    if (!pybuf.ok())
        return NULL;
    ULONG pcbWritten;
    PY_INTERFACE_PRECALL;
    HRESULT hr = pILB->WriteAt(ulOffset, pybuf.ptr(), pybuf.len(), &pcbWritten);
    PY_INTERFACE_POSTCALL;
    if (FAILED(hr))
        return PyCom_BuildPyException(hr, pILB, IID_ILockBytes);

    // @rdesc The result is the number of bytes actually written.
    return PyLong_FromUnsignedLong(pcbWritten);
}

// @pymethod |PyILockBytes|Flush|Ensures that any internal buffers maintained by the byte array object are written out
// to the backing storage.
PyObject *PyILockBytes::Flush(PyObject *self, PyObject *args)
{
    ILockBytes *pILB = GetI(self);
    if (pILB == NULL)
        return NULL;
    if (!PyArg_ParseTuple(args, ":Flush"))
        return NULL;
    PY_INTERFACE_PRECALL;
    HRESULT hr = pILB->Flush();
    PY_INTERFACE_POSTCALL;
    if (FAILED(hr))
        return PyCom_BuildPyException(hr, pILB, IID_ILockBytes);
    Py_INCREF(Py_None);
    return Py_None;
}

// @pymethod |PyILockBytes|SetSize|Changes the size of the byte array.
PyObject *PyILockBytes::SetSize(PyObject *self, PyObject *args)
{
    ILockBytes *pILB = GetI(self);
    if (pILB == NULL)
        return NULL;
    // @pyparm <o ULARGE_INTEGER>|cb||The new size.
    PyObject *obcb;
    if (!PyArg_ParseTuple(args, "O:SetSize", &obcb))
        return NULL;
    ULARGE_INTEGER cb;
    BOOL bPythonIsHappy = TRUE;
    if (!PyWinObject_AsULARGE_INTEGER(obcb, &cb))
        bPythonIsHappy = FALSE;
    if (!bPythonIsHappy)
        return NULL;
    PY_INTERFACE_PRECALL;
    HRESULT hr = pILB->SetSize(cb);
    PY_INTERFACE_POSTCALL;
    if (FAILED(hr))
        return PyCom_BuildPyException(hr, pILB, IID_ILockBytes);
    Py_INCREF(Py_None);
    return Py_None;
}

// @pymethod |PyILockBytes|LockRegion|Restricts access to a specified range of bytes in the byte array.
PyObject *PyILockBytes::LockRegion(PyObject *self, PyObject *args)
{
    ILockBytes *pILB = GetI(self);
    if (pILB == NULL)
        return NULL;
    // @pyparm <o ULARGE_INTEGER>|libOffset||The beginning of the region to lock.
    // @pyparm <o ULARGE_INTEGER>|cb||The number of bytes to lock.
    // @pyparm int|dwLockType||Specifies the restrictions being requested on accessing the range.
    PyObject *oblibOffset;
    PyObject *obcb;
    DWORD dwLockType;
    if (!PyArg_ParseTuple(args, "OOi:LockRegion", &oblibOffset, &obcb, &dwLockType))
        return NULL;
    ULARGE_INTEGER libOffset;
    ULARGE_INTEGER cb;
    BOOL bPythonIsHappy = TRUE;
    if (!PyWinObject_AsULARGE_INTEGER(oblibOffset, &libOffset))
        bPythonIsHappy = FALSE;
    if (!PyWinObject_AsULARGE_INTEGER(obcb, &cb))
        bPythonIsHappy = FALSE;
    if (!bPythonIsHappy)
        return NULL;
    PY_INTERFACE_PRECALL;
    HRESULT hr = pILB->LockRegion(libOffset, cb, dwLockType);
    PY_INTERFACE_POSTCALL;
    if (FAILED(hr))
        return PyCom_BuildPyException(hr, pILB, IID_ILockBytes);
    Py_INCREF(Py_None);
    return Py_None;
}

// @pymethod |PyILockBytes|UnlockRegion|Removes the access restriction on a range of bytes previously restricted with
// <om PyILockBytes.LockRegion>.
PyObject *PyILockBytes::UnlockRegion(PyObject *self, PyObject *args)
{
    ILockBytes *pILB = GetI(self);
    if (pILB == NULL)
        return NULL;
    // @pyparm <o ULARGE_INTEGER>|libOffset||The beginning of the region to unlock.
    // @pyparm <o ULARGE_INTEGER>|cb||The number of bytes to lock.
    // @pyparm int|dwLockType||Specifies the restrictions being requested on accessing the range.
    PyObject *oblibOffset;
    PyObject *obcb;
    DWORD dwLockType;
    if (!PyArg_ParseTuple(args, "OOi:UnlockRegion", &oblibOffset, &obcb, &dwLockType))
        return NULL;
    ULARGE_INTEGER libOffset;
    ULARGE_INTEGER cb;
    BOOL bPythonIsHappy = TRUE;
    if (!PyWinObject_AsULARGE_INTEGER(oblibOffset, &libOffset))
        bPythonIsHappy = FALSE;
    if (!PyWinObject_AsULARGE_INTEGER(obcb, &cb))
        bPythonIsHappy = FALSE;
    if (!bPythonIsHappy)
        return NULL;
    PY_INTERFACE_PRECALL;
    HRESULT hr = pILB->UnlockRegion(libOffset, cb, dwLockType);
    PY_INTERFACE_POSTCALL;
    if (FAILED(hr))
        return PyCom_BuildPyException(hr, pILB, IID_ILockBytes);
    Py_INCREF(Py_None);
    return Py_None;
}

// @pymethod <o STATSTG>|PyILockBytes|Stat|Retrieves a <o STATSTG> structure for this byte array object.
PyObject *PyILockBytes::Stat(PyObject *self, PyObject *args)
{
    ILockBytes *pILB = GetI(self);
    if (pILB == NULL)
        return NULL;
    // @pyparm int|grfStatFlag||Specifies that this method does not return some of the fields in the STATSTG structure,
    // thus saving a memory allocation operation. Values are taken from the STATFLAG enumerationg
    DWORD grfStatFlag;
    if (!PyArg_ParseTuple(args, "i:Stat", &grfStatFlag))
        return NULL;
    STATSTG pstatstg;
    PY_INTERFACE_PRECALL;
    HRESULT hr = pILB->Stat(&pstatstg, grfStatFlag);
    PY_INTERFACE_POSTCALL;
    if (FAILED(hr))
        return PyCom_BuildPyException(hr, pILB, IID_ILockBytes);

    PyObject *obpstatstg = PyCom_PyObjectFromSTATSTG(&pstatstg);
    // STATSTG doco says our responsibility to free
    if ((pstatstg).pwcsName)
        CoTaskMemFree((pstatstg).pwcsName);
    PyObject *pyretval = Py_BuildValue("O", obpstatstg);
    Py_XDECREF(obpstatstg);
    return pyretval;
}

// @object PyILockBytes|Description of the interface
static struct PyMethodDef PyILockBytes_methods[] = {
    {"ReadAt", PyILockBytes::ReadAt, 1},    // @pymeth ReadAt|Reads a specified number of bytes starting at a specified
                                            // offset from the beginning of the byte array object.
    {"WriteAt", PyILockBytes::WriteAt, 1},  // @pymeth WriteAt|Writes the specified number of bytes starting at a
                                            // specified offset from the beginning of the byte array.
    {"Flush", PyILockBytes::Flush, 1},  // @pymeth Flush|Ensures that any internal buffers maintained by the byte array
                                        // object are written out to the backing storage.
    {"SetSize", PyILockBytes::SetSize, 1},  // @pymeth SetSize|Changes the size of the byte array.
    {"LockRegion", PyILockBytes::LockRegion,
     1},  // @pymeth LockRegion|Restricts access to a specified range of bytes in the byte array.
    {"UnlockRegion", PyILockBytes::UnlockRegion,
     1},  // @pymeth UnlockRegion|Removes the access restriction on a range of bytes previously restricted with <om
          // PyILockBytes.LockRegion>.
    {"Stat", PyILockBytes::Stat, 1},  // @pymeth Stat|Retrieves a <o STATSTG> structure for this byte array object.
    {NULL}};

PyComTypeObject PyILockBytes::type("PyILockBytes",
                                   &PyIUnknown::type,  // @base PyILockBytes|PyIUnknown
                                   sizeof(PyILockBytes), PyILockBytes_methods, GET_PYCOM_CTOR(PyILockBytes));
// ---------------------------------------------------
//
// Gateway Implementation

STDMETHODIMP PyGLockBytes::ReadAt(
    /* [in] */ ULARGE_INTEGER ulOffset,
    /* [in] */ void __RPC_FAR *pv,
    /* [in] */ ULONG cb,
    /* [out] */ ULONG __RPC_FAR *pcbRead)
{
    if (pv == NULL)
        return E_POINTER;
    if (pcbRead)
        *pcbRead = 0;

    PY_GATEWAY_METHOD;
    PyObject *obulOffset = PyWinObject_FromULARGE_INTEGER(ulOffset);
    PyObject *result;
    HRESULT hr = InvokeViaPolicy("ReadAt", &result, "Oi", obulOffset, cb);
    Py_XDECREF(obulOffset);
    if (FAILED(hr))
        return hr;

    // Process the Python results, and convert back to the real params
    // Length of returned object must fit in buffer !
    PyWinBufferView pybuf(result);
    if (pybuf.ok()) {
        if (pybuf.len() > cb)
            PyErr_SetString(PyExc_ValueError, "PyGLockBytes::ReadAt: returned data longer than requested");
        else {
            memcpy(pv, pybuf.ptr(), pybuf.len());
            if (pcbRead)
                *pcbRead = pybuf.len();
            hr = S_OK;
        }
    }
    Py_DECREF(result);
    return MAKE_PYCOM_GATEWAY_FAILURE_CODE("Read");
}

STDMETHODIMP PyGLockBytes::WriteAt(
    /* [in] */ ULARGE_INTEGER ulOffset,
    /* [in] */ const void __RPC_FAR *pv,
    /* [in] */ ULONG cb,
    /* [out] */ ULONG __RPC_FAR *pcbWritten)
{
    if (pv == NULL)
        return E_POINTER;
    if (pcbWritten)
        *pcbWritten = 0;

    PY_GATEWAY_METHOD;
    PyObject *obulOffset = PyWinObject_FromULARGE_INTEGER(ulOffset);
    PyObject *obbuf = PyBytes_FromStringAndSize((char *)pv, cb);
    PyObject *result;
    HRESULT hr = InvokeViaPolicy("WriteAt", &result, "OO", obulOffset, obbuf);
    Py_XDECREF(obulOffset);
    Py_XDECREF(obbuf);
    if (FAILED(hr))
        return hr;
    // Process the Python results, and convert back to the real params
    int cbWritten = PyLong_AsLong(result);
    Py_DECREF(result);
    if (cbWritten == -1) {
        PyErr_Clear();
        return PyCom_SetCOMErrorFromSimple(E_FAIL, GetIID());
    }
    if (pcbWritten != NULL)
        *pcbWritten = cbWritten;
    return S_OK;
}

STDMETHODIMP PyGLockBytes::Flush(void)
{
    PY_GATEWAY_METHOD;
    HRESULT hr = InvokeViaPolicy("Flush", NULL, "i");
    return hr;
}

STDMETHODIMP PyGLockBytes::SetSize(
    /* [in] */ ULARGE_INTEGER cb)
{
    PY_GATEWAY_METHOD;
    PyObject *obcb = PyWinObject_FromULARGE_INTEGER(cb);
    HRESULT hr = InvokeViaPolicy("SetSize", NULL, "O", obcb);
    Py_XDECREF(obcb);
    return hr;
}

STDMETHODIMP PyGLockBytes::LockRegion(
    /* [in] */ ULARGE_INTEGER libOffset,
    /* [in] */ ULARGE_INTEGER cb,
    /* [in] */ DWORD dwLockType)
{
    PY_GATEWAY_METHOD;
    PyObject *oblibOffset = PyWinObject_FromULARGE_INTEGER(libOffset);
    PyObject *obcb = PyWinObject_FromULARGE_INTEGER(cb);
    HRESULT hr = InvokeViaPolicy("LockRegion", NULL, "OOi", oblibOffset, obcb, dwLockType);
    Py_XDECREF(oblibOffset);
    Py_XDECREF(obcb);
    return hr;
}

STDMETHODIMP PyGLockBytes::UnlockRegion(
    /* [in] */ ULARGE_INTEGER libOffset,
    /* [in] */ ULARGE_INTEGER cb,
    /* [in] */ DWORD dwLockType)
{
    PY_GATEWAY_METHOD;
    PyObject *oblibOffset = PyWinObject_FromULARGE_INTEGER(libOffset);
    PyObject *obcb = PyWinObject_FromULARGE_INTEGER(cb);
    HRESULT hr = InvokeViaPolicy("UnlockRegion", NULL, "OOi", oblibOffset, obcb, dwLockType);
    Py_XDECREF(oblibOffset);
    Py_XDECREF(obcb);
    return hr;
}

STDMETHODIMP PyGLockBytes::Stat(
    /* [out] */ STATSTG __RPC_FAR *pstatstg,
    /* [in] */ DWORD grfStatFlag)
{
    PY_GATEWAY_METHOD;
    PyObject *result;
    HRESULT hr = InvokeViaPolicy("Stat", &result, "i", grfStatFlag);
    if (FAILED(hr))
        return hr;
    // Process the Python results, and convert back to the real params
    PyObject *obpstatstg;
    if (!PyArg_Parse(result, "O", &obpstatstg))
        return PyCom_HandlePythonFailureToCOM(/*pexcepinfo*/);
    BOOL bPythonIsHappy = TRUE;
    if (!PyCom_PyObjectAsSTATSTG(obpstatstg, pstatstg, 0 /*flags*/))
        bPythonIsHappy = FALSE;
    if (!bPythonIsHappy)
        hr = PyCom_HandlePythonFailureToCOM(/*pexcepinfo*/);
    Py_DECREF(result);
    return hr;
}
