"""
Informe de salida: genera un Excel y un informe HTML con los resultados del momento.
"""
from __future__ import annotations

import io
from datetime import datetime
from html import escape

import pandas as pd
import plotly.express as px

import reglas as R


def _excel_seguro(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    for c in d.columns:
        if pd.api.types.is_datetime64_any_dtype(d[c]):
            d[c] = d[c].dt.strftime("%Y-%m-%d %H:%M")
    return d


def excel(resumen: dict, mt, mp, alertas, kpi, base, invalidos, bitacora, datos) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame([(k, str(v)) for k, v in resumen.items()], columns=["Concepto", "Valor"]).to_excel(xw, sheet_name="Resumen", index=False)
        mt.to_excel(xw, sheet_name="Matriz tranque", index=False)
        mp.to_excel(xw, sheet_name="Matriz plataforma", index=False)
        kpi.to_excel(xw, sheet_name="Indicadores", index=False)
        _excel_seguro(alertas).to_excel(xw, sheet_name="Alertas", index=False)
        base.to_excel(xw, sheet_name="Linea base", index=False)
        _excel_seguro(invalidos).to_excel(xw, sheet_name="Datos invalidos", index=False)
        bitacora.to_excel(xw, sheet_name="Bitacora", index=False)
        _excel_seguro(datos).to_excel(xw, sheet_name="Datos validados", index=False)
        for ws in xw.book.worksheets:
            for col in ws.columns:
                largo = max(len(str(c.value)) if c.value is not None else 0 for c in col[:200])
                ws.column_dimensions[col[0].column_letter].width = min(max(10, largo + 2), 60)
            ws.freeze_panes = "A2"
    return buf.getvalue()


def _tabla(df: pd.DataFrame, colorear: str | None = None) -> str:
    df = df.astype(object).where(df.notna(), "")   # equivale a .fillna(""): sin "None" ni "nan" en el informe
    colores = {"Alta": "#fadbd8", "Media": "#fff1cc", "Baja": "#ddeee6",
               "Posible superación normativa": "#fadbd8", "Revisión": "#fff1cc", "Calidad del dato": "#e7edf3"}
    filas = []
    for _, r in df.iterrows():
        celdas = []
        for c in df.columns:
            v = r[c]
            estilo = ""
            if colorear and c in (colorear if isinstance(colorear, (list, tuple)) else [colorear]) and v in colores:
                estilo = f' style="background:{colores[v]}"'
            if isinstance(v, float):
                v = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            celdas.append(f"<td{estilo}>{escape(str(v))}</td>")
        filas.append("<tr>" + "".join(celdas) + "</tr>")
    cab = "".join(f"<th>{escape(str(c))}</th>" for c in df.columns)
    return f"<table><thead><tr>{cab}</tr></thead><tbody>{''.join(filas)}</tbody></table>"


def html(resumen: dict, mt, mp, alertas, kpi, base, serie_sulfato: pd.DataFrame, corte) -> str:
    fig = px.line(serie_sulfato, x="fecha", y="valor", color="punto", markers=True, log_y=True,
                  labels={"valor": "Sulfato (mg/L, escala log)", "fecha": "Fecha", "punto": "Punto"},
                  title="Sulfato en los cuatro puntos", template="plotly_white")
    fig.add_scatter(x=[serie_sulfato["fecha"].min(), serie_sulfato["fecha"].max()], y=[R.LIMITE_SULFATO_ET0F] * 2,
                    mode="lines", line=dict(color="#c0392b", dash="dash"), name="Límite RCA ET-0F (250 mg/L)")
    graf = fig.to_html(full_html=False, include_plotlyjs=True)   # autocontenido: el gráfico se ve sin internet
    res = "".join(f"<tr><th>{escape(k)}</th><td>{escape(str(v))}</td></tr>" for k, v in resumen.items())
    cols_m = ["ID", "Impacto", "M", "E", "D", "P = M+E+D", "Prioridad", "Prioridad P'", "Significativo", "Origen de M"]
    al = alertas[["tipo", "punto", "parametro", "fecha_dato", "valor", "estado", "responsable", "accion", "verificacion"]] \
        if not alertas.empty else alertas
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<title>Informe de salida · Monitoreo El Torito</title>
<style>
body{{font-family:Arial,Helvetica,sans-serif;color:#243b53;margin:32px auto;max-width:1100px;padding:0 16px;font-size:14px}}
h1{{font-size:22px}} h2{{font-size:17px;margin-top:28px;border-bottom:2px solid #243b53;padding-bottom:4px}}
table{{border-collapse:collapse;width:100%;margin:8px 0}} th,td{{border:1px solid #cfd8e3;padding:4px 6px;text-align:left;vertical-align:top}}
th{{background:#243b53;color:#fff}} .nota{{color:#52677c;font-size:12px}}
.res th{{background:#eef2f6;color:#243b53;width:40%}}
@media print{{.noprint{{display:none}}}}
</style></head><body>
<h1>Informe de salida · Monitoreo ambiental del tranque El Torito</h1>
<p class="nota">Generado el {datetime.now():%d/%m/%Y %H:%M} por el prototipo académico. Línea base con datos hasta {pd.Timestamp(corte):%d/%m/%Y}.
Datos históricos reales (marzo 2021 a septiembre 2022); los registros marcados como simulados son de demostración. Una alerta indica revisión, no incumplimiento.</p>
<p class="noprint nota">Para guardarlo como PDF: Imprimir → Guardar como PDF.</p>
<h2>1. Resumen</h2><table class="res">{res}</table>
<h2>2. Matriz principal: impactos del tranque (P = M + E + D)</h2>{_tabla(mt[cols_m], ["Prioridad", "Prioridad P'"])}
<p class="nota">Significativo: M ≥ 2 y P ≥ 7, o declarado significativo por la RCA N.º 02/2020. P' = 2M + E + D es la prueba de sensibilidad (baja 4–6, media 7–9, alta 10–12).</p>
<h2>3. Matriz complementaria: impactos de la plataforma</h2>{_tabla(mp[cols_m], ["Prioridad", "Prioridad P'"])}
<h2>4. Indicadores</h2>{_tabla(kpi)}
<h2>5. Alertas vigentes</h2>{_tabla(al, "tipo") if not al.empty else "<p>No hay alertas vigentes.</p>"}
<h2>6. Línea base (rangos de K9 a K11)</h2>{_tabla(base)}
<h2>7. Evolución del sulfato</h2>{graf}
</body></html>"""
