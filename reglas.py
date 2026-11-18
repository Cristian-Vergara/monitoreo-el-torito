"""
Reglas del prototipo de monitoreo ambiental del tranque El Torito.

Este módulo no depende de Streamlit: contiene la validación de datos,
la línea base, las alertas, los indicadores (KPI) y el cálculo de la
matriz de impactos. Así se puede probar con pytest (test_reglas.py).
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Constantes del proyecto (fuente: informe del grupo y RCA N.º 02/2020)
# ---------------------------------------------------------------------------
PUNTOS = ["Laguna AO-3", "Dren AO-1", "Pozo 8/A", "ET-0F"]
DESCRIPCION_PUNTOS = {
    "Laguna AO-3": "Laguna de aguas claras: agua del propio depósito (origen)",
    "Dren AO-1": "Dren de captación de infiltraciones (parte de la mitigación)",
    "Pozo 8/A": "Pozo de monitoreo de agua subterránea aguas abajo",
    "ET-0F": "Estero El Cobre: cuerpo receptor superficial",
}

# Parámetros clave de los indicadores K9, K10 y K11
CLAVES = ["pH", "Conductividad", "Sulfato"]
UNIDAD_CLAVE = {"pH": "unidad pH", "Conductividad": "uS/cm", "Sulfato": "mg/L"}
KPI_CLAVE = {"pH": "K9", "Conductividad": "K10", "Sulfato": "K11"}

LIMITE_SULFATO_ET0F = 250.0      # mg/L, límite de referencia de la RCA para ET-0F
SULFATO_AGUA_NATURAL = 200.0     # mg/L, condición natural del acuífero según la RCA

COLUMNAS = ["fuente", "fecha", "punto", "parametro", "valor", "unidad",
            "calificador", "grupo", "simulado"]
OBLIGATORIAS = ["fecha", "punto", "parametro", "valor", "unidad"]

# Parámetros que pueden ser negativos por naturaleza (no se marcan como inválidos)
PUEDEN_SER_NEGATIVOS = {"Temperatura", "Error del Balance Iónico", "Potencial Redox"}

# Nombres simples aceptados en la carga y su equivalente en la base
ALIAS_PARAMETRO = {
    "ph": ("Potencial de Hidrógeno", "Parámetros de terreno"),
    "ph (terreno)": ("Potencial de Hidrógeno", "Parámetros de terreno"),
    "conductividad": ("Conductividad Eléctrica", "Parámetros de terreno"),
    "conductividad (terreno)": ("Conductividad Eléctrica", "Parámetros de terreno"),
    "sulfato": ("Sulfato", "Aniones mayoritarios"),
}
ALIAS_PUNTO = {
    "laguna": "Laguna AO-3", "laguna ao-3": "Laguna AO-3", "ao-3": "Laguna AO-3",
    "laguna tranque el torito": "Laguna AO-3",
    "dren": "Dren AO-1", "dren ao-1": "Dren AO-1", "ao-1": "Dren AO-1",
    "dren de captación de infiltraciones": "Dren AO-1",
    "pozo": "Pozo 8/A", "pozo 8/a": "Pozo 8/A", "pozo n 8/a": "Pozo 8/A", "pozo n°8/a": "Pozo 8/A",
    "et-0f": "ET-0F", "et0f": "ET-0F", "estero": "ET-0F",
}


def _sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def normalizar_unidad(u) -> str:
    if u is None or (isinstance(u, float) and np.isnan(u)):
        return ""
    return str(u).strip().lower().replace("µ", "u").replace("μ", "u").replace(" ", "")


# ---------------------------------------------------------------------------
# 1. Carga y normalización (variables de ingreso)
# ---------------------------------------------------------------------------
def normalizar(df: pd.DataFrame, fuente_por_defecto: str = "Carga manual") -> pd.DataFrame:
    """Lleva un DataFrame cargado al formato interno. Lanza ValueError si faltan columnas."""
    df = df.copy()
    df.columns = [_sin_tildes(str(c)).strip().lower() for c in df.columns]
    faltan = [c for c in OBLIGATORIAS if c not in df.columns]
    if faltan:
        raise ValueError("Faltan columnas obligatorias: " + ", ".join(faltan))
    if df.empty:
        raise ValueError("El archivo no tiene filas de datos.")

    if "fuente" not in df.columns:
        df["fuente"] = fuente_por_defecto
    if "calificador" not in df.columns:
        df["calificador"] = "Igual"
    if "grupo" not in df.columns:
        df["grupo"] = ""
    if "simulado" not in df.columns:
        df["simulado"] = 0

    # Puntos: se aceptan alias. Los vacíos (NaN o "") quedan como "" y validar() los marca "Punto vacío".
    df["punto"] = [ALIAS_PUNTO.get(p.lower(), p) for p in (texto_limpio(x) for x in df["punto"])]

    # Parámetros: se aceptan nombres simples (pH, Conductividad, Sulfato)
    def _param(row):
        clave = str(row["parametro"]).strip().lower()
        if clave in ALIAS_PARAMETRO:
            nombre, grupo = ALIAS_PARAMETRO[clave]
            return pd.Series([nombre, row["grupo"] if str(row["grupo"]).strip() else grupo])
        return pd.Series([str(row["parametro"]).strip(), row["grupo"]])

    df[["parametro", "grupo"]] = df.apply(_param, axis=1)
    df["fecha"] = leer_fechas(df["fecha"])
    # Texto con coma decimal y/o punto de miles ("7,5", "2.950", "1.400,5") -> número (ver a_numero)
    df["valor"] = pd.to_numeric(df["valor"].map(lambda v: a_numero(v) if isinstance(v, str) else v), errors="coerce")
    df["calificador"] = df["calificador"].fillna("Igual").astype(str).str.strip()
    df["simulado"] = pd.to_numeric(df["simulado"], errors="coerce").fillna(0).astype(int)
    df["fuente"] = df["fuente"].fillna(fuente_por_defecto).astype(str)
    df["unidad"] = df["unidad"].fillna("").astype(str)
    df["grupo"] = df["grupo"].fillna("").astype(str)
    return df[COLUMNAS]


def leer_fechas(serie: pd.Series) -> pd.Series:
    """Fechas ISO (AAAA-MM-DD) primero; el resto se lee con el día primero (dd/mm/aaaa, formato chileno).

    No se usa dayfirst=True sobre todo: con format="mixed" invierte día y mes también en fechas ISO.
    """
    if pd.api.types.is_datetime64_any_dtype(serie):
        return serie
    iso = pd.to_datetime(serie, format="ISO8601", errors="coerce")
    resto = iso.isna() & serie.notna()
    if resto.any():
        iso = iso.copy()
        iso[resto] = pd.to_datetime(serie[resto].astype(str), format="mixed", dayfirst=True, errors="coerce")
    return iso


def fin_del_dia(corte) -> pd.Timestamp:
    """Primer instante del día siguiente al corte: la línea base incluye todo el día del corte."""
    return pd.Timestamp(corte).normalize() + pd.Timedelta(days=1)


def texto_limpio(v) -> str:
    """Texto sin espacios; los vacíos (None, NaN, "") se devuelven como ""."""
    if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NA or v is pd.NaT:
        return ""
    return str(v).strip()


# Punto de miles sin coma: 1 a 3 dígitos (sin cero inicial) seguidos de grupos ".ddd" ("2.950", "12.345.678")
_SOLO_MILES = re.compile(r"^[+-]?[1-9]\d{0,2}(\.\d{3})+$")


def a_numero(v):
    """Convierte a número un texto con formato chileno o internacional. Devuelve NaN si no se puede.

    - Si tiene coma, la coma es decimal y los puntos son de miles: "1.400,5" -> 1400.5; "0,02" -> 0.02.
    - Si no tiene coma y es de la forma 2.950 / 12.345.678, los puntos son de miles: "2.950" -> 2950.
    - En otro caso el punto es decimal: "7.4" -> 7.4; "0.02" -> 0.02; "2900.0" -> 2900.
    Ambigüedad: "1.234" sin coma se lee como 1234 (miles), no como 1,234 (ver README).
    """
    if isinstance(v, str):
        t = v.strip().replace(" ", "").replace(" ", "")
        if t == "":
            return np.nan
        if "," in t:
            t = t.replace(".", "").replace(",", ".")
        elif _SOLO_MILES.match(t):
            t = t.replace(".", "")
        v = t
    try:
        return float(v)
    except (TypeError, ValueError):
        return np.nan


# --------------------------- lectura de archivos y carga múltiple ---------------------------
def leer_archivo(nombre: str, datos: bytes) -> pd.DataFrame:
    """Lee un CSV (separador , o ; y codificación UTF-8 o latin-1) o un Excel. Errores en español."""
    import csv
    import io
    if nombre.lower().endswith(".csv"):
        if not datos.strip():
            raise ValueError("El archivo está vacío.")
        for codificacion in ("utf-8-sig", "latin-1"):
            try:
                return pd.read_csv(io.BytesIO(datos), sep=None, engine="python", encoding=codificacion)
            except UnicodeDecodeError:
                continue
            except pd.errors.EmptyDataError:
                raise ValueError("El archivo está vacío.")
            except (pd.errors.ParserError, csv.Error) as e:
                raise ValueError(f"No se pudo leer el CSV; revise el separador y las comillas ({e}).")
    if nombre.lower().endswith((".xlsx", ".xls")):
        try:
            return pd.read_excel(io.BytesIO(datos))
        except Exception:
            raise ValueError("No se pudo leer el archivo Excel: está dañado o no es un .xlsx válido.")
    raise ValueError("Formato no admitido: use un archivo .csv o .xlsx.")


def preparar_carga(archivos, existentes: pd.DataFrame):
    """Prepara la carga de varios archivos a la vez.

    archivos: lista de (nombre, bytes). existentes: registros ya guardados (columnas COLUMNAS).
    Devuelve (resumen, nuevos, chequeo):
      - resumen: una fila por archivo con registros leídos, inválidos y el error (si no se pudo leer);
      - nuevos: dict {nombre: DataFrame normalizado} solo con los archivos que se pudieron leer;
      - chequeo: vista previa combinada validada contra la base y entre los propios archivos.
    Un archivo con error (de cualquier tipo) no impide cargar los demás: el error queda en su fila, en español.
    La columna 'observaciones' resume los motivos de los registros inválidos de cada archivo.
    """
    filas, nuevos = [], {}
    for nombre, datos in archivos:
        try:
            nuevos[nombre] = normalizar(leer_archivo(nombre, datos), fuente_por_defecto=nombre)
        except ValueError as e:
            error = str(e)
        except Exception as e:  # noqa: BLE001 — un archivo raro nunca debe mostrar un traceback
            error = (f"No se pudo procesar el archivo ({type(e).__name__}). Revise que tenga las columnas y el "
                     "formato de la plantilla.")
        else:
            error = ""
        filas.append({"archivo": nombre, "registros leídos": len(nuevos[nombre]) if not error else 0,
                      "inválidos": 0, "observaciones": "", "error": error})
    chequeo = pd.DataFrame(columns=COLUMNAS + ["archivo", "valido", "motivo"])
    if nuevos:
        combinado = pd.concat([d.assign(archivo=n) for n, d in nuevos.items()], ignore_index=True)
        base = existentes[COLUMNAS].assign(archivo="")
        chequeo = validar(pd.concat([base, combinado], ignore_index=True)).tail(len(combinado))
        for f in filas:
            inv = chequeo[(chequeo["archivo"] == f["archivo"]) & ~chequeo["valido"]]
            f["inválidos"] = len(inv)
            f["observaciones"] = "; ".join(dict.fromkeys(m for ms in inv["motivo"] for m in ms.split("; ") if m))
    columnas = ["archivo", "registros leídos", "inválidos", "observaciones", "error"]
    return pd.DataFrame(filas, columns=columnas), nuevos, chequeo


# --------------------------- ingreso de campaña ---------------------------
# Parámetros de campaña que se muestran primero y su nombre en la base, grupo y unidad
CAMPANA_PRIMEROS = {
    "pH": ("Potencial de Hidrógeno", "Parámetros de terreno", "unidad pH"),
    "Conductividad": ("Conductividad Eléctrica", "Parámetros de terreno", "uS/cm"),
    "Sulfato": ("Sulfato", None, None),
    "Temperatura": ("Temperatura", "Parámetros de terreno", None),
}


def catalogo_parametros(registros: pd.DataFrame) -> pd.DataFrame:
    """Catálogo de parámetros para el ingreso de campaña, construido con los datos de la base.

    Columnas: etiqueta (lo que ve el usuario), parametro (nombre en la base), grupo y unidad
    (los que predominan en la base). pH, conductividad y temperatura van como "Parámetros de terreno".
    Orden: pH, Conductividad, Sulfato, Temperatura y luego el resto en orden alfabético.
    """
    d = registros[registros["parametro"].astype(str).str.strip() != ""]

    def predominante(serie):
        s = serie.dropna().astype(str)
        s = s[s.str.strip() != ""]
        return s.value_counts().idxmax() if len(s) else ""

    filas = []
    usados = set()
    for etiqueta, (nombre, grupo, unidad) in CAMPANA_PRIMEROS.items():
        g = d[d["parametro"] == nombre]
        if grupo:
            g_t = g[g["grupo"] == grupo]
            g = g_t if len(g_t) else g
        filas.append({"etiqueta": etiqueta, "parametro": nombre,
                      "grupo": grupo or predominante(g["grupo"]),
                      "unidad": unidad or predominante(g["unidad"])})
        usados.add(nombre)
    for nombre in sorted(set(d["parametro"]) - usados, key=lambda s: _sin_tildes(s).lower()):
        g = d[d["parametro"] == nombre]
        filas.append({"etiqueta": nombre, "parametro": nombre, "grupo": predominante(g["grupo"]),
                      "unidad": predominante(g["unidad"])})
    return pd.DataFrame(filas)


def registros_campana(punto: str, fecha_hora, filas: pd.DataFrame, simulado: bool, catalogo: pd.DataFrame,
                      fuente: str = "Ingreso de campaña") -> pd.DataFrame:
    """Convierte la tabla de la campaña (columnas Parámetro y Valor) en registros (formato COLUMNAS).

    Se ignoran las filas sin parámetro o sin valor. Todas comparten punto, fecha y hora. Acepta coma decimal.
    Un valor escrito que no es número se conserva como vacío (queda inválido al validar).
    """
    cat = catalogo.set_index("etiqueta")
    salida = []
    for _, f in filas.iterrows():
        etiqueta = f.get("Parámetro")
        texto = f.get("Valor")
        if etiqueta is None or (isinstance(etiqueta, float) and np.isnan(etiqueta)) or str(etiqueta).strip() == "":
            continue
        if texto is None or (isinstance(texto, float) and np.isnan(texto)) or str(texto).strip() == "":
            continue
        info = cat.loc[etiqueta]
        salida.append({"fuente": fuente, "fecha": pd.Timestamp(fecha_hora), "punto": punto,
                       "parametro": info["parametro"], "valor": a_numero(texto), "unidad": info["unidad"],
                       "calificador": "Igual", "grupo": info["grupo"], "simulado": int(bool(simulado))})
    return pd.DataFrame(salida, columns=COLUMNAS)


def parametro_clave(parametro: str, grupo: str):
    """Devuelve 'pH', 'Conductividad' o 'Sulfato' si el registro alimenta K9-K11."""
    if parametro == "Potencial de Hidrógeno" and grupo == "Parámetros de terreno":
        return "pH"
    if parametro == "Conductividad Eléctrica" and grupo == "Parámetros de terreno":
        return "Conductividad"
    if parametro == "Sulfato":
        return "Sulfato"
    return None


# ---------------------------------------------------------------------------
# 2. Validación (K1) y calidad de datos
# ---------------------------------------------------------------------------
def validar(df: pd.DataFrame) -> pd.DataFrame:
    """Agrega las columnas 'valido', 'motivo', 'anomalo', 'clave'.

    Inválido = falta un campo obligatorio, copia repetida exacta, unidad atípica,
    pH fuera de 0-14 o concentración negativa. Un valor anómalo NO es inválido:
    se marca para revisión.
    """
    d = df.copy().reset_index(drop=True)
    motivos = [[] for _ in range(len(d))]

    # a) campos obligatorios
    for i in d.index[d["fecha"].isna()]:
        motivos[i].append("Fecha no reconocida")
    puntos = [texto_limpio(p) for p in d["punto"]]
    for i, p in zip(d.index, puntos):
        if p == "":
            motivos[i].append("Punto vacío")
        elif p not in PUNTOS:
            motivos[i].append(f"Punto desconocido ({p})")
    falta = d["valor"].isna() | (d["parametro"].astype(str).str.strip() == "")
    for i in d.index[falta]:
        motivos[i].append("Campo obligatorio vacío o no numérico")

    # b) copias repetidas exactas (se conserva la primera)
    llave = ["fuente", "punto", "fecha", "parametro", "valor", "unidad", "calificador", "grupo"]
    dup = d.duplicated(subset=llave, keep="first")
    for i in d.index[dup]:
        motivos[i].append("Copia repetida exacta")

    # c) unidad atípica: distinta de la unidad predominante del parámetro
    un = d["unidad"].map(normalizar_unidad)
    predominante = un.groupby(d["parametro"]).agg(lambda x: x.value_counts().idxmax() if len(x) else "")
    for i in d.index:
        p = d.at[i, "parametro"]
        clave = parametro_clave(p, d.at[i, "grupo"])
        esperada = normalizar_unidad(UNIDAD_CLAVE[clave]) if clave else predominante.get(p, "")
        if esperada and un.iat[i] and un.iat[i] != esperada:
            motivos[i].append(f"Unidad atípica ({d.at[i, 'unidad']}; se espera {esperada})")

    # d) pH fuera de rango y e) negativos
    es_ph = d["parametro"] == "Potencial de Hidrógeno"
    for i in d.index[es_ph & ((d["valor"] < 0) | (d["valor"] > 14))]:
        motivos[i].append("pH fuera del rango 0-14")
    neg = (d["valor"] < 0) & ~d["parametro"].isin(PUEDEN_SER_NEGATIVOS) & ~es_ph
    for i in d.index[neg]:
        motivos[i].append("Concentración negativa")

    d["motivo"] = ["; ".join(m) for m in motivos]
    d["valido"] = d["motivo"] == ""
    d["clave"] = [parametro_clave(p, g) for p, g in zip(d["parametro"], d["grupo"])]

    # Anomalía: conductividad de terreno muy distinta de la de laboratorio del mismo día
    d["anomalo"] = ""
    lab = d[(d["parametro"] == "Conductividad Eléctrica") & (d["clave"].isna()) & d["valido"]]
    lab_dia = lab.assign(dia=lab["fecha"].dt.date).groupby(["punto", "dia"])["valor"].median()
    terreno = d.index[(d["clave"] == "Conductividad") & d["valido"]]
    for i in terreno:
        k = (d.at[i, "punto"], d.at[i, "fecha"].date())
        if k in lab_dia.index and lab_dia[k] > 0:
            razon = d.at[i, "valor"] / lab_dia[k]
            if razon < 0.2 or razon > 5:
                d.at[i, "anomalo"] = (f"Conductividad de terreno ({d.at[i, 'valor']:g}) muy distinta "
                                      f"de la de laboratorio del mismo día ({lab_dia[k]:g}); en revisión")
    return d


def k1(dv: pd.DataFrame) -> float:
    """K1 = registros válidos / registros cargados x 100."""
    return 100.0 * dv["valido"].sum() / len(dv) if len(dv) else float("nan")


def k1_por_punto(dv: pd.DataFrame) -> pd.DataFrame:
    t = dv.groupby("punto").agg(registros=("valido", "size"), invalidos=("valido", lambda x: (~x).sum()))
    t["K1 (%)"] = 100 * (t["registros"] - t["invalidos"]) / t["registros"]
    tot = pd.DataFrame({"registros": [t["registros"].sum()], "invalidos": [t["invalidos"].sum()]},
                       index=["Total"])
    tot["K1 (%)"] = 100 * (tot["registros"] - tot["invalidos"]) / tot["registros"]
    return pd.concat([t, tot]).round({"K1 (%)": 1})


def serie_clave(dv: pd.DataFrame) -> pd.DataFrame:
    """Mediciones válidas de pH, conductividad y sulfato, cuantificadas y sin anomalías.

    'Menor a' (bajo el límite de detección) no entra al cálculo de rangos.
    'Mayor a' de la planilla AG1 se trata como valor cuantificado (ver informe).
    """
    s = dv[dv["valido"] & dv["clave"].notna() & (dv["anomalo"] == "")
           & dv["calificador"].isin(["Igual", "Mayor a"])]
    return s.sort_values("fecha")


PARAMETROS_DE_ALERTA = {"Potencial de Hidrógeno", "Conductividad Eléctrica", "Sulfato"}


def otros_parametros(dv: pd.DataFrame) -> list:
    """Parámetros con datos válidos que no generan alertas (solo visualización), en orden alfabético."""
    s = dv[dv["valido"] & ~dv["parametro"].isin(PARAMETROS_DE_ALERTA)]
    return sorted(s["parametro"].dropna().unique(), key=lambda x: _sin_tildes(str(x)).lower())


def serie_parametro(dv: pd.DataFrame, parametro: str, puntos) -> pd.DataFrame:
    """Valores válidos de un parámetro cualquiera en los puntos elegidos (solo visualización).

    Agrega la columna 'bajo_limite' (calificador "Menor a": el valor es el límite de detección).
    """
    s = dv[dv["valido"] & (dv["parametro"] == parametro) & dv["punto"].isin(puntos)].copy()
    s["bajo_limite"] = s["calificador"].eq("Menor a")
    return s.sort_values("fecha")


def unidad_predominante(dv: pd.DataFrame, parametro: str) -> str:
    u = dv.loc[dv["parametro"] == parametro, "unidad"].dropna().astype(str)
    u = u[u.str.strip() != ""]
    return u.value_counts().idxmax() if len(u) else ""


def meses_sin_datos(dv: pd.DataFrame) -> pd.DataFrame:
    """Meses sin ninguna medición válida de parámetros clave, por punto."""
    s = serie_clave(dv)
    if s.empty:
        return pd.DataFrame(columns=["punto", "mes"])
    meses = pd.period_range(s["fecha"].min().to_period("M"), s["fecha"].max().to_period("M"), freq="M")
    filas = []
    for p in PUNTOS:
        con = set(s.loc[s["punto"] == p, "fecha"].dt.to_period("M"))
        filas += [{"punto": p, "mes": str(m)} for m in meses if m not in con]
    return pd.DataFrame(filas)


# ---------------------------------------------------------------------------
# 3. Línea base, tendencias y alertas
# ---------------------------------------------------------------------------
def linea_base(dv: pd.DataFrame, corte=None) -> pd.DataFrame:
    """Mínimo, mediana y máximo por punto y parámetro, con datos reales hasta 'corte'."""
    s = serie_clave(dv)
    s = s[s["simulado"] == 0]
    if corte is not None:
        s = s[s["fecha"] < fin_del_dia(corte)]
    b = s.groupby(["punto", "clave"])["valor"].agg(n="count", minimo="min", mediana="median", maximo="max")
    return b.reset_index()


def tendencias(dv: pd.DataFrame) -> pd.DataFrame:
    """Pendiente (unidades/año) y variación entre la primera y la última medición. Descriptivo."""
    filas = []
    for (p, c), g in serie_clave(dv).groupby(["punto", "clave"]):
        if len(g) < 3:
            continue
        x = (g["fecha"] - g["fecha"].min()).dt.days.values / 365.25
        pend = np.polyfit(x, g["valor"].values, 1)[0] if x.max() > 0 else np.nan
        var = 100 * (g["valor"].iloc[-1] - g["valor"].iloc[0]) / g["valor"].iloc[0]
        filas.append({"punto": p, "parametro": c, "n": len(g), "pendiente_por_año": round(pend, 2),
                      "variación_primera_a_última_%": round(var, 1),
                      "tendencia_lineal": "al alza" if pend > 0 else "a la baja"})
    return pd.DataFrame(filas)


def id_alerta(punto, clave, fecha, tipo) -> str:
    base = f"{punto}|{clave}|{pd.Timestamp(fecha).isoformat()}|{tipo}"
    return hashlib.sha1(base.encode()).hexdigest()[:10]


def generar_alertas(dv: pd.DataFrame, corte, margen_pct: float = 10.0) -> pd.DataFrame:
    """Evalúa las mediciones posteriores a 'corte' contra la línea base hasta 'corte'.

    Tipos de alerta (umbral justificado):
      - "Posible superación normativa": sulfato en ET-0F sobre 250 mg/L (límite RCA).
      - "Revisión": valor fuera del rango histórico del punto, ampliado en 'margen_pct' %.
      - "Calidad del dato": registro inválido o anómalo posterior al corte.
    Una alerta de revisión indica la necesidad de revisar, no un incumplimiento.
    """
    base = linea_base(dv, corte).set_index(["punto", "clave"])
    corte = fin_del_dia(corte)
    s = serie_clave(dv)
    nuevos = s[s["fecha"] >= corte]
    filas = []
    for _, r in nuevos.iterrows():
        p, c, v = r["punto"], r["clave"], r["valor"]
        if p == "ET-0F" and c == "Sulfato" and v > LIMITE_SULFATO_ET0F:
            filas.append(dict(tipo="Posible superación normativa", punto=p, parametro=c, fecha=r["fecha"],
                              valor=v, criterio=f"Sulfato {v:g} mg/L > límite RCA de {LIMITE_SULFATO_ET0F:g} mg/L en ET-0F",
                              simulado=r["simulado"]))
            continue
        if (p, c) not in base.index or base.loc[(p, c), "n"] < 3:
            continue
        lo, hi = base.loc[(p, c), "minimo"], base.loc[(p, c), "maximo"]
        ancho = (hi - lo) if hi > lo else abs(hi)
        tol = ancho * margen_pct / 100.0
        if v < lo - tol or v > hi + tol:
            filas.append(dict(tipo="Revisión", punto=p, parametro=c, fecha=r["fecha"], valor=v,
                              criterio=(f"{v:g} fuera del rango histórico [{lo:g}; {hi:g}] "
                                        f"(+/- {margen_pct:g} % del ancho)"),
                              simulado=r["simulado"]))
    # calidad del dato posterior al corte
    q = dv[(dv["fecha"] >= corte) & (~dv["valido"] | (dv["anomalo"] != "")) & dv["clave"].notna()]
    for _, r in q.iterrows():
        filas.append(dict(tipo="Calidad del dato", punto=r["punto"], parametro=r["clave"], fecha=r["fecha"],
                          valor=r["valor"], criterio=r["motivo"] or r["anomalo"], simulado=r["simulado"]))
    a = pd.DataFrame(filas, columns=["tipo", "punto", "parametro", "fecha", "valor", "criterio", "simulado"])
    if not a.empty:
        a.insert(0, "id", [id_alerta(*x) for x in zip(a["punto"], a["parametro"], a["fecha"], a["tipo"])])
    else:
        a.insert(0, "id", [])
    return a


ESTADOS = ["Abierta", "Revisada", "Acción registrada", "Cerrada"]


def k2_k3(alertas: pd.DataFrame):
    """K2 = promedio de horas entre emisión y acción; K3 = cerradas con verificación / emitidas x 100."""
    if alertas is None or alertas.empty:
        return float("nan"), float("nan")
    em = pd.to_datetime(alertas["emitida"], errors="coerce")
    ac = pd.to_datetime(alertas["fecha_accion"], errors="coerce")
    horas = ((ac - em).dt.total_seconds() / 3600).dropna()
    k2 = horas.mean() if len(horas) else float("nan")
    cerradas = ((alertas["estado"] == "Cerrada") & alertas["verificacion"].fillna("").astype(str).str.strip().ne("")).sum()
    k3 = 100.0 * cerradas / len(alertas)
    return k2, k3


# ---------------------------------------------------------------------------
# 4. Matriz de impactos calculada según los datos de ingreso
# ---------------------------------------------------------------------------
def prioridad(p: float) -> str:
    if p <= 4:
        return "Baja"
    if p <= 6:
        return "Media"
    return "Alta"


def prioridad_ponderada(p2: float) -> str:
    """Escala de sensibilidad P' = 2M + E + D (rango 4 a 12)."""
    if p2 <= 6:
        return "Baja"
    if p2 <= 9:
        return "Media"
    return "Alta"


