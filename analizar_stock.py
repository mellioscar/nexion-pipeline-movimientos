import pandas as pd
import numpy as np
import firebase_admin
from firebase_admin import credentials, firestore
import config
import logging

log = logging.getLogger(__name__)

def inicializar_firebase():
    """Inicializa la conexión con Firestore."""
    if not firebase_admin._apps:
        cred = credentials.Certificate(config.FIREBASE_CREDENTIALS_PATH)
        firebase_admin.initialize_app(cred)
    return firestore.client()

def subir_a_firebase(resultado, db):
    """Sube el JSON consolidado a la colección unificada de indicadores."""
    periodo = resultado['periodo']
    
    # Nueva ruta: indicadores / operaciones / movimientos / 2026-05
    doc_ref = db.collection('indicadores').document('operaciones').collection('movimientos').document(periodo)
    doc_ref.set(resultado, merge=True)
    
    log.info(f"✓ Datos del período {periodo} guardados en Firestore (indicadores/operaciones).")

def limpiar_y_filtrar_datos(df):
    """Aplica la regla estricta para chapas de 13 metros y limpia la data base."""
    def es_articulo_auditable(desc):
        desc_upper = str(desc).upper()
        es_chapa = "CHAPA T 101" in desc_upper or "CHAPA CAN." in desc_upper
        if es_chapa:
            return "13.00" in desc_upper
        return True # El resto de los artículos pasan normalmente

    df_filtrado = df[df['Descripción'].apply(es_articulo_auditable)].copy()
    
    # Asegurar que los importes y cantidades sean numéricos
    cols_numericas = ['Cant', 'CantIngreso', 'CantEgreso', 'Kgs', 'Imp. Costo']
    for col in cols_numericas:
        if col in df_filtrado.columns:
            df_filtrado[col] = pd.to_numeric(df_filtrado[col], errors='coerce').fillna(0)
            
    return df_filtrado

def analizar_interdepositos(df):
    """
    Agrupa los movimientos de interdepósito.
    Implementa un motor de emparejamiento matemático para detectar y
    eliminar los movimientos virtuales de reservas sin depender de los textos.
    """
    # 1. Filtramos solo los comprobantes que nos interesan
    df_inter = df[df['Comp.'].isin(['REM-INTER', 'RCP-INTER'])].copy()
    
    # --- MOTOR DE ELIMINACIÓN VIRTUAL ---
    # Guardamos el índice original para saber exactamente qué filas borrar al final
    df_inter['idx_original'] = df_inter.index

    # Separamos egresos e ingresos
    df_rem = df_inter[df_inter['Comp.'] == 'REM-INTER']
    df_rcp = df_inter[df_inter['Comp.'] == 'RCP-INTER']

    # Cruzamos (Merge) buscando la coincidencia exacta: Fecha, Artículo y Cantidad
    cruce = pd.merge(
        df_rem, df_rcp,
        on=['Fec. Comp.', 'Artículo', 'Cant'],
        suffixes=('_rem', '_rcp')
    )

    idx_a_borrar = set()

    # Evaluamos cada coincidencia encontrada
    for _, row in cruce.iterrows():
        # Formateamos a 4 dígitos para que coincida perfecto con el config (ej: 7 -> '0007')
        dep_rem = str(row['Depósito_rem']).zfill(4)
        dep_rcp = str(row['Depósito_rcp']).zfill(4)

        # Preguntamos: ¿El destino de este remito es su propia reserva? (o viceversa)
        if config.MAPEO_RESERVAS.get(dep_rem) == dep_rcp:
            idx_rem = row['idx_original_rem']
            idx_rcp = row['idx_original_rcp']

            # Evitamos borrar dos veces en el caso raro de múltiples movimientos idénticos en el mismo día
            if idx_rem not in idx_a_borrar and idx_rcp not in idx_a_borrar:
                idx_a_borrar.add(idx_rem)
                idx_a_borrar.add(idx_rcp)

    # Filtramos el DataFrame original eliminando los pares virtuales detectados
    df_inter_limpio = df_inter[~df_inter['idx_original'].isin(idx_a_borrar)].copy()
    # ------------------------------------

    # Ahora sí, agrupamos solo los movimientos reales que quedaron vivos
    resumen = df_inter_limpio.groupby(['Depósito', 'Artículo', 'Descripción']).agg({
        'Kgs': 'sum',
        'Cant': 'sum',
        'Imp. Costo': 'sum'
    }).reset_index()
    
    return resumen.to_dict(orient='records')

