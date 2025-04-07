import os
import re
import time
import logging
import shlex
import subprocess
from itertools import product, combinations
from typing import List, Tuple, Generator
import multiprocessing as mp
from multiprocessing import Manager
from logging.handlers import QueueHandler
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from tqdm import tqdm
import argparse


def setup_logging(queue):
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    queue_handler = QueueHandler(queue)
    logger.addHandler(queue_handler)


def log_listener_process(queue):
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler = logging.FileHandler('perftest.log')
    stream_handler = logging.StreamHandler()
    file_handler.setFormatter(formatter)
    stream_handler.setFormatter(formatter)
    while True:
        try:
            record = queue.get()
            if record is None:
                break
            file_handler.handle(record)
            stream_handler.handle(record)
        except Exception:
            import traceback
            traceback.print_exc()


class PerfTestConfig:
    def __init__(self, mode: str, ip_file_path: str):
        self.ssh_port = 22
        self.mode = mode
        self.username = os.getenv('PERF_TEST_USER', 'metaxadmin')
        self.password = os.getenv('PERF_TEST_PASS')
        self.node_list = []
        self.ip_file_path = ip_file_path
        try:
            with open(self.ip_file_path, 'r') as file:
                for line in file:
                    ip_address = line.strip()
                    if ip_address:
                        self.node_list.append(ip_address)
            logging.info(f"IP addresses loaded from '{self.ip_file_path}': {self.node_list}")
        except FileNotFoundError:
            print(f"Error: File '{self.ip_file_path}' not found. Using default node list.")
            self.node_list = [f"10.200.146.{idx}" for idx in range(14, 28)]
        self.network_interfaces = (
            'mlx5_0', 'mlx5_1', 'mlx5_4', 'mlx5_5',
            'mlx5_6', 'mlx5_7', 'mlx5_10', 'mlx5_11'
        )
        self.min_port = 35000
        self.max_port = 45000


