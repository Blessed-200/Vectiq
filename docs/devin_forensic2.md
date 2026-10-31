# Segunda investigación — Estrategia «Devin»: mecanismo, correcciones y validación prospectiva

Código reproducible: `tools/mech_extract.py` (extrae escaleras G17 completas + finales reales por ventana de entrada, 9 639 ventanas / 7 869 con final), `tools/mech_analyze.py` (probabilidad real vs implícita por línea/stage/día), `tools/devin_audit2.py` (correcciones metodológicas), `tools/backtest.py` (replay causal). Datos nuevos: release `tape-2026-10-30` (parcial, horas 00–02 UTC) + liquidación de 31 partidos censurados vía API oficial de resultados → **validación prospectiva real sobre datos que la estrategia nunca vio**.

---

## 1. Auditoría corregida

### 1.1 Calibration slope — interpretación corregida
- Valor anterior reportado: 0.444. Valor corregido con muestra completa liquidada por API (n=1 125): **slope=0.516, intercept=−0.000, IC95% cluster-boot = [−0.359, +1.395]**.
- Interpretación correcta: slope<1 significa que las p̂ son demasiado extremas en proporción a la señal real (para calibrar habría que encogerlas hacia la media), **pero el IC incluye 1.0 → no hay evidencia firme de dirección de miscalibración**. Con un rango de p̂ de solo 0.20 el contraste tiene poca potencia. Se retira la afirmación «comprimidas 2.2×»: no sustentable.

### 1.2 Descomposición de Brier — identidad verificada
- Brier individual medio = **0.24989**; descomposición UNC−RES+REL = 0.24953−0.00100+0.00167 = **0.25019**. Diferencia = −0.0003 (error de bineado aceptable, <0.15 %). La identidad se aplica correctamente; la descomposición es válida.
- Resolución 0.0010–0.0016 sigue siendo ≈0: el modelo no discrimina resultados por encima de la tasa base.

### 1.3 Bootstrap línea≥108.5 — corregido
- **Bug anterior**: excluía cualquier partido con apuestas fuera de la máscara (clusters parcialmente elegibles descartados).
- Corregido (remuestreo por partido, dentro de cada partido solo apuestas elegibles): n=268 (antes 231), ROI **+4.7 %** (antes +6.7 %), IC95% **[−7.0, +16.5]**, P(ROI>0)=**79.2 %**. El IC sigue incluyendo 0.

### 1.4 Dependencia temporal — qué se puede y qué no inferir de 3 días
- ROI unitario por día: +8.4 % / +5.7 % / +5.4 % (valores corregidos tras liquidar censurados por API).
- Bootstrap por días (recombinar los 3 días enteros): P(ROI>0)=100 % **pero es tautológico** — solo recombina los 3 días observados, no puede representar días no vistos. Con n_días=3 **no se puede estimar varianza inter-día, drift ni estacionalidad** con potencia alguna. Todo estimador de «probabilidad futura» basado solo en estos días es una cota optimista.

### 1.5 Probabilidad de rentabilidad futura — capas separadas
| Hipótesis | P(ROI>0) | IC95% ROI | P(ROI>5%) | P(ROI>10%) |
|---|---|---|---|---|
| iid apuestas | 98.2 % | [+0.4,+12.4] | — | — |
| cluster partido | **97.4 %** | [−0.1,+12.8] | 67.0 % | 14.0 % |
| cluster día (3 obs.) | 100 % | [+5.4,+8.4] | 100 % | 0.0 % |
| jerárquico día→apuesta | 98.4 % | — | — | — |

- La diferencia entre filas ES la incertidumbre: P(ROI>10 %) varía de 0 % a 14 % según la estructura de dependencia asumida. Ninguna captura «la ineficiencia desaparece» — para eso hace falta tiempo.
- Posterior Beta sobre WR: posterior ≠ predicción futura. El posterior responde «¿el WR histórico supera el breakeven?» (99.3 %); la predicción futura además exige estacionariedad, no evaluable con 3 días.

### 1.6 Riesgo — categorías separadas
- **Pérdida extrema** (peor apuesta): −1 unidad.
- **Drawdown histórico** (stake plano): 37 u de MDD = 3.3 % del turnover; con stake fraccional dinámico la curva de 3 días dio MDD 30.6 %.
- **Ruina operativa** (equity <20 % del pico inicial): MC 20k×30d, 55 apuestas/día, stake 2.5 % → <0.1 %.
- **Pérdida total**: con stake proporcional es matemáticamente inalcanzable (equity asintótica); el riesgo real es el drawdown profundo: MDD mediana 46 %, P(MDD>50 %)=48 % a 30 días.

