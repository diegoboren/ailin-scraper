"""
Ailin Scraper — Interfaz visual
Correr con: streamlit run app.py
"""

import asyncio
import csv
import io
import re
import threading
import queue
import time
from datetime import datetime
from urllib.parse import quote

import streamlit as st
import pandas as pd

# ─── Config página ────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Ailin — Buscador de Leads",
    page_icon="👗",
    layout="centered",
)

# ─── Estilos ──────────────────────────────────────────────────────────────────

st.markdown("""
<style>
    /* Fondo crema */
    .stApp { background: #F3EAD7; }

    /* Header principal */
    .ailin-header {
        background: #1C1407;
        border-radius: 16px;
        padding: 20px 24px;
        margin-bottom: 24px;
        display: flex;
        align-items: center;
        gap: 14px;
    }
    .ailin-logo {
        background: #C99A2E;
        width: 44px; height: 44px;
        border-radius: 12px;
        display: flex; align-items: center; justify-content: center;
        font-size: 22px; font-weight: 900; color: #1C1407;
        flex-shrink: 0;
    }
    .ailin-title { color: white; font-size: 20px; font-weight: 800; margin: 0; }
    .ailin-sub   { color: rgba(255,255,255,.45); font-size: 13px; margin: 2px 0 0; }

    /* Cards de stats */
    .stat-card {
        background: white;
        border-radius: 14px;
        padding: 16px;
        text-align: center;
        box-shadow: 0 1px 4px rgba(44,26,7,.08);
    }
    .stat-n   { font-size: 32px; font-weight: 900; }
    .stat-lbl { font-size: 12px; color: #7C6B55; font-weight: 600; text-transform: uppercase; letter-spacing: .5px; margin-top: 4px; }

    /* Botón principal */
    .stButton > button {
        background: #1C1407 !important;
        color: white !important;
        border: none !important;
        border-radius: 12px !important;
        font-weight: 700 !important;
        font-size: 16px !important;
        padding: 14px !important;
        width: 100% !important;
    }
    .stButton > button:hover { opacity: .85 !important; }
    .stButton > button:disabled { opacity: .4 !important; }

    /* Chips de ciudad */
    .city-chip {
        display: inline-block;
        background: #E8DEC9;
        border-radius: 20px;
        padding: 4px 12px;
        font-size: 13px;
        font-weight: 600;
        color: #2C1A07;
        margin: 3px;
    }

    /* Ocultar el menú de Streamlit */
    #MainMenu, footer, header { visibility: hidden; }
</style>
""", unsafe_allow_html=True)

# ─── Header ───────────────────────────────────────────────────────────────────

st.markdown("""
<div class="ailin-header">
    <div class="ailin-logo">A</div>
    <div>
        <div class="ailin-title">Ailin — Buscador de Leads</div>
        <div class="ailin-sub">Locales de ropa infantil en Google Maps</div>
    </div>
</div>
""", unsafe_allow_html=True)

# ─── Imports del scraper ──────────────────────────────────────────────────────

from scraper import scrape_query, normalize_phone, extract_phone, extract_city

# ─── Estado de sesión ─────────────────────────────────────────────────────────

if "results" not in st.session_state:
    st.session_state.results = []
if "running" not in st.session_state:
    st.session_state.running = False
if "log" not in st.session_state:
    st.session_state.log = []

# ─── Panel de configuración ───────────────────────────────────────────────────

st.markdown("### ⚙️ Configuración de búsqueda")

CIUDADES = {
    "Buenos Aires": ["ropa niños Buenos Aires", "ropa infantil Buenos Aires", "local ropa chicos CABA"],
    "Rosario":      ["ropa niños Rosario", "ropa infantil Rosario"],
    "Córdoba":      ["ropa niños Córdoba", "ropa infantil Córdoba"],
    "Mendoza":      ["ropa niños Mendoza", "ropa infantil Mendoza"],
    "La Plata":     ["ropa niños La Plata"],
    "Mar del Plata":["ropa infantil Mar del Plata"],
    "Tucumán":      ["ropa niños Tucumán"],
    "Salta":        ["ropa niños Salta"],
}

ciudades_sel = st.multiselect(
    "Ciudades a buscar",
    options=list(CIUDADES.keys()),
    default=["Buenos Aires", "Rosario"],
    help="Podés seleccionar varias ciudades"
)

col1, col2 = st.columns(2)
with col1:
    max_por_query = st.slider("Máx. resultados por búsqueda", 20, 100, 50, 10)
with col2:
    solo_con_telefono = st.checkbox("Solo con teléfono", value=True)

# Query personalizada
with st.expander("➕ Agregar búsqueda personalizada"):
    query_custom = st.text_input("Búsqueda", placeholder='Ej: "ropa invierno niños Neuquén"')

# Armar lista de queries
queries = []
for ciudad in ciudades_sel:
    queries.extend(CIUDADES[ciudad])
if query_custom.strip():
    queries.append(query_custom.strip())

if queries:
    st.markdown(
        "**Búsquedas que se van a ejecutar:** " +
        " ".join(f'<span class="city-chip">{q}</span>' for q in queries),
        unsafe_allow_html=True
    )

# ─── Botón de ejecución ───────────────────────────────────────────────────────

st.markdown("---")

run_btn = st.button(
    "🔍 Buscar leads",
    disabled=st.session_state.running or not queries,
    use_container_width=True
)

# ─── Lógica de scraping ───────────────────────────────────────────────────────

