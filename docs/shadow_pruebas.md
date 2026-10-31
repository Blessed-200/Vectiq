# Shadow Engine — resumen de pruebas ejecutadas

Qué se probó, con qué datos y qué salió. Todos los números reproducibles con
los scripts de `tools/` y `tests/`.

## Datos utilizados

| Fuente | Contenido |
|---|---|
| Releases `tape-2026-10-22` … `tape-2026-10-30` | Shards JSONL.zst de ticks Betwinner (LiveFeed/Get1x2_VZip), 9 días |
| API oficial de resultados (`BetwinnerClient.results`) | Finales de partidos para liquidar censurados; 2.332 finales, 0 desacuerdos con el tape |
| `LiveBet-update/Open/UpdateCoupon` (capturas del usuario, replay propio) | maxBet/minBet/Block/changeCf por selección, sin autenticación |

## Pruebas y resultados

| Prueba | Datos | Resultado |
|---|---|---|
| Regresión ST0 (shadow ≡ frozen_eval) | shard sintético + shard real | ≥90 % mismos gids, (línea, cuota) idénticos en los comunes; test automático en `tests/test_shadow.py` |
| Store append-only | unitario | 6/6 tests: dedup, causalidad del historial de jugadores, centro implícito con empate de devig, sin decisiones duplicadas al reprocesar |
| Backtest retrospectivo ST0 | tape 22–30 oct | n=3.258 decisiones / 2.526 liquidadas, WR 52,4 %, ROI +12,1 %, CLV +8,8 % (78,7 % bate cierre), MDD 24,7u, racha máx 10 |
| Backtest retrospectivo ST1 (ST0+gap≥5) | mismo tape | n=219/154, WR 59,1 %, ROI +25,1 %, CLV +9,4 %, MDD 6,6u, racha 6 |
| **Prueba ciega (prospectiva, reglas congeladas)** | tape-2026-10-30 parcial — datos jamás vistos al congelar | ST0: 63 decisiones / 54 liquidadas, WR 55,6 %, ROI +19,5 %, CLV +7,0 % |
| Capacidad real UpdateCoupon | Masters España en vivo (Under 110.5 @1.848) | `maxBet=62.500`, `minBet=10`, `Block=false`, `changeCf=false`; línea suspendida → `maxBet=230`, `Block=true` |
| Liquidación dual tape + API | 2.332 finales cruzados | 0 desacuerdos |

## Pendiente honesto

- IC95 % de la prueba ciega: [−8,8 %, +47,6 %] — 54 liquidadas no bastan para
  adoptar; la validación corre 30–45 días en vivo.
- `Currency: 278` del maxBet: unidad sin mapear (experiencia del usuario:
  límites reales $300–500 prematch, $2–3k en vivo).
- El retrospectivo 22–30 oct no es prueba ciega: esos datos se usaron al
  construir las reglas.
