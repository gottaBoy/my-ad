#pragma once

#include "CoreMinimal.h"

struct FMeshDescription;

namespace CarlaUfbxMesh
{
    struct FCoordinateCheck
    {
        int32 SourceFrontSign;
        bool bMirroredInstance;
        bool bReversedWinding;
        bool bFacingMatchesNormals;
        FBox Bounds;
        FVector3f Normal;
    };
    CARLAUFBXMESH_API bool ImportStatic(const FString& Filename, FMeshDescription& Output, FString& Error);
    CARLAUFBXMESH_API bool RunSelfTests(int32& Passed, FString& Error, TArray<FCoordinateCheck>& CoordinateChecks);
}
