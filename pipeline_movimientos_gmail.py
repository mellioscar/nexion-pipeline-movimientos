"""
pipeline_movimientos_gmail.py
Bridge: Gmail "Movimientos de stock diarios" → analizar_stock.py → Firebase
Carlos Isla y Cía. — NET-LogistK ISLA

Arquitectura:
  - Este pipeline procesa SOLO los movimientos diarios del mes en curso.
  - El stock a una fecha se procesa en un repositorio separado (nexion-pipeline-stock).
  - Stock_Inicial siempre se pasa vacío → analizar_stock.py corre en modo diario.

Exclusiones de interdepósito:
  - Los rubros y artículos definidos en INTERDEPOSITO_EXCLUIR_RUBROS y
    INTERDEPOSITO_EXCLUIR_ARTICULOS de config.py se eliminan de las líneas
    REM-INTER / RCP-INTER ANTES de pasar el DataFrame a analizar_stock.py.
  - Esto evita que movimientos fijos planificados (ej: Hierro Torsionado
    desde Planta de Hierro) contaminen el análisis de interdepósito.

Flujo:
  1. Busca en Gmail el email con asunto configurable (no procesado aún)
  2. Descarga el adjunto .xlsx (hoja STK)
  3. Normaliza columnas STK → esquema de analizar_stock.py
  4. Filtra exclusiones de interdepósito
  5. Crea Excel temporal con hojas Movimientos + Stock_Inicial (vacío)
  6. Llama a analizar_periodo() → subir_a_firebase()
  7. Marca el email con label NEXION_MOV_PROCESADO

Mapeo de columnas STK → analizar_stock.py:
  FecComp       → Fec. Comp.
  AbrevCOM      → Comp.
  NroComp       → Número
  CodDEP        → Depósito
  CodART        → Artículo
  DescART       → Descripción
  ImpCostoItem  → Imp. Costo
  NomRDR        → Nom RDR
  DescVHC       → Desc VHC
  NomFMART      → Nombre familia
  NomGRART      → Nombre grupo
  NomRBART      → Nombre rubro
  (Cant, CantIngreso, CantEgreso, Kgs → mismo nombre, no se renombran)

Firestore resultante:
  stock_analytics/{YYYY-MM}   ← analizar_stock.subir_a_firebase()
  stock_historico/{YYYY-MM}   ← analizar_stock.subir_a_firebase()
"""

import os
import base64
import tempfile
import logging
from datetime import datetime
from dotenv import load_dotenv

import pandas as pd

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# Pipeline existente — NO se modifica
from analizar_stock import (
    analizar_periodo,
    inicializar_firebase,
    subir_a_firebase,
)
from config import (
    INTERDEPOSITO_EXCLUIR_RUBROS,
    INTERDEPOSITO_EXCLUIR_ARTICULOS,
)

load_dotenv()

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
GMAIL_CREDS  = os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")
GMAIL_TOKEN  = os.getenv("GMAIL_TOKEN_PATH",       "token_movimientos.json")
ASUNTO_MOV   = os.getenv("ASUNTO_MOVIMIENTOS",     "Movimientos de stock diarios")
LABEL_MOV    = "Procesado-Movimientos"

# Comprobantes de interdepósito donde se aplican las exclusiones
COMP_INTERDEPOSITO = {"REM-INTER", "RCP-INTER"}

# Mapeo de columnas STK → analizar_stock.py
MAPEO = {
    "FecComp":      "Fec. Comp.",
    "AbrevCOM":     "Comp.",
    "NroComp":      "Número",
    "CodDEP":       "Depósito",
    "NomDEP":       "NomDEP",
    "CodART":       "Artículo",
    "DescART":      "Descripción",
    "ImpCostoItem": "Imp. Costo",
    "NomRDR":       "Nom RDR",
    "DescVHC":      "Desc VHC",
    "NomFMART":     "Nombre familia",
    "NomGRART":     "Nombre grupo",
    "NomRBART":     "Nombre rubro",
}

# Las que validar_datos() exige con sys.exit(1)
COLS_REQUERIDAS = [
    "Fec. Comp.", "Comp.", "Depósito", "Artículo", "Descripción",
    "Cant", "CantIngreso", "CantEgreso", "Kgs", "Imp. Costo",
]

