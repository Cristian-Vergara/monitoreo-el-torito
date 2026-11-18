"""Casos de prueba del prototipo. Ejecutar:  pytest -q"""
import os

import pandas as pd
import pytest

import reglas as R

CSV = os.path.join(os.path.dirname(__file__), "datos", "torito_4_puntos.csv")


@pytest.fixture(scope="module")
def base():
    return R.validar(R.normalizar(pd.read_csv(CSV)))


def _con(base, filas):
    nuevo = R.normalizar(pd.DataFrame(filas))
    return R.validar(pd.concat([base[R.COLUMNAS], nuevo], ignore_index=True))


def test_k1_base_real(base):
    # Línea base del informe: 16 inválidos de 1.869 -> 99,1 %
    assert len(base) == 1869
    assert (~base["valido"]).sum() == 16
    assert round(R.k1(base), 1) == 99.1


def test_ph_fuera_de_rango_es_invalido(base):
    d = _con(base, [{"fecha": "2022-10-01", "punto": "Laguna AO-3", "parametro": "pH", "valor": 15, "unidad": "unidad pH"}])
    assert not d.iloc[-1]["valido"]
    assert "pH fuera" in d.iloc[-1]["motivo"]


def test_copia_repetida_se_detecta(base):
    fila = {"fecha": "2022-10-01", "punto": "ET-0F", "parametro": "Sulfato", "valor": 80, "unidad": "mg/L"}
    d = _con(base, [fila, fila])
    assert d.iloc[-2]["valido"] and not d.iloc[-1]["valido"]


def test_unidad_atipica(base):
    d = _con(base, [{"fecha": "2022-10-01", "punto": "ET-0F", "parametro": "Sulfato", "valor": 80, "unidad": "uS/cm"}])
    assert "Unidad atípica" in d.iloc[-1]["motivo"]


def test_anomalia_conductividad_no_se_descarta(base):
    an = base[base["anomalo"] != ""]
    assert len(an) == 1 and an.iloc[0]["valor"] == 18.67 and an.iloc[0]["valido"]


def test_linea_base_coincide_con_informe(base):
    b = R.linea_base(base).set_index(["punto", "clave"])
    assert b.loc[("Pozo 8/A", "Sulfato"), "minimo"] == pytest.approx(704.4)
    assert b.loc[("Pozo 8/A", "Sulfato"), "maximo"] == pytest.approx(1852.1)
    assert b.loc[("ET-0F", "Sulfato"), "maximo"] == pytest.approx(210.3)
    assert b.loc[("Laguna AO-3", "pH"), "n"] == 14


def test_sin_alertas_con_toda_la_base(base):
    assert R.generar_alertas(base, "2022-09-30").empty


def test_escenario_normal_sin_alerta(base):
    d = _con(base, [{"fecha": "2022-10-15", "punto": "Laguna AO-3", "parametro": "Sulfato", "valor": 1300,
                     "unidad": "mg/L", "simulado": 1}])
    assert R.generar_alertas(d, "2022-09-30").empty


def test_escenario_anomalo_pozo_genera_revision(base):
    a = R.generar_alertas(base, "2022-02-28", 10)
    pozo = a[(a["punto"] == "Pozo 8/A") & (a["parametro"] == "Sulfato")]
    assert (pozo["tipo"] == "Revisión").all()
    assert pd.Timestamp("2022-04-08 10:10") in set(pozo["fecha"])


def test_superacion_et0f_es_roja(base):
    d = _con(base, [{"fecha": "2022-10-15", "punto": "ET-0F", "parametro": "Sulfato", "valor": 300,
                     "unidad": "mg/L", "simulado": 1}])
    a = R.generar_alertas(d, "2022-09-30")
    assert list(a["tipo"]) == ["Posible superación normativa"]


def test_k2_k3():
    al = pd.DataFrame({"emitida": ["2022-10-01 08:00", "2022-10-01 08:00"],
                       "fecha_accion": ["2022-10-01 10:00", None],
                       "estado": ["Cerrada", "Abierta"], "verificacion": ["Remuestreo OK", None]})
    k2, k3 = R.k2_k3(al)
    assert k2 == pytest.approx(2.0) and k3 == pytest.approx(50.0)


