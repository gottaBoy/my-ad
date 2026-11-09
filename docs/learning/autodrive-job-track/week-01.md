# Week 01：C++、Linux、CMake 和测试

## 状态

- 学习状态：`NOT_STARTED`
- 实验状态：`NOT_RUN`
- 推荐入口：`projects/job-track/cpp-basics/`

## 目标

本周结束时，能独立构建、测试和解释一个小型 C++ 模块：

```text
头文件声明 -> .cpp 实现 -> CMake target -> CTest -> README
```

## 关键词

| English | 中文 | 本周要会 |
| --- | --- | --- |
| RAII | 资源获取即初始化 | 让对象生命周期管理资源 |
| const/reference | 常量/引用 | 避免不必要复制和修改 |
| STL | 标准库 | `vector`、`array`、`string` |
| CMake target | 构建目标 | 明确库、可执行文件、测试 |
| CTest | 测试驱动 | 从构建系统运行测试 |
| Debug build | 调试构建 | 保留调试信息，便于 gdb |

## 练习顺序

```bash
cd /home/my/workspace/autodrive/my-ad
cmake -S projects/job-track/cpp-basics \
  -B /tmp/my-ad-job-track-cpp-build -DCMAKE_BUILD_TYPE=Debug
cmake --build /tmp/my-ad-job-track-cpp-build
ctest --test-dir /tmp/my-ad-job-track-cpp-build --output-on-failure
/tmp/my-ad-job-track-cpp-build/vehicle_state_demo
```

然后阅读：

```bash
sed -n '1,220p' projects/job-track/cpp-basics/include/vehicle_state.hpp
sed -n '1,260p' projects/job-track/cpp-basics/src/vehicle_state.cpp
sed -n '1,240p' projects/job-track/cpp-basics/tests/state_test.cpp
```

## 你要改一次的内容

增加 `yaw_deg()`，将 `yaw_rad` 转成角度，并补一个测试。要求：

- 不改变 `VehicleState` 内部单位；
- 角度转换写在实现文件；
- 测试 `pi` 弧度约等于 180 度；
- NaN 测试仍保留。

## 验收

- 构建退出码为 0；
- CTest 通过；
- 故意把测试期望改错后，CTest 确实失败；
- 恢复测试后再次通过；
- 能说明为什么 `speed_mps` 不应改名成 `speed`。

## 作品集

保留源码、CMakeLists、测试和 README；不要提交 `/tmp` build 目录。
