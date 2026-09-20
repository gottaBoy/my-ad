#include "CoreMinimal.h"
#include "CarlaUfbxScene.h"
#include "CarlaInterchangePayloadChecks.h"
#include "CarlaInterchangeGraph.h"
#include "InterchangeMeshNode.h"
#include "InterchangeSceneNode.h"
#include "Nodes/InterchangeBaseNodeContainer.h"
#include "StaticMeshOperations.h"
#include "Dom/JsonObject.h"
#include "Misc/Paths.h"
#include "Serialization/LargeMemoryReader.h"
#include "Serialization/LargeMemoryWriter.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/BufferArchive.h"
#include "Serialization/MemoryReader.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/Parse.h"
#include "Misc/ScopeExit.h"
#include "UObject/StrongObjectPtr.h"
#include "RequiredProgramMainCPPInclude.h"

IMPLEMENT_APPLICATION(CarlaInterchangeProbe, "CarlaInterchangeProbe")

static bool Bootstrap(FString& Error, int64& Bytes)
{
    TStrongObjectPtr<UInterchangeBaseNodeContainer> Container(NewObject<UInterchangeBaseNodeContainer>());
    auto* Root = NewObject<UInterchangeSceneNode>(Container.Get());
    auto* Child = NewObject<UInterchangeSceneNode>(Container.Get());
    auto* Mesh = NewObject<UInterchangeMeshNode>(Container.Get());
    Root->InitializeNode(TEXT("root"), TEXT("Root"), EInterchangeNodeContainerType::TranslatedScene);
    Child->InitializeNode(TEXT("child"), TEXT("Child"), EInterchangeNodeContainerType::TranslatedScene);
    Mesh->InitializeNode(TEXT("mesh"), TEXT("Mesh"), EInterchangeNodeContainerType::TranslatedAsset);
    Container->AddNode(Root);
    Container->AddNode(Child);
    Container->AddNode(Mesh);
    if (!Container->SetNodeParentUid(TEXT("child"), TEXT("root"))
        || !Root->SetCustomLocalTransform(Container.Get(), FTransform::Identity)
        || !Child->SetCustomLocalTransform(Container.Get(), FTransform(FVector(1, 2, 3)))
        || !Child->SetCustomAssetInstanceUid(TEXT("mesh")))
    {
        Error = TEXT("Could not create real Interchange node relationships");
        return false;
    }
    FBufferArchive Saved;
    Container->SerializeNodeContainerData(Saved);
    if (Saved.IsError() || Saved.IsEmpty())
    {
        Error = TEXT("Interchange node serialization failed");
        return false;
    }
    Bytes = Saved.Num();
    TStrongObjectPtr<UInterchangeBaseNodeContainer> Loaded(NewObject<UInterchangeBaseNodeContainer>());
    FMemoryReader Reader(Saved);
    Loaded->SerializeNodeContainerData(Reader);
    Loaded->ComputeChildrenCache();
    const auto* LoadedChild = Cast<UInterchangeSceneNode>(Loaded->GetNode(TEXT("child")));
    FString AssetUid;
    FTransform Global;
    TArray<FString> Nodes;
    Loaded->GetNodes(UInterchangeBaseNode::StaticClass(), Nodes);
    const bool Valid = !Reader.IsError() && Reader.Tell() == Saved.Num() && Nodes.Num() == 3
        && Loaded->GetNodeChildrenCount(TEXT("root")) == 1 && LoadedChild
        && LoadedChild->GetParentUid() == TEXT("root")
        && LoadedChild->GetCustomAssetInstanceUid(AssetUid) && AssetUid == TEXT("mesh")
        && LoadedChild->GetCustomGlobalTransform(Loaded.Get(), FTransform::Identity, Global)
        && Global.Equals(FTransform(FVector(1, 2, 3)), 1e-9)
        && Cast<UInterchangeMeshNode>(Loaded->GetNode(TEXT("mesh")));
    if (!Valid) Error = TEXT("Interchange graph changed during round trip");
    return Valid;
}

