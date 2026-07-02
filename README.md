# Pruebas de conectividad Domain 2 ⟷ Domain 3 (EC.3)

Herramientas para validar, de forma sistemática, la conectividad y las reglas
de firewall abiertas entre "Domain 2" y "Domain 3" (EC.3), a partir de la
matriz de reglas en `inputs/*.csv`.

## Los tres entornos

Este proyecto se mueve entre tres entornos con capacidades muy distintas:

| Entorno | Acceso de red | Qué se hace ahí |
|---|---|---|
| **PC de desarrollo** | Sin acceso a Domain 3 | Generar el spec, los manifiestos K8s y el script autocontenido, con `uv` |
| **PC de laboratorio** | `kubectl` directo a los clusters de Domain 3, y acceso al jumphost (SSH) | Aplicar manifiestos K8s, ejecutar la automatización contra clusters, abrir sesión al jumphost |
| **Jumphost / VM en Domain 3** | Acceso directo a Domain 2, pero transferir ficheros es difícil | Pegar el script autocontenido generado en el PC de desarrollo, o usar Docker Compose manualmente |

Los artefactos generados en el PC de desarrollo (`inputs/connectivity-test-spec.*`,
`manifests/`, `standalone/`) se trasladan al PC de laboratorio manualmente vía
**OneDrive Web** (no automatizable). Desde ahí:
- Todo lo relativo a **K8s** (`kubectl apply` / `exec` / `cp`) se ejecuta directamente
  desde el PC de laboratorio.
- Todo lo relativo a **VM/Docker** se ejecuta abriendo una sesión al jumphost y
  pegando ahí el script de `standalone/` (autocontenido: no requiere `scp`).

## Prerrequisitos e instalación (PC de desarrollo)

