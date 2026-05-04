"""
config.py
Configuración del pipeline de análisis de movimientos de stock
Carlos Isla y Cía - NET-LogistK ISLA
Versión: 3.0 - Pipeline Gmail automatizado
"""

# ============================================================================
# DEPÓSITOS
# ============================================================================
DEPOSITOS = {
    5:  'Neuquén',
    7:  'San Juan',
    9:  'Planta de Hierro',
    11: 'Carpintería',
    14: 'JJ Gómez',
    15: 'Cipolletti',
    23: 'Cutral-Co',
}

# ============================================================================
# MAPEO PARA FILTRO DE RESERVAS VIRTUALES
# ============================================================================
MAPEO_RESERVAS = {
    '0025': '0005', '0005': '0025', # Neuquén <-> Reservas Neuquén
    '0027': '0007', '0007': '0027', # San Juan <-> Reservas San Juan
    '0024': '0014', '0014': '0024', # JJ Gomez <-> Reservas JJ Gomez
    '0026': '0015', '0015': '0026', # Cipolletti <-> Reservas Cipolletti
    '0028': '0023', '0023': '0028'  # Cutral Co <-> Reservas Cutral Co
}

# ============================================================================
# TIPOS DE COMPROBANTE
# ============================================================================

# Egresos de stock (ventas + movimientos internos)
TIPOS_EGRESO = [
    'Remito',        # Ventas a clientes
    'REM-INTER',     # Transferencia a otro depósito
    'Egr.Stk. R',    # Rotura
    'Egr.Stk.RR',    # Rotura de reparto
    'Egr.R. Pro',    # Rotura proveedor
    'Egr.Stk DI',    # Diferencia de inventario
    'Egr.Stk.AC',    # Atención comercial
    'Egr.Stk.CO',    # Conversión
    'Egr.Stk.VE',    # Vencimiento
    'Egr.Stk.MD',    # Migración DYN
]

# Ingresos de stock (compras + movimientos internos)
TIPOS_INGRESO = [
    'Recep.Cpra',    # Compras a proveedores
    'RCP-INTER',     # Recepción desde otro depósito
    'De.Mercad',     # Devolución de cliente
    'Ing.Stk.DI',    # Diferencia de inventario
    'Ing.Stk.RE',    # Recupero
    'Ing.Stk.AC',    # Atención comercial
    'Ing.Stk.CO',    # Conversión
    'Ing.Stk.DY',    # Migración DYN
]

# Ajustes de stock — comprobantes que se analizan en detalle
# (ingresos y egresos que no son ventas ni compras normales)
TIPOS_AJUSTE_INGRESO = [
    'Ing.Stk.DI',    # Diferencia de inventario
    'Ing.Stk.RE',    # Recupero
    'Ing.Stk.AC',    # Atención comercial
    'Ing.Stk.CO',    # Conversión (cambio de artículo/unidad)
    'Ing.Stk.DY',    # Migración DYN
    'De.Mercad',     # Devolución de mercadería
]

TIPOS_AJUSTE_EGRESO = [
    'Egr.Stk DI',    # Diferencia de inventario
    'Egr.Stk. R',    # Rotura
    'Egr.Stk.RR',    # Rotura de reparto
    'Egr.R. Pro',    # Rotura proveedor
    'Egr.Stk.AC',    # Atención comercial
    'Egr.Stk.CO',    # Conversión
    'Egr.Stk.VE',    # Vencimiento
    'Egr.Stk.MD',    # Migración DYN
]

TIPOS_AJUSTE = TIPOS_AJUSTE_INGRESO + TIPOS_AJUSTE_EGRESO

# RMC - Recepciones de Clientes (Devoluciones)
TIPOS_RMC_DEPOSITO = [
    'RMC-D-CdM',     # Cambio de Material
    'RMC-D-EDR',     # Error de Remisión
    'RMC-D-AT',      # Atención Comercial
    'RMC-D-EdE',     # Error de Entrega
]