### 1.7 Reproducibilidad de cálculos previos
- ROI ponderado: recomputado — consistente (+12.3 % stake-ponderado vs +7.5 % unitario; la diferencia es el Kelly por EV, no un error).
- CLV: verificado contra `close_odds` del tape (+4.64 %). Calibradores: reproducidos (isotónica −8.7 % OOS). Barrido de umbrales: reproducido completo en §3.

---

## 2. El mecanismo de la ventaja — investigado, no asumido

**Setup**: para cada partido Masters bo7 finalizado se extrajo la escalera G17 COMPLETA en cada ventana elegible (st0 pre-match, descansos st1–3): cada línea con cuotas over/under + total final real. Esto da P(under | línea, stage) verdadera vs la probabilidad implícita devigueada del mercado, sobre ~2 000 líneas evaluadas por línea.

### 2.1 El sesgo es estructural y cubre TODA la escalera (st0)

| Línea | n | P(under) real | P implícita | edge (pp) | cuota under media |
|---|---|---|---|---|---|
| 102 | 52 | .615 | .442 | **+17.4** | 2.08 |
| 104 | 290 | .562 | .429 | **+13.3** | 2.14 |
| 106 | 904 | .530 | .435 | **+9.5** | 2.11 |
| 108 | 965 | .588 | .470 | **+11.7** | 1.95 |
| 110 | 1032 | .624 | .503 | **+12.1** | 1.82 |

**La ineficiencia no vive en un umbral — el mercado subprecia el under en toda la escalera.** Las líneas quedan sistemáticamente por encima del total real: media(línea−final)=+2.5 pts en línea 106 → +7.2 pts en línea 110. La casa «empuja» la escalera ~5–7 pts arriba de la distribución verdadera pero mantiene precios casi centrados (under implícita .43–.50, overround ~9.3 % parejo en ambos lados).

### 2.2 Decae con el estado del partido (el mercado se corrige)

ROI unitario apostando under en **todas** las líneas disponibles por ventana:

| Stage | n | ROI |
|---|---|---|
| st0 (pre-match) | 3 243 | **+14.1 %** |
| st1 | 4 467 | +11.1 % |
| st2 | 4 505 | +10.2 % |
| st3 | 4 260 | +6.3 % |

El edge es máximo en pre-match y se erosiona en vivo: el trading in-play de la casa recalibra mejor que su precio de apertura.

### 2.3 Dónde NO hay edge

En el extremo alto de la escalera in-play (líneas ≥116–118 en st1–st3) el edge se desvanece a +3 pp o menos — ahí la cuota under (≈1.89) ya descuenta el sesgo. El sesgo no es monótono infinito: es una traslación de la escalera, no una pendiente.

### 2.4 Estabilidad por día (edge medio st0)

27 oct +14.1pp | 28 oct +9.3pp | 29 oct +10.4pp | 30 oct (parcial) +20.2pp — positivo los 4 días; el 28–29 fue más flojo (ese es el «drift» observado en ROI).

### 2.5 Veredicto sobre el origen de la ventaja

La ventaja proviene de una **característica estructural del mercado**: en Masters bo7 la casa coloca la escalera del total desplazada +5–7 pts respecto a la distribución real de totales, sin ajustar los precios lo suficiente. No es selección de oportunidades ni el modelo: **apostar under en todas las líneas rinde lo mismo (+8.1 % global, +14.1 % st0) que la selección del motor (+7.5 % unitario)**. La selección del motor apenas añade valor sobre una regla trivial — y su cap EV≤15 % de hecho excluyó las líneas más mal preciadas (102–104, edge +13–17 pp, cuotas ~2.1 → EV real +20–28 %, filtradas por exceso de EV).

## 3. Investigación de la probabilidad — por qué la resolución es ≈0

- La p del motor es una CDF empírica evaluada en (línea−jugados): es **mecánicamente monótona en línea** dentro de un partido, así que sí ordena líneas intra-partido. La resolución≈0 viene de que entre partidos el p̂ apenas varía (rango 0.20, mediana .534) porque el sesgo es uniforme — **no hay señal condicional que extraer**: P(under|línea) ya está casi determinada por la línea, y el estado del partido añade poca discriminación adicional.
- Conclusión: no es un fallo del estimador sino del **objetivo**. El problema útil no es «predecir mejor el total» sino «detectar dónde el mercado desplaza la escalera». Cualquier arquitectura nueva debe evaluarse contra «P implícita vs P real por línea», no contra Brier de selección.
- Hipótesis de arquitectura alternativa (NO implementada, a evaluar independientemente): estimar directamente `edge(línea, stage, liga) = P_real − P_implícita` con un modelo jerárquico por liga, y apostar donde edge·cuota > umbral. Baseline de comparación obligatorio: la regla trivial «under en st0».

## 4. Frontera volumen-rentabilidad (stake plano, reglas sobre la escalera completa)