static bool SceneTest(const FString& Filename, const FString& ResultDir, FString& Error,
    int32& SceneNodes, int32& MeshNodes, int32& MaterialNodes, int64& PayloadBytes,
    int32& PayloadCount, int32& PayloadVertices, int32& PayloadTriangles,
    int32& PayloadMaterials, FString& SourceHash, TArray<TSharedPtr<FJsonValue>>& PayloadRecords,
    int32& PayloadTests, int32& SceneTests)
{
    if (!CarlaUfbxMesh::RunSceneSelfTests(SceneTests, Error) || SceneTests <= 0) return false;
    if (!IFileManager::Get().MakeDirectory(*ResultDir, true)) { Error = TEXT("Could not create result directory"); return false; }
    CarlaUfbxMesh::FStaticScene Scene;
    if (!CarlaUfbxMesh::ImportScene(Filename, Scene, Error)) return false;
    TStrongObjectPtr<UInterchangeBaseNodeContainer> Container;
    if (!BuildGraph(Scene, Container, Error)) return false;
    const FString GraphPath = FPaths::Combine(ResultDir, TEXT("nodes.bin"));
    if (FPaths::FileExists(GraphPath)) { Error = TEXT("Refusing to overwrite node graph"); return false; }
    Container->SaveToFile(GraphPath);
    TArray64<uint8> GraphData;
    if (!FFileHelper::LoadFileToArray(GraphData, *GraphPath) || GraphData.Num() <= 0)
    { Error = TEXT("Could not save node graph"); return false; }
    TStrongObjectPtr<UInterchangeBaseNodeContainer> Loaded(NewObject<UInterchangeBaseNodeContainer>());
    Loaded->LoadFromFile(GraphPath);
    if (!VerifyGraph(Scene, *Container, Error) || !VerifyGraph(Scene, *Loaded, Error)) return false;
    if (!RunPayloadChecks(Scene, ResultDir, PayloadRecords, PayloadTests, PayloadBytes,
        PayloadVertices, PayloadTriangles, PayloadMaterials, Error)) return false;
    PayloadCount = PayloadRecords.Num();
    SceneNodes = Scene.Nodes.Num(); MeshNodes = Scene.Meshes.Num(); MaterialNodes = Scene.Materials.Num();
    SourceHash = Scene.SourceSha256;
    return true;
}

