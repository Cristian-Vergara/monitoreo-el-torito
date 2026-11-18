"""
Almacenamiento en SQLite: registros de agua, alertas y bitácora.
La base se crea sola la primera vez con los datos reales de datos/torito_4_puntos.csv.

La aplicación comparte una sola conexión entre las sesiones y las re-ejecuciones de Streamlit, que corren
en hilos distintos. Por eso todas las escrituras (y las lecturas) pasan por un candado de módulo: así dos
clics seguidos en "Reiniciar base completa" no usan la conexión al mismo tiempo
("sqlite3.InterfaceError: bad parameter or other API misuse").
"""
from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime

import pandas as pd

import reglas as R

CARPETA = os.path.dirname(os.path.abspath(__file__))
RUTA_DB = os.environ.get("TORITO_DB", os.path.join(CARPETA, "datos", "monitoreo.db"))
RUTA_CSV = os.path.join(CARPETA, "datos", "torito_4_puntos.csv")

# RLock (reentrante): algunas funciones que escriben llaman a registrar(), que también toma el candado.
_CANDADO = threading.RLock()

ESQUEMA = """
CREATE TABLE IF NOT EXISTS registros (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fuente TEXT, fecha TEXT, punto TEXT, parametro TEXT, valor REAL,
    unidad TEXT, calificador TEXT, grupo TEXT, simulado INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS alertas (
    id TEXT PRIMARY KEY, tipo TEXT, punto TEXT, parametro TEXT, fecha_dato TEXT, valor REAL,
    criterio TEXT, simulado INTEGER, emitida TEXT, estado TEXT,
    responsable TEXT, decision TEXT, revisada TEXT, accion TEXT, fecha_accion TEXT,
    verificacion TEXT, cerrada TEXT
);
CREATE TABLE IF NOT EXISTS bitacora (
    id INTEGER PRIMARY KEY AUTOINCREMENT, momento TEXT, usuario TEXT, rol TEXT,
    evento TEXT, detalle TEXT
);
"""


def ahora() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def conectar(ruta: str = None) -> sqlite3.Connection:
    ruta = ruta or RUTA_DB
    os.makedirs(os.path.dirname(ruta) or ".", exist_ok=True)
    con = sqlite3.connect(ruta, check_same_thread=False)
    with _CANDADO:
        con.executescript(ESQUEMA)
        if con.execute("SELECT COUNT(*) FROM registros").fetchone()[0] == 0:
            cargar_base_real(con)
    return con


def _datos_reales() -> pd.DataFrame:
    return R.normalizar(pd.read_csv(RUTA_CSV))


def _insertar_sin_confirmar(con, df: pd.DataFrame) -> int:
    """Inserta filas dentro de la transacción en curso (no hace commit)."""
    d = df.copy()
    d["fecha"] = pd.to_datetime(d["fecha"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    d = d[R.COLUMNAS].astype(object).where(d[R.COLUMNAS].notna(), None)
    con.executemany(f"INSERT INTO registros ({', '.join(R.COLUMNAS)}) VALUES ({', '.join('?' * len(R.COLUMNAS))})",
                    d.itertuples(index=False, name=None))
    return len(d)


def cargar_base_real(con: sqlite3.Connection):
    insertar_registros(con, _datos_reales())


def insertar_registros(con, df: pd.DataFrame) -> int:
    with _CANDADO, con:
        return _insertar_sin_confirmar(con, df)


def leer_registros(con) -> pd.DataFrame:
    with _CANDADO:
        df = pd.read_sql("SELECT * FROM registros ORDER BY fecha, id", con)
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    return df


def actualizar_valor(con, id_registro: int, nuevo_valor: float, usuario: str, rol: str, motivo: str):
    with _CANDADO:
        anterior = con.execute("SELECT valor FROM registros WHERE id=?", (id_registro,)).fetchone()
        if anterior is None:
            raise ValueError(f"El registro {id_registro} no existe: no se corrigió nada.")
        with con:
            con.execute("UPDATE registros SET valor=? WHERE id=?", (nuevo_valor, id_registro))
        registrar(con, usuario, rol, "Corrección de dato",
                  f"Registro {id_registro}: {anterior[0]} -> {nuevo_valor}. Motivo: {motivo}")


def borrar_simulados(con):
    with _CANDADO, con:
        return con.execute("DELETE FROM registros WHERE simulado=1").rowcount


def reiniciar(con):
    """Borra todo y recarga los datos reales en UNA sola transacción (si algo falla, no queda a medias)."""
    datos = _datos_reales()   # se lee el CSV antes de tomar el candado
    with _CANDADO, con:
        con.execute("DELETE FROM registros")
        con.execute("DELETE FROM alertas")
        con.execute("DELETE FROM bitacora")
        _insertar_sin_confirmar(con, datos)


# ---------------- alertas ----------------
def sincronizar_alertas(con, nuevas: pd.DataFrame) -> int:
    """Inserta las alertas nuevas (las ya existentes conservan su estado y su hora de emisión)."""
    n = 0
    with _CANDADO, con:
        for _, a in nuevas.iterrows():
            cur = con.execute(
                "INSERT OR IGNORE INTO alertas (id,tipo,punto,parametro,fecha_dato,valor,criterio,simulado,emitida,estado)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (a["id"], a["tipo"], a["punto"], a["parametro"], str(pd.Timestamp(a["fecha"])), float(a["valor"]),
                 a["criterio"], int(a["simulado"]), ahora(), "Abierta"))
            n += cur.rowcount
    return n


def leer_alertas(con) -> pd.DataFrame:
    with _CANDADO:
        return pd.read_sql("SELECT * FROM alertas ORDER BY emitida, fecha_dato", con)


def revisar_alerta(con, id_alerta, responsable, decision, usuario, rol):
    with _CANDADO:
        with con:
            con.execute("UPDATE alertas SET responsable=?, decision=?, revisada=?, estado=? WHERE id=?",
                        (responsable, decision, ahora(), "Revisada", id_alerta))
        registrar(con, usuario, rol, "Alerta revisada", f"{id_alerta}: {decision}; responsable {responsable}")


def accion_alerta(con, id_alerta, accion, usuario, rol):
    with _CANDADO:
        with con:
            con.execute("UPDATE alertas SET accion=?, fecha_accion=?, estado=? WHERE id=?",
                        (accion, ahora(), "Acción registrada", id_alerta))
        registrar(con, usuario, rol, "Acción registrada", f"{id_alerta}: {accion}")


def cerrar_alerta(con, id_alerta, verificacion, usuario, rol):
    with _CANDADO:
        with con:
            con.execute("UPDATE alertas SET verificacion=?, cerrada=?, estado=? WHERE id=?",
                        (verificacion, ahora(), "Cerrada", id_alerta))
        registrar(con, usuario, rol, "Alerta cerrada", f"{id_alerta}: {verificacion}")


# ---------------- bitácora ----------------
def registrar(con, usuario, rol, evento, detalle):
    with _CANDADO, con:
        con.execute("INSERT INTO bitacora (momento,usuario,rol,evento,detalle) VALUES (?,?,?,?,?)",
                    (ahora(), usuario, rol, evento, detalle))


def leer_bitacora(con) -> pd.DataFrame:
    with _CANDADO:
        return pd.read_sql("SELECT momento, usuario, rol, evento, detalle FROM bitacora ORDER BY id DESC", con)
