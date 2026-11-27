# Registro de carteras VQC (búsqueda exhaustiva en Colab, 26 nov 2026)

Constancia permanente de lo que recomendó la búsqueda con GPU y de lo que decidió el dueño.
Nomenclatura: **estrategia** = una sola (ST3, ST5, ST0, R1…). **Cartera** = un conjunto de estrategias.

## Cómo se obtuvo
- Se probaron las 536.870.911 combinaciones posibles de 29 estrategias (ST3, ST5, ST0 y 26 reglas minadas), una por una.
- Datos: cinta real, 15 días de búsqueda (hasta el 24/11) + 24 h de prueba ciega posterior que la búsqueda no vio.
- Con tope de 2 unidades por partido y modo prudente (reglas minadas rebajadas 32 % de ROI y 3 puntos por apuesta; ST: 3 puntos).
- Control de sobreajuste: correlación entre el ranking de la búsqueda y el ROI del ciego = **+0,06** (casi nula).
  Es decir: en los 15 días siempre sale algo que se ve perfecto; solo el seguimiento en sombra dirá si es real.

## Carteras 1 a 7 (mejor combinación de cada tamaño)

| Cartera | Estrategias | Calmar | Horas+ | Bloques 6 h+ | Caída (u) | Laguna (h) | Ciego: profit (u) | Ciego: ROI |
|---|---|---|---|---|---|---|---|---|
| 1 | ST3 | 18,4 | 64 % | 70 % | 11,5 | 55 | 13,1 | 14,1 % |
| 2 | ST3, R4 | 22,9 | 63 % | 72 % | 11,6 | 39 | 17,4 | 16,6 % |
| 3 | ST3, R1, R2 | 24,4 | 64 % | 78 % | 11,5 | 38 | 17,6 | 16,8 % |
| 4 | ST3, R2, R3, R13 | 27,1 | 62 % | 83 % | 14,8 | 36 | 11,7 | 7,2 % |
| 5 | ST3, R2, R3, R5, R7 | 28,5 | 64 % | 83 % | 15,4 | 30 | 3,3 | 1,8 % |
| 6 | ST3, R1, R2, R3, R7, R12 | 28,7 | 64 % | 85 % | 16,6 | 34 | 12,2 | 6,5 % |
| 7 | ST3, R1, R3, R7, R10, R12, R37 | 27,3 | 64 % | 85 % | 17,4 | 30 | 2,3 | 1,1 % |

## Finalistas con stake (caída objetivo 20 % en el peor 5 % a 30 días, banca compuesta)

| # | Estrategias (múltiplo del stake base) | Stake base | Ciego: profit / ROI / caída |
|---|---|---|---|
| 1 | ST3 ×1, R1 ×0,5, R2 ×1, R3 ×1, R7 ×0,5, R10 ×1, R12 ×1,5 | 1,28 % | 4,8 u / 2,4 % / 10,7 u |
| 2 | ST3 ×1, R1 ×1, R2 ×1, R3 ×1, R7 ×1, R12 ×1 | 1,15 % | 12,2 u / 6,5 % / 8,0 u |
| 3 | ST3 ×1, R2 ×1, R3 ×1, R7 ×0,5, R10 ×1,5, R12 ×1,5 | 1,26 % | 0,8 u / 0,4 % / 12,4 u |
| 4 | ST3 ×1, R1 ×2, R2 ×1, R3 ×1, R7 ×1, R10 ×1, R12 ×1, R37 ×1 | 1,14 % | 3,7 u / 1,7 % / 9,4 u |
| 5 | ST3 ×0,5, R1 ×1, R2 ×1, R3 ×1, R10 ×0,5, R12 ×0,5 | 2,13 % | 13,2 u / 12,9 % / 4,2 u |

## Decisión del dueño (26 nov): estrategias individuales a seguir
Se conserva la unión de las estrategias que aparecen en las carteras 1 a 7, cada una **por separado** (no por cartera):

- **En producción (sin cambios):** ST3, ST5, ST0 (la combinada del panel).
- **En sombra individual (papel, sin Telegram):** R1, R2, R3, R4, R5, R7, R10, R12, R13, R37.

**Archivadas** (de la cartera 8 hacia abajo; no se siguen, quedan solo como registro):
R0, R6, R8, R9, R11, R14, R15, R16, R17, R18, R19, R20, R21, R22, R23, R24, R25, R26, R27, R28, R29, R30, R31, R32, R33, R34, R35, R36, R38.

## Definición de cada estrategia en sombra
Ver `src/vectiq/signals/sombra.py` y `docs/SOMBRA.md`. Todas apuestan Under en el rungo más bajo; ver el texto exacto en `cand_data.json` de la investigación.
- R1: hora de Cuba ≤ 16 y jugador de bajo puntaje (rating mínimo ≤ −13,9), previa.
- R4: Rusia y rating mínimo ≤ −11,9, previa.
- R2, R3, R10, R37 también usan el rating de jugadores; R5, R7, R12, R13 usan el modelo Markov y la forma del mercado.

## Lectura honesta
El ciego fue de solo 24 h. Ninguna cartera está demostrada. Criterio de revisión: 7 días de sombra individual; se descarta la estrategia que no supere su ROI de confirmación rebajado a la mitad o que quede negativa con volumen suficiente.
