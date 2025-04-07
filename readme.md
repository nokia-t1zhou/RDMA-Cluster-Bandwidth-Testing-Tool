# RDMA 集群带宽测试工具

## 概述

本工具 `fullmesh_rping.py` 旨在自动化执行RDMA（Remote Direct Memory Access）网络的性能测试。它通过在指定的节点之间运行 `ibv_rc_pingpong` 工具来测量带宽，并将结果整理成CSV文件，方便用户分析RDMA网络的性能表现。

本工具支持以下功能：

- **多种测试模式**: 支持 `half_full` (半双工) 和 `full_mesh` (全互联) 两种测试模式，灵活适应不同的测试场景。
- **灵活的节点配置**:  可以通过指定IP地址列表文件或使用默认IP地址范围来配置测试节点。
- **并发执行**:  利用多线程技术并发执行测试，显著缩短测试时间。
- **详细日志**:  提供全面的日志记录，包括测试过程中的信息、警告和错误，方便问题排查。
- **结果分析**:  自动解析测试结果，并将带宽数据和失败的测试记录分别保存到CSV文件中。
- **易于使用**:  通过简单的命令行参数和环境变量配置，即可快速启动和执行性能测试。

----
## 用法

### 前提条件

- **Python环境**: 确保您的测试环境安装了Python 3.6 或更高版本。
- **RDMA环境**:  测试节点需要已配置好RDMA网络环境，包括安装必要的驱动和软件，例如 Mellanox OFED。
- **ibverbs工具包**:  测试节点上需要安装 `ibverbs` 工具包，其中包含 `ibv_rc_pingpong` 工具。
- **SSH访问**:  运行测试的机器需要能够通过SSH免密码登录到所有测试节点。您可能需要配置SSH密钥或使用SSH Agent。
- **环境变量**:  需要设置以下环境变量：
    - `PERF_TEST_USER`:  SSH 登录远程节点的用户名，默认为 `admin`。
    - `PERF_TEST_PASS`:  SSH 登录远程节点的密码 (可选，如果使用SSH密钥可以不设置)。

### 安装 (无需安装)

本工具是一个独立的Python脚本，无需安装。您只需要下载 `fullmesh_rping.py` 文件即可开始使用。

### 运行测试

- **准备IP地址列表文件**:
   请创建一个文本文件，例如 `ips_list.txt`，每行一个IP地址。
```bash
    192.168.1.10
    192.168.1.11
    192.168.1.12
```

- **运行脚本**:

```bash
python3 fullmesh_rping.py --ip_file_path ips_list.txt
```

#### 指定测试模式:

使用 --mode 参数指定测试模式。默认为 half_full 模式。

- 半双工模式 (half_full): 测试节点两两之间进行单向带宽测试。
- 全互联模式 (full_mesh): 测试所有节点之间互相进行双向带宽测试。
```bash
python perftest.py --mode full_mesh --ip_file_path ips_list.txt
```

----

## 性能

**128主机/8网口 half_full**
    耗时：3:58
**128主机/8网口 full_mesh**
    耗时：9:12

----

## 详细信息
### 工作流程

- **配置加载**: PerfTestConfig 类负责加载配置信息，包括测试模式、IP地址列表、SSH 用户名和密码等。IP地址列表可以从指定的文件读取。
- **目录创建**: PerfTestRunner 初始化时会创建一个以时间戳命名的结果目录 perftest_result_YYYY-MM-DD_HH-MM-SS，用于存放测试结果和日志文件。
- **远程目录准备**: 在所有测试节点上创建或清空 ./rping 目录，用于存放远程测试的日志文件。
- **生成测试对**: 根据指定的测试模式 (half_full 或 full_mesh)，生成需要进行测试的IP地址对。
    - half_full 模式下，生成节点两两组合的测试对 (例如: (node1, node2), (node1, node3), (node2, node3)...)。
    - full_mesh 模式下，生成所有节点之间互相测试的测试对 (例如: (node1, node2), (node2, node1), (node1, node3), (node3, node1), (node2, node3), (node3, node2)...)。
