"""
Prototipo de monitoreo ambiental · Tranque de relaves El Torito
Ingeniería Ambiental · UTEM · Sección 411 · Grupo 1

Ejecutar:  streamlit run app.py
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime, time

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import almacen as A
import informe as INF
import reglas as R

st.set_page_config(page_title="Monitoreo El Torito", page_icon="💧", layout="wide")

# Celdas coloreadas: el texto oscuro fijo (#1F2933) se lee sobre el fondo pastel en modo claro y oscuro
COLOR_PRIORIDAD = {"Alta": "background-color:#fadbd8;color:#1F2933", "Media": "background-color:#fff1cc;color:#1F2933",
                   "Baja": "background-color:#ddeee6;color:#1F2933"}
COLOR_TIPO = {"Posible superación normativa": "background-color:#fadbd8;color:#1F2933",
              "Revisión": "background-color:#fff1cc;color:#1F2933",
              "Calidad del dato": "background-color:#e7edf3;color:#1F2933"}
GRIS_REFERENCIA = "#7B8DA0"   # línea y texto del corte: legible sobre fondo claro y oscuro
SEPARADOR_OTROS = "── Otros parámetros (solo visualización) ──"
FECHA_DEMO = pd.Timestamp("2022-10-15 10:00")   # fecha de los registros simulados de la demo
CORTE_ANOMALO = date(2022, 2, 28)

# ---------------------------------------------------------------------------
# Acceso (sitio seguro): si se define APP_PASSWORD en secrets o variables de entorno,
# la aplicación pide contraseña antes de mostrar los datos.
# ---------------------------------------------------------------------------
def _clave_configurada():
    try:
        if "APP_PASSWORD" in st.secrets:
            return str(st.secrets["APP_PASSWORD"])
    except Exception:
        pass
    return os.environ.get("APP_PASSWORD")


clave = _clave_configurada()
if clave and not st.session_state.get("acceso_ok"):
    st.title("Monitoreo ambiental · Tranque El Torito")
    with st.form("login"):
        intento = st.text_input("Contraseña de acceso", type="password")
        if st.form_submit_button("Ingresar"):
            if intento == clave:
                st.session_state["acceso_ok"] = True
                st.rerun()
            else:
                st.error("Contraseña incorrecta.")
    st.stop()


@st.cache_resource
def conexion():
    return A.conectar()


con = conexion()

# ---------------------------------------------------------------------------
# Estado inicial
# ---------------------------------------------------------------------------
def _ultimo_dato_real():
    r = con.execute("SELECT MAX(fecha) FROM registros WHERE simulado=0").fetchone()[0]
    return pd.Timestamp(r).date() if r else date.today()


ss = st.session_state
ss.setdefault("corte", _ultimo_dato_real())
ss.setdefault("margen", 10)
ss.setdefault("usuario", "Grupo 1")
ss.setdefault("rol", "Encargado ambiental")
ss.setdefault("ajustes", {})
ss.setdefault("upl_n", 0)      # clave dinámica del cargador de archivos
ss.setdefault("camp_n", 0)     # clave dinámica de la tabla de campaña
for _k, _v in R.UMBRALES.items():
    ss.setdefault("u_" + _k, float(_v))
for _k, _v in {"n_notebooks": 3, "w_notebook": 30.0, "w_router": 10.0, "horas_dia": 8.0,
               "dias_mes": 22, "vcpu": 2}.items():
    ss.setdefault("eq_" + _k, _v)

# ---------------------------------------------------------------------------
# Escenarios de demostración (se ejecutan antes de dibujar los controles)
# ---------------------------------------------------------------------------
def _simulado(punto, clave_, valor):
    nombre, grupo = R.ALIAS_PARAMETRO[clave_.lower()]
    return {"fuente": "Simulado (demo)", "fecha": FECHA_DEMO, "punto": punto, "parametro": nombre,
            "valor": valor, "unidad": R.UNIDAD_CLAVE[clave_], "calificador": "Igual", "grupo": grupo, "simulado": 1}


def escenario_normal():
    A.borrar_simulados(con)
    filas = [_simulado("Laguna AO-3", "pH", 7.6), _simulado("Laguna AO-3", "Conductividad", 2900),
             _simulado("Laguna AO-3", "Sulfato", 1350)]
    A.insertar_registros(con, pd.DataFrame(filas))
    ss["corte"] = _ultimo_dato_real()
    A.registrar(con, ss["usuario"], ss["rol"], "Escenario", "Normal: medición simulada de la laguna dentro de rango")
    ss["msg"] = ("Escenario normal cargado: 3 mediciones simuladas de la laguna (15/10/2022) dentro de su rango "
                 "histórico. Resultado esperado: ninguna alerta nueva.")


def escenario_anomalo():
    A.borrar_simulados(con)
    ss["corte"] = CORTE_ANOMALO
    A.registrar(con, ss["usuario"], ss["rol"], "Escenario", "Anómalo real: línea base hasta 28/02/2022")
    ss["msg"] = ("Escenario anómalo con datos reales: la línea base usa solo los datos hasta el 28/02/2022 y se "
                 "evalúan las mediciones posteriores. El caso más claro es el Pozo 8/A: sulfato y conductividad "
                 "suben juntos desde abril de 2022.")


def escenario_superacion():
    A.borrar_simulados(con)
    A.insertar_registros(con, pd.DataFrame([_simulado("ET-0F", "Sulfato", 300.0)]))
    ss["corte"] = _ultimo_dato_real()
    A.registrar(con, ss["usuario"], ss["rol"], "Escenario", "Superación simulada: sulfato 300 mg/L en ET-0F")
    ss["msg"] = ("Superación simulada: se agregó un sulfato de 300 mg/L en ET-0F (dato SIMULADO). Resultado "
                 "esperado: alerta roja de posible superación normativa y la fila T3 de la matriz sube a M3.")


def escenario_limpiar():
    n = A.borrar_simulados(con)
    ss["corte"] = _ultimo_dato_real()
    A.registrar(con, ss["usuario"], ss["rol"], "Escenario", f"Limpieza: {n} registros simulados eliminados")
    ss["msg"] = f"Se eliminaron {n} registros simulados y la línea base volvió a todo el período real."


def reiniciar_todo():
    if not ss.get("confirmar_reinicio") or ss["rol"] != "Administrador":
        return
    A.reiniciar(con)
    A.registrar(con, ss["usuario"], ss["rol"], "Reinicio de base",
                "Base reiniciada con los datos reales; se borraron las alertas y la bitácora anteriores")
    ss["corte"] = _ultimo_dato_real()
    ss["confirmar_reinicio"] = False   # la casilla se desmarca: otro reinicio exige confirmar de nuevo
    ss["msg"] = "Base reiniciada con los datos reales."


def corregir_valor(id_registro: int):
    """Callback del botón Corregir: toma el nuevo valor y el motivo del estado de los widgets."""
    nv = ss.get(f"corr_nv_{id_registro}")
    motivo = (ss.get("corr_motivo") or "").strip()
    if ss["rol"] not in ("Operador", "Administrador") or not motivo:
        return
    try:
        A.actualizar_valor(con, int(id_registro), float(nv), ss["usuario"], ss["rol"], motivo)
    except ValueError as e:
        ss["corr_error"] = str(e)
    else:
        ss["corr_motivo"] = ""
        ss["msg"] = f"Registro {int(id_registro)} corregido."


def restablecer_umbrales():
    for _k, _v in R.UMBRALES.items():
        ss["u_" + _k] = float(_v)


def restablecer_puntajes():
    ss["ajustes"] = {}
    for _k in [k for k in list(ss.keys()) if re.fullmatch(r"[med]_[TR]\d+", str(k))]:
        del ss[_k]


# ---------------------------------------------------------------------------
# Barra lateral
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### 💧 Monitoreo El Torito")
    st.caption("Prototipo académico · Ingeniería Ambiental UTEM · Grupo 1")
    st.text_input("Usuario", key="usuario")
    st.selectbox("Rol", ["Operador", "Encargado ambiental", "Administrador"], key="rol")
    st.divider()
    st.markdown("**Parámetros de las alertas**")
    st.date_input("Línea base con datos hasta", key="corte",
                  help="Se calcula el rango histórico con los datos reales hasta esta fecha y se evalúan las mediciones posteriores.")
    st.slider("Margen de tolerancia sobre el rango (%)", 0, 50, key="margen",
              help="Amplía el rango histórico en este porcentaje de su ancho para evitar alertas por variaciones pequeñas.")
    st.divider()
    st.caption("Datos históricos reales (mar 2021 – sep 2022). No es un diagnóstico actual del tranque. "
               "Una alerta indica revisión, no incumplimiento.")

# ---------------------------------------------------------------------------
# Cálculos comunes
# ---------------------------------------------------------------------------
registros = A.leer_registros(con)
dv = R.validar(registros)
alertas_calc = R.generar_alertas(dv, ss["corte"], ss["margen"])
A.sincronizar_alertas(con, alertas_calc)
todas = A.leer_alertas(con)
vigentes = todas[todas["id"].isin(alertas_calc["id"])].copy()
k2, k3 = R.k2_k3(vigentes)
base = R.linea_base(dv, ss["corte"])
AVISO_BASE_VACIA = (f"No hay datos reales hasta el {pd.Timestamp(ss['corte']):%d/%m/%Y}: la línea base está vacía y "
                    "no se pueden emitir alertas de Revisión. Elija una fecha de corte posterior (los datos reales "
                    "comienzan en marzo de 2021).")
if base.empty:
    st.sidebar.warning(AVISO_BASE_VACIA)

eq = {k[3:]: ss[k] for k in list(ss.keys()) if str(k).startswith("eq_")}
umbrales = {k: ss["u_" + k] for k in R.UMBRALES}
kwh = R.energia_kwh_anual(eq["n_notebooks"], eq["w_notebook"], eq["w_router"], eq["horas_dia"], eq["dias_mes"])
variables = R.variables_desde_datos(dv)
variables.update(kwh_anual=kwh, n_equipos=eq["n_notebooks"] + 1, vcpu=eq["vcpu"])
mt, mp = R.calcular_matriz(variables, umbrales, ss["ajustes"])


def fmt(x, dec=1, suf=""):
    if x is None or pd.isna(x):
        return "—"
    return f"{x:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".") + suf


ORDEN_MATRIZ = ["ID", "Impacto", "M", "E", "D", "P = M+E+D", "Prioridad", "Significativo", "P' = 2M+E+D",
                "Prioridad P'", "Origen de M", "N.º RCA", "Signo", "Componente", "Aspecto", "Actividad"]


def estilo_matriz(df):
    df = df[[c for c in ORDEN_MATRIZ if c in df.columns]]
    return df.style.map(lambda v: COLOR_PRIORIDAD.get(v, ""), subset=["Prioridad", "Prioridad P'"])


def tabla_kpi():
    s = R.serie_clave(dv)
    fuera = vigentes[vigentes["tipo"] == "Revisión"].groupby("parametro").size()
    filas = [
        ("K1", "A", "Porcentaje de datos válidos", fmt(R.k1(dv), 1, " %"), "≥ 99 %", "Calculado"),
        ("K2", "A", "Tiempo de respuesta a alertas (h, emisión → acción)", fmt(k2, 2, " h"), "Revisión ≤ 24 h; acción ≤ 5 días hábiles", "Calculado con las alertas registradas"),
        ("K3", "A", "Alertas cerradas con verificación / emitidas", fmt(k3, 1, " %"), "100 % en ≤ 15 días hábiles", "Calculado con las alertas registradas"),
        ("K4", "B", "Consumo eléctrico local (Ecuación 1)", fmt(kwh, 0, " kWh/año"), "Medición del primer mes", "Estimado con las variables de ingreso"),
        ("K5", "B", "Energía y agua atribuibles en la nube", "Dato del proveedor", "Sin referencia", "Propuesto (dato faltante ≠ cero)"),
        ("K6", "B", "Equipos adquiridos", f"{eq['n_notebooks']} notebooks + 1 router", "3 notebooks y 1 router", "Variable de ingreso"),
        ("K7", "B", "Equipos reutilizados o entregados a gestor / retirados", "Al cierre", "100 %", "Propuesto"),
        ("K8", "B", "Baterías entregadas a gestor / retiradas", "Al cierre", "100 %", "Propuesto"),
        ("K9", "C", "pH fuera del rango histórico (alertas vigentes)", str(int(fuera.get("pH", 0))), "Rango histórico del punto", "Calculado"),
        ("K10", "C", "Conductividad fuera del rango histórico (alertas vigentes)", str(int(fuera.get("Conductividad", 0))), "Rango histórico del punto", "Calculado"),
        ("K11", "C", "Sulfato fuera de rango o sobre 250 mg/L en ET-0F (alertas vigentes)",
         str(int(fuera.get("Sulfato", 0)) + int((vigentes["tipo"] == "Posible superación normativa").sum())),
         "Rango histórico; ET-0F 250 mg/L", "Calculado"),
        ("K12", "C", "Muestras de relave no generadoras de ácido (NAG 2018)", "6 de 6 (100 %)", "Se informa por método", "Dato del informe (Anexo 24 Adenda)"),
        ("K13", "B", "Residuos de embalaje y papel", "Pesaje", "Papel mínimo", "Propuesto"),
        ("K14–K19", "D", "Seguimiento del titular (aves, superficie, flora, fauna, peces, gestión de riesgo)", "Fuera de la plataforma", "Ver informe, Tabla 25", "Propuesto"),
    ]
    return pd.DataFrame(filas, columns=["KPI", "Grupo", "Indicador", "Valor actual", "Referencia", "Estado"])


# ---------------------------------------------------------------------------
# Encabezado
# ---------------------------------------------------------------------------
st.title("Monitoreo ambiental · Tranque El Torito")
st.caption(f"Usuario: {ss['usuario']} · Rol: {ss['rol']} · Línea base hasta {pd.Timestamp(ss['corte']):%d/%m/%Y} "
           f"· Margen {ss['margen']} %")
if ss.get("msg"):
    # st.toast no ocupa lugar antes de las pestañas: así no se recrean ni saltan a "Instrucciones"
    st.toast(ss.pop("msg"))

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Registros", f"{len(dv):,}".replace(",", "."), f"{int(registros['simulado'].sum())} simulados",
          delta_color="off")
c2.metric("K1 datos válidos", fmt(R.k1(dv), 1, " %"))
c3.metric("Alertas vigentes", len(vigentes), f"{int((vigentes['estado'] != 'Cerrada').sum())} sin cerrar",
          delta_color="off")
c4.metric("Superación normativa", int((vigentes["tipo"] == "Posible superación normativa").sum()))
c5.metric("K2 respuesta", fmt(k2, 2, " h"))
c6.metric("K3 cerradas", fmt(k3, 1, " %"))

tabs = st.tabs(["📘 Instrucciones", "📥 Datos de ingreso", "📈 Dashboard", "🧪 Calidad de datos",
                "🚨 Alertas", "🧮 Matriz ambiental", "📊 Indicadores", "🎬 Demo", "📄 Informe de salida"])

# ---------------------------------------------------------------------------
# 0. Instrucciones
# ---------------------------------------------------------------------------
with tabs[0]:
    st.subheader("Cómo usar el prototipo")
    st.markdown(f"""
