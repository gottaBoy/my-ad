#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdio.h>
#include <string.h>

#if PY_VERSION_HEX != 0x030b08f0
#error "This smoke requires CPython 3.11.8 final headers"
#endif
#if !defined(__aarch64__) || __SIZEOF_POINTER__ != 8
#error "This smoke requires native AArch64 LP64"
#endif

#ifdef PYTHON_SMOKE_EXTENSION

static PyObject *answer(PyObject *self, PyObject *args)
{
    (void)self;
    (void)args;
    return Py_BuildValue("(iii)", 42, (int)sizeof(void *), 31108);
}

static PyMethodDef methods[] = {
    {"answer", answer, METH_NOARGS, "Return a checked native C API result."},
    {NULL, NULL, 0, NULL}
};
static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "_ue_native", NULL, -1, methods,
    NULL, NULL, NULL, NULL
};
PyMODINIT_FUNC PyInit__ue_native(void) { return PyModule_Create(&module); }

#else

int main(int argc, char **argv)
{
    if (argc != 2) return 64;
    if (strncmp(Py_GetVersion(), "3.11.8 ", 7) != 0 || sizeof(void *) != 8) return 2;
    PyConfig config;
    PyConfig_InitIsolatedConfig(&config);
    config.write_bytecode = 0;
    config.site_import = 0;
    PyStatus status = PyConfig_SetBytesString(&config, &config.home, argv[1]);
    if (!PyStatus_Exception(status)) status = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    if (PyStatus_Exception(status))
    {
        fprintf(stderr, "CPython embedding initialization FAIL: %s\n", status.err_msg);
        return 3;
    }
    int code = PyRun_SimpleString(
        "import sys, struct, json, math, importlib.util\n"
        "assert sys.version_info[:3] == (3, 11, 8)\n"
        "assert struct.calcsize('P') == 8\n"
        "assert json.loads('{\"value\": 42}')['value'] == 42\n"
        "assert math.sqrt(81) == 9\n"
        "p = sys.prefix + '/lib/python-smoke/_ue_native.so'\n"
        "s = importlib.util.spec_from_file_location('_ue_native', p)\n"
        "m = importlib.util.module_from_spec(s)\n"
        "s.loader.exec_module(m)\n"
        "assert m.answer() == (42, 8, 31108)\n");
    PyObject *value = PyLong_FromLong(42);
    if (!value || PyLong_AsLong(value) != 42) code = -1;
    Py_XDECREF(value);
    if (PyErr_Occurred()) { PyErr_Print(); code = -1; }
    if (Py_FinalizeEx() < 0) return 5;
    if (code != 0) return 4;
    puts("CPython 3.11.8 embedding C API + dynamic extension PASS aarch64 pointer=8");
    return 0;
}

#endif
