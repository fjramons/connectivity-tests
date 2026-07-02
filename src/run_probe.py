#!/usr/bin/env python3
"""Connectivity probe engine for Local cloud domain -> Remote cloud domain.

Only uses the Python standard library (json, socket, subprocess, argparse)
so it can run inside nicolaka/netshoot (or any VM/pod with
python3) without needing `pip install` inside Local cloud domain.

Reads a spec file (connectivity-test-spec.json), and for each case with
"automatable": true attempts the TCP/UDP connection; if it fails (or always,
for UDP, which doesn't confirm delivery), runs complementary diagnostics
(ping, tcptraceroute/traceroute) to distinguish "host unreachable" from
"port/service down but host alive". remote_cloud_domain_to_local_cloud_domain cases
(where Local cloud domain acts as server) are logged as pending
manual testing from Remote cloud domain -- see README.md.
"""
from __future__ import annotations

import argparse
import errno
import json
import re
import shutil
import socket
import subprocess
import sys
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

TCP_TIMEOUT = 3
UDP_TIMEOUT = 3
PING_COUNT = 4
PING_TIMEOUT = 2
TRACEROUTE_TIMEOUT = 20

# UDP ICMP margin configuration (see README, section 5, UDP note).
# Overridable via --config connectivity-tests.toml, [probe] table.
DEFAULT_PROBE_CONFIG = {
    "udp_icmp_wait_default_seconds": 0.5,
    "udp_icmp_wait_max_seconds": 2.0,
    "udp_icmp_wait_rtt_multiplier": 4,
}

_NO_ROUTE_ERRNOS = {errno.EHOSTUNREACH, errno.ENETUNREACH}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_probe_config(config_path: Path | None) -> dict:
    cfg = dict(DEFAULT_PROBE_CONFIG)
    if config_path and config_path.exists():
        with config_path.open("rb") as f:
            toml_data = tomllib.load(f)
        cfg.update(toml_data.get("probe", {}))
    return cfg


def tcp_check(ip: str, port: int) -> tuple[str, str]:
    """Returns (status, detail). status in {"connected","refused","timeout","no_route","error"}.

    The refused/timeout/no_route distinction is the key to the A/B/C diagnosis:
    an immediate refusal (RST) proves the packet reached the host and the
    firewall let it through (case A); a "no route to host"/"network
    unreachable" is an explicit ICMP from an intermediate router (different from a
    silent timeout, though for verdict purposes it's still case B/C);
    a plain timeout says nothing on its own (ping/traceroute is needed).
    """
    try:
        with socket.create_connection((ip, port), timeout=TCP_TIMEOUT):
            return "connected", "TCP connection established"
    except ConnectionRefusedError as e:
        return "refused", str(e)
    except (TimeoutError, socket.timeout) as e:
        return "timeout", str(e)
    except OSError as e:
        if e.errno in _NO_ROUTE_ERRNOS:
            return "no_route", str(e)
        return "error", str(e)


