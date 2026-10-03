set pagination off
set confirm off
set breakpoint pending on
set disable-randomization off
handle SIGTRAP nostop noprint nopass
python
import os
import sys
sys.path.insert(0, os.environ["CARLA_GDB_SCRIPTS"])
import gdb_vulkan_device_capture
gdb_vulkan_device_capture.start()
end