TIPOS_RMC_REPARTO = [
    'RMC-R-NpR',     # Nadie Para Recibir
    'RMC-R-NNM',     # No Necesita el Material
    'RMC-R-RER',     # Rotura en Reparto
    'RMC-R-FdH',     # Fuera de Horario
    'RMC-R-DE',      # Dirección Errónea
    'RMC-R-EdE',     # Error de Entrega
    'RMC-R-RdO',     # Retiro de Obras
    'RMC-R-EdF',     # Error de Facturación
    'RMC-R-RdE',     # Reprogramación de Entrega
    'RMC-R-CC',      # Condición Climática
    'RMC-R-IE',      # Impedimento de Entrega
    'RMC-R-EdC',     # Error de Carga
    'RMC-R-EdR',     # Error Remisión de Reparto
    'RMC-FA',        # Factores Ajenos
    'RMC-MD',        # Material Dañado
    'RMC-FeC',       # Faltante en Carga
    'RMC-OTROS',     # Otros
]

TIPOS_RMC = TIPOS_RMC_DEPOSITO + TIPOS_RMC_REPARTO

DESCRIPCIONES_RMC = {
    'RMC-D-CdM': 'Cambio de Material',
    'RMC-D-EDR': 'Error de Remisión',
    'RMC-D-AT':  'Atención Comercial',
    'RMC-D-EdE': 'Error de Entrega (Depósito)',
    'RMC-R-NpR': 'Nadie Para Recibir',
    'RMC-R-NNM': 'No Necesita el Material',
    'RMC-R-RER': 'Rotura en Reparto',
    'RMC-R-FdH': 'Fuera de Horario',
    'RMC-R-DE':  'Dirección Errónea',
    'RMC-R-EdE': 'Error de Entrega (Reparto)',
    'RMC-R-RdO': 'Retiro de Obras',
    'RMC-R-EdF': 'Error de Facturación',
    'RMC-R-RdE': 'Reprogramación de Entrega',
    'RMC-R-CC':  'Condición Climática',
    'RMC-R-IE':  'Impedimento de Entrega',
    'RMC-R-EdC': 'Error de Carga',
    'RMC-R-EdR': 'Error Remisión de Reparto',
    'RMC-FA':    'Factores Ajenos',
    'RMC-MD':    'Material Dañado',
    'RMC-FeC':   'Faltante en Carga',
    'RMC-OTROS': 'Otros',
}

# ============================================================================
# EXCLUSIONES DE INTERDEPÓSITO
# ============================================================================
# Artículos que se excluyen del análisis de interdepósito porque son
# movimientos planificados y fijos (no representan ineficiencias).
#
# Configurar acá los rubros y artículos específicos a excluir.
# El pipeline filtra estos registros de REM-INTER / RCP-INTER
# ANTES de pasarlos a analizar_stock.py.
#
# RUBROS EXCLUIDOS:
#   - PROD.MET. = HIERRO TORSIONADO: se produce en Planta de Hierro
#     y Carpintería y se distribuye a toda la red. Es un movimiento
#     fijo y planificado, no una ineficiencia de abastecimiento.
#
# Para agregar más, añadir el nombre exacto del rubro (campo NomRBART)
# o el código de artículo (campo CodART).

INTERDEPOSITO_EXCLUIR_RUBROS = [
    'PROD.MET. = HIERRO TORSIONADO',
    # Agregar aquí otros rubros con movimientos fijos planificados
]

INTERDEPOSITO_EXCLUIR_ARTICULOS = [
    # Agregar aquí códigos de artículo específicos si es necesario
    # Ejemplo: '005263', '005264'
]

# ============================================================================
# UMBRALES PARA ALERTAS
# ============================================================================
UMBRALES = {
    'anomalia_zscore':               3.0,
    'quiebre_dias_critico':          15,
    'quiebre_dias_medio':            30,
    'stock_descendente_porcentaje': -50,
    'stock_descendente_meses':        2,
    'discrepancia_stock_absoluta':  100,   # unidades
    'discrepancia_stock_porcentaje': 10,   # %
}

# ============================================================================
# ARTÍCULOS A EXCLUIR DE ANÁLISIS DE QUIEBRES
# (servicios, no productos físicos)
# ============================================================================
SERVICIOS_EXCLUIR = [
    'CORTE METALURGICO',
    'CORTE',
    'SERVICIO',
    'FLETE',
    'TRANSPORTE',
    'MANO DE OBRA',
    'ACARREO',
]

# ============================================================================
# FIREBASE
# ============================================================================
FIREBASE_CREDENTIALS_PATH = 'serviceAccountKey.json'
FIREBASE_PROJECT_ID       = 'net-logistk-isla'
