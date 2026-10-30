# Congelación de versiones — Shadow Engine ST0/ST1

Fecha de congelación: 2026-10-30. Cualquier cambio futuro crea versión nueva y requiere autorización expresa del usuario.

## ST0 — control
- Fuente canónica: `tools/frozen_eval.py` sha256 `8a4cb08ed5db309b4dc6026e755c33c389642a7d59227e6ae67b9036d4305863`
- Regla: Masters BO7, mercado total (G17), UNDER, solo st0 (prematch, 60s–4h antes), primera ventana elegible, 1 apuesta/partido, argmax EV con suelo EV≥5%, sin techo, sin filtro de línea, sin calibradores. p_under = EmpBook(4) causal.
- Implementación en sombra: `shadow/engine.py` (adaptador — frozen_eval no es importable). Regresión verificada: mismas decisiones en `tests/test_shadow.py::test_st0_regression_vs_frozen_eval`.

## ST1 — candidata
- ST0 + filtro `gap >= 5`, gap = centro_implícito − media_causal_totales_jugadores (≥8 partidos previos cada uno). Sin ningún otro cambio.

## Hashes de módulos
- `shadow/engine.py` sha256 `fcbd74478fa15612d2eea6d65eb7a73f15753d87539af8902c8f7afc2b144a08`
- `shadow/store.py` sha256 `aa308b8c8344b5c613b480a107e8dda1d14c7ace5a4a4850cae35aa24e782412`

## Almacenamiento
- `shadow/state/*.jsonl` append-only, esquema `shadow-1`, release `shadow-ledger`.
- Liquidación: primaria = final en tape; respaldo = API de resultados (champId+día, verificada 0 discrepancias en 2,332 finales).
