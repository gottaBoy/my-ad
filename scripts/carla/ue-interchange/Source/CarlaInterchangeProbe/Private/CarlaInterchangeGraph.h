#pragma once

#include "CarlaUfbxInterchange.h"

int32 RunLegacyHierarchyChecks(const FString& Input, const FString& ResultDir, const FString& Output);

#if CARLA_INTERCHANGE_UFBX_STATIC
int32 RunNativeParserChecks(const FString& Input, const FString& ResultDir, const FString& Output);
int32 RunNativeWorkerChecks(const FString& Input, const FString& ResultDir, const FString& Output);
#endif