def variables_desde_datos(dv: pd.DataFrame) -> dict:
    """Variables de la matriz principal que se calculan con los datos de agua."""
    s = serie_clave(dv)
    ph = s[s["clave"] == "pH"]["valor"]
    pozo = s[(s["punto"] == "Pozo 8/A") & (s["clave"] == "Sulfato")]["valor"]
    et = s[(s["punto"] == "ET-0F") & (s["clave"] == "Sulfato")]["valor"]
    return {
        "ph_min": float(ph.min()) if len(ph) else np.nan,
        "sulfato_pozo_mediana": float(pozo.median()) if len(pozo) else np.nan,
        "sulfato_et0f_max": float(et.max()) if len(et) else np.nan,
    }


def m_por_umbral(valor, u1, u2, ascendente=True) -> int:
    """1, 2 o 3 según dos umbrales. ascendente=False para variables donde menor es peor (pH)."""
    if valor is None or np.isnan(valor):
        return 2  # sin dato: se asume moderado y se informa
    if ascendente:
        return 1 if valor <= u1 else (2 if valor <= u2 else 3)
    return 1 if valor >= u1 else (2 if valor >= u2 else 3)


# Matriz principal: (ID, actividad, componente, aspecto, impacto, N.º RCA, E, D, M por defecto, regla)
MATRIZ_TRANQUE = [
    ("T1", "AT1", "Agua y suelo", "Posible generación de acidez del relave", "Alteración de la calidad del agua y del suelo", "—", 2, 3, 1, "ph"),
    ("T2", "AT2", "Agua subterránea", "Infiltración de agua de contacto hacia el acuífero", "Deterioro de la calidad del agua subterránea", "8", 2, 3, 3, "pozo"),
    ("T3", "AT2", "Agua superficial", "Llegada de las infiltraciones al estero El Cobre", "Alteración de la calidad del agua superficial", "9", 2, 3, 2, "et0f"),
    ("T4", "AT2", "Fauna (peces)", "Metales y sales en el agua del estero", "Alteración de la ictiofauna nativa", "10", 2, 3, 2, "et0f"),
    ("T5", "AT1", "Fauna (aves)", "Espejo de agua que atrae aves acuáticas", "Afectación y mortalidad de aves acuáticas", "6", 2, 3, 2, None),
    ("T6", "AT3", "Suelo", "Ocupación de terreno por la cubeta y las obras", "Pérdida de suelo de capacidad de uso II y III", "1", 1, 3, 2, None),
    ("T7", "AT3", "Flora", "Corta de vegetación nativa", "Pérdida de bosque nativo de preservación", "2", 1, 3, 2, None),
    ("T8", "AT3", "Flora", "Corta de vegetación nativa", "Pérdida de matorral singular", "3", 1, 3, 1, None),
    ("T9", "AT3", "Flora", "Corta de individuos en categoría de conservación", "Afectación de guayacán y naranjillo", "4", 1, 3, 1, None),
    ("T10", "AT3", "Fauna", "Movimiento de tierra y avance de la cubeta", "Afectación de fauna de baja movilidad", "5", 1, 3, 2, None),
    ("T11", "AT3", "Fauna", "Remoción de vegetación nativa", "Pérdida de hábitat de fauna silvestre", "7", 1, 3, 2, None),
    ("T12", "AT4", "Medio humano", "Presencia del depósito y antecedente de colapsos en 1965", "Percepción de riesgo en la población", "11", 2, 3, 2, None),
]

