# Vectiq · Auditoría de las alertas Under

**Dictamen de la Fase 0. Detener antes de probar mejoras.** En descubrimiento se observa una ventaja a cuota publicada, especialmente tras el primer set. Faltan los datos de la cuenta que permitirían atribuir una rentabilidad ejecutada, medir el deslizamiento y reconstruir la caída de la banca.

## 1. Resumen en cinco líneas

1. El total acabó por debajo del centro de mercado con más frecuencia que el 50 % implícito, tanto antes del primer punto como tras el primer set. Sólo cinco partidos con identificadores distintos permitieron el cotejo estricto de prepartido.
2. A igual etapa, liga, línea y cuota, ST3 añadió **6,88 puntos porcentuales de ROI** en la cinta. IC95 de **3,18 a 10,31**, con 1.929 señales y 10.143 ocasiones de control, en 19 días.
3. En los libros que alimentan el panel, 771 de 896 entradas tienen final corroborado y 664 tienen, además, cuota exacta y registro de mensaje conciliados.
4. El histórico contiene dos estrategias prepartido distintas y dos formas de entrar tras el segundo set. La composición predeterminada cambió dentro del período.
5. El ROI real del 16,6 %, la caída real del 19 % y el techo por estrategia siguen **no verificados**. Se mantuvieron cerradas confirmación y prueba ciega.

## 2. Fuentes, población y reglas de entrada

