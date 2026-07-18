"""TCP reachability helpers for Layer-3 processor discovery.

Pure helpers: no DB, no FastAPI imports. Used by the processor_discovery
route + CRUD layer to check whether a static IP exposes the Lutron LEAP
(8081) and LAP (8083) sockets before we insert a row in the `processor`
table.
"""

from __future__ import annotations

import ipaddress
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable, List, Sequence, Tuple


LEAP_PORT = 8081
LAP_PORT = 8083
DEFAULT_PORTS: Tuple[int, int] = (LEAP_PORT, LAP_PORT)
DEFAULT_TIMEOUT = 2.0
DEFAULT_MAX_WORKERS = 32


def is_valid_ipv4(value: str) -> bool:
    """Return True if `value` is a syntactically valid IPv4 address."""
    if not value or not isinstance(value, str):
        return False
    try:
        ip = ipaddress.ip_address(value.strip())
    except ValueError:
        return False
    return isinstance(ip, ipaddress.IPv4Address)


def parse_ip_list(raw: str) -> List[str]:
    """Split a free-form string (commas / newlines / whitespace) into a
    de-duplicated list of valid IPv4 strings, preserving original order.
    Invalid tokens are silently dropped — the route layer reports them
    via the per-IP result instead of raising.
    """
    if not raw:
        return []
    tokens: List[str] = []
    for chunk in raw.replace(",", "\n").splitlines():
        token = chunk.strip()
        if token:
            tokens.append(token)

    seen: set[str] = set()
    out: List[str] = []
    for tok in tokens:
        if is_valid_ipv4(tok) and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def check_tcp_port(ip: str, port: int, timeout: float = DEFAULT_TIMEOUT) -> bool:
    """Open a single TCP socket; return True if the 3-way handshake succeeds."""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except (socket.timeout, OSError):
        return False


def scan_ip(
    ip: str,
    ports: Sequence[int] = DEFAULT_PORTS,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """Probe one IP against a tuple of ports. Returns a dict with per-port
    booleans, an overall `reachable` flag (True if ANY listed port answered),
    and a measured `latency_ms` for the first successful port (or None).
    """
    result: dict = {
        "ip": ip,
        "reachable": False,
        "latency_ms": None,
        "ports": {},
    }

    if not is_valid_ipv4(ip):
        result["error"] = "invalid_ipv4"
        return result

    for port in ports:
        started = time.perf_counter()
        ok = check_tcp_port(ip, port, timeout=timeout)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        result["ports"][port] = ok
        if ok:
            result["reachable"] = True
            if result["latency_ms"] is None:
                result["latency_ms"] = round(elapsed_ms, 2)

    return result


def scan_ips_parallel(
    ips: Iterable[str],
    ports: Sequence[int] = DEFAULT_PORTS,
    timeout: float = DEFAULT_TIMEOUT,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> List[dict]:
    """Run `scan_ip` over many IPs concurrently. Order of the returned
    list mirrors the input iterable.
    """
    ip_list = list(ips)
    if not ip_list:
        return []

    results_by_ip: dict[str, dict] = {}
    worker_count = max(1, min(max_workers, len(ip_list)))

    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = {
            pool.submit(scan_ip, ip, ports, timeout): ip for ip in ip_list
        }
        for fut in as_completed(futures):
            ip = futures[fut]
            try:
                results_by_ip[ip] = fut.result()
            except Exception as e:  # defensive — never let one IP kill the batch
                results_by_ip[ip] = {
                    "ip": ip,
                    "reachable": False,
                    "latency_ms": None,
                    "ports": {p: False for p in ports},
                    "error": f"scan_error: {e}",
                }

    return [results_by_ip[ip] for ip in ip_list]
