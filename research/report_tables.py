"""Render the descriptive tables using the frozen aggregate outputs only."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def number(x, digits=2):
    return f"{x:,.{digits}f}".replace(",", "_").replace(".", ",").replace("_", ".")


def band(obj, key="estimate", ci="ci95", multiplier=100):
    if obj.get(key) is None or obj.get(ci) is None:
        return "—"
    lo, hi = obj[ci]
    return f"{number(obj[key]*multiplier)} [{number(lo*multiplier)} a {number(hi*multiplier)}]"


def table(headers, rows):
    return "\n".join("| " + " | ".join(str(v) for v in row) + " |" for row in
                     [headers, ["---"] * len(headers), *rows])


def render(folder):
    def load(name):
        return json.loads((folder / (name + ".json")).read_text())
    ledger, tape, trace = (load(n) for n in ("ledger_tape_discovery","tape_discovery","entry_trace"))
    centers = [(("Antes del primer punto (incluye vivo 0–0)" if k == "k0" else "Tras set 1"),
                v["games_with_bracket"],v) for k,v in tape["center_bias"].items()]
    strict = load("prematch_clock")
    inferred = load("prebook_identity")
    centers.extend([("Tablero prepartido estricto, por ID nativo",strict["estimate"]["games_with_bracket"],strict["estimate"]),
                    ("Tablero prepartido estricto, enlace inferido",inferred["pre_ids_with_no_vig_bracket"],inferred["estimate"])])
    out = ["# Tablas de descubrimiento", "Todas las cuotas son decimales. Los aciertos, ROI y diferencias llevan el IC95 percentil entre corchetes. Los intervalos se obtuvieron remuestreando los días UTC completos con la semilla 20261122, conservando juntos los registros de cada jornada. Un guion indica que no hay muestra evaluable.",
           "## Centro de mercado y total de puntos",
           table(["Etapa","Partidos con centro","Partidos con centro y final","Días","Centro menos total, puntos [IC95]","Total inferior al centro, % [IC95]"],
                 [[k,with_center,v["games_with_outcome"],v["days"],band(v["center_minus_total"],"value",multiplier=1),
                   band(v["fraction_below_center"],"value")] for k,with_center,v in centers])]
    out.extend(["## Entradas guardadas en los libros que alimentan el panel",
                "Resultados a una unidad por entrada, valoradas a su cuota publicada y con desenlace reconstruido por cinta. En la columna ponderada se usa el stake del libro de papel. Las dos caídas se definen por separado en el informe. ST5 después del segundo set es un subconjunto de ST5.",
                ])
    data=[]
    for leg, label in (("ST0","ST0 / PRE"),("ST3","ST3 / SET1"),("ST5","ST5"),("ST5-k2","ST5 tras set 2"),("all","Conjunto del panel")):
        if leg=="all":
            v=ledger["portfolio"]
            opened=ledger["opening_overlap"]["n_book_rows_available"]
        elif leg=="ST5-k2":
            b=ledger["books"]["ST5"]
            v=b["performance_by_sets_done"]["2"]
            opened=b["openings"]["stage_counts"]["2"]
        else:
            b=ledger["books"][leg]
            v=b["performance"]
            opened=b["openings"]["n"]
        data.append([label,opened,v["n_WL"],v["days_of_opening_with_WL"],number(v["mean_decimal_price_WL"],3) if v["n_WL"] else "—",
                     band(v["wr_positive_net_per_WL"]),band(v["roi_flat_per_WL"]),band(v["roi_at_booked_paper_stakes_per_WL"]),
                     number(100*v["panel_mdd_fraction_synthetic_t_plus_10min_initial_100"]) if v["n_WL"] else "—",
                     v["loss_streak_in_opening_order"] if v["n_WL"] else "—"])
    out.append(table(["Libro","Aperturas","Finales G/P","Días activos","Cuota","Acierto %","ROI plano %","ROI ponderado %","Caída simulada a 10 min %","Racha de pérdidas"],data))
    out.extend(["## Precio publicado y registro del aviso",
                "La columna «Con final G/P» exige la misma entrada, precio, línea, etapa y partido en la cinta, registro con identificador de mensaje y marcador final conciliable. Una marca de Telegram no acredita que la casa aceptara un boleto."])
    data=[]
    for leg in ("ST0","ST3","ST5","portfolio"):
        a=trace["linked_quote_marker_terminal"][leg]
        perf=a["performance_at_displayed_odds"]
        data.append(["Conjunto" if leg=="portfolio" else leg,a["exact_quote_marker_with_id_n"],perf["n_WL"],perf["days_of_opening_with_WL"],
                     band(perf["wr_positive_net_per_WL"]),band(perf["roi_flat_per_WL"])])
    out.append(table(["Rama","Pizarra y aviso","Con final G/P","Días","Acierto %","ROI plano %"],data))
    out+=["## Comparación sobre oportunidades observadas en toda la cinta",
          "Una unidad por señal de cada motor. El control toma el Under sin exigir señal y se pondera con los estratos de la estrategia. En cada remuestreo se vuelven a calcular las ponderaciones. Una ocasión de control corresponde a partido, motor cuando corresponda, etapa, liga, línea y centésima de cuota. Un partido puede aparecer en varias celdas.",
          ]
    names={"ST0":"ST0, rama identificable","ST3":"ST3","ST5":"ST5","ST5_after_set_2":"ST5 tras set 2","combined_flat_per_leg":"Conjunto contrafactual"}
    for key,title in (("unit_roi","ROI plano"),("win_rate","Acierto")):
        rows=[]
        for leg,label in names.items():
            v=tape["comparison"][leg]
            m=v[key]
            def fmt(k):
                return band(m[k],"value","ci95_resampled_utc_days")
            rows.append([label,m["signal_n"],m["control_n"],v["control_distinct_games"],v["selected_days"],
                         fmt("signal"),fmt("matched_blind_under"),fmt("increment")])
        out.extend(["### "+title,table(["Rama","Señales n","Ocasiones control n","Partidos control","Días de señal",
                                       title+" señal %",title+" Under sin señal exigida %","Diferencia pareada, pp"],rows)])
    out+=["## Fuentes y cobertura de los finales",
          table(["Motivo de exclusión","Partidos Masters"],sorted(tape["game_exclusions"].items())),
          table(["Rama","Primeras candidatas de pizarra","Pasan precio y marcador antes del riesgo","Con final admisible"],
                [[k,v.get("first_visible_paper_or_possible",0),v.get("passed_price_and_score_before_risk_gates",0),v.get("usable",0)]
                 for k,v in tape["candidates"].items()]),
          "Los ficheros JSON incluyen cantidades por liga, días sin final, denominadores del bootstrap, solapamiento de señales y casos excluidos. El IC de casos completos no cuantifica el sesgo de perder partidos sin final."]
    return "\n\n".join(out)+"\n"


if __name__=="__main__":
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--results",type=Path,default=ROOT/"results")
    cli.add_argument("--out",type=Path,default=ROOT/"00_tablas.md")
    args=cli.parse_args()
    args.out.write_text(render(args.results))
