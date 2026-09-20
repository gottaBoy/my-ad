#pragma once

#include "CoreMinimal.h"
#include "MeshDescription.h"

THIRD_PARTY_INCLUDES_START
#include <ufbx.h>
THIRD_PARTY_INCLUDES_END

namespace CarlaUfbxMesh::Private
{
    ufbx_load_opts StaticLoadOptions();
    bool ConvertFlattened(const ufbx_scene& Scene, FMeshDescription& Output, FString& Error);
    bool ConvertLocalPayload(const ufbx_scene& Scene, const ufbx_mesh& Mesh,
        const TArray<FString>& SlotKeys, FMeshDescription& Output, FString& Error);
}
