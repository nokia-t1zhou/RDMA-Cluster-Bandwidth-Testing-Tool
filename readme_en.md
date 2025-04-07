# RDMA Cluster Bandwidth Testing Tool

## Overview

The tool `fullmesh_rping.py` is designed to automate RDMA (Remote Direct Memory Access) network performance testing. It measures bandwidth by running the `ibv_rc_pingpong` tool between specified nodes and organizes the results into CSV files for easy analysis of RDMA network performance.

This tool supports the following features:

- **Multiple Testing Modes**: Supports `half_full` (half-duplex) and `full_mesh` (full interconnect) modes to flexibly adapt to different testing scenarios.
- **Flexible Node Configuration**: Allows specifying nodes through an IP address list file or using a default IP address range.
- **Concurrent Execution**: Utilizes multithreading for concurrent testing, significantly reducing execution time.
- **Detailed Logging**: Provides comprehensive logging, including information, warnings, and errors during the testing process, to aid troubleshooting.
- **Result Analysis**: Automatically parses test results and saves bandwidth data and failed tests separately into CSV files.
- **Ease of Use**: Allows quick setup and execution of performance tests via simple command-line parameters and environment variable configurations.

----
## Usage

### Prerequisites

- **Python Environment**: Ensure Python 3.6 or later is installed in your test environment.
- **RDMA Environment**: Test nodes must be configured with an RDMA network, including the necessary drivers and software, such as Mellanox OFED.
- **ibverbs Toolkit**: The `ibverbs` package, which includes `ibv_rc_pingpong`, must be installed on the test nodes.
- **SSH Access**: The machine running the test must be able to SSH into all test nodes without a password. You may need to configure SSH keys or use an SSH agent.
- **Environment Variables**: The following environment variables need to be set:
    - `PERF_TEST_USER`: Username for SSH login to remote nodes, default is `admin`.
    - `PERF_TEST_PASS`: Password for SSH login (optional, not needed if using SSH keys).

### Installation (No Installation Required)

This tool is a standalone Python script and does not require installation. Simply download the `fullmesh_rping.py` file to start using it.

### Running Tests

- **Prepare an IP Address List File**:
  Create a text file, e.g., `ips_list.txt`, with one IP address per line.
```bash
    192.168.1.10
    192.168.1.11
    192.168.1.12
```

- **Run the Script**:

```bash
python3 fullmesh_rping.py --ip_file_path ips_list.txt
```

#### Specify Testing Mode:

Use the `--mode` parameter to specify the testing mode. The default is `half_full` mode.

- Half-Duplex Mode (`half_full`): Performs one-way bandwidth tests between node pairs.
- Full Interconnect Mode (`full_mesh`): Tests bidirectional bandwidth between all nodes.
```bash
python perftest.py --mode full_mesh --ip_file_path ips_list.txt
```

----

## Performance

**128 Hosts / 8 NICs - half_full Mode**
    Time Taken: 3:58
**128 Hosts / 8 NICs - full_mesh Mode**
    Time Taken: 9:12

----

## Details
### Workflow

- **Configuration Loading**: The `PerfTestConfig` class loads configurations including test mode, IP address list, SSH username, and password. The IP list can be read from a specified file.
- **Directory Creation**: Upon initialization, `PerfTestRunner` creates a results directory named `perftest_result_YYYY-MM-DD_HH-MM-SS` to store test results and logs.
- **Remote Directory Preparation**: Creates or clears the `./rping` directory on all test nodes for storing remote test logs.
- **Test Pair Generation**: Generates IP address pairs based on the selected test mode (`half_full` or `full_mesh`).
- **Batch Processing**: The `batch_generator` function groups test pairs into batches to optimize resource usage and minimize contention.
- **Concurrent Execution**: Uses `ThreadPoolExecutor` for parallel execution of `ibv_rc_pingpong` commands on client and server nodes.
- **Port Allocation**: Manages ports using a shared pool (`port_pool`) and a lock (`port_lock`) to avoid conflicts. Ports range from 35000 to 45000.
- **Command Execution**: The `_execute_test` function runs `ibv_rc_pingpong` over SSH, with the server using `-s` mode and the client connecting via `-c <server_ip>`.
- **Network Interfaces**: The test cycles through predefined network interfaces (`mlx5_0`, `mlx5_1`, etc.).
- **Result Collection**: The `collect_results` function uses `scp` to download logs from all test nodes to the local results directory.
- **Log Parsing & Analysis**: The `process_log_files` function extracts bandwidth data from logs.
- **Result Storage**: Successful tests are saved in `rdma_analysis_passed.csv`, while failed tests go to `rdma_analysis_failed.csv`.
- **Process Cleanup**: The `cleanup` function kills residual `ibv_rc_pingpong` processes before and after testing.
- **Logging**: Uses `logging` module with multiprocessing queues for structured logging.

### Configuration
- **Test Mode (`--mode`)**: Specifies either `half_full` or `full_mesh`.
- **IP Address List (`--ip_file_path`)**: Specifies the IP list file path. If unavailable, a default range is used.
- **SSH Username (`PERF_TEST_USER`)**: Configured via environment variable.
- **SSH Password (`PERF_TEST_PASS`)**: Optional if using SSH keys.
- **Network Interfaces (`network_interfaces`)**: Defined in `PerfTestConfig`, defaulting to (`mlx5_0`, `mlx5_1`, etc.).
- **Port Range (`min_port`, `max_port`)**: Defaults to 35000–45000, adjustable based on environment.

### Port Management
To prevent concurrent `ibv_rc_pingpong` tests from using the same port, the script manages allocations using a shared port pool and lock mechanism.

