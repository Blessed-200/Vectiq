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
  `VECTIQ_ALERTS=st3` (por defecto) | `masters` | `st3,masters`.

## Controles de riesgo (para dinero real; el libro en papel siempre registra)

* Apuesta sugerida = 1 % de la banca actual; **0,5 %** tras una caída del 15 %; **pausa** a los 30 %.
* Pausa si las últimas 150 apuestas pierden más del 8 % o si el día pierde más del 10 % de la banca.
* Mensajes "EN PAUSA" / "REANUDADO"; aviso si pasan 3 h sin señales; resumen diario con ROI, caída y fill rate.

## Qué no sabemos aún

Límite de apuesta, retraso real de la apuesta, aceptación de cuota y limitación de cuentas ganadoras. Se miden con los
botones. No hay forma de garantizar el futuro: el ROI real puede ser menor que el del backtest.
