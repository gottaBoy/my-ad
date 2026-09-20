#if CARLA_INTERCHANGE_UFBX_STATIC

#include "CarlaInterchangeGraph.h"
#include "InterchangeFbxParser.h"
#include "InterchangeFbxStaticBackend.h"
#include "Dom/JsonObject.h"
#include "Features/IModularFeatures.h"
#include "HAL/FileManager.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "Misc/ScopeExit.h"
#include "Serialization/BufferArchive.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/LargeMemoryReader.h"
#include "Serialization/LargeMemoryWriter.h"
#include "Serialization/MemoryReader.h"

namespace
{
class FStaticBackend final : public UE::Interchange::IInterchangeFbxStaticBackend
{
public:
    TUniquePtr<UE::Interchange::IInterchangeFbxStaticSession> CreateSession() override
{ return CreateCarlaUfbxStaticSession(); }
};

TArray64<uint8> MeshBytes(FMeshDescription Mesh)
{
    FLargeMemoryWriter Writer;
    Mesh.Serialize(Writer);
    return TArray64<uint8>(Writer.GetData(), Writer.TotalSize());
}

bool ReadPayload(const FString& Path, const FMeshDescription& Expected)
{
    TArray64<uint8> Data;
    if (Path.IsEmpty() || !FFileHelper::LoadFileToArray(Data, *Path) || Data.IsEmpty()) return false;
    FLargeMemoryReader Reader(Data.GetData(), Data.Num());
    FMeshDescription Mesh;
    Mesh.Serialize(Reader);
    bool Skinned = true;
    Reader << Skinned;
    return !Reader.IsError() && Reader.Tell() == Data.Num() && !Skinned && MeshBytes(Mesh) == MeshBytes(Expected);
}

bool TestParser(const FString& Input, const FString& Dir, TArray<FString>& Passed,
    TArray<TSharedPtr<FJsonValue>>& Files, FString& Error, FString& SourceHash)
{
    using UE::Interchange::FInterchangeFbxParser;
    const FName Feature = UE::Interchange::IInterchangeFbxStaticBackend::FeatureName();
    auto Check = [&](bool Condition, const TCHAR* Name)
    {
        if (!Condition) { Error = FString::Printf(TEXT("%s: %s"), Name, *Error); return false; }
        Passed.Add(Name);
        return true;
    };
    auto Retain = [&](const FString& Path)
    { Files.Add(MakeShared<FJsonValueString>(FPaths::ConvertRelativePathToFull(Path))); };
    if (!IFileManager::Get().MakeDirectory(*Dir, true))
    { Error = TEXT("Cannot create test output directory"); return false; }
    CarlaUfbxMesh::FStaticScene Source;
    if (!CarlaUfbxMesh::ImportScene(Input, Source, Error) || Source.Meshes.Num() != 2) return false;
    SourceHash = Source.SourceSha256;
    const FString Key = Source.Meshes[0].PayloadKey;
    {
        FInterchangeFbxParser Missing;
        Missing.LoadFbxFile(Input, Dir);
        if (!Check(Missing.GetResultFilepath().IsEmpty() && !Missing.GetJsonLoadMessages().IsEmpty(),
            TEXT("missing provider is a real parser error"))) return false;
    }
    FStaticBackend Backend;
    IModularFeatures::Get().RegisterModularFeature(Feature, &Backend);
    ON_SCOPE_EXIT { IModularFeatures::Get().UnregisterModularFeature(Feature, &Backend); };
    FInterchangeFbxParser Parser;
    Parser.SetConvertSettings(true, true, true, true);
    TStrongObjectPtr<UInterchangeResultsContainer> Sink(NewObject<UInterchangeResultsContainer>());
    Sink->Add<UInterchangeResultError_Generic>()->Text = FText::FromString(TEXT("caller-owned message"));
    Parser.SetResultContainer(Sink.Get());
    Parser.LoadFbxFile(Input, FPaths::Combine(Dir, TEXT("load-a")));
    const FString GraphPath = Parser.GetResultFilepath();
    TStrongObjectPtr<UInterchangeBaseNodeContainer> Graph(NewObject<UInterchangeBaseNodeContainer>());
    if (!GraphPath.IsEmpty()) Graph->LoadFromFile(GraphPath);
    if (!Check(!GraphPath.IsEmpty() && Parser.GetJsonLoadMessages().IsEmpty()
        && VerifyGraph(Source, *Graph, Error) && Sink->GetResults().Num() == 1,
        TEXT("real parser loads graph without clearing caller messages"))) return false;
    Retain(GraphPath);

    FString FirstUid;
    const FTransform Transforms[] = {FTransform::Identity, FTransform(FVector(17, -23, 31)),
        FTransform(FQuat::Identity, FVector(3, 7, 11), FVector(-2, 3, 0.5))};
    TSet<FString> QueryIds;
    for (const auto& Mesh : Source.Meshes)
        for (const FTransform& Transform : Transforms)
        {
            CarlaUfbxMesh::FStaticPayload Expected;
            if (!CarlaUfbxMesh::FetchStaticPayload(Source, Mesh.PayloadKey, Transform, Expected, Error)) return false;
            const FString Uid = Parser.FetchMeshPayload(Mesh.PayloadKey, Transform, FPaths::Combine(Dir, TEXT("payload-a")));
            const FString Path = Parser.GetResultPayloadFilepath(Uid);
            if (!Check(!Uid.IsEmpty() && Uid == Expected.RequestUid && !QueryIds.Contains(Uid)
                && Parser.GetJsonLoadMessages().IsEmpty() && ReadPayload(Path, Expected.Mesh),
                TEXT("real parser mesh key/transform and consumer readback"))) return false;
            QueryIds.Add(Uid);
            Retain(Path);
            if (FirstUid.IsEmpty()) FirstUid = Uid;
        }
    const FString Repeat = Parser.FetchMeshPayload(Key, FTransform::Identity, FPaths::Combine(Dir, TEXT("payload-a")));
    if (!Check(Repeat == FirstUid && Parser.GetJsonLoadMessages().IsEmpty(), TEXT("repeat query reuses verified bytes"))) return false;
    const FString Moved = Parser.FetchMeshPayload(Key, FTransform::Identity, FPaths::Combine(Dir, TEXT("payload-b")));
    if (!Check(Moved == FirstUid && Parser.GetResultPayloadFilepath(Moved).Contains(TEXT("/payload-b/"))
        && ReadPayload(Parser.GetResultPayloadFilepath(Moved), Source.Meshes[0].Mesh),
        TEXT("same request in another result directory"))) return false;
    Retain(Parser.GetResultPayloadFilepath(Moved));
    const int32 BeforeErrors = Sink->GetResults().Num();
    if (!Check(Parser.FetchMeshPayload(TEXT("bad-key"), FTransform::Identity, Dir).IsEmpty()
        && !Parser.GetJsonLoadMessages().IsEmpty() && Sink->GetResults().Num() > BeforeErrors,
        TEXT("query failure reports to owned and external containers"))) return false;
    const FString BadFile = FPaths::Combine(Dir, TEXT("not-a-directory"));
    if (!FFileHelper::SaveStringToFile(TEXT("occupied"), *BadFile)) return false;
    if (!Check(Parser.FetchMeshPayload(Key, FTransform::Identity, BadFile).IsEmpty()
        && Parser.GetResultPayloadFilepath(FirstUid).IsEmpty(), TEXT("write failure never publishes cached path"))) return false;
    const FTransform ExtraTransform(FVector(91, 0, 0));
    FString ExtraUid = Parser.FetchMeshPayload(Key, ExtraTransform, FPaths::Combine(Dir, TEXT("cache-negative")));
    const FString ExtraPath = Parser.GetResultPayloadFilepath(ExtraUid);
    if (!Check(!ExtraUid.IsEmpty() && IFileManager::Get().Delete(*ExtraPath)
        && Parser.FetchMeshPayload(Key, ExtraTransform, FPaths::Combine(Dir, TEXT("cache-negative"))).IsEmpty()
        && Parser.GetResultPayloadFilepath(ExtraUid).IsEmpty(), TEXT("missing cached file is rejected"))) return false;
    ExtraUid = Parser.FetchMeshPayload(Key, ExtraTransform, FPaths::Combine(Dir, TEXT("cache-negative")));
    if (!Check(!ExtraUid.IsEmpty() && FFileHelper::SaveStringToFile(TEXT("tampered"), *ExtraPath)
        && Parser.FetchMeshPayload(Key, ExtraTransform, FPaths::Combine(Dir, TEXT("cache-negative"))).IsEmpty()
        && Parser.GetResultPayloadFilepath(ExtraUid).IsEmpty(), TEXT("conflicting cached bytes are rejected"))) return false;
    Parser.LoadFbxFile(Input, BadFile);
    if (!Check(Parser.GetResultFilepath().IsEmpty() && !Parser.GetJsonLoadMessages().IsEmpty()
        && Parser.FetchMeshPayload(Key, FTransform::Identity, Dir).IsEmpty(), TEXT("graph write failure invalidates loaded state"))) return false;

    Parser.LoadFbxFile(Input + TEXT(".missing"), FPaths::Combine(Dir, TEXT("load-b")));
    if (!Check(Parser.GetResultFilepath().IsEmpty() && Parser.GetResultPayloadFilepath(FirstUid).IsEmpty()
        && !Parser.GetJsonLoadMessages().IsEmpty()
        && Parser.FetchMeshPayload(Key, FTransform::Identity, Dir).IsEmpty(),
        TEXT("failed reload invalidates old graph and scene"))) return false;
    Parser.LoadFbxFile(Input, FPaths::Combine(Dir, TEXT("load-c")));
    if (!Check(!Parser.GetResultFilepath().IsEmpty() && Parser.GetJsonLoadMessages().IsEmpty(),
        TEXT("load succeeds again after prior failure"))) return false;
    Retain(Parser.GetResultFilepath());
    Parser.Reset();
    if (!Check(Parser.GetResultFilepath().IsEmpty() && Parser.GetJsonLoadMessages().IsEmpty()
        && Parser.FetchMeshPayload(Key, FTransform::Identity, Dir).IsEmpty(), TEXT("reset invalidates session"))) return false;
    Parser.ReleaseResources();
    Parser.ReleaseResources();
    if (!Check(Parser.FetchMeshPayload(Key, FTransform::Identity, Dir).IsEmpty(), TEXT("repeated release is safe"))) return false;

    for (int32 Setting = 0; Setting < 4; ++Setting)
    {
        Parser.SetConvertSettings(Setting != 0, Setting != 1, Setting != 2, Setting != 3);
        Parser.LoadFbxFile(Input, Dir);
        if (!Check(Parser.GetResultFilepath().IsEmpty() && !Parser.GetJsonLoadMessages().IsEmpty(),
            TEXT("unsupported conversion policy is rejected at load"))) return false;
    }
    Parser.SetConvertSettings(true, true, true, true);
    TStrongObjectPtr<UInterchangeBaseNodeContainer> MemoryGraph(NewObject<UInterchangeBaseNodeContainer>());
    Parser.LoadFbxFile(Input, *MemoryGraph);
    if (!Check(Parser.GetJsonLoadMessages().IsEmpty() && VerifyGraph(Source, *MemoryGraph, Error),
        TEXT("real parser memory graph overload"))) return false;
    Parser.LoadFbxFile(Input, *MemoryGraph);
    if (!Check(!Parser.GetJsonLoadMessages().IsEmpty() && VerifyGraph(Source, *MemoryGraph, Error),
        TEXT("nonempty caller graph is preserved and rejected"))) return false;
    Parser.FetchPayload(Key, Dir);
    if (!Check(!Parser.GetJsonLoadMessages().IsEmpty(), TEXT("generic non-static payload is rejected"))) return false;
    if (!Check(Parser.FetchAnimationBakeTransformPayloads(FString(TEXT("[]")), Dir).IsEmpty()
        && !Parser.GetJsonLoadMessages().IsEmpty(), TEXT("animation query is explicitly unsupported"))) return false;

    FInterchangeFbxParser Other;
    Other.SetConvertSettings(true, true, true, true);
    Other.LoadFbxFile(Input, FPaths::Combine(Dir, TEXT("other")));
    Parser.Reset();
    const FString OtherUid = Other.FetchMeshPayload(Key, FTransform::Identity, FPaths::Combine(Dir, TEXT("other-payload")));
    if (!Check(!OtherUid.IsEmpty() && ReadPayload(Other.GetResultPayloadFilepath(OtherUid), Source.Meshes[0].Mesh),
        TEXT("parser instances do not share session state"))) return false;
    Retain(Other.GetResultFilepath());
    Retain(Other.GetResultPayloadFilepath(OtherUid));
    {
        FStaticBackend Duplicate;
        IModularFeatures::Get().RegisterModularFeature(Feature, &Duplicate);
        ON_SCOPE_EXIT { IModularFeatures::Get().UnregisterModularFeature(Feature, &Duplicate); };
        Parser.LoadFbxFile(Input, Dir);
        if (!Check(Parser.GetResultFilepath().IsEmpty() && !Parser.GetJsonLoadMessages().IsEmpty(),
            TEXT("ambiguous providers are rejected"))) return false;
    }
    FString FixtureRoot;
    if (!FParse::Value(FCommandLine::Get(), TEXT("fixture-root="), FixtureRoot))
    { Error = TEXT("Missing unsupported FBX fixture root"); return false; }
    for (const TCHAR* Name : {TEXT("AnimatedCharacter.fbx"), TEXT("MorphTargets.fbx")})
    {
        const FString Path = FPaths::Combine(FixtureRoot, Name);
        if (!FPaths::FileExists(Path)) { Error = TEXT("Unsupported-feature control is missing"); return false; }
        Parser.LoadFbxFile(Path, Dir);
        const TArray<FString> Messages = Parser.GetJsonLoadMessages();
        if (!Check(Parser.GetResultFilepath().IsEmpty() && !Messages.IsEmpty()
            && Messages[0].Contains(TEXT("unsupported")), TEXT("real animated/morph FBX rejected"))) return false;
    }
    return true;
}
}

