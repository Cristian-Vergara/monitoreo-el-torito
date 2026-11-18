# Prototipo de monitoreo ambiental · Tranque de relaves El Torito

Ingeniería Ambiental · UTEM · Sección 411 · Grupo 1
Cristóbal Carreño, Fernando Neculqueo, Elliot Acevedo, Benjamin Aballay, Cristian Vergara
Profesora: Carolina Parodi

Aplicación web (Python + Streamlit) que hace el **seguimiento y control** de las variables ambientales
propuestas en el informe del proyecto: **pH, conductividad eléctrica y sulfato** en cuatro puntos de
monitoreo del tranque El Torito, y **calcula la matriz de impactos ambientales** con los datos de ingreso.

> Usa datos históricos reales (planillas de seguimiento ambiental reportadas a la SMA, marzo 2021 –
> septiembre 2022). No es un diagnóstico actual del tranque. Una alerta indica la necesidad de revisar,
> no un incumplimiento normativo.

---

## 1. Instalación y ejecución

Requisitos: Python 3.11 o superior (probado con Python 3.13; las versiones exactas están en `requirements.txt`).

```bash
# 1) instalar las librerías
pip install -r requirements.txt          # en Windows: py -m pip install -r requirements.txt

# 2) ejecutar la aplicación
streamlit run app.py                     # en Windows: py -m streamlit run app.py
```

Se abre en el navegador en `http://localhost:8501`. La primera vez crea la base `datos/monitoreo.db`
con los 1.869 registros reales de `datos/torito_4_puntos.csv`.

Casos de prueba automáticos:

```bash
pytest test_reglas.py -q          # 37 pruebas
```

## 2. Variables de ingreso

| Variable | Dónde se ingresa | Formato |
|---|---|---|
| Datos de calidad de agua | Pestaña **Datos de ingreso** → cargar archivos | **Uno o varios archivos a la vez** (CSV y Excel mezclados) con columnas `fecha, punto, parametro, valor, unidad` y opcionales `calificador, grupo, fuente, simulado` (descargue la plantilla). Se muestra un resumen por archivo (leídos, inválidos o el error) y una vista previa combinada; un archivo con error no impide cargar los demás |
| Una campaña de terreno | **Datos de ingreso** → ingreso de campaña | Punto, fecha y hora una sola vez, y una tabla Parámetro / Valor (los 49 parámetros de la base, con pH, Conductividad, Sulfato y Temperatura primero). La unidad y el grupo se asignan solos; se ignoran las filas sin valor y se acepta la coma decimal; casilla "simulado" activada por defecto |
| Corrección de un dato | **Datos de ingreso** → corregir un valor | Se elige el registro por su ID, en un desplegable filtrado por punto y parámetro, o seleccionando su fila en la tabla de registros. Se muestra su ficha (fecha, punto, parámetro, valor actual, unidad, calificador, fuente, simulado, válido y motivo); el nuevo valor viene precargado, se ve «actual → nuevo» y se exige un motivo (queda en la bitácora) |
| Línea base y margen | Barra lateral | Fecha de corte de la línea base y margen de tolerancia (%) |
| Equipos de la plataforma | Pestaña **Matriz ambiental** → variables de ingreso | N.º de notebooks, potencias (W), horas por día, días por mes, vCPU del servidor |
| Umbrales de magnitud | **Matriz ambiental** → umbrales | Cortes para calcular M (criterio del grupo, ajustables) |

Puntos aceptados: `Laguna AO-3`, `Dren AO-1`, `Pozo 8/A`, `ET-0F` (también `laguna`, `dren`, `pozo`, `estero`).
Un punto vacío deja el registro inválido («Punto vacío») y cualquier otro nombre lo deja inválido como
«Punto desconocido (…)», sin detener la carga.

**Números escritos como texto** (CSV con coma decimal, campaña): si el valor tiene coma, la coma es decimal y
los puntos son de miles (`1.400,5` → 1400,5; `0,02` → 0,02). Si no tiene coma y tiene la forma `2.950` o
`12.345.678` (1 a 3 dígitos y grupos de 3), los puntos son de miles (`2.950` → 2950). En otro caso el punto es
decimal (`7.4`, `0.02`, `2900.0`). **Ambigüedad:** `1.234` sin coma se lee como **1234** (miles), no como 1,234;
si quiere decir un decimal con tres cifras, escríbalo con coma (`1,234`). Esta regla se aplica solo a valores
leídos como texto: en un CSV que usa punto decimal en todos sus valores, pandas los lee directamente como números
(`2.950` → 2,95), y las celdas numéricas de Excel no se modifican.
Unidades esperadas: pH en `unidad pH`, conductividad en `uS/cm`, sulfato en `mg/L`.

