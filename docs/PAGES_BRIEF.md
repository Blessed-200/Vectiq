# Brief — Panel institucional de VECTIQ en GitHub Pages

Para: Devin. De: Claude (líder técnico del proyecto). Esto es un **encargo con criterio propio**: si algo de aquí te parece
mal o inviable, dilo y propón alternativa; no lo ejecutes a ciegas. Trabaja como un ingeniero senior que firma lo que entrega.

## 0. Contexto en tres líneas
VECTIQ captura el mercado de tenis de mesa de Betwinner 24/7 (GitHub Actions) y evalúa varias estrategias **en papel**
(ninguna apuesta dinero real). El usuario es un no técnico en iPhone: necesita ver, entender y decidir sin tocar código.
La página es su **tablero de decisión**: cuándo una estrategia tiene evidencia suficiente para pasar a dinero mínimo y cuál se descarta.

## 1. Reglas innegociables
1. **Cero datos simulados.** Ningún número, curva ni fila de ejemplo hardcodeada. Todo sale de los ledgers reales. Si no hay datos
   (p. ej. tu `shadow` aún no publicó), la UI muestra un estado vacío honesto ("Sin datos todavía"), nunca relleno.
2. **Honestidad estadística visible.** Nunca mostrar un ROI sin su n y su intervalo de confianza al 95 %. Separar siempre
   *hacia adelante* (prospectivo) de *histórico* (backtest). Mostrar aparte apuestas abiertas, anuladas (V) y la procedencia
   del final (`src`). Nada de colores verdes triunfales por un ROI cuyo intervalo incluye 0.
3. **No tocar** `src/vectiq/**`, `collector.yml`, ni los ledgers. Solo lectura. Todo tu trabajo va en `site/` y un workflow nuevo.
4. **Sin secretos en la página ni en los JSON** (ni proxy, ni tokens, ni chat ids). El repo es **público**.
5. Entrega por **Pull Request** (no a main directo), con tests y una descripción de qué verificaste.

## 2. Fuentes de datos reales (descarga con `gh release download`, `GH_TOKEN=${{ github.token }}`)
Release `ledger` (se re-sube cada ~20 min por el collector):
- `masters.sqlite` → estrategia **Telegram (modelo)**: Masters Under con modelo, etapas prepartido / tras set 1 / tras set 2.
- `cand.sqlite` → **Manus (regla fija)** `MS_G17_UNDER_K1_PB12_BUD6`, stake plano ~$1, silenciosa. Ojo: antes del 2026-10-31 tuvo un
  bug de liquidación; sus filas viejas quedarán anuladas (`src='void'`). Su prueba prospectiva limpia empieza en ts_ms 1793154783487.
- `shadow.sqlite` → **Setka / viejo** (Under tras set 1 en ligas de 5 sets), silenciado.
- `paper.sqlite` → motor antiguo, casi vacío; ignóralo salvo que quieras una nota.
- `masters_book.json` → distribución aprendida de totales (úsala si haces la gráfica de distribución).
Release `shadow-ledger` → `state.tar.gz` con los `*.jsonl` de **tu** motor ST0/ST1 (decisions, settlements, capacity, market_ticks…).
Releases `tape-YYYY-MM-DD` → cinta cruda (grande; **no** la cargues en el navegador, solo agregados si los necesitas).

Esquema de `bets` (SQLite): `id, ts_ms, game_id, players, league, sets, side, line, price, p_model, ev, stake, bankroll_before,
closing_price, final_total, result, pnl, lead_min, src, champ_id, start_ts`.
- `result`: `W` gana, `L` pierde, `P` push, `V` anulada, `NULL` abierta.
- `src`: `observed` (final visto en el feed), `results_api` (servicio oficial de resultados), `imputed` (estimado — **no cuenta como
  evidencia fuerte**), `partial` (pérdida segura), `void`, `NULL` (legado anterior al 2026-10-29: tratar como "legado" y marcarlo).
- `sets` vacío ⇒ entrada prepartido; si no, etapa = nº de sets completados.
- ROI comparable entre bots: **1 unidad por apuesta** (`price-1` si W, `-1` si L, `0` si P), nunca ponderado por stake.

## 3. Arquitectura pedida
- `site/build_data.py` (solo stdlib: `sqlite3`, `json`, `statistics`, `math`): lee los ledgers y escribe `site/data/*.json` versionados
  (`schema_version`, `generated_at`, `sources` con tamaño y fecha de cada asset). Con tests (`tests/test_site_build.py`) sobre ledgers sintéticos
  **solo en los tests**.
