"""
scraper.py
----------
Script principal de scraping multi-fuente con navegación profunda y prioridad legal.
Fuentes: Páginas Amarillas, Kompass, Bing Maps
"""

import os
import re
import sys
import time
import random
from pathlib import Path
from typing import Optional, List, Dict
from urllib.parse import urlparse, urlunparse, parse_qs

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, Page, TimeoutError as PWTimeout

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.models import init_db
from src.utils import (
    setup_logger, save_lead, export_to_csv, clean_text,
    extract_emails, extract_nifs, normalize_url, validate_spanish_id
)

# ─────────────────────────────────────────────
# Configuración
# ─────────────────────────────────────────────

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

KEYWORD      = os.getenv("SEARCH_KEYWORD", "instalaciones solares")
LOCATION     = os.getenv("SEARCH_LOCATION", "").strip()
MAX_PAGES    = int(os.getenv("MAX_PAGES", "5"))
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///data/leads.db")
LOG_DIR      = os.getenv("LOG_DIR", "logs")
HEADLESS     = os.getenv("HEADLESS", "true").lower() != "false"

PROJECT_ROOT = Path(__file__).parent.parent
db_path_raw = DATABASE_URL.replace("sqlite:///", "")
if not Path(db_path_raw).is_absolute():
    DATABASE_URL = f"sqlite:///{PROJECT_ROOT / db_path_raw}"
LOG_DIR = str(PROJECT_ROOT / LOG_DIR)

logger = setup_logger(LOG_DIR)


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

LEGAL_KEYWORDS   = ["aviso legal", "legal notice", "privacidad", "privacy", "rgpd", "lopd", "quienes somos"]
CONTACT_KEYWORDS = ["contacto", "contact"]


def random_delay(min_s: float = 1.0, max_s: float = 2.0) -> None:
    time.sleep(random.uniform(min_s, max_s))


def deep_extract_from_website(page: Page, web_url: str) -> Dict[str, Optional[str]]:
    """Navega por la web buscando NIF y Email con prioridad a páginas legales."""
    results = {"email": None, "nif": None}
    if not web_url or not web_url.startswith("http"):
        return results

    try:
        logger.debug(f"Deep Scraping Home: {web_url}")
        page.goto(web_url, timeout=15_000, wait_until="domcontentloaded")
        random_delay(0.5, 1.0)
        html_home = page.content()

        emails_home = extract_emails(html_home)
        nifs_home   = extract_nifs(html_home)
        if emails_home: results["email"] = emails_home[0]
        if nifs_home:   results["nif"]   = nifs_home[0]

        all_links = page.evaluate("""
            () => Array.from(document.querySelectorAll('a'))
                .map(a => ({ text: a.innerText.toLowerCase(), href: a.href }))
                .filter(l => l.href && l.href.startsWith('http'))
        """)

        legal_links   = [l['href'] for l in all_links if any(k in l['text'] for k in LEGAL_KEYWORDS)]
        contact_links = [l['href'] for l in all_links if any(k in l['text'] for k in CONTACT_KEYWORDS)]

        for link in set(legal_links[:2]):
            try:
                page.goto(link, timeout=10_000, wait_until="domcontentloaded")
                random_delay(0.5, 1.0)
                html_legal   = page.content()
                nifs_legal   = extract_nifs(html_legal)
                emails_legal = extract_emails(html_legal)
                if nifs_legal:   results["nif"]   = nifs_legal[0]
                if emails_legal: results["email"] = emails_legal[0]
                if results["nif"]: break
            except Exception:
                continue

        if not results["email"]:
            for link in set(contact_links[:2]):
                try:
                    page.goto(link, timeout=10_000, wait_until="domcontentloaded")
                    random_delay(0.5, 1.0)
                    emails_contact = extract_emails(page.content())
                    if emails_contact:
                        results["email"] = emails_contact[0]
                        break
                except Exception:
                    continue

    except Exception as exc:
        logger.debug(f"Error en deep_extract '{web_url}': {exc}")

    return results


