"""
analizar_stock.py
Carlos Isla y Cía. — NET-LogistK ISLA
Pipeline de análisis de movimientos de stock → Firebase

Conceptos clave:
  - ENTREGAS:     Remitos (clientes en mostrador + reparto). NO es "ventas".
  - RECEPCIONES:  Recep.Cpra (ingreso de mercadería de proveedores). NO es "compras".
  - RMC:          Devoluciones de clientes (Remito de Mercadería a Clientes).
  - AJUSTES:      Movimientos extraordinarios (DI, R, AC, CO, etc.).
  - INTERDEP.:    Transferencias reales entre depósitos (sin pares virtuales de reservas).

Valores:
  - Siempre se usa Imp. Costo (neto). Remitos tienen signo negativo (egreso de stock).
  - Kgs y Lineas se incluyen en todas las métricas. No se usan precios de venta.

Estructura Firestore (lectura eficiente para comparación mes a mes):
  indicadores/operaciones/movimientos/{YYYY-MM}   ← documento raíz LIVIANO (~5KB)
    ├── metricas_base                              ← totales globales con pesos + kgs + lineas
    ├── entregas_por_deposito                      ← array por depósito
    ├── recepciones_por_deposito                   ← array por depósito
    └── rmc_por_deposito                           ← array por depósito
  indicadores/operaciones/movimientos/{YYYY-MM}/detalles/
    ├── ajustes_reales                             ← DI, R, AC, etc. por depósito+artículo
    ├── conversiones                               ← CO con flag de alerta si no cierran en 0
    ├── interdepositos                             ← movimientos reales (sin reservas virtuales)
    ├── rmcs                                       ← detalle por tipo y artículo
    ├── evolucion_diaria                           ← entregas diarias del período
    └── dimensiones                                ← top rubros + top vendedores (solo entregas)
"""

import pandas as pd
import numpy as np
import firebase_admin
from firebase_admin import credentials, firestore
import config
import logging

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# FIREBASE
# ─────────────────────────────────────────────────────────────────────────────

def inicializar_firebase():
    """Inicializa la conexión con Firestore (singleton)."""
    if not firebase_admin._apps:
        cred = credentials.Certificate(config.FIREBASE_CREDENTIALS_PATH)
        firebase_admin.initialize_app(cred)
    return firestore.client()


def subir_a_firebase(resultado, db):
    """
    Arquitectura liviana:
      - Documento raíz: solo métricas agregadas (~5KB). Se usa para dashboards
        comparativos leyendo N períodos sin cargar el detalle.
      - Subcolección 'detalles': arrays pesados, se leen solo en drilldown.
    """
    periodo = resultado['periodo']
    root_ref = (
        db.collection('indicadores')
          .document('operaciones')
          .collection('movimientos')
          .document(periodo)
    )

    # 1. Raíz liviana (merge=True para no pisar otros campos si existen)
    root_ref.set(resultado['resumen_liviano'], merge=True)

    # 2. Subcolección de detalles — una escritura por sección
    subcol = root_ref.collection('detalles')
    detalles = resultado['detalles']

    subcol.document('ajustes_reales').set({"datos": detalles['ajustes_reales']})
    subcol.document('conversiones').set({"datos": detalles['conversiones']})
    subcol.document('interdepositos').set({"datos": detalles['interdepositos']})
    subcol.document('rmcs').set({"datos": detalles['rmcs']})
    subcol.document('evolucion_diaria').set({"datos": detalles['evolucion_diaria']})
    subcol.document('dimensiones').set({
        "rubros":     detalles['dimensiones']['rubros'],
        "vendedores": detalles['dimensiones']['vendedores'],
    })

    log.info(
        f"✓ Período {periodo} guardado en Firestore "
        f"(raíz liviana + {len(detalles)} sub-docs)."
    )


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _nombre_deposito(cod):
    """Resuelve el nombre legible del depósito a partir del código numérico."""
    try:
        return config.DEPOSITOS.get(int(cod), f"Dep.{cod}")
    except (ValueError, TypeError):
        return f"Dep.{cod}"


def _safe_col(df, col, default="SIN DATO"):
    """Devuelve la columna si existe, o una Serie de defaults."""
    return df[col] if col in df.columns else pd.Series(default, index=df.index)