- [`uv`](https://docs.astral.sh/uv/) instalado:
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
- Python gestionado por `uv` (no hace falta instalarlo aparte):
  ```bash
  uv python install 3.12
  ```
- Crear el entorno virtual con las dependencias (`pyyaml`) declaradas en `pyproject.toml`:
  ```bash
  uv sync
  ```
- Docker (opcional, solo si quieres probar localmente la imagen `nicolaka/netshoot:v0.15`
  antes de llevarla a Domain 3).

Prerrequisitos en el resto de entornos:
- **PC de laboratorio**: `kubectl` configurado con contexto a los clusters de Domain 3;
  acceso SSH (u otro) al jumphost; acceso a OneDrive Web para recibir los artefactos.
- **Jumphost / VM de Domain 3**: Docker + Docker Compose instalados, con salida de
  red hacia Domain 2 en los puertos a validar.

## Estructura del repositorio

```
inputs/                CSV de origen + spec generado (YAML legible + JSON para el runner)
connectivity-tests.toml  Config del generador (emparejamiento puerto<->protocolo)
src/                    Scripts (generadores en el PC de desarrollo, runner stdlib-only)
manifests/              Manifiestos K8s/Compose de CLIENTE (netshoot) y de SERVIDOR (por destino)
standalone/             Script(s) autocontenidos para pegar en el jumphost/VM
outputs/                Logs de las ejecuciones
```

## 1. Generar la especificación de pruebas

A partir de los dos CSV de `inputs/`:

```bash
uv run src/generate_test_spec.py
```

Esto genera `inputs/connectivity-test-spec.yaml` (legible, editable a mano) y su
gemelo `inputs/connectivity-test-spec.json` (el que realmente lee el runner,
sin depender de PyYAML dentro de Domain 3).

Cada CSV se expande a casos de prueba individuales (una IP × un puerto), incluyendo
listas (`10.2.113.129, 10.2.113.131`) y rangos (`10.180.141.99-10.180.141.105`).
Cuando puertos y protocolos tienen la misma cantidad de elementos en una fila
(p. ej. 3 puertos y 3 protocolos), el emparejamiento se controla desde
`connectivity-tests.toml` (`port_protocol_pairing`: `one_to_one` por defecto,
o `cross_product`); también se puede forzar puntualmente con
`--port-protocol-pairing cross_product`.

Si editas el YAML a mano (por ejemplo, para anotar o corregir un caso en
`unresolved`), resincroniza solo el JSON sin volver a leer los CSV:

```bash
uv run src/generate_test_spec.py --from-yaml
```

Cada caso de prueba indica `direction` (`domain3_to_domain2` si Domain 3 actúa de
cliente, `domain2_to_domain3` si actúa de servidor) y `automatable` (solo `true`
para `domain3_to_domain2`, que es lo único que podemos lanzar sin depender de que
alguien en Domain 2 ejecute algo).

## 2. Lanzar clientes de prueba en Domain 3 (manual)

### En un cluster K8s

```bash
kubectl apply -f manifests/netshoot-client-k8s.yaml   # despliega el mismo namespace que la app real
kubectl exec -it deploy/netshoot-client -- bash
```

Dentro del pod, con las herramientas ya incluidas en `nicolaka/netshoot:v0.15`:

```bash
nc -zv 10.45.66.48 1167          # TCP: ¿puerto abierto?
nc -u -zv 10.45.66.48 1167       # UDP: envío best-effort
curl -kv https://10.180.141.111:443
openssl s_client -connect 10.180.141.110:6566
ping -c4 10.45.66.48
tcptraceroute 10.45.66.48 1167   # traceroute a nivel de puerto TCP
```

### En una VM (Docker Compose)

```bash
docker compose -f manifests/netshoot-client-docker-compose.yml up -d
docker compose -f manifests/netshoot-client-docker-compose.yml exec netshoot bash
```

(mismos comandos que arriba; `network_mode: host` hace que el tráfico salga con
la IP propia de la VM).

Si alguno de estos comandos falla, no te quedes en "no funciona": la sección 5
explica cómo leer el fallo para saber si es un problema de firewall o
simplemente que el servicio de Domain 2 aún no está desplegado.

## 3. Servidores de prueba en Domain 3 y cómo probarlos desde Domain 2

Los destinos de `domain2_to_domain3` (Spotfire, Vertica/Olap DB, IAM/Keycloak,
Ingress de CMM) ya pertenecen a apps reales que aún no están desplegadas. Para
poder validar el firewall sin esperar a que esas apps estén listas, genera un
manifiesto de servidor de prueba **por cada destino único** (mismo IP:puerto que
la app real):

```bash
uv run src/generate_server_manifests.py
kubectl apply -f manifests/servers/<slug>-k8s.yaml
```

Cada manifiesto despliega el mismo contenedor `nicolaka/netshoot:v0.15` actuando
de listener (`socat`) en el puerto exacto de la app real, con su propio `Service
type: LoadBalancer`. **No lo despliegues junto con la app real** en el mismo
puerto. Si ya existe un `Service` real con esa IP de LoadBalancer ya reservada y
autorizada en el firewall, edita el `Service` de ese manifiesto (o el selector
del real) para no crear una IP nueva no autorizada — el propio YAML generado
incluye este aviso como comentario.

Una vez desplegado, pide a alguien en Domain 2 que pruebe con herramientas
comunes de Linux:

```bash
nc -zv 10.11.119.182 443
curl -v https://10.11.119.182:443
openssl s_client -connect 10.11.119.180:5433
nmap -p 443,8443 10.11.119.178
ping -c4 10.11.119.182
traceroute 10.11.119.182
```

Si ninguna de estas pruebas funciona, no asumas que el firewall está mal
configurado: podría ser que el servicio de prueba no se haya desplegado, o que
el `Service` esté usando una IP de LoadBalancer distinta a la autorizada. La
sección 5 explica, paso a paso, cómo distinguir ambos casos con las mismas
herramientas (`nc`, `ping`, `traceroute`) usadas arriba.

## 4. Automatización Domain 3 → Domain 2

Solo se automatiza esta dirección (Domain 3 actúa de cliente), porque es la que
podemos ejecutar sin depender de que alguien en Domain 2 haga algo. El motor de
pruebas (`src/run_probe.py`) solo usa la librería estándar de Python: hace un
intento de conexión TCP o un envío UDP, y si falla (o siempre, para UDP, que no
confirma entrega) ejecuta `ping`/`tcptraceroute` como diagnóstico complementario
para distinguir "host inalcanzable" de "puerto/servicio caído pero host vivo".
Los casos `domain2_to_domain3` se registran como `SKIPPED_MANUAL_TEST_REQUIRED`
(ver sección 3). Además de los veredictos, el log incluye señales adicionales
que ayudan a interpretar un fallo sin tener que repetirlo a mano: si un TCP
falló con un "no route to host"/"network unreachable" explícito en vez de un
timeout silencioso, se anota como tal; y tras un `traceroute`/`tcptraceroute`
se añade una frase-resumen indicando hasta qué *hop* llegó el tráfico y si es
o no el propio destino (ver sección 5 para el detalle de cómo se interpreta).

El margen de espera usado en el truco de detección de ICMP en UDP (ver
sección 5, nota UDP) se calcula a partir del RTT medido por `ping` a ese
mismo host, y es configurable en `connectivity-tests.toml` (tabla `[probe]`)
si los valores por defecto no encajan con la latencia real Domain 3 → Domain 2;
ambos backends de abajo lo usan automáticamente si el fichero existe.

### Backend K8s (desde el PC de laboratorio)

```bash
kubectl apply -f manifests/netshoot-client-k8s.yaml   # una sola vez
src/run_via_kubectl.sh [namespace] [nombre-deployment]
```

El script hace `kubectl cp` de `run_probe.py`, del spec y de
`connectivity-tests.toml` (si existe) al pod, lo ejecuta con `kubectl exec`, y
copia el log resultante a `outputs/domain3-to-domain2-k8s-<timestamp>.log`.
Todo desde el PC de laboratorio, sin pasar por el jumphost.

### Backend VM/jumphost (script autocontenido)

En el PC de desarrollo, genera el script (embebe el código de `run_probe.py`,
el subconjunto del spec con `source.type == "VM"`, y `connectivity-tests.toml`
si existe):

```bash
uv run src/generate_standalone_script.py
```

Esto crea `standalone/domain3-to-domain2-vm-tests.sh`. Llévalo al PC de
laboratorio (OneDrive Web), abre una sesión SSH al jumphost/VM desde ahí, y
**pega el contenido completo del fichero** en la terminal (no requiere `scp`: el
script escribe sus propios ficheros temporales localmente y solo necesita
Docker instalado). Al terminar, imprime el log delimitado por
`===== INICIO DEL LOG =====` / `===== FIN DEL LOG =====`: copia ese bloque y
guárdalo como `outputs/domain3-to-domain2-vm-tests-<timestamp>.log` en el PC de
laboratorio.

## 5. Diagnóstico manual paso a paso (sin scripts)

Todo lo que hace `src/run_probe.py` se puede reproducir a mano con
herramientas comunes de Linux — esto es intencional: la automatización no
debe ser una caja negra. Esta sección explica cómo interpretar un fallo de
conectividad para distinguir tres situaciones:

- **A) No existe el servidor al otro lado, pero la red está abierta**
  (señal fuerte): la conexión TCP es **rechazada al instante** (`RST`,
  "Connection refused"). El paquete llegó hasta el host de destino y el
  firewall lo dejó pasar — solo falta que el servicio esté desplegado.
