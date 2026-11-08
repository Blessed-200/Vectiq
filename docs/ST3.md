# ST3 — la regla, las alertas y los controles de riesgo

Congelada el 2026-11-04. Todo es **papel** (libro `st3.sqlite`, banca simulada de $100 con 1 % por apuesta, compuesta). Las
alertas de Telegram son una ayuda para apostar a mano; el sistema no apuesta por sí solo.

## Regla

Masters (sin China ni Super League), al mejor de 7, en vivo, con **exactamente un set completado** y:

1. el ganador del set 1 ganó por **5 puntos o más**;
2. como mucho **4 puntos** jugados del set 2;
3. se apuesta **Menos de** en la **línea más baja** de la escalera de totales cuyo Under paga **≥ 2,00**;
4. primera lectura que cumple, **una posición por partido**.

Por qué: tras una paliza en el set 1 la casa sigue fijando el total restante como si los dos jugadores fueran iguales
(≈ 90 puntos); lo real ronda 82–83. La línea baja paga más por el mismo error.

## Evidencia (honesta)

| | n | acierto | cuota | ROI | IC 95 % |
|---|---|---|---|---|---|
| Backtest 26-oct a 3-nov (cinta) | 1.342 | 53,6 % | 2,13 | +13,9 % | [+8,3, +19,7] |
| Forward real 1-nov a 3-nov | 319 | 52,0 % | 2,12 | +10,4 % | ≈ [−1, +22] |

La regla se diseñó mirando esos días: la cifra esperable es **+8 a +12 %**. Un modelo largo de 40 constantes rindió lo
mismo (+16,6 % contra +16,4 % en días no vistos), por eso se queda la regla simple. El 3B (entrada dentro del set 2) rindió
+8,7 % con n=829 y ventana de 12–25 s: queda en investigación.

## Alertas (Telegram, hora de Cuba en 12 h)

* **ST3 · PREPÁRATE** — el set 1 va con ventaja ≥ 5 y el líder ≥ 9 puntos. Calibrado con 4.586 partidos: acierta el 75–80 %,
  anticipa 30–66 s. Variable `VECTIQ_ARM=on|silent|off` (por defecto `on`).
* **ST3 · APUESTA YA** — decisión, cuota mínima (2,00), apuesta sugerida, enlace directo al partido y tres botones:
  *Aceptada / Cuota menor / No llegué*. Lo que se pulse queda en la tabla `exec` de `st3.sqlite` (fill rate, deslizamiento).
* La línea elegida vive ~18 s de mediana: con 10 s de retraso queda ejecutable ~57 % de las señales (el ROI de las que quedan
  no baja).
* El Telegram viejo (modelo Masters) **queda en silencio**: sigue registrando en `masters.sqlite` para comparar.
  `VECTIQ_ALERTS=st3,pre,st4` (por defecto): ST3 (palizas en vivo) + ST4 (segunda oportunidad tras 2-0, ver `signals/st4.py`) + PRE (prepartido del modelo Telegram viejo). `masters` = formato antiguo. `VECTIQ_MASTERS_LIVE=on` reactiva las entradas en vivo del modelo viejo (apagadas: +3 a +7 % de ROI, por debajo de ST3). Tope 1,5 % por partido entre PRE y ST3 y paro del día (-10 %) contando los dos.

## Controles de riesgo (para dinero real; el libro en papel siempre registra)

* Apuesta sugerida = 1 % de la banca actual; **0,5 %** tras una caída del 15 %; **pausa** a los 30 %.
* Pausa si las últimas 150 apuestas pierden más del 8 % o si el día pierde más del 10 % de la banca.
* Mensajes "EN PAUSA" / "REANUDADO"; aviso si pasan 3 h sin señales; resumen diario con ROI, caída y fill rate.

## Qué no sabemos aún

Límite de apuesta, retraso real de la apuesta, aceptación de cuota y limitación de cuentas ganadoras. Se miden con los
botones. No hay forma de garantizar el futuro: el ROI real puede ser menor que el del backtest.

## ST4 y ST5 (7-8 nov)
- **ST4 «segunda oportunidad»** (`signals/st4.py`): Under tras 2-0 del mismo jugador con márgenes sumados >= 8. +15 % oct, +14.4 % a ciegas.
- **ST5 «alta probabilidad»** (`signals/st5.py`, modelo en `signals/markov.py`): cadena de Markov del tenis de mesa con prior de fuerza ajustado solo con resultados finales (sin líneas de la casa). Under en el peldaño de mayor EV con probabilidad >= 65 % y EV >= +5 % tras el set 1 o 2. Prior congelado el 27 oct: +15.5 % (22-27 oct), +26.8 % (28-31 oct), +19.4 % (2-7 nov), acierto 62-68 %, ~30 señales/día. Stake 1 % desde el 8 nov (`VECTIQ_ST5_FRAC`; era 2 %, subir solo si las primeras 150 apuestas en vivo confirman >= 62 % de acierto). Tras el set 3 solo se mide en papel. Cuando ST5 avisa, ST4 no avisa en ese partido.

## Stakes desde el 8 nov (medio agresivos)
ST3 1.5 %, ST4 1.5 % (0.75 % si ya hay apuesta en el partido), ST5 1 % (ya en main), PRE 0.5 %. Tope por partido: 2 % PRE+ST3, 3 % con ST4/ST5. Base: en papel desde el 5 nov, ST3 +16.9 % (718 apuestas, positiva los 4 días), ST4 +13.8 %, PRE solo ~+3 %, ST5 -1.4 desviaciones bajo su backtest. Mismos frenos (paro del día -10 %, mitad a -15 %, pausa a -30 %).
