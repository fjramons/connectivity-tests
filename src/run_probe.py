#!/usr/bin/env python3
"""Motor de pruebas de conectividad Domain 3 -> Domain 2.

Solo usa la libreria estandar de Python (json, socket, subprocess, argparse)
para poder ejecutarse dentro de nicolaka/netshoot (o cualquier VM/pod con
python3) sin necesidad de `pip install` dentro de Domain 3.

Lee un fichero de spec (connectivity-test-spec.json), y para cada caso con
"automatable": true intenta la conexion TCP/UDP; si falla (o siempre, en el
caso de UDP, que no confirma entrega), ejecuta diagnosticos complementarios
(ping, tcptraceroute/traceroute) para distinguir "host inalcanzable" de
"puerto/servicio caido pero host vivo". Los casos domain2_to_domain3 (donde
Domain 3 actua de servidor) se registran como pendientes de prueba manual
desde Domain 2 -- ver README.md.
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

# Configuracion del margen ICMP de UDP (ver README, seccion 5, nota UDP).
# Sobreescribible via --config connectivity-tests.toml, tabla [probe].
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
    """Devuelve (status, detalle). status en {"connected","refused","timeout","no_route","error"}.

    La distincion refused/timeout/no_route es la clave del diagnostico A/B/C:
    un rechazo inmediato (RST) prueba que el paquete llego al host y el
    firewall lo dejo pasar (caso A); un "no route to host"/"network
    unreachable" es un ICMP explicito de un router intermedio (distinto de un
    timeout silencioso, aunque a efectos de veredicto siga siendo caso B/C);
    un timeout puro no dice nada por si solo (hace falta ping/traceroute).
    """
    try:
        with socket.create_connection((ip, port), timeout=TCP_TIMEOUT):
            return "connected", "conexion TCP establecida"
    except ConnectionRefusedError as e:
        return "refused", str(e)
    except (TimeoutError, socket.timeout) as e:
        return "timeout", str(e)
    except OSError as e:
        if e.errno in _NO_ROUTE_ERRNOS:
            return "no_route", str(e)
        return "error", str(e)


def udp_send(ip: str, port: int, icmp_wait: float) -> tuple[str, str]:
    """Devuelve (status, detalle). status en {"sent","refused","send_failed"}.

    Usa un socket UDP "conectado" + doble envio para intentar capturar un ICMP
    port-unreachable asincrono (en Linux se entrega en la siguiente operacion
    sobre el socket tras el primer send, no en el propio send). Si se recibe,
    es el equivalente UDP del caso A (rechazo => red abierta, falta el
    listener). Si no se recibe nada, NO es concluyente: muchos firewalls
    corporativos filtran el ICMP de vuelta aunque el trafico UDP si pase.

    "icmp_wait" es el margen entre los dos send(), calibrado por el llamador
    a partir del RTT medido con ping (ver compute_icmp_wait).
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
                    "ICMP port-unreachable recibido tras el envio: el host rechaza "
                    "activamente el puerto UDP"
                )
        return "sent", "datagrama(s) UDP enviado(s) sin error de socket (sin ICMP port-unreachable observado)"
    except OSError as e:
        return "send_failed", f"no se pudo enviar el datagrama UDP: {e}"


_PING_RTT_RE = re.compile(r"rtt [\w/]+ = [\d.]+/([\d.]+)/[\d.]+/[\d.]+\s*ms")


