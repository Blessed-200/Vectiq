# Tablas de descubrimiento

Todas las cuotas son decimales. Los aciertos, ROI y diferencias llevan el IC95 percentil entre corchetes. Los intervalos se obtuvieron remuestreando los días UTC completos con la semilla 20261122, conservando juntos los registros de cada jornada. Un guion indica que no hay muestra evaluable.

## Centro de mercado y total de puntos

| Etapa | Partidos con centro | Partidos con centro y final | Días | Centro menos total, puntos [IC95] | Total inferior al centro, % [IC95] |
| --- | --- | --- | --- | --- | --- |
| Antes del primer punto (incluye vivo 0–0) | 7087 | 5675 | 19 | 5,76 [4,99 a 6,48] | 59,26 [57,90 a 60,65] |
| Tras set 1 | 9744 | 8364 | 19 | 5,46 [4,89 a 6,01] | 59,11 [58,00 a 60,25] |
| Tablero prepartido estricto, por ID nativo | 539 | 0 | 0 | — | — |
| Tablero prepartido estricto, enlace inferido | 539 | 5 | 4 | 19,74 [-0,95 a 30,79] | 80,00 [0,00 a 100,00] |

## Entradas guardadas en los libros que alimentan el panel

Resultados a una unidad por entrada, valoradas a su cuota publicada y con desenlace reconstruido por cinta. En la columna ponderada se usa el stake del libro de papel. Las dos caídas se definen por separado en el informe. ST5 después del segundo set es un subconjunto de ST5.

| Libro | Aperturas | Finales G/P | Días activos | Cuota | Acierto % | ROI plano % | ROI ponderado % | Caída simulada a 10 min % | Racha de pérdidas |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ST0 / PRE | 0 | 0 | 0 | — | — | — | — | — | — |
| ST3 / SET1 | 731 | 623 | 7 | 2,182 | 56,18 [53,48 a 59,53] | 22,68 [16,74 a 29,96] | 19,57 [15,69 a 26,28] | 9,92 | 8 |
| ST5 | 165 | 148 | 3 | 1,816 | 64,19 [51,92 a 75,93] | 16,89 [-6,00 a 38,95] | 17,11 [-6,41 a 37,82] | 7,05 | 4 |
| ST5 tras set 2 | 160 | 143 | 3 | 1,815 | 62,94 [50,98 a 75,47] | 14,47 [-7,68 a 38,16] | 14,48 [-8,07 a 36,99] | 7,79 | 4 |
| Conjunto del panel | 896 | 771 | 7 | 2,112 | 57,72 [54,35 a 60,90] | 21,57 [14,91 a 28,95] | 19,32 [15,71 a 24,89] | 9,32 | 7 |

## Precio publicado y registro del aviso

La columna «Con final G/P» exige la misma entrada, precio, línea, etapa y partido en la cinta, registro con identificador de mensaje y marcador final conciliable. Una marca de Telegram no acredita que la casa aceptara un boleto.

| Rama | Pizarra y aviso | Con final G/P | Días | Acierto % | ROI plano % |
| --- | --- | --- | --- | --- | --- |
| ST0 | 0 | 0 | 0 | — | — |
| ST3 | 675 | 572 | 7 | 55,94 [53,08 a 59,36] | 22,21 [15,79 a 29,74] |
| ST5 | 107 | 92 | 3 | 67,39 [41,18 a 81,08] | 22,78 [-26,23 a 48,52] |
| Conjunto | 782 | 664 | 7 | 57,53 [53,54 a 60,66] | 22,29 [14,52 a 29,11] |

## Comparación sobre oportunidades observadas en toda la cinta

Una unidad por señal de cada motor. El control toma el Under sin exigir señal y se pondera con los estratos de la estrategia. En cada remuestreo se vuelven a calcular las ponderaciones. Una ocasión de control corresponde a partido, motor cuando corresponda, etapa, liga, línea y centésima de cuota. Un partido puede aparecer en varias celdas.

### ROI plano

| Rama | Señales n | Ocasiones control n | Partidos control | Días de señal | ROI plano señal % | ROI plano Under sin señal exigida % | Diferencia pareada, pp |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ST0, rama identificable | 2479 | 3486 | 3142 | 19 | 16,34 [12,20 a 20,84] | 15,79 [12,03 a 19,87] | 0,55 [-1,83 a 3,05] |
| ST3 | 1929 | 10143 | 4152 | 19 | 23,97 [18,12 a 29,87] | 17,10 [12,21 a 22,36] | 6,88 [3,18 a 10,31] |
| ST5 | 154 | 790 | 512 | 3 | 15,87 [-2,70 a 37,18] | 7,01 [-6,12 a 24,35] | 8,87 [3,42 a 14,49] |
| ST5 tras set 2 | 149 | 768 | 498 | 3 | 13,53 [-4,25 a 36,39] | 5,04 [-7,74 a 23,33] | 8,48 [3,35 a 14,15] |
| Conjunto contrafactual | 4562 | 14419 | 5351 | 19 | 19,55 [15,27 a 24,09] | 16,05 [12,33 a 20,09] | 3,50 [1,33 a 5,61] |

### Acierto

| Rama | Señales n | Ocasiones control n | Partidos control | Días de señal | Acierto señal % | Acierto Under sin señal exigida % | Diferencia pareada, pp |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ST0, rama identificable | 2479 | 3486 | 3142 | 19 | 52,32 [50,46 a 54,34] | 52,07 [50,39 a 53,91] | 0,25 [-0,83 a 1,37] |
| ST3 | 1929 | 10143 | 4152 | 19 | 56,92 [54,24 a 59,64] | 53,74 [51,53 a 56,17] | 3,18 [1,47 a 4,76] |
| ST5 | 154 | 790 | 512 | 3 | 63,64 [53,70 a 75,00] | 58,89 [51,79 a 68,09] | 4,75 [1,89 a 7,81] |
| ST5 tras set 2 | 149 | 768 | 498 | 3 | 62,42 [52,83 a 74,55] | 57,88 [50,88 a 67,51] | 4,54 [1,81 a 7,62] |
| Conjunto contrafactual | 4562 | 14419 | 5351 | 19 | 54,65 [52,68 a 56,72] | 53,01 [51,32 a 54,83] | 1,64 [0,63 a 2,59] |

## Fuentes y cobertura de los finales

| Motivo de exclusión | Partidos Masters |
| --- | --- |
| conflicting_raw_key_or_parser_error | 1 |
| conflicting_terminal_scores | 1 |
| no_legal_observed_terminal | 2450 |
| start_outside_discovery | 14 |
| unverified_best_of_7 | 1 |

| Rama | Primeras candidatas de pizarra | Pasan precio y marcador antes del riesgo | Con final admisible |
| --- | --- | --- | --- |
| ST0 | 7619 | 3043 | 2479 |
| ST3 | 3305 | 2299 | 1929 |
| ST5 | 343 | 171 | 154 |

Los ficheros JSON incluyen cantidades por liga, días sin final, denominadores del bootstrap, solapamiento de señales y casos excluidos. El IC de casos completos no cuantifica el sesgo de perder partidos sin final.
