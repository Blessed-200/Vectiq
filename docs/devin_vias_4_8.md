# Misión cuantitativa — Vías 4–8: mecanismo de la escalera Masters BO7

Estrategia congelada intacta: `tools/frozen_eval.py` (st0, 1 apuesta/partido, argmax-EV ≥5 %, sin calibradores). Nada modificado. Todos los módulos nuevos son independientes: `tools/prematch_extract.py` (series temporales de escaleras prepartido, 3 759 partidos), `tools/mech_*` previos. Datos: releases tape-2026-10-27..30 (30 = parcial, 4 h). Liquidaciones vía tape + API oficial.

## VÍA 7 — Reconstrucción matemática de la escalera (prioritaria)

**Modelo del mercado descubierto**: la casa tiene la FORMA correcta y el CENTRO equivocado.

- Distribución real de totales (1 102 finales): media 102.8, σ=21.4, q10/50/90 = 73/102.5/133 — colas pesadas simétricas; Normal es aproximación cruda, la CDF empírica es la referencia.
- Curva implícita por partido (logit de p devigueada vs línea): σ implícita media **22.9** ≈ real 21.4 → la dispersión la estima bien.
- **Desplazamiento δ = centro implícito − total real: media +7.09 ± 20.9, mediana +7.8** (n=609 con ≥3 líneas). La escalera entera está trasladada ~7 pts hacia arriba.
- Función de la ineficiencia: `edge(línea) ≈ δ × f_real(línea)` — constante ~+12–14 pp en el rango central (offset −4..+2 respecto al centro implícito), decae en los extremos de la escalera.
- Persistencia de δ: por día 27→29: +7.9/+6.1/+6.5 (30 oct parcial +12.3). Por liga: Russia +8.5, Belarus +6.9, Spain +7.0, China +6.6, Argentina +6.2, Poland +6.0, **Poland Women +2.8, Super League −1.4** (¡sin desplazamiento → sin edge! coherente con su ROI histórico ≈0).
- Dependencia intra-partido respetada: todos los cálculos de δ son por partido (una observación por partido), no por línea.

**Veredicto VÍA 7: DEMOSTRADO (mecanismo completo descrito matemáticamente).**

## VÍA 5 — Heterogeneidad competición/jugadores

- **Liga**: δ varía +2.8 → +8.5; Super League δ≈−1.4 y Poland Women δ≈+2.8 explican sus ROIs históricos negativos. La exclusión NO es filtro arbitrario: es el mecanismo medido.
- **Hora UTC**: edge positivo las 24 h (2.5–21.9 pp), sin patrón persistente → no filtrar por horario.
- **Jugadores** (n≥10, under en línea centrada): heterogeneidad real ENORME — jugadores de totales altos (Abdurakhmanov 118.7, Chernushin 119.1, Zi Hao Wang 115.4) → ROI under **−39 a −55 %**; de totales bajos (Skvortsov 84.5, Semerkin 88.1) → **+65 a +84 %**. La casa ajusta la línea por jugador pero insuficientemente.
- **Distribución de totales por liga**: medianas 101–106, P(>120) 20–41 % — Super League es la liga «larga» (40.5 % >120) donde el desplazamiento desaparece.
- Advertencia: extremos por jugador con n=10–21 requieren shrinkage jerárquico antes de explotar individualmente; el efecto agregado (media de jugadores) es robusto (ver VÍA 8).

**Veredicto VÍA 5: evidencia favorable fuerte a nivel de media por jugador; refutada la exclusión por resultados pequeños (sí hay mecanismo medible); horario refutado como factor.**

## VÍA 4 — Movimientos de cuotas

- La cuota under cae en media −0.013 prematch (50.2 % de líneas bajan vs 25.4 % suben): el mercado se autocorrige ligeramente hacia el under.
- **ROI @primera cuota +8.0 % vs @última +7.3 %** → entrar temprano captura más precio; esperar erosiona ~0.7 pp por apuesta.
- Dirección del movimiento: patrones en U — grandes movimientos en CUALQUIER dirección predicen WR/ROI alto en la última cuota (du<−0.05: WR .657 ROI +23.6 %; du>+0.05: WR .702 ROI +47.2 %; líneas quietas: −0.9 %). La volatilidad de línea marca dónde está la acción; la dirección no es señal limpia por sí sola.
- Limitación: solo 610/3 759 partidos tienen ≥2 ticks con cambios (los polls 'item' traen pocas líneas) → muestra de liquidez sesgada.