## 3. Cálculos

1. **Validación (K1 = registros válidos / cargados × 100).** Es inválido un registro con un campo
   obligatorio vacío, una copia repetida exacta, una unidad atípica, un pH fuera de 0–14 o una
   concentración negativa. Con la base real: 16 inválidos de 1.869 → **K1 = 99,1 %**. Un valor anómalo
   (por ejemplo, 18,67 µS/cm en el Pozo 8/A, contra 2.499 µS/cm del laboratorio) no se descarta: se marca
   para revisión.
2. **Línea base.** Mínimo, mediana y máximo por punto y parámetro con los datos reales hasta la fecha de
   corte (reproduce la Tabla 24 del informe). Los valores «Menor a» no entran al rango.
3. **Alertas con umbral justificado.** Se evalúan las mediciones posteriores al corte:
   - 🟡 **Revisión**: valor fuera del rango histórico del punto, ampliado en el margen (%). *No es un incumplimiento.*
   - 🔴 **Posible superación normativa**: sulfato > 250 mg/L en ET-0F, único límite aplicable a los datos
     (RCA N.º 02/2020). Requiere verificar la vigencia del límite y el calificador del dato.
   - ⚪ **Calidad del dato**: registro inválido o anómalo.
4. **Ciclo de cada alerta**: Abierta → Revisada (responsable, confirmada o descartada) → Acción registrada →
   Cerrada (con verificación obligatoria). **K2** = horas promedio entre emisión y acción; **K3** = alertas
   cerradas con verificación / emitidas × 100.
5. **Matriz ambiental.** P = M + E + D (cada uno de 1 a 3); prioridad baja 3–4, media 5–6, alta 7–9.
   La magnitud M se **calcula con los datos de ingreso**:

   | Fila | Variable | Regla (umbrales ajustables) |
   |---|---|---|
   | T1 acidez | pH mínimo medido | M1 si ≥ 6,0; M2 si 4,5–6,0; M3 si < 4,5 |
   | T2 agua subterránea | sulfato mediano del Pozo 8/A ÷ 200 mg/L (agua natural, RCA) | M1 si ≤ 1,5; M2 si ≤ 3; M3 si > 3 |
   | T3 estero y T4 peces | sulfato máximo en ET-0F ÷ 250 mg/L (límite RCA) | M1 si ≤ 80 %; M2 si ≤ 100 %; M3 si > 100 % |
   | R3, R4 electricidad | kWh/año (Ecuación 1: Σ n·P·h / 1000) | M1 si ≤ 1.000; M2 si ≤ 10.000 |
   | R1, R2, R10 equipos | n.º de equipos | M1 si ≤ 10; M2 si ≤ 50 |
   | R5–R7 nube | vCPU contratadas | M1 si ≤ 4; M2 si ≤ 16 |

   El resto de las filas (T5–T12 de la RCA y R8, R9, R11–R13) usa los puntajes del informe y se puede
   ajustar. También se calcula:
   - **Criterio de significancia**: significativo si M ≥ 2 y P ≥ 7, o si la RCA lo declara significativo.
   - **Prueba de sensibilidad**: P' = 2M + E + D (baja 4–6, media 7–9, alta 10–12). Con P', las filas de
     la plataforma (todas de magnitud pequeña) dejan de ser prioridad alta.
6. **Indicadores**: K1–K13 calculados o propuestos y K14–K19 de seguimiento del titular (fuera de la
   plataforma), como en el informe.

## 4. Resultados

- **Dashboard**: evolución por punto de pH, conductividad y sulfato (los que generan alertas), con la línea
  del límite de 250 mg/L, la banda del rango histórico (al elegir un solo punto) y las mediciones con alerta
  marcadas. Debajo de «Otros parámetros (solo visualización)» se puede graficar cualquier otro parámetro con
  datos (por ejemplo, Cobre Total), con su unidad en el eje; los valores «Menor a» se muestran como ▽ en el
  valor del límite de detección. Esos parámetros no generan alertas ni tienen bandas o límites. Se actualiza
  al cargar o corregir datos.
