import streamlit as st
import pandas as pd
import os
import sys
import subprocess
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine
from jinja2 import Environment, FileSystemLoader

# Configuración de rutas
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.models import init_db, Lead
from src.mailer import send_email

# Cargar variables de entorno
load_dotenv(PROJECT_ROOT / ".env")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///data/leads.db")
TEMPLATE_PATH = PROJECT_ROOT / "templates" / "email_comercial.html"

# ─────────────────────────────────────────────
# Sectores y ubicaciones de Páginas Amarillas
# ─────────────────────────────────────────────

SECTORES = [
    "instalaciones solares",
    "fontanería",
    "electricistas",
    "climatización y aire acondicionado",
    "reformas y construcción",
    "cerrajería",
    "pintura y decoración",
    "jardinería y paisajismo",
    "mudanzas",
    "carpintería",
    "albañilería",
    "impermeabilizaciones",
    "piscinas",
    "alarmas y seguridad",
    "telecomunicaciones",
    "informática y reparación de ordenadores",
    "asesorías y gestorías",
    "abogados",
    "clínicas dentales",
    "clínicas veterinarias",
    "fisioterapia",
    "gimnasios",
    "academias",
    "restaurantes",
    "hoteles",
    "inmobiliarias",
    "talleres mecánicos",
    "autoescuelas",
    "transporte y logística",
    "supermercados y alimentación",
]

PROVINCIAS = {
    "Toda España": "",
    "Álava": "alava",
    "Albacete": "albacete",
    "Alicante": "alicante",
    "Almería": "almeria",
    "Asturias": "asturias",
    "Ávila": "avila",
    "Badajoz": "badajoz",
    "Barcelona": "barcelona",
    "Burgos": "burgos",
    "Cáceres": "caceres",
    "Cádiz": "cadiz",
    "Cantabria": "cantabria",
    "Castellón": "castellon",
    "Ciudad Real": "ciudad-real",
    "Córdoba": "cordoba",
    "Cuenca": "cuenca",
    "Girona": "girona",
    "Granada": "granada",
    "Guadalajara": "guadalajara",
    "Guipúzcoa": "guipuzcoa",
    "Huelva": "huelva",
    "Huesca": "huesca",
    "Islas Baleares": "islas-baleares",
    "Jaén": "jaen",
    "La Coruña": "la-coruna",
    "La Rioja": "la-rioja",
    "Las Palmas": "las-palmas",
    "León": "leon",
    "Lérida": "lerida",
    "Lugo": "lugo",
    "Madrid": "madrid",
    "Málaga": "malaga",
    "Murcia": "murcia",
    "Navarra": "navarra",
    "Orense": "orense",
    "Palencia": "palencia",
    "Pontevedra": "pontevedra",
    "Salamanca": "salamanca",
    "Santa Cruz de Tenerife": "santa-cruz-de-tenerife",
    "Segovia": "segovia",
    "Sevilla": "sevilla",
    "Soria": "soria",
    "Tarragona": "tarragona",
    "Teruel": "teruel",
    "Toledo": "toledo",
    "Valencia": "valencia",
    "Valladolid": "valladolid",
    "Vizcaya": "vizcaya",
    "Zamora": "zamora",
    "Zaragoza": "zaragoza",
    "Ceuta": "ceuta",
    "Melilla": "melilla",
}

# ─────────────────────────────────────────────
# Configuración de la página
# ─────────────────────────────────────────────

st.set_page_config(page_title="AI Client Scrapper - Panel de Control", layout="wide", page_icon="🚀")

st.markdown("""
    <style>
    .main { background-color: #f8f9fa; }
    .stButton>button { width: 100%; border-radius: 5px; height: 3em; background-color: #2c3e50; color: white; }
    .stButton>button:hover { background-color: #34495e; color: white; border: 1px solid #2c3e50; }
    .success-text { color: #27ae60; font-weight: bold; }
    .priority-tag { background-color: #e74c3c; color: white; padding: 2px 8px; border-radius: 10px; font-size: 0.8em; }
    </style>
    """, unsafe_allow_html=True)

