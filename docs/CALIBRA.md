# CALIBRA (sombra)

Capa de aprendizaje que vigila a ANCLA (prepartido del modelo Telegram viejo + palizas ST3). **No toca apuestas ni alertas.**

- **Sensor**: error de la casa (`línea central − total real`) por etapa (0, 1, 2 sets jugados) sobre todos los partidos Masters.
  Referencia = primeros 3 días con ≥ 100 partidos por etapa; CUSUM de un solo lado (k=0.5, h=5) que salta solo si el error se acerca a 0.
  Línea diaria en el canal. Tras una alarma sigue activa hasta que la evidencia se disipa.
- **Creencia + tamaño (sombra)**: posterior Normal-Normal del ROI por (estrategia, liga) con las apuestas liquidadas de cada libro; Kelly 1/4 sobre el
  límite inferior, 0.2 % de exploración, 0 si la celda parece muerta, mitad con alarma, techo 1 %. Se guarda junto al tamaño real (`calibra.sqlite`, tabla `shadow`).
- **Explorador**: prueba cada celda (etapa × liga × lado × peldaño) día a día (elige con días anteriores, evalúa en el siguiente) con umbral Bonferroni. Solo informa.
- **Comparación ciega**: `python -m vectiq.signals.calibra <carpeta>` (crecimiento, caída máxima y capital desplegado de ANCLA vs CALIBRA). No se envía a Telegram.

Límite conocido: la referencia se aprende de sus propios 3 primeros días (error ≈ ±0.6 puntos) y, tras una alarma, el freno tarda en soltarse (el CUSUM baja 0.5 por día).