- **批量测试**: 为了更有效地利用资源和控制并发度，测试对会被分成批次进行处理。batch_generator 函数负责生成批次，确保每个批次内的测试尽可能不涉及重复的节点，以减少资源竞争。
- **并发执行测试**: 对于每个批次的测试对，使用线程池 ThreadPoolExecutor 并发地在客户端和服务器节点上执行 ibv_rc_pingpong 命令。
- **端口分配**: 使用共享的端口池 port_pool 和锁 port_lock 来管理端口分配，避免端口冲突。端口范围为 35000 到 45000。
- **命令执行**: _execute_test 函数负责在客户端和服务器节点上通过SSH执行 ibv_rc_pingpong 命令。服务器端运行 ibv_rc_pingpong -s (server 模式)，客户端运行 ibv_rc_pingpong -c <server_ip> (client 模式)。
- **网络接口**: 测试会在预定义的网络接口列表 ('mlx5_0', 'mlx5_1', 'mlx5_4', 'mlx5_5', 'mlx5_6', 'mlx5_7', 'mlx5_10', 'mlx5_11') 上循环执行。
- **结果收集**: 测试完成后，collect_results 函数通过 scp 命令从所有测试节点的 ./rping 目录下载日志文件到本地结果目录 perftest_result_YYYY-MM-DD_HH-MM-SS/rping_results/ 下。
- **日志解析与结果分析**: process_log_files 函数解析下载的日志文件，提取 ibv_rc_pingpong 输出中的带宽数据。
- **结果保存**: 解析成功的带宽数据会被保存到 rdma_analysis_passed.csv 文件中，包含源IP、源接口、目标IP、目标接口和带宽 (Mbps) 等信息。解析失败或未找到带宽数据的测试对信息会被保存到 rdma_analysis_failed.csv 文件中，方便用户检查失败原因。
- **进程清理**: cleanup 函数在测试开始前和结束后都会执行，通过 pkill -f ibv_rc_pingpong 命令尝试清理远程节点上可能残留的 ibv_rc_pingpong 进程。
- **日志记录**: 整个测试过程使用 logging 模块进行日志记录。日志会同时输出到 perftest.log 文件和终端。使用了多进程日志队列 multiprocessing.Queue 和 QueueHandler 来处理多线程环境下的日志记录，避免日志信息混乱。
### 配置
- **测试模式 (mode)**: 通过 --mode 命令行参数配置，可选 half_full 或 full_mesh。
- **IP地址列表 (ip_file_path)**: 通过 --ip_file_path 命令行参数指定IP地址列表文件路径。如果文件不存在，则使用默认IP地址范围。
- **SSH 用户名 (PERF_TEST_USER)**: 通过环境变量 PERF_TEST_USER 配置。
- **SSH 密码 (PERF_TEST_PASS)**: 通过环境变量 PERF_TEST_PASS 配置，可选，可以使用SSH密钥代替密码。
- **网络接口 (network_interfaces)**: 在 PerfTestConfig 类中硬编码定义，默认为 ('mlx5_0', 'mlx5_1', 'mlx5_4', 'mlx5_5', 'mlx5_6', 'mlx5_7', 'mlx5_10', 'mlx5_11')。可以根据实际环境修改。
- **端口范围 (min_port, max_port)**: 在 PerfTestConfig 类中硬编码定义，默认为 35000 到 45000。可以根据实际环境修改。
**测试模式**
    - **half_full** (半双工): 适用于测试节点两两之间的单向带宽性能。例如，如果您想测试节点 A 到节点 B 的带宽，以及节点 C 到节点 D 的带宽，可以使用 half_full 模式。
    - **full_mesh** (全互联): 适用于测试所有节点之间互相的带宽性能。例如，如果您想测试节点 A 到节点 B 和节点 B 到节点 A 的带宽，以及节点 A 到节点 C 和节点 C 到节点 A 的带宽，等等，可以使用 full_mesh 模式。


#### 端口管理
为了避免多个并发的 ibv_rc_pingpong 测试使用相同的端口导致冲突，脚本使用了端口池 port_pool 和锁 port_lock 来管理端口分配。端口范围默认为 35000 到 45000。在执行测试前，会从端口池中取出一个端口，测试结束后，端口会返回到端口池中，供后续测试使用。如果默认端口范围不足以支持并发测试的数量，您可以适当扩大端口范围。