# Matriz complementaria: (ID, actividad, componente, aspecto, impacto, signo, E, D, M por defecto, regla)
MATRIZ_PLATAFORMA = [
    ("R1", "A1", "Clima", "Emisiones de fabricación y transporte de los equipos", "Contribución al cambio climático", "−", 3, 3, 1, "equipos"),
    ("R2", "A1", "Recursos", "Demanda de metales, plásticos, circuitos y baterías", "Agotamiento de recursos materiales", "−", 3, 3, 1, "equipos"),
    ("R3", "A2", "Clima", "Consumo de electricidad de notebooks y router", "Contribución al cambio climático", "−", 3, 3, 1, "kwh"),
    ("R4", "A2", "Recursos", "Electricidad generada en parte con combustibles fósiles", "Agotamiento de recursos no renovables", "−", 3, 3, 1, "kwh"),
    ("R5", "A3", "Clima", "Electricidad atribuible a servidores del proveedor", "Contribución al cambio climático", "−", 3, 3, 1, "vcpu"),
    ("R6", "A3", "Recursos", "Uso de infraestructura física del proveedor", "Agotamiento de recursos materiales", "−", 3, 3, 1, "vcpu"),
    ("R7", "A3", "Agua", "Agua de refrigeración atribuible al servicio, si aplica", "Presión sobre la disponibilidad hídrica local", "−", 2, 2, 1, "vcpu"),
    ("R8", "A4", "Recursos", "Residuos de embalaje y papel", "Pérdida de materiales recuperables", "−", 2, 3, 1, None),
    ("R9", "A4", "Suelo", "Residuos comunes en relleno sanitario", "Ocupación y alteración del suelo", "−", 1, 3, 1, None),
    ("R10", "A5", "Recursos", "Retiro de equipos sin recuperar materiales", "Pérdida de materiales recuperables", "−", 3, 3, 1, "equipos"),
    ("R11", "A6", "Suelo", "Posible liberación de metales de baterías", "Contaminación del suelo", "−", 1, 3, 1, None),
    ("R12", "A6", "Agua", "Lixiviación de esas mismas sustancias", "Contaminación del agua", "−", 2, 3, 1, None),
    ("R13", "A7", "Agua", "Información para detectar cambios y actuar a tiempo", "Posible reducción de afectaciones hídricas", "+", 2, 2, 1, None),
]

