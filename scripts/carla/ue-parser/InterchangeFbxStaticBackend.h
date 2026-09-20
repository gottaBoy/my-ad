#pragma once

#include "CoreMinimal.h"
#include "Features/IModularFeature.h"

class UInterchangeBaseNodeContainer;

namespace UE::Interchange
{
class INTERCHANGEFBXPARSER_API IInterchangeFbxStaticSession
{
public:
    virtual ~IInterchangeFbxStaticSession() = default;
    virtual bool LoadScene(const FString& Filename, bool bConvertScene, bool bForceFrontXAxis,
        bool bConvertSceneUnit, bool bKeepNamespace, UInterchangeBaseNodeContainer& Container,
        FString& Error) = 0;
    virtual bool FetchMesh(const FString& Key, const FTransform& Transform,
        FString& RequestUid, TArray64<uint8>& Bytes, FString& Error) = 0;
};

// The engine owns the interface; a Program registers the implementation.
// This opt-in static slice is not an Editor FBX replacement.
class INTERCHANGEFBXPARSER_API IInterchangeFbxStaticBackend : public IModularFeature
{
public:
    static FName FeatureName() { return TEXT("InterchangeFbxStaticBackend"); }
    virtual TUniquePtr<IInterchangeFbxStaticSession> CreateSession() = 0;
};
}
