# Fase 1 · Acuerdo de investigación

## 1. Resumen para el dueño

El estudio sigue las señales ST0, ST3 y ST5 desde el primer día completo de cinta.  
El 16,6 % fue una cifra recordada y carece de una fecha de corte.  
Compararemos el precio que apareció, el que se volvió a ver unos segundos después y situaciones de Under comparables.  
La decisión sobre cada regla exige ventajas en dos periodos posteriores y suficiente estabilidad.  
Se conserva como evidencia negativa cualquier candidato fallido y se mantiene un solo acceso final.

## 2. Datos y tiempos

El manifiesto previo research/sources.json identifica los archivos por SHA-256. El calendario inmutable en research/split.json delimita descubrimiento del 23 de octubre al 10 de noviembre de 2026, confirmación del 11 al 16 de noviembre y ciego del 17 al 22 de noviembre, todos inclusive en UTC. Las tres primeras horas disponibles, de 21:00 a 23:59 del 22 de octubre, sólo inicializan las estadísticas. Se conserva el corte de la primera auditoría. El propietario confirmó que carece de un histórico de tickets y que sus porcentajes eran aproximaciones. Esta aclaración permite medir señales históricas en la cinta y estudiar explícitamente el coste de diferencias de cuota.

## 3. Libro de pruebas y criterios

| Grupo | Número | Documento que fija su interpretación |
|---|---:|---|
| Mecanismos | 7 | hypotheses.md original y policies_protocol.md, H1–H7 |
| Políticas de entrada o stake | 6 | policies_protocol.md, P1–P6 |
| Sustitución de probabilidad | 1 | structural.py, calibración simétrica y aceptación contra dos referencias |
| Diagnósticos secundarios | Sin selección de reglas | Oráculo con dos coberturas, residuos y sondeo de precios, simulaciones de cartera y vigilancia |

Las puertas de decisión están en acceptance.md de esta carpeta. Se declara una sola especificación por hipótesis. Cada comparación adicional de sensibilidad lleva esa etiqueta; una simulación de riesgo no cuenta como una mejora verificada.

## 4. En palabras sencillas

Subir los aciertos sólo quitando casi todas las apuestas puede dejar menos dinero y una evidencia más débil. Por eso la comparación conserva el recuento de entradas, el acierto, el dinero ganado por unidad arriesgada y la caída de la banca.

## 5. Límites conocidos de antemano

Confirmación y ciego duran seis días cada uno y corresponden a noviembre. No pueden demostrar recurrencia en dos meses por ventana. Los umbrales comerciales se editaron durante el periodo que hoy se reserva; esto limita la independencia histórica para validar la regla vigente. El precio aceptado, los límites personales y los anulados por la casa siguen sin identificación.

## 6. Refutación

La revisión independiente examina el riesgo de seleccionar una regla conocida por el histórico, la censura de partidos, la disponibilidad de precios y el número de días independientes. Las fechas y los criterios se congelan antes de abrir confirmación. El detalle técnico de objeciones se acumula en 08_refutacion.md.

## 7. Decisión

Continuar el estudio sobre ofertas históricas bajo estas condiciones. Ante un fallo técnico que afecte la integridad de los precios, la partición o el acceso único, se conservará el último tramo íntegro y se informará exactamente qué conclusiones quedan abiertas.
