# MISIÓN 3 — Auditoría científica, capacidad de capital y rentabilidad ejecutable

Datos: releases tape-2026-10-22..30 (sha256 registrados), `prematch.json` (3,759 partidos), `mech.json` (9,639 ventanas), `bt3.json`+`finals*.json`. Script reproducible: `tools/mission3.py`. ST0 congelado intacto (`tools/frozen_eval.py` sin tocar).

## 1. RECONSTRUCCIÓN FORENSE — verificación de cifras del informe anterior

| Cifra declarada | Reconstruida | Estado |
|---|---|---|
| 3,759 partidos con series | 3,759 | VERIFICADO |
| 1,102 con final st0 | 1,102 (mech st0+final) | VERIFICADO |
| 609 con ≥3 líneas | 1,102 tienen ≥3 líneas; **609** tienen cruce p=0.5 interpolable | CORREGIDO el denominador |
| 348 historial ≥8 | 348 (12% de 2,938 finales) | VERIFICADO — sesgo: excluye 88% (jugadores nuevos/rotación) |
| δ≈+7.09 | +7.09 IC95% [+5.44,+8.75], mediana +7.8 | VERIFICADO |
| gap 5–10: +17.9% | n=110, ROI +17.9%, WR .645 IC [.553,.729] | VERIFICADO |
| gap>10: +25.1% | n=105, ROI +25.1%, WR .686 IC [.592,.767] | VERIFICADO |
| st0 +10.4% | +11.8% sobre la misma muestra de 348 | CORREGIDO (era denominador distinto) |

Separación: 821 partidos sin final (censurados, no liquidados), 0 duplicados de partido detectados, 0 apuestas correlacionadas (1 pick/partido en B). Toda variable (escalera, historial de jugador) es causal — verificado por construcción del extractor.

## 2. CAPACIDAD ECONÓMICA — la misión prioritaria

### 2.1 Auditoría de liquidez (clasificación exigida)

| Concepto | Estado |
|---|---|
| Límites máximos por selección | **DESCONOCIDO** — el tape no registra importes |
| Importes aceptados/rechazados/parciales | **DESCONOCIDO** — nunca hemos apostado |
| Cambio de cuota durante colocación | **DESCONOCIDO** (el drift prematch −0.013 sí es VERIFICADO) |
| Suspensiones/mercado bloqueado (flag B) | VERIFICADO — existe en el feed |
| Frecuencia de actualización de líneas | VERIFICADO — la escalera cambia prematch |
| Ventana de la oportunidad | VERIFICADO — primera cuota rinde más que última (+8.0 vs +7.3) |
| Capacidad de ejecución | **ESTIMADO** — solo modelable con supuestos, sin verificación |

**Conclusión de liquidez: con los datos actuales es imposible saber cuánto dinero acepta Betwinner en Masters bo7.** La única medición real posible es empírica: lanzar apuestas mínimas reales y registrar aceptación/rechazo/parcial (execution telemetry). Todo lo demás es supuesto.

### 2.2 Curva capital→beneficio (simulación, no prueba ejecutable)

Serie empírica n=1,125 apuestas liquidadas, ritmo ~345/día, 30 días, 800 sims:

| Bankroll | Stake | Apostado/día | PnL 30d mediano | MDD95 |
|---|---|---|---|---|
| $100 | fijo 1% | $3.45 | +$661 | 18% |
| $500 | fijo 1% | $17.3 | +$3,341 | 18% |
| $1,000 | fijo 1% | $34.5 | +$6,698 | 18% |
| $5,000 | fijo 1% | $172.5 | +$33,054 | 19% |
| cualquiera | prop. 2.5%/Kelly | crece con equity | astronómico (compuesto) | 82–97% |

Interpretación honesta: con stake fijo 1% el rendimiento escala lineal (+6.6× a 30d por el ROI unitario). Con stake proporcional el compuesto de ~345 apuestas/día produce cifras absurdas que **el mercado no aceptaría**: a $5k el stake medio sería ~$125/apuesta → ~$43k/día en una liga menor de tenis de mesa. Punto donde más stake deja de mejorar el resultado ajustado a riesgo: DESCONOCIDO — depende del límite real de la casa, no medible sin ejecutar.

### 2.3 Stake: comparación de reglas

- **Fijo 1%**: controlable, MDD95 ~18-19%, escalado lineal. Recomendado como base operativa.
- **Proporcional 2.5%**: matemáticamente superior en expectativa pero MDD95 ~85% y exige liquidez creciente indefinida — irreal.
- **Kelly 0.5 con p del motor**: injustificable — la p no ordena (resolución ≈0); Kelly amplifica una probabilidad que no discrimina. RECHAZADO hasta que exista probabilidad calibrada con resolución.

## 3. HIPÓTESIS

