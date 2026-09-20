import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FACADE_DIR = ROOT / "scripts/carla/interchange"
FACADE = FACADE_DIR / "f1-parser-facade.cpp"
CONTRACT = FACADE_DIR / "f1-parser-contract-test.cpp"
UE = ROOT / "third_party/unreal-engine"

PARSER_H = UE / "Engine/Plugins/Interchange/Runtime/Source/Parsers/Fbx/Public/InterchangeFbxParser.h"
PARSER_CPP = UE / "Engine/Plugins/Interchange/Runtime/Source/Parsers/Fbx/Private/InterchangeFbxParser.cpp"
TRANSLATOR_CPP = UE / "Engine/Plugins/Interchange/Runtime/Source/Import/Private/Fbx/InterchangeFbxTranslator.cpp"
WORKER_CPP = UE / "Engine/Source/Programs/InterchangeWorker/Private/InterchangeWorkerImpl.cpp"
TASK_H = UE / "Engine/Plugins/Interchange/Runtime/Source/Dispatcher/Public/InterchangeDispatcherTask.h"
TASK_CPP = UE / "Engine/Plugins/Interchange/Runtime/Source/Dispatcher/Private/InterchangeDispatcherTask.cpp"

class InterchangeParserFacadeTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which(os.environ.get("CXX", "clang++")),
                         "C++ compiler required; run in native ARM64 carla-dev Docker")
    def test_facade_is_backend_neutral_and_compilable(self):
        facade_text = (FACADE_DIR / "f1-parser-facade.h").read_text(encoding="utf-8")
        self.assertNotIn("CoreMinimal", facade_text)
        self.assertNotIn("UnrealEd", facade_text)
        self.assertNotIn("UInterchange", facade_text)
        compiler = os.environ.get("CXX", "clang++")
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "f1-parser-contract"
            subprocess.run(
                [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                 str(FACADE), str(CONTRACT), "-o", str(binary)],
                cwd=ROOT, check=True, capture_output=True, text=True, timeout=60)
            result = subprocess.run([str(binary)], capture_output=True, text=True,
                                        check=True, timeout=30)
        self.assertIn("F1 parser facade contract PASS", result.stdout)

    def test_facade_contract_is_grounded_in_real_parser_worker_symbols(self):
        parser_h = PARSER_H.read_text(encoding="utf-8")
        parser_cpp = PARSER_CPP.read_text(encoding="utf-8")
        translator = TRANSLATOR_CPP.read_text(encoding="utf-8")
        worker = WORKER_CPP.read_text(encoding="utf-8")
        task_h = TASK_H.read_text(encoding="utf-8")
        task_cpp = TASK_CPP.read_text(encoding="utf-8")
        for token in ("LoadFbxFile", "FetchPayload", "FetchMeshPayload",
                      "FetchAnimationBakeTransformPayloads", "SetConvertSettings"):
            self.assertIn(token, parser_h)
        for token in ("FbxParser.LoadFbxFile", "FbxParser.FetchMeshPayload",
                      "CreateLoadFbxFileCommand", "CreateFetchMeshPayloadFbxCommand",
                      "FJsonLoadSourceCmd", "FJsonFetchMeshPayloadCmd"):
            self.assertIn(token, translator)
        for token in ("LoadSourceCommand.FromJson", "FetchMeshPayloadCommand.FromJson",
                      "FbxParser.LoadFbxFile", "FbxParser.FetchMeshPayload",
                      "FJsonLoadSourceCmd::JsonResultParser",
                      "ResultPayloadsUniqueId"):
            self.assertIn(token, worker + parser_cpp)
        for token in ("LoadSource", "Payload", "GlobalMeshTransform",
                      "SourceFile", "ConvertScene", "ForceFrontXAxis",
                      "ConvertSceneUnit", "KeepFbxNamespace"):
            self.assertIn(token, task_h + task_cpp)
        for token in ("-ServerPID", "-ServerPort",
                      "-InterchangeDispatcherVersion", "-ResultFolder"):
            self.assertIn(token, (UE / "Engine/Source/Programs/InterchangeWorker/Private/InterchangeWorker.cpp").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
