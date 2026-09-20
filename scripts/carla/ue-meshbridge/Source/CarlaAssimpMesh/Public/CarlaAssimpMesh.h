#pragma once

#include "CoreMinimal.h"

struct FMeshDescription;

namespace CarlaAssimpMesh
{
    CARLAASSIMPMESH_API bool ImportStatic(const FString& Filename, FMeshDescription& Output, FString& Error);
    CARLAASSIMPMESH_API bool CheckMemoryRoundTrip(FMeshDescription& Mesh, int64& Bytes, FString& Error);
    CARLAASSIMPMESH_API bool RunSelfTests(int32& Passed, FString& Error);
}
