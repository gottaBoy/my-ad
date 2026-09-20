#if CARLA_INTERCHANGE_UFBX_STATIC

#include "InterchangeFbxParser.h"
#include "InterchangeFbxStaticBackend.h"
#include "Features/IModularFeatures.h"
#include "HAL/FileManager.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/LargeMemoryWriter.h"
#include "UObject/Package.h"

namespace UE::Interchange
{
namespace Private
{
class FFbxParser
{
public:
    TUniquePtr<IInterchangeFbxStaticSession> Session;
    bool bLoaded = false;
    bool bConvertScene = true;
    bool bForceFrontXAxis = false;
    bool bConvertSceneUnit = true;
    bool bKeepNamespace = false;
};
}

namespace
{
void ErrorMessage(UInterchangeResultsContainer* Owned, UInterchangeResultsContainer* Sink,
    const FString& Source, const FString& Message)
{
    for (UInterchangeResultsContainer* Container : {Owned, Sink == Owned ? nullptr : Sink})
    {
        if (!Container) continue;
        auto* Result = Container->Add<UInterchangeResultError_Generic>();
        Result->SourceAssetName = Source;
        Result->Text = FText::FromString(Message);
    }
}

TUniquePtr<IInterchangeFbxStaticSession> NewSession(FString& Error)
{
    IModularFeatures::FScopedLockModularFeatureList Lock;
    auto Backends = IModularFeatures::Get().GetModularFeatureImplementations<IInterchangeFbxStaticBackend>(
        IInterchangeFbxStaticBackend::FeatureName());
    if (Backends.Num() != 1)
    {
        Error = TEXT("Exactly one registered ufbx static backend is required");
        return nullptr;
    }
    auto Session = Backends[0]->CreateSession();
    if (!Session) Error = TEXT("Static backend did not create a session");
    return Session;
}

bool SaveVerified(const TArray64<uint8>& Bytes, const FString& Filename, bool bReuseIdentical,
    FString& Error)
{
    if (Bytes.IsEmpty())
    { Error = TEXT("Refusing to publish an empty parser result"); return false; }
    if (FPaths::FileExists(Filename))
    {
        TArray64<uint8> Existing;
        if (bReuseIdentical && FFileHelper::LoadFileToArray(Existing, *Filename) && Existing == Bytes)
            return true;
        Error = TEXT("Refusing to overwrite a conflicting parser result");
        return false;
    }
    if (!IFileManager::Get().MakeDirectory(*FPaths::GetPath(Filename), true))
    { Error = TEXT("Cannot create parser result directory"); return false; }
    TUniquePtr<FArchive> Writer(IFileManager::Get().CreateFileWriter(*Filename, FILEWRITE_NoReplaceExisting));
    if (!Writer) { Error = TEXT("Cannot create parser result file"); return false; }
    Writer->Serialize(const_cast<uint8*>(Bytes.GetData()), Bytes.Num());
    const bool bClosed = Writer->Close();
    if (!bClosed || Writer->IsError()) { Error = TEXT("Cannot write complete parser result"); return false; }
    Writer.Reset();
    TArray64<uint8> Readback;
    if (!FFileHelper::LoadFileToArray(Readback, *Filename) || Readback != Bytes)
    { Error = TEXT("Parser result readback differs from the serialized data"); return false; }
    return true;
}
}

FInterchangeFbxParser::FInterchangeFbxParser()
{
    ResultsContainer.Reset(NewObject<UInterchangeResultsContainer>(GetTransientPackage()));
    FbxParserPrivate = MakeUnique<Private::FFbxParser>();
}

FInterchangeFbxParser::~FInterchangeFbxParser() { ReleaseResources(); }

void FInterchangeFbxParser::Reset()
{
    FScopeLock Lock(&ResultPayloadsCriticalSection);
    FbxParserPrivate->Session.Reset();
    FbxParserPrivate->bLoaded = false;
    SourceFilename.Reset();
    ResultFilepath.Reset();
    ResultPayloads.Reset();
    ResultsContainer->Empty();
}

void FInterchangeFbxParser::ReleaseResources()
{
    // Keep the diagnostic container usable for calls after release; destruction
    // releases it normally. Sessions are destroyed before a provider is unloaded.
    Reset();
}

void FInterchangeFbxParser::SetResultContainer(UInterchangeResultsContainer* Result)
{
    InternalResultsContainer = Result;
}

UInterchangeResultsContainer* FInterchangeFbxParser::GetResultContainer() const
{
    return ResultsContainer.Get();
}

void FInterchangeFbxParser::SetConvertSettings(bool Convert, bool FrontX, bool Unit, bool Namespace)
{
    Reset();
    FbxParserPrivate->bConvertScene = Convert;
    FbxParserPrivate->bForceFrontXAxis = FrontX;
    FbxParserPrivate->bConvertSceneUnit = Unit;
    FbxParserPrivate->bKeepNamespace = Namespace;
}

void FInterchangeFbxParser::LoadFbxFile(const FString& Filename, UInterchangeBaseNodeContainer& Container)
{
    Reset();
    SourceFilename = Filename;
    FString Error;
    FbxParserPrivate->Session = NewSession(Error);
    if (!FbxParserPrivate->Session || !FbxParserPrivate->Session->LoadScene(Filename,
        FbxParserPrivate->bConvertScene, FbxParserPrivate->bForceFrontXAxis,
        FbxParserPrivate->bConvertSceneUnit, FbxParserPrivate->bKeepNamespace, Container, Error))
    {
        FbxParserPrivate->Session.Reset();
        ErrorMessage(ResultsContainer.Get(), InternalResultsContainer, Filename, Error);
        return;
    }
    FbxParserPrivate->bLoaded = true;
}

void FInterchangeFbxParser::LoadFbxFile(const FString& Filename, const FString& ResultFolder)
{
    TStrongObjectPtr<UInterchangeBaseNodeContainer> Container(NewObject<UInterchangeBaseNodeContainer>());
    LoadFbxFile(Filename, *Container);
    if (!FbxParserPrivate->bLoaded) return;
    FLargeMemoryWriter Writer;
    Container->SerializeNodeContainerData(Writer);
    FString Error;
    const FString Path = FPaths::Combine(FPaths::ConvertRelativePathToFull(ResultFolder),
        FString::Printf(TEXT("SceneDescription-%lld.itc"), UniqueIdCounter.IncrementExchange()));
    if (ResultFolder.IsEmpty() || Writer.IsError()
        || !SaveVerified(TArray64<uint8>(Writer.GetData(), Writer.TotalSize()), Path, false, Error))
    {
        Reset();
        ErrorMessage(ResultsContainer.Get(), InternalResultsContainer, Filename,
            Error.IsEmpty() ? TEXT("Invalid scene output directory or serialization") : Error);
        return;
    }
    ResultFilepath = Path;
}

FString FInterchangeFbxParser::FetchMeshPayload(const FString& Key, const FTransform& Transform,
    const FString& ResultFolder)
{
    FScopeLock Lock(&ResultPayloadsCriticalSection);
    ResultsContainer->Empty();
    FString Uid, Error;
    TArray64<uint8> Bytes;
    if (!FbxParserPrivate->bLoaded || !FbxParserPrivate->Session)
    { Error = TEXT("No loaded static FBX scene"); }
    else if (ResultFolder.IsEmpty())
    { Error = TEXT("Invalid payload output directory"); }
    else if (FbxParserPrivate->Session->FetchMesh(Key, Transform, Uid, Bytes, Error))
    {
        bool bValidUid = Uid.Len() == 64;
        for (TCHAR Character : Uid)
            bValidUid &= (Character >= '0' && Character <= '9') || (Character >= 'a' && Character <= 'f');
        if (!bValidUid) Error = TEXT("Backend returned an invalid result identity");
        else
        {
            const FString Path = FPaths::Combine(FPaths::ConvertRelativePathToFull(ResultFolder), Uid + TEXT(".payload"));
            const FString* Cached = ResultPayloads.Find(Uid);
            if (Cached && *Cached == Path && !FPaths::FileExists(Path))
                Error = TEXT("Cached payload file is missing");
            else if (SaveVerified(Bytes, Path, true, Error))
            {
                ResultPayloads.Add(Uid, Path);
                return Uid;
            }
        }
    }
    if (!Uid.IsEmpty()) ResultPayloads.Remove(Uid);
    ErrorMessage(ResultsContainer.Get(), InternalResultsContainer, SourceFilename,
        Error.IsEmpty() ? TEXT("Static mesh payload failed") : Error);
    return FString();
}

void FInterchangeFbxParser::FetchPayload(const FString&, const FString&)
{
    ResultsContainer->Empty();
    ErrorMessage(ResultsContainer.Get(), InternalResultsContainer, SourceFilename,
        TEXT("Unsupported generic payload: this backend only implements static mesh queries"));
}

void FInterchangeFbxParser::FetchAnimationBakeTransformPayloads(const TArray<FAnimationPayloadQuery>&, const FString&)
{
    ResultsContainer->Empty();
    ErrorMessage(ResultsContainer.Get(), InternalResultsContainer, SourceFilename,
        TEXT("Unsupported animation payload in the static ufbx backend"));
}

TMap<FString, FString> FInterchangeFbxParser::FetchAnimationBakeTransformPayloads(const FString&, const FString&)
{
    ResultsContainer->Empty();
    ErrorMessage(ResultsContainer.Get(), InternalResultsContainer, SourceFilename,
        TEXT("Unsupported animation payload in the static ufbx backend"));
    return {};
}

TArray<FString> FInterchangeFbxParser::GetJsonLoadMessages() const
{
    TArray<FString> Messages;
    for (UInterchangeResult* Result : ResultsContainer->GetResults())
        Messages.Add(Result->ToJson());
    return Messages;
}
}

#endif // CARLA_INTERCHANGE_UFBX_STATIC
