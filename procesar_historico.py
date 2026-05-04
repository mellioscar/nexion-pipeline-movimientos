import os
import logging
from pipeline_movimientos_gmail import preparar_excel_temporal
from analizar_stock import analizar_periodo, inicializar_firebase, subir_a_firebase

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

CARPETA_HISTORICOS = './historicos'

# Mapeo del nombre de tu archivo físico al período en Firestore
archivos_a_procesar = {
    '2025_09.xlsx': '2025-09',
    '2025_10.xlsx': '2025-10',
    '2025_11.xlsx': '2025-11',
    '2025_12.xlsx': '2025-12',
    '2026_01.xlsx': '2026-01',
    '2026_02.xlsx': '2026-02',
    '2026_03.xlsx': '2026-03',
    '2026_04.xlsx': '2026-04',
}

def main():
    log.info("Iniciando carga histórica a Firebase...")
    db = inicializar_firebase()
    
    for archivo, periodo in archivos_a_procesar.items():
        ruta_completa = os.path.join(CARPETA_HISTORICOS, archivo)
        
        if not os.path.exists(ruta_completa):
            log.warning(f"Archivo no encontrado: {archivo}. Saltando...")
            continue
            
        log.info(f"--- Procesando {periodo} desde {archivo} ---")
        
        try:
            # 1. Leemos el archivo local como bytes (simulando que viene de Gmail)
            with open(ruta_completa, "rb") as f:
                data = f.read()
            
            # 2. Usamos tu misma función del pipeline para normalizar columnas y limpiar Hierro Torsionado
            tmp_path = preparar_excel_temporal(data)
            
            # 3. Analizamos el período con la regla de chapas de 13m y separamos ajustes
            resultado = analizar_periodo(
                ruta_excel=tmp_path, 
                periodo=periodo, 
                incluir_interdeposito=True, 
                incluir_alertas=True
            )
            
            # 4. Subimos a la nueva colección de indicadores
            subir_a_firebase(resultado, db)
            
            # Limpiamos el temporal local
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
                
        except Exception as e:
            log.error(f"Error al procesar {periodo}: {e}")

if __name__ == "__main__":
    main()