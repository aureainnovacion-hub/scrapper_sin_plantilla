"""
utils.py
--------
Utilidades compartidas: logging, limpieza de datos, extracción con Regex
y validación de NIF/CIF español.
"""

import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional, List

import pandas as pd
from sqlalchemy.orm import Session

from src.models import Lead


# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────

def setup_logger(log_dir: str = "logs", level: int = logging.INFO) -> logging.Logger:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_path = os.path.join(log_dir, "scraper.log")

    logger = logging.getLogger("ai_client_scrapper")
    logger.setLevel(level)

    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    fh = RotatingFileHandler(log_path, maxBytes=5 * 1024 * 1024, backupCount=3)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


# ─────────────────────────────────────────────
# Validación NIF/CIF
# ─────────────────────────────────────────────

def validate_spanish_id(id_str: str) -> bool:
    id_str = id_str.upper().replace("-", "").replace(".", "").replace(" ", "")
    if len(id_str) != 9:
        return False

    nie_prefix = {"X": "0", "Y": "1", "Z": "2"}
    if id_str[0] in nie_prefix:
        temp_id = nie_prefix[id_str[0]] + id_str[1:]
    else:
        temp_id = id_str

    if re.match(r"^\d{8}[A-Z]$", temp_id):
        letras = "TRWAGMYFPDXBNJZSQVHLCKE"
        return letras[int(temp_id[:8]) % 23] == temp_id[8]

    if re.match(r"^[ABCDEFGHJNPQRSUVW]\d{7}[0-9A-J]$", id_str):
        letter = id_str[0]
        digits = [int(d) for d in id_str[1:8]]
        even_sum = digits[1] + digits[3] + digits[5]
        odd_sum = 0
        for i in [0, 2, 4, 6]:
            prod = digits[i] * 2
            odd_sum += (prod // 10) + (prod % 10)
        total_sum = even_sum + odd_sum
        control_digit = (10 - (total_sum % 10)) % 10
        control_letter = "JABCDEFGHI"[control_digit]
        last_char = id_str[8]
        if letter in "ABEH":
            return last_char == str(control_digit)
        elif letter in "PQSW":
            return last_char == control_letter
        else:
            return last_char == str(control_digit) or last_char == control_letter

    return False


# ─────────────────────────────────────────────
# Extracción y Limpieza
# ─────────────────────────────────────────────

def clean_text(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    return re.sub(r"\s+", " ", text.strip()) or None


def normalize_text_for_cif(text: str) -> str:
    text = text.upper().replace("\xa0", " ")
    text = re.sub(r'(\d)\.(\d)', r'\1\2', text)
    text = re.sub(r'(\d)\s+(?=\d)', r'\1', text)
    text = re.sub(r'([A-Z])\s*[-—·\.]\s*(\d)', r'\1\2', text)
    text = re.sub(r'(\d)\s*[-—·\.]\s*([A-Z])', r'\1\2', text)
    return text


def extract_emails(text: Optional[str]) -> List[str]:
    if not text:
        return []
    pattern = r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
    matches = re.findall(pattern, text)
    cleaned = [m.lower() for m in matches if not any(x in m.lower() for x in ['.png', '.jpg', '.js', '.css', 'example.com'])]
    return list(set(cleaned))


def extract_nifs(text: Optional[str]) -> List[str]:
    if not text:
        return []
    text_norm = normalize_text_for_cif(text)
    patterns = [
        r'(?:CIF|NIF|N\.I\.F\.|C\.I\.F\.|NIF/CIF)[:\s\-—·]*([A-Z0-9]{9})',
        r'\b([A-Z]\d{7}[0-9A-Z])\b',
        r'\b(\d{8}[A-Z])\b',
    ]
    found = []
    for pattern in patterns:
        for m in re.findall(pattern, text_norm):
            cif_clean = re.sub(r'[^\w]', '', str(m).upper())
            if len(cif_clean) == 9 and validate_spanish_id(cif_clean):
                found.append(cif_clean)
    return list(set(found))


def normalize_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    url = url.strip()
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url or None


# ─────────────────────────────────────────────
# Persistencia
# ─────────────────────────────────────────────

def save_lead(session: Session, lead_data: dict, logger: logging.Logger) -> bool:
    """
    Guarda un lead si tiene nombre + al menos un dato de contacto
    (email, teléfono o web).
    """
    nombre   = lead_data.get("nombre", "").strip()
    fuente   = lead_data.get("fuente", "desconocida")
    web      = normalize_url(lead_data.get("web"))
    nif      = lead_data.get("nif")
    email    = lead_data.get("email")
    telefono = lead_data.get("telefono")

    if not nombre or not (email or telefono or web):
        logger.debug(
            f"Descartado (sin contacto): '{nombre}' "
            f"| email={email} | tel={telefono} | web={web}"
        )
        return False

    existing = session.query(Lead).filter_by(nombre=nombre, fuente=fuente).first()
    if existing:
        updated = False
        if not existing.nif and nif:
            existing.nif = nif
            updated = True
        if not existing.email and email:
            existing.email = email
            updated = True
        if not existing.web and web:
            existing.web = web
            updated = True
        if not existing.telefono and telefono:
            existing.telefono = telefono
            updated = True
        if updated:
            session.commit()
            logger.info(f"Lead actualizado: '{nombre}'")
        return False

    lead = Lead(
        nombre=nombre,
        web=web,
        nif=nif,
        email=email,
        telefono=telefono,
        fuente=fuente,
        keyword=lead_data.get("keyword"),
    )
    session.add(lead)
    try:
        session.commit()
        logger.info(f"Lead guardado: '{nombre}' | Tel={telefono} | Email={email} | Web={web}")
        return True
    except Exception as exc:
        session.rollback()
        logger.error(f"Error al guardar lead '{nombre}': {exc}")
        return False


def export_to_csv(session: Session, output_path: str, logger: logging.Logger) -> None:
    leads = session.query(Lead).all()
    if not leads:
        return
    df = pd.DataFrame([lead.to_dict() for lead in leads])
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    logger.info(f"Exportados {len(df)} leads a '{output_path}'.")