- **B) Probablemente no existe el servidor, pero con menos certeza que en A**:
  la conexión TCP **agota el timeout** (silencio total, sin `RST`) pero
  `ping`/`tcptraceroute` sí llegan al host. Compatible con "nada escuchando
  en ese puerto", pero también con un firewall que deja pasar ICMP y filtra
  selectivamente ese puerto TCP — señal más débil que A.
- **C) Ninguna de las dos anteriores** (no concluyente / sospechar del
  firewall): ni el puerto ni `ping`/`tcptraceroute` responden. Desde fuera no
  se puede distinguir "regla de firewall no aplicada" de "host apagado"; la
  primera sospecha razonable es la regla de firewall.

### Cómo distinguir "rechazado" de "timeout" a mano (TCP)

```bash
nc -zv -w3 <ip> <puerto>
# Ejemplo real de rechazo (caso A):
#   nc: connect to 127.0.0.1 port 54329 (tcp) failed: Connection refused
# Ejemplo real de timeout (casos B/C, sin RST):
#   nc: connect to 203.0.113.1 port 12345 (tcp) timed out: Operation now in progress

# Alternativa sin nc (mismo mensaje de error, vía bash):
bash -c 'cat < /dev/tcp/<ip>/<puerto>'
#   bash: connect: Connection refused        <- caso A
#   (se queda colgado hasta el timeout de bash/TCP, sin mensaje) <- casos B/C
```

El mensaje **"Connection refused" es siempre caso A** (rechazo inmediato,
red abierta). Cualquier otro desenlace (timeout, "No route to host", sin
respuesta) requiere el siguiente paso para distinguir B de C.

### Tabla de decisión

| Señal observada | Conclusión | Veredicto equivalente en `run_probe.py` |
|---|---|---|
| Conexión TCP establecida | Conectividad OK | `PASS` |
| TCP rechazado al instante ("Connection refused") | **A**: red abierta hasta el host, falta el servicio | `PORT_REFUSED_NETWORK_OPEN` |
| TCP agota timeout, pero `ping`/`tcptraceroute` llegan al host | **B**: probablemente falta el servicio, señal más débil que A | `PORT_CLOSED_HOST_REACHABLE` |
| TCP agota timeout y `ping`/`tcptraceroute` tampoco llegan | **C**: no concluyente / sospechar firewall | `HOST_UNREACHABLE` |
| UDP: ICMP port-unreachable recibido tras el envío | **A** (equivalente UDP): red abierta, falta el listener | `UDP_REFUSED_NETWORK_OPEN` |
| UDP: enviado sin error observable, `ping` OK | Ver nota UDP — no concluyente | `UDP_SENT_HOST_REACHABLE` |
| UDP: enviado sin error observable, `ping` falla | Ver nota UDP — no concluyente | `UDP_SENT_HOST_UNREACHABLE` |