async def run_scraper(queries, max_results, progress_q):
    from playwright.async_api import async_playwright

    all_results = []
    seen_phones = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        context = await browser.new_context(
            locale="es-AR",
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()

        for i, query in enumerate(queries):
            progress_q.put(("progress", i, len(queries), query))
            rows = await scrape_query(page, query, max_results)
            for row in rows:
                key = row["telefono_crm"] or (row["nombre"] + row["ciudad"])
                if key in seen_phones:
                    continue
                seen_phones.add(key)
                all_results.append(row)
            progress_q.put(("partial", list(all_results)))

        await browser.close()

    progress_q.put(("done", list(all_results)))


def run_in_thread(queries, max_results, progress_q):
    asyncio.run(run_scraper(queries, max_results, progress_q))


if run_btn:
    st.session_state.running = True
    st.session_state.results = []
    st.session_state.log = []

    progress_q = queue.Queue()
    t = threading.Thread(
        target=run_in_thread,
        args=(queries, max_por_query, progress_q),
        daemon=True
    )
    t.start()

    # UI de progreso
    status_txt = st.empty()
    progress_bar = st.progress(0)
    results_placeholder = st.empty()

    done = False
    while not done:
        try:
            msg = progress_q.get(timeout=0.5)
            kind = msg[0]

            if kind == "progress":
                _, i, total, query = msg
                pct = int((i / total) * 100)
                progress_bar.progress(pct)
                status_txt.markdown(f"🔍 **Buscando:** {query}  ({i+1}/{total})")

            elif kind == "partial":
                partial = msg[1]
                if solo_con_telefono:
                    partial = [r for r in partial if r["telefono"]]
                if partial:
                    df = pd.DataFrame(partial)
                    results_placeholder.dataframe(
                        df[["nombre", "telefono", "ciudad", "rating", "direccion"]],
                        use_container_width=True,
                        hide_index=True
                    )

            elif kind == "done":
                done = True
                st.session_state.results = msg[1]
                st.session_state.running = False
                progress_bar.progress(100)
                status_txt.markdown("✅ **¡Listo!**")

        except queue.Empty:
            time.sleep(0.1)

    st.rerun()

# ─── Resultados ───────────────────────────────────────────────────────────────

if st.session_state.results:
    results = st.session_state.results
    if solo_con_telefono:
        results = [r for r in results if r["telefono"]]

    total = len(results)
    con_tel = sum(1 for r in results if r["telefono"])
    ciudades_unicas = len(set(r["ciudad"] for r in results))

    st.markdown("### 📊 Resultados")

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(f'<div class="stat-card"><div class="stat-n" style="color:#1C1407">{total}</div><div class="stat-lbl">Leads encontrados</div></div>', unsafe_allow_html=True)
    with c2:
        st.markdown(f'<div class="stat-card"><div class="stat-n" style="color:#16A34A">{con_tel}</div><div class="stat-lbl">Con teléfono</div></div>', unsafe_allow_html=True)
    with c3:
        st.markdown(f'<div class="stat-card"><div class="stat-n" style="color:#C99A2E">{ciudades_unicas}</div><div class="stat-lbl">Ciudades</div></div>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    df = pd.DataFrame(results)

    # Filtro rápido
    col_f1, col_f2 = st.columns([2, 1])
    with col_f1:
        busqueda = st.text_input("🔎 Filtrar resultados", placeholder="Nombre o ciudad...")
    with col_f2:
        ciudad_filtro = st.selectbox("Ciudad", ["Todas"] + sorted(set(r["ciudad"] for r in results)))

    if busqueda:
        mask = (
            df["nombre"].str.contains(busqueda, case=False, na=False) |
            df["telefono"].str.contains(busqueda, case=False, na=False) |
            df["direccion"].str.contains(busqueda, case=False, na=False)
        )
        df = df[mask]
    if ciudad_filtro != "Todas":
        df = df[df["ciudad"] == ciudad_filtro]

    st.dataframe(
        df[["nombre", "telefono", "ciudad", "rating", "direccion"]].rename(columns={
            "nombre": "Nombre",
            "telefono": "Teléfono",
            "ciudad": "Ciudad",
            "rating": "⭐",
            "direccion": "Dirección",
        }),
        use_container_width=True,
        hide_index=True,
        height=420,
    )

    st.markdown("---")
    st.markdown("### 💾 Descargar")

    col_d1, col_d2 = st.columns(2)

    # CSV completo
    csv_buf = io.StringIO()
    writer = csv.DictWriter(csv_buf, fieldnames=["nombre", "telefono", "telefono_crm", "ciudad", "direccion", "rating", "maps_url", "query"])
    writer.writeheader()
    writer.writerows(results)

    with col_d1:
        st.download_button(
            label="📥 Descargar CSV completo",
            data=csv_buf.getvalue().encode("utf-8"),
            file_name=f"leads_ailin_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
            use_container_width=True,
        )

    # CSV solo para CRM (nombre + telefono_crm)
    crm_rows = [{"nombre": r["nombre"], "telefono": r["telefono_crm"], "ciudad": r["ciudad"]} for r in results if r["telefono_crm"]]
    crm_buf = io.StringIO()
    crm_writer = csv.DictWriter(crm_buf, fieldnames=["nombre", "telefono", "ciudad"])
    crm_writer.writeheader()
    crm_writer.writerows(crm_rows)

    with col_d2:
        st.download_button(
            label="📱 Descargar para CRM (WhatsApp)",
            data=crm_buf.getvalue().encode("utf-8"),
            file_name=f"leads_crm_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
            use_container_width=True,
        )

    st.caption(f"CSV completo: todos los campos  |  CSV CRM: nombre + teléfono en formato WhatsApp (549XXXXXXXXXX)")
