# ST2-core (sombra) + registro de observación

Estado: **solo papel**. Sin dinero real hasta tener ~1.000 apuestas forward y decisión explícita.

## Regla congelada (2026-11-01)
Masters (no China / Super League), best-of-7, en vivo, **exactamente 1 set completado**; la escalera de totales
necesita ≥ 2 líneas con over y under; centro = línea interpolada donde el Under devigado vale 0,5; línea = la de la
escalera más cercana al centro; **Under si su cuota < 1,85**; primer snapshot que califica por partido; una posición
plana (~$1) por partido. Código: `src/vectiq/signals/st2.py`. Ledger: `st2.sqlite` (release `ledger`).

## Por qué esta y no otra
Backtest en ~3,8k partidos Masters: ROI +7,1 % ±2,8 (IC95 agrupado por partido), WR 59,3 %, ~375 apuestas/día.
El mecanismo: tras el set 1 el mercado asume ~90 puntos restantes casi constantes; la realidad baja cuando el set 1
fue de paliza (quedan menos sets). Los demás bots (Telegram, Manus, ST0, ST1) apuestan al mismo Under del mismo
partido; combinarlos no diversifica. ST2+ (recalibración), la mezcla de 2 estados y la tabla empírica dieron mejoras
de Brier no significativas.

## Registro de observación (no apuesta)
Tabla `obs` en `st2.sqlite`: primer snapshot de cada partido en k=1 y k=2 con la escalera completa, marcadores de
set y marcador del set en juego. Permite calcular offline, sobre datos forward, ST2+, la fórmula de mezcla de 2
estados y la tabla empírica, y compararlas con ST2-core sobre los mismos partidos.

## Criterio de decisión
~1.000 apuestas liquidadas. Dinero real solo si el IC95 del ROI forward excluye 0 y el drawdown a 1 % de stake es
< 15 %. Gestión de riesgo al pasar a dinero: Kelly fraccional, ×0,5 a −15 % de drawdown, tope de riesgo abierto 35 %,
parada si el ROI de las últimas 150 apuestas < −8 %. Capacidad (maxBet de Betwinner) sin verificar.
