#pragma once
#include "CarlaUfbxScene.h"
#include "Nodes/InterchangeBaseNodeContainer.h"
#include "UObject/StrongObjectPtr.h"

CARLAUFBXINTERCHANGE_API bool BuildGraph(const CarlaUfbxMesh::FStaticScene& Scene,
    TStrongObjectPtr<UInterchangeBaseNodeContainer>& Container, FString& Error);
CARLAUFBXINTERCHANGE_API bool VerifyGraph(const CarlaUfbxMesh::FStaticScene& Scene,
    const UInterchangeBaseNodeContainer& Container, FString& Error);
#if CARLA_INTERCHANGE_UFBX_STATIC
#include "InterchangeFbxStaticBackend.h"
CARLAUFBXINTERCHANGE_API TUniquePtr<UE::Interchange::IInterchangeFbxStaticSession> CreateCarlaUfbxStaticSession();
#endif
