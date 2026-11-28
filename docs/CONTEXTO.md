# VECTIQ · Contexto vigente (actualizado 27 nov 2026)

Fuente única para cualquier chat o agente nuevo. Si algo de aquí contradice un documento viejo (`docs/devin_*`, `HIPOTESIS.md`, etc.), **manda este**. Lo histórico se conserva solo como registro.

## 1. Qué es y para quién
Operación de apuestas Masters (tenis de mesa, mejor de 7) sobre el **total del partido (Under/Over)** en Betwinner. El dueño habla español, no es técnico y quiere lenguaje llano, honestidad y decisiones basadas en matemática. Tres personas usan el mismo sistema con cuentas separadas (el dueño, su esposa y su cuñado); la cuota es la misma. El sistema graba la cinta completa de la casa, calcula señales, las manda por Telegram y publica un panel en GitHub Pages.

## 2. En producción (no tocar sin pruebas)
| Estrategia | Qué hace | Stake |
|---|---|---|
| **ST3** | Under tras el set 1, rungo bajo, modelo Markov | 0,7 % de la banca por alerta (antes 1,0 %) |
| **ST5** | Under tras 2 sets completos, valor 15–30 % de EV, aparece ~78 s después del set | 1,0 % |
| **ST0** | Prepartido, Under | 0,65 % (antes 0,45 %) |

- **Desde el 28/11** el stake base es ST3 0,7 % · ST5 1,0 % · ST0 0,65 % (estudio a igual riesgo; ver «Estudio de pesos»). Variables: `VECTIQ_STAKE_PCT`, `VECTIQ_ST3_REL` (0,7), `VECTIQ_ST0_PCT` (0,0065).
- **Freno de cartera** (`signals/brake.py`, `VECTIQ_BOOK_BRAKE=0` lo apaga): un solo multiplicador 0,25–1 para las tres estrategias y todos los usuarios. Baja si el ROI ponderado reciente (vida media 120 h, prior 800 apuestas al +10 %) cae por debajo del +10 % esperado, y ×0,5 si la caída de la cartera llega al 20 % (se libera al 10 %). Antes los frenos graduales (evidencia/caída/vigilante CUSUM) no actuaban sobre el stake real en modo % de banca; solo los duros (caída 30 %, parada del día 15 %, ROI de las últimas 150 < −8 %). Recalcula cada 15 min y avisa en Telegram al activarse/liberarse.
- Stake dinámico: se recalcula cada hora o al moverse ±5 % la banca; mín. $1, máx. $3.000 (`notify/bankroll.py`). Frenos por caída en `signals/st3.py`/`collector.py`.
- Alertas por privado; el canal es panel (botones, resumen 6 AM Cuba). Los botones «aceptada / cuota menor / no llegué» **no son fiables** para saber ejecutabilidad.
- Día de apuestas en Cuba = 6 AM a 6 AM.

## 3. Sombra individual (desde 27/11 09:50 UTC)
Diez reglas minadas, en papel, **sin Telegram**, cada una por separado, con página propia `sombra.html`: **R1, R2, R3, R4, R5, R7, R10, R12, R13, R37** (`signals/sombra.py`, `docs/SOMBRA.md`). Se apaga con `VECTIQ_SOMBRA=0`.
- Archivadas (no se siguen): las otras 29 reglas (lista en `docs/CARTERAS_VQC.md`).
- **Decisión del dueño: 7 días de sombra sin cambios, revisión el 04/12.** Descartar la que quede negativa con volumen o por debajo de la mitad de su ROI de confirmación. Ninguna cartera está demostrada.
- R1, R2, R3, R4, R10 y R37 usan el rating de jugadores `ps_*` (propio, NO el ridge de `players.py`); R5, R7, R12 y R13 usan el modelo Markov y la forma de la escalera. R5, R7, R37 también apuestan Over a veces.
- Pendiente acordado: variante **R5b** (R5 solo en la línea más baja) como hipótesis a probar hacia adelante.