Se congeló el código de producción `b706336`, junto con un manifiesto de archivos de Releases identificados por SHA-256. Se usaron los 31 días UTC completos del 23 de octubre al 22 de noviembre de 2026. El primer día parcial y el último día UTC en curso se excluyeron antes de calcular rendimientos. Se asignaron 19 días a descubrimiento, 6 a confirmación y 6 al ciego mediante cortes acumulados redondeados. **Este informe sólo usa del 23 de octubre al 10 de noviembre, ambos incluidos.** La regla estadística actual aplicada a esas fechas es retrospectiva. [Protocolo sellado](https://github.com/Blessed-200/Vectiq/tree/37f86f53fd02867ebc9f833497e26db17ccd540b/research) · [Estudio de trazabilidad](https://github.com/Blessed-200/Vectiq/tree/184b2d391e29baa0c8aeb0a65e3282ce45159a02/research)

La cinta contiene 4.803.946 registros en 558 fragmentos de descubrimiento y al menos un registro en cada una de las 456 horas. Se observaron 11.260 identificadores de partidos de la familia Masters y se validaron 8.793 finales dentro del tramo. Se exigieron cuatro sets ganados, tanteos legales, acuerdo del marcador global, formato Bo7 acreditado y ausencia de finales contradictorios. La población incluye Argentina, Belarús, España, Polonia y Rusia, además de etiquetas femeninas. Las métricas de intervalo y los motivos de exclusión están desglosados en el inventario. [Inventario completo](results/tape_discovery.json)

| Nombre del dueño | Regla encontrada en el código congelado | Consecuencia para la auditoría |
|---|---|---|
| PRE | El panel actual usa ST0. A cero puntos, necesita al menos tres líneas completas y elige la menor. Acepta cuota ≥2,20, o ≥2,10 con puntuación conjunta de jugadores z≤−0,4. Puede activarse en vivo a 0–0. | La línea 104 no es un umbral. El perfil de los jugadores cambia también la elegibilidad de esta rama. ST0 entró en el código el 22 de noviembre. Existe un PRE anterior en el libro Masters con etiqueta `pre`. |
| SET1 | ST3, un set completo con margen ≥5, hasta cuatro puntos ya jugados del siguiente set. Primera línea ascendente con Under ≥2,00 para papel. La alerta exige ≥2,10 y pasa guardas adicionales. | El código vigente no impone un máximo de 2,30. La primera entrada de papel puede consumir el partido sin generar aviso. Entre las 731 aperturas ST3, 38 superan 2,30. La hipótesis de que 2,10–2,30 es la mejor banda queda sin validar. |
| SET2 | El panel vigente usa ST5, un modelo Markov. El papel admite k=1, 2 y 3 con p≥0,65 y ventaja esperada ≥0,05. Para avisar necesita k=1 o 2 y ventaja estrictamente mayor de 0,15 y hasta 0,30. | En descubrimiento, cinco de sus 165 entradas del panel fueron en k=1. Existe una rama histórica ST4 que sí exige dos sets ganados por el mismo jugador y margen acumulado ≥8 por defecto. |

El orden predeterminado al cierre incluye ST3, ST5 y ST0. En versiones anteriores figuran `pre` y ST4. En descubrimiento constan 1.292 marcadores `pre` y 281 de ST4. Por eso las cero aperturas de ST0 en estas fechas no significan que el bot careciera de alertas prepartido. El historial de Git documenta los valores predeterminados, mientras que el registro de avisos acredita una actuación concreta. Los valores efectivos por despliegue pueden depender de la configuración. El colector corre con GitHub Actions, distribuye avisos por Telegram y Pages publica la curva derivada de los libros. CALIBRA ya registra un residuo diario y un CUSUM. Su tasa real de falsas alarmas queda sin validar. [Historial del código](results/source_facts.json) · [Registros conciliados](results/entry_trace.json)

El cliente apunta por defecto a BetWinner y admite otros dominios por configuración. El panel de cuotas distingue ganador de partido, total de partido y cuotas de subpartidos por set. Se encontró un grupo de hándicap cuya unidad en sets no está acreditada por el parser. Se conservaron los grupos desconocidos con su código. Un registro crudo contiene hora local de recepción, tipo de consulta (`item`, `line` o `game`), identificador del partido y respuesta. El contador de cambios se reinicia en los relevos y el detector coteja duplicados. El JSON de cobertura trae el número de observaciones con precios abiertos o bloqueados por mercado. [Esquema y mercados](results/tape_discovery.json)

La mediana entre cambios de la lista en vivo fue de 6,0 segundos para el feed completo y de 12,0 segundos por partido Masters. Para ese partido, el percentil 95 fue de 42,0 segundos. Hubo un lapso de 73,0 minutos sin un cambio registrado en la lista global. La ausencia de latidos en los registros impide afirmar que todo ese intervalo fuera una caída del servicio. En 5.595 registros el reloj de actualización del proveedor queda por delante de la hora local de recepción. Las decisiones usan esta última. La cinta conserva cambios observados y no acredita todas las cotizaciones entre sondeos. [Distribuciones de tiempos](results/tape_discovery.json)

## 3. Resultados y libro de pruebas

### Cuánto se desviaba la línea

Se calculó el primer centro disponible de la escalera a partir de las cuotas de ambos lados, retirando el margen e interpolando dónde el mercado asignaba un 50 % al Under. El centro de ese mercado y la línea concreta de una alerta son dos cantidades diferentes.

| Primer centro disponible | Partidos con final / con centro | Centro menos total real, puntos [IC95] | Fracción que acabó debajo, % [IC95] |
|---|---:|---:|---:|
| A cero puntos, incluido el vivo a 0–0 | 5.675 / 7.087 | +5,76 [4,99 a 6,48] | 59,26 [57,90 a 60,65] |
| Tras el primer set | 8.364 / 9.744 | +5,46 [4,89 a 6,01] | 59,11 [58,00 a 60,25] |

Ambas estimaciones abarcan 19 días. Los cinco puntos son compatibles con el intervalo anterior al primer punto, que incluye el estado en vivo a 0–0. Para este primer centro tras el set 1, cuatro puntos quedan por debajo del intervalo estimado. Otro reloj o la línea concreta comprada definen otra comparación. La fracción por debajo del centro aporta una comprobación distinta de la distancia media en puntos, ya que el centro representa una probabilidad del 50 %. [Cálculo y denominadores](00_tablas.md)

**Prepartido estricto.** El feed de línea anterior al inicio programado contiene 921 identificadores Masters, de los cuales 539 ofrecen una escalera que permite calcular el centro. Ninguno de los 921 coincide por identificador con los 8.793 partidos con final admisible. Por tanto, atribuir los +5,76 puntos directamente al tablero prepartido exigiría una conciliación entre identificadores. Esta comprobación se especificó después de identificar que el estado a cero puntos original admite el vivo. [Sensibilidad por reloj](results/prematch_clock.json)

Como prueba exploratoria de identidad, se exigió la misma liga, ambos jugadores en el mismo orden y el mismo segundo de inicio, sin tolerancia. Se comprobó unicidad frente a todos los identificadores de ambos tableros antes de consultar sus finales. Sólo cinco de los 539 partidos con centro permitieron el enlace, en cuatro días. En esos cinco, los identificadores de ambos jugadores concordaban y la primera identificación en vivo fue posterior a la cuota prepartido y anterior al marcador final. Se usó únicamente para atribuir el desenlace. El centro menos total fue +19,74 puntos [IC95 −0,95 a 30,79]. La muestra es demasiado pequeña para acreditar los cinco puntos en el **prepartido estricto**. Los 251 casos con hora de inicio revisada y los 282 sin clave exacta en vivo quedaron excluidos. [Resultados del enlace](results/prebook_identity.json) · [Correspondencias inferidas](results/prebook_identity.csv)

### Regla actual frente al Under ciego

Se tomó la primera cuota visible para cada combinación de partido, etapa, liga, línea y centésima de precio, con o sin señal. Los controles incluyen los propios partidos seleccionados. Se ponderaron sus estratos para reproducir los de la señal en cada remuestreo. Se excluyeron finales desconocidos. La cartera del cuadro cuenta una unidad por señal y permite que un mismo partido aporte a varios motores. En el conjunto el control conserva también el motor y la restricción temporal del prior de ST5.

| Pizarra, sin guardas personales ni ejecución | Señales / ocasiones control | Días activos | ROI plano de la señal, % [IC95] | ROI plano del control, % [IC95] | Mejora en puntos porcentuales [IC95] |
|---|---:|---:|---:|---:|---:|
| PRE actual, parte de ST0 identificable | 2.479 / 3.486 | 19 | 16,34 [12,20 a 20,84] | 15,79 [12,03 a 19,87] | +0,55 [−1,83 a 3,05] |
| SET1, ST3 | 1.929 / 10.143 | 19 | 23,97 [18,12 a 29,87] | 17,10 [12,21 a 22,36] | **+6,88 [3,18 a 10,31]** |
| ST5, ambos momentos de alerta | 154 / 790 | 3 | 15,87 [−2,70 a 37,18] | 7,01 [−6,12 a 24,35] | +8,87 [3,42 a 14,49] |
| ST5, sólo después del segundo set | 149 / 768 | 3 | 13,53 [−4,25 a 36,39] | 5,04 [−7,74 a 23,33] | +8,48 [3,35 a 14,15] |
| **Conjunto contrafactual** | **4.562 / 14.419** | **19** | **19,55 [15,27 a 24,09]** | **16,05 [12,33 a 20,09]** | **+3,50 [1,33 a 5,61]** |

ST3 mejora el acierto en **3,18 puntos porcentuales** [1,47 a 4,76]. Acertó el 56,92 % [54,24 a 59,64] de 1.929 señales, frente al 53,74 % [51,53 a 56,17] en 10.143 ocasiones comparables de 4.152 partidos. En ST0 quedaron 4.576 primeras candidatas cuyo historial de jugadores podía cambiar la decisión. En ST5, tres jornadas aportan casi toda la información efectiva. El bootstrap con tres jornadas no acredita estabilidad. La mayor diferencia puntual de ST5 no basta para darle prioridad sobre ST3.

El conjunto contrafactual cuenta 3.675 partidos distintos y el control contiene 5.351. La selección retrospectiva arranca antes de la implantación de ST0 y ST3. No representa la cantidad de alertas enviadas en esas fechas. El detalle conserva los días activos, partidos repetidos, apuestas devueltas sin ganancia y tamaños reales de las muestras. [Comparaciones con acierto y ROI](00_tablas.md)

### Entradas del panel, mensajes y dinero

| Punto de comprobación | ST0 | ST3 | ST5 | Conjunto |
|---|---:|---:|---:|---:|
| Aperturas que hoy mostraría el panel | 0 | 731 | 165 | 896 |
| Misma cuota, línea y etapa en la cinta al milisegundo registrado | 0 | 729 | 165 | 894 |
| Mismo marcador de aviso con identificador de mensaje | 0 | 677 | 107 | 784 |
| Las dos comprobaciones anteriores para la misma entrada | 0 | 675 | 107 | 782 |
| Además, final de partido admisible dentro del tramo | 0 | 572 | 92 | 664 |

Para las **664** entradas que pasan todos esos cruces, en siete días activos, el ROI plano a la cuota del aviso fue **+22,29 % [14,52 a 29,11]**. El denominador describe mensajes con precio y desenlace verificables. Falta la confirmación del operador que vincule cada uno con un precio aceptado y el dinero liquidado. Un botón de Telegram se identifica por partido y carece de la combinación completa de motor, línea, importe, hora y cuota aceptada. [Cruce de avisos](results/entry_trace.json)

En los libros filtrados por el panel, aunque no haya registro de aviso, se corroboraron 771 entradas con final, correspondientes a 724 partidos, entre 896 aperturas de 843 partidos. En las 771 hubo 445 aciertos, **57,72 % [54,35 a 60,90]**, cuota media 2,112, ROI plano **+21,57 % [14,91 a 28,95]** y ROI ponderado por el stake de papel **+19,32 % [15,71 a 24,89]**, en siete días activos. Las 125 entradas sin final tienen un efecto acotable a cuota publicada. Si todas perdieran, el resultado muestral de las 896 sería +4,61 %. Si todas ganaran a su propia cuota, sería +34,55 %. Estos extremos son límites contables de la muestra. El IC de las 771 observadas y esas cotas responden a preguntas distintas. [Resultados por motor y conjunto](00_tablas.md) · [Cotas por día](results/ledger_tape_discovery.json)

El panel simula una liquidación diez minutos después de cada entrada. En 748 de las 771 entradas corroboradas el primer final visible en la cinta apareció más tarde. La mediana fue 20,67 minutos. Con 100 dólares iniciales y la convención de ese panel, la máxima caída fue 9,32 % sobre las 771 observadas. El máximo descenso con una unidad plana fue de 9,59 unidades por orden de entrada y de 8,82 si se reconocen a la vez los resultados recibidos al mismo milisegundo. El orden bancario efectivo sigue sin verificarse. [Riesgo, rachas y temporización](results/ledger_tape_discovery.json)

### Las otras dos ramas que sí enviaron avisos históricos

Se identificaron después de inventariar los nombres de los avisos. Para cada marcador se cotejaron partido, etapa, cuota y línea con la lista de la casa al mismo instante. Se exigieron también identidad consistente y final Bo7 posterior. Este diagnóstico identifica las cohortes que el dueño podría haber llamado PRE o SET2.

| Rama registrada | Avisos / con cuota y final conciliados | Días | Cuota media | Acierto, % [IC95] | ROI plano, % [IC95] | Peor racha por entrada |
|---|---:|---:|---:|---:|---:|---:|
| PRE anterior, etiqueta `pre` | 1.292 / 907 | 5 | 1,923 | 54,80 [51,84 a 57,46] | +4,64 [−2,09 a 9,69] | 9 |
| ST4, regla de dos sets | 281 / 239 | 3 | 2,134 | 49,79 [44,44 a 52,63] | +6,45 [−7,27 a 13,19] | 12 |

La línea mediana del PRE anterior en los 907 casos fue 108,5. Ambas estimaciones de rendimiento son compatibles con pérdidas. La cartera analizada en el panel actual excluye estas dos cohortes. [Cohortes históricas y exclusiones](results/legacy_marker_discovery.json)

### Afirmaciones del dueño

| Afirmación | Dictamen sobre el alcance comprobado |
|---|---|
| Más de 1.000 liquidadas y 16,6 % de ROI combinado | **No verificado** para el período y la cuenta originales. El panel actual selecciona 896 aperturas en descubrimiento. Las 4.562 posibilidades de la cinta representan otra población. |
| Máxima caída de 19 % | **No verificado**. La muestra, el reloj de liquidación y los stakes de la banca original no están identificados. |
| Racha de ocho pérdidas en SET1, en un solo día | **Reproducida en papel** entre las 623 ST3 con desenlace conocido, por orden de entrada el 10 de noviembre UTC. Ese día hubo 120 entradas ST3, 22 sin final. Al conservarlas en su posición, la peor racha compatible con los desenlaces desconocidos va de cinco a nueve pérdidas. La de la cuenta queda sin verificar. |
| Todos los días positivos | Se observan siete de siete días UTC positivos entre las 771 entradas con final. El 7 de noviembre tenía 18 entradas sin final y podría cerrar en −6,33 unidades. Seis días siguen positivos incluso si pierden todas sus faltantes. **No verificado** para toda la cuenta o el día operativo de la web. |
| Jugadores sólo modulan el stake | La rama alternativa de ST0 usa z para decidir si hay entrada. El efecto histórico del rating calculado en tiempo real quedó **no verificado**. |
| El ML complejo dio peor resultado que la regla simple | **No verificado** en igualdad de oportunidades, predicciones congeladas y precios. Hay código de backtest y modelos previos. En el libro vigente ST3 guarda 1/cuota como probabilidad y ST0 guarda 0,52 fijo. No se puede atribuir la discriminación del filtro a esos campos. La comparación de calibración corresponde a una fase posterior. |
| SET1 es la que más aporta | ST3 domina el volumen de registros conciliados y muestra valor incremental en descubrimiento. Su aportación marginal a la banca real con solapamiento, límites y capital disponible queda **no verificada**. |

## 4. Qué significa para la herramienta

La casa mostró precios favorables al Under incluso tomando partidos sin exigir el margen del primer set. La regla de ST3 añadió valor encima de esa referencia en este tramo. En ST0, la mejora sobre el Under comparable puede ser positiva o negativa dentro del intervalo. Para ST5, el volumen de ticks oculta que sólo hay tres días de evidencia. El orden de prioridad para **investigar en sombra** es ST3, ST5 cuando acumule más días y, para ST0, reconstruir primero el historial de jugadores. Éste es un orden de verificación de reglas existentes. No se identificó ninguna mejora nueva aprobada, ningún aumento de stake ni un techo máximo.

## 5. Qué podría estar mal y qué quedó pendiente

- El operador puede suspender o actualizar una cuota entre la recepción de la lista y la aceptación del boleto. Las incidencias, la capacidad y el deslizamiento no se pueden calcular a partir de un mensaje. Puede haber oportunidades que no aparecieron en la lista o en el muestreo de detalle.
- Las etiquetas financieras originales del SQLite acumulado carecen de hora de liquidación. Se transfirió el archivo de publicación en memoria y se extrajeron sólo columnas de apertura o de registro de avisos dentro de descubrimiento. No se consultaron sus columnas de desenlace. Los tres runs exitosos de Pages al final de descubrimiento ya no exponían artefacto en la consulta. Los resultados se reconstruyeron a partir del marcador de cinta y las fuentes exactas de esa reconstrucción están fijadas por hash. [Verificación de archivos históricos](results/pages_probe.json) · [Procedencia de las proyecciones](inputs/origins.json)
- Un marcador incompleto puede corresponder a demora, pérdida de captura, retiro o interrupción. `PaperLedger` puede devolver o marcar pérdida por desaparición. Al igualar exactamente una línea entera en un partido incompleto también puede marcar pérdida aunque aún sea posible una devolución. El reglamento público de 1xBet contempla la devolución a cuota 1 si hay retiro en el primer set, salvo mercados ya determinados. Si la interrupción ocurre tras más de dos sets, distingue los totales ya decididos de los pendientes. Un tanteo por debajo de la línea cuando se corta el partido no acredita una victoria del Under. La documentación regional de BetWinner contiene una distinción similar. El dominio, versión contractual aplicable y tratamiento de walkovers, aplazamientos, retiros y casos frontera en esta cuenta siguen **no verificados**. [Reglamento 1xBet, tenis de mesa](https://1xbet.com/rulespdf) · [Reglas regionales de BetWinner](https://betwinner.co.tz/pt/information/rules/10)
- La base de jugadores puede registrar el resultado final junto a un instante anterior del partido sin guardar cuándo se conoció el final. Cargar la base actual para recrear una alerta antigua introduciría fuga. No se usó para rellenar ST0. La frecuencia histórica de esa fuga en decisiones de producción sigue **no verificada**. El test de relojes rechaza predictores posteriores al corte, incluso si su partido ya había empezado. [Pruebas de disponibilidad](test_asof.py)
- El filtro de precios de ST3 y de ventaja de ST5 llegó al código durante el intervalo reservado. La política actual de ST0 se añadió el 22 de noviembre. El ciego está intacto para este equipo. Su independencia frente a los ajustes originales del dueño no se puede garantizar. Los tres días de ST5, los partidos sin final y la correlación entre jornadas limitan el alcance de los intervalos. Entre los partidos de control del conjunto, 18 contribuyeron cotizaciones a más de un día UTC. Queda esa dependencia entre bloques. Los IC no incluyen el costo del deslizamiento ni la selección de finales.

## 6. Refutación independiente y registro de pruebas

| Control preregistrado | Resultado |
|---|---|
| A1. Identidad de las estrategias | Parcial. Se verificaron las reglas del código y el historial de nombres. Falta la configuración efectiva del período alegado. |
| A2. Cobertura y relojes | Se inventariaron toda la cinta de descubrimiento, los mercados, la disponibilidad y las pausas entre cambios. La cobertura de todas las ofertas de la casa sigue abierta. |
| A3. Del libro al boleto | 782 coincidencias de cuota y aviso con id. Falta el cruce con el boleto del operador. |
| A4. Liquidación y casos faltantes | 771 entradas con final corroborado entre las 896 aperturas del panel. Disponibles cotas extremas y motivos de exclusión. Reglas de la cuenta y retiros sin cotejar. |
| A5. Under comparable | Comparación descriptiva por motor y conjunto, con IC pareados por día. El incremento ejecutable sigue sin identificar. |
| A6. Disponibilidad de variables | Contraejemplos automáticos de precio y final futuro, formato/conflictos y precio bloqueado. El z histórico no tiene trazabilidad suficiente en los archivos actuales. |
| A7. Banca y orden de liquidación | Se midieron racha, días y caída de papel sobre el subconjunto observable. La caída real queda abierta. |

Siete controles de calidad realizados. Siete hipótesis de mejora registradas, **cero probadas** y **cero candidatas promovidas**. No hay familia de p-valores de mejora sobre la que aplicar Holm en Fase 0. Los intervalos son descriptivos y el control múltiple está previsto para la evaluación futura. El auditor revisó el protocolo y los scripts. Sus objeciones sobre la coincidencia de identidades y sobre empates entre distintas cotizaciones con el mismo reloj se atendieron con pruebas de regresión. [Informe del auditor](00_refutacion.md) · [Libro de hipótesis](hypotheses.md) · [Criterios de aceptación](acceptance.md) · [Registro de acceso](access_log.csv)

## 7. Decisión y condiciones para reanudar

**Detener en Fase 0.** Quedaron sin reproducir el historial exacto de la cuenta, la liquidación contractual de las excepciones y la ejecución. Por ello no se ajustó ningún oráculo ni un modelo de probabilidad, no se buscaron filtros, no se optimizó el portafolio ni se calibraron detectores. Los techos de PRE, SET1, SET2 y del conjunto, las brechas, y una rentabilidad futura de cualquier mejora quedan **no verificados**. El error medio de la línea y el ROI de descubrimiento no son cotas superiores.

Para cerrar Fase 0 hace falta identificar el período y el origen de las cifras históricas, conciliar las alertas de ese período con los boletos aceptados, y resolver con evidencia las excepciones de liquidación. Con ello se reconstruye el dinero disponible en cada instante y se vuelve a correr la comparación de A3, A4 y A7. El siguiente paso sería sellar una captura prospectiva de cuotas vistas y aceptadas, rechazos y tiempos. H1–H7 sólo pasan a pruebas tras ese cierre. El tamaño mínimo futuro se fijaría con la variabilidad **diaria ejecutada**, la menor mejora de interés y la potencia prevista. Hoy esas entradas siguen sin determinar. Confirmación y ciego permanecen reservados.

**Preguntas para el dueño**

1. ¿Qué fechas exactas, qué pantalla o exportación, qué ramas PRE/SET2 y qué zona/hora de corte generaron 16,6 %, 19 % y «todos los días positivos»?
2. Para esa ventana, ¿hay un historial anonimizado con hora de colocación y liquidación, línea y cuota aceptadas, stake y abono, más los intentos rechazados que se hayan registrado? Para el contraste de descubrimiento basta el período hasta el 10 de noviembre. No se requieren claves ni registros posteriores guardados aparte.
3. ¿Qué dominio y jurisdicción regían la cuenta entonces, y qué boletos de ese mismo período documentan retiro, walkover o interrupción? ¿Qué fechas habían sido consultadas al seleccionar los filtros anteriores?

[Reproducción completa](REPRODUCIR.md) · [Tablas detalladas](00_tablas.md)