def _variables(base, **extra):
    v = R.variables_desde_datos(base)
    v.update(kwh_anual=R.energia_kwh_anual(3, 30, 10, 8, 22), n_equipos=4, vcpu=2)
    v.update(extra)
    return v


def test_matriz_coincide_con_informe(base):
    mt, mp = R.calcular_matriz(_variables(base))
    assert dict(zip(mt["ID"], mt["P = M+E+D"])) == {"T1": 6, "T2": 8, "T3": 7, "T4": 7, "T5": 7, "T6": 6, "T7": 6,
                                                   "T8": 5, "T9": 5, "T10": 6, "T11": 6, "T12": 7}
    assert (mt["Prioridad"] == "Alta").sum() == 5 and (mp["Prioridad"] == "Alta").sum() == 7


def test_energia_ecuacion_1():
    assert R.energia_kwh_anual(3, 30, 10, 8, 22) == pytest.approx(211.2)


def test_matriz_se_recalcula_con_datos(base):
    _, mp = R.calcular_matriz(_variables(base))
    mt2, _ = R.calcular_matriz(_variables(base, sulfato_et0f_max=300))
    assert mt2.set_index("ID").loc["T3", "M"] == 3          # sobre el límite -> M3
    assert mt2.set_index("ID").loc["T3", "P = M+E+D"] == 8
    # sensibilidad: con P' las filas de la plataforma dejan de ser alta
    assert (mp["Prioridad P'"] == "Alta").sum() == 0


def test_valor_texto_genera_alerta_calidad_del_dato(base, tmp_path):
    # Un valor no numérico ("alto") posterior al corte: inválido y alerta "Calidad del dato" con valor vacío
    d = _con(base, [{"fecha": "2022-10-21 09:00", "punto": "ET-0F", "parametro": "Sulfato",
                     "valor": "alto", "unidad": "mg/L"}])
    assert not d.iloc[-1]["valido"]
    a = R.generar_alertas(d, "2022-09-30")
    assert list(a["tipo"]) == ["Calidad del dato"]
    assert pd.isna(a["valor"].iat[0])
    # se guarda y se vuelve a leer sin errores
    import almacen as A
    con = A.conectar(str(tmp_path / "prueba.db"))
    assert A.sincronizar_alertas(con, a) == 1
    leidas = A.leer_alertas(con)
    assert pd.isna(pd.to_numeric(leidas["valor"], errors="coerce").iat[0])
    con.close()