def _pa_extract_ficha(page: Page, ficha_url: str) -> Dict[str, Optional[str]]:
    """
    Visita la ficha individual de una empresa en Páginas Amarillas
    y extrae teléfono, web, email y NIF.
    """
    data = {"telefono": None, "web": None, "email": None, "nif": None}
    try:
        page.goto(ficha_url, timeout=20_000, wait_until="domcontentloaded")
        random_delay(1.0, 2.0)
        html = page.content()

        result = page.evaluate("""
            () => {
                let telefono = null;
                for (const sel of [
                    '[itemprop="telephone"]', '.phone', '.tel',
                    '[class*="phone"]', '[class*="tel"]', 'a[href^="tel:"]'
                ]) {
                    const el = document.querySelector(sel);
                    if (el) {
                        telefono = el.innerText.trim()
                                || (el.getAttribute('href') || '').replace('tel:', '');
                        if (telefono) break;
                    }
                }

                let web = null;
                for (const sel of [
                    'a[href*="http"]:not([href*="paginasamarillas"]):not([href*="facebook"]):not([href*="twitter"]):not([href*="instagram"]):not([href*="beedigital"])',
                    '[itemprop="url"]', '.web a', 'a.web'
                ]) {
                    const el = document.querySelector(sel);
                    if (el && el.href && el.href.startsWith('http')) {
                        web = el.href;
                        break;
                    }
                }

                let email = null;
                const emailLink = document.querySelector('a[href^="mailto:"]');
                if (emailLink) email = emailLink.href.replace('mailto:', '');

                return { telefono, web, email };
            }
        """)

        data.update({k: v for k, v in result.items() if v})

        nifs = extract_nifs(html)
        if nifs: data["nif"] = nifs[0]

        if not data["email"]:
            emails = extract_emails(html)
            if emails: data["email"] = emails[0]

    except Exception as exc:
        logger.debug(f"Error extrayendo ficha PA '{ficha_url}': {exc}")

    return data


def _save_leads(page: Page, session, leads: list, source: str, keyword: str) -> int:
    """Hace deep extract de la web propia y guarda cada lead."""
    unique = list({d["nombre"]: d for d in leads if d.get("nombre", "").strip()}.values())
    logger.info(f"[{source}] {len(unique)} leads únicos. Extrayendo datos...")

    total = 0
    for i, data in enumerate(unique, 1):
        web_url   = normalize_url(data.get("web"))
        deep_data = {"email": None, "nif": None}
        if web_url:
            logger.debug(f"[{source}] ({i}/{len(unique)}) Deep extract: {data['nombre']}")
            deep_data = deep_extract_from_website(page, web_url)

        lead_final = {
            "nombre":   data["nombre"],
            "web":      web_url,
            "email":    deep_data["email"] or data.get("email"),
            "nif":      deep_data["nif"]   or data.get("nif"),
            "telefono": data.get("telefono"),
            "fuente":   source,
            "keyword":  keyword,
        }
        if save_lead(session, lead_final, logger):
            total += 1

    return total


# ─────────────────────────────────────────────
# Fuente 1 — Páginas Amarillas
# ─────────────────────────────────────────────

