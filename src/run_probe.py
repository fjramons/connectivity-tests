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

# UDP ICMP margin configuration (see README's "Step-by-step manual
# diagnosis" appendix, UDP note).
# Overridable via --config connectivity-tests.toml, [probe] table.
DEFAULT_PROBE_CONFIG = {
    "udp_icmp_wait_default_seconds": 0.5,
    "udp_icmp_wait_max_seconds": 2.0,
    "udp_icmp_wait_rtt_multiplier": 4,
}

# Visual verdict indicator, kept in sync with the "Verdict interpretation"
# table in README.md and .claude/skills/run-local-cloud-domain-connectivity-tests/SKILL.md.
VERDICT_ICON = {
    "PASS": "✅",
    "PORT_REFUSED_NETWORK_OPEN": "✅",
    "UDP_REFUSED_NETWORK_OPEN": "✅",
    "PORT_CLOSED_HOST_REACHABLE": "⚠️",
    "UDP_SENT_HOST_REACHABLE": "⚠️",
    "HOST_UNREACHABLE": "❌",
    "UDP_SENT_HOST_UNREACHABLE": "⚠️",
    "UDP_SEND_FAILED": "❌",
    "SKIPPED_MANUAL_TEST_REQUIRED": "⏭️",
}

# Short diagnostic-nuance phrase for the report's "comment" column, shown
# only for the weak/inconclusive verdicts (case B/C). Deliberately not the
# CSV business `comments`/`service` field from the spec -- this explains
# *why* the verdict is ambiguous, not what the test is for. Verdicts absent
# from this dict (PASS, *_REFUSED_NETWORK_OPEN, UDP_SEND_FAILED,
# SKIPPED_MANUAL_TEST_REQUIRED) get no comment.
VERDICT_COMMENT = {
    "PORT_CLOSED_HOST_REACHABLE": (
        "host answers ping but the port didn't respond -- could be the service "
        "not deployed yet, or a firewall selectively filtering just that port"
    ),
    "HOST_UNREACHABLE": (
        "ping also failed -- could be a firewall block or the host being down, "
        "cannot tell which from here"
    ),
    "UDP_SENT_HOST_REACHABLE": (
        "no ICMP port-unreachable observed and host answers ping -- UDP delivery "
        "not confirmed, check with the receiving team in Remote cloud domain"
    ),
    "UDP_SENT_HOST_UNREACHABLE": (
        "no ICMP observed and ping also failed -- still not conclusive for UDP, "
        "could be a firewall block or the host being down"
    ),
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


def run_cmd(cmd: list[str], timeout: int) -> tuple[bool, str, list[str]]:
    if shutil.which(cmd[0]) is None:
        return False, f"tool '{cmd[0]}' not available in this environment", cmd
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode == 0, output.strip(), cmd
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout}s running: {' '.join(cmd)}", cmd


def ping_check(ip: str) -> tuple[bool, str, list[str]]:
    return run_cmd(["ping", "-c", str(PING_COUNT), "-W", str(PING_TIMEOUT), ip], timeout=PING_COUNT * PING_TIMEOUT + 5)


def traceroute_check(ip: str, port: int | None) -> tuple[bool, str, list[str]]:
    if port and shutil.which("tcptraceroute"):
        ok, out, cmd = run_cmd(["tcptraceroute", "-m", "15", ip, str(port)], timeout=TRACEROUTE_TIMEOUT)
        return ok, f"tcptraceroute:\n{out}", cmd
    ok, out, cmd = run_cmd(["traceroute", "-w", "2", "-m", "15", ip], timeout=TRACEROUTE_TIMEOUT)
    return ok, f"traceroute:\n{out}", cmd


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


def format_tcp_command(ip: str, port: int) -> str:
    """Human-readable line documenting the exact parameters of the raw-socket
    TCP check (there's no real subprocess argv to show, since tcp_check()
    uses socket.create_connection() directly, not a CLI tool)."""
    return (
        f"TCP_CONNECT ip={ip} port={port} timeout={TCP_TIMEOUT}s "
        f"(python socket.create_connection; roughly equivalent to: nc -zv -w {TCP_TIMEOUT} {ip} {port})"
    )