# Inicializar sesión de base de datos
@st.cache_resource
def get_session():
    return init_db(DATABASE_URL)

session = get_session()

def get_leads_df():
    try:
        engine = create_engine(DATABASE_URL)
        return pd.read_sql("SELECT * FROM leads ORDER BY fecha_scraping DESC", engine)
    except Exception:
        return pd.DataFrame()

# ─────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────

st.sidebar.title("⚙️ Configuración")

# Sector — desplegable con opción de texto libre
sector_opciones = ["✏️ Escribir manualmente..."] + sorted(SECTORES)
sector_sel = st.sidebar.selectbox("Sector / Actividad", sector_opciones)
if sector_sel == "✏️ Escribir manualmente...":
    keyword = st.sidebar.text_input("Escribe la palabra clave", value="")
else:
    keyword = sector_sel

# Provincia — desplegable
provincia_nombre = st.sidebar.selectbox("Provincia", list(PROVINCIAS.keys()))
location = PROVINCIAS[provincia_nombre]

max_pages = st.sidebar.slider("Páginas por fuente", 1, 10, 2)
st.sidebar.divider()

if st.sidebar.button("🔍 Lanzar Scraper"):
    if not keyword:
        st.sidebar.error("Escribe o selecciona un sector antes de buscar.")
    else:
        st.sidebar.info("Iniciando búsqueda... Revisa los logs para el progreso.")
        with st.spinner(f'Buscando "{keyword}" en {provincia_nombre}...'):
            subprocess.run(
                [sys.executable, str(PROJECT_ROOT / "src" / "scraper.py")],
                env={
                    **os.environ,
                    "SEARCH_KEYWORD": keyword,
                    "SEARCH_LOCATION": location,
                    "MAX_PAGES": str(max_pages),
                }
            )
            enrich_path = PROJECT_ROOT / "src" / "enrich_leads.py"
            if enrich_path.exists():
                subprocess.run([sys.executable, str(enrich_path)])

        st.sidebar.success("Búsqueda completada.")
        st.rerun()

# ─────────────────────────────────────────────
# Dashboard Principal
# ─────────────────────────────────────────────

st.title("🚀 AI Client Scrapper")
st.subheader("Gestión Inteligente de Leads y Subvenciones")

df = get_leads_df()

col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Total Leads", len(df))
with col2:
    count_sub = len(df[df['total_subvenciones'] > 0]) if 'total_subvenciones' in df.columns else 0
    st.metric("Con Subvenciones", count_sub)
with col3:
    count_pri = len(df[df['es_prioritario'] == True]) if 'es_prioritario' in df.columns else 0
    st.metric("Prioritarios", count_pri)
with col4:
    count_con = len(df[df['contactado'] == True]) if 'contactado' in df.columns else 0
    st.metric("Contactados", count_con)

st.divider()

# ─────────────────────────────────────────────
# Tabs
# ─────────────────────────────────────────────

tab1, tab2 = st.tabs(["📋 Listado de Leads", "📧 Editor de Plantilla"])