# ─────────────────────────────────────────────────────────────────────────────
# LIMPIEZA
# ─────────────────────────────────────────────────────────────────────────────

def limpiar_y_filtrar_datos(df):
    """
    Regla de negocio: de las chapas T-101 y CAN, solo se auditan las de 13.00m.
    Garantiza tipos numéricos y datetime correctos en todas las columnas clave.

    Nexion genera dos formatos de export según la vista utilizada:
      • STK Raw (pipeline diario): columnas camelCase → normalizadas por el pipeline
      • Histórico mensual: columnas ya legibles pero con nombres levemente distintos
    Los alias se normalizan aquí antes de cualquier otro procesamiento.
    """
    # ── Normalización de alias entre formatos de export ──────────────────────
    # Nexion genera dos vistas con nombres de columna distintos para los mismos datos:
    #
    #   STK Raw (export automático / pipeline diario)
    #     FecRPTO      → MAPEO pipeline → 'Fec. Reparto'
    #     NomVDR       → MAPEO pipeline → 'Vendedor'  (nombre legible)
    #
    #   Histórico mensual (export manual desde Nexion)
    #     'Fec RPTO'       (espacio, sin punto) → alias de 'Fec. Reparto'
    #     'Nom DEP'        (espacio)             → alias de 'NomDEP'
    #     'Vendedor'       contiene el CÓDIGO numérico del vendedor
    #     'Nombre vendedor' contiene el NOMBRE legible → se usa este, se descarta el código
    #     'Kgs.' y 'Kgs..1' son columnas espurias siempre en 0 (artefacto del JOIN interno)
    _ALIAS = {
        'Fec RPTO': 'Fec. Reparto',
        'Nom DEP':  'NomDEP',
        'Kgs.':     '_kgs_aux_1',
        'Kgs..1':   '_kgs_aux_2',
    }
    df = df.rename(columns={k: v for k, v in _ALIAS.items() if k in df.columns})

    # Histórico: reemplazar el código numérico de 'Vendedor' por el nombre legible
    if 'Nombre vendedor' in df.columns:
        df = df.drop(columns=['Vendedor'], errors='ignore')
        df = df.rename(columns={'Nombre vendedor': 'Vendedor'})

    # Nota: el filtro de chapas 13m (solo auditar las de 13.00m) se aplica
    # ÚNICAMENTE en el análisis de quiebres de stock, que no forma parte de
    # este pipeline. Aquí se procesan todos los artículos sin excepción.

    # Numéricos
    for col in ['Cant', 'CantIngreso', 'CantEgreso', 'Kgs', 'Imp. Costo']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    # Fecha
    if 'Fec. Comp.' in df.columns:
        df['Fec. Comp.'] = pd.to_datetime(df['Fec. Comp.'], errors='coerce')

    # Nulos en dimensiones de texto
    for col in ['Vendedor', 'Nombre rubro', 'Nombre grupo', 'Nombre familia',
                'NomDEP', 'UMD', 'Número']:
        if col in df.columns:
            df[col] = df[col].fillna('SIN DATO')

    # Flag tipo_entrega (solo válido para Remitos):
    #   DEPOSITO  → FecRPTO vacío (cliente retira en mostrador)
    #   REPARTO   → FecRPTO con fecha (entrega domiciliaria/obra)
    #   DESCONOCIDO → campo ausente en el export (datos históricos sin FecRPTO)
    #   N/A       → comprobante que no es Remito
    if 'Fec. Reparto' in df.columns:
        df['Fec. Reparto'] = pd.to_datetime(df['Fec. Reparto'], errors='coerce')
        es_remito = df['Comp.'] == 'Remito'
        df['tipo_entrega'] = 'N/A'
        df.loc[es_remito & df['Fec. Reparto'].isna(),  'tipo_entrega'] = 'DEPOSITO'
        df.loc[es_remito & df['Fec. Reparto'].notna(), 'tipo_entrega'] = 'REPARTO'
    else:
        df['tipo_entrega'] = df['Comp.'].apply(
            lambda c: 'DESCONOCIDO' if c == 'Remito' else 'N/A'
        )

    return df


