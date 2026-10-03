set pagination off
set confirm off
set breakpoint pending on
set disable-randomization off
# This diagnostic continues past UE handled-ensure debug traps to reach render calls.
handle SIGTRAP nostop noprint nopass
python
import os
import sys
sys.path.insert(0, os.environ["CARLA_GDB_SCRIPTS"])
import gdb_vulkan_pipeline_capture
end