class PerfTestRunner:
    def __init__(self, config: PerfTestConfig, manager: Manager):
        self.config = config
        self.timestamp = time.strftime('%Y-%m-%d_%H-%M-%S')
        self.result_dir = f"perftest_result_{self.timestamp}"
        self._create_directory()
        self.port_pool = manager.list(range(config.min_port, config.max_port + 1))
        self.port_lock = manager.Lock()

    def _create_directory(self):
        try:
            os.makedirs(self.result_dir, exist_ok=True)
            logging.info(f"Created result directory: {self.result_dir}")
        except OSError as e:
            logging.error(f"Directory creation failed: {e}")
            raise

    def create_remote_directories(self):
        cmd = "mkdir -p ./rping; rm -f -R ./rping/*"
        logging.info("Creating rping directories on remote nodes")
        def create_host(host):
            self._ssh_execute(host, cmd)
        with ThreadPoolExecutor(max_workers=20) as executor:
            executor.map(create_host, self.config.node_list)
        time.sleep(5)

    def collect_results(self):
        local_dir = os.path.join(self.result_dir, 'rping_results')
        os.makedirs(local_dir, exist_ok=True)
        logging.info("Starting log file collection")
        def transfer_host(host):
            remote_path = f"{self.config.username}@{host}:./rping/*"
            local_path = os.path.join(local_dir, host)
            os.makedirs(local_path, exist_ok=True)
            scp_cmd = (
                f"scp -P {self.config.ssh_port} "
                f"-o StrictHostKeyChecking=no -r {remote_path} {local_path}/"
            )
            try:
                subprocess.run(
                    scp_cmd,
                    shell=True,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=30
                )
            except subprocess.CalledProcessError as e:
                logging.error(f"Failed to collect files from node {host}: {e.stderr}")
            except subprocess.TimeoutExpired:
                logging.error(f"File collection timeout from node {host}")
        with ThreadPoolExecutor(max_workers=10) as executor:
            executor.map(transfer_host, self.config.node_list)
        time.sleep(5)
        logging.info("Log file collection completed")
        self.process_log_files(local_dir)

    def process_log_files(self, log_dir):
        """Process all log files and generate analysis results"""
        results = []
        results_failed = []
        ip_pattern = re.compile(r'^(\d+\.\d+\.\d+\.\d+)_(.+)$')

        for root, _, files in os.walk(log_dir):
            for filename in files:
                if not filename.endswith('.txt'):
                    continue
                try:
                    base_name = os.path.splitext(filename)[0]
                    part1, part2 = base_name.split('__')
                    ip1_match = ip_pattern.match(part1)
                    ip2_match = ip_pattern.match(part2)
                    if not ip1_match or not ip2_match:
                        logging.warning(f"Invalid filename format: {filename}")
                        continue
                    src_ip, src_interface = ip1_match.groups()
                    dst_ip, dst_interface = ip2_match.groups()
                except ValueError:
                    logging.warning(f"Invalid filename format: {filename}")
                    continue

                file_path = os.path.join(root, filename)
                try:
                    with open(file_path, 'r') as f:
                        content = f.read()
                        bandwidth_match = re.search(r'=\s*([\d.]+)\s+Mbit/sec', content)
                        if not bandwidth_match:
                            results_failed.append({
                                'source_ip': src_ip,
                                'source_interface': src_interface,
                                'destination_ip': dst_ip,
                                'destination_interface': dst_interface,
                            })
                            continue
                        mbps = bandwidth_match.group(1)
                except Exception as e:
                    logging.error(f"Failed to read file {filename}: {str(e)}")
                    continue

                results.append({
                    'source_ip': src_ip,
                    'source_interface': src_interface,
                    'destination_ip': dst_ip,
                    'destination_interface': dst_interface,
                    'bandwidth_mbps': mbps
                })

        if results:
            csv_path = os.path.join(self.result_dir, 'rdma_analysis_passed.csv')
            with open(csv_path, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=[
                    'source_ip',
                    'source_interface',
                    'destination_ip',
                    'destination_interface',
                    'bandwidth_mbps'
                ])
                writer.writeheader()
                writer.writerows(results)
            logging.info(f"Analysis results saved to {csv_path}")
        else:
            logging.warning("No valid data found")
        
        if results_failed:
            csv_path = os.path.join(self.result_dir, 'rdma_analysis_failed.csv')
            with open(csv_path, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=[
                    'source_ip',
                    'source_interface',
                    'destination_ip',
                    'destination_interface'
                ])
                writer.writeheader()
                writer.writerows(results_failed)
            logging.info(f"Failed results saved to {csv_path}")

    def generate_ip_combinations(self) -> Generator[Tuple[str, str], None, None]:
        if self.config.mode == "full_mesh":
            return (pair for pair in product(self.config.node_list, self.config.node_list)
                    if pair[0] != pair[1])
        else:
            return [tuple(sorted(pair)) for pair in combinations(self.config.node_list, 2)]

    def batch_generator(self, data: List[Tuple[str, str]]) -> Generator[List[Tuple[str, str]], None, None]:
        while data:
            used_ips = set()
            batch = []
            remaining = []
            for pair in data:
                ip1, ip2 = pair
                if ip1 not in used_ips and ip2 not in used_ips:
                    batch.append(pair)
                    used_ips.update({ip1, ip2})
                else:
                    remaining.append(pair)
            data = remaining
            yield batch

    def _ssh_execute(self, host: str, command: str) -> str:
        sanitized_cmd = shlex.quote(command)
        ssh_cmd = f"ssh -o StrictHostKeyChecking=no {host} {sanitized_cmd}"
        try:
            result = subprocess.run(
                ssh_cmd,
                shell=True,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=3
            )
            return result.stdout
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return ""

    def _execute_test(self, client_ip: str, server_ip: str, intf: str) -> dict:
        port = None
        try:
            with self.port_lock:
                if not self.port_pool:
                    raise ValueError("No available ports")
                port = self.port_pool.pop()
            result_file = f"{self.result_dir}/{client_ip}_{intf}__{server_ip}_{intf}.txt"
            client_result_file = f"./rping/{client_ip}_{intf}__{server_ip}_{intf}.txt"
            server_cmd = f"nohup ibv_rc_pingpong -d {intf} -p {port} -g 3 > /dev/null 2>&1 &"
            self._ssh_execute(server_ip, server_cmd)
            client_cmd = f"nohup ibv_rc_pingpong -d {intf} -p {port} -g 3 {server_ip} > {client_result_file} 2>&1 &"
            self._ssh_execute(client_ip, client_cmd)
        except Exception as e:
            logging.error(f"Test failed {client_ip}->{server_ip}: {str(e)}")
            return {}
        finally:
            if port is not None:
                with self.port_lock:
                    if port not in self.port_pool:
                        self.port_pool.append(port)

    def _parse_output(self, output: str) -> float:
        match = re.search(r"(\d+)\s+bytes in\s+(\d+\.\d+)\s+seconds =\s+([0-9.]+)\s+Mbit/sec", output)
        return float(match.group(3)) if match else 0.0

    def parallel_execute(self, batch: List[Tuple[str, str]]):
        max_workers = min(os.cpu_count() * 2, 128)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for client_ip, server_ip in batch:
                for intf in self.config.network_interfaces:
                    future = executor.submit(self._execute_test, client_ip, server_ip, intf)
                    futures[future] = time.time()
            for future in as_completed(futures):
                submit_time = futures[future]
                timeout_remaining = 120 - (time.time() - submit_time)
                if timeout_remaining <= 0:
                    logging.warning("Task timeout")
                    continue
                try:
                    future.result(timeout=timeout_remaining)
                except TimeoutError:
                    logging.warning("Task did not complete in allowed time")
                except Exception as e:
                    logging.error(f"Task execution failed: {e}")

    def cleanup(self):
        logging.info("Cleaning up test processes...")
        kill_cmd = "nohup pkill -f ibv_rc_pingpong > /dev/null 2>&1 &"
        def kill_host(host):
            self._ssh_execute(host, kill_cmd)
        with ThreadPoolExecutor(max_workers=20) as executor:
            executor.map(kill_host, self.config.node_list)
        time.sleep(5)