with tab1:
    st.write("### Empresas Identificadas")

    if df.empty:
        st.info("No hay leads todavía. Lanza el scraper desde el panel lateral.")
    else:
        f_col1, f_col2 = st.columns(2)
        with f_col1:
            search_term = st.text_input("Filtrar por nombre o NIF")
        with f_col2:
            only_priority = st.checkbox("Mostrar solo prioritarios")

        filtered_df = df.copy()
        if search_term:
            mask = filtered_df['nombre'].str.contains(search_term, case=False, na=False)
            if 'nif' in filtered_df.columns:
                mask = mask | filtered_df['nif'].str.contains(search_term, case=False, na=False)
            filtered_df = filtered_df[mask]
        if only_priority and 'es_prioritario' in filtered_df.columns:
            filtered_df = filtered_df[filtered_df['es_prioritario'] == True]

        base_cols     = ['id', 'nombre', 'nif', 'email', 'web', 'telefono', 'fuente', 'contactado']
        optional_cols = ['total_subvenciones', 'num_concesiones', 'es_prioritario']
        show_cols = base_cols + [c for c in optional_cols if c in filtered_df.columns]
        show_cols = [c for c in show_cols if c in filtered_df.columns]

        st.dataframe(filtered_df[show_cols], use_container_width=True, hide_index=True)

        st.write("---")
        st.write("### Acciones sobre Lead")
        lead_ids = filtered_df['id'].tolist() if not filtered_df.empty else []
        lead_id  = st.selectbox("Selecciona un Lead para contactar", lead_ids)

        if lead_id:
            selected_lead = session.query(Lead).filter(Lead.id == lead_id).first()
            if selected_lead:
                l_col1, l_col2 = st.columns([2, 1])

                with l_col1:
                    st.write(f"**Empresa:** {selected_lead.nombre}")
                    st.write(f"**Email:** {selected_lead.email or '⚠️ No disponible'}")
                    telefono = getattr(selected_lead, 'telefono', None)
                    st.write(f"**Teléfono:** {telefono or '⚠️ No disponible'}")
                    if hasattr(selected_lead, 'total_subvenciones') and selected_lead.total_subvenciones:
                        st.write(f"**Subvenciones:** {selected_lead.total_subvenciones:,.2f}€")

                with l_col2:
                    if not selected_lead.email:
                        st.warning("No se puede enviar email (falta dirección)")
                    elif selected_lead.contactado:
                        st.success("✅ Ya contactado")
                        if st.button("Re-enviar Email"):
                            st.info("Re-enviando...")
                    else:
                        if st.button("🚀 Enviar Propuesta Ahora"):
                            jinja_env    = Environment(loader=FileSystemLoader(str(PROJECT_ROOT / "templates")))
                            template     = jinja_env.get_template("email_comercial.html")
                            sub_val      = getattr(selected_lead, 'total_subvenciones', 0) or 0
                            html_content = template.render(
                                nombre_empresa=selected_lead.nombre,
                                total_subvenciones=f"{sub_val:,.2f}"
                            )
                            if send_email(selected_lead.email, f"Propuesta para {selected_lead.nombre}", html_content):
                                selected_lead.contactado = True
                                session.commit()
                                st.success(f"Email enviado a {selected_lead.email}")
                                st.rerun()
                            else:
                                st.error("Error al enviar el email. Revisa la configuración SMTP.")

                    st.write("---")
                    if st.button("🗑️ Eliminar este lead", type="secondary"):
                        session.delete(selected_lead)
                        session.commit()
                        st.success(f"Lead '{selected_lead.nombre}' eliminado.")
                        st.rerun()

with tab2:
    st.write("### Personalizar Plantilla de Email")
    if TEMPLATE_PATH.exists():
        with open(TEMPLATE_PATH, 'r', encoding='utf-8') as f:
            current_template = f.read()

        new_template = st.text_area("Código HTML de la plantilla", value=current_template, height=400)

        if st.button("💾 Guardar Cambios en Plantilla"):
            with open(TEMPLATE_PATH, 'w', encoding='utf-8') as f:
                f.write(new_template)
            st.success("Plantilla actualizada correctamente.")

        st.write("#### Previsualización (Empresa Ejemplo)")
        jinja_env = Environment(loader=FileSystemLoader(str(PROJECT_ROOT / "templates")))
        try:
            prev_template = jinja_env.from_string(new_template)
            preview_html  = prev_template.render(
                nombre_empresa="EMPRESA DE PRUEBA S.L.",
                total_subvenciones="150,000.00"
            )
            st.components.v1.html(preview_html, height=500, scrolling=True)
        except Exception as e:
            st.error(f"Error en la plantilla: {e}")
    else:
        st.error("No se encontró el archivo de plantilla.")