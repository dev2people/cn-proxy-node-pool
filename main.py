#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
China Proxy Node Pool Checker
1. Filters China (CN) proxy nodes from input data directory (input-raw-data/).
2. Tests connectivity:
   - TCP Handshake check (fast fail if port unreachable)
   - HTTP connectivity to 4 captive portal / connectivity test endpoints:
     * Xiaomi: http://connect.rom.miui.com/generate_204
     * Huawei: http://connectivitycheck.platform.hicloud.com/generate_204
     * Microsoft: http://www.msftconnecttest.com/connecttest.txt
     * Apple: http://captive.apple.com/hotspot-detect.html
3. Exports valid nodes as URLs into filter-data/allnode-url.txt (one per line).
4. Supports configurable concurrency (default 16) and timeout (default 5s) via CLI or environment variables.
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

import aiohttp
from aiohttp_socks import ProxyConnector

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ProxyChecker")

# Target connectivity endpoints
TARGETS = [
    {
        "name": "xiaomi",
        "url": "http://connect.rom.miui.com/generate_204",
        "check": lambda status, text: status in (200, 204),
        "desc": "小米 (Xiaomi generate_204)",
    },
    {
        "name": "huawei",
        "url": "http://connectivitycheck.platform.hicloud.com/generate_204",
        "check": lambda status, text: status in (200, 204),
        "desc": "华为 (Huawei generate_204)",
    },
    {
        "name": "microsoft",
        "url": "http://www.msftconnecttest.com/connecttest.txt",
        "check": lambda status, text: status == 200 and "Microsoft Connect Test" in text,
        "desc": "微软 (Windows connecttest.txt)",
    },
    {
        "name": "apple",
        "url": "http://captive.apple.com/hotspot-detect.html",
        "check": lambda status, text: status == 200 and "Success" in text,
        "desc": "苹果 (Apple hotspot-detect.html)",
    },
]


class ProxyCandidate:
    def __init__(self, scheme: str, ip: str, port: int, original_info: Optional[Dict[str, Any]] = None):
        self.scheme = scheme.lower()
        self.ip = ip.strip()
        self.port = int(port)
        self.original_info = original_info or {}

    @property
    def url(self) -> str:
        return f"{self.scheme}://{self.ip}:{self.port}"

    def __hash__(self):
        return hash(self.url)

    def __eq__(self, other):
        if not isinstance(other, ProxyCandidate):
            return False
        return self.url == other.url

    def __repr__(self):
        return f"ProxyCandidate({self.url})"


def normalize_schemes(protocol_str: str) -> List[str]:
    """
    Normalizes protocol string to standard proxy URL scheme(s).
    e.g.:
      'Http' -> ['http']
      'https' -> ['http']  # standard HTTP CONNECT proxy
      'socks5' -> ['socks5']
      'Socks4' -> ['socks4']
      'Http, Socks4' -> ['http', 'socks4']
    """
    schemes = []
    parts = re.split(r"[,/|;]+", protocol_str.strip())
    for part in parts:
        p = part.strip().lower()
        if not p:
            continue
        if "socks5" in p:
            schemes.append("socks5")
        elif "socks4" in p:
            schemes.append("socks4")
        elif "http" in p:
            schemes.append("http")
    return list(dict.fromkeys(schemes)) if schemes else ["http"]


def is_china_country(country: Optional[str]) -> bool:
    """Checks if the country indicator corresponds to China."""
    if not country:
        return False
    c = str(country).strip().upper()
    return c in ("CN", "CHINA", "CHN", "中国") or "CHINA" in c or "中国" in c