def format_udp_command(ip: str, port: int, icmp_wait: float) -> str:
    """Same idea as format_tcp_command() for the raw-socket UDP send in
    udp_send() (connect() + double send(), no real subprocess argv)."""
    return (
        f"UDP_SEND ip={ip} port={port} timeout={UDP_TIMEOUT}s icmp_wait={icmp_wait:.2f}s "
        f"(python connect()+send() x2; roughly equivalent to: nc -u -zv -w {UDP_TIMEOUT} {ip} {port})"
    )


def format_cmd_line(argv: list[str]) -> str:
    return " ".join(argv)


def run_test(test: dict, log, cfg: dict) -> tuple[str, dict]:
    ip = test["destination"]["ip"]
    port = test["port"]
    started_at = now()
    header = f"[{started_at}] [{test['id']}] {format_endpoint(test)}"
    lines = [header]
    commands: list[dict] = []

    def log_command(tool: str, display: str, argv: list[str] | None = None) -> None:
        commands.append({"tool": tool, "argv": argv, "display": display})
        lines.append(f"  COMMAND: {display}")

    if test["protocol"] == "tcp":
        log_command("tcp_connect", format_tcp_command(ip, port))
        status, detail = tcp_check(ip, port)
        lines.append(f"  RESULT: {status.upper()} - {detail}")
        print(f"    RESULT: {status.upper()} - {detail}", flush=True)
        if status == "connected":
            verdict = "PASS"
        elif status == "refused":
            # Immediate refusal (RST): the packet reached the host and the firewall
            # let it through. Strong signal (case A) -- ping isn't needed to decide,
            # but it's still logged as extra context.
            print("    · ping...", flush=True)
            ping_ok, ping_out, ping_argv = ping_check(ip)
            log_command("ping", format_cmd_line(ping_argv), ping_argv)
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
            print("    · ping...", flush=True)
            ping_ok, ping_out, ping_argv = ping_check(ip)
            log_command("ping", format_cmd_line(ping_argv), ping_argv)
            lines.append(f"  COMPLEMENTARY ping: {'host responds' if ping_ok else 'no response'}\n    {ping_out}")
            print("    · traceroute (up to 20s)...", flush=True)
            trace_ok, trace_out, trace_argv = traceroute_check(ip, port)
            log_command("traceroute", format_cmd_line(trace_argv), trace_argv)
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
        print("    · ping...", flush=True)
        ping_ok, ping_out, ping_argv = ping_check(ip)
        log_command("ping", format_cmd_line(ping_argv), ping_argv)
        lines.append(f"  COMPLEMENTARY ping: {'host responds' if ping_ok else 'no response'}\n    {ping_out}")
        rtt_ms = parse_ping_rtt(ping_out) if ping_ok else None
        icmp_wait = compute_icmp_wait(rtt_ms, cfg)
        lines.append(
            f"  COMPLEMENTARY ICMP margin: {icmp_wait:.2f}s "
            f"(computed from ping mean RTT={rtt_ms}ms)" if rtt_ms is not None else
            f"  COMPLEMENTARY ICMP margin: {icmp_wait:.2f}s (default value, no RTT available)"
        )
        log_command("udp_send", format_udp_command(ip, port, icmp_wait))
        status, detail = udp_send(ip, port, icmp_wait=icmp_wait)
        lines.append(f"  RESULT: {status.upper()} - {detail}")
        print(f"    RESULT: {status.upper()} - {detail}", flush=True)
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
                    "  VERDICT: UDP_SENT_HOST_UNREACHABLE - datagram sent with no error, but the host "
                    "doesn't respond to ping either. Still NOT conclusive for the UDP port itself (see "
                    "UDP note): ICMP is often filtered independently of the data port, so ping failure "
                    "alone doesn't confirm a block; confirm with the receiving team in Remote cloud "
                    "domain whether the packet arrived."
                )
        else:
            verdict = "UDP_SEND_FAILED"

    icon = VERDICT_ICON.get(verdict, "?")
    lines.append(f"  ---> FINAL VERDICT: {icon} {verdict}\n")
    text = "\n".join(lines)
    log.write(text + "\n")
    log.flush()

    record = {
        "id": test["id"],
        "direction": test["direction"],
        "automatable": test["automatable"],
        "source": test["source"],
        "destination": test["destination"],
        "port": test["port"],
        "protocol": test["protocol"],
        "protocol_label": test["protocol_label"],
        "service": test.get("service"),
        "started_at": started_at,
        "finished_at": now(),
        "verdict": verdict,
        "verdict_icon": icon,
        "comment": VERDICT_COMMENT.get(verdict),
        "commands": commands,
        "detail": text,
    }
    return verdict, record