def parse_ping_rtt(ping_output: str) -> float | None:
    """Extrae el RTT medio (ms) de la linea final de `ping` (formato iputils)."""
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
        return False, f"herramienta '{cmd[0]}' no disponible en este entorno"
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode == 0, output.strip()
    except subprocess.TimeoutExpired:
        return False, f"timeout tras {timeout}s ejecutando: {' '.join(cmd)}"


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
    """Frase con el ultimo hop que respondio y si es el destino final o no.

    Best-effort: parsea el formato habitual de traceroute/tcptraceroute
    ("N  host (ip)  tiempo..." o "N  * * *" para saltos sin respuesta). Si no
    reconoce ningun hop, lo dice explicitamente en vez de fallar.
    """
    last_hop: tuple[str, str] | None = None
    for line in output.splitlines():
        m = _HOP_LINE_RE.match(line)
        if not m:
            continue
        hop_num, rest = m.groups()
        if rest.strip().startswith("*"):
            continue  # hop sin respuesta
        ip_m = _HOP_IP_RE.search(rest)
        if ip_m:
            last_hop = (hop_num, ip_m.group(1))
    if last_hop is None:
        return "ningun hop respondio (todo '* * *'); no se puede saber donde se corta el camino"
    hop_num, hop_ip = last_hop
    if hop_ip == target_ip:
        return f"el trafico llega hasta el propio destino ({target_ip}) en el hop {hop_num}"
    return (
        f"el trafico llega hasta el hop {hop_num} ({hop_ip}) y no se alcanza el "
        f"destino final ({target_ip}) -- ahi es donde hay que buscar el firewall/ruta que falta"
    )