def scrape_paginas_amarillas(page: Page, session, keyword: str, max_pages: int, location: str = "") -> int:
    source        = "Páginas Amarillas"
    keyword_slug  = keyword.replace(" ", "-").lower()
    location_slug = location.replace(" ", "-").lower() if location else "all-ci"
    raw_leads     = []

    for page_num in range(1, max_pages + 1):
        url_listado = (
            f"https://www.paginasamarillas.es/search/{keyword_slug}"
            f"/all-ma/all-pr/all-is/{location_slug}/all-ba/all-pu/all-nc/{page_num}"
            f"?what={keyword.replace(' ', '+')}"
            + (f"&where={location.replace(' ', '+')}" if location else "")
            + "&qc=true"
        )
        logger.info(f"[{source}] Página {page_num} — '{location or 'toda España'}'")

        try:
            page.goto(url_listado, timeout=25_000, wait_until="domcontentloaded")
            random_delay(1, 2)
            card_selector = "div.listado-item, article.advert-item, div[id^='advert-']"
            page.wait_for_selector(card_selector, timeout=10_000)

            # Extraer nombre + enlace a ficha de cada tarjeta del listado
            tarjetas = page.evaluate("""
                () => {
                    const items = document.querySelectorAll('div.listado-item, article.advert-item, div[id^="advert-"]');
                    return Array.from(items).map(card => {
                        const h2 = card.querySelector('h2, .business-name, a[title]');
                        const nombre = h2 ? h2.innerText.replace('+info','').trim() : '';

                        // Enlace a la ficha individual
                        let ficha = '';
                        const fichaEl = card.querySelector('h2 a, h3 a, a[href*="/empresas/"], a[href*="/es/pr/"]');
                        if (fichaEl) ficha = fichaEl.href;

                        // Teléfono visible en el listado como fallback
                        let telefono = '';
                        const telEl = card.querySelector(
                            '[itemprop="telephone"], [class*="phone"], [class*="tel"], a[href^="tel:"]'
                        );
                        if (telEl) telefono = telEl.innerText.trim()
                                           || (telEl.getAttribute('href') || '').replace('tel:', '');

                        // Web visible en el listado como fallback
                        const webEl = card.querySelector('a.web, a[href*="http"]:not([href*="paginasamarillas"])');
                        const web = webEl ? webEl.href : '';

                        return { nombre, ficha, telefono, web };
                    }).filter(d => d.nombre.length > 2);
                }
            """)

            logger.info(f"[{source}] Página {page_num}: {len(tarjetas)} tarjetas")

            for i, tarjeta in enumerate(tarjetas, 1):
                nombre    = tarjeta["nombre"]
                ficha_url = tarjeta.get("ficha", "")

                # Si hay ficha individual, visitarla para obtener teléfono
                if ficha_url and ficha_url.startswith("http"):
                    logger.debug(f"  ({i}/{len(tarjetas)}) Ficha: {nombre}")
                    ficha_data = _pa_extract_ficha(page, ficha_url)

                    raw_leads.append({
                        "nombre":   nombre,
                        "telefono": ficha_data.get("telefono") or tarjeta.get("telefono"),
                        "web":      ficha_data.get("web")      or tarjeta.get("web"),
                        "email":    ficha_data.get("email"),
                        "nif":      ficha_data.get("nif"),
                    })

                    # Volver al listado con goto() — más fiable que go_back()
                    page.goto(url_listado, timeout=25_000, wait_until="domcontentloaded")
                    random_delay(1.0, 2.0)
                else:
                    # Sin ficha, usar datos del listado directamente
                    raw_leads.append({
                        "nombre":   nombre,
                        "telefono": tarjeta.get("telefono"),
                        "web":      tarjeta.get("web"),
                        "email":    None,
                        "nif":      None,
                    })

            if not page.query_selector('a[rel="next"], .pagination-next'):
                break

        except Exception as e:
            logger.warning(f"[{source}] Error en página {page_num}: {e}")
            break

    return _save_leads(page, session, raw_leads, source, keyword)


# ─────────────────────────────────────────────
# Fuente 2 — Kompass
# ─────────────────────────────────────────────

