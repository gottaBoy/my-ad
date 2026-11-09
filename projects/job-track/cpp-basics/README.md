# C++ Basics：VehicleState

这是就业强化教程第 1 周的第一个可运行练习。
它只依赖 C++17、CMake 和 CTest，不依赖 ROS 2、GPU 或 Waymax。

## 目录

```text
include/vehicle_state.hpp  # 对外类型和函数声明
src/vehicle_state.cpp      # 单位转换、有限值和范围校验
src/main.cpp               # 最小示例程序
tests/state_test.cpp       # 正常值和非法值测试
CMakeLists.txt             # 构建和 CTest 注册
```

## 构建

在仓库根目录执行：

```bash
cmake -S projects/job-track/cpp-basics \
  -B /tmp/my-ad-job-track-cpp-build \
  -DCMAKE_BUILD_TYPE=Debug
cmake --build /tmp/my-ad-job-track-cpp-build
ctest --test-dir /tmp/my-ad-job-track-cpp-build --output-on-failure
/tmp/my-ad-job-track-cpp-build/vehicle_state_demo
```

预期：

```text
100% tests passed
x=10.000 m, y=2.000 m, yaw=0.000 rad, speed=10.000 m/s
```

## 你要读懂什么

- `x_m`、`y_m`：地图/全局坐标，单位米。
- `yaw_rad`：朝向角，单位弧度。
- `speed_mps`：速度，单位米每秒。
- `speed_kph()`：只做 `m/s -> km/h`，不改变原状态。
- `validate()`：拒绝 NaN、无穷和负速度。
- `describe()`：先校验，再生成给人看的字符串。

## 练习顺序

1. 先不改代码，完成构建和测试。
2. 将 `speed_kph()` 的转换写在纸上：`m/s × 3.6`。
3. 增加一个 yaw 范围测试，但先说明你选择该范围的依据。
4. 故意把测试中的 `36.0` 改成 `35.0`，确认测试会失败。
5. 恢复测试，再运行一次。

不要把“测试通过”解释成车辆安全；它只证明这个小模块满足当前写下的契约。
