# Reproducir la auditoría

Usar una copia Git con historial completo de esta rama y CPython 3.12. El entorno de referencia usa 3.12.14, SQLite 3.53.1, NumPy 2.3.5, zstandard 0.25.0 y el lector JSON estándar. La ejecución usa la semilla 20261122 y 10.000 remuestreos. Los precios en los archivos de apertura conservan su precisión original.

```bash
git clone --branch research/auditoria-under-20261122 https://github.com/Blessed-200/Vectiq.git
cd Vectiq
python3.12 -m venv /tmp/vectiq-phase0-venv
/tmp/vectiq-phase0-venv/bin/python -m pip install numpy==2.3.5 zstandard==0.25.0
/tmp/vectiq-phase0-venv/bin/python -m unittest discover -s research -p 'test_*.py' -v
/tmp/vectiq-phase0-venv/bin/python research/reproduce.py --work /tmp/vectiq-reproduccion --cache /tmp/vectiq-discovery-cache --jobs 4
```

El último comando descarga sólo los 558 fragmentos de descubrimiento (1.117.530.862 bytes comprimidos) y verifica que las huellas coincidan con el manifiesto publicado. Una segunda ejecución reutiliza los archivos verificados. Toda la cinta de confirmación y de prueba queda fuera del descargador. Los límites horarios se comprueban también dentro de cada archivo de descubrimiento. La orden recalcula los JSON de métricas, el CSV de finales usado por los cruces, el diagnóstico del prepartido estricto, su correspondencia de identificadores y las tablas. Comprueba los hashes de los agregados contra los publicados en Git. En otras versiones de bibliotecas o SQLite puede variar la serialización o algún último decimal.

Las pequeñas proyecciones de `research/inputs` contienen aperturas y marcadores del bot durante descubrimiento. Se extrajeron mediante una consulta SQL con límite temporal y lista explícita de columnas. Se conservan porque la publicación original `ledger` se reemplaza durante el funcionamiento del bot. Sus identificadores de assets, hashes y columnas constan en `origins.json` y `marker_origin.json`. Las tablas de usuarios, resultados acumulados del ledger, cuotas de cierre e identificadores de personas de Telegram quedan fuera de las proyecciones.

Los comandos `python research/sources.py index`, `python research/opening_sources.py capture` y `python research/entry_trace.py capture-markers` se usaron para capturar los originales. Volver a ejecutarlos sería una captura nueva de archivos mutables. Para reproducir la evidencia publicada se usa el manifiesto fijado en Git, como hace `reproduce.py`. El catálogo se obtiene con `python research/sources.py catalog`. La consulta de disponibilidad de viejos artefactos, que puede cambiar en GitHub, se conserva en `results/pages_probe.json`.

El informe `00_auditoria.md` interpreta los agregados. `00_tablas.md` se puede regenerar exactamente con `python research/report_tables.py`. Los porcentajes de riesgo del panel son simulados. El registro de acceso distingue la transferencia opaca de un SQLite acumulado de la lectura temporal permitida. Las pruebas de reloj, identificación del partido y control de dominios se ejecutan sólo sobre ejemplos sintéticos o metadatos.
