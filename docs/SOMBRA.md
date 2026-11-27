# Sombra individual (R1, R2, R3, R4, R5, R7, R10, R12, R13, R37)

**Qué es.** Diez reglas minadas en la investigación VQD, seguidas **cada una por separado** en papel, con la cuota real que ofrece la casa en el instante en que la regla se cumple. **Nunca envía nada a Telegram** y no toca ST3, ST5 ni ST0. Sirve para comprobar con cuota real si lo que se vio en la cinta se sostiene antes de poner dinero.

**Dónde se ve.** En la página, enlace «Individuales (sombra)» (`sombra.html`), separado de la sección combinada (que es solo ST3 + ST5 + ST0).

**Cómo decide.** Idéntico a la investigación (comprobado repitiendo la cinta del 22/10 al 26/11: 23 mil partidos, las 10 reglas apuestan en el mismo partido, con la misma línea y cuota en el 100 % de los casos):
- Cada partido Masters se evalúa una vez por etapa (antes del 1.er set, tras el 1.º, 2.º…), en el primer instante ejecutable (como máximo 4 puntos jugados del set en curso).
- Dentro de un instante se prueban los rungos de la línea más baja a la más alta, primero Under y luego Over; la primera fila que cumple todas las condiciones de la regla es la apuesta. Una apuesta por partido y regla.
- Varias reglas usan un rating de jugadores propio (`ps_*`): media ponderada (vida media 5 días) de «total final − centro de la escalera al inicio del partido». **No** es el rating de `players.py`; no se mezclan.
- Otras usan el modelo Markov (`markov.py`, mejor de 7) y la forma de la escalera.
- **Importante:** varias reglas también apuestan al Over (R5 ~21 %, R7 ~26 %, R37 ~20 %, R10 ~13 %, R3/R13 ~8 %, R12 ~4 %); R1, R2 y R4 solo Under.

**Aislamiento.** Archivo propio (`sombra.sqlite`, release `ledger`), cada llamada del colector va en `try/except`, y se apaga con `VECTIQ_SOMBRA=0`. Las pruebas (`tests/test_sombra.py`) comprueban que las alertas y el libro de ST0 son idénticos con la sombra encendida, apagada o rota.

**Arranque.** `sombra_seed.json` trae los ratings de jugadores calculados con la cinta hasta el 26/11 09:44 UTC; sin ellos las reglas con rating no dispararían durante días.

**Criterio de revisión (7 días).** Se descarta la regla que quede negativa con volumen suficiente o por debajo de la mitad de su ROI de confirmación. Registro de las carteras: `docs/CARTERAS_VQC.md`.