def run_skipped(test: dict, log) -> tuple[str, dict]:
    started_at = now()
    header = f"[{started_at}] [{test['id']}] {format_endpoint(test)}"
    note = test.get("note", "Requires manual testing from Remote cloud domain.")
    verdict = "SKIPPED_MANUAL_TEST_REQUIRED"
    icon = VERDICT_ICON[verdict]
    text = f"{header}\n  ---> FINAL VERDICT: {icon} {verdict} ({note})\n"
    log.write(text + "\n")
    log.flush()

    record = {
        "id": test["id"],
        "direction": test["direction"],
        "automatable": test["automatable"],
        "source": test["source"],
        "destination": test["destination"],
        "port": test["port"],
        "protocol": test["protocol"],
        "protocol_label": test["protocol_label"],
        "service": test.get("service"),
        "started_at": started_at,
        "finished_at": now(),
        "verdict": verdict,
        "verdict_icon": icon,
        "comment": None,
        "commands": [],
        "detail": text,
    }
    return verdict, record


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_probe.py", description=__doc__)
    sub = parser.add_subparsers(dest="command")

    p_batch = sub.add_parser(
        "batch",
        help="Run every automatable case in a spec file, unattended (the original full-battery mode).",
        description="Runs every automatable case in a spec file, unattended, logging full diagnosis detail to --out.",
    )
    p_batch.add_argument("--spec", type=Path, required=True, help="Path to connectivity-test-spec.json")
    p_batch.add_argument("--out", type=Path, required=True, help="Path to the output log file")
    p_batch.add_argument(
        "--filter-source-type",
        choices=["VM", "K8s Cluster"],
        default=None,
        help="Only runs tests whose source.type matches (useful for separating VM vs cluster).",
    )
    p_batch.add_argument(
        "--config",
        type=Path,
        default=None,
        help=(
            "Path to connectivity-tests.toml ([probe] table) to calibrate the UDP ICMP margin. "
            "If omitted or the file doesn't exist, the embedded default values are used."
        ),
    )

    p_list = sub.add_parser(
        "list",
        help="Print the known automatable test cases (destination IP, port, protocol) in a human-friendly table.",
        description="Prints the automatable test cases from a spec file: id, destination IP, port and protocol only.",
    )
    p_list.add_argument("--spec", type=Path, required=True, help="Path to connectivity-test-spec.json")
    p_list.add_argument(
        "--filter-source-type",
        choices=["VM", "K8s Cluster"],
        default=None,
        help="Only lists tests whose source.type matches.",
    )

    p_tcp = sub.add_parser(
        "tcp",
        help="Run a single manual TCP probe against ip:port, with the same diagnosis methodology as batch.",
        description=(
            "Runs a single TCP probe against ip:port by hand: attempts the connection and, if it fails, "
            "runs the same ping/traceroute diagnosis used in batch mode, printing full detail to stdout."
        ),
    )
    p_tcp.add_argument("ip", help="Destination IP address")
    p_tcp.add_argument("port", type=int, help="Destination port")

    p_udp = sub.add_parser(
        "udp",
        help="Run a single manual UDP probe against ip:port, with the same diagnosis methodology as batch.",
        description=(
            "Runs a single UDP probe against ip:port by hand: pings first (to calibrate the ICMP wait "
            "margin), then sends the datagram twice to try to observe an ICMP port-unreachable, printing "
            "full detail to stdout. Does not require ping/ICMP to succeed -- see README's "
            "'Low-level: raw Linux tools' appendix."
        ),
    )
    p_udp.add_argument("ip", help="Destination IP address")
    p_udp.add_argument("port", type=int, help="Destination port")
    p_udp.add_argument(
        "--config",
        type=Path,
        default=None,
        help=(
            "Path to connectivity-tests.toml ([probe] table) to calibrate the UDP ICMP margin. "
            "If omitted or the file doesn't exist, the embedded default values are used."
        ),
    )

    return parser