| Hipótesis | Muestra | Resultado | Incertidumbre | Estado | Próxima prueba |
|---|---|---|---|---|---|
| A: GAP≥5 (ST1) | n=215 de 348 | ROI +21.5% vs −3.8% gap<5; no concentrado (top3 jugadores=19% pnl, 152 jugadores); cuotas idénticas (1.826=1.826) → no es efecto cuota | Russia +30%(n=128) concentra 60% muestra; 30oct: n=6 ROI+50% (ridículamente pequeña) | **PROMETEDORA** | sombra prospectiva congelada gap≥5 vs ST0, ~2 semanas |
| B: shrinkage κ | κ≈8.9 estimado EB (var_entre=46/var_dentro=408) | shrunk RMSE 21.48 vs liga 21.28 vs cruda 23.09 | la media de LIGA gana al shrinkage → la heterogeneidad es de partido, no solo jugador | **RECHAZADA** en su forma (mejor: prior=liga, no global) | re-estimar con prior por liga |
| C: residual empírico vs normal | n=1,642 | Brier .2384 vs .2380; LL .6698 vs .6688 | mejora ~0.1-0.4% — marginal | **NO DEMOSTRADA** | ya descartable, la normal con desplazamiento basta |
| D: línea −4..+2 | n=1,535 dentro / 292 fuera | ROI +15.9% vs +15.5% | sin diferencia — la restricción no añade nada | **RECHAZADA** | — |
| E: ligas | δ27-29 vs δ30 (n pequeñas) | Russia +10.1→+4.5, Belarus +6.5→+21.1, Poland +3.9→+32.9, Pol.Women +0.2→−1.7, SuperLeague −1.5 | δ inestable entre periodos con n minúsculas | **DATOS INSUFICIENTES** | δ diaria congelada como métrica de monitoreo |
| F: patrón U | por día | ext positivo 8/9 días (+14..+83%), quiet inestable (−22..+77%), n pequeñas | inconsistente día a día | **NO DEMOSTRADA** | registro continuo en sombra, sin operar |

## 4. MOTOR DE INTELIGENCIA DE MERCADO — diseño (sin implementar aún)

Jerarquía exigida ya soportada por los módulos existentes: `prematch_extract` (market reconstruction: escalera, centro implícito, drift) → historial causal de jugadores (player intelligence, con prior por liga, κ≈9) → residuos de predicción (match distribution, normal con δ propio) → `gap` (edge detector, umbral congelable) → EV=bucket density vs cuota (probability engine, poca resolución — limitación conocida) → escenarios §2 (capital engine) → MDD/concentración (risk) → vacío real: **execution telemetry no existe** (nunca se ha apostado).

## 5. PROTOCOLO DE VALIDACIÓN EN SOMBRA (preparado, no lanzado sin aprobación)

- ST0: `frozen_eval.py` intacto.
- ST1: candidato congelable: gap≥5, mismas reglas (argmax-EV floor 5%, 1 bet/partido).
- ST2: línea de investigación (shrinkage prior-liga, U-pattern) — solo registro, sin apuestas.
- Registro por partido: escalera, centro, gap, p, EV, decisión+motivo, cierre, final, pnl.
- Tamaño muestral (calculado, no fijado a 300): para detectar ROI ≥5pp vs ST0 con σ≈1 y potencia 80% → n≈400 apuestas (~6 días de tape).

## 6. DECISIÓN FINAL (respuestas directas)

1. **¿Ventaja estadística respaldada?** Sí: δ≈+7 IC [5.4,8.7] es un error estructural medible; GAP discrimina monótonamente. Replicado con 2 métodos y parcialmente prospectivo.
2. **¿Ventaja económica tras cuotas?** Sí a nivel unitario (+12–20% ROI en 1,200+ apuestas liquidadas reales del histórico). El CLV +5–10% confirma que no es artefacto del cierre.
3. **¿Cuánto dinero acepta el mercado?** DESCONOCIDO — es el agujero central. Ninguna cifra de capacidad es verificable sin telemetría de ejecución real.
4. **Beneficio razonable a modelar:** con stake fijo 1% y el ritmo observado: ~+6.6× bankroll/30d como escenario estadístico — NO como promesa: depende de límites desconocidos y de que δ persista.
5. **Mayor obstáculo:** (a) capacidad de ejecución desconocida — no sabemos si aceptan $5 o $500 por selección; (b) muestra prospectiva aún corta (63 partidos); (c) inestabilidad de δ por liga/día.
6. **Siguiente prueba de mayor valor:** lanzar ST0+ST1 en sombra con telemetría completa sobre los próximos releases (~6 días → n≈400) **y** una micro-prueba de ejecución real (apuestas mínimas $0.5–1) para medir el punto C-D de capacidad — es la única forma de convertir ESTIMADO en VERIFICADO.
