# Contrastes y políticas de F4 y F8

Este registro se escribió antes de leer filas o resultados de confirmación y ciego. Las fechas agregadas y la rama prioritaria ya se conocían por F0. Cada política usa una sola señal por partido y motor en su primera ocasión histórica identificable. El capital de referencia arriesga el 1 % por alerta; se trata de una comparación de papel a cuota ofertada. Los denominadores de apertura, terminales y soporte se declaran por separado.

| ID | Política y única parametrización |
|---|---|
| P1 | Sobre ST3 vigente, imponer también cuota ≤2,30. Las otras dos ramas mantienen su decisión. |
| P2 | Sobre cada alerta vigente, tomar si la ventaja calculada alcanza 0,05. |
| P3 | Sobre las mismas alertas, multiplicar el 1 % por el valor de ventaja / 0,10 limitado entre cero y uno. |
| P4 | Omitir exposiciones cuya fracción histórica de deuce supere el percentil 75 de su motor en descubrimiento. |
| P5 | Multiplicar el 1 % por 1 − max(0,z)/2, limitado a [0,25, 1]. z estandariza la fracción de deuce con media y desviación poblacional del motor en descubrimiento. |
| P6 | Sólo ST3; de la primera señal de papel con margen ≥5 y cuota ≥2,00, tomar si la ventaja alcanza 0,05. Aquí sí entran cuotas fuera de la banda. Contrastar aparte con el criterio vigente ≥2,10 y la banda 2,10–2,30 sobre el mismo universo extensivo. |

Se usa p_cal congelada según el protocolo del modelo, condicional a ganar o perder. La ventaja por unidad inicialmente apostada es (1 − p_push) × (p_cal × cuota − 1). Si p_cal falta o hay línea entera y no se puede estimar devolución, P2/P3/P6 mantienen el dinero sin exponer. Si un ajuste de deuce no se puede calcular, P4/P5 conservan el stake de referencia y lo declaran. Las políticas principales P1–P5 se deciden para la cartera completa, P6 para ST3. Las filas por motor son descriptivas.

La medida primaria de política es el cambio en crecimiento logarítmico por apertura de referencia, con cero por señal omitida. Referencias: regla vigente y Under en iguales motor, etapa, liga, línea y centésima de cuota. El Under de comparación recibe el mismo tamaño que la candidata y se valora a su precio, usando la fracción de ganadas/perdidas/devueltas del estrato de control. Se vuelve a ponderar el control en cada muestra. Se publican también retorno unitario, acierto, cuota, volumen y sensibilidad de ejecución al precio visible observado 3–15 segundos después, condicionado a misma línea y estadio. Los resultados sin final o sin soporte se cuentan aparte.

Para cada política se exige límite inferior del IC95 positivo contra ambas referencias en confirmación y ciego. En P6 debe superar también la banda. Holm al 5 % sobre las seis, usando por política el máximo de los valores p de las referencias (prueba de intersección). Los p de política se obtienen por prueba de signos unilateral sobre diferencias diarias emparejadas; es un contraste de consistencia del signo, adicional al IC para la mejora media. Exigir ≥70 % de la frecuencia de las entradas de referencia, signo favorable en ≥80 % de los meses, al menos dos meses evaluables y dos ligas con signo favorable, y caída máxima P95 sin aumento. La caída intermedia por política se simuló a cierre de apuestas por bloques de días, con capital fraccional hipotético. El estudio de cartera y la ejecución real requieren su propia validación.

| Hipótesis | Contraste direccional único, separado de las políticas |
|---|---|
| H1 | Entre primeros sets con margen ≥5, comparar margen orientado hacia su ganador en S3 menos S2. La reducción extra en S2 sobre S3 excedería la regresión a la media que una fuerza estacionaria genera en ambos. No identifica adaptación permanente. |
| H2 | A igual liga y margen absoluto del S1, mayor ventaja temprana orientada al ganador anticipa mayor residuo Under frente a la probabilidad de mercado. |
| H3 | A igual liga y margen del S1, mayor volatilidad histórica reduce el margen posterior del ganador inicial, promedio S2 y S3. |
| H4 | A igual etapa, liga y banda de favorito del mercado ganador, mayor propensión histórica al deuce eleva la fracción de sets restantes que llegan a diez iguales. |
| H5 | La corrección media del centro por liga y franjas UTC de seis horas (20 partidos virtuales hacia su etapa) reduce error cuadrático frente a una corrección común por etapa. Se ajusta en descubrimiento; su uso en ese mismo tramo es descriptivo, fuera de muestra se congela. |
| H6 | Un recorte de precio respecto al último tick conocido de la misma línea predice menor residuo Under a igual etapa, liga, línea y centésima de cuota actual. |
| H7 | A igual etapa, liga y centésima de cuota, mayor línea restante (línea menos puntos ya jugados) anticipa mayor residuo Under. |

Una fila canónica por partido y etapa para mecanismos: primera hora de cotización elegible y escalón más cercano a 50 % sin margen en esa hora. Se comprueba cada pendiente dentro de los estratos definidos; los estratos sin variación quedan fuera del denominador identificable. Una sola familia Holm para los siete mecanismos. Estas pruebas no promueven filtros. Las rachas se comparan descriptivamente con 2.000 permutaciones de partidos dentro del día, manteniendo juntos todos sus motores.

Para pronósticos, sólo un contraste primario: p_cal frente a p_current y p_market en las mismas filas con desenlace W/L. Exigir al menos 0,005 nats de mejora en log-loss sobre ambas además de las puertas temporales. Brier, AUC, medias por bandas de probabilidad y desgloses por motor son métricas secundarias. Semilla 20261122, 10.000 remuestreos de días UTC enteros y 95 % percentil para estimaciones, incluyendo diferencias emparejadas. La réplica sin denominador queda fuera del intervalo y se cuenta. Ningún resultado puede demostrar rentabilidad ejecutada.