- **Calidad de datos**: K1 por punto, registros inválidos, anómalos y meses sin mediciones.
- **Alertas**: tabla con colores, gestión del ciclo y bitácora.
- **Matriz ambiental** e **Indicadores**.

## 5. Informe de salida

Pestaña **Informe de salida**:
- **Excel** con hojas: Resumen, Matriz tranque, Matriz plataforma, Indicadores, Alertas, Línea base,
  Datos inválidos, Bitácora y Datos validados.
- **Informe HTML** con resumen, matrices, indicadores, alertas, línea base y gráfico de sulfato. Para
  obtener un PDF: abrirlo en el navegador → Imprimir → Guardar como PDF.

## 6. Demostración (pestaña Demo)

| Escenario | Qué hace | Resultado esperado |
|---|---|---|
| 1 · Normal | Agrega mediciones simuladas de la laguna dentro de su rango | Ninguna alerta |
| 2 · Anómalo (datos reales) | Línea base solo hasta el 28/02/2022; evalúa los datos posteriores | 30 alertas 🟡; la más clara: Pozo 8/A, sulfato y conductividad suben juntos desde abril de 2022 |
| 3 · Superación simulada | Agrega un sulfato **simulado** de 300 mg/L en ET-0F | 1 alerta 🔴 y T3/T4 pasan a M3 en la matriz |
| Limpiar | Borra los datos simulados | Vuelve a la base real |

Guion sugerido (5 minutos): carga y K1 → escenario 1 → escenario 2, dashboard del Pozo 8/A y cierre de una
alerta (K2 y K3 cambian) → escenario 3, alerta roja y matriz recalculada → descarga del informe de salida.

## 7. Roles

- **Operador**: carga archivos, ingresa campañas y corrige datos.
- **Encargado ambiental**: revisa, registra acciones y cierra alertas. No ve los formularios de carga, campaña
  ni corrección (en su lugar, un aviso).
- **Administrador**: todo lo anterior y **reiniciar la base**, que exige marcar la casilla «Confirmo que quiero
  borrar todo» (se desmarca sola después de cada reinicio).

Cada acción queda registrada en la bitácora (quién, cuándo y qué). Las escrituras en la base pasan por un
candado y el reinicio es una sola transacción, por lo que varios clics seguidos no dejan la base a medias.

## 7.1 Modo oscuro

Menú ⋮ (arriba a la derecha) → **System**, **Light** o **Dark**. Los colores de ambos temas están en
`.streamlit/config.toml` (`[theme.light]` y `[theme.dark]`). Las celdas coloreadas de la matriz y de las alertas
usan texto oscuro fijo para leerse en los dos modos. El navegador recuerda la elección.

## 8. Publicación en un sitio seguro (entrega final)

Opción recomendada: **Streamlit Community Cloud** (gratuito, con HTTPS).
1. Crear un repositorio en GitHub y subir esta carpeta completa (el `.gitignore` excluye la base local y la contraseña).
2. Entrar a <https://share.streamlit.io> con la cuenta de GitHub → **Create app** → elegir el repositorio,
   la rama `main` y el archivo `app.py`.
3. En **Advanced settings** elegir **Python 3.13** (la versión probada) y, en **Secrets**, pegar `APP_PASSWORD = "una-clave"` para exigir contraseña.
4. **Deploy**. La aplicación queda en una dirección `https://<nombre>.streamlit.app`.

Si el servidor se reinicia, la base se vuelve a crear automáticamente con los datos reales (las alertas
gestionadas en la demo se pierden; para conservarlas habría que usar una base externa).

## 9. Estructura

```
app.py              interfaz (pestañas)
reglas.py           validación, línea base, alertas, KPI, matriz, carga de archivos y campañas
                    (sin Streamlit, probado con pytest)
almacen.py          base SQLite: registros, alertas y bitácora (escrituras protegidas con un candado)
informe.py          informe de salida (Excel y HTML)
test_reglas.py      37 casos de prueba
datos/torito_4_puntos.csv   1.869 registros reales de los 4 puntos
requirements.txt    librerías
.streamlit/         temas claro y oscuro, y ejemplo de contraseña
```
