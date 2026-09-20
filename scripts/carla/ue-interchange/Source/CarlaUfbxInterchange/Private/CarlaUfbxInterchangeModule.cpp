#include "CarlaUfbxInterchange.h"
#include "Modules/ModuleManager.h"
#if CARLA_INTERCHANGE_UFBX_STATIC
#include "Features/IModularFeatures.h"
#include "Serialization/BufferArchive.h"
#include "Serialization/LargeMemoryWriter.h"
#include "Serialization/MemoryReader.h"

namespace
{
class FStaticSession final : public UE::Interchange::IInterchangeFbxStaticSession
{
    CarlaUfbxMesh::FStaticScene Scene;
    bool bReady = false;
public:
    bool LoadScene(const FString& Filename, bool Convert, bool FrontX, bool Unit, bool Namespace,
        UInterchangeBaseNodeContainer& Container, FString& Error) override
    {
        Scene = {};
        bReady = false;
        // Only the explicit front-X, centimeter, namespace-preserving policy has
        // native controls. It is not advertised as Autodesk conversion parity.
        if (!Convert || !FrontX || !Unit || !Namespace)
        { Error = TEXT("Unsupported conversion settings for the verified static ufbx policy"); return false; }
        TArray<FString> Existing;
        Container.GetNodes(UInterchangeBaseNode::StaticClass(), Existing);
        if (!Existing.IsEmpty()) { Error = TEXT("Destination graph must be empty"); return false; }
        CarlaUfbxMesh::FStaticScene Candidate;
        if (!CarlaUfbxMesh::ImportScene(Filename, Candidate, Error)) return false;
        TStrongObjectPtr<UInterchangeBaseNodeContainer> Graph;
        if (!BuildGraph(Candidate, Graph, Error)) return false;
        FBufferArchive Bytes;
        Graph->SerializeNodeContainerData(Bytes);
        if (Bytes.IsError() || Bytes.IsEmpty()) { Error = TEXT("Cannot serialize parser graph"); return false; }
        FMemoryReader Reader(Bytes);
        Container.SerializeNodeContainerData(Reader);
        Container.ComputeChildrenCache();
        if (Reader.IsError() || Reader.Tell() != Bytes.Num() || !VerifyGraph(Candidate, Container, Error))
        { Error = TEXT("Cannot restore parser graph"); return false; }
        Scene = MoveTemp(Candidate);
        bReady = true;
        return true;
    }

    bool FetchMesh(const FString& Key, const FTransform& Transform, FString& RequestUid,
        TArray64<uint8>& Bytes, FString& Error) override
    {
        if (!bReady) { Error = TEXT("No loaded ufbx source"); return false; }
        CarlaUfbxMesh::FStaticPayload Payload;
        if (!CarlaUfbxMesh::FetchStaticPayload(Scene, Key, Transform, Payload, Error)) return false;
        FLargeMemoryWriter Writer;
        Payload.Mesh.Serialize(Writer);
        bool bSkinned = false;
        Writer << bSkinned;
        if (Writer.IsError() || Writer.TotalSize() <= 0)
        { Error = TEXT("Cannot serialize static parser payload"); return false; }
        RequestUid = Payload.RequestUid;
        Bytes = TArray64<uint8>(Writer.GetData(), Writer.TotalSize());
        return true;
    }
};

class FCarlaUfbxInterchangeModule final : public IModuleInterface,
    public UE::Interchange::IInterchangeFbxStaticBackend
{
public:
    void StartupModule() override
    {
        IModularFeatures::Get().RegisterModularFeature(FeatureName(), this);
    }
    void ShutdownModule() override
    {
        IModularFeatures::Get().UnregisterModularFeature(FeatureName(), this);
    }
    TUniquePtr<UE::Interchange::IInterchangeFbxStaticSession> CreateSession() override
    { return CreateCarlaUfbxStaticSession(); }
};
}

TUniquePtr<UE::Interchange::IInterchangeFbxStaticSession> CreateCarlaUfbxStaticSession()
{
    return MakeUnique<FStaticSession>();
}
IMPLEMENT_MODULE(FCarlaUfbxInterchangeModule, CarlaUfbxInterchange)
#else
IMPLEMENT_MODULE(FDefaultModuleImpl, CarlaUfbxInterchange)
#endif