| Regla | n/día | ROI | por día (27/28/29/30p) |
|---|---|---|---|
| Original (mejor EV en [5,15 %]) | ~345 | +7.5 % | +8.4/+5.7/+5.4/(+37.2∗) |
| Blanket under, todas las líneas | ~5 530 | +8.1 % | +11.6/+4.6/+6.6/+31.2 |
| Una apuesta por ventana, mejor cuota | ~1 967 | +7.3 % | +10.4/+3.3/+6.5/+32.8 |
| Una apuesta por ventana, línea máx | ~1 967 | +6.4 % | +9.9/+3.1/+4.8/+28.9 |
| **st0 solo, 1 pick/partido, mejor cuota** | **~276** | **+14.4 %** | **+19.6/+8.8/+13.5/+36.3** |
| st0 solo, 1 pick, línea máx | ~276 | +13.4 % | +19.3/+9.4/+10.8/+27.3 |
| st0 solo, 1 pick, cuota~2.0 | ~276 | +14.2 % | +17.5/+12.1/+11.9/+31.4 |
| Original filtrado ≥108.5 | ~89 | +4.7 % | IC95% [−7.0,+16.5] |

∗ día 30 = solo 3 h de tape (00–02 UTC), n=34 liquidadas.

**Lectura**: la selección por línea apenas importa (todas las variantes st0 rinden ~+13–14 %); la selección por **stage** sí (st0 ≫ st3). La reducción de volumen óptima no es un umbral de línea sino **limitarse a pre-match**: mismo edge con menos ruido y menos apuestas por partido. Nota de correlación: «todas las líneas» multiplica apuestas sobre el mismo resultado — el ROI se conserva pero la varianza intra-partido crece; la recomendación es 1 pick por partido en st0.

## 5. Validación prospectiva (reglas congeladas, tape-2026-10-30, 3 h)

Estrategia congelada = Masters-Under tal como quedó el 29 oct; nada se reajustó tras ver el 30.

| Cohorte | n | WR | ROI |
|---|---|---|---|
| Masters-Under (todo) | 34 | 64.7 % | **+37.2 %** |
| st0 | 22 | 72.7 % | **+55.6 %** |
| st1+ | 12 | 50.0 % | +4.4 % |
| línea≥108.5 | 3 | 33.3 % | **−31.3 %** |

- El edge del segmento **se confirma prospectivamente** (n pequeño: 3 h de tape; la evaluación completa requiere el día entero).
- El filtro ≥108.5 **falla prospectivamente** — n=3 insuficiente para refutar estadísticamente, pero consistente con el mecanismo (edge uniforme, no en el umbral).
- Advertencia: la ventana 00–02 UTC concentra ligas concretas; no representa el día completo.

## 6. Tabla de decisión actualizada

| Componente | Veredicto | Evidencia |
|---|---|---|
| Edge Masters-Under (segmento) | **Demostrado** (+ prospectivo: +37 % n=34) | edge +9–17pp en toda la escalera, 4/4 días positivos, CLV +4.6 % |
| Mecanismo | **Demostrado**: traslación sistemática +5–7 pts de la escalera vs distribución real; decae st0→st3 | medido sobre 7 869 ventanas, no inferido |
| Regla ≥108.5 | **Refutada como filtro** | IC incluye 0; prospectivo −31 %; mecanismo muestra edge uniforme (en líneas bajas incluso mayor) |
| Ranking por EV/p | **Refutado** | resolución≈0; blanket ≈ selección |
| st0-only (1 pick/partido) | **Evidencia favorable** | +14.4 % ROI, 4/4 días+, mitad de volumen; falta validación de día completo |
| Cap EV≤15 % | **Contra-productivo**: excluye las líneas más mal preciadas | recomputado sobre escaleras |
| Calibradores | **Refutados como mejora** | isotónica −8.7 % OOS |
| Slope calibración «0.44» | **Corregido**: 0.52, IC [−0.36,1.40] — sin evidencia firme de dirección | incluye 1.0 |
| P(ROI>0 futuro) | 97–98 % bajo iid/cluster; **no interpretable como futuro** sin más días | la incertidumbre estructural (días no vistos) no está en el modelo |
| 100×/30d | **Refutado** (~3 %, mediana ~12×) | MC dependiente |

## 7. Lo que sigue siendo incierto y la próxima prueba

- Día 30 incompleto (3 h): esperar release completo y re-correr `mech_extract` + `backtest` — día prospectivo íntegro.
- 5 partidos del 30 siguen sin final en la API (aún en juego o sin publicar).
- Para declarar «st0-only» como mejora permanente hace falta ~1 semana prospectiva con la regla congelada hoy — ningún ajuste posterior.
- Hipótesis abierta: ¿por qué la casa no corrige? Posible respuesta: volumen bajo en Masters bo7 — mercado de nicho donde el coste de calibrar supera el vig perdido. No verificable desde el tape.
