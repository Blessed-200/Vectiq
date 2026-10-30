# Auditoría forense — Estrategia «Devin» (Masters bo7, Total UNDER)

Reconstrucción completa del procedimiento. Todo lo reproducible está en `tools/backtest.py` (generación de apuestas) y `tools/devin_audit.py` (todas las cifras de este documento). Datos: `bt3.json` (apuestas OOS), `finals.json` (finales de la API oficial de resultados para partidos censurados).

## 1. Reconstrucción forense — datos exactos

| Ítem | Valor |
|---|---|
| Registros tape procesados | 1 642 912 (8 releases `tape-2026-10-22..29`, 202 shards .zst) |
| Partidos observados | 14 323 |
| Periodo de diseño/entrenamiento | 22–26 oct 2026 (solo alimenta libros/Elo) |
| Periodo de evaluación (ciego) | 27–29 oct 2026 |
| Apuestas candidatas estrategia (Masters-Under, st0+st1) | 1 119 |
| Liquidadas con final real | 1 035 (947 partidos únicos) |
| Void sin final ni API | 84 |
| Ganadas | 545/1035 (WR 52.66 %) |
| Tamaño efectivo de muestra | ≈947 (m̄=1.09 apuestas/partido, DEFF 1.09) |
| Convención de liquidación | Under gana iff total_final < línea; push si igual (0 observados) |
| Censuradas | liquidadas por `/service-api/result/web/api/v3/games` (167 recuperadas); las que el servicio no tenía quedan void (excluidas) |
| Cuotas | rango 1.70–2.43, media 2.030; líneas 97.5–123.5, media 106.4 |
| Stake simulado | Kelly 0.5 con shrink 0.5, cap 1 %/apuesta, 2 %/partido |

Observado vs estimado: «WR 52.7 %», «ROI +7.5 % unitario», «+12.3 % ponderado» son observados. «P(ROI>0)» es estimación estadística bajo supuestos declarados. «~10×/30d» es simulación Monte Carlo, no historia.

## 2. Calibración — estudio completo

### 2a. Reliability (deciles de p̂ → WR observado, IC95% Wilson)

| Bin p̂ | p̄ | WR | IC95% | n |
|---|---|---|---|---|
| .448–.509 | .501 | .548 | [.452,.640] | 104 |
| .509–.520 | .515 | .553 | [.457,.646] | 103 |
| .520–.524 | .521 | .510 | [.415,.604] | 104 |
| .524–.528 | .526 | .466 | [.373,.562] | 103 |
| .528–.534 | .531 | .456 | [.363,.552] | 103 |
| .534–.539 | .537 | .558 | [.462,.649] | 104 |
| .539–.544 | .542 | .500 | [.405,.595] | 102 |
| .544–.557 | .549 | .529 | [.434,.622] | 104 |
| .557–.608 | .581 | .587 | [.490,.676] | 104 |
| .608–.644 | .615 | .558 | [.462,.649] | 104 |

Lectura: NO monótona (caída a .456 en el centro); ICs ±9–10 pts → no se puede ordenar por p.

### 2b. Brier + descomposición de Murphy

`BS = UNC − RES + REL`, 10 bins:
- Modelo: **0.2496** = 0.2493 − 0.0016 + 0.0019
- Mercado (p=1/cuota, con vig — limitación): **0.2505** = 0.2493 − 0.0009 + 0.0023
- **Resolución ≈0.002**: el modelo no discrimina casi nada por encima de la tasa base.

### 2c. Log Loss

Modelo **0.6924** | mercado 0.6941 | tasa base 0.6917 → el modelo no mejora al prior global en scoring; la utilidad viene de la SELECCIÓN de segmento, no del score.

### 2d. Calibration intercept/slope (logit)

y ~ logit sobre logit(p̂): intercept **+0.032** (≈0), slope **0.444** — las p están comprimidas ~2.2× respecto a lo ideal (la varianza de p̂ es demasiado baja para la señal real).

### 2e. Por segmento/día

| Segmento | n | WR | Brier | Res | Rel | LogLoss |
|---|---|---|---|---|---|---|
| st0 (pre) | 521 | .545 | .2478 | .0038 | .0035 | .6886 |
| st1 (descanso) | 514 | .508 | .2515 | .0028 | .0048 | .6963 |
| día 27 | 329 | .532 | .2499 | .0049 | .0062 | .6929 |
| día 28 | 360 | .522 | .2480 | .0120 | .0105 | .6890 |
| día 29 | 346 | .526 | .2512 | .0113 | .0133 | .6956 |

La resolución crece por día — artefacto de mezcla, no mejora real.

### 2f. Distribución (sharpness)

p̂ ∈ [.448,.644], mediana .534, rango 0.197. Mercado (1/odds) mediana .482. El modelo solo emite p en una banda de ±0.10 — no produce sharpness útil.

### 2g. Calibradores comparados (walk-forward, sin fuga: ajuste en días previos, evaluación en días posteriores)