### Nota sobre UDP

A diferencia de TCP, UDP no tiene handshake: no hay un "Connection refused"
directo. Existe un equivalente — el host de destino puede responder con un
ICMP *port-unreachable* cuando nada escucha en ese puerto — pero, a
diferencia del `RST` de TCP, **muchos firewalls corporativos filtran ese
ICMP de vuelta** aunque el UDP en sí pase sin problema. Por eso:

- Si se observa el ICMP port-unreachable, es una señal tan fuerte como el
  caso A en TCP (`UDP_REFUSED_NETWORK_OPEN`).
- Si **no** se observa, **no significa nada** (a diferencia de un timeout en
  TCP): es simplemente el caso más común, incluso con el firewall bien
  configurado. Solo queda usar `ping` como proxy de alcanzabilidad del host,
  y en última instancia confirmar con el equipo receptor en Domain 2 si les
  llegó el paquete (p. ej. revisando logs/trazas de Netcool para el caso
  SNMP).

`nc`/`bash` no exponen ese ICMP de forma fiable. Para replicarlo a mano, usa
este one-liner de Python (mismo truco que usa `run_probe.py`: socket UDP
"conectado" + doble envío, ya que en Linux el ICMP pendiente se entrega en la
siguiente operación sobre el socket, no en el primer `send`):

```bash
python3 -c "
import socket, time
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(3)
s.connect(('<ip>', <puerto>)); s.send(b'probe')
time.sleep(0.5)
try:
    s.send(b'probe'); print('sin ICMP port-unreachable (no concluyente)')
except ConnectionRefusedError:
    print('ICMP port-unreachable recibido -> puerto rechazado, red abierta')
"
```

### Procedimiento manual completo (ejemplo: Netcool UDP 1167)

1. Desplegar/entrar al cliente en Domain 3 (sección 2): `kubectl exec -it
   deploy/netshoot-client -- bash` o `docker compose ... exec netshoot bash`.
2. Intentar la conexión con la herramienta del protocolo correspondiente:
   - TCP: `nc -zv -w3 10.180.141.111 443`
   - UDP: el one-liner de Python de arriba, con la IP/puerto del caso (p. ej.
     `10.45.66.48` / `1167`).
3. Si el resultado es "Connection refused" (TCP) o ICMP port-unreachable
   (UDP) → **caso A**, fin del diagnóstico: falta el servicio, no es firewall.
4. Si no, `ping -c4 <ip>`.
5. Si el ping responde, `tcptraceroute <ip> <puerto>` (TCP) o `traceroute
   <ip>` (UDP) para confirmar que el camino llega hasta el destino.
6. Aplicar la tabla de decisión de arriba con lo observado en 2-5.

## Interpretación de veredictos

| Veredicto | Significado |
|---|---|
| `PASS` | Conexión TCP establecida — firewall y servicio OK |
| `PORT_REFUSED_NETWORK_OPEN` | Rechazo TCP inmediato (RST) — caso A: red/firewall abiertos hasta el puerto, falta el servicio en Domain 2 |
| `PORT_CLOSED_HOST_REACHABLE` | TCP agota el timeout pero el host responde a ping — caso B: probablemente falta el servicio, señal más débil que un rechazo explícito |
| `HOST_UNREACHABLE` | Ni el puerto ni el ping responden — caso C, no concluyente: revisar regla de firewall/ruta |
| `UDP_REFUSED_NETWORK_OPEN` | ICMP port-unreachable recibido tras el envío UDP — equivalente UDP del caso A |
| `UDP_SENT_HOST_REACHABLE` | Datagrama UDP enviado sin error y host responde a ping, pero sin ICMP observado — no concluyente, confirmar con el equipo receptor |
| `UDP_SENT_HOST_UNREACHABLE` | Datagrama UDP enviado sin error de socket, pero el host no responde a ping — posible bloqueo de firewall |
| `UDP_SEND_FAILED` | Error de socket al enviar el datagrama UDP |
| `SKIPPED_MANUAL_TEST_REQUIRED` | Domain 3 actúa de servidor: requiere que alguien en Domain 2 lo pruebe manualmente (sección 3) |

Ver la sección 5 para el detalle de cómo se llega a cada veredicto y cómo
reproducirlo a mano.