# Umbrales por defecto (criterio del grupo, ajustables en la aplicación)
UMBRALES = {
    "ph_u1": 6.0, "ph_u2": 4.5,            # pH mínimo >= 6 -> M1; 4,5-6 -> M2; < 4,5 -> M3
    "pozo_u1": 1.5, "pozo_u2": 3.0,        # razón sulfato pozo / 200 mg/L
    "et0f_u1": 0.8, "et0f_u2": 1.0,        # razón sulfato máx ET-0F / 250 mg/L
    "kwh_u1": 1000.0, "kwh_u2": 10000.0,   # kWh/año
    "equipos_u1": 10, "equipos_u2": 50,    # n.º de equipos
    "vcpu_u1": 4, "vcpu_u2": 16,           # vCPU contratadas
}


def energia_kwh_anual(n_notebooks, w_notebook, w_router, horas_dia, dias_mes, meses=12) -> float:
    """Ecuación 1 del informe: E = sum(n x P x h) / 1000."""
    return (n_notebooks * w_notebook + w_router) * horas_dia * dias_mes * meses / 1000.0


def calcular_matriz(variables: dict, umbrales: dict | None = None, ajustes: dict | None = None):
    """Devuelve (matriz_tranque, matriz_plataforma) como DataFrames.

    variables: ph_min, sulfato_pozo_mediana, sulfato_et0f_max, kwh_anual, n_equipos, vcpu
    ajustes: {ID: {"M":..,"E":..,"D":..}} para modificar filas sin regla de datos.
    """
    u = dict(UMBRALES, **(umbrales or {}))
    ajustes = ajustes or {}
    razon_pozo = variables["sulfato_pozo_mediana"] / SULFATO_AGUA_NATURAL
    razon_et = variables["sulfato_et0f_max"] / LIMITE_SULFATO_ET0F

    reglas_m = {
        "ph": (m_por_umbral(variables["ph_min"], u["ph_u1"], u["ph_u2"], ascendente=False),
               f"pH mínimo medido {variables['ph_min']:.2f} (M1 si ≥ {u['ph_u1']:g}; M3 si < {u['ph_u2']:g})"),
        "pozo": (m_por_umbral(razon_pozo, u["pozo_u1"], u["pozo_u2"]),
                 f"Sulfato mediano del pozo {variables['sulfato_pozo_mediana']:.0f} mg/L = {razon_pozo:.1f} veces "
                 f"el agua natural (200 mg/L) (M1 si ≤ {u['pozo_u1']:g}; M3 si > {u['pozo_u2']:g})"),
        "et0f": (m_por_umbral(razon_et, u["et0f_u1"], u["et0f_u2"]),
                 f"Sulfato máximo en ET-0F {variables['sulfato_et0f_max']:.0f} mg/L = {100*razon_et:.0f} % del límite "
                 f"RCA (M1 si ≤ {100*u['et0f_u1']:g} %; M3 si > {100*u['et0f_u2']:g} %)"),
        "kwh": (m_por_umbral(variables["kwh_anual"], u["kwh_u1"], u["kwh_u2"]),
                f"Consumo local estimado {variables['kwh_anual']:.0f} kWh/año (M1 si ≤ {u['kwh_u1']:g})"),
        "equipos": (m_por_umbral(variables["n_equipos"], u["equipos_u1"], u["equipos_u2"]),
                    f"{variables['n_equipos']:g} equipos (M1 si ≤ {u['equipos_u1']:g})"),
        "vcpu": (m_por_umbral(variables["vcpu"], u["vcpu_u1"], u["vcpu_u2"]),
                 f"Servidor de {variables['vcpu']:g} vCPU (M1 si ≤ {u['vcpu_u1']:g})"),
    }

    def _armar(filas, es_tranque):
        out = []
        for f in filas:
            if es_tranque:
                id_, act, comp, asp, imp, rca, e, d, m0, regla = f
                signo = "−"
            else:
                id_, act, comp, asp, imp, signo, e, d, m0, regla = f
                rca = ""
            if regla:
                m, origen = reglas_m[regla]
                origen = ("Calculada con datos de agua: " if es_tranque else "Calculada con variables de ingreso: ") + origen
            else:
                m = m0
                origen = "Fija (RCA)" if es_tranque else "Fija (juicio del grupo)"
            aj = ajustes.get(id_, {})
            if not regla and "M" in aj:
                m = int(aj["M"])
                origen += " · ajustada"
            e = int(aj.get("E", e))
            d = int(aj.get("D", d))
            p = m + e + d
            p2 = 2 * m + e + d
            significativo = (m >= 2 and p >= 7) or (es_tranque and rca not in ("—", ""))
            fila = {"ID": id_, "Actividad": act, "Componente": comp, "Aspecto": asp, "Impacto": imp}
            if es_tranque:
                fila["N.º RCA"] = rca
            else:
                fila["Signo"] = signo
            fila.update({"M": m, "E": e, "D": d, "P = M+E+D": p, "Prioridad": prioridad(p),
                         "P' = 2M+E+D": p2, "Prioridad P'": prioridad_ponderada(p2),
                         "Significativo": "Sí" if significativo else "No", "Origen de M": origen})
            out.append(fila)
        return pd.DataFrame(out)

    return _armar(MATRIZ_TRANQUE, True), _armar(MATRIZ_PLATAFORMA, False)