# ─────────────────────────────────────────────────────────────────────────────
# ANÁLISIS ESPECÍFICOS
# ─────────────────────────────────────────────────────────────────────────────

def analizar_interdepositos(df):
    """
    Movimientos reales entre depósitos.
    Elimina los pares virtuales depósito-operativo ↔ depósito-reserva
    usando el motor de emparejamiento por Fecha + Artículo + Cant.
    """
    df_inter = df[df['Comp.'].isin(['REM-INTER', 'RCP-INTER'])].copy()
    if df_inter.empty:
        return []

    df_inter['idx_original'] = df_inter.index
    df_rem = df_inter[df_inter['Comp.'] == 'REM-INTER']
    df_rcp = df_inter[df_inter['Comp.'] == 'RCP-INTER']

    cruce = pd.merge(
        df_rem, df_rcp,
        on=['Fec. Comp.', 'Artículo', 'Cant'],
        suffixes=('_rem', '_rcp')
    )

    idx_borrar = set()
    for _, row in cruce.iterrows():
        dep_rem = str(row['Depósito_rem']).zfill(4)
        dep_rcp = str(row['Depósito_rcp']).zfill(4)
        if config.MAPEO_RESERVAS.get(dep_rem) == dep_rcp:
            if row['idx_original_rem'] not in idx_borrar and row['idx_original_rcp'] not in idx_borrar:
                idx_borrar.update([row['idx_original_rem'], row['idx_original_rcp']])

    df_real = df_inter[~df_inter['idx_original'].isin(idx_borrar)].copy()

    resumen = (
        df_real
        .groupby(['Depósito', 'Comp.', 'Artículo', 'Descripción'])
        .agg(kgs=('Kgs', 'sum'), cant=('Cant', 'sum'), pesos=('Imp. Costo', 'sum'))
        .reset_index()
    )
    resumen['nombre_deposito'] = resumen['Depósito'].apply(_nombre_deposito)
    return resumen.to_dict(orient='records')


def analizar_ajustes(df):
    """
    Conversiones (CO): deben cerrar en 0 (ingreso = egreso). Se alerta si no.
    Ajustes reales (DI, R, AC, RE, etc.): movimientos extraordinarios auditables.
    Retorna dict con 'conversiones' y 'ajustes_reales'.
    """
    df_adj = df[df['Comp.'].isin(config.TIPOS_AJUSTE)].copy()
    if df_adj.empty:
        return {"conversiones": [], "ajustes_reales": []}

    mask_co = df_adj['Comp.'].isin(['Ing.Stk.CO', 'Egr.Stk.CO'])

    # Conversiones
    resumen_co = (
        df_adj[mask_co]
        .groupby(['Depósito', 'Artículo', 'Descripción'])
        .agg(
            cant_ingreso=('CantIngreso', 'sum'),
            cant_egreso=('CantEgreso', 'sum'),
            pesos=('Imp. Costo', 'sum'),
            kgs=('Kgs', 'sum')
        )
        .reset_index()
    )
    resumen_co['diferencia_neta'] = resumen_co['cant_ingreso'] - resumen_co['cant_egreso']
    resumen_co['alerta'] = np.abs(resumen_co['diferencia_neta']) > 0.01
    resumen_co['nombre_deposito'] = resumen_co['Depósito'].apply(_nombre_deposito)

    # Ajustes reales
    resumen_adj = (
        df_adj[~mask_co]
        .groupby(['Depósito', 'Comp.', 'Artículo', 'Descripción'])
        .agg(
            cant_ingreso=('CantIngreso', 'sum'),
            cant_egreso=('CantEgreso', 'sum'),
            pesos=('Imp. Costo', 'sum'),
            kgs=('Kgs', 'sum')
        )
        .reset_index()
    )
    resumen_adj['nombre_deposito'] = resumen_adj['Depósito'].apply(_nombre_deposito)

    return {
        "conversiones":   resumen_co.to_dict(orient='records'),
        "ajustes_reales": resumen_adj.to_dict(orient='records'),
    }