INT32_MAIN_INT32_ARGC_TCHAR_ARGV()
{
    FTaskTagScope Scope(ETaskTag::EGameThread);
    ON_SCOPE_EXIT
    {
        RequestEngineExit(TEXT("CarlaInterchangeProbe finished"));
        FEngineLoop::AppPreExit();
        FModuleManager::Get().UnloadModulesAtShutdown();
        FEngineLoop::AppExit();
    };
    const int32 InitResult = GEngineLoop.PreInit(ArgC, ArgV);
    if (InitResult) return InitResult;
    FString Output, Input, ResultDir, Error;
    if (!FParse::Value(FCommandLine::Get(), TEXT("output="), Output)) return 64;
    if (FParse::Param(FCommandLine::Get(), TEXT("legacy-hierarchy-test")))
    {
        if (!FParse::Value(FCommandLine::Get(), TEXT("input="), Input)
            || !FParse::Value(FCommandLine::Get(), TEXT("result-dir="), ResultDir)) return 64;
        return RunLegacyHierarchyChecks(Input, ResultDir, Output);
    }
#if CARLA_INTERCHANGE_UFBX_STATIC
    if (FParse::Param(FCommandLine::Get(), TEXT("native-worker-test")))
    {
        if (!FParse::Value(FCommandLine::Get(), TEXT("input="), Input)
            || !FParse::Value(FCommandLine::Get(), TEXT("result-dir="), ResultDir)) return 64;
        return RunNativeWorkerChecks(Input, ResultDir, Output);
    }
    if (FParse::Param(FCommandLine::Get(), TEXT("native-parser-test")))
    {
        if (!FParse::Value(FCommandLine::Get(), TEXT("input="), Input)
            || !FParse::Value(FCommandLine::Get(), TEXT("result-dir="), ResultDir)) return 64;
        return RunNativeParserChecks(Input, ResultDir, Output);
    }
#endif
    int64 Bytes = 0;
    int32 SceneNodes = 0, MeshNodes = 0, MaterialNodes = 0;
    int32 Tests = 0;
    int32 PayloadTests = 0, SceneTests = 0;
    TArray<TSharedPtr<FJsonValue>> PayloadRecords;
    int32 PayloadCount = 0;
    int32 PayloadVertices = 0, PayloadTriangles = 0, PayloadMaterials = 0;
    FString SourceHash;
    const bool bScene = FParse::Param(FCommandLine::Get(), TEXT("scene-test"));
    const bool bSourceSceneSelfTest = FParse::Param(
        FCommandLine::Get(), TEXT("source-scene-self-test"));
    bool Success = false;
    if (bSourceSceneSelfTest)
    {
        Success = CarlaUfbxMesh::RunSceneSelfTests(Tests, Error);
        if (Success && Tests <= 0)
        {
            Error = TEXT("Source-scene selftests returned zero passing tests");
            Success = false;
        }
    }
    else if (FParse::Param(FCommandLine::Get(), TEXT("bootstrap-test")))
        Success = Bootstrap(Error, Bytes);
    else if (bScene
        && FParse::Value(FCommandLine::Get(), TEXT("input="), Input)
        && FParse::Value(FCommandLine::Get(), TEXT("result-dir="), ResultDir))
        Success = SceneTest(Input, ResultDir, Error, SceneNodes, MeshNodes, MaterialNodes,
            Bytes, PayloadCount, PayloadVertices, PayloadTriangles, PayloadMaterials, SourceHash,
            PayloadRecords, PayloadTests, SceneTests);
    else if (Error.IsEmpty()) Error = TEXT("Expected bootstrap-test or scene-test with input and result-dir");
    TSharedRef<FJsonObject> Report = MakeShared<FJsonObject>();
    const bool bSceneReport = bScene && !bSourceSceneSelfTest;
    Report->SetStringField(TEXT("stage"), bSceneReport
        ? TEXT("ue-ufbx-interchange-static")
        : bSourceSceneSelfTest
        ? TEXT("ue-ufbx-scene-selftests")
        : TEXT("interchange-node-bootstrap"));
    Report->SetStringField(TEXT("scope"), bSceneReport
        ? TEXT("ufbx to UE Interchange static nodes and mesh payloads; not translator/worker, material shading, factory assets, Editor or Cook")
        : bSourceSceneSelfTest
        ? TEXT("source-scene API selftests ONLY; not Interchange worker, Editor or Cook")
        : TEXT("Interchange node construction/serialization only; not FBX translation or Cook"));
    Report->SetStringField(TEXT("status"), Success ? TEXT("PASS") : TEXT("FAIL"));
    Report->SetStringField(TEXT("error"), Error);
    if (bSourceSceneSelfTest) Report->SetNumberField(TEXT("self_tests"), Tests);
    Report->SetNumberField(TEXT("nodes"), bSceneReport ? SceneNodes + MeshNodes + MaterialNodes : (Success && !bSourceSceneSelfTest ? 3 : 0));
    Report->SetNumberField(TEXT("scene_nodes"), SceneNodes);
    Report->SetNumberField(TEXT("mesh_nodes"), MeshNodes);
    Report->SetNumberField(TEXT("material_nodes"), MaterialNodes);
    Report->SetNumberField(TEXT("payload_vertices"), PayloadVertices);
    Report->SetNumberField(TEXT("payload_triangles"), PayloadTriangles);
    Report->SetNumberField(TEXT("payload_materials"), PayloadMaterials);
    if (bSceneReport)
    {
        Report->SetNumberField(TEXT("payload_contract_version"), 2);
        Report->SetNumberField(TEXT("payload_count"), PayloadCount);
        Report->SetNumberField(TEXT("payload_self_tests"), PayloadTests);
        Report->SetNumberField(TEXT("source_scene_self_tests"), SceneTests);
        Report->SetArrayField(TEXT("payloads"), PayloadRecords);
    }
    Report->SetStringField(TEXT("source_sha256"), SourceHash);
    Report->SetBoolField(TEXT("graph_roundtrip"), bSceneReport && Success);
    Report->SetBoolField(TEXT("payload_roundtrip"), bSceneReport && Success);
    Report->SetNumberField(TEXT("serialized_bytes"), Bytes);
    FString Text;
    FJsonSerializer::Serialize(Report, TJsonWriterFactory<>::Create(&Text));
    if (!FFileHelper::SaveStringToFile(Text, *Output)) return 3;
    return Success ? 0 : 2;
}