| Ajuste→Eval | Método | Brier | keep EV≥5 % | ROI mantenido |
|---|---|---|---|---|
| 27→28+29 | raw | .2495 | 706/706 | +6.6 % |
| | isotónica | .2499 | 498 | +5.5 % |
| | Platt | .2495 | 564 | +6.0 % |
| 27–28→29 | raw | .2512 | 346 | +7.0 % |
| | isotónica | .2585 | 213 | **−8.7 %** |
| | Platt | .2494 | 230 | +10.5 % |

Isotónica sobreajusta (peor OOS, ROI negativo). Platt neutro. Beta/temperature/Bayesiano: **no evaluados** — se declara explícitamente; hipótesis débil dado que el problema es resolución, no forma de la curva. **Decisión: no calibrar — la falta de edge-ranking no la arregla un calibrador.**

### 2h. Conclusión del especialista

El motor produce una probabilidad útil SOLO como indicador de segmento («esta familia de líneas está mal preciada»), no como estimador ordenable. La ventaja es estructural (sesgo de nivel ~6 pts en Masters bo7), capturada por la distribución empírica de puntos restantes — no por precisión relativa. Métrica agregada que lo ocultaba: el ROI positivo convive con resolución ≈0.

## 3. Auditoría del umbral línea ≥108.5

**Cómo se encontró:** transparencia total — probé ~6 reglas de ranking (EV, p, EV·shrink, Sharpe, línea, stage) × cortes top-N {25,40,60,80} y luego un barrido de umbrales 103.5–110.5 (8 valores). Es decir, **~30 variantes evaluadas sobre los mismos 3 días → riesgo real de selección retrospectiva**; la corrección honesta es exigir validación prospectiva, no más historia.

**Barrido completo (unit ROI, sin cap):**

| Umbral | n/día | WR | ROI | d27 | d28 | d29 |
|---|---|---|---|---|---|---|
| ≥103.5 | 313 | .520 | +5.6 % | +5.9 | +4.7 | +6.3 |
| ≥104.5 | 258 | .528 | +6.1 % | +9.4 | +7.8 | +1.7 |
| ≥105.5 | 199 | .533 | +5.4 % | +9.9 | +9.0 | −2.4 |
| ≥106.5 | 133 | .555 | +7.4 % | +10.6 | +19.4 | −6.2 |
| ≥107.5 | 90 | .568 | +7.4 % | +13.3 | +10.2 | +1.2 |
| **≥108.5** | **77** | **.567** | **+6.7 %** | **+6.6** | **+9.7** | **+4.3** |
| ≥109.5 | 65 | .588 | +9.3 % | −1.6 | +20.4 | +6.1 |
| ≥110.5 | 50 | .591 | +9.1 % | +1.7 | +22.8 | +2.9 |

**Bootstrap por cluster (20 000 réplicas):** ROI +6.7 %, IC95% **[−8.2, +18.2]**, P(ROI>0)=**76.5 %** — el intervalo incluye 0.

**Interpretación honesta:** existe mecanismo estructural plausible (línea alta → más margen frente al sesgo de ~6 pts), el ROI agregado sube con el umbral (r ≥0.9 de tendencia), pero día a día es volátil y el IC no excluye 0 → clasificación: **evidencia favorable, aún insuficiente**. No se convierte en filtro permanente: solo piloto A/B con tape nuevo.

## 4. Auditoría del «98.6 %»

**Modelo:** WR como Bernoulli iid por apuesta, prior Beta(1,1) (uniforme), likelihood binomial → posterior Beta(1+545, 1+490)=Beta(546,491). Breakeven a cuota media 2.047 ⇒ p*=0.4886.
- P(p > p*) = **99.3 %** (el 98.6 % anterior usaba p*=1/2.03 y Beta(545/490): misma cuenta, redondeo).
- IC95 % del WR: [.496, .557].

**Sensibilidad a supuestos:**
- Dependencia intra-partido: DEFF 1.09 → casi nula (1.09 apuestas/partido). El bootstrap por cluster es el estimador principal: P(ROI>0)=**98.5 %**, IC95% ROI [+0.8, +14.1].
- Censuradas: liquidadas con API real (finales verdaderos), las 84 sin dato quedaron fuera. Si TODAS las void restantes fueran pérdidas: WR baja a 545/1119=48.7 % ≈ breakeven — esa es la cola pesimista del supuesto de censura.

**Probabilidades separadas (como se pidió):**

| Probabilidad | Valor | Método |
|---|---|---|
| P(ROI futuro > 0) | **98.5 %** | bootstrap por partido del ROI medio |
| P(ROI futuro > 5 %) | 77.0 % | idem |
| P(ROI futuro > 10 %) | **22.8 %** | idem |
| P(WR > breakeven) | 99.3 % | posterior Beta |
| P(día positivo) | ~91 % | 10 000 días simulados, ~345 apuestas iid |
| P(mes positivo) | ≈100 % | mismo modelo, 10 350 apuestas — bajo iid; colas irreales |
| P(MDD30d > 20 %) | ~100 % | MC dependiente |
| P(MDD30d > 30 %) | 99.4 % | idem |
| P(MDD30d > 40 %) | 84.0 % | idem |
| P(MDD30d > 50 %) | 48.1 % | idem |

