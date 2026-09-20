#include "CoreMinimal.h"
#include "Chaos/VectorUtility.h"

#include <cstdio>
#include <cstring>
#include <limits>

static_assert(PLATFORM_CPU_ARM_FAMILY, "Requires the native ARM64 target");
static_assert(!PLATFORM_ENABLE_VECTORINTRINSICS, "Exercise the actual Linux Editor scalar path");

static int Checks = 0;

static bool Require(bool Condition, const char* Name)
{
    ++Checks;
    if (!Condition)
    {
        std::fprintf(stderr, "FAIL %s\n", Name);
    }
    return Condition;
}

template<int Shift>
static bool CheckShift(int32 A, int32 B, int32 C, int32 D)
{
    const VectorRegister4Int Input = MakeVectorRegisterInt(A, B, C, D);
    const VectorRegister4Int Actual = VectorShiftRightImmArithmetic(Input, Shift);
    const VectorRegister4Int Left = VectorShiftLeftImm(Input, Shift);
    const VectorRegister4Int Right = VectorShiftRightImmLogical(Input, Shift);
    const int64 Divisor = int64(1) << (Shift < 32 ? Shift : 31);
    for (int Lane = 0; Lane < 4; ++Lane)
    {
        const int64 Value = Input.V[Lane];
        const int64 Expected = Value >= 0 ? Value / Divisor : -((-Value + Divisor - 1) / Divisor);
        if (!Require(Actual.V[Lane] == Expected, "arithmetic shift versus floor division"))
        {
            return false;
        }
        const uint64 Bits = static_cast<uint32>(Input.V[Lane]);
        uint32 ExpectedLeft = 0, ExpectedRight = 0;
        if constexpr (Shift < 32)
        {
            const uint64 Factor = uint64(1) << Shift;
            ExpectedLeft = static_cast<uint32>(Bits * Factor);
            ExpectedRight = static_cast<uint32>(Bits / Factor);
        }
        if (!Require(static_cast<uint32>(Left.V[Lane]) == ExpectedLeft, "logical left shift modulo 32 bits")
            || !Require(static_cast<uint32>(Right.V[Lane]) == ExpectedRight, "logical right shift zero fill")) return false;
    }
    return true;
}

template<int Shift>
static bool CheckAllShifts()
{
    if (!CheckShift<Shift>(0, -1, std::numeric_limits<int32>::min(), std::numeric_limits<int32>::max())
        || !CheckShift<Shift>(-3, 3, -65537, 65537))
    {
        return false;
    }
    if constexpr (Shift < 33)
    {
        return CheckAllShifts<Shift + 1>();
    }
    return true;
}

int main()
{
    if (!CheckAllShifts<0>() || !CheckShift<255>(-7, 7, -1, 0))
    {
        return 1;
    }
    const uint16 Unsigned[] = {0, 1, 32768, 65535};
    const int16 Signed[] = {-32768, -1, 0, 32767};
    unsigned char Bytes[9];
    std::memcpy(Bytes + 1, Unsigned, sizeof(Unsigned));
    const VectorRegister4Float U = VectorLoadURGBA16N(Bytes + 1);
    std::memcpy(Bytes + 1, Signed, sizeof(Signed));
    const VectorRegister4Float S = VectorLoadSRGBA16N(Bytes + 1);
    for (int Lane = 0; Lane < 4; ++Lane)
        if (!Require(U.V[Lane] == float(Unsigned[Lane]), "unaligned unsigned 16-bit load")
            || !Require(S.V[Lane] == float(Signed[Lane]), "unaligned signed 16-bit load")) return 1;
    const VectorRegister4Int Bits = MakeVectorRegisterInt(0x3f800000, std::numeric_limits<int32>::min(),
        0x7fc12345, -1);
    const VectorRegister4Float AsFloat = VectorCast4IntTo4Float(Bits);
    const VectorRegister4Int RoundTrip = VectorCast4FloatTo4Int(AsFloat);
    if (!Require(std::memcmp(&Bits, &RoundTrip, sizeof(Bits)) == 0, "bit cast preserves negative zero and NaN payloads"))
    {
        return 1;
    }
    const VectorRegister4Float Floats = VectorUnpackLo(
        MakeVectorRegisterFloat(1.f, 2.f, 3.f, 4.f), MakeVectorRegisterFloat(5.f, 6.f, 7.f, 8.f));
    if (!Require(Floats.V[0] == 1.f && Floats.V[1] == 5.f && Floats.V[2] == 2.f && Floats.V[3] == 6.f,
        "float lower lanes"))
    {
        return 1;
    }
    const double A = 1.0000000000000002;
    const double B = 9007199254740991.0;
    const VectorRegister4Double Doubles = VectorUnpackLo(
        MakeVectorRegisterDouble(A, B, 3.0, 4.0), MakeVectorRegisterDouble(-A, -B, 7.0, 8.0));
    if (!Require(Doubles.V[0] == A && Doubles.V[1] == -A && Doubles.V[2] == B && Doubles.V[3] == -B,
        "double lower lanes preserve double precision"))
    {
        return 1;
    }
    std::printf("PASS scalar math checks=%d\n", Checks);
    return 0;
}
