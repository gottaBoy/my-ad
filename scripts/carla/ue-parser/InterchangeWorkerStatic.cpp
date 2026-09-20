#if CARLA_INTERCHANGE_UFBX_STATIC
#include "InterchangeWorkerImpl.h"
#include "HAL/FileManager.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "UObject/StrongObjectPtr.h"

using namespace UE::Interchange;

namespace
{
FString ProtocolError(const FString& Text)
{
    TStrongObjectPtr<UInterchangeResultsContainer> Results(NewObject<UInterchangeResultsContainer>());
    auto* Error = Results->Add<UInterchangeResultError_Generic>();
    Error->Text = FText::FromString(Text);
    return Error->ToJson();
}

FCompletedTaskCommand ExecuteStaticTask(FInterchangeFbxParser& Parser,
    const FRunTaskCommand& Task, const FString& Folder)
{
    FCompletedTaskCommand Completed;
    Completed.TaskIndex = Task.TaskIndex;
    Completed.ProcessResult = ETaskState::ProcessFailed;
    FJsonLoadSourceCmd Load;
    FJsonFetchMeshPayloadCmd Mesh;
    FJsonFetchAnimationQueriesCmd Animation;
    FJsonFetchPayloadCmd Generic;
    FString Path;
    if (Load.FromJson(Task.JsonDescription) && Load.GetTranslatorID() == TEXT("FBX"))
    {
        Parser.SetConvertSettings(Load.GetDoesConvertScene(), Load.GetDoesForceFrontXAxis(),
            Load.GetDoesConvertSceneUnit(), Load.GetDoesKeepFbxNamespace());
        Parser.LoadFbxFile(Load.GetSourceFilename(), Folder);
        Path = Parser.GetResultFilepath();
    }
    else if (Animation.FromJson(Task.JsonDescription) && Animation.GetTranslatorID() == TEXT("FBX"))
    {
        Parser.FetchAnimationBakeTransformPayloads(Animation.GetQueriesJsonString(), Folder);
    }
    else if (Mesh.FromJson(Task.JsonDescription) && Mesh.GetTranslatorID() == TEXT("FBX"))
    {
        const FString Id = Parser.FetchMeshPayload(Mesh.GetPayloadKey(), Mesh.GetMeshGlobalTransform(), Folder);
        if (!Id.IsEmpty()) Path = Parser.GetResultPayloadFilepath(Id);
    }
    else if (Generic.FromJson(Task.JsonDescription) && Generic.GetTranslatorID() == TEXT("FBX"))
    {
        Parser.FetchPayload(Generic.GetPayloadKey(), Folder);
    }
    else
    {
        Completed.JSonMessages.Add(ProtocolError(TEXT("Invalid static Worker command or translator")));
        return Completed;
    }
    Completed.JSonMessages = Parser.GetJsonLoadMessages();
    if (Completed.JSonMessages.IsEmpty() && !Path.IsEmpty() && IFileManager::Get().FileSize(*Path) > 0)
    {
        FJsonLoadSourceCmd::JsonResultParser Result;
        Result.SetResultFilename(Path);
        Completed.JSonResult = Result.ToJson();
        Completed.ProcessResult = ETaskState::ProcessOk;
    }
    else if (Completed.JSonMessages.IsEmpty())
    {
        Completed.JSonMessages.Add(ProtocolError(TEXT("Parser did not publish a verified result file")));
    }
    return Completed;
}
}

FInterchangeWorkerImpl::FInterchangeWorkerImpl(int32 PID, int32 Port, FString& Folder)
    : ServerPID(PID), ServerPort(Port), PingStartCycle(0), ResultFolder(Folder)
{}

bool FInterchangeWorkerImpl::Run(const FString& VersionError)
{
    if (ServerPID <= 0 || ServerPort <= 0 || ServerPort > 65535 || ResultFolder.IsEmpty()) return false;
    if (!NetworkInterface.Connect(TEXT("Interchange static Worker"), ServerPort, 3.0)) return false;
    CommandIO.SetNetworkInterface(&NetworkInterface);
    double IdleSeconds = 10.0;
    FParse::Value(FCommandLine::Get(), TEXT("CarlaWorkerIdleSeconds="), IdleSeconds);
    if (!FMath::IsFinite(IdleSeconds) || IdleSeconds < 0.5 || IdleSeconds > 30.0) return false;
    if (!VersionError.IsEmpty())
    {
        FErrorCommand Error;
        Error.ErrorMessage = ProtocolError(VersionError);
        if (!CommandIO.SendCommand(Error, 1.0)) return false;
        const double Deadline = FPlatformTime::Seconds() + 3.0;
        while (FPlatformTime::Seconds() < Deadline)
        {
            const auto Command = CommandIO.GetNextCommand(0.02);
            if (Command && Command->GetType() == ECommandId::Terminate) break;
            if (!CommandIO.IsValid()) break;
        }
        return false;
    }
    FPingCommand Ping;
    if (!CommandIO.SendCommand(Ping, 1.0)) return false;
    double LastReceive = FPlatformTime::Seconds();
    while (FPlatformProcess::IsApplicationRunning(ServerPID) && CommandIO.IsValid())
    {
        auto Command = CommandIO.GetNextCommand(0.02);
        if (!Command)
        {
            // ReceiveMessage may not flag a clean FIN. The bounded idle deadline
            // also covers a stalled/lost peer while the parent process is alive.
            if (FPlatformTime::Seconds() - LastReceive > IdleSeconds) return false;
            continue;
        }
        LastReceive = FPlatformTime::Seconds();
        switch (Command->GetType())
        {
        case ECommandId::Ping:
        {
            FBackPingCommand Reply;
            if (!CommandIO.SendCommand(Reply, 1.0)) return false;
            break;
        }
        case ECommandId::BackPing: break;
        case ECommandId::RunTask:
        {
            // The complete parser/result/messages transaction is serial on the
            // Worker main thread. No state leaks through concurrent reloads.
            const auto& Task = *StaticCast<FRunTaskCommand*>(Command.Get());
            FCompletedTaskCommand Completed = ExecuteStaticTask(FbxParser, Task, ResultFolder);
            if (!CommandIO.SendCommand(Completed, 1.0)) return false;
            LastReceive = FPlatformTime::Seconds();
            break;
        }
        case ECommandId::Terminate:
            CommandIO.Disconnect(0);
            return true;
        default:
        {
            FErrorCommand Error;
            Error.ErrorMessage = ProtocolError(TEXT("Unsupported static Worker protocol command"));
            if (!CommandIO.SendCommand(Error, 1.0)) return false;
            break;
        }
        }
    }
    return false;
}
#endif