def udp_send(ip: str, port: int, icmp_wait: float) -> tuple[str, str]:
    """Returns (status, detail). status in {"sent","refused","send_failed"}.

    Uses a "connected" UDP socket + double send to try to capture an
    asynchronous ICMP port-unreachable (on Linux it's delivered on the next
    socket operation after the first send, not on the send itself). If
    received, it's the UDP equivalent of case A (refusal => network open,
    listener missing). If nothing is received, it is NOT conclusive: many
    corporate firewalls filter the return ICMP even though the UDP traffic
    does get through.

    "icmp_wait" is the margin between the two send()s, calibrated by the
    caller from the RTT measured with ping (see compute_icmp_wait).
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(UDP_TIMEOUT)
            s.connect((ip, port))
            payload = b"connectivity-test-probe\n"
            try:
                s.send(payload)
                time.sleep(icmp_wait)
                s.send(payload)
            except ConnectionRefusedError:
                return "refused", (
                    "ICMP port-unreachable received after sending: the host actively "
                    "refuses the UDP port"
                )
        return "sent", "UDP datagram(s) sent with no socket error (no ICMP port-unreachable observed)"
    except OSError as e:
        return "send_failed", f"could not send the UDP datagram: {e}"


_PING_RTT_RE = re.compile(r"rtt [\w/]+ = [\d.]+/([\d.]+)/[\d.]+/[\d.]+\s*ms")


def parse_ping_rtt(ping_output: str) -> float | None:
    """Extracts the mean RTT (ms) from the final line of `ping` (iputils format)."""
    m = _PING_RTT_RE.search(ping_output)
    return float(m.group(1)) if m else None


def compute_icmp_wait(rtt_ms: float | None, cfg: dict) -> float:
    default_wait = cfg["udp_icmp_wait_default_seconds"]
    if rtt_ms is None:
        return default_wait
    max_wait = cfg["udp_icmp_wait_max_seconds"]
    scaled = (rtt_ms / 1000) * cfg["udp_icmp_wait_rtt_multiplier"]
    return min(max_wait, max(default_wait, scaled))


def run_cmd(cmd: list[str], timeout: int) -> tuple[bool, str]:
    if shutil.which(cmd[0]) is None:
        return False, f"tool '{cmd[0]}' not available in this environment"
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode == 0, output.strip()
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout}s running: {' '.join(cmd)}"


def ping_check(ip: str) -> tuple[bool, str]:
    return run_cmd(["ping", "-c", str(PING_COUNT), "-W", str(PING_TIMEOUT), ip], timeout=PING_COUNT * PING_TIMEOUT + 5)


def traceroute_check(ip: str, port: int | None) -> tuple[bool, str]:
    if port and shutil.which("tcptraceroute"):
        ok, out = run_cmd(["tcptraceroute", "-m", "15", ip, str(port)], timeout=TRACEROUTE_TIMEOUT)
        return ok, f"tcptraceroute:\n{out}"
    ok, out = run_cmd(["traceroute", "-w", "2", "-m", "15", ip], timeout=TRACEROUTE_TIMEOUT)
    return ok, f"traceroute:\n{out}"


_HOP_LINE_RE = re.compile(r"^\s*(\d+)\s+(.*)$")
_HOP_IP_RE = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3})")


def summarize_traceroute(output: str, target_ip: str) -> str:
    """Sentence with the last hop that responded and whether it's the final destination or not.

    Best-effort: parses the usual traceroute/tcptraceroute format
    ("N  host (ip)  time..." or "N  * * *" for hops with no response). If it
    doesn't recognize any hop, it says so explicitly instead of failing.
    """
    last_hop: tuple[str, str] | None = None
    for line in output.splitlines():
        m = _HOP_LINE_RE.match(line)
        if not m:
            continue
        hop_num, rest = m.groups()
        if rest.strip().startswith("*"):
            continue  # hop with no response
        ip_m = _HOP_IP_RE.search(rest)
        if ip_m:
            last_hop = (hop_num, ip_m.group(1))
    if last_hop is None:
        return "no hop responded (all '* * *'); cannot tell where the path is cut off"
    hop_num, hop_ip = last_hop
    if hop_ip == target_ip:
        return f"traffic reaches the destination itself ({target_ip}) at hop {hop_num}"
    return (
        f"traffic reaches hop {hop_num} ({hop_ip}) and does not reach the "
        f"final destination ({target_ip}) -- that's where to look for the missing firewall rule/route"
    )


def format_endpoint(test: dict) -> str:
    src = test["source"]
    dst = test["destination"]
    return (
        f"{src.get('range') or src.get('description') or '?'} "
        f"({src.get('type') or 'unknown'}) -> "
        f"{dst['ip']}:{test['port']}/{test['protocol']} [{test['protocol_label']}]"
    )


def run_test(test: dict, log, cfg: dict) -> str:
    ip = test["destination"]["ip"]
    port = test["port"]
    header = f"[{now()}] [{test['id']}] {format_endpoint(test)}"
    lines = [header]

    if test["protocol"] == "tcp":
        status, detail = tcp_check(ip, port)
        lines.append(f"  RESULT: {status.upper()} - {detail}")
        if status == "connected":
            verdict = "PASS"
        elif status == "refused":
            # Immediate refusal (RST): the packet reached the host and the firewall
            # let it through. Strong signal (case A) -- ping isn't needed to decide,
            # but it's still logged as extra context.
            ping_ok, ping_out = ping_check(ip)
            lines.append(f"  COMPLEMENTARY ping: {'host responds' if ping_ok else 'no response'}\n    {ping_out}")
            verdict = "PORT_REFUSED_NETWORK_OPEN"
            lines.append(
                "  VERDICT: PORT_REFUSED_NETWORK_OPEN - the host actively refused the connection (RST). "
                "Proves the network/firewall lets traffic through to that port (case A); "
                "the service is missing, it's not a firewall problem."
            )
        else:  # timeout, no_route or other error: ambiguous, ping/traceroute needed
            if status == "no_route":
                lines.append(
                    "  NOTE: explicit routing failure (ICMP host/network unreachable), not a "
                    "silent timeout -- an intermediate router responded actively."
                )
            ping_ok, ping_out = ping_check(ip)
            lines.append(f"  COMPLEMENTARY ping: {'host responds' if ping_ok else 'no response'}\n    {ping_out}")
            trace_ok, trace_out = traceroute_check(ip, port)
            lines.append(f"  COMPLEMENTARY traceroute:\n    {trace_out}")
            lines.append(f"  COMPLEMENTARY traceroute summary: {summarize_traceroute(trace_out, ip)}")
            if ping_ok:
                verdict = "PORT_CLOSED_HOST_REACHABLE"
                lines.append(
                    "  VERDICT: PORT_CLOSED_HOST_REACHABLE - the host responds to ping but the port did not "
                    "respond with either RST or data (case B). Weaker signal than an explicit refusal: "
                    "consistent with the service not being deployed yet, but also with a firewall "
                    "that lets ICMP through while selectively blocking that TCP port."
                )
            else:
                verdict = "HOST_UNREACHABLE"
                lines.append(
                    "  VERDICT: HOST_UNREACHABLE - neither the port nor ping respond (case C). Inconclusive: "
                    "check the firewall rule/route to Remote cloud domain (or whether the host is powered off)."
                )
    else:  # udp: ping first (to calibrate the ICMP margin from the RTT), then the send
        ping_ok, ping_out = ping_check(ip)
        lines.append(f"  COMPLEMENTARY ping: {'host responds' if ping_ok else 'no response'}\n    {ping_out}")
        rtt_ms = parse_ping_rtt(ping_out) if ping_ok else None
        icmp_wait = compute_icmp_wait(rtt_ms, cfg)
        lines.append(
            f"  COMPLEMENTARY ICMP margin: {icmp_wait:.2f}s "
            f"(computed from ping mean RTT={rtt_ms}ms)" if rtt_ms is not None else
            f"  COMPLEMENTARY ICMP margin: {icmp_wait:.2f}s (default value, no RTT available)"
        )
        status, detail = udp_send(ip, port, icmp_wait=icmp_wait)
        lines.append(f"  RESULT: {status.upper()} - {detail}")
        if status == "refused":
            verdict = "UDP_REFUSED_NETWORK_OPEN"
            lines.append(
                "  VERDICT: UDP_REFUSED_NETWORK_OPEN - ICMP port-unreachable received (UDP equivalent of "
                "case A). Network/firewall let traffic through to that port; the listener is missing."
            )
        elif status == "sent":
            if ping_ok:
                verdict = "UDP_SENT_HOST_REACHABLE"
                lines.append(
                    "  VERDICT: UDP_SENT_HOST_REACHABLE - datagram sent with no error and host reachable via "
                    "ICMP, but no ICMP port-unreachable observed. NOT conclusive (unlike TCP): "
                    "many firewalls filter that return ICMP even though the UDP traffic does get through; "
                    "confirm with the receiving team in Remote cloud domain whether the packet arrived."
                )
            else:
                verdict = "UDP_SENT_HOST_UNREACHABLE"
                lines.append(
                    "  VERDICT: UDP_SENT_HOST_UNREACHABLE - the UDP send did not raise a socket error, but the host "
                    "does not respond to ping. Could be blocked by a firewall or the host could be down."
                )
        else:
            verdict = "UDP_SEND_FAILED"

    lines.append(f"  ---> FINAL VERDICT: {verdict}\n")
    text = "\n".join(lines)
    log.write(text + "\n")
    log.flush()
    return verdict


def run_skipped(test: dict, log) -> str:
    header = f"[{now()}] [{test['id']}] {format_endpoint(test)}"
    note = test.get("note", "Requires manual testing from Remote cloud domain.")
    text = f"{header}\n  ---> FINAL VERDICT: SKIPPED_MANUAL_TEST_REQUIRED ({note})\n"
    log.write(text + "\n")
    log.flush()
    return "SKIPPED_MANUAL_TEST_REQUIRED"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True, help="Path to connectivity-test-spec.json")
    parser.add_argument("--out", type=Path, required=True, help="Path to the output log file")
    parser.add_argument(
        "--filter-source-type",
        choices=["VM", "K8s Cluster"],
        default=None,
        help="Only runs tests whose source.type matches (useful for separating VM vs cluster).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=(
            "Path to connectivity-tests.toml ([probe] table) to calibrate the UDP ICMP margin. "
            "If omitted or the file doesn't exist, the embedded default values are used."
        ),
    )
    args = parser.parse_args()

    cfg = load_probe_config(args.config)

    with args.spec.open(encoding="utf-8") as f:
        spec = json.load(f)

    tests = spec.get("tests", [])
    if args.filter_source_type:
        tests = [t for t in tests if t["source"].get("type") == args.filter_source_type]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    verdicts: dict[str, int] = {}
    with args.out.open("w", encoding="utf-8") as log:
        log.write(f"# Local cloud domain -> Remote cloud domain connectivity test run\n# Start: {now()}\n\n")
        for test in tests:
            if test.get("automatable"):
                verdict = run_test(test, log, cfg)
            else:
                verdict = run_skipped(test, log)
            verdicts[verdict] = verdicts.get(verdict, 0) + 1

        log.write("# Summary\n")
        for verdict, count in sorted(verdicts.items()):
            log.write(f"#   {verdict}: {count}\n")
        log.write(f"# End: {now()}\n")

    print(f"Log written to {args.out}")
    for verdict, count in sorted(verdicts.items()):
        print(f"  {verdict}: {count}")


if __name__ == "__main__":
    sys.exit(main())
