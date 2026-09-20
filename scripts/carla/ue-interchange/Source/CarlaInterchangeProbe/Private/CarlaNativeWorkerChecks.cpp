#if CARLA_INTERCHANGE_UFBX_STATIC
#include "CarlaInterchangeGraph.h"
#include "Dom/JsonObject.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"
#include "InterchangeCommands.h"
#include "InterchangeDispatcherNetworking.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "Misc/ScopeExit.h"
#include "SocketSubsystem.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/LargeMemoryReader.h"
#include "Serialization/LargeMemoryWriter.h"

using namespace UE::Interchange;
namespace
{
class FWorkerPeer
{
public:
    TUniquePtr<FNetworkServerNode> Server;
    FCommandQueue Queue;
    FProcHandle Process;
    uint32 Pid = 0;
    bool bWasKilled = false;
    ~FWorkerPeer()
    {
        if (Process.IsValid())
        {
            if (FPlatformProcess::IsProcRunning(Process))
            {
                bWasKilled = true;
                FPlatformProcess::TerminateProc(Process, true);
                FPlatformProcess::WaitForProc(Process);
            }
            FPlatformProcess::CloseProc(Process);
        }
    }
    bool Start(const FString& Binary, const FString& Dir, const FString& Version, FString& Error)
    {
        if (!IFileManager::Get().MakeDirectory(*Dir, true)) { Error = TEXT("Cannot create Worker output directory"); return false; }
        Server = MakeUnique<FNetworkServerNode>();
        if (Server->GetListeningPort() <= 0) { Error = TEXT("Cannot bind Worker peer socket"); return false; }
        const FString Args = FString::Printf(
            TEXT("-ServerPID %u -ServerPort %d -InterchangeDispatcherVersion %s -ResultFolder \"%s\" -CarlaWorkerIdleSeconds=3 -abslog=\"%s\""),
            FPlatformProcess::GetCurrentProcessId(), Server->GetListeningPort(), *Version, *Dir,
            *FPaths::Combine(Dir, TEXT("worker.log")));
        Process = FPlatformProcess::CreateProc(*Binary, *Args, false, true, true, &Pid, 0, nullptr, nullptr);
        if (!Process.IsValid()) { Error = TEXT("Cannot spawn real Worker"); return false; }
        if (!Server->Accept(TEXT("Native Worker peer"), 10.0)) { Error = TEXT("Worker did not connect"); return false; }
        Queue.SetNetworkInterface(Server.Get());
        return true;
    }
    bool WaitExit(int32 ExpectedCode, FString& Error)
    {
        const double Deadline = FPlatformTime::Seconds() + 8.0;
        while (FPlatformProcess::IsProcRunning(Process) && FPlatformTime::Seconds() < Deadline)
            FPlatformProcess::Sleep(0.01);
        if (FPlatformProcess::IsProcRunning(Process))
        { Error = TEXT("Worker exit timeout; forced cleanup is not PASS"); return false; }
        int32 Code = -1;
        if (!FPlatformProcess::GetProcReturnCode(Process, &Code) || Code != ExpectedCode)
        { Error = FString::Printf(TEXT("Worker exit code %d, expected %d"), Code, ExpectedCode); return false; }
        FPlatformProcess::WaitForProc(Process);
        return true;
    }
    TSharedPtr<ICommand> Receive(ECommandId Expected, FString& Error)
    {
        const double Deadline = FPlatformTime::Seconds() + 12.0;
        while (FPlatformTime::Seconds() < Deadline)
        {
            auto Command = Queue.GetNextCommand(0.02);
            if (!Command)
            {
                if (!Queue.IsValid() || !FPlatformProcess::IsProcRunning(Process)) break;
                continue;
            }
            if (Command->GetType() == ECommandId::Ping)
            {
                FBackPingCommand Reply;
                if (!Queue.SendCommand(Reply, 1.0)) break;
                if (Expected == ECommandId::Ping) return Command;
                continue;
            }
            if (Command->GetType() == ECommandId::BackPing) continue;
            if (Command->GetType() == Expected) return Command;
            Error = TEXT("Unexpected Worker protocol response");
            return nullptr;
        }
        Error = TEXT("Worker response deadline/disconnect");
        return nullptr;
    }
    bool SendTask(int32 Id, const FString& Json, FString& Error)
    {
        FRunTaskCommand Command;
        Command.TaskIndex = Id;
        Command.JsonDescription = Json;
        if (!Queue.SendCommand(Command, 1.0)) { Error = TEXT("Cannot send Worker task"); return false; }
        return true;
    }
    bool Result(int32 Id, bool Success, FString& Path, FString& Error)
    {
        auto Reply = Receive(ECommandId::NotifyEndTask, Error);
        if (!Reply) return false;
        auto* Completed = StaticCast<FCompletedTaskCommand*>(Reply.Get());
        if (Completed->TaskIndex != Id || Completed->ProcessResult != (Success ? ETaskState::ProcessOk : ETaskState::ProcessFailed))
        { Error = TEXT("Worker task index/state mismatch"); return false; }
        if (!Success)
        {
            if (!Completed->JSonResult.IsEmpty() || Completed->JSonMessages.IsEmpty())
            { Error = TEXT("Failed task published a successful result or omitted diagnostics"); return false; }
            Path.Reset();
            return true;
        }
        FJsonLoadSourceCmd::JsonResultParser Result;
        if (!Completed->JSonMessages.IsEmpty() || !Result.FromJson(Completed->JSonResult)
            || !FPaths::FileExists(Result.GetResultFilename()))
        { Error = TEXT("Successful task did not return a valid file"); return false; }
        Path = Result.GetResultFilename();
        return true;
    }
    bool Terminate(FString& Error, int32 ExitCode = 0)
    {
        FTerminateCommand Stop;
        if (!Queue.SendCommand(Stop, 1.0)) { Error = TEXT("Cannot send Worker Terminate"); return false; }
        return WaitExit(ExitCode, Error);
    }
};

TArray64<uint8> MeshBytes(FMeshDescription Mesh)
{
    FLargeMemoryWriter Writer;
    Mesh.Serialize(Writer);
    return TArray64<uint8>(Writer.GetData(), Writer.TotalSize());
}

bool ReadMesh(const FString& Filename, const CarlaUfbxMesh::FStaticScene& Source,
    const FString& Key, const FTransform& Transform, FString& Error)
{
    CarlaUfbxMesh::FStaticPayload Expected;
    if (!CarlaUfbxMesh::FetchStaticPayload(Source, Key, Transform, Expected, Error)) return false;
    TArray64<uint8> Data;
    if (!FFileHelper::LoadFileToArray(Data, *Filename) || Data.IsEmpty()) return false;
    FLargeMemoryReader Reader(Data.GetData(), Data.Num());
    FMeshDescription Mesh;
    Mesh.Serialize(Reader);
    bool Skinned = true;
    Reader << Skinned;
    return !Reader.IsError() && Reader.Tell() == Data.Num() && !Skinned && MeshBytes(Mesh) == MeshBytes(Expected.Mesh);
}

bool WorkerSuite(const FString& Input, const FString& Dir, const FString& Binary,
    TArray<FString>& Checks, TSet<FString>& Files, TArray<uint32>& Pids, FString& Hash, FString& Error)
{
    if (Binary.Contains(TEXT("\"")) || Dir.Contains(TEXT("\"")))
    { Error = TEXT("Quotes in Worker command paths are unsupported"); return false; }
    auto Check = [&](bool Good, const TCHAR* Name)
    {
        if (!Good) { Error = FString::Printf(TEXT("%s: %s"), Name, *Error); return false; }
        Checks.Add(Name); return true;
    };
    CarlaUfbxMesh::FStaticScene Source;
    if (!CarlaUfbxMesh::ImportScene(Input, Source, Error) || Source.Meshes.Num() != 2) return false;
    Hash = Source.SourceSha256;
    FWorkerPeer Peer;
    if (!Peer.Start(Binary, FPaths::Combine(Dir, TEXT("valid")), DispatcherCommandVersion::ToString(), Error)) return false;
    Pids.Add(Peer.Pid);
    if (!Check(Peer.Receive(ECommandId::Ping, Error).IsValid(), TEXT("valid Worker handshake"))) return false;
    int32 Task = 0;
    FString Path;
    auto Exchange = [&](const FString& Json, bool Success)
    { const int32 Id = Task++; return Peer.SendTask(Id, Json, Error) && Peer.Result(Id, Success, Path, Error); };
    auto Load = [&](const FString& Filename)
    { return FJsonLoadSourceCmd(TEXT("FBX"), Filename, true, true, true, true).ToJson(); };
    auto Mesh = [&](const FString& Key, const FTransform& Transform)
    { return FJsonFetchMeshPayloadCmd(TEXT("FBX"), Key, Transform).ToJson(); };
    auto ReadGraph = [&]()
    {
        TStrongObjectPtr<UInterchangeBaseNodeContainer> Graph(NewObject<UInterchangeBaseNodeContainer>());
        Graph->LoadFromFile(Path);
        return VerifyGraph(Source, *Graph, Error);
    };
    if (!Check(Exchange(Load(Input), true) && ReadGraph(), TEXT("IPC load and graph readback"))) return false;
    Files.Add(Path);
    const FTransform Transforms[] = {FTransform::Identity, FTransform(FVector(17, -23, 31)),
        FTransform(FQuat::Identity, FVector(3, 7, 11), FVector(-2, 3, 0.5))};
    for (const auto& Item : Source.Meshes)
        for (const FTransform& Transform : Transforms)
        {
            if (!Check(Exchange(Mesh(Item.PayloadKey, Transform), true)
                && ReadMesh(Path, Source, Item.PayloadKey, Transform, Error), TEXT("IPC mesh consumer readback"))) return false;
            Files.Add(Path);
        }
    // Queue three commands without waiting: task-index/result isolation must hold
    // across a parser failure in the middle of the ordered stream.
    const int32 Burst = Task;
    if (!Peer.SendTask(Task++, Mesh(Source.Meshes[0].PayloadKey, Transforms[0]), Error)
        || !Peer.SendTask(Task++, Mesh(TEXT("unknown-key"), Transforms[0]), Error)
        || !Peer.SendTask(Task++, Mesh(Source.Meshes[1].PayloadKey, Transforms[1]), Error)) return false;
    for (int32 Index = 0; Index < 3; ++Index)
    {
        const bool Success = Index != 1;
        if (!Check(Peer.Result(Burst + Index, Success, Path, Error)
            && (!Success || ReadMesh(Path, Source, Source.Meshes[Index == 0 ? 0 : 1].PayloadKey,
                Transforms[Index == 0 ? 0 : 1], Error)), TEXT("queued requests preserve task and error isolation"))) return false;
    }
    if (!Check(Exchange(TEXT("{}"), false), TEXT("malformed task explicitly fails"))) return false;
    if (!Check(Exchange(FJsonLoadSourceCmd(TEXT("OBJ"), Input, true, true, true, true).ToJson(), false),
        TEXT("unknown translator explicitly fails"))) return false;
    if (!Check(Exchange(FJsonFetchAnimationQueriesCmd(TEXT("FBX"), TEXT("[]")).ToJson(), false),
        TEXT("unsupported animation task explicitly fails"))) return false;
    if (!Check(Exchange(FJsonLoadSourceCmd(TEXT("FBX"), Input, true, false, true, true).ToJson(), false),
        TEXT("unsupported conversion task explicitly fails"))) return false;
    if (!Check(Exchange(Load(Input + TEXT(".missing")), false)
        && Exchange(Mesh(Source.Meshes[0].PayloadKey, Transforms[0]), false), TEXT("failed reload cannot serve old payload"))) return false;
    if (!Check(Exchange(Load(Input), true) && ReadGraph(), TEXT("Worker reload recovers after failure"))) return false;
    Files.Add(Path);
    if (!Check(Exchange(Mesh(Source.Meshes[0].PayloadKey, Transforms[0]), true)
        && ReadMesh(Path, Source, Source.Meshes[0].PayloadKey, Transforms[0], Error), TEXT("payload recovers after failed tasks"))) return false;
    if (!Check(Peer.Terminate(Error), TEXT("normal Terminate exits zero without kill"))) return false;

    const FString Versions[] = {
        FString::Printf(TEXT("%d.0.0.0"), DispatcherCommandVersion::GetMajor() + 1),
        FString::Printf(TEXT("%d.bad.0.bad"), DispatcherCommandVersion::GetMajor())};
    for (int32 Index = 0; Index < 2; ++Index)
    {
        FWorkerPeer Rejected;
        const FString BadDir = FPaths::Combine(Dir, FString::Printf(TEXT("version-%d"), Index));
        if (!Rejected.Start(Binary, BadDir, Versions[Index], Error)) return false;
        Pids.Add(Rejected.Pid);
        auto Response = Rejected.Receive(ECommandId::Error, Error);
        if (!Check(Response && !StaticCast<FErrorCommand*>(Response.Get())->ErrorMessage.IsEmpty()
            && Rejected.Terminate(Error, 1), TEXT("bad version returns protocol error and nonzero exit"))) return false;
    }
    {
        FWorkerPeer Lost;
        if (!Lost.Start(Binary, FPaths::Combine(Dir, TEXT("disconnect")), DispatcherCommandVersion::ToString(), Error)) return false;
        Pids.Add(Lost.Pid);
        if (!Lost.Receive(ECommandId::Ping, Error)) return false;
        Lost.Queue.Disconnect(0);
        Lost.Server.Reset();
        if (!Check(Lost.WaitExit(1, Error), TEXT("lost peer exits nonzero within deadline"))) return false;
    }
    // Port is reserved but not listening: no unrelated local service is contacted.
    {
        FWorkerPeer Refused;
        ISocketSubsystem* Sockets = ISocketSubsystem::Get();
        auto Address = Sockets->CreateInternetAddr();
        Address->SetLoopbackAddress();
        Address->SetPort(0);
        FSocket* Reservation = Sockets->CreateSocket(NAME_Stream, TEXT("Worker refusal control"), Address->GetProtocolType());
        ON_SCOPE_EXIT { if (Reservation) { Reservation->Close(); Sockets->DestroySocket(Reservation); } };
        if (!Reservation || !Reservation->Bind(*Address)) { Error = TEXT("Cannot reserve non-listening port"); return false; }
        const int32 Port = Reservation->GetPortNo();
        const FString Args = FString::Printf(TEXT("-ServerPID %u -ServerPort %d -InterchangeDispatcherVersion %s -ResultFolder \"%s\""),
            FPlatformProcess::GetCurrentProcessId(), Port, *DispatcherCommandVersion::ToString(), *FPaths::Combine(Dir, TEXT("no-server")));
        Refused.Process = FPlatformProcess::CreateProc(*Binary, *Args, false, true, true, &Refused.Pid, 0, nullptr, nullptr);
        if (!Refused.Process.IsValid()) { Error = TEXT("Cannot spawn connection-refusal case"); return false; }
        Pids.Add(Refused.Pid);
        if (!Check(Refused.WaitExit(1, Error), TEXT("connection failure exits nonzero"))) return false;
    }
    return true;
}
}

int32 RunNativeWorkerChecks(const FString& Input, const FString& ResultDir, const FString& Output)
{
    FString Binary, Error, Hash;
    TArray<FString> Checks;
    TSet<FString> Files;
    TArray<uint32> Pids;
    const bool Success = FParse::Value(FCommandLine::Get(), TEXT("worker="), Binary)
        && FPaths::FileExists(Binary) && WorkerSuite(Input, ResultDir, Binary, Checks, Files, Pids, Hash, Error);
    TSharedRef<FJsonObject> Report = MakeShared<FJsonObject>();
    Report->SetStringField(TEXT("stage"), TEXT("ue-ufbx-worker-static"));
    Report->SetStringField(TEXT("scope"), TEXT("Real InterchangeWorker static ufbx over UE command-queue TCP; not production WorkerHandler, full FBX, Editor or Cook"));
    Report->SetStringField(TEXT("status"), Success ? TEXT("PASS") : TEXT("FAIL"));
    Report->SetStringField(TEXT("error"), Error);
    Report->SetStringField(TEXT("source_sha256"), Hash);
    Report->SetStringField(TEXT("worker"), Binary);
    Report->SetStringField(TEXT("protocol_version"), DispatcherCommandVersion::ToString());
    Report->SetNumberField(TEXT("parent_pid"), FPlatformProcess::GetCurrentProcessId());
    Report->SetNumberField(TEXT("self_tests"), Checks.Num());
    TArray<TSharedPtr<FJsonValue>> Names, Outputs, Processes;
    for (const FString& Name : Checks) Names.Add(MakeShared<FJsonValueString>(Name));
    for (const FString& Path : Files) Outputs.Add(MakeShared<FJsonValueString>(Path));
    for (uint32 Pid : Pids) Processes.Add(MakeShared<FJsonValueNumber>(Pid));
    Report->SetArrayField(TEXT("checks"), Names);
    Report->SetArrayField(TEXT("files"), Outputs);
    Report->SetArrayField(TEXT("worker_pids"), Processes);
    FString Text;
    FJsonSerializer::Serialize(Report, TJsonWriterFactory<>::Create(&Text));
    if (!FFileHelper::SaveStringToFile(Text, *Output)) return 3;
    return Success ? 0 : 2;
}
#endif