def analizar_rmc(df):
    """
    Devoluciones de clientes (RMC), agrupadas por depósito, tipo y artículo.
    Incluye descripción legible del tipo de devolución.
    """
    df_rmc = df[df['Comp.'].isin(config.TIPOS_RMC)].copy()
    if df_rmc.empty:
        return []

    resumen = (
        df_rmc
        .groupby(['Depósito', 'Comp.', 'Artículo', 'Descripción'])
        .agg(kgs=('Kgs', 'sum'), cant=('Cant', 'sum'), pesos=('Imp. Costo', 'sum'))
        .reset_index()
    )
    resumen['descripcion_tipo'] = (
        resumen['Comp.'].map(config.DESCRIPCIONES_RMC).fillna(resumen['Comp.'])
    )
    resumen['nombre_deposito'] = resumen['Depósito'].apply(_nombre_deposito)
    return resumen.to_dict(orient='records')


# ─────────────────────────────────────────────────────────────────────────────
# ORQUESTADOR
# ─────────────────────────────────────────────────────────────────────────────

def analizar_periodo(ruta_excel, periodo):
    """
    Procesa el Excel de movimientos del período y retorna el dict listo
    para subir_a_firebase().

    Estructura de salida:
      {
        "periodo": "2026-05",
        "resumen_liviano": { ... },   ← va al documento raíz de Firestore
        "detalles": { ... }           ← va a la subcolección 'detalles'
      }
    """
    df_raw = pd.read_excel(ruta_excel, sheet_name=0)
    df = limpiar_y_filtrar_datos(df_raw)

    # ── ENTREGAS: Remitos, diferenciando DEPÓSITO vs REPARTO ────────────────
    # DEPOSITO  → cliente retira en mostrador (Fec. Reparto vacía)
    # REPARTO   → entrega domiciliaria / en obra (Fec. Reparto con valor)
    # DESCONOCIDO → export histórico sin columna FecRPTO
    #
    # Exclusiones aplicadas:
    #   • Solo depósitos operativos (config.DEPOSITOS). Los depósitos de reserva
    #     (24, 25, 26, 27, 28) son virtuales — no generan remitos propios.
    #     Si aparecen es un dato anómalo que no debe entrar en el conteo.
    #   • El filtro de chapas 13m (limpiar_y_filtrar_datos) puede eliminar líneas
    #     de un remito que igual se cuenta en 'remitos'. El kgs/pesos de ese remito
    #     queda subreportado respecto al documento físico. Es una limitación
    #     deliberada de la regla de auditoría: las chapas de otras medidas no se
    #     auditan y no deben inflar el análisis.
    deps_operativos = set(config.DEPOSITOS.keys())
    df_ent = df[(df['Comp.'] == 'Remito') & (df['Depósito'].isin(deps_operativos))].copy()
    df_dep = df_ent[df_ent['tipo_entrega'] == 'DEPOSITO']
    df_rep = df_ent[df_ent['tipo_entrega'] == 'REPARTO']

    def _agg_entregas(frame):
        """Retorna dict con pesos/kgs/lineas/remitos para un subconjunto."""
        if frame.empty:
            return {"pesos": 0.0, "kgs": 0.0, "lineas": 0, "remitos": 0}
        rem = frame['Número'].nunique() if 'Número' in frame.columns else len(frame)
        return {
            "pesos":   float(frame['Imp. Costo'].sum()),
            "kgs":     float(frame['Kgs'].sum()),
            "lineas":  int(len(frame)),
            "remitos": int(rem),
        }

    def _agg_entregas_por_dep(frame):
        """GroupBy Depósito con métricas de entrega."""
        if frame.empty:
            return pd.DataFrame(columns=['Depósito', 'pesos', 'kgs', 'lineas', 'remitos'])
        g = frame.groupby('Depósito')
        agg = g.agg(pesos=('Imp. Costo', 'sum'), kgs=('Kgs', 'sum'), lineas=('Comp.', 'count')).reset_index()
        if 'Número' in frame.columns:
            agg = agg.join(g['Número'].nunique().rename('remitos'), on='Depósito')
        else:
            agg['remitos'] = agg['lineas']
        return agg

    # Totales por tipo de entrega
    tot_dep = _agg_entregas(df_dep)
    tot_rep = _agg_entregas(df_rep)

    # Por depósito: una fila por depósito con sub-dicts deposito/reparto
    agg_dep = _agg_entregas_por_dep(df_dep).set_index('Depósito')
    agg_rep = _agg_entregas_por_dep(df_rep).set_index('Depósito')
    todos_deps = sorted(set(agg_dep.index) | set(agg_rep.index))

    entregas_por_dep_lista = []
    for cod in todos_deps:
        def _row(agg, cod):
            if cod in agg.index:
                r = agg.loc[cod]
                return {"pesos": float(r['pesos']), "kgs": float(r['kgs']),
                        "lineas": int(r['lineas']), "remitos": int(r['remitos'])}
            return {"pesos": 0.0, "kgs": 0.0, "lineas": 0, "remitos": 0}

        d_row = _row(agg_dep, cod)
        r_row = _row(agg_rep, cod)
        entregas_por_dep_lista.append({
            "deposito":  int(cod),
            "nombre":    _nombre_deposito(cod),
            "total_pesos":  round(d_row['pesos'] + r_row['pesos'], 2),
            "total_kgs":    round(d_row['kgs']   + r_row['kgs'],   4),
            "total_lineas": d_row['lineas'] + r_row['lineas'],
            "total_remitos": d_row['remitos'] + r_row['remitos'],
            "mostrador": d_row,
            "reparto":   r_row,
        })

    # ── RECEPCIONES: Recep.Cpra (ingreso de stock de proveedores) ────────────
    # Nota: es RECEPCIÓN de mercadería, no "compra" (la compra la gestiona
    # Administración/Compras; Operaciones solo registra la recepción física).
    df_rec = df[df['Comp.'] == 'Recep.Cpra'].copy()

    g_rec = df_rec.groupby('Depósito')
    recepciones_dep = g_rec.agg(
        pesos=('Imp. Costo', 'sum'),
        kgs=('Kgs',          'sum'),
        lineas=('Comp.',     'count')
    ).reset_index()
    if 'Número' in df_rec.columns:
        recepciones_dep = recepciones_dep.join(
            g_rec['Número'].nunique().rename('ordenes'), on='Depósito'
        )
    else:
        recepciones_dep['ordenes'] = recepciones_dep['lineas']
    recepciones_dep['nombre'] = recepciones_dep['Depósito'].apply(_nombre_deposito)

    # ── RMC por depósito (resumen para el documento raíz) ───────────────────
    df_rmc_rows = df[df['Comp.'].isin(config.TIPOS_RMC)]
    if not df_rmc_rows.empty:
        rmc_dep = (
            df_rmc_rows.groupby('Depósito')
            .agg(pesos=('Imp. Costo', 'sum'), kgs=('Kgs', 'sum'), lineas=('Comp.', 'count'))
            .reset_index()
        )
        rmc_dep['nombre'] = rmc_dep['Depósito'].apply(_nombre_deposito)
        rmc_por_dep = rmc_dep.to_dict('records')
    else:
        rmc_por_dep = []

    # ── TOTALES GLOBALES ─────────────────────────────────────────────────────
    tot_ent_pesos  = float(df_ent['Imp. Costo'].sum())
    tot_ent_kgs    = float(df_ent['Kgs'].sum())
    tot_ent_lineas = int(len(df_ent))
    tot_ent_remitos = int(df_ent['Número'].nunique()) if 'Número' in df_ent.columns else tot_ent_lineas

    tot_rec_pesos  = float(df_rec['Imp. Costo'].sum())
    tot_rec_kgs    = float(df_rec['Kgs'].sum())
    tot_rec_lineas = int(len(df_rec))

    tot_rmc_pesos  = float(df_rmc_rows['Imp. Costo'].sum())
    tot_rmc_kgs    = float(df_rmc_rows['Kgs'].sum())
    tot_rmc_lineas = int(len(df_rmc_rows))

    df_adj_rows = df[df['Comp.'].isin(config.TIPOS_AJUSTE)]
    tot_adj_egr = float(
        df_adj_rows[df_adj_rows['Comp.'].isin(config.TIPOS_AJUSTE_EGRESO)]['Imp. Costo'].sum()
    )
    tot_adj_ing = float(
        df_adj_rows[df_adj_rows['Comp.'].isin(config.TIPOS_AJUSTE_INGRESO)]['Imp. Costo'].sum()
    )

    # ── RUBROS: top por kgs entregados (solo Remitos) ────────────────────────
    top_rubros = []
    if 'Nombre rubro' in df_ent.columns:
        top_rubros = (
            df_ent.groupby('Nombre rubro')
            .agg(pesos=('Imp. Costo', 'sum'), kgs=('Kgs', 'sum'), lineas=('Comp.', 'count'))
            .reset_index()
            .sort_values('kgs')           # más negativo = más entregado en kgs
            .head(50)
            .to_dict('records')
        )

    # ── VENDEDORES: top por kgs (solo Remitos) ───────────────────────────────
    top_vendedores = []
    if 'Vendedor' in df_ent.columns:
        top_vendedores = (
            df_ent.groupby('Vendedor')
            .agg(pesos=('Imp. Costo', 'sum'), kgs=('Kgs', 'sum'), lineas=('Comp.', 'count'))
            .reset_index()
            .sort_values('kgs')
            .head(50)
            .to_dict('records')
        )

    # ── EVOLUCIÓN DIARIA (solo entregas / Remitos) ───────────────────────────
    evolucion = []
    if 'Fec. Comp.' in df_ent.columns:
        df_ent_d = df_ent.copy()
        df_ent_d['dia'] = df_ent_d['Fec. Comp.'].dt.strftime('%Y-%m-%d')
        evolucion = (
            df_ent_d.groupby('dia')
            .agg(pesos=('Imp. Costo', 'sum'), kgs=('Kgs', 'sum'), lineas=('Comp.', 'count'))
            .reset_index()
            .to_dict('records')
        )

    # ── ANÁLISIS DE AJUSTES Y RMC ────────────────────────────────────────────
    ajustes_data = analizar_ajustes(df)

    # ── ENSAMBLE FINAL ───────────────────────────────────────────────────────
    return {
        "periodo": periodo,

        # ── Documento raíz liviano (comparación mes a mes en el dashboard) ──
        "resumen_liviano": {
            "periodo": periodo,
            "metricas_base": {
                # Entregas: Imp. Costo negativo (egreso de stock). Kgs negativo.
                "entregas": {
                    "total_pesos":   tot_ent_pesos,
                    "total_kgs":     tot_ent_kgs,
                    "total_lineas":  tot_ent_lineas,
                    "total_remitos": tot_ent_remitos,
                    "mostrador": tot_dep,   # retiro en depósito (FecRPTO vacía)
                    "reparto":   tot_rep,   # entrega domiciliaria/obra (FecRPTO con valor)
                },
                # Recepciones: Imp. Costo positivo (ingreso de stock). Kgs positivo.
                "recepciones": {
                    "total_pesos":  tot_rec_pesos,
                    "total_kgs":    tot_rec_kgs,
                    "total_lineas": tot_rec_lineas,
                },
                # RMC: Imp. Costo positivo (retorno al stock).
                "rmc": {
                    "total_pesos":  tot_rmc_pesos,
                    "total_kgs":    tot_rmc_kgs,
                    "total_lineas": tot_rmc_lineas,
                },
                # Ajustes: separados entre egresos (DI, R, AC) e ingresos (RE, AC).
                "ajustes": {
                    "total_pesos_egreso":  tot_adj_egr,
                    "total_pesos_ingreso": tot_adj_ing,
                },
            },
            # Arrays por depósito para comparación granular sin drilldown
            "entregas_por_deposito":    entregas_por_dep_lista,
            "recepciones_por_deposito": recepciones_dep.to_dict('records'),
            "rmc_por_deposito":         rmc_por_dep,
        },

        # ── Subcolección detalles (se leen solo en drilldown) ────────────────
        "detalles": {
            "ajustes_reales":  ajustes_data['ajustes_reales'],
            "conversiones":    ajustes_data['conversiones'],
            "interdepositos":  analizar_interdepositos(df),
            "rmcs":            analizar_rmc(df),
            "evolucion_diaria": evolucion,
            "dimensiones": {
                "rubros":     top_rubros,
                "vendedores": top_vendedores,
            },
        },
    }