def load_candidates_from_dir(input_dir: str) -> List[ProxyCandidate]:
    """
    Scans the input directory for raw data files (.json, .txt)
    and extracts China (CN) proxy candidates.
    """
    candidates: Set[ProxyCandidate] = set()

    if not os.path.exists(input_dir):
        logger.warning(f"输入目录不存在: {input_dir}")
        return []

    for root, _, files in os.walk(input_dir):
        for file in sorted(files):
            file_path = os.path.join(root, file)
            logger.info(f"正在读取文件: {file_path}")

            if file.endswith(".json"):
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        data = json.load(f)

                    items = []
                    if isinstance(data, dict):
                        if "data" in data and isinstance(data["data"], list):
                            items = data["data"]
                        elif "proxies" in data and isinstance(data["proxies"], list):
                            items = data["proxies"]
                        else:
                            items = [v for v in data.values() if isinstance(v, dict)]
                    elif isinstance(data, list):
                        items = data

                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        country = item.get("country") or item.get("country_code") or item.get("location")
                        if not is_china_country(country):
                            continue

                        ip = item.get("ip") or item.get("host")
                        port = item.get("port")
                        protocol = item.get("protocol") or item.get("type") or "http"

                        if ip and port:
                            try:
                                port_int = int(port)
                                schemes = normalize_schemes(str(protocol))
                                for s in schemes:
                                    candidates.add(ProxyCandidate(s, str(ip), port_int, item))
                            except ValueError:
                                pass
                except Exception as e:
                    logger.error(f"解析 JSON 文件出错 {file_path}: {e}")

            elif file.endswith(".txt"):
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        for line in f:
                            line = line.strip()
                            if not line or line.startswith("#"):
                                continue

                            # Case 1: Full URL e.g. http://1.2.3.4:8080 or socks5://...
                            if "://" in line:
                                parsed = urlparse(line)
                                scheme = parsed.scheme.lower()
                                ip = parsed.hostname
                                port = parsed.port
                                if ip and port:
                                    # If file contains country or is china-only
                                    schemes = normalize_schemes(scheme)
                                    for s in schemes:
                                        candidates.add(ProxyCandidate(s, ip, port))
                            else:
                                # Case 2: ip:port or ip:port:protocol or ip:port:country
                                parts = line.split(":")
                                if len(parts) >= 2:
                                    ip = parts[0].strip()
                                    try:
                                        port = int(parts[1].strip())
                                        scheme = "http"
                                        country_flag = True
                                        if len(parts) >= 3:
                                            # Check if parts[2] is protocol or country
                                            p2 = parts[2].strip()
                                            if is_china_country(p2):
                                                country_flag = True
                                            elif any(proto in p2.lower() for proto in ("http", "socks")):
                                                scheme = p2
                                                if len(parts) >= 4 and not is_china_country(parts[3]):
                                                    country_flag = False
                                            elif len(parts) == 3 and not is_china_country(p2):
                                                country_flag = False

                                        if country_flag:
                                            schemes = normalize_schemes(scheme)
                                            for s in schemes:
                                                candidates.add(ProxyCandidate(s, ip, port))
                                    except ValueError:
                                        pass
                except Exception as e:
                    logger.error(f"解析 TXT 文件出错 {file_path}: {e}")

    logger.info(f"过滤出中国 (CN) 待测候选节点数量: {len(candidates)}")
    return sorted(list(candidates), key=lambda c: (c.ip, c.port, c.scheme))