## 4. Qué se sabe de los datos (honestidad estadística)
- Cinta desde el **22/10/2026**. Las reglas R se **descubrieron** con 22/10–08/11, se confirmaron con 10–16/11 y se probaron en ciego 18–23/11; 24/11–26/11 09:36 UTC ya se usó para validar y elegir. **Todo ese tramo está contaminado.** El único ciego limpio es lo posterior al 26/11 09:36 UTC (la sombra). ST3/ST5/ST0 tampoco son ciegas en octubre.
- Retención típica de una regla minada: ~0,68–0,73 de su ROI de confirmación. Modo prudente: −32 % de ROI y −3 puntos por apuesta (ST: −3 puntos).
- Buscar todas las combinaciones (Colab, 29 estrategias) no encontró nada demostrable: la correlación entre el ranking de la búsqueda y el ciego fue +0,06. Las carteras 1–7 están en `docs/CARTERAS_VQC.md` solo como registro.
- El microscopio (línea, cuota, set, liga, hora, ratings) **no encontró zonas que valga la pena quitar en ST3, ST5 ni ST0**. Una pista débil en R5 (no apostar fuera de la línea más baja).
- El techo de horas en positivo es ~65 %; bloques de 6 h ~81 %; día ~94 %. «Todas las horas positivas» es imposible con este volumen.

## 5. Ejecución real (medido en la cinta, 6 días)
Porcentaje de alertas donde **la misma línea sigue ofrecida** pasados 10 / 30 / 60 / 120 s: ST5 76 / 50 / 37 / 23 % · ST3 92 / 82 / 74 / 22 % · ST0 98 / 95 / 93 / 88 %. ST5 necesita ~20–30 s. Llegar tarde cuesta poco en promedio (ROI a 60 s +8,0 % vs +8,6 % al instante) pero sí cuando la línea cae ≥10 puntos (13–22 % de los casos). No hay evidencia para una regla de «saltar si la línea bajó».
Desde este cambio se guarda `alert_timing` (detección y envío de cada alerta) para medir la latencia del bot.

## 6. Banca en Telegram
`/estado` muestra: saldo que escribiste, resultados liquidados, **en juego**, **disponible** (lo que debería marcar la casa si jugaste todas) y banca contando lo apostado. Antes mostraba solo saldo + resultados, que supera al saldo de la casa en el stake abierto (mediana ~3,7 % de la banca, hasta ~24 %). El tamaño de las alertas no cambió.
Cuenta cada alerta como jugada salvo «No llegué»: si no juegas alguna, `/banca <saldo>` reajusta.

## 7. Infraestructura
- `collector.yml`: recolector encadenado (~5,75 h por corrida); `publish` sube ledgers a la release `ledger` cada ~20 min; `pages.yml` construye el sitio. Los ledgers viajan entre corridas como assets (`st3/st5/st0/calibra/masters/sombra .sqlite`, `users.json`, `session.json`).
- **No leer ni exponer `users.json`** (datos personales).
- Tape por hora en la release `tape-YYYY-MM-DD`.
- El token de `gh` en el entorno de trabajo puede estar vencido: la API pública y `git push` funcionan.

## 8. Investigación (fuera del repo, en Colab/scratchpad)
Notebooks: VQD (descubrimiento), VQC (combinaciones, GPU), VQM (microscopio). Datos: `datos_cartera.npz`, `datos_micro.npz`. El PR #31 (borrador, solo `research/`, auditoría Fase 0 del 23/11) no es de este flujo; decidir si se fusiona como archivo o se cierra.

## 9. Cosas que NO hacer
- No confiar en los botones de Telegram para ejecutabilidad.
- No dimensionar con simulaciones aditivas: la banca es compuesta con recálculo horario.
- No elegir reglas «por ser las mejores» de un barrido: el barrido siempre encuentra algo perfecto en la muestra.
- No cambiar stakes/filtros de producción sin prueba y aviso. Publicar directo a `main` está autorizado por el dueño (27/11) **después de pruebas**, avisando de lo hecho.

## 10. Pendientes vivos
1. Revisión de la sombra el 04/12 (ver §3). 2. R5b. 3. Medir latencia de entrega con `alert_timing`. 4. Vigilar ST3 (ROI de los últimos 6 días +5,8 % vs +15 % acumulado). 5. PR #31. 6. Posibles mejoras de envío (conexión persistente, envío paralelo) tras medir.

## Hallazgos del 28/11 (ver también el chat)
- ROI unitario con alertas liquidadas (hasta 27/11 23:00 UTC): ST3 +12,4 % (n 3313, IC95 +8,8/+16,1), ST5 +12,4 % (n 1305, +7,7/+17,2), ST0 +16,6 % (n 908, +9,3/+23,6; solo 6 días, 22–27/11, los 6 positivos). La variación diaria coincide con la esperada por azar (sin agrupamiento).
- Brecha esposa/dueño: ella apuesta 0,5 plano (2,1–2,4 % de su banca en las tres), el bot da 1 %/1 %/0,45 %. Con las mismas alertas un stake plano pesa ~2,2× más a ST3/ST5 y ~5× más a ST0 en % de banca; en 26/11 (ST3 −20 u, ST0 +24 u) plano daba ≈ +1 USD y el bot ≈ −15 USD.
- Boletos del dueño (25–27/11, 150): línea y cuota idénticas a la alerta en ST0 90 %, ST3 82 %, ST5 24 % (en ST5 la escalera cambia); demora mediana alerta→boleto ~35 s; tomó ~60 % de las alertas en sus horas activas; sin diferencia demostrable entre lo tomado y lo no tomado.
- `alert_timing` no se llenaba (las alertas ya no llevan message_id de canal); corregido el 28/11 (commit 0d8c2db), datos desde la siguiente corrida.
- Telegram «serverless» (tgcloud): solo JavaScript en V8, sin npm, sin red salvo fetch del SDK; no sirve para este sistema (Python, modelo Markov, ledgers). Descartado.