# ---------------------------------------------------------------------------
# Mejoras v2: reinicio concurrente, campaña, carga múltiple y corrección de datos
# ---------------------------------------------------------------------------
def test_reinicio_concurrente_deja_1869(tmp_path):
    import threading
    import almacen as A
    con = A.conectar(str(tmp_path / "concurrente.db"))
    A.registrar(con, "QA", "Administrador", "prueba", "antes del reinicio")
    barrera = threading.Barrier(5)
    errores = []

    def reiniciar():
        try:
            barrera.wait()
            A.reiniciar(con)
        except Exception as e:  # noqa: BLE001
            errores.append(repr(e))

    hilos = [threading.Thread(target=reiniciar) for _ in range(5)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert errores == []
    assert con.execute("SELECT COUNT(*) FROM registros").fetchone()[0] == 1869
    assert con.execute("SELECT COUNT(*) FROM bitacora").fetchone()[0] == 0
    con.close()


def _filas_campana(pares):
    return pd.DataFrame(pares, columns=["Parámetro", "Valor"])


def test_catalogo_de_campana(base):
    cat = R.catalogo_parametros(base)
    assert len(cat) == 49
    assert list(cat["etiqueta"][:4]) == ["pH", "Conductividad", "Sulfato", "Temperatura"]
    fila = cat.set_index("etiqueta")
    assert fila.loc["pH", "grupo"] == "Parámetros de terreno" and fila.loc["pH", "unidad"] == "unidad pH"
    assert fila.loc["Temperatura", "grupo"] == "Parámetros de terreno"
    assert fila.loc["Sulfato", "unidad"] == "mg/L"


def test_campana_tres_parametros_mismo_punto_y_fecha(base):
    cat = R.catalogo_parametros(base)
    filas = _filas_campana([("pH", "7,5"), ("Conductividad", "2500"), ("Temperatura", "18,2")])
    nuevos = R.registros_campana("Pozo 8/A", "2022-10-20 10:30", filas, True, cat)
    assert len(nuevos) == 3
    assert nuevos["punto"].nunique() == 1 and nuevos["fecha"].nunique() == 1
    assert list(nuevos["valor"]) == [7.5, 2500.0, 18.2]                       # coma decimal aceptada
    assert set(nuevos["simulado"]) == {1}
    d = _con(base, nuevos.to_dict("records"))
    ult = d.tail(3)
    assert ult["valido"].all()
    assert list(ult["clave"][:2]) == ["pH", "Conductividad"]                 # alimentan K9 y K10


def test_campana_ignora_filas_vacias(base):
    cat = R.catalogo_parametros(base)
    filas = _filas_campana([("pH", "7.1"), ("Conductividad", ""), ("Temperatura", None), (None, "5")])
    nuevos = R.registros_campana("ET-0F", "2022-10-20 10:00", filas, False, cat)
    assert len(nuevos) == 1 and nuevos["parametro"].iat[0] == "Potencial de Hidrógeno"


def test_carga_combinada_de_dos_archivos(base):
    a1 = b"fecha,punto,parametro,valor,unidad\n2022-10-20 09:00,ET-0F,Sulfato,120,mg/L\n"
    a2 = (b"fecha;punto;parametro;valor;unidad\n2022-10-21 09:00;Pozo 8/A;pH;7,4;unidad pH\n"
          b"2022-10-21 09:00;Pozo 8/A;Sulfato;1500;mg/L\n")
    roto = b"fecha,punto,parametro,unidad\n2022-10-20,ET-0F,Sulfato,mg/L\n"
    resumen, nuevos, chequeo = R.preparar_carga([("a1.csv", a1), ("a2.csv", a2), ("roto.csv", roto)],
                                                base[R.COLUMNAS])
    assert list(resumen["registros leídos"]) == [1, 2, 0]
    assert resumen.set_index("archivo").loc["roto.csv", "error"].startswith("Faltan columnas")
    assert set(nuevos) == {"a1.csv", "a2.csv"}                               # el archivo con error no bloquea
    assert len(chequeo) == 3 and chequeo["valido"].all()


def test_corregir_id_existente_deja_trazabilidad(tmp_path):
    import almacen as A
    con = A.conectar(str(tmp_path / "corr.db"))
    anterior = con.execute("SELECT valor FROM registros WHERE id=1").fetchone()[0]
    A.actualizar_valor(con, 1, 9.9, "Ana", "Operador", "error de digitación")
    assert con.execute("SELECT valor FROM registros WHERE id=1").fetchone()[0] == 9.9
    usuario, rol, evento, detalle = con.execute(
        "SELECT usuario, rol, evento, detalle FROM bitacora ORDER BY id DESC LIMIT 1").fetchone()
    assert (usuario, rol, evento) == ("Ana", "Operador", "Corrección de dato")
    assert f"{anterior} -> 9.9" in detalle and "Motivo: error de digitación" in detalle
    con.close()


def test_corregir_id_inexistente_no_corrige(tmp_path):
    import almacen as A
    con = A.conectar(str(tmp_path / "corr2.db"))
    with pytest.raises(ValueError, match="no existe"):
        A.actualizar_valor(con, 999999, 1.0, "Ana", "Operador", "prueba")
    assert con.execute("SELECT COUNT(*) FROM bitacora").fetchone()[0] == 0
    con.close()


# ---------------------------------------------------------------------------
# Correcciones con datos de prueba: punto vacío, punto desconocido, miles y carga robusta
# ---------------------------------------------------------------------------
def test_punto_vacio_queda_invalido_sin_excepcion(base):
    crudo = pd.DataFrame({"fecha": ["2023-04-05 10:00"], "punto": [float("nan")], "parametro": ["Sulfato"],
                          "valor": [90], "unidad": ["mg/L"]})
    d = _con(base, R.normalizar(crudo).to_dict("records"))
    assert not d.iloc[-1]["valido"] and "Punto vacío" in d.iloc[-1]["motivo"]


def test_punto_desconocido_queda_invalido(base):
    d = _con(base, [{"fecha": "2023-04-05 10:00", "punto": "Pozo 9", "parametro": "Sulfato", "valor": 90,
                     "unidad": "mg/L"}])
    assert not d.iloc[-1]["valido"] and "Punto desconocido (Pozo 9)" in d.iloc[-1]["motivo"]


@pytest.mark.parametrize("texto, esperado", [
    ("2.950", 2950.0), ("1.400,5", 1400.5), ("12.345.678", 12345678.0), ("7.4", 7.4), ("0.02", 0.02),
    ("0,02", 0.02), ("7,4", 7.4), ("2900.0", 2900.0), ("0.020", 0.02), ("-1.234", -1234.0)])
def test_conversion_de_numeros_con_miles_y_coma(texto, esperado):
    assert R.a_numero(texto) == pytest.approx(esperado)
    crudo = pd.DataFrame({"fecha": ["2023-05-10"], "punto": ["Laguna AO-3"], "parametro": ["pH"],
                          "valor": [texto], "unidad": ["unidad pH"]})
    assert R.normalizar(crudo)["valor"].iat[0] == pytest.approx(esperado)


def test_carga_de_tres_archivos_con_punto_vacio(base):
    bueno1 = b"fecha,punto,parametro,valor,unidad\n2023-01-11 10:00,Laguna AO-3,pH,7.7,unidad pH\n"
    bueno2 = (b"fecha;punto;parametro;valor;unidad\n10/05/2023;Laguna AO-3;Conductividad;2.950;uS/cm\n"
              b"10/05/2023;Laguna AO-3;Sulfato;1400,5;mg/L\n")
    vacio = b"fecha,punto,parametro,valor,unidad,calificador,fuente,simulado\n2023-04-05 10:00,,Sulfato,90,mg/L,Igual,x,1\n"
    resumen, nuevos, chequeo = R.preparar_carga(
        [("bueno1.csv", bueno1), ("bueno2.csv", bueno2), ("vacio.csv", vacio)], base[R.COLUMNAS])
    r = resumen.set_index("archivo")
    assert (r["error"] == "").all()                                        # ninguna excepción
    assert r.loc["bueno1.csv", "inválidos"] == 0 and r.loc["bueno2.csv", "inválidos"] == 0
    assert r.loc["vacio.csv", "inválidos"] == 1 and r.loc["vacio.csv", "observaciones"] == "Punto vacío"
    assert list(nuevos["bueno2.csv"]["valor"]) == [2950.0, 1400.5]          # miles y coma decimal


def test_carga_con_archivo_que_lanza_excepcion_no_bloquea(base, monkeypatch):
    original = R.normalizar

    def falla(df, *a, **k):
        if "raro" in str(k.get("fuente_por_defecto", "")):
            raise KeyError("columna rara")
        return original(df, *a, **k)

    monkeypatch.setattr(R, "normalizar", falla)
    ok = b"fecha,punto,parametro,valor,unidad\n2023-01-11 10:00,ET-0F,Sulfato,120,mg/L\n"
    resumen, nuevos, _ = R.preparar_carga([("ok.csv", ok), ("raro.csv", ok)], base[R.COLUMNAS])
    r = resumen.set_index("archivo")
    assert set(nuevos) == {"ok.csv"}
    assert r.loc["raro.csv", "error"].startswith("No se pudo procesar el archivo (KeyError)")


DATOS_PRUEBA = os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop", "pruyeba")


@pytest.mark.skipif(not os.path.isdir(DATOS_PRUEBA), reason="carpeta de datos de prueba no disponible")
def test_carga_real_de_los_tres_archivos_de_prueba(base):
    nombres = ["2_anomalo_ene-mar_2023.csv", "5_excel_junio_2023.xlsx", "6_punto_vacio.csv"]
    archivos = [(n, open(os.path.join(DATOS_PRUEBA, n), "rb").read()) for n in nombres]
    resumen, nuevos, _ = R.preparar_carga(archivos, base[R.COLUMNAS])
    r = resumen.set_index("archivo")
    assert (r["error"] == "").all()
    assert (r.loc[nombres[0], "registros leídos"], r.loc[nombres[0], "inválidos"]) == (36, 0)
    assert (r.loc[nombres[1], "registros leídos"], r.loc[nombres[1], "inválidos"]) == (12, 0)
    assert r.loc[nombres[2], "observaciones"] == "Punto vacío"