Este prototipo hace el **seguimiento y control** de las variables ambientales propuestas en el informe del
tranque de relaves El Torito (faena El Soldado, Nogales): **pH, conductividad eléctrica y sulfato** en cuatro
puntos de monitoreo, y calcula la **matriz de impactos ambientales** con los datos ingresados.

**1. Variables de ingreso** (pestaña *Datos de ingreso*)
- *Datos de calidad de agua*: archivo CSV o Excel con las columnas `fecha, punto, parametro, valor, unidad`
  (y opcionales `calificador, grupo, fuente, simulado`). Puntos: {", ".join(R.PUNTOS)}.
  Parámetros: `pH`, `Conductividad` (µS/cm), `Sulfato` (mg/L) u otros con su nombre original. Se puede
  descargar una plantilla y **cargar varios archivos a la vez** (CSV y Excel mezclados).
- *Ingreso de campaña*: se eligen una vez el punto, la fecha y la hora, y se anotan en una tabla los valores
  de todos los parámetros medidos (pH, conductividad, temperatura, sulfato u otros de la base). La unidad y
  el grupo se asignan solos. Se acepta la coma decimal.
- *Corregir un valor*: se elige el registro por su ID, buscándolo por punto y parámetro o seleccionando su
  fila en la tabla; se ve su ficha antes de corregir y el cambio queda en la bitácora con su motivo.
