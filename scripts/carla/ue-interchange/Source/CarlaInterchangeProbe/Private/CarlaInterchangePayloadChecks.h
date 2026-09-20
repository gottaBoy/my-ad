#pragma once

#include "CarlaUfbxScene.h"
#include "Dom/JsonValue.h"

bool RunPayloadChecks(const CarlaUfbxMesh::FStaticScene& Scene, const FString& ResultDir,
    TArray<TSharedPtr<FJsonValue>>& Records, int32& Passed, int64& Bytes,
    int32& Vertices, int32& Triangles, int32& Materials, FString& Error);
