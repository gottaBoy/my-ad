#include <boost/python.hpp>
#include <boost/version.hpp>
#include <stdexcept>
#include <string>

#ifndef _LIBCPP_VERSION
#error "UE libc++ is required"
#endif
static_assert(BOOST_VERSION == 108200 && PY_MAJOR_VERSION == 3 && PY_MINOR_VERSION == 11,
              "Boost 1.82.0 and CPython 3.11 required");

struct Counter
{
    explicit Counter(int initial) : value(initial) {}
    int increment(int amount) { return value += amount; }
    int value;
};

static int add(int a, int b) { return a + b; }
static int version() { return BOOST_VERSION; }
static std::string greet(const std::string& name) { return "hello " + name; }
static void reject() { throw std::invalid_argument("boost rejection"); }
static void translate(const std::invalid_argument& error) { PyErr_SetString(PyExc_ValueError, error.what()); }

BOOST_PYTHON_MODULE(_boost_stage)
{
    namespace bp = boost::python;
    bp::def("add", add);
    bp::def("version", version);
    bp::def("greet", greet);
    bp::def("reject", reject);
    bp::class_<Counter>("Counter", bp::init<int>()).def("increment", &Counter::increment);
    bp::register_exception_translator<std::invalid_argument>(&translate);
}
