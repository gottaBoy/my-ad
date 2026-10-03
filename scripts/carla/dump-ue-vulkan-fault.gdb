set pagination off
set confirm off
set breakpoint pending on
set disable-randomization off
set may-call-functions off
# Continue handled ensures, but stop before the first failing Vulkan check executes.
handle SIGTRAP nostop noprint nopass
python
import os
import sys
sys.path.insert(0, os.environ["CARLA_GDB_SCRIPTS"])
import gdb_vulkan_fault_capture
gdb_vulkan_fault_capture.start()
end
