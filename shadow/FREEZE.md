# FREEZE — Shadow Engine ST0/ST1

Registro de versiones congeladas (sha256). Toda modificación a estos archivos
debe anotarse aquí; ST0 no puede cambiar su lógica sin nueva congelación.

## Componentes

- `tools/frozen_eval.py` `8a4cb08ed5db309b4dc6026e755c33c389642a7d59227e6ae67b9036d4305863`
  — evaluador canónico ST0 (fuente de verdad de la regla, NO se modifica)
- `shadow/engine.py` `35e231e2e300b5bbe6b7685f05b5a4cfc9ec746150b100ae720aa13694deb453`
  — engine compartido tape/live: `_on_state(state, t_ms)`, ST0 idéntico a
  frozen_eval (regresión testeada), ST1 = ST0 + gap≥5
- `shadow/store.py` `6a0a31b276024fa3f92d9deb5980737dee8a3924b7c5c93ff254965f8e065d5e`
  — store append-only `shadow-1` (+fichero `capacity`)
- `shadow/live.py` `9557813ede035206d805e85644b61388a9e4105d68840ff7e52fd148fb6799c1`
  — loop en vivo LineFeed/Get1x2_VZip 20s + probe de capacidad por BET
- `shadow/capacity.py` `ad4f41402d0f3b3b08944dd03882e61439ad55b060f70dd3534d120807a56584`
  — probe UpdateCoupon (maxBet/minBet/Block/changeCf) solo lectura
- `shadow/report.py` `a43f9aa83db07452b26a1256d7362cc59d83e3d349b0bcbba355cb8c681e6502`
  — panel ST0/ST1 + resumen de capacidad
- `shadow/results_api.py` `76aa22b8c51bfcccea03fc1fbacd7bfc1b6da79c1b514cf3bfac1aae3f119220`
  — liquidación respaldo API oficial de resultados

## Reglas congeladas

- ST0: Masters BO7, total UNDER, st0, 1 apuesta/partido, argmax-EV con suelo
  5%, sin cap, sin filtro de línea, sin calibradores.
- ST1: ST0 + gap≥5 (gap = centro implícito − media histórica jugadores, ≥8
  partidos previos cada uno, causal).
- Stake teórico 1u. Registro de TODAS las oportunidades con motivo.
- Telemetría de capacidad: probe UpdateCoupon por decisión BET (max_bet,
  min_bet, block, change_cf, coef_returned). Solo lectura.