- *Variables de la plataforma* (pestaña *Matriz ambiental*): n.º de notebooks, potencias, horas de uso y
  vCPU del servidor. Con ellas se calcula el consumo eléctrico (Ecuación 1 del informe).
- La aplicación ya trae cargados los **1.869 registros reales** de los cuatro puntos (planillas SMA,
  marzo 2021 – septiembre 2022).

**2. Cálculos que hace**
- *Validación (K1)*: marca como inválidos las copias repetidas, las unidades atípicas, el pH fuera de 0–14 y
  las concentraciones negativas. Un valor anómalo no se descarta: se marca para revisión.
- *Línea base y alertas*: calcula el rango histórico (mínimo–máximo) de cada punto con los datos hasta la
  fecha elegida en la barra lateral y evalúa las mediciones posteriores.
  - 🟡 **Revisión**: valor fuera del rango histórico (más el margen). **No es un incumplimiento.**
  - 🔴 **Posible superación normativa**: sulfato > 250 mg/L en ET-0F, único límite aplicable (RCA N.º 02/2020).
  - ⚪ **Calidad del dato**: registro inválido o anómalo.
- *Matriz ambiental*: P = M + E + D (1 a 3 cada uno). La magnitud M de T1–T4 y de las filas de la plataforma
  **se calcula con los datos de ingreso**; el resto proviene de la RCA. Incluye criterio de significancia
  y prueba de sensibilidad (P' = 2M + E + D).
- *Indicadores*: K1 a K13 calculados o propuestos, y K14–K19 de seguimiento del titular.

**3. Resultados**: *Dashboard* (gráficos que se actualizan al cambiar los datos), *Alertas* (ciclo con
responsable, acción y cierre), *Matriz ambiental* e *Indicadores*.

**4. Informe de salida**: en la pestaña *Informe de salida* se descarga un **Excel** con todas las tablas y un
**informe HTML** (se puede imprimir como PDF).

**5. Demo**: la pestaña *Demo* carga tres escenarios: normal, anómalo con datos reales y superación simulada.

**Dashboard**: pH, conductividad y sulfato (los que generan alertas) con su rango histórico y el límite de
250 mg/L; además, cualquier otro parámetro de la base *solo para consulta* (▽ = bajo el límite de detección).

**Modo oscuro**: menú ⋮ (arriba a la derecha) → *Settings* → *Light*, *Dark* o *System*.

**Roles**: *Operador* carga, ingresa campañas y corrige datos; *Encargado ambiental* revisa y cierra alertas
(no ve los formularios de carga ni de corrección); *Administrador* puede todo y además reiniciar la base
(con confirmación). Cada acción queda en la **bitácora**.
""")
    st.warning("Alcance: datos históricos reales del seguimiento ambiental; no describen el estado actual del "
               "tranque. Los registros simulados de la demo quedan marcados como tales. La plataforma apoya el "
               "seguimiento, pero no evalúa oficialmente el cumplimiento normativo.")

# ---------------------------------------------------------------------------
# 1. Datos de ingreso
# ---------------------------------------------------------------------------
with tabs[1]:
    st.subheader("Datos de calidad de agua")
    puede_cargar = ss["rol"] in ("Operador", "Administrador")
    if not puede_cargar:
        st.info("La carga de archivos y el ingreso de campañas corresponden al Operador o al Administrador "
                "(elija ese rol en la barra lateral).")
    else:
        col_a, col_b = st.columns([1, 1], gap="large")
        # ---------------- 2. Carga de uno o varios archivos ----------------
        with col_a:
            st.markdown("**Cargar archivos (CSV o Excel, uno o varios a la vez)**")
            plantilla = pd.DataFrame([
                {"fecha": "2022-10-20 09:00", "punto": "ET-0F", "parametro": "Sulfato", "valor": 120, "unidad": "mg/L",
                 "calificador": "Igual", "fuente": "Campaña octubre", "simulado": 1},
                {"fecha": "2022-10-20 09:00", "punto": "Pozo 8/A", "parametro": "pH", "valor": 7.5, "unidad": "unidad pH",
                 "calificador": "Igual", "fuente": "Campaña octubre", "simulado": 1},
            ])
            st.download_button("Descargar plantilla CSV", plantilla.to_csv(index=False).encode("utf-8"),
                               "plantilla_carga.csv", "text/csv")
            archivos = st.file_uploader("Archivos", type=["csv", "xlsx"], accept_multiple_files=True,
                                        key=f"upl_{ss['upl_n']}")
            if archivos:
                resumen, nuevos, chequeo = R.preparar_carga([(a.name, a.getvalue()) for a in archivos],
                                                            registros)
                st.dataframe(resumen, width="stretch", hide_index=True)
                for _, f in resumen[resumen["error"] != ""].iterrows():
                    st.error(f"{f['archivo']}: {f['error']}")
                for _, f in resumen[(resumen["error"] == "") & (resumen["inválidos"] > 0)].iterrows():
                    st.warning(f"{f['archivo']}: {f['inválidos']} registro(s) inválido(s) — {f['observaciones']}. "
                               "Se guardan marcados como inválidos y no entran a los cálculos.")
                if nuevos:
                    n_nuevos = sum(len(d) for d in nuevos.values())
                    st.write(f"{len(nuevos)} archivo(s) válido(s) · {n_nuevos} registros leídos · "
                             f"{int((~chequeo['valido']).sum())} inválidos")
                    st.dataframe(chequeo[["archivo", "fecha", "punto", "parametro", "valor", "unidad", "valido",
                                          "motivo"]], width="stretch", height=240, hide_index=True)
                    if st.button("Agregar a la base", type="primary"):
                        for nombre, df in nuevos.items():
                            A.insertar_registros(con, df)
                            A.registrar(con, ss["usuario"], ss["rol"], "Carga de archivo",
                                        f"{nombre}: {len(df)} registros")
                        ss["msg"] = f"Se agregaron {n_nuevos} registros desde {len(nuevos)} archivo(s)."
                        ss["upl_n"] += 1   # clave nueva: el cargador queda vacío y no se puede agregar dos veces
                        st.rerun()

        # ---------------- 3. Ingreso de campaña ----------------
        with col_b:
            st.markdown("**Ingreso de campaña** (varios parámetros medidos en un mismo punto y momento)")
            catalogo = R.catalogo_parametros(registros)
            c1, c2 = st.columns(2)
            punto_c = c1.selectbox("Punto", R.PUNTOS, key="camp_punto")
            sim_c = c2.checkbox("Marcar como dato simulado", value=True, key="camp_sim")
            c3, c4 = st.columns(2)
            fecha_c = c3.date_input("Fecha", value=date(2022, 10, 15), key="camp_fecha")
            hora_c = c4.time_input("Hora", value=time(10, 0), step=300, key="camp_hora")
            inicial = pd.DataFrame({"Parámetro": ["pH", "Conductividad", "Temperatura"], "Valor": ["", "", ""]})
            tabla = st.data_editor(
                inicial, key=f"camp_tabla_{ss['camp_n']}", num_rows="dynamic", hide_index=True, width="stretch",
                column_config={
                    "Parámetro": st.column_config.SelectboxColumn("Parámetro", options=list(catalogo["etiqueta"]),
                                                                  required=True, width="medium"),
                    "Valor": st.column_config.TextColumn("Valor", help="Se acepta coma decimal (7,5)."),
                })
            momento = datetime.combine(fecha_c, hora_c)
            nuevos_c = R.registros_campana(punto_c, momento, tabla, sim_c, catalogo)
            elegidos = [p for p in tabla["Parámetro"].dropna().unique() if p in set(catalogo["etiqueta"])]
            unidades = catalogo.set_index("etiqueta")["unidad"]
            if elegidos:
                st.caption("Unidad que se asigna: " + " · ".join(f"{p} → {unidades[p] or 'sin unidad'}"
                                                                 for p in elegidos))
            if ss.get("camp_resultado"):
                st.success(ss["camp_resultado"])
            if st.button("Guardar campaña", type="primary", disabled=nuevos_c.empty,
                         help="Se ignoran las filas sin valor."):
                chequeo_c = R.validar(pd.concat([registros[R.COLUMNAS], nuevos_c], ignore_index=True)).tail(len(nuevos_c))
                invalidos_c = int((~chequeo_c["valido"]).sum())
                A.insertar_registros(con, nuevos_c)
                A.registrar(con, ss["usuario"], ss["rol"], "Ingreso de campaña",
                            f"Campaña: {punto_c}, {momento:%d/%m/%Y %H:%M}, {len(nuevos_c)} parámetros "
                            f"({'simulado' if sim_c else 'real'})")
                ss["camp_resultado"] = (f"Campaña guardada en {punto_c} ({momento:%d/%m/%Y %H:%M}): "
                                        f"{len(nuevos_c)} registros guardados, {invalidos_c} inválidos.")
                ss["msg"] = ss["camp_resultado"]
                ss["camp_n"] += 1   # la tabla vuelve a su estado inicial
                st.rerun()

    st.divider()
    st.markdown("**Registros en la base**")
    f1, f2, f3 = st.columns(3)
    fp = f1.multiselect("Punto", R.PUNTOS, default=R.PUNTOS)
    solo_clave = f2.checkbox("Solo pH, conductividad y sulfato", value=True)
    solo_sim = f3.checkbox("Solo simulados", value=False)
    vista = dv[dv["punto"].isin(fp)]
    if solo_clave:
        vista = vista[vista["clave"].notna()]
    if solo_sim:
        vista = vista[vista["simulado"] == 1]
    vista = vista[["id", "fecha", "punto", "parametro", "valor", "unidad", "calificador", "fuente",
                   "simulado", "valido", "motivo"]].reset_index(drop=True)
    if puede_cargar:
        st.caption("Seleccione una fila (casilla a la izquierda) para cargarla en «Corregir un valor».")
        evento = st.dataframe(vista, width="stretch", height=300, hide_index=True, on_select="rerun",
                              selection_mode="single-row", key="tabla_registros")
        filas_sel = list(evento.selection.rows) if evento is not None else []
        id_tabla = int(vista.iloc[filas_sel[0]]["id"]) if filas_sel else None
        if id_tabla is not None and id_tabla != ss.get("_id_tabla_previo"):
            ss["corr_modo"] = "Escribir el ID"   # aún no se dibujan esos widgets: se pueden fijar aquí
            ss["corr_id"] = id_tabla
        ss["_id_tabla_previo"] = id_tabla
    else:
        st.dataframe(vista, width="stretch", height=300, hide_index=True)

    # ---------------- 6. Corregir un valor ----------------
    st.markdown("**Corregir un valor** (queda registrado en la bitácora)")
    if not puede_cargar:
        st.info("La corrección de datos corresponde al Operador o al Administrador.")
    else:
        with st.container(border=True):
            modo = st.radio("Elegir el registro", ["Escribir el ID", "Buscar por punto y parámetro"],
                            horizontal=True, key="corr_modo")
            if modo == "Escribir el ID":
                id_sel = int(st.number_input("ID del registro", min_value=1, step=1, key="corr_id"))
            else:
                b1, b2 = st.columns(2)
                p_pt = b1.selectbox("Punto", R.PUNTOS, key="corr_punto")
                params = sorted(dv.loc[dv["punto"] == p_pt, "parametro"].dropna().unique(),
                                key=lambda s: R._sin_tildes(str(s)).lower())
                p_par = b2.selectbox("Parámetro", params, key="corr_param")
                cand = dv[(dv["punto"] == p_pt) & (dv["parametro"] == p_par)].sort_values("fecha", ascending=False)
                ids_por_etiqueta = {
                    (f"ID {int(r.id)} · {pd.Timestamp(r.fecha):%Y-%m-%d %H:%M} · {r.punto} · {r.parametro} · "
                     f"{fmt(r.valor, 4).rstrip('0').rstrip(',')} {r.unidad}"): int(r.id)
                    for r in cand.itertuples()}
                etiqueta_sel = st.selectbox("Registro", list(ids_por_etiqueta), key="corr_lista")
                id_sel = ids_por_etiqueta.get(etiqueta_sel, 0)
            fila = dv[dv["id"] == id_sel]
            if ss.get("corr_error"):
                st.error(ss.pop("corr_error"))
            if fila.empty:
                st.warning(f"El registro {id_sel} no existe.")
                actual = None
            else:
                r = fila.iloc[0]
                actual = None if pd.isna(r["valor"]) else float(r["valor"])
                ficha = {
                    "ID": str(int(r["id"])),
                    "Fecha": "—" if pd.isna(r["fecha"]) else f"{pd.Timestamp(r['fecha']):%d/%m/%Y %H:%M}",
                    "Punto": r["punto"], "Parámetro": r["parametro"],
                    "Valor actual": "— (vacío o no numérico)" if actual is None else f"{actual:g}",
                    "Unidad": r["unidad"] or "—", "Calificador": r["calificador"], "Fuente": r["fuente"],
                    "Simulado": "Sí" if int(r["simulado"]) else "No",
                    "Válido": "Sí" if r["valido"] else "No",
                    "Motivo de invalidez": r["motivo"] or "—",
                }
                st.table(pd.DataFrame(list(ficha.items()), columns=["Campo", "Registro actual"]).set_index("Campo"))
            if actual is not None or not fila.empty:
                nv = st.number_input("Nuevo valor", value=actual if actual is not None else 0.0, format="%g",
                                     key=f"corr_nv_{id_sel}")
            else:
                nv = None
            motivo = st.text_input("Motivo de la corrección", key="corr_motivo",
                                   help="Obligatorio. Presione Enter después de escribirlo.")
            if not fila.empty:
                st.markdown(f"**{'—' if actual is None else f'{actual:g}'} → {nv:g}**")
            iguales = actual is not None and nv is not None and float(nv) == actual
            if iguales:
                st.caption("El nuevo valor es igual al actual.")
            st.button("Corregir", type="primary", on_click=corregir_valor, args=(id_sel,),
                      disabled=fila.empty or iguales or not (motivo or "").strip())

# ---------------------------------------------------------------------------
# 2. Dashboard
# ---------------------------------------------------------------------------
with tabs[2]:
    st.subheader("Evolución de las variables")
    d1, d2, d3 = st.columns([2, 1, 1])
    puntos_sel = d1.multiselect("Puntos", R.PUNTOS, default=R.PUNTOS, key="dash_puntos")
    otros = R.otros_parametros(dv)
    opciones_param = R.CLAVES + ([SEPARADOR_OTROS] + otros if otros else [])
    param_sel = d2.selectbox("Parámetro", opciones_param, index=2, key="dash_param",
                             help="pH, Conductividad y Sulfato generan alertas. Los demás se muestran solo para consulta.")
    log = d3.checkbox("Escala logarítmica", value=(param_sel == "Sulfato"))
    serie = R.serie_clave(dv)
    serie = serie[(serie["clave"] == param_sel) & (serie["punto"].isin(puntos_sel))].copy()
    serie["origen"] = serie["simulado"].map({0: "Real", 1: "Simulado"})
    if param_sel == SEPARADOR_OTROS:
        st.info("Elija uno de los parámetros que aparecen debajo de este título en la lista.")
    elif param_sel not in R.CLAVES:
        # ---- Otros parámetros: solo visualización (sin bandas, límites ni alertas) ----
        s_otro = R.serie_parametro(dv, param_sel, puntos_sel)
        if s_otro.empty:
            st.warning(f"No hay datos válidos de {param_sel} en los puntos elegidos.")
        else:
            unidad = R.unidad_predominante(dv, param_sel)
            colores = dict(zip(R.PUNTOS, px.colors.qualitative.Plotly))
            fig = go.Figure()
            for p in [x for x in R.PUNTOS if x in puntos_sel]:
                g = s_otro[s_otro["punto"] == p]
                q, b = g[~g["bajo_limite"]], g[g["bajo_limite"]]
                if len(q):
                    fig.add_trace(go.Scatter(
                        x=q["fecha"], y=q["valor"], mode="lines+markers", name=p, legendgroup=p,
                        line=dict(color=colores[p]),
                        marker=dict(color=colores[p], size=7,
                                    symbol=["x" if v else "circle" for v in q["simulado"]]),
                        hovertemplate=f"{p}<br>%{{x|%d/%m/%Y}}<br>%{{y}} {unidad}<extra></extra>"))
                if len(b):
                    fig.add_trace(go.Scatter(
                        x=b["fecha"], y=b["valor"], mode="markers", name=f"{p} · bajo límite", legendgroup=p,
                        showlegend=not len(q),
                        marker=dict(symbol="triangle-down-open", size=11, color=colores[p], line=dict(width=2)),
                        hovertemplate=(f"{p}<br>%{{x|%d/%m/%Y}}<br>&lt; %{{y}} {unidad} "
                                       "(bajo límite de detección)<extra></extra>")))
            if s_otro["bajo_limite"].any():
                # entrada de leyenda neutra que explica el símbolo
                fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers", name="bajo límite de detección",
                                         marker=dict(symbol="triangle-down-open", size=11, color=GRIS_REFERENCIA,
                                                     line=dict(width=2))))
            fig.update_layout(height=460, legend_title_text="", margin=dict(t=30),
                              xaxis_title="Fecha", yaxis_title=f"{param_sel} ({unidad})" if unidad else param_sel)
            if log:
                fig.update_yaxes(type="log")
            st.plotly_chart(fig, width="stretch")
            st.info("Este parámetro no genera alertas en el prototipo; se muestra solo para consulta.")
            st.caption("▽ = medición bajo el límite de detección («Menor a»): se grafica el valor del límite. "
                       "Las X son datos simulados.")
    elif serie.empty:
        st.info("No hay datos para la selección.")
    else:
        fig = px.line(serie, x="fecha", y="valor", color="punto", markers=True, log_y=log,
                      symbol="origen", symbol_map={"Real": "circle", "Simulado": "x"},
                      labels={"valor": f"{param_sel} ({R.UNIDAD_CLAVE[param_sel]})", "fecha": "Fecha"},
                      category_orders={"punto": R.PUNTOS})
        fig.add_vline(x=pd.Timestamp(ss["corte"]), line_dash="dot", line_color=GRIS_REFERENCIA)
        fig.add_annotation(x=pd.Timestamp(ss["corte"]), y=1.02, yref="paper", text="corte línea base",
                           showarrow=False, font=dict(size=11, color=GRIS_REFERENCIA))
        x0, x1 = serie["fecha"].min(), serie["fecha"].max()
        # Las líneas de referencia se dibujan como trazos (no como formas) para que funcionen en escala log
        if param_sel == "Sulfato" and "ET-0F" in puntos_sel:
            fig.add_trace(go.Scatter(x=[x0, x1], y=[R.LIMITE_SULFATO_ET0F] * 2, mode="lines",
                                     line=dict(color="#c0392b", dash="dash"), name="Límite RCA ET-0F (250 mg/L)"))
        if len(puntos_sel) == 1:
            b = base[(base["punto"] == puntos_sel[0]) & (base["clave"] == param_sel)]
            if not b.empty:
                lo, hi = b["minimo"].iat[0], b["maximo"].iat[0]
                fig.add_trace(go.Scatter(x=[x0, x1, x1, x0, x0], y=[lo, lo, hi, hi, lo], fill="toself",
                                         fillcolor="rgba(46,134,222,0.12)", line=dict(width=0),
                                         name="Rango histórico (línea base)", hoverinfo="skip"))
        al = vigentes[(vigentes["parametro"] == param_sel) & (vigentes["punto"].isin(puntos_sel))]
        if not al.empty:
            fig.add_trace(go.Scatter(x=pd.to_datetime(al["fecha_dato"]), y=al["valor"], mode="markers",
                                     marker=dict(size=16, color="rgba(0,0,0,0)", line=dict(color="#c0392b", width=2)),
                                     name="Con alerta"))
        fig.update_layout(height=460, legend_title_text="", margin=dict(t=30))
        st.plotly_chart(fig, width="stretch")
        st.caption("Seleccione un solo punto para ver la banda del rango histórico. Los círculos rojos marcan "
                   "mediciones con alerta vigente; las X son datos simulados.")

    st.markdown("**Tendencias del período** (descriptivas: con 5 a 14 datos por punto no son estadísticamente robustas)")
    st.dataframe(R.tendencias(dv), width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# 3. Calidad de datos
# ---------------------------------------------------------------------------
with tabs[3]:
    st.subheader("Calidad de los datos (K1)")
    q1, q2 = st.columns([1, 2])
    with q1:
        st.dataframe(R.k1_por_punto(dv), width="stretch")
        st.caption("Inválido = copia repetida, unidad atípica, pH fuera de 0–14, concentración negativa o campo vacío.")
    with q2:
        inv = dv[~dv["valido"]]
        st.markdown(f"**Registros inválidos: {len(inv)}** (se conservan, pero no entran a los cálculos)")
        st.dataframe(inv[["id", "fecha", "punto", "parametro", "valor", "unidad", "motivo"]],
                     width="stretch", height=220, hide_index=True)
    an = dv[dv["anomalo"] != ""]
    st.markdown(f"**Valores anómalos en revisión: {len(an)}** (no se descartan)")
    st.dataframe(an[["id", "fecha", "punto", "parametro", "valor", "unidad", "anomalo"]],
                 width="stretch", hide_index=True)
    st.markdown("**Meses sin mediciones de pH, conductividad o sulfato** (la frecuencia de muestreo varía por punto; "
                "el vacío de junio a agosto de 2021 corresponde al cambio de planilla)")
    faltan = R.meses_sin_datos(dv)
    if not faltan.empty:
        st.dataframe(faltan.groupby("punto")["mes"].apply(lambda x: ", ".join(x)).reset_index(),
                     width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# 4. Alertas
# ---------------------------------------------------------------------------
with tabs[4]:
    st.subheader("Alertas y su ciclo: emisión → revisión → acción → cierre")
    st.markdown("🟡 **Revisión**: fuera del rango histórico, no es incumplimiento · "
                "🔴 **Posible superación normativa**: sulfato > 250 mg/L en ET-0F, requiere verificar vigencia del "
                "límite y calificador · ⚪ **Calidad del dato**")
    if base.empty:
        st.warning(AVISO_BASE_VACIA)
    if vigentes.empty:
        st.success("No hay alertas vigentes con la línea base y el margen actuales.")
    else:
        fpunto = st.multiselect("Filtrar por punto", R.PUNTOS, default=R.PUNTOS, key="al_puntos")
        ver = vigentes[vigentes["punto"].isin(fpunto)]
        cols = ["id", "tipo", "punto", "parametro", "fecha_dato", "valor", "criterio", "simulado", "estado",
                "responsable", "accion", "verificacion"]
        ver = ver.assign(valor=pd.to_numeric(ver["valor"], errors="coerce").round(2), fecha_dato=ver["fecha_dato"].astype(str).str[:16])
        st.dataframe(ver[cols].style.map(lambda v: COLOR_TIPO.get(v, ""), subset=["tipo"]),
                     width="stretch", hide_index=True, height=300)

        st.markdown("**Gestionar una alerta**")
        puede_gestionar = ss["rol"] in ("Encargado ambiental", "Administrador")
        if not puede_gestionar:
            st.info("Para gestionar alertas, seleccione el rol Encargado ambiental o Administrador.")
        etiquetas = {r.id: f"{r.id} · {r.tipo} · {r.punto} · {r.parametro} · {str(r.fecha_dato)[:10]} · {r.estado}"
                     for r in ver.itertuples()}
        if ss.get("alerta_sel") not in etiquetas:
            ss.pop("alerta_sel", None)
        if etiquetas:
            elegida = st.selectbox("Alerta", list(etiquetas), format_func=lambda i: etiquetas[i], key="alerta_sel")
            a = ver[ver["id"] == elegida].iloc[0]
            st.write(f"**Criterio:** {a['criterio']} · **Emitida:** {a['emitida']} · **Estado:** {a['estado']}"
                     + (" · ⚠️ dato SIMULADO" if a["simulado"] else ""))
            g1, g2, g3 = st.columns(3)
            with g1.form("revisar"):
                st.markdown("**1. Revisión**")
                resp = st.text_input("Responsable", value=a["responsable"] or ss["usuario"])
                dec = st.radio("Decisión", ["Confirmada: requiere acción", "Descartada: falso positivo"])
                if st.form_submit_button("Guardar revisión", disabled=not puede_gestionar or a["estado"] != "Abierta"):
                    A.revisar_alerta(con, a["id"], resp, dec, ss["usuario"], ss["rol"])
                    st.rerun()
            with g2.form("accion"):
                st.markdown("**2. Acción**")
                acc = st.text_area("Acción decidida", placeholder="Ej.: solicitar remuestreo del Pozo 8/A y revisar el dren")
                if st.form_submit_button("Registrar acción", disabled=not puede_gestionar or a["estado"] != "Revisada"):
                    if acc.strip():
                        A.accion_alerta(con, a["id"], acc, ss["usuario"], ss["rol"])
                        st.rerun()
                    else:
                        st.error("Describa la acción.")
            with g3.form("cerrar"):
                st.markdown("**3. Comprobación y cierre**")
                ver_txt = st.text_area("Cómo se verificó el resultado", placeholder="Ej.: la medición siguiente volvió al rango")
                if st.form_submit_button("Cerrar alerta", disabled=not puede_gestionar or a["estado"] != "Acción registrada"):
                    if ver_txt.strip():
                        A.cerrar_alerta(con, a["id"], ver_txt, ss["usuario"], ss["rol"])
                        st.rerun()
                    else:
                        st.error("La verificación es obligatoria para cerrar.")
    st.metric("K2 · tiempo de respuesta (h)", fmt(k2, 2))
    st.metric("K3 · alertas cerradas con verificación", fmt(k3, 1, " %"))
    with st.expander("Bitácora (trazabilidad de acciones)"):
        st.dataframe(A.leer_bitacora(con), width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# 5. Matriz ambiental
# ---------------------------------------------------------------------------
with tabs[5]:
    st.subheader("Matriz de impactos calculada con los datos de ingreso")
    st.markdown("P = M + E + D, cada criterio de 1 a 3 · Prioridad: baja 3–4, media 5–6, alta 7–9 · "
                "**Significativo**: M ≥ 2 y P ≥ 7, o declarado significativo por la RCA N.º 02/2020 · "
                "**Sensibilidad**: P' = 2M + E + D (baja 4–6, media 7–9, alta 10–12)")

    with st.expander("Variables de ingreso de la matriz", expanded=True):
        v1, v2, v3 = st.columns(3)
        v1.markdown("**Calculadas con los datos de agua**")
        v1.metric("pH mínimo medido (T1)", fmt(variables["ph_min"], 2))
        v1.metric("Sulfato mediano del Pozo 8/A (T2)", fmt(variables["sulfato_pozo_mediana"], 0, " mg/L"))
        v1.metric("Sulfato máximo en ET-0F (T3, T4)", fmt(variables["sulfato_et0f_max"], 0, " mg/L"))
        v2.markdown("**Plataforma (editables)**")
        v2.number_input("N.º de notebooks", 1, 500, key="eq_n_notebooks")
        v2.number_input("Potencia media por notebook (W)", 1.0, 500.0, key="eq_w_notebook")
        v2.number_input("Potencia del router (W)", 1.0, 200.0, key="eq_w_router")
        v3.number_input("Horas de uso por día", 1.0, 24.0, key="eq_horas_dia")
        v3.number_input("Días de uso por mes", 1, 31, key="eq_dias_mes")
        v3.number_input("vCPU del servidor en la nube", 1, 256, key="eq_vcpu")
        v3.metric("Consumo local estimado (K4)", fmt(kwh, 0, " kWh/año"))

    with st.expander("Umbrales para calcular la magnitud M (criterio del grupo, ajustables)"):
        x1, x2, x3 = st.columns(3)
        x1.number_input("T1: pH bajo el cual M = 2", step=0.1, key="u_ph_u1")
        x1.number_input("T1: pH bajo el cual M = 3", step=0.1, key="u_ph_u2")
        x2.number_input("T2: veces el agua natural sobre la cual M = 2", step=0.1, key="u_pozo_u1")
        x2.number_input("T2: veces el agua natural sobre la cual M = 3", step=0.1, key="u_pozo_u2")
        x3.number_input("T3/T4: fracción del límite sobre la cual M = 2", step=0.05, key="u_et0f_u1")
        x3.number_input("T3/T4: fracción del límite sobre la cual M = 3", step=0.05, key="u_et0f_u2")
        st.button("Restablecer umbrales", on_click=restablecer_umbrales)
        st.caption("Referencias: agua natural del acuífero ≈ 200 mg/L de sulfato y límite de 250 mg/L en ET-0F (RCA "
                   "N.º 02/2020). Los cortes de pH y de las razones son criterio del grupo.")

    with st.expander("Ajustar puntajes de filas sin regla de datos"):
        fijas = [f[0] for f in R.MATRIZ_TRANQUE + R.MATRIZ_PLATAFORMA if not f[-1]]
        y1, y2, y3, y4 = st.columns(4)
        fila = y1.selectbox("Fila", fijas)
        actual = pd.concat([mt, mp]).set_index("ID").loc[fila]
        nm = y2.selectbox("M", [1, 2, 3], index=int(actual["M"]) - 1, key=f"m_{fila}")
        ne = y3.selectbox("E", [1, 2, 3], index=int(actual["E"]) - 1, key=f"e_{fila}")
        nd = y4.selectbox("D", [1, 2, 3], index=int(actual["D"]) - 1, key=f"d_{fila}")
        b1, b2 = st.columns(2)
        if b1.button("Aplicar ajuste"):
            ss["ajustes"][fila] = {"M": nm, "E": ne, "D": nd}
            A.registrar(con, ss["usuario"], ss["rol"], "Ajuste de matriz", f"{fila}: M{nm} E{ne} D{nd}")
            st.rerun()
        b2.button("Restablecer todos los puntajes", on_click=restablecer_puntajes)

    st.markdown("### Matriz principal: impactos del tranque (T1–T12)")
    st.dataframe(estilo_matriz(mt), width="stretch", hide_index=True, height=36 * (len(mt) + 1) + 4)
    st.markdown("### Matriz complementaria: impactos de la plataforma (R1–R13)")
    st.dataframe(estilo_matriz(mp), width="stretch", hide_index=True, height=36 * (len(mp) + 1) + 4)

    st.markdown("### Resumen y sensibilidad")
    r1, r2 = st.columns(2)
    for col, nombre, m in [(r1, "Tranque", mt), (r2, "Plataforma", mp)]:
        resumen = pd.DataFrame({
            "P = M+E+D": m["Prioridad"].value_counts(),
            "P' = 2M+E+D": m["Prioridad P'"].value_counts()}).reindex(["Alta", "Media", "Baja"]).fillna(0).astype(int)
        col.markdown(f"**{nombre}** · significativos: {int((m['Significativo'] == 'Sí').sum())} de {len(m)}")
        col.dataframe(resumen, width="stretch")
    st.caption("Con P' (magnitud con doble peso) las filas de la plataforma, todas de magnitud pequeña, dejan de ser "
               "prioridad alta, mientras que T2 se mantiene alta: las prioridades altas de la plataforma se deben a "
               "la extensión y duración globales de los impactos sobre clima y recursos, no a su magnitud.")

# ---------------------------------------------------------------------------
# 6. Indicadores
# ---------------------------------------------------------------------------
with tabs[6]:
    st.subheader("Indicadores (KPI)")
    kpi = tabla_kpi()
    st.dataframe(kpi, width="stretch", hide_index=True)
    st.markdown("**Línea base de K9 a K11** (rango histórico con datos reales hasta el corte)")
    st.dataframe(base, width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# 7. Demo
# ---------------------------------------------------------------------------
with tabs[7]:
    st.subheader("Escenarios de demostración")
    st.markdown("Cada escenario reemplaza los datos simulados anteriores. Los datos reales no se modifican.")
    e1, e2, e3, e4 = st.columns(4)
    e1.button("1 · Escenario normal", on_click=escenario_normal, width="stretch")
    e2.button("2 · Anómalo (datos reales)", on_click=escenario_anomalo, width="stretch")
    e3.button("3 · Superación simulada", on_click=escenario_superacion, width="stretch")
    e4.button("Limpiar escenarios", on_click=escenario_limpiar, width="stretch")
    st.markdown("""
| Escenario | Qué hace | Resultado esperado |
|---|---|---|
| **1 · Normal** | Agrega mediciones simuladas de la laguna dentro de su rango (pH 7,6; 2.900 µS/cm; 1.350 mg/L) | Ninguna alerta nueva |
| **2 · Anómalo real** | Calcula la línea base solo hasta el 28/02/2022 y evalúa los datos reales posteriores | Alertas 🟡 de revisión; la más clara: Pozo 8/A, sulfato y conductividad suben juntos desde abril de 2022 |
| **3 · Superación simulada** | Agrega un sulfato **simulado** de 300 mg/L en ET-0F | Alerta 🔴 de posible superación normativa; en la matriz, T3 y T4 pasan a M3 |

**Guion sugerido**: (1) mostrar la carga y K1; (2) escenario 1 → sin alertas; (3) escenario 2 → dashboard del
Pozo 8/A y gestionar una alerta hasta cerrarla (K2 y K3 cambian); (4) escenario 3 → alerta roja y matriz
recalculada; (5) descargar el informe de salida.
""")
    st.markdown(f"**Estado actual**: línea base hasta {pd.Timestamp(ss['corte']):%d/%m/%Y} · "
                f"{int(registros['simulado'].sum())} registros simulados · {len(vigentes)} alertas vigentes")
    if ss["rol"] == "Administrador":
        with st.container(border=True):
            st.markdown("**Reiniciar base completa**: borra los datos agregados, las alertas y la bitácora, y "
                        "vuelve a cargar los 1.869 registros reales.")
            st.checkbox("Confirmo que quiero borrar todo", key="confirmar_reinicio")
            st.button("⚠️ Reiniciar base completa (borra alertas y bitácora)", on_click=reiniciar_todo,
                      disabled=not ss.get("confirmar_reinicio"))

# ---------------------------------------------------------------------------
# 8. Informe de salida
# ---------------------------------------------------------------------------
with tabs[8]:
    st.subheader("Informe de salida")
    kpi = tabla_kpi()
    resumen = {
        "Fecha del informe": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "Usuario y rol": f"{ss['usuario']} ({ss['rol']})",
        "Registros cargados": len(dv),
        "Registros simulados": int(registros["simulado"].sum()),
        "K1 datos válidos": fmt(R.k1(dv), 1, " %"),
        "Línea base hasta": f"{pd.Timestamp(ss['corte']):%d/%m/%Y}",
        "Margen de tolerancia": f"{ss['margen']} %",
        "Alertas vigentes": len(vigentes),
        "Posibles superaciones normativas": int((vigentes["tipo"] == "Posible superación normativa").sum()),
        "K2 tiempo de respuesta": fmt(k2, 2, " h"),
        "K3 alertas cerradas con verificación": fmt(k3, 1, " %"),
        "Matriz del tranque (alta/media/baja)": "/".join(str(int((mt["Prioridad"] == p).sum())) for p in ["Alta", "Media", "Baja"]),
        "Matriz de la plataforma (alta/media/baja)": "/".join(str(int((mp["Prioridad"] == p).sum())) for p in ["Alta", "Media", "Baja"]),
        "Impactos significativos del tranque": int((mt["Significativo"] == "Sí").sum()),
        "Consumo eléctrico local estimado": fmt(kwh, 0, " kWh/año"),
    }
    st.table(pd.DataFrame([(k, str(v)) for k, v in resumen.items()], columns=["Concepto", "Valor"]).set_index("Concepto"))
    serie_s = R.serie_clave(dv)
    serie_s = serie_s[serie_s["clave"] == "Sulfato"]
    xls = INF.excel(resumen, mt, mp, vigentes, kpi, base, dv[~dv["valido"]],
                    A.leer_bitacora(con), dv.drop(columns=["clave"]))
    htm = INF.html(resumen, mt, mp, vigentes, kpi, base, serie_s, ss["corte"])
    sello = datetime.now().strftime("%Y%m%d_%H%M")
    d1, d2 = st.columns(2)
    d1.download_button("📊 Descargar informe Excel", xls, f"informe_el_torito_{sello}.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch")
    d2.download_button("📄 Descargar informe HTML (imprimible a PDF)", htm.encode("utf-8"),
                       f"informe_el_torito_{sello}.html", "text/html", width="stretch")