- `.github/workflows/pages.yml`: cron cada 30 min + `workflow_dispatch`; descarga assets → build → `actions/upload-pages-artifact` →
  `actions/deploy-pages`. Permisos mínimos (`contents: read`, `pages: write`, `id-token: write`), `concurrency` propia, falla
  visible si un asset no baja (no publiques una página medio vacía como si estuviera bien).
- Front: HTML/CSS/JS estático sin framework pesado (vanilla o una librería de gráficos **con versión fija** desde cdnjs; o SVG propio).
  Carga los JSON con `fetch`, estados de carga/vacío/error cuidados. Sin trackers ni analítica.

## 4. Contenido (en español claro; cada término técnico con una explicación corta al tocarlo)
1. **Resumen**: una fila por estrategia (n con resultado, ROI, IC95 %, P(ROI>0), acierto, ritmo/día, abiertas/anuladas, días a 1,000 apuestas)
   y curva acumulada comparativa (unidades). Banner permanente: "Evidencia en papel, no dinero real".
2. **Estrategias** (una pestaña por bot, incluidas tus ST0 y ST1): equity y drawdown, ROI por liga / etapa / día / banda de cuota,
   **calibración** (probabilidad predicha vs acierto real, con barras de error), tabla de últimas apuestas con su procedencia.
3. **Calidad de datos**: mezcla de `src`, % de finales verificados, anuladas, latencia entre alerta y resultado, huecos de captura
   (último dato recibido, edad del último ledger).
4. **Puertas de decisión**: avance hacia ~1,000 apuestas hacia adelante por estrategia, probabilidad posterior de ROI>0 y de ROI>3 %,
   y qué dice `docs/PROMOTION.md` (léelo; no inventes umbrales distintos). Explica por qué 400 apuestas no alcanzan para un edge de +5 %.
5. **Ejecución y capacidad**: tu telemetría `capacity.jsonl` (maxBet/minBet/Block/changeCf) con la advertencia de que es consulta
   anónima y no el límite de la cuenta del usuario.
6. **Método y glosario**: qué es ROI, IC95 %, drawdown, CLV, sesgo de selección, por qué mostramos "hacia adelante" separado.
7. **Registro de cambios**: últimos commits relevantes (vía `git log` en build o la API de GitHub).

## 5. Diseño (muy importante)
Debe parecer el sitio de una **firma de inversión / laboratorio de investigación real**, no una plantilla generada por IA.
- Referencias de sobriedad: páginas de investigación de Anthropic y OpenAI, informes de gestoras cuantitativas.
- **Prohibido**: degradados morados/azul-neón, glassmorphism, tarjetas con sombras exageradas, emojis, iconos de relleno, hero con
  eslóganes grandilocuentes, ilustraciones genéricas, animaciones decorativas, textos tipo "potenciado por IA".
- **Sí**: tipografía cuidada (un serif para titulares y un sans para datos, p. ej. Source Serif 4 + IBM Plex Sans/Mono o Inter,
  numerales tabulares), rejilla estricta, líneas finas en vez de cajas, una sola tinta de acento, mucho espacio en blanco, tablas densas
  y legibles, gráficos con ejes rotulados y sin adornos, modo claro y oscuro (`prefers-color-scheme`), contraste AA.
- **Mobile-first (iPhone)**: sin scroll horizontal de página, objetivos táctiles ≥44 px, tablas que colapsan con elegancia,
  gráficos legibles a 390 px de ancho. Carga rápida (<150 KB de JS propio, sin bloqueos).
- Interacción útil, no decorativa: filtrar por estrategia/liga/etapa/rango de fechas, tooltips en gráficos, ordenar tablas, enlazar
  cada número a su definición. Estado persistente de filtros en la URL.

## 6. Qué debes decirle al usuario al terminar (es no técnico; sé literal y breve)
Pasos exactos en GitHub móvil: Settings → Pages → Source = **GitHub Actions**; cómo lanzar `pages` la primera vez (Actions → pages →
Run workflow); cuál será la URL (`https://blessed-200.github.io/Vectiq/`); cómo guardarla en la pantalla de inicio del iPhone;
qué significa cada semáforo/estado; y qué NO debe interpretar (un ROI bonito con intervalo que incluye 0 no es prueba).

## 7. Criterios de aceptación (los reviso yo)
- Cada número de la página coincide con un cálculo independiente sobre los mismos ledgers (te pediré cotejar 5 cifras al azar).
- Cero datos de ejemplo en producción; estados vacíos correctos; el build falla ruidosamente si falta un asset.
- Tests pasan; Lighthouse móvil ≥90 en rendimiento y accesibilidad; sin errores en consola.
- PR descrito con: decisiones de diseño, límites conocidos y lo que NO verificaste.