async def tcp_handshake(ip: str, port: int, timeout: float) -> bool:
    """
    Performs TCP connect handshake test.
    Fast fail if the remote port is closed, filtered, or unreachable.
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port),
            timeout=timeout,
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


async def test_single_target(
    session: aiohttp.ClientSession,
    target: Dict[str, Any],
) -> Tuple[str, bool, Optional[int], Optional[float], Optional[str]]:
    """
    Tests proxy against a single connectivity check target.
    Returns: (target_name, is_success, status_code, latency_ms, error_msg)
    """
    name = target["name"]
    url = target["url"]
    checker = target["check"]

    start = time.monotonic()
    try:
        async with session.get(url, allow_redirects=False) as resp:
            text = ""
            # Only read body if checker inspects text (Microsoft and Apple)
            if name in ("microsoft", "apple"):
                text = await resp.text()

            latency_ms = round((time.monotonic() - start) * 1000, 1)
            ok = checker(resp.status, text)
            return (name, ok, resp.status, latency_ms, None)
    except asyncio.TimeoutError:
        return (name, False, None, None, "Timeout")
    except Exception as e:
        return (name, False, None, None, type(e).__name__)


async def check_node(
    candidate: ProxyCandidate,
    semaphore: asyncio.Semaphore,
    timeout: float,
    policy: str,
    progress_counter: Dict[str, int],
    total_count: int,
) -> Optional[Dict[str, Any]]:
    """
    Runs handshake check followed by 4 target HTTP connectivity tests.
    """
    async with semaphore:
        proxy_url = candidate.url

        # Step 1: TCP Handshake detection
        hs_start = time.monotonic()
        handshake_ok = await tcp_handshake(candidate.ip, candidate.port, timeout=timeout)
        hs_time_ms = round((time.monotonic() - hs_start) * 1000, 1)

        progress_counter["tested"] += 1
        idx = progress_counter["tested"]

        if not handshake_ok:
            progress_counter["handshake_failed"] += 1
            if idx % 20 == 0 or idx == total_count:
                logger.info(
                    f"进度: [{idx}/{total_count}] | 存活: {progress_counter['alive']} | "
                    f"握手失败: {progress_counter['handshake_failed']}"
                )
            return None

        progress_counter["handshake_passed"] += 1

        # Step 2: HTTP Connectivity Tests
        connector = None
        try:
            connector = ProxyConnector.from_url(proxy_url, ssl=False)
        except Exception as e:
            logger.debug(f"创建代理连接器失败 {proxy_url}: {e}")
            return None

        client_timeout = aiohttp.ClientTimeout(total=timeout, connect=timeout)
        target_results: Dict[str, Any] = {}

        try:
            async with aiohttp.ClientSession(connector=connector, timeout=client_timeout) as session:
                tasks = [test_single_target(session, target) for target in TARGETS]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for res in results:
                    if isinstance(res, tuple):
                        t_name, ok, status, lat, err = res
                        target_results[t_name] = {
                            "success": ok,
                            "status": status,
                            "latency_ms": lat,
                            "error": err,
                        }
                    else:
                        logger.debug(f"Target test exception for {proxy_url}: {res}")
        except Exception as e:
            logger.debug(f"ClientSession error for {proxy_url}: {e}")
            return None

        passed_targets = [k for k, v in target_results.items() if v.get("success")]
        passed_count = len(passed_targets)

        # Evaluate success condition
        if policy == "all":
            is_success = (passed_count == len(TARGETS))
        else:  # default 'any'
            is_success = (passed_count > 0)

        if is_success:
            progress_counter["alive"] += 1
            logger.info(
                f"✅ [成功] {proxy_url:<30} 握手: {hs_time_ms}ms | 通过目标: {passed_targets}"
            )
            return {
                "url": proxy_url,
                "ip": candidate.ip,
                "port": candidate.port,
                "scheme": candidate.scheme,
                "handshake_ms": hs_time_ms,
                "passed_count": passed_count,
                "passed_targets": passed_targets,
                "targets": target_results,
            }
        else:
            logger.debug(f"❌ [未通过] {proxy_url} 握手成功但所有HTTP测试均未通过")
            return None


async def run_checker(
    input_dir: str,
    output_file: str,
    concurrency: int,
    timeout: float,
    policy: str,
    details_file: Optional[str] = None,
):
    start_time = time.time()
    logger.info("=" * 60)
    logger.info("开始执行中国代理节点连通性检测任务")
    logger.info(f"输入目录: {input_dir}")
    logger.info(f"输出文件: {output_file}")
    logger.info(f"并发数:   {concurrency}")
    logger.info(f"超时时间: {timeout} 秒")
    logger.info(f"判定策略: {policy} ('any'=通过任一目标, 'all'=通过全部4个目标)")
    logger.info("=" * 60)

    # 1. 过滤中国节点
    candidates = load_candidates_from_dir(input_dir)
    total_candidates = len(candidates)
    if total_candidates == 0:
        logger.warning("未找到任何中国待测节点，程序结束。")
        # Ensure output directory exists and file is created
        os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            f.write("")
        return

    # 2. 批量测试
    semaphore = asyncio.Semaphore(concurrency)
    progress_counter = {
        "tested": 0,
        "handshake_passed": 0,
        "handshake_failed": 0,
        "alive": 0,
    }

    logger.info(f"启动异步检测，并发限制: {concurrency}...")
    tasks = [
        check_node(c, semaphore, timeout, policy, progress_counter, total_candidates)
        for c in candidates
    ]
    results = await asyncio.gather(*tasks)

    valid_nodes = [r for r in results if r is not None]

    # Sort valid nodes by passed_count descending, then by ip
    valid_nodes.sort(key=lambda x: (-x["passed_count"], x["ip"], x["port"]))

    # 3. 写入输出文件
    output_dir = os.path.dirname(os.path.abspath(output_file))
    os.makedirs(output_dir, exist_ok=True)

    with open(output_file, "w", encoding="utf-8") as f:
        for node in valid_nodes:
            f.write(f"{node['url']}\n")

    logger.info(f"检测完成！已将 {len(valid_nodes)} 个成功节点写入: {output_file}")

    # Optional: Write detailed JSON file
    if details_file:
        os.makedirs(os.path.dirname(os.path.abspath(details_file)), exist_ok=True)
        with open(details_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "generated_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                    "total_candidates": total_candidates,
                    "handshake_passed": progress_counter["handshake_passed"],
                    "alive_count": len(valid_nodes),
                    "policy": policy,
                    "timeout": timeout,
                    "nodes": valid_nodes,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
        logger.info(f"详细检测数据已写入: {details_file}")

    elapsed = round(time.time() - start_time, 2)
    logger.info("=" * 60)
    logger.info("任务执行总结:")
    logger.info(f"总耗时:            {elapsed} 秒")
    logger.info(f"中国待测候选总数:  {total_candidates}")
    logger.info(f"TCP 握手成功:      {progress_counter['handshake_passed']}")
    logger.info(f"TCP 握手失败:      {progress_counter['handshake_failed']}")
    logger.info(f"验证有效节点数:    {len(valid_nodes)}")
    logger.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Batch detection of Chinese proxy nodes availability."
    )
    parser.add_argument(
        "--input-dir",
        "-i",
        default=os.getenv("INPUT_DIR", "input-raw-data"),
        help="Input raw data directory (default: input-raw-data or env INPUT_DIR)",
    )
    parser.add_argument(
        "--output-file",
        "-o",
        default=os.getenv("OUTPUT_FILE", "filter-data/allnode-url.txt"),
        help="Output file path (default: filter-data/allnode-url.txt or env OUTPUT_FILE)",
    )
    parser.add_argument(
        "--concurrency",
        "-c",
        type=int,
        default=int(os.getenv("CONCURRENCY", "16")),
        help="Concurrency worker count (default: 16 or env CONCURRENCY)",
    )
    parser.add_argument(
        "--timeout",
        "-t",
        type=float,
        default=float(os.getenv("TIMEOUT", "5")),
        help="Timeout in seconds for connection and requests (default: 5 or env TIMEOUT)",
    )
    parser.add_argument(
        "--policy",
        "-p",
        choices=["any", "all"],
        default=os.getenv("SUCCESS_POLICY", "any"),
        help="Success policy: 'any' (pass at least 1 target) or 'all' (pass all 4 targets) (default: any or env SUCCESS_POLICY)",
    )
    parser.add_argument(
        "--details-file",
        default=os.getenv("DETAILS_FILE", "filter-data/allnode-details.json"),
        help="Output details json file path (default: filter-data/allnode-details.json or env DETAILS_FILE)",
    )

    args = parser.parse_args()

    try:
        asyncio.run(
            run_checker(
                input_dir=args.input_dir,
                output_file=args.output_file,
                concurrency=args.concurrency,
                timeout=args.timeout,
                policy=args.policy,
                details_file=args.details_file,
            )
        )
    except KeyboardInterrupt:
        logger.info("检测已被用户手动终止")
        sys.exit(130)


if __name__ == "__main__":
    main()