# Columnas mínimas de Stock_Inicial (siempre vacío en este pipeline)
COLS_STOCK_INI = ["Depósito", "Artículo", "Stock_Unidades", "Stock_Kg"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 1. GMAIL
# ─────────────────────────────────────────────────────────────────────────────
def get_gmail_service():
    """OAuth2 con refresh automático. Crea TOKEN_REFRESHED_MOV si se renovó."""
    creds = None
    if os.path.exists(GMAIL_TOKEN):
        creds = Credentials.from_authorized_user_file(GMAIL_TOKEN, GMAIL_SCOPES)

    refrescado = False
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            log.info("Token expirado — refrescando...")
            creds.refresh(Request())
            refrescado = True
        else:
            # Primera ejecución local — abre el navegador una sola vez
            flow = InstalledAppFlow.from_client_secrets_file(GMAIL_CREDS, GMAIL_SCOPES)
            creds = flow.run_local_server(port=0)
            refrescado = True

    with open(GMAIL_TOKEN, "w") as f:
        f.write(creds.to_json())

    if refrescado:
        with open("TOKEN_REFRESHED_MOV", "w") as f:
            f.write("1")
        log.info(f"Token actualizado → {GMAIL_TOKEN}")

    return build("gmail", "v1", credentials=creds)


def buscar_email(service) -> str | None:
    q      = f'subject:"{ASUNTO_MOV}" -label:{LABEL_MOV} has:attachment'
    result = service.users().messages().list(userId="me", q=q, maxResults=1).execute()
    msgs   = result.get("messages", [])
    if not msgs:
        log.info("Sin emails nuevos de movimientos.")
        return None
    log.info(f"Email encontrado: {msgs[0]['id']}")
    return msgs[0]["id"]


def descargar_excel(service, msg_id: str) -> tuple[bytes | None, str]:
    """Retorna (bytes, nombre) del primer adjunto .xlsx."""
    mensaje = service.users().messages().get(userId="me", id=msg_id).execute()
    for parte in mensaje.get("payload", {}).get("parts", []):
        nombre = parte.get("filename", "")
        if nombre.lower().endswith((".xlsx", ".xls")):
            att_id = parte["body"].get("attachmentId")
            if att_id:
                raw  = service.users().messages().attachments().get(
                    userId="me", messageId=msg_id, id=att_id
                ).execute()
                data = base64.urlsafe_b64decode(raw["data"])
                log.info(f"Excel descargado: {nombre} ({len(data)/1024:.1f} KB)")
                return data, nombre
    log.warning("Sin adjunto Excel en el email.")
    return None, ""


def marcar_procesado(service, msg_id: str):
    labels   = service.users().labels().list(userId="me").execute().get("labels", [])
    label_id = next((l["id"] for l in labels if l["name"] == LABEL_MOV), None)
    if not label_id:
        nuevo    = service.users().labels().create(
            userId="me",
            body={"name": LABEL_MOV, "labelListVisibility": "labelShow"}
        ).execute()
        label_id = nuevo["id"]
    service.users().messages().modify(
        userId="me", id=msg_id, body={"addLabelIds": [label_id]}
    ).execute()
    log.info(f"Email marcado: {LABEL_MOV}")


# ─────────────────────────────────────────────────────────────────────────────
# 2. NORMALIZACIÓN Y FILTRADO
# ─────────────────────────────────────────────────────────────────────────────
def normalizar_columnas(df: pd.DataFrame) -> pd.DataFrame:
    """Renombra columnas STK al esquema de analizar_stock.py."""
    df.columns = [str(c).strip() for c in df.columns]
    log.info(f"Columnas en el export ({len(df.columns)}): {list(df.columns[:8])}...")

    renombrar = {k: v for k, v in MAPEO.items() if k in df.columns}
    df        = df.rename(columns=renombrar)
    log.info(f"Renombradas: {list(renombrar.keys())}")

    faltantes = [c for c in COLS_REQUERIDAS if c not in df.columns]
    if faltantes:
        raise ValueError(
            f"Columnas faltantes tras normalizar: {faltantes}\n"
            f"Revisar MAPEO en pipeline_movimientos_gmail.py."
        )
    log.info("✓ Todas las columnas requeridas presentes")
    return df


def aplicar_exclusiones_interdeposito(df: pd.DataFrame) -> pd.DataFrame:
    """
    Elimina de las líneas REM-INTER / RCP-INTER los artículos y rubros
    configurados en INTERDEPOSITO_EXCLUIR_RUBROS y INTERDEPOSITO_EXCLUIR_ARTICULOS
    de config.py.

    Los movimientos no interdepósito (Remitos, Recep.Cpra, ajustes, RMC)
    NO se tocan — pasan completos a analizar_stock.py.
    """
    if not INTERDEPOSITO_EXCLUIR_RUBROS and not INTERDEPOSITO_EXCLUIR_ARTICULOS:
        log.info("Sin exclusiones de interdepósito configuradas.")
        return df

    mask_inter = df["Comp."].isin(COMP_INTERDEPOSITO)
    total_inter = mask_inter.sum()

    # Máscara de exclusión (solo aplica a líneas de interdepósito)
    mask_excluir = pd.Series(False, index=df.index)

    if INTERDEPOSITO_EXCLUIR_RUBROS and "Nombre rubro" in df.columns:
        mask_excluir |= (
            mask_inter &
            df["Nombre rubro"].isin(INTERDEPOSITO_EXCLUIR_RUBROS)
        )

    if INTERDEPOSITO_EXCLUIR_ARTICULOS and "Artículo" in df.columns:
        mask_excluir |= (
            mask_inter &
            df["Artículo"].isin(INTERDEPOSITO_EXCLUIR_ARTICULOS)
        )

    excluidas = mask_excluir.sum()
    if excluidas > 0:
        log.info(
            f"Exclusiones interdepósito: {excluidas} líneas eliminadas "
            f"de {total_inter} ({excluidas/total_inter*100:.1f}%) | "
            f"Rubros: {INTERDEPOSITO_EXCLUIR_RUBROS}"
        )
        df = df[~mask_excluir].copy()
    else:
        log.info(f"Sin líneas de interdepósito excluidas (total interdep: {total_inter})")

    return df


def preparar_excel_temporal(data: bytes) -> str:
    """
    Lee el STK diario, normaliza columnas, aplica exclusiones de
    interdepósito y crea un Excel temporal con dos hojas:
        - Movimientos   ← datos procesados
        - Stock_Inicial ← siempre vacío (análisis sin snapshot mensual)

    analizar_stock.cargar_datos() lee exactamente esas dos hojas.
    """
    df = pd.read_excel(data, sheet_name="STK", header=0)
    log.info(f"STK cargado: {len(df):,} filas")

    # 1. Normalizar columnas
    df = normalizar_columnas(df)

    # 2. Filtrar exclusiones de interdepósito
    df = aplicar_exclusiones_interdeposito(df)

    # Resumen antes de crear el temp
    comps = df["Comp."].value_counts().to_dict()
    log.info(f"Comprobantes tras filtros: {comps}")

    # 3. Stock_Inicial vacío (modo diario — sin snapshot mensual)
    df_stock_ini = pd.DataFrame(columns=COLS_STOCK_INI)

    # 4. Guardar Excel temporal con las dos hojas
    tmp = tempfile.NamedTemporaryFile(
        delete=False, suffix=".xlsx", prefix="nexion_mov_"
    )
    tmp.close()
    with pd.ExcelWriter(tmp.name, engine="openpyxl") as writer:
        df.to_excel(writer,           sheet_name="Movimientos",  index=False)
        df_stock_ini.to_excel(writer, sheet_name="Stock_Inicial", index=False)

    log.info(
        f"Excel temporal: {tmp.name} "
        f"({os.path.getsize(tmp.name)/1024:.1f} KB) | "
        f"{len(df):,} movimientos"
    )
    return tmp.name


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 60)
    log.info("PIPELINE MOVIMIENTOS: Gmail → analizar_stock.py → Firebase")
    log.info("=" * 60)

    tmp_path = None
    try:
        # 1. Gmail
        service = get_gmail_service()
        msg_id  = buscar_email(service)
        if not msg_id:
            return

        # 2. Descargar Excel
        data, _ = descargar_excel(service, msg_id)
        if not data:
            return

        # 3. Normalizar + filtrar + crear Excel temporal
        tmp_path = preparar_excel_temporal(data)

        # 4. Detectar período desde los datos
        df_check  = pd.read_excel(
            tmp_path, sheet_name="Movimientos",
            usecols=["Fec. Comp."], nrows=5
        )
        fecha_max = pd.to_datetime(df_check["Fec. Comp."], errors="coerce").max()
        periodo   = fecha_max.strftime("%Y-%m") if pd.notna(fecha_max) \
                    else datetime.now().strftime("%Y-%m")
        log.info(f"Período: {periodo} (datos hasta {fecha_max.date()})")

        # 5. Analizar — analizar_stock.py sin modificaciones
        #    Stock_Inicial vacío → modo diario (sin proyección de quiebres)
        db        = inicializar_firebase()
        resultado = analizar_periodo(
            tmp_path,
            periodo,
            incluir_interdeposito=True,
            incluir_alertas=True,
        )
        subir_a_firebase(resultado, db)

        # 6. Marcar email procesado
        marcar_procesado(service, msg_id)

        total = len(pd.read_excel(tmp_path, sheet_name="Movimientos"))
        valor = resultado["metricas_base"]["egresos"]["total_egresos"]["pesos"]
        log.info(f"\n✅ Pipeline completado — {periodo}")
        log.info(f"   Movimientos : {total:,}")
        log.info(f"   Valor total : ${valor/1e6:.1f}M")

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
            log.info("Archivo temporal eliminado.")


if __name__ == "__main__":
    main()