def cmd_batch(args: argparse.Namespace) -> None:
    cfg = load_probe_config(args.config)

    with args.spec.open(encoding="utf-8") as f:
        spec = json.load(f)

    tests = spec.get("tests", [])
    if args.filter_source_type:
        tests = [t for t in tests if t["source"].get("type") == args.filter_source_type]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    total = len(tests)
    print(f"Running {total} tests...", flush=True)
    print(flush=True)
    verdicts: dict[str, int] = {}
    records: list[dict] = []
    run_started_at = now()
    with args.out.open("w", encoding="utf-8") as log:
        log.write(f"# Local cloud domain -> Remote cloud domain connectivity test run\n# Start: {run_started_at}\n\n")
        for i, test in enumerate(tests, start=1):
            print(f"▶ [{i}/{total}] {test['id']}  {format_endpoint(test)}", flush=True)
            t0 = time.monotonic()
            if test.get("automatable"):
                verdict, record = run_test(test, log, cfg)
            else:
                verdict, record = run_skipped(test, log)
            elapsed = time.monotonic() - t0
            record["log_file"] = args.out.name
            record["duration_seconds"] = round(elapsed, 3)
            records.append(record)
            print(f"    {VERDICT_ICON.get(verdict, '?')} {verdict}  ({elapsed:.1f}s)", flush=True)
            print(flush=True)
            verdicts[verdict] = verdicts.get(verdict, 0) + 1

        log.write("# Summary\n")
        for verdict, count in sorted(verdicts.items()):
            log.write(f"#   {VERDICT_ICON.get(verdict, '?')} {verdict}: {count}\n")
        log.write(f"# End: {now()}\n")

    results_path = args.out.with_suffix(".json")
    with results_path.open("w", encoding="utf-8") as jf:
        json.dump(
            {
                "generated_by": "run_probe.py batch",
                "spec": str(args.spec),
                "filter_source_type": args.filter_source_type,
                "log_file": args.out.name,
                "run_started_at": run_started_at,
                "run_finished_at": now(),
                "results": records,
            },
            jf,
            indent=2,
            ensure_ascii=False,
        )

    print()
    print(f"✅ Log written to {args.out}")
    print(f"✅ Structured results written to {results_path}")
    print()
    print("Summary:")
    for verdict, count in sorted(verdicts.items()):
        print(f"  {VERDICT_ICON.get(verdict, '?')} {verdict}: {count}")
    print()


def cmd_list(args: argparse.Namespace) -> None:
    with args.spec.open(encoding="utf-8") as f:
        spec = json.load(f)

    tests = [t for t in spec.get("tests", []) if t.get("automatable")]
    if args.filter_source_type:
        tests = [t for t in tests if t["source"].get("type") == args.filter_source_type]

    if not tests:
        print("No automatable test cases found.")
        return

    tests.sort(key=lambda t: t["id"])
    id_w = max(len(t["id"]) for t in tests)
    ip_w = max(len(t["destination"]["ip"]) for t in tests)
    print(f"{'ID':<{id_w}}  {'DEST IP':<{ip_w}}  {'PORT':>5}  PROTO")
    for t in tests:
        print(f"{t['id']:<{id_w}}  {t['destination']['ip']:<{ip_w}}  {t['port']:>5}  {t['protocol']}")
    print(f"\n{len(tests)} automatable test case(s).")


def cmd_tcp(args: argparse.Namespace) -> None:
    test = {
        "id": "manual",
        "direction": "local_cloud_domain_to_remote_cloud_domain",
        "automatable": True,
        "source": {"type": None, "range": None, "description": "manual"},
        "destination": {"ip": args.ip},
        "port": args.port,
        "protocol": "tcp",
        "protocol_label": "TCP",
    }
    run_test(test, sys.stdout, load_probe_config(None))


def cmd_udp(args: argparse.Namespace) -> None:
    test = {
        "id": "manual",
        "direction": "local_cloud_domain_to_remote_cloud_domain",
        "automatable": True,
        "source": {"type": None, "range": None, "description": "manual"},
        "destination": {"ip": args.ip},
        "port": args.port,
        "protocol": "udp",
        "protocol_label": "UDP",
    }
    run_test(test, sys.stdout, load_probe_config(args.config))


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    commands = {"batch": cmd_batch, "list": cmd_list, "tcp": cmd_tcp, "udp": cmd_udp}
    commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