**Advertencia obligatoria:** el 98.5 % es posterior bajo estacionariedad iid. Con 3 días y drift visible (ROI/día 9.5→6.2→7.0 %) NO es una probabilidad de éxito futuro calibrada; la cola de «la ineficiencia desaparece» no está en el modelo. Datos necesarios para sustentar 70 % con honestidad: ≥10–14 días adicionales con la estrategia congelada.

## 5. Auditoría del riesgo

**Setup:** 20 000 trayectorias × 30 días × 55 apuestas/día (recomposición desde la empírica), stake 2.5 % dinámico, ruina = equity <20 % del inicial, MDD = máx(1−e/peak) por trayectoria.

| Modelo | P(100×) | P(pérdida) | P(ruina) | MDD med | MDD p95 | mediana 30d |
|---|---|---|---|---|---|---|
| iid por apuesta | 3.0 % | 0.6 % | <0.1 % | 46 % | 67 % | 12.6× |
| bloque por partido | 3.1 % | 0.7 % | <0.1 % | 46 % | 67 % | 12.7× |

La dependencia por partido casi no cambia nada (m̄=1.09). Lo que sí domina el riesgo es el **volumen** (1 650 apuestas/30d componiendo): aun con ROI+ el path cae ~46 % de media — por eso el drawdown histórico de 3 días fue 30.6 %.

**Corrección respecto al informe previo:** el «MDD 19.3 %» reportado antes era de la curva de 3 días sobre apuestas liquidadas en tape; el ~46–47 % es la expectativa a 30 días. No son comparables — declarado.

## 6. Tabla de decisión del colectivo

| Componente | Estado | Evidencia | Incertidumbre | Clasificación |
|---|---|---|---|---|
| Motor original (selección de segmento) | ROI unitario +7.5 %, stake-pond +12.3 % | IC95% [+0.8,+14.2], CLV +4.64 %, 62 % bate cierre | censura residual, 3 días | **Demostrado (a nivel de segmento)** |
| Calidad probabilística (p) | Resolución ≈0, LogLoss ≈ base-rate | deciles no monótonos, slope 0.44 | — | **Refutado como ranking** |
| Calibración (Platt/isotónica/…) | sin mejora OOS; isotónica negativa | walk-forward 27→29 | beta/temp. no evaluados | **Refutado como mejora** |
| Línea ≥108.5 | +6.7 %, positiva los 3 días | IC95% [−8.2,+18.2] incluye 0; ~30 reglas probadas | **Evidencia favorable, insuficiente** — piloto |
| EV como ranking | destruye ROI (top-60 +2.7 %) | — | **Refutado** |
| CLV | +4.64 %, 62 % beat-close | medición vs última cuota del tape | **Demostrado** |
| Crecimiento 100×/30d | P≈3 %, mediana ~12× | iid/bloque, stake 2.5 % | supuesto estacionario | **Refutado al edge actual** |
| ~10×/30d | mediana 12.6× | P(pérdida)<1 %, P(ruina)<0.1 % | idem | **Evidencia favorable** |
| Generalización (P futuro) | P(ROI>0)≈98.5 % iid; 76.5 % para ≥108.5 | drift diario; solo 3 días; subligas heterogéneas | **Evidencia favorable, insuficiente para 70 % en ROI>10 %** |
| Riesgo | ruina <0.1 %, MDDmed 46 %, P(MDD>50 %)≈48 % | 20k trayectorias | **Demostrado (el riesgo es profundo)** |

## 7. Lo que NO se hizo (declaración explícita)

- Beta calibration, temperature scaling, métodos bayesianos de calibración: no evaluados (hipótesis débil: el déficit es resolución).
- Modelos alternativos de probabilidad (jerárquico nuevo, Markov detallado por jugador): no entrenados — no se afirma que mejoren.
- Costes de ejecución/latencia/límites de stake reales: no medibles desde el tape.
- Purga/embargo entre días: no aplicable (sin solape de features entre días en este diseño; los libros se alimentan solo de partidos terminados).
- Validación en ≥2 semanas de tape: pendiente, es la prueba que falta para declarar el 70 %.

## 8. Datos adicionales necesarios

1. Más días de tape (los releases siguen publicándose): re-correr `tools/devin_audit.py` cuando haya 10–14 días OOS.
2. Finales de los 84 partidos void restantes (la API no los tenía) o confirmación de abandono.
3. Precios ejecutables reales (snapshot propio del book al momento de apostar) para medir deslizamiento vs cuota del tape.
