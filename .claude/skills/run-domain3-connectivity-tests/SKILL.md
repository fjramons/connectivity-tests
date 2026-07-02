---
name: run-domain3-connectivity-tests
description: Guia para ejecutar las pruebas automatizadas de conectividad Domain 3 -> Domain 2 (EC.3), eligiendo el backend correcto (K8s desde el PC de laboratorio, o VM/jumphost con el script autocontenido) y consolidando resultados en outputs/.
---
# Ejecutar las pruebas de conectividad Domain 3 → Domain 2

Solo se automatiza la dirección `domain3_to_domain2` (Domain 3 actúa de
cliente): es la única que se puede lanzar sin depender de que alguien en
Domain 2 ejecute algo. Los casos `domain2_to_domain3` requieren coordinación
manual con Domain 2 — ver la sección 3 de `README.md`.

## Elegir el backend según `source.type`

Cada caso de prueba en `inputs/connectivity-test-spec.json` tiene un
`source.type`: `"K8s Cluster"` o `"VM"`. El backend a usar depende de eso:

- **`source.type == "K8s Cluster"`** → Backend K8s, ejecutado **desde el PC de
  laboratorio** (tiene `kubectl` con acceso directo al cluster):
  ```bash
  kubectl apply -f manifests/netshoot-client-k8s.yaml   # una sola vez, si no existe ya
  src/run_via_kubectl.sh [namespace] [nombre-deployment]
  ```
  El log queda automáticamente en `outputs/domain3-to-domain2-k8s-<timestamp>.log`.

- **`source.type == "VM"`** → Backend VM/jumphost. En el PC de desarrollo, genera
  el script autocontenido (si no está ya generado o el spec cambió):
  ```bash
  uv run src/generate_standalone_script.py
  ```
  Lleva `standalone/domain3-to-domain2-vm-tests.sh` al PC de laboratorio (OneDrive
  Web), abre una sesión al jumphost/VM desde ahí, y **pega el contenido completo
  del fichero** en la terminal (no requiere transferencia de ficheros: el script
  escribe sus propios temporales localmente y solo necesita Docker). Copia el
  bloque de log delimitado por `===== INICIO DEL LOG =====` / `===== FIN DEL LOG =====`
  a un fichero nuevo en `outputs/` en el PC de laboratorio.

## Interpretar los resultados

Antes de concluir que el firewall está mal, revisa el veredicto completo — un
`FAIL` de TCP/UDP por sí solo no confirma un problema de firewall, porque el
servicio de Domain 2 podría no estar desplegado todavía:

- `PASS`: conectividad OK.
- `PORT_REFUSED_NETWORK_OPEN`: rechazo TCP inmediato (RST) — caso A, la señal
  más fuerte: la red/firewall dejan pasar el tráfico hasta ese puerto, solo
  falta que el servicio esté desplegado en Domain 2. No es un problema de
  firewall.
- `PORT_CLOSED_HOST_REACHABLE`: TCP agota el timeout (sin RST) pero el host
  responde a ping — caso B, señal más débil que un rechazo explícito:
  probablemente el servicio aún no está desplegado, pero también podría ser
  un firewall que deja pasar ICMP y filtra selectivamente ese puerto.
- `HOST_UNREACHABLE`: ni el puerto ni el ping responden — caso C, no
  concluyente: revisar la regla de firewall o la ruta.
- `UDP_REFUSED_NETWORK_OPEN`: se recibió un ICMP port-unreachable tras el
  envío — equivalente UDP del caso A.
- `UDP_SENT_*`: sin ICMP observado, UDP no confirma entrega — a diferencia de
  TCP, la ausencia de rechazo NO es concluyente (muchos firewalls filtran ese
  ICMP de vuelta); usar el resultado del ping como proxy de alcanzabilidad, y
  confirmar con el equipo receptor si hace falta.
- `SKIPPED_MANUAL_TEST_REQUIRED`: caso `domain2_to_domain3`, no se intentó
  automáticamente — ver la sección 3 de `README.md`.

Si varios casos seguidos dan `HOST_UNREACHABLE` para el mismo destino, sospecha
de una regla de firewall no aplicada correctamente en vez de un problema puntual
de servicio. La sección 5 de `README.md` documenta cómo reproducir a mano
(sin scripts) el mismo diagnóstico A/B/C que aplica este runner.
