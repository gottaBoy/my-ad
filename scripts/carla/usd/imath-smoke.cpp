#include <cstdio>

#ifdef IMATH_SMOKE_DRIVER

extern "C" int imath_smoke();

int main()
{
    const int code = imath_smoke();
    if (code != 0)
    {
        std::fprintf(stderr, "Imath smoke FAIL check=%d\n", code);
        return code;
    }
    std::puts("Imath 3.1.9 smoke PASS half/vector/matrix/color/libc++");
    return 0;
}

#else

#include <Imath/ImathColorAlgo.h>
#include <Imath/ImathConfig.h>
#include <Imath/ImathMatrix.h>
#include <Imath/ImathVec.h>
#include <Imath/half.h>
#include <cmath>
#include <sstream>

#ifndef _LIBCPP_VERSION
#error "This smoke requires UE libc++ headers"
#endif
static_assert(IMATH_VERSION_MAJOR == 3 && IMATH_VERSION_MINOR == 1 && IMATH_VERSION_PATCH == 9,
              "Imath version must be exactly 3.1.9");

static bool near(double actual, double expected)
{
    return std::isfinite(actual) && std::abs(actual - expected) < 1e-6;
}

extern "C" int imath_smoke()
{
    volatile float input = 1.5f;
    Imath::half h(input);
    if (h.bits() != 0x3e00 || !near(float(h), 1.5))
        return 1;
    h.setBits(0xc000);
    if (!near(float(h), -2.0))
        return 2;

    const Imath::V3f a(1, 2, 3), b(4, -1, 2);
    const Imath::V3f cross = a.cross(b);
    if (!near(a.dot(b), 8) || cross != Imath::V3f(7, 10, -9))
        return 3;
    Imath::M44f transform;
    transform.setTranslation(Imath::V3f(5, -2, 4));
    Imath::V3f translated, restored;
    transform.multVecMatrix(a, translated);
    transform.inverse().multVecMatrix(translated, restored);
    if (translated != Imath::V3f(6, 0, 7) || restored != a)
        return 4;

    // These calls require compiled Imath symbols, not just header-only arithmetic.
    const Imath::V3f rgb = Imath::hsv2rgb(Imath::V3f(0.5f, 1, 1));
    if (!near(rgb.x, 0) || !near(rgb.y, 1) || !near(rgb.z, 1))
        return 5;
    std::ostringstream stream;
    stream << h;
    if (stream.str() != "-2")
        return 6;
    return 0;
}

#endif