class FailFileParser:
    def __init__(self, filename):
        self.filename = filename
        self.links = []
        self._parse_file()

    def _parse_file(self):
        seen = set()
        with open(self.filename, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                source_ip = row['source_ip']
                destination_ip = row['destination_ip']
                pair = (source_ip, destination_ip)
                if pair not in seen:
                    seen.add(pair)
                    self.links.append(pair)

    def get_links(self):
        return self.links


def retry_failed_tests(runner: PerfTestRunner, description: str):
    failed_csv_path = os.path.join(runner.result_dir, 'rdma_analysis_failed.csv')
    if os.path.exists(failed_csv_path) and os.path.getsize(failed_csv_path) > 0:
        parser = FailFileParser(failed_csv_path)
        failed_pairs = parser.get_links()
        if failed_pairs:
            logging.info(f"Found {len(failed_pairs)} failed test pairs, starting {description} retry...")
            runner.cleanup()
            with tqdm(
                total=len(failed_pairs),
                desc=f"{description} retry progress",
                unit="pair",
                dynamic_ncols=True,
                mininterval=0.3,
                maxinterval=1.0,
                bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]"
            ) as pbar_retry:
                for batch_num, batch in enumerate(runner.batch_generator(failed_pairs), 1):
                    start_time = time.time()
                    runner.parallel_execute(batch)
                    elapsed_time = time.time() - start_time
                    pbar_retry.update(len(batch))
                    pbar_retry.set_postfix_str(f"Batch #{batch_num} took {elapsed_time:.1f}s")
            runner.collect_results()


def main():
    parser = argparse.ArgumentParser(description='RDMA performance testing tool')
    parser.add_argument('--mode', type=str, default='half_full',
                        choices=['half_full', 'full_mesh'],
                        help='Test mode: half_full (default) or full_mesh')
    parser.add_argument('--ip_file_path', type=str, default='ips_list.txt',
                        help='Path to file containing IP list (default: ips_list.txt)')
    args = parser.parse_args()

    log_queue = mp.Queue()
    listener = mp.Process(target=log_listener_process, args=(log_queue,))
    listener.start()
    setup_logging(log_queue)
    try:
        with Manager() as manager:
            config = PerfTestConfig(mode=args.mode, ip_file_path=args.ip_file_path)
            runner = PerfTestRunner(config, manager)
            runner.cleanup()
            runner.create_remote_directories()
            test_pairs = list(runner.generate_ip_combinations())
            total_pairs = len(test_pairs)
            logging.info(f"Total test pairs: {total_pairs}")

            with tqdm(
                total=total_pairs,
                desc="Testing progress",
                unit="pair",
                dynamic_ncols=True,
                mininterval=0.3,
                maxinterval=1.0,
                bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]"
            ) as pbar:
                for batch_num, batch in enumerate(runner.batch_generator(test_pairs), 1):
                    start_time = time.time()
                    runner.parallel_execute(batch)
                    elapsed_time = time.time() - start_time
                    pbar.update(len(batch))
                    pbar.set_postfix_str(f"Batch #{batch_num} took {elapsed_time:.1f}s")
                logging.info("All tests completed")

            runner.collect_results()
            # Retry failed tests twice
            retry_failed_tests(runner, "First")
            retry_failed_tests(runner, "Second")

    except Exception as e:
        logging.critical(f"Critical error: {str(e)}")
    finally:
        if 'runner' in locals():
            time.sleep(5)
            runner.cleanup()
        log_queue.put(None)
        listener.join()


if __name__ == "__main__":
    main()