def analizar_ajustes(df):
    """
    Agrupa todos los ajustes para que el front haga la comparación mes a mes.
    Valida internamente que las conversiones den neto 0.
    """
    # 1. Filtrar solo los comprobantes de ajuste
    df_ajustes = df[df['Comp.'].isin(config.TIPOS_AJUSTE)].copy()
    
    # 2. Separar las conversiones (.CO)
    es_conversion = df_ajustes['Comp.'].isin(['Ing.Stk.CO', 'Egr.Stk.CO'])
    df_conversiones = df_ajustes[es_conversion].copy()
    
    # Para validar conversiones sumamos CantIngreso - CantEgreso 
    # (o sumamos 'Cant' neta dependiendo de si los egresos ya vienen negativos)
    # Asumimos que agrupando por Depósito y Artículo, el Imp. Costo o Cant neto debería tender a 0
    resumen_conversiones = df_conversiones.groupby(['Depósito', 'Artículo', 'Descripción']).agg({
        'CantIngreso': 'sum',
        'CantEgreso': 'sum',
        'Imp. Costo': 'sum',
        'Kgs': 'sum'
    }).reset_index()
    
    # Agregamos un flag booleano si la diferencia entre ingresos y egresos no es cero
    resumen_conversiones['diferencia_neta'] = resumen_conversiones['CantIngreso'] - resumen_conversiones['CantEgreso']
    resumen_conversiones['alerta_conversion'] = np.abs(resumen_conversiones['diferencia_neta']) > 0.01

    # 3. Procesar el resto de los ajustes reales (DI, R, AC, etc.)
    df_ajustes_reales = df_ajustes[~es_conversion].copy()
    
    resumen_ajustes = df_ajustes_reales.groupby(['Depósito', 'Comp.', 'Artículo', 'Descripción']).agg({
        'CantIngreso': 'sum',
        'CantEgreso': 'sum',
        'Imp. Costo': 'sum',
        'Kgs': 'sum'
    }).reset_index()

    return {
        "conversiones": resumen_conversiones.to_dict(orient='records'),
        "detalle_ajustes": resumen_ajustes.to_dict(orient='records')
    }

def analizar_periodo(ruta_excel, periodo, incluir_interdeposito=True, incluir_alertas=True):
    """
    Orquestador llamado por pipeline_movimientos_gmail.py.
    """
    # El archivo temporal del pipeline solo tiene 'Movimientos' y un 'Stock_Inicial' vacío
    df_raw = pd.read_excel(ruta_excel, sheet_name=0)
    df_limpio = limpiar_y_filtrar_datos(df_raw)
    
    # Calculamos la métrica base para que el log del pipeline funcione sin romperse
    df_egresos = df_limpio[df_limpio['Comp.'].isin(config.TIPOS_EGRESO)]
    total_egresos_pesos = float(df_egresos['Imp. Costo'].sum())
    
    resultados = {
        "periodo": periodo,
        "metricas_base": {
            "egresos": {
                "total_egresos": {
                    "pesos": total_egresos_pesos
                }
            }
        },
        "ajustes_auditoria": analizar_ajustes(df_limpio)
    }
    
    if incluir_interdeposito:
        resultados["interdepositos"] = analizar_interdepositos(df_limpio)
        
    return resultados