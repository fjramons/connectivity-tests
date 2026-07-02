---
name: generate-connectivity-spec
description: Regenera la especificacion de pruebas de conectividad Domain 2 <-> Domain 3 (EC.3) a partir de los CSV de inputs/, y los manifiestos/scripts derivados. Usar cuando cambien los CSV de inputs/ o el YAML del spec se haya editado a mano.
---
# Regenerar la especificación de pruebas de conectividad

Este proyecto valida la conectividad entre Domain 2 y Domain 3 (EC.3) a partir
de dos CSV en `inputs/` (`... Clients at EC.3.csv` y `... Servers at EC.3.csv`).
Todo el flujo de regeneración se ejecuta en el PC de desarrollo con `uv`
(`uv sync` una vez, para tener PyYAML disponible).

## Cuándo usar esta skill

- Han cambiado los CSV de `inputs/` (nueva versión de la matriz de firewall).
- Se ha editado a mano `inputs/connectivity-test-spec.yaml` (por ejemplo, para
  corregir una entrada de `unresolved`) y hace falta resincronizar el JSON.
- Han cambiado los destinos de `domain2_to_domain3` y hacen falta regenerar los
  manifiestos de servidor o el script autocontenido para el jumphost.

## Pasos

1. **Regenerar el spec desde los CSV** (caso normal, cuando cambian los CSV):
   ```bash
   uv run src/generate_test_spec.py
   ```
   Revisa el resumen impreso: número de casos `domain3_to_domain2` (automatizables)
   vs. `domain2_to_domain3` (manuales), y cuántos quedaron en `unresolved` (deberían
   ser 0 salvo que el CSV tenga datos incompletos).

2. **Si en vez de eso se editó el YAML a mano**, no repitas el paso 1 (perderías la
   edición): resincroniza solo el JSON:
   ```bash
   uv run src/generate_test_spec.py --from-yaml
   ```

3. **Regenerar los manifiestos de servidor** (uno por cada destino único
   `domain2_to_domain3`, para desplegar en Domain 3 y que Domain 2 los pruebe):
   ```bash
   uv run src/generate_server_manifests.py
   ```

4. **Regenerar el script autocontenido para el jumphost/VM** (pruebas
   `domain3_to_domain2` con `source.type == "VM"`):
   ```bash
   uv run src/generate_standalone_script.py
   ```

5. Recuerda que estos artefactos (`inputs/connectivity-test-spec.*`, `manifests/`,
   `standalone/`) deben trasladarse manualmente al PC de laboratorio vía OneDrive
   Web — no hay forma automatizada de hacerlo desde aquí.

Ver `README.md` para el detalle de cada backend (K8s vs. VM/jumphost) y cómo se
interpretan los veredictos de las pruebas.
