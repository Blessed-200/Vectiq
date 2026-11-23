# Contrato previo de auditoría

Fuente de código b706336b3c093daf3338ec501e20c8f27657b185, repo Blessed-200/Vectiq. Corte de fuentes por día de la cinta al 23 de noviembre de 2026 a las 00:00 UTC, equivalente a las 19:00 del día 22 en La Habana. Se excluye el primer día de captura, iniciado a las 21 UTC, y el día UTC en curso. Los días interiores cuentan incluso si tienen huecos. Las bases o pruebas reservadas por el dueño quedan fuera. Se consultaron código y metadatos para este registro. Algunos comentarios de código y el encargo contienen afirmaciones históricas, que no se usan como estimaciones.

## Partición y acceso

Dividir días de calendario UTC contiguos en 60/20/20 por límites acumulados, redondeando al entero más próximo. El universo incluye del 23 de octubre al 22 de noviembre de 2026. Descubrimiento hasta el 10 de noviembre inclusive, confirmación del 11 al 16, ciego del 17 al 22. Horas, nombres, tamaños y hashes externos son metadatos. Permitir únicamente la lectura semántica de descubrimiento en Fase 0. Separar partidos en la frontera por inicio/primera observación y descartar aperturas o desenlaces que requieran cruzar al tramo vecino. Si falta el inicio, declarar censura y el criterio operativo utilizado. No publicar una reproducción del total histórico antes del cierre final. Esta reserva protege esta investigación. La independencia del desarrollo anterior depende además de las fechas de elección de cada regla. Mantener registro de acceso por tramo.

## Comprobaciones registradas antes de rendimientos

| ID | Comprobación y mecanismo | Contraste previsto |
|---|---|---|
| A1 | Desplegar una estrategia diferente a la descrita cambia sus apuestas | Bo7/liga, fuente, mercados, filtros, orden de consumo del partido, fechas de código y configuración real |
| A2 | Los relevos o bloqueos del proveedor interrumpen la observación | Identificar fragmentos, cobertura por hora, huecos, latencia y cadencia medibles, entradas repetidas en relevos |
| A3 | La tabla de papel puede contar candidatas sin una apuesta aceptada | Libro por rama, filtros del panel, solapamiento por partido, filas de entrada frente a comprobantes verificables |
| A4 | La liquidación imputada o sin final observable cambia los retornos | Marcadores legales y unívocos del mismo partido en descubrimiento, sin propagar final posterior, voto del usuario o saldo simulado como recibo |
| A5 | El precio/linea ya explican parte de la señal | Medir control Under comparable con primera observación sin exigir señal en la misma etapa, liga, línea y precio. Informar cobertura, emparejamiento y diferencia pareada |
| A6 | Las fechas de conocimiento de una variable pueden ser posteriores a la alerta | Guardas de disponibilidad por fuente y resultado de cada partido anterior, corte alerta menos latencia y precios con vigencia; añadir contraejemplos sintéticos |
| A7 | El orden de apertura y una duración supuesta deforman las caídas y rachas | Etiquetar proxies de papel, calendario, pendientes, stake plano vs de libro y liquidaciones simultáneas; métricas reales solo con recibos |

No hay experimentos de mejora ni pruebas inferenciales de selección en esta fase. Estos siete controles de integridad no se cuentan como descubrimientos de alpha. H1–H7 quedan registrados aparte y no se evalúan mientras la Fase 0 esté abierta.

## Unidades y estimandos

Una observación principal por partido y motor, en orden causal del feed. En ST5 se distingue k=1 de k=2 y se respeta el consumo de una primera entrada de papel anterior incluso si no pasó el filtro de alerta. Separar la política congelada aplicada retrospectivamente del motor realmente disponible en cada fecha. Con fuerza de jugadores PIT o configuración histórica desconocida, publicar cobertura identificada, condición concreta e indeterminados. En particular, en ST0 se debe considerar si una rama anterior con z desconocido pudo consumir la entrada.

Para el control descriptivo de pizarra: primer precio visible por partido, etapa, línea exacta, cuota a céntimo; mismo universo geográfico, momento elegible y disponibilidad de escalera. Aplicar pesos de los estratos de las señales en soporte común a los controles, con todo partido elegible independientemente de si activó la señal. El precio se indexa por entero de céntimos (redondeo al céntimo más cercano). Cada control del estrato tiene el mismo peso dentro del estrato. Volver a calcular soporte y pesos en cada réplica diaria; si una réplica carece de soporte, dejarla sin estimar e informar cuántas tienen denominador. Informar denominadores, coincidencias, solapamiento y pérdida de soporte. Varias cotizaciones de un partido son oportunidades diferentes en este control estadístico, nunca múltiples apuestas acumuladas en una cartera. Denominar toda cifra de pizarra o papel como tal. Para la economía ejecutable exigir cotización vigente al ejecutar, aceptación, cuota y stake efectivos; la cuota de cierre solo sirve de referencia.

Ganadas y perdidas forman el denominador del acierto. Mostrar n, jornadas y cuotas, void/push/abiertas y finales ausentes por separado cuando la fuente lo permita. ROI plano = suma de retornos con stake unitario dividida por unidades arriesgadas de la convención descrita; publicar aparte ROI ponderado = suma PnL / suma de stakes dentro de la cobertura declarada. Contar cada pata de la cartera e informar partidos únicos. Bootstrap percentil de días UTC completos, 10.000 réplicas, semilla 20261122, conservar juntos todos los partidos y ramas de cada día, diferencias pareadas en el mismo remuestreo. No publicar WR, ROI ni Brier sin IC95 y n; un n=0 produce «no estimable». Bootstrap con pocos días es descriptivo; ni más simulaciones ni miles de ticks crean meses adicionales. Crecimiento, rachas, drawdown y días positivos exigen calendario/orden declarados. Sin hora bancaria de liquidación ni bankroll conjunto, sólo estimandos de simulación debidamente nombrados.

## Puerta de la fase

Detener antes de mejoras si la referencia o el universo ejecutable no se reproducen, existe fuga o hay divergencias sin resolver en resultados/liquidación. Terminar los diagnósticos de Fase 0 independientes posibles. Mantener cerrados ambos tramos siguientes al detenerse. El informe publicará evidencia faltante, pérdidas de cobertura y pruebas fallidas.