## Estudio de pesos ST3/ST5/ST0 (28/11, aún NO aplicado)
- Rachas: dentro de cada estrategia son las normales por azar (prueba de rachas z = 0,1 / 0,9 / 0,8; racha máx. ST0 = 11 con orden de liquidación estimado). La racha «combinada» de 23 baja a 17 al contar un resultado por partido (varias alertas comparten partido). No hay evidencia de régimen.
- Correlación entre estrategias (bloques de 3 h): ST0–ST3 0,30, ST3–ST5 0,34, ST0–ST5 0,16.
- A igual riesgo que hoy (SD ≈ 6,1 % de banca por bloque de 3 h tomando todas las alertas), con ROI posterior ST3 12,0 / ST5 11,8 / ST0 13,8 %: stakes por alerta actuales 1/1/0,45 → Sharpe por bloque 0,637; parejo 0,76 c/u → 0,686; ST3 0,71 / ST5 1,0 / ST0 0,71 → 0,685; óptimo 0,54/0,90/0,86 → 0,693. Si ST0 cae a 8 %: parejo 0,534 (peor que hoy 0,548); ST3 0,71/ST5 1,0/ST0 0,71 → 0,544 (casi igual que hoy). Propuesta robusta: ST3 0,7 % · ST5 1,0 % · ST0 0,7 %. El stake se redondea a $0,5 y mín. $1 (`bankroll.stake_now`).
- Un optimizador sin encoger sobre solo 6 días pone 100 % en ST0: no confiar (sobreajuste a una racha de 6 días).
- Micro: ningún stake por apuesta (∝ EV, ∝ 1/(cuota−1), ∝ cuota) supera al plano de forma demostrable. Pista débil a seguir hacia adelante, sin aplicar: ST5 con EV 15–16 % (quintil bajo, n 266) ROI −0,5 % frente a +16 % el resto.

## Regla de jugadores (auditada 28/11)
- `signals/players.py` multiplica el stake (×0,4–1,6, media 1) según el rating de puntos de los jugadores. Reconstruida punto-en-el-tiempo con datos posteriores a su diseño (21/11→28/11, 2.225 alertas): ganancia extra +49 u (IC95 +12/+88) frente a stake plano; ST5 +19,5 u (IC +3,7/+34,8), ST3 +22,8 u (IC −1/+46), ST0 +6,9 u (no concluyente). ROI por quintil de multiplicador en ST3/ST5: ~0–4 % (m≈0,4) a ~23–25 % (m≈1,5); pendiente ROI ≈ −0,12 + 0,22·m (z=3). Fuerza actual ≈ óptima en Sharpe (k=1: 0,679; k=1,5: 0,681; k=2: 0,671). No tocar. Los multiplicadores de ST0 tienen media 1,14 (no se centran por estrategia).
- Pruebas de robustez de los pesos 0,7/1,0/0,7 vs 1/1/0,45 (22–28/11, con tilt): Sharpe 0,792 vs 0,674, dif +0,119 IC95 [+0,052, +0,200]; 1.ª mitad 0,835 vs 0,749, 2.ª mitad 0,679 vs 0,533; sin los dos mejores días de ST0 0,605 vs 0,553; con descuento de ejecución (−1/−3/−0,5 pts) 0,706 vs 0,590; sin tilt 0,722 vs 0,607. Exposición abierta simultánea (todas tomadas): mediana 3,9 %, p99 8,7 %, máx 11,5 % de la banca.
- Riesgo con todas las alertas tomadas y reinvirtiendo: sin ventaja real el bot pierde ~70 % en 60 días por arrastre de varianza → por eso el freno graduado. Calibración sobre 14–28/11: caída máx 18 %; el freno por ventaja cuesta ~6 % del crecimiento final y reduce la caída mediana en el escenario «ventaja desaparece» de 80 % a 41 %.
