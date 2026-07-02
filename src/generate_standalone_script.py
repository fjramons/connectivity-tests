#!/usr/bin/env python3
"""Genera standalone/<nombre>.sh: un unico script bash AUTOCONTENIDO, pensado
para pegarse entero en una sesion de terminal del jumphost de Domain 3 (donde
transferir ficheros es dificil, pero pegar un bloque de texto es facil).

El script generado:
  1. Escribe run_probe.py en un directorio temporal LOCAL del jumphost/VM
     (no requiere scp/transferencia externa: el contenido viaja embebido).
  2. Escribe el subconjunto del spec (tests automatizables cuyo source.type
     coincide con --source-type, VM por defecto) como spec.json, tambien local.
  3. Ejecuta las pruebas con `docker run --network host nicolaka/netshoot:v0.15`.
  4. Vuelca el log resultante por stdout (`cat`) para que el operador copie la
     salida y la guarde en outputs/ en el PC de laboratorio.

Se ejecuta en el PC de desarrollo con `uv run src/generate_standalone_script.py`.
Solo usa la libreria estandar.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SPEC = ROOT / "inputs" / "connectivity-test-spec.json"
DEFAULT_PROBE = ROOT / "src" / "run_probe.py"
DEFAULT_PROBE_CONFIG = ROOT / "connectivity-tests.toml"
DEFAULT_OUT = ROOT / "standalone" / "domain3-to-domain2-vm-tests.sh"

SCRIPT_TEMPLATE = """\
#!/usr/bin/env bash
# Script AUTOCONTENIDO de pruebas de conectividad Domain 3 (VM) -> Domain 2.
# Generado por src/generate_standalone_script.py -- no editar a mano.
#
# Uso: pegar este fichero completo en la sesion de terminal del jumphost/VM
# (o ejecutarlo si se pudo copiar de algun modo), y al final copiar el log
# que se imprime por pantalla a un fichero dentro de outputs/ en el PC de
# laboratorio, p. ej.:
#   outputs/domain3-to-domain2-vm-tests-$(date +%Y%m%dT%H%M%S).log
#
# Requiere: docker instalado y con salida de red hacia Domain 2 (misma IP de
# egress que la VM, gracias a --network host).
set -euo pipefail

WORKDIR="$(mktemp -d /tmp/conntest.XXXXXX)"
trap 'rm -rf "$WORKDIR"' EXIT

cat > "$WORKDIR/run_probe.py" <<'PROBE_PY_EOF'
{probe_source}
PROBE_PY_EOF

cat > "$WORKDIR/spec.json" <<'SPEC_JSON_EOF'
{spec_json}
SPEC_JSON_EOF

cat > "$WORKDIR/connectivity-tests.toml" <<'PROBE_TOML_EOF'
{probe_config}
PROBE_TOML_EOF

echo "Ejecutando {n_tests} pruebas de conectividad (source.type={source_type}) via docker/netshoot..." >&2

docker run --rm --network host -v "$WORKDIR:/data" nicolaka/netshoot:v0.15 \\
  python3 /data/run_probe.py --spec /data/spec.json --config /data/connectivity-tests.toml --out /data/result.log

echo "" >&2
echo "===== INICIO DEL LOG (copia desde aqui hasta el marcador de FIN a outputs/ en el PC de laboratorio) =====" >&2
cat "$WORKDIR/result.log"
echo "===== FIN DEL LOG =====" >&2
"""


def build_filtered_spec(spec_path: Path, source_type: str) -> dict:
    with spec_path.open(encoding="utf-8") as f:
        spec = json.load(f)
    tests = [
        t
        for t in spec.get("tests", [])
        if t.get("automatable") and t["source"].get("type") == source_type
    ]
    return {
        "metadata": {**spec.get("metadata", {}), "filtered_for_source_type": source_type},
        "tests": tests,
        "unresolved": [],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--probe", type=Path, default=DEFAULT_PROBE)
    parser.add_argument("--probe-config", type=Path, default=DEFAULT_PROBE_CONFIG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--source-type", default="VM", help="source.type a incluir (por defecto: VM)")
    args = parser.parse_args()

    probe_source = args.probe.read_text(encoding="utf-8")
    probe_config = args.probe_config.read_text(encoding="utf-8") if args.probe_config.exists() else ""
    filtered = build_filtered_spec(args.spec, args.source_type)
    spec_json = json.dumps(filtered, indent=2, ensure_ascii=False)

    delimiters = {
        "PROBE_PY_EOF": probe_source,
        "SPEC_JSON_EOF": spec_json,
        "PROBE_TOML_EOF": probe_config,
    }
    for delimiter, content in delimiters.items():
        if delimiter in content:
            raise SystemExit(
                f"Colision de delimitador heredoc: el contenido embebido contiene '{delimiter}'. "
                "Renombra los delimitadores en la plantilla."
            )

    script = SCRIPT_TEMPLATE.format(
        probe_source=probe_source,
        spec_json=spec_json,
        probe_config=probe_config,
        n_tests=len(filtered["tests"]),
        source_type=args.source_type,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(script, encoding="utf-8")
    args.out.chmod(0o755)
    print(f"Generado {args.out} ({len(filtered['tests'])} pruebas, source.type={args.source_type})")


if __name__ == "__main__":
    sys.exit(main())
