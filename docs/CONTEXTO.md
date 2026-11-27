# VECTIQ · Contexto vigente (actualizado 27 nov 2026)

Fuente única para cualquier chat o agente nuevo. Si algo de aquí contradice un documento viejo (`docs/devin_*`, `HIPOTESIS.md`, etc.), **manda este**. Lo histórico se conserva solo como registro.

## 1. Qué es y para quién
Operación de apuestas Masters (tenis de mesa, mejor de 7) sobre el **total del partido (Under/Over)** en Betwinner. El dueño habla español, no es técnico y quiere lenguaje llano, honestidad y decisiones basadas en matemática. Tres personas usan el mismo sistema con cuentas separadas (el dueño, su esposa y su cuñado); la cuota es la misma. El sistema graba la cinta completa de la casa, calcula señales, las manda por Telegram y publica un panel en GitHub Pages.

## 2. En producción (no tocar sin pruebas)
| Estrategia | Qué hace | Stake |
|---|---|---|
| **ST3** | Under tras el set 1, rungo bajo, modelo Markov | 1,0 % de la banca por alerta |
| **ST5** | Under tras 2 sets completos, valor 15–30 % de EV, aparece ~78 s después del set | 1,0 % |
| **ST0** | Prepartido, Under | 0,45 % |

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