def format_endpoint(test: dict) -> str:
    src = test["source"]
    dst = test["destination"]
    return (
        f"{src.get('range') or src.get('description') or '?'} "
        f"({src.get('type') or 'desconocido'}) -> "
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
            # Rechazo inmediato (RST): el paquete llego al host y el firewall lo
            # dejo pasar. Senal fuerte (caso A) -- no hace falta ping para decidir,
            # pero se registra igualmente como contexto adicional en el log.
            ping_ok, ping_out = ping_check(ip)
            lines.append(f"  COMPLEMENTARIO ping: {'host responde' if ping_ok else 'sin respuesta'}\n    {ping_out}")
            verdict = "PORT_REFUSED_NETWORK_OPEN"
            lines.append(
                "  VERDICT: PORT_REFUSED_NETWORK_OPEN - el host rechazo la conexion activamente (RST). "
                "Prueba que la red/firewall dejan pasar el trafico hasta ese puerto (caso A); "
                "falta el servicio, no es un problema de firewall."
            )
        else:  # timeout, no_route u otro error: ambiguo, hace falta ping/traceroute
            if status == "no_route":
                lines.append(
                    "  NOTA: fallo explicito de enrutamiento (ICMP host/network unreachable), no un "
                    "timeout silencioso -- un router intermedio respondio activamente."
                )
            ping_ok, ping_out = ping_check(ip)
            lines.append(f"  COMPLEMENTARIO ping: {'host responde' if ping_ok else 'sin respuesta'}\n    {ping_out}")
            trace_ok, trace_out = traceroute_check(ip, port)
            lines.append(f"  COMPLEMENTARIO traceroute:\n    {trace_out}")
            lines.append(f"  COMPLEMENTARIO resumen traceroute: {summarize_traceroute(trace_out, ip)}")
            if ping_ok:
                verdict = "PORT_CLOSED_HOST_REACHABLE"
                lines.append(
                    "  VERDICT: PORT_CLOSED_HOST_REACHABLE - el host responde a ping pero el puerto no "
                    "respondio ni con RST ni con datos (caso B). Senal mas debil que un rechazo explicito: "
                    "es compatible con que el servicio aun no este desplegado, pero tambien con un firewall "
                    "que deja pasar ICMP y bloquea selectivamente ese puerto TCP."
                )
            else:
                verdict = "HOST_UNREACHABLE"
                lines.append(
                    "  VERDICT: HOST_UNREACHABLE - ni el puerto ni el ping responden (caso C). No concluyente: "
                    "revisar regla de firewall/ruta hacia Domain 2 (o si el host esta apagado)."
                )
    else:  # udp: primero ping (para calibrar el margen ICMP con el RTT), luego el envio
        ping_ok, ping_out = ping_check(ip)
        lines.append(f"  COMPLEMENTARIO ping: {'host responde' if ping_ok else 'sin respuesta'}\n    {ping_out}")
        rtt_ms = parse_ping_rtt(ping_out) if ping_ok else None
        icmp_wait = compute_icmp_wait(rtt_ms, cfg)
        lines.append(
            f"  COMPLEMENTARIO margen ICMP: {icmp_wait:.2f}s "
            f"(calculado a partir de RTT medio de ping={rtt_ms}ms)" if rtt_ms is not None else
            f"  COMPLEMENTARIO margen ICMP: {icmp_wait:.2f}s (valor por defecto, sin RTT disponible)"
        )
        status, detail = udp_send(ip, port, icmp_wait=icmp_wait)
        lines.append(f"  RESULT: {status.upper()} - {detail}")
        if status == "refused":
            verdict = "UDP_REFUSED_NETWORK_OPEN"
            lines.append(
                "  VERDICT: UDP_REFUSED_NETWORK_OPEN - ICMP port-unreachable recibido (equivalente UDP del "
                "caso A). Red/firewall dejan pasar el trafico hasta ese puerto; falta el listener."
            )
        elif status == "sent":
            if ping_ok:
                verdict = "UDP_SENT_HOST_REACHABLE"
                lines.append(
                    "  VERDICT: UDP_SENT_HOST_REACHABLE - datagrama enviado sin error y host alcanzable por "
                    "ICMP, pero sin ICMP port-unreachable observado. NO concluyente (a diferencia de TCP): "
                    "muchos firewalls filtran ese ICMP de vuelta aunque el UDP si pase; validar con el "
                    "equipo receptor en Domain 2 si el paquete llego."
                )
            else:
                verdict = "UDP_SENT_HOST_UNREACHABLE"
                lines.append(
                    "  VERDICT: UDP_SENT_HOST_UNREACHABLE - el envio UDP no dio error de socket, pero el host "
                    "no responde a ping. Podria estar bloqueado por firewall o el host caido."
                )
        else:
            verdict = "UDP_SEND_FAILED"

    lines.append(f"  ---> VEREDICTO FINAL: {verdict}\n")
    text = "\n".join(lines)
    log.write(text + "\n")
    log.flush()
    return verdict


def run_skipped(test: dict, log) -> str:
    header = f"[{now()}] [{test['id']}] {format_endpoint(test)}"
    note = test.get("note", "Requiere prueba manual desde Domain 2.")
    text = f"{header}\n  ---> VEREDICTO FINAL: SKIPPED_MANUAL_TEST_REQUIRED ({note})\n"
    log.write(text + "\n")
    log.flush()
    return "SKIPPED_MANUAL_TEST_REQUIRED"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True, help="Ruta al connectivity-test-spec.json")
    parser.add_argument("--out", type=Path, required=True, help="Ruta del fichero de log de salida")
    parser.add_argument(
        "--filter-source-type",
        choices=["VM", "K8s Cluster"],
        default=None,
        help="Solo ejecuta pruebas cuyo source.type coincida (util para separar VM vs cluster).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=(
            "Ruta a connectivity-tests.toml (tabla [probe]) para calibrar el margen ICMP de UDP. "
            "Si se omite o el fichero no existe, se usan los valores por defecto embebidos."
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
        log.write(f"# Ejecucion de pruebas de conectividad Domain 3 -> Domain 2\n# Inicio: {now()}\n\n")
        for test in tests:
            if test.get("automatable"):
                verdict = run_test(test, log, cfg)
            else:
                verdict = run_skipped(test, log)
            verdicts[verdict] = verdicts.get(verdict, 0) + 1

        log.write("# Resumen\n")
        for verdict, count in sorted(verdicts.items()):
            log.write(f"#   {verdict}: {count}\n")
        log.write(f"# Fin: {now()}\n")

    print(f"Log escrito en {args.out}")
    for verdict, count in sorted(verdicts.items()):
        print(f"  {verdict}: {count}")


if __name__ == "__main__":
    sys.exit(main())