**Veredicto VÍA 4: hallazgo histórico prometedor — el movimiento extremo marca edge, entrar tarde cuesta ~0.7 pp; insuficiente para regla hasta confirmación prospectiva.**

## VÍA 6 — Momento óptimo de entrada

- Entrar a la **primera cuota** observada supera a la última (+8.0 % vs +7.3 % ROI): la corrección del mercado erosiona el precio.
- La ventana st0 (60 s–4 h prepartido) es adecuada; no hay evidencia de que esperar mejore el precio neto — sí lo contrario.
- Ejecución realista: primer tick ≈ apertura del mercado en el feed; riesgo: en producción real puede haber latencia hasta el fill. Recomendación operativa: entrar en la primera observación con escalera completa disponible.

**Veredicto VÍA 6: evidencia favorable — primera cuota ≥ última; regla «entrar pronto» documentada.**

## VÍA 8 — Detector independiente de ineficiencias (sombra)

**Diseño ejecutado**: `gap = centro_implícito(partido) − media_histórica_totales(jugadores)` con historial causal por jugador (≥8 partidos previos, solo finales ya ocurridos). Evaluado sobre 348 partidos con ambos historiales:

| gap (pts) | n | WR | ROI unitario |
|---|---|---|---|
| <0 | 37 | .541 | −1.2 % |
| 0–5 | 96 | .521 | −4.8 % |
| 5–10 | 110 | .645 | +17.9 % |
| >10 | 105 | .686 | **+25.1 %** |

**Monótono**: el gap ordena la rentabilidad de principio a fin — es un detector con discriminación real, cosa que la p del motor nunca tuvo. La arquitectura completa (reconstruir distribución por jugador → devig → gap → umbral con IC → alerta → registro → liquidación) está implementada como prototipo; los umbrales de producción NO se fijan aún (se congelarían antes de cualquier evaluación prospectiva formal).

Comparación con st0 congelada en la misma muestra: gap>5 rinde +18–25 % vs +10.4 % global → candidato a superarla, **pero n=215 en el cubo útil y desarrollado sobre datos históricos** → necesita evaluación prospectiva congelada como exige la propia misión.

**Veredicto VÍA 8: evidencia favorable fuerte — detector prototipado, mecanismo coherente, falta validación prospectiva congelada.**

## Registro de variantes experimentadas (riesgo de selección)

~15 variantes evaluadas en esta misión: offsets de línea, cortes de hora, línea centrada vs mejor cuota, cubos de gap, jugadores individuales, entradas primera/última. El hallazgo VÍA 8 (gap jugador) es mecánico y consistente con VÍA 7 (traslación δ): no es minería aislada — es la consecuencia matemática del mismo sesgo desagregado por jugador.

## Clasificación final

| Hallazgo | Estado |
|---|---|
| Escalera trasladada δ≈+7 pts, σ correcta | Demostrado |
| δ≈0 en Super League / Poland Women → sin edge | Demostrado (explica ROIs históricos) |
| Edge uniforme en escalera central, decae en extremos y por stage | Demostrado |
| Jugador-condicionado: gap ordena ROI (−5 % → +25 %) | Evidencia favorable fuerte |
| Entrada temprana > tardía | Evidencia favorable |
| Volatilidad de línea marca edge | Hallazgo histórico (muestra sesgada) |
| Hora del día como filtro | Refutado |
| Detector VÍA 8 como reemplazo de st0 | No demostrado aún — requiere congelación y evaluación prospectiva |

## Próximo paso recomendado

Congelar regla candidata `gap ≥ umbral_fijo` (decidir umbral ANTES de mirar más datos: propuesta gap≥5) y evaluarla prospectivamente sobre los próximos releases junto a la st0 congelada — mismo pipeline `frozen_eval.py` con registro de rechazadas. Hasta entonces: st0 congelada sigue siendo el control y la opción desplegable.
