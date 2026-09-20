#include "ImportUtils/SceneImportNodeInfo.h"

#include <cstdio>
#include <type_traits>

#ifdef _FBXSDK_H_
#error Scene node metadata must not include the Autodesk SDK
#endif

using FNodeInfo = UE::Import::FSceneNodeInfo;
static_assert(std::is_same_v<decltype(FNodeInfo::Transform), FTransform>);
static_assert(std::is_same_v<decltype(FNodeInfo::RotationPivot), FVector>);
static_assert(std::is_same_v<decltype(FNodeInfo::ScalePivot), FVector>);
static_assert(std::is_same_v<decltype(FNodeInfo::UniqueId), uint64>);

int main()
{
    FNodeInfo Node;
    if (Node.UniqueId != 0 || Node.ParentUniqueId != 0 || Node.AttributeUniqueId != 0
        || Node.Transform.GetTranslation() != FVector(0.0) || Node.Transform.GetScale3D() != FVector(1.0)
        || Node.Transform.GetRotation() != FQuat(0.0, 0.0, 0.0, 1.0)
        || Node.RotationPivot != FVector(0.0) || Node.ScalePivot != FVector(0.0))
    {
        return 1;
    }
    Node.UniqueId = 0xfedcba9876543210ULL;
    Node.ParentUniqueId = 0x123456789abcdef0ULL;
    Node.ObjectName = TEXT("Node_With_A_Long_Name_To_Exercise_String_Ownership");
    Node.Transform.SetTranslation(FVector(9007199254740991.0, -27.5, 1.0000000000000002));
    Node.Transform.SetScale3D(FVector(-1.0, 2.0, 0.00000001));
    Node.RotationPivot = FVector(1.0, -2.0, 3.0);
    Node.ScalePivot = FVector(-4.0, 5.0, -6.0);
    FNodeInfo Copy = Node;
    if (Copy.UniqueId != Node.UniqueId || Copy.ParentUniqueId != Node.ParentUniqueId
        || Copy.ObjectName != Node.ObjectName
        || Copy.Transform.GetTranslation() != Node.Transform.GetTranslation()
        || Copy.Transform.GetScale3D() != Node.Transform.GetScale3D()
        || Copy.RotationPivot != Node.RotationPivot || Copy.ScalePivot != Node.ScalePivot)
    {
        return 2;
    }
    Copy.Transform.SetTranslation(FVector(42.0));
    Copy.RotationPivot = FVector(0.0);
    Copy.ObjectName = TEXT("Renamed");
    if (Node.Transform.GetTranslation().X != 9007199254740991.0 || Node.RotationPivot.Y != -2.0
        || Node.ObjectName == Copy.ObjectName)
    {
        return 3;
    }
    std::puts("PASS SDK-free scene node metadata");
    return 0;
}