int32 RunNativeParserChecks(const FString& Input, const FString& ResultDir, const FString& Output)
{
    TArray<FString> Passed;
    TArray<TSharedPtr<FJsonValue>> Files;
    FString Error, Hash;
    const bool Success = TestParser(Input, ResultDir, Passed, Files, Error, Hash);
    TSharedRef<FJsonObject> Report = MakeShared<FJsonObject>();
    Report->SetStringField(TEXT("stage"), TEXT("ue-ufbx-parser-static"));
    Report->SetStringField(TEXT("scope"), TEXT("Real FInterchangeFbxParser with static ufbx session; not worker process, factory assets, full FBX, Editor or Cook"));
    Report->SetStringField(TEXT("status"), Success ? TEXT("PASS") : TEXT("FAIL"));
    Report->SetStringField(TEXT("error"), Error);
    Report->SetStringField(TEXT("source_sha256"), Hash);
    Report->SetNumberField(TEXT("self_tests"), Passed.Num());
    TArray<TSharedPtr<FJsonValue>> Names;
    for (const FString& Name : Passed) Names.Add(MakeShared<FJsonValueString>(Name));
    Report->SetArrayField(TEXT("checks"), Names);
    Report->SetArrayField(TEXT("files"), Files);
    FString Text;
    FJsonSerializer::Serialize(Report, TJsonWriterFactory<>::Create(&Text));
    if (!FFileHelper::SaveStringToFile(Text, *Output)) return 3;
    return Success ? 0 : 2;
}

#endif // CARLA_INTERCHANGE_UFBX_STATIC
