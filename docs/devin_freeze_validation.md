# Validación prospectiva — Estrategia B congelada vs Motor A

**Estado: provisional** — el release post-congelación `tape-2026-10-30` contiene solo las horas 00–03 UTC (el día sigue grabándose). Todo lo que sigue es sobre una ventana parcial de ~4 h; NO es un día completo.

## 1. Especificación congelada (sin cambios tras observar resultados)

- Masters BO7 (`league` contiene "masters", `best_of==7`), mercado G17 total, lado UNDER.
- Solo st0: primer tick prepartido elegible (no live, 0 sets, inicio en [60 s, 4 h]).
- Una apuesta por partido.
- Línea: `argmax` del EV del modelo sobre la escalera disponible en ese tick, exigiendo EV ≥ 5 %. Sin techo de EV, sin filtro de línea, sin calibradores, sin modificación del motor (mismo EmpBook causal: solo aprende de partidos ya finalizados).
- Ambigüedad resuelta: la regla del motor era «mejor EV dentro de [5 %, 15 %]»; la congelación elimina el techo, conservando suelo y argmax — la única lectura determinista reconstruible sin inventar regla.

## 2. Auditoría de código (reconstrucción)

`tools/frozen_eval.py` (nuevo, aislado): replay del tape, registro de **todas** las oportunidades st0 con escalera completa, p del modelo, EV por línea, decisión o motivo de rechazo. Verificado:

| Punto | Resultado |
|---|---|
| IDs de partido | `game_id` entero del feed; apuestas y finales se cruzan solo por gid; sin colisiones observadas |
| Línea↔cuota↔resultado | Under gana iff `total_final < línea`; todas las líneas apostadas son .5 → **push imposible** (verificado: 0 líneas enteras en 2 234 apuestas históricas; solo 10/26 977 basura 18.0/19.0 en escaleras) |
| Info futura en st0 | Selección usa solo cuotas del tick + libro de partidos finalizados; CLV se mide post-apuesta pero no alimenta decisiones |
| Censurados | Liquidados por API oficial (`/result/web/api/v3/games`); quedan void excluidos, nunca contados como pérdida — OJO: históricamente los censurados tenían WR 37.7 % (< media), excluirlos sesga ROI al alza si no se cubren por API |
| Duplicados/correlación | B = 1/partido por construcción (solo primera ventana st0); A = 1 por (stage, mercado) |
| Devig | `p_impl = (1/U)/((1/U)+(1/O))` — cuota-parte normalizada, correcta para mercado binario |
| ROI | unitario = media de (cuota−1)/−1; ponderado = Σpnl/Σturnover con fracción Kelly registrada por apuesta |
| CLV | última cuota del MISMO (market, selección, línea) antes del cierre del feed |
| Reproducción | cifras históricas re-computadas desde tape, no copiadas de informes |

**Fallo encontrado en auditoría previa y corregido**: el bootstrap por clusters descartaba partidos parcialmente elegibles (ya corregido en devin_audit2.py: n 231→268, ROI +6.7 %→+4.7 %).

## 3. Datos y hashes

- Evaluación: release `tape-2026-10-30` (5 shards, horas 00–03 UTC, publicado 00:21 UTC; sha256 en `/tmp/tape30_sha256.txt`, ejemplo `f562cba1…` shard 00).
- Entrenamiento causal del libro: releases `tape-2026-10-22..29` (mismos que el backtest original).
- Finales censurados: 75 partidos recuperados por API (`finals_oct30.json`); 9 apuestas B y 45 A quedaron void sin final publicado aún.

## 4. Comparación A vs B (misma ventana, mismos precios)

| Métrica | A motor original (Masters-Under st0) | B congelada |
|---|---|---|
| Partidos con apuesta | 34 | 63 |
| Liquidadas / void | 28 / 6 | 54 / 9 |
| WR | 60.7 % | 55.6 % |
| ROI unitario | **+29.0 %** | **+19.5 %** |
| CLV medio | +10.2 % | +7.0 % |
| % bate cierre | 74 % | 66 % |
| Cuota media | 2.02 | 2.15 |
| Líneas elegidas | 105.5–110.5 | 101.5–106.5 |
| IC95% ROI (cluster partido) | — | [−8.8, +47.6], P(>0)=90 % |
| MDD (unidades) | — | 5 |
| Beneficio neto unitario | +8.1 u sobre 28 | +10.5 u sobre 54 |

Lectura honesta: **A es mejor por apuesta** (su cap EV∈[5,15 %] filtra hacia líneas de cuota ~2.0 con más WR), pero **B captura 29 partidos que A rechazaba** y rinde más en euros absolutos en esta ventana. La regla argmax-EV de B elige líneas bajas (101.5–106.5) con cuota media 2.15 — consistente con el mecanismo (edge mayor en líneas bajas).

## 5. Métricas B completas

- Por liga: Russia +60.8 % (n=12), Belarus +19.4 % (18), Poland +16.8 % (11), Spain +8.0 % (4), Poland Women **−11.6 %** (5), Argentina **−46.5 %** (4). Negativos reportados, no eliminados.
- Oportunidades: 64 registradas; 1 rechazada (`sin_escalera_over_under`); registro completo en `frozenB.json` (oportunidades + apuestas con p, EV, línea, decisión).
- ROI ponderado stake (Kelly 0.5 shrink 0.5 cap 1 %): +19.5 %, pnl +0.105 u.
- Sensibilidad/distribución: ventana 00–03 UTC concentra ligas europeas Masters — NO extrapolable a día completo.

## 6. Decisión fundamentada

**Conservar la candidata en validación** — no adoptarla todavía:

- B es rentable en la ventana (+19.5 %, P(ROI>0)=90 % por cluster) y cubre ~2× partidos que A, pero la muestra es parcial y el IC es ancho.
- La regla argmax-EV ya no replica la selección histórica de A (elige líneas más bajas): ambas funcionan por el mismo mecanismo (escalera desplazada), y la comparación limpia necesita días completos.
- Próxima acción: cuando `tape-2026-10-30` esté completo y salga `tape-2026-10-31`, re-ejecutar `frozen_eval.py` + `backtest.py` sin tocar reglas. Script y registro ya producidos; la comparación se re-corre tal cual.
