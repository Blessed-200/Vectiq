# Criterio de paso a dinero real (fijado el 2026-10-27 23:07 UTC, antes de ver datos nuevos)

Corte: solo cuentan apuestas con ts_ms >= 1793142468000. Lo anterior sirvió para descubrir; no sirve para aprobar.

## Hipótesis registradas (las únicas que se evalúan)
- H_A  Masters Under prepartido (motor actual, 1 posición por partido).
- H_B  Under en Bo5 (Setka/Liga Pro) tras el set 1, tabla shadow.sqlite, sets == 1 (49 apuestas previas, +35 %: descubrimiento, no prueba).
- H_C  Polonia+Belarús vs Rusia en Masters: diferencia de ROI, una sola prueba bilateral. Gemini lo propuso con 13 horas de datos; la cinta antigua (n=514 Rusia) NO lo apoya.

## Reglas (métrica = ROI, no el acierto: el acierto depende de la cuota)
- H_A: >= 400 liquidadas posteriores al corte, >= 5 días distintos, IC95 % bootstrap por partido con límite inferior > 0.
- H_B: >= 200 liquidadas posteriores al corte, mismos criterios.
- Drawdown simulado a stake real < 15 %. Nada de filtros nuevos sin repetir la prueba en datos posteriores a su creación.
- Pasar a real = piloto con stake mínimo (0.30-1 USD) en 30 tickets para medir aceptación, cuota realmente obtenida (deslizamiento) y límites. Subir stake solo si cuota real ~ cuota capturada.
- Parar: ROI < 0 con n >= 300 en la hipótesis, o SPRT apagado.


## Addendum 2026-10-29 — settlement source
Finals now come from the results service (`src='results_api'`, verified 0/2,332 disagreements against the tape).
`observed` and `results_api` count as real outcomes; `imputed` (service had nothing within 90 min), `partial` and `void` are
reported separately and never counted toward the pre-registered sample sizes unless the main result is also shown without them.