def scrape_kompass(page: Page, session, keyword: str, max_pages: int, location: str = "") -> int:
    source      = "Kompass"
    keyword_enc = keyword.replace(" ", "+")
    query       = f"{keyword_enc}+{location.replace(' ', '+')}" if location else keyword_enc
    raw_leads   = []

    for page_num in range(1, max_pages + 1):
        url = f"https://es.kompass.com/searchCompanies?text={query}&page={page_num}"
        logger.info(f"[{source}] Página {page_num}: {url}")

        try:
            page.set_extra_http_headers({
                "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": "https://www.google.es/",
            })
            page.goto(url, timeout=30_000, wait_until="domcontentloaded")
            random_delay(2.5, 4.0)

            try:
                page.wait_for_selector(
                    ".companyCard, .company-card, [class*='CompanyCard'], article[class*='company']",
                    timeout=10_000
                )
            except PWTimeout:
                logger.warning(f"[{source}] Sin resultados en página {page_num}")
                diag = PROJECT_ROOT / "logs" / f"kompass_p{page_num}_debug.html"
                diag.write_text(page.content(), encoding="utf-8")
                break

            cards = page.evaluate("""
                () => {
                    const sels = ['.companyCard','.company-card','[class*="CompanyCard"]','article[class*="company"]'];
                    let items = [];
                    for (const s of sels) { items = document.querySelectorAll(s); if (items.length) break; }
                    return Array.from(items).map(card => {
                        const nameEl  = card.querySelector('h2,h3,[class*="name"],[class*="Name"]');
                        const nombre  = nameEl ? nameEl.innerText.trim() : '';
                        const webEl   = card.querySelector('a[href^="http"]:not([href*="kompass"])');
                        const web     = webEl ? webEl.href : '';
                        const telEl   = card.querySelector('[class*="phone"],[class*="tel"],a[href^="tel:"]');
                        let telefono  = '';
                        if (telEl) telefono = telEl.innerText.trim() || (telEl.getAttribute('href') || '').replace('tel:', '');
                        const cifEl   = card.querySelector('[class*="vat"],[class*="siret"],[class*="nif"]');
                        const nif     = cifEl ? cifEl.innerText.trim() : '';
                        const fichaEl = card.querySelector('a[href*="/es/"]');
                        const ficha   = fichaEl ? fichaEl.href : '';
                        return { nombre, web, telefono, nif, ficha };
                    }).filter(d => d.nombre.length > 2);
                }
            """)

            logger.info(f"[{source}] Página {page_num}: {len(cards)} tarjetas")

            for card in cards:
                if card.get("ficha") and "kompass" in card.get("ficha", ""):
                    try:
                        page.goto(card["ficha"], timeout=15_000, wait_until="domcontentloaded")
                        random_delay(1.5, 3.0)
                        html_ficha = page.content()
                        if not card.get("email"):
                            emails = extract_emails(html_ficha)
                            if emails: card["email"] = emails[0]
                        if not card.get("nif"):
                            nifs = extract_nifs(html_ficha)
                            if nifs: card["nif"] = nifs[0]
                        if not card.get("web"):
                            m = re.search(r'href="(https?://(?!.*kompass\.com)[^"]{5,})"', html_ficha)
                            if m: card["web"] = m.group(1)
                        if not card.get("telefono"):
                            telEl = page.query_selector('[class*="phone"],[class*="tel"],a[href^="tel:"]')
                            if telEl:
                                card["telefono"] = telEl.inner_text().strip() or \
                                    (telEl.get_attribute("href") or "").replace("tel:", "")
                    except Exception as exc:
                        logger.debug(f"Error ficha Kompass: {exc}")

            raw_leads.extend(cards)

            has_next = page.evaluate("""
                !!document.querySelector('a[aria-label="Next"],.pagination__next,[class*="paginationNext"]')
            """)
            if not has_next:
                break

        except Exception as exc:
            logger.error(f"[{source}] Error página {page_num}: {exc}")
            break

    return _save_leads(page, session, raw_leads, source, keyword)


# ─────────────────────────────────────────────
# Fuente 3 — Bing Maps
# ─────────────────────────────────────────────

def scrape_bing_maps(page: Page, session, keyword: str, max_pages: int, location: str = "") -> int:
    source    = "Bing Maps"
    query     = f"{keyword} {location}".strip() if location else keyword
    query_enc = query.replace(" ", "%20")
    raw_leads = []

    url = f"https://www.bing.com/maps?q={query_enc}&mkt=es-ES"
    logger.info(f"[{source}] Búsqueda: {url}")

    try:
        page.set_extra_http_headers({
            "Accept-Language": "es-ES,es;q=0.9",
            "Referer": "https://www.bing.com/",
        })
        page.goto(url, timeout=30_000, wait_until="domcontentloaded")
        random_delay(3.0, 5.0)

        try:
            page.wait_for_selector(
                ".listings-container, [class*='listings'], [class*='listItem'], [data-entity-id]",
                timeout=12_000
            )
        except PWTimeout:
            logger.warning(f"[{source}] No cargó la lista de resultados.")
            diag = PROJECT_ROOT / "logs" / "bing_maps_debug.html"
            diag.write_text(page.content(), encoding="utf-8")
            return 0

        for _ in range(min(max_pages, 5)):
            page.evaluate("""
                const c = document.querySelector('.listings-container,[class*="listings"]');
                if (c) c.scrollBy(0, 600);
            """)
            random_delay(1.5, 2.5)

        cards = page.evaluate("""
            () => {
                const sels = ['.listings-item','[class*="listingItem"]','[class*="listItem"]','[data-entity-id]'];
                let items = [];
                for (const s of sels) { items = document.querySelectorAll(s); if (items.length) break; }
                return Array.from(items).map(card => {
                    const nameEl = card.querySelector('h2,h3,[class*="title"],[class*="name"]');
                    const nombre = nameEl ? nameEl.innerText.trim() : '';
                    const telEl  = card.querySelector('[class*="phone"],[class*="tel"],a[href^="tel:"]');
                    let telefono = '';
                    if (telEl) telefono = telEl.innerText.trim() || (telEl.getAttribute('href') || '').replace('tel:', '');
                    const webEl = card.querySelector('a[href^="http"]:not([href*="bing"])');
                    const web   = webEl ? webEl.href : '';
                    return { nombre, telefono, web };
                }).filter(d => d.nombre.length > 2);
            }
        """)

        logger.info(f"[{source}] {len(cards)} resultados encontrados")
        raw_leads.extend(cards)

    except Exception as exc:
        logger.error(f"[{source}] Error general: {exc}")

    return _save_leads(page, session, raw_leads, source, keyword)


# ─────────────────────────────────────────────
# Punto de entrada
# ─────────────────────────────────────────────

def run_all_scrapers():
    logger.info("=" * 60)
    logger.info(f"INICIANDO SCRAPER — keyword: '{KEYWORD}' | ubicación: '{LOCATION or 'toda España'}' | páginas: {MAX_PAGES}")
    logger.info("=" * 60)

    session = init_db(DATABASE_URL)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/119.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()

        page.route(
            "**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ttf,eot,ico}",
            lambda route: route.abort()
        )

        total = 0
        try:
            logger.info("\n>>> FUENTE 1: Páginas Amarillas")
            count = scrape_paginas_amarillas(page, session, KEYWORD, MAX_PAGES, LOCATION)
            logger.info(f"    Leads guardados: {count}")
            total += count

            random_delay(3.0, 5.0)

            logger.info("\n>>> FUENTE 2: Kompass")
            count = scrape_kompass(page, session, KEYWORD, MAX_PAGES, LOCATION)
            logger.info(f"    Leads guardados: {count}")
            total += count

            random_delay(3.0, 5.0)

            logger.info("\n>>> FUENTE 3: Bing Maps")
            count = scrape_bing_maps(page, session, KEYWORD, MAX_PAGES, LOCATION)
            logger.info(f"    Leads guardados: {count}")
            total += count

        finally:
            browser.close()

    logger.info("=" * 60)
    logger.info(f"PROCESO COMPLETO — Total leads nuevos: {total}")
    logger.info("=" * 60)

    export_to_csv(session, str(PROJECT_ROOT / "data" / "leads.csv"), logger)
    session.close()


if __name__ == "__main__":
    run_all_scrapers()
