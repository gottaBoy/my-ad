# Compose 实施运行手册

## 1. 角色分工

同一份仓库复制到两台机器：

| 主机 | `.env` | Compose | 主要服务 |
|---|---|---|---|
| x86_64 RTX | `HOST_ROLE=sim-x86` | `compose.sim-x86.yaml` | AWSIM、ground truth、场景元数据 |
| DGX Spark | `HOST_ROLE=dgx` | `compose.dgx.yaml` | Autoware、录包、回放、训练、部署 |

两端必须使用相同的 `ROS_DOMAIN_ID`、ROS 发行版和 RMW。主路径默认使用
CycloneDDS；跨发行版或需要路由隔离时才启用 Zenoh。

## 2. 镜像契约

Compose 不负责猜测外部项目的镜像 tag。`.env` 必须提供：

- `AWSIM_IMAGE`：包含官方 x86_64 Linux Player。
- `AUTOWARE_IMAGE`：包含已验证的 ARM64 Autoware。
- `ROS_BASE_IMAGE`：与 Recorder 的 ROS 发行版一致。
- `FOXGLOVE_IMAGE`：包含 `foxglove_bridge`。
- `SCENARIO_IMAGE`：包含 Scenario Simulator。
- `ISAAC_IMAGE`：包含与 DGX Spark 匹配的 Isaac Sim。
- `TENSORRT_IMAGE`：与导出模型和目标 runtime 匹配。

正式部署时所有值都要固定 tag 和 digest。没有 digest 的值只允许用于
预检阶段，不能用于数据生产或模型基线。

## 3. 运行证据

每个测试至少保存：

```text
artifacts/<test-id>/
  host-info/
  image-info/
  compose-config/
  logs/
  topic-snapshot/
  bag-metadata/
  metrics/
  decision.md
```

服务显示 `running` 不等于系统通过。必须另外验证：

- GPU 能力。
- ROS 话题、QoS、TF 和 `/clock`。
- 传感器频率和丢包。
- 真值时间戳对齐。
- 控制指令往返。
- bag 关闭、回放和重复运行。

## 4. 失败处理

任何前置项失败时，结果标为 `BLOCKED`，不要继续启动训练、TensorRT 或 RL。

典型处理：

| 失败 | 处理 |
|---|---|
| AWSIM ARM64 不可用 | 使用 x86_64 AWSIM + DGX Spark 主路径 |
| DGX 无 GPU 容器能力 | 修复驱动和 NVIDIA Container Toolkit |
| DDS 发现失败 | 检查 host network、接口、Domain ID 和 CycloneDDS |
| 大消息丢包 | 录包移到 x86 源端或升级网络 |
| bag 无法回放 | 检查 storage plugin、QoS 和完整关闭流程 |
| BEVFormer 扩展无法编译 | 锁定依赖矩阵并单独构建 ARM64 训练镜像 |
