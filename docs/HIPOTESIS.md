# Registro de hipótesis (VECTIQ)

Cada hipótesis tiene su prueba y su criterio **antes** de mirar los datos. Se revisa con el comando «revisa hipótesis» o en la fecha indicada.
Regla general: un cambio solo entra si pasa en un tramo ciego posterior a su descubrimiento. Apuestas necesarias (80 % potencia, 5 % error)
para confirmar una diferencia de ROI: 20 pts → 170; 15 pts → 300; 10 pts → 680; 5 pts → 2,700 (ST3 genera ~150 al día).

| # | Hipótesis | Prueba | Datos | Fecha de revisión | Criterio |
|---|---|---|---|---|---|
| H1 | La casa está corrigiendo su sesgo (se pierde la ventaja de ST3/PRE) | Sensor CALIBRA (sesgo por etapa) + CUSUM sobre el ROI de ST3 (h=40: falsa alarma cada ~50 días, detecta una caída a +5 % en ~4 días) | Diario | 11 nov | Alarma del sensor o ROI 150 apuestas < 0 → bajar stakes a la mitad |
| H2 | Rusia (la liga más fuerte) pierde ventaja; Polonia es negativa | ROI por liga con intervalo, 300+ apuestas por liga | ST3 por liga | 14 nov | Liga con límite inferior < 0 en 300 apuestas → stake 0.2 % (CALIBRA) |
| H3 | ST5 está sobreconfiada (58.8 % vs 66.4 % predicho en 80 apuestas) | Calibración en vivo por tramo de probabilidad; 150 apuestas | st5.sqlite | 12 nov | >= 62 % de acierto → volver a 2 %; < 55 % → capa de calibración o apagar |
| H4 | Los primeros puntos del set 2 mejoran la entrada de ST3 | Registrar puntos en juego al avisar; comparar entrar con ventaja del ganador del set 1 vs sin ella | Solo 104 casos hoy | 23 nov | >= 300 casos y diferencia de ROI > 10 pts |
| H5 | Memoria de jugadores (ventaja histórica del ganador del set 1) mejora ST3 | Ventaja previa del ganador en cuartiles vs ROI | 12 partidos por jugador hoy (+24 % vs +11 %, no concluyente) | 30 nov | Diferencia > 10 pts con p < 0.05 en un tramo ciego |
| H6 | ST4 (2-0, márgenes >= 8) mantiene su ventaja | ROI vivo 100 apuestas, luego 300 | st4.sqlite (+13.8 % en las primeras 77) | 14 nov | ROI >= +5 % con 300 apuestas |
| H7 | PRE vale la pena a 0.5 % | ROI por 300 apuestas reales y en papel | masters.sqlite | 16 nov | < +3 % → quitar alertas PRE |
| H8 | Stake por evidencia (no por rachas) reduce caídas sin perder ventaja | Simulación: en colapso de ventaja baja la caída mediana de 54 % a 28 %; por racha no ayuda | Simulación hecha el 9 nov | 14 nov | Implementar en CALIBRA como sombra y comparar con ANCLA |
| H8b | (actualiza H8) El stake por evidencia ya está en el bot desde el 10 nov: ¼ Kelly sobre el límite inferior del ROI de las últimas 1,000 apuestas, tope 1 % (PRE 0.5 %) | Mirar los stakes reales que sugiere el bot contra el ROI siguiente; vigilante CUSUM (ROI esperado +10 %, alarma 5) | Diario | 17 nov | Si la caída máxima real supera 20 % con ROI positivo → bajar tope; si el stake queda en 0 con ROI > +8 % durante 7 días → acortar la ventana |
| H9 | ST3 rinde menos entre 8 AM y 4 PM (hora de Cuba): +10 % → -10 % entre mitades; resto +24 % → +11 % | ROI por franja con intervalo, 300 apuestas por franja, en tramo ciego | 8-16 h: 335 apuestas, 4-7 nov vs 8-10 nov | 17 nov | Límite superior de la franja < +5 % con 300 apuestas ciegas → no apostar en esa franja |
| H10 | PRE con cuota <= 2.00 rinde más que con cuota > 2.00 (en 4-7 nov +12 % vs +4 %; en 8-10 nov +3 % vs +4 %: no se sostuvo) | ROI por cuota en tramo ciego | masters.sqlite | 17 nov | Diferencia > 8 pts en 400 apuestas ciegas |
| H11 | El margen del set 1 mejora ST3 de forma monótona (según otra IA con octubre). En noviembre: margen 5 +13 %, 6 +12 %, 7 +19 %, 8 +5 %, 9+ +20 % | ROI por margen, 300 apuestas por tramo | st3.sqlite | 17 nov | Pendiente real > 3 pts de ROI por punto de margen con p < 0.05 → stake por tramos |

