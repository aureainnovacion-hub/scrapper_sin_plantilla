"""
enrich_leads.py
---------------
Script para enriquecer los leads existentes en la base de datos con información
de subvenciones públicas obtenida de la BDNS (Hacienda).
"""

import os
import sys
import time
from dotenv import load_dotenv
from pathlib import Path

# Añadir el directorio raíz al path para importar módulos locales
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.models import init_db, Lead
from src.utils import setup_logger
from services.bdns_service import check_subsidies

# Cargar variables de entorno
load_dotenv(PROJECT_ROOT / ".env")

# Configuración
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///data/leads.db")
LOG_DIR = os.getenv("LOG_DIR", "logs")
logger = setup_logger(LOG_DIR)


def enrich_leads():
    """Recorre los leads con NIF y actualiza sus datos de subvenciones."""
    logger.info("=" * 60)
    logger.info("Iniciando proceso de enriquecimiento de subvenciones (BDNS)")
    logger.info(f"Base de datos: {DATABASE_URL}")
    logger.info("=" * 60)

    session = init_db(DATABASE_URL)
    
    # Obtener leads que tengan NIF y que no hayan sido enriquecidos aún 
    # (o simplemente todos los que tengan NIF)
    leads_to_enrich = session.query(Lead).filter(Lead.nif.isnot(None), Lead.total_subvenciones == 0.0).all()
    
    if not leads_to_enrich:
        logger.warning("No se encontraron leads con NIF para enriquecer.")
        session.close()
        return

    logger.info(f"Se han encontrado {len(leads_to_enrich)} leads con NIF.")
    
    enriched_count = 0
    error_count = 0

    for lead in leads_to_enrich:
        logger.info(f"Consultando BDNS para: {lead.nombre} (NIF: {lead.nif})")
        
        # Llamada al servicio BDNS
        result = check_subsidies(lead.nif)
        
        if "error" in result:
            logger.error(f"Error consultando {lead.nif}: {result['error']}")
            error_count += 1
            continue
            
        # Actualizar campos del lead
        lead.total_subvenciones = result.get("total_amount", 0.0)
        lead.num_concesiones = result.get("total_subsidies", 0)
        
        # Lógica de prioridad: por ejemplo, si ha recibido más de 50.000€ en subvenciones
        # o tiene más de 3 concesiones. (Personalizable según necesidad)
        lead.es_prioritario = lead.total_subvenciones > 50000 or lead.num_concesiones > 3
        
        logger.info(
            f"  -> Resultado: {lead.num_concesiones} concesiones | "
            f"Total: {lead.total_subvenciones:,.2f}€ | Prioritario: {lead.es_prioritario}"
        )
        
        enriched_count += 1
        
        # Respetar la API de Hacienda con un pequeño delay
        time.sleep(1)

    try:
        session.commit()
        logger.info("=" * 60)
        logger.info(f"Proceso completado con éxito.")
        logger.info(f"Leads enriquecidos: {enriched_count}")
        logger.info(f"Errores: {error_count}")
        logger.info("=" * 60)
    except Exception as e:
        logger.error(f"Error al guardar los cambios en la base de datos: {e}")
        session.rollback()
    finally:
        session.close()


if __name__ == "__main__":
    enrich_leads()
