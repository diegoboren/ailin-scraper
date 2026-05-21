"""
Google Maps Scraper — Ailin
Busca locales de ropa infantil en Argentina y extrae nombre, teléfono, dirección, rating.
Salida: CSV listo para importar al CRM.

Uso:
    python3 scraper.py
    python3 scraper.py --queries "ropa niños Rosario" "ropa infantil Córdoba"
    python3 scraper.py --max 60 --output resultados.csv
"""

import asyncio
import csv
import re
import argparse
from datetime import datetime
from urllib.parse import quote
from playwright.async_api import async_playwright

# ─── Configuración por defecto ────────────────────────────────────────────────

DEFAULT_QUERIES = [
    "ropa niños Buenos Aires",
    "ropa infantil Buenos Aires",
    "local ropa chicos CABA",
    "ropa niños Rosario",
    "ropa infantil Rosario",
    "ropa niños Córdoba",
    "ropa infantil Córdoba",
    "ropa niños Mendoza",
    "ropa infantil Mendoza",
    "ropa niños La Plata",
    "ropa infantil Mar del Plata",
    "ropa niños Tucumán",
    "ropa niños Salta",
]

MAX_PER_QUERY = 60   # resultados por búsqueda
SCROLL_PAUSES = 10   # scrolls para cargar más resultados


# ─── Helpers ──────────────────────────────────────────────────────────────────

PHONE_RE = re.compile(r'(\+?54[\s\-]?9?[\s\-]?|\b0)(\d[\d\s\-]{6,14}\d)')

def extract_phone(text: str) -> str:
    """Extrae el primer teléfono encontrado en un texto."""
    # Patrón específico para el formato de Google Maps: "· 011 4794-6219"
    m = re.search(r'·\s*((?:\+54|0)\d[\d\s\-]{5,14}\d)', text)
    if m:
        return m.group(1).strip()
    # Fallback genérico
    m = PHONE_RE.search(text)
    if m:
        return (m.group(1) + m.group(2)).strip()
    return ""


def normalize_phone(raw: str) -> str:
    """
    Normaliza a formato 549XXXXXXXXXX (WhatsApp Argentina).
    Ej: 011 4794-6219 → 541147946219
        +54 9 11 5555-1234 → 549115555 1234
    """
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""

    # Ya empieza con 549 o 54 (internacional)
    if digits.startswith("549") and len(digits) >= 13:
        return digits
    if digits.startswith("54") and len(digits) >= 12:
        # Agregar 9 para celulares si no está
        rest = digits[2:]
        if not rest.startswith("9"):
            return "549" + rest
        return "54" + rest

    # Empieza con 0 (ej: 011 4794-6219)
    if digits.startswith("0"):
        digits = digits[1:]  # sacar el 0 inicial
    # Empieza con 9 (celular sin prefijo país)
    if digits.startswith("9") and len(digits) >= 10:
        return "54" + digits
    return "549" + digits


def extract_city(query: str) -> str:
    cities = ["Buenos Aires", "CABA", "Rosario", "Córdoba", "Mendoza",
              "La Plata", "Mar del Plata", "Tucumán", "Salta"]
    for c in cities:
        if c.lower() in query.lower():
            return c if c != "CABA" else "Buenos Aires"
    return ""


# ─── Scraper ──────────────────────────────────────────────────────────────────

async def scrape_query(page, query: str, max_results: int) -> list[dict]:
    url = f"https://www.google.com/maps/search/{quote(query)}"
    print(f"\n🔍  {query}")

    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_timeout(3000)

    city = extract_city(query)
    results = []
    seen_names = set()

    feed = page.locator('div[role="feed"]')
    try:
        await feed.wait_for(timeout=8000)
    except Exception:
        print("    ⚠️  No se encontró el panel de resultados")
        return results

    for scroll_i in range(SCROLL_PAUSES):
        await feed.evaluate("el => el.scrollBy(0, 1800)")
        await page.wait_for_timeout(1500)
        end = await page.locator('span:has-text("Has llegado al final")').count()
        if end > 0:
            break

    # Extraer cada card de resultado
    cards = await page.locator('div[role="feed"] > div').all()

    for card in cards:
        if len(results) >= max_results:
            break
        try:
            text = (await card.inner_text()).strip()
            if not text or len(text) < 10:
                continue

            # Nombre: primera línea no vacía
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            if not lines:
                continue
            name = lines[0]

            # Filtros de ruido
            NOISE = {"Resultados", "ClasificaciónHorasTodos los filtros", "Compartir",
                     "Clasificación", "Horas", "Todos los filtros"}
            if name in NOISE or any(n in name for n in NOISE):
                continue
            if len(name) < 3 or name.startswith("·"):
                continue
            if name in seen_names:
                continue

            # Link de Maps
            link_el = card.locator('a[href*="/maps/place/"]').first
            maps_url = ""
            try:
                maps_url = await link_el.get_attribute("href") or ""
            except Exception:
                pass

            # Teléfono
            phone_raw = extract_phone(text)
            phone_crm = normalize_phone(phone_raw)

            # Dirección: buscar la línea "Tipo de negocio · Dirección"
            address = ""
            for line in lines[1:]:
                if re.match(r"^\d+\.\d", line):  # rating
                    continue
                if any(x in line.lower() for x in ["abierto", "cerrado", "cierra", "vuelve", "precio"]):
                    continue
                # Formato Google Maps: "Tienda de ropa · Calle 123"
                if "·" in line:
                    parts = line.split("·")
                    if len(parts) >= 2:
                        addr_candidate = parts[-1].strip()
                        if len(addr_candidate) > 5:
                            address = addr_candidate
                            break

            # Rating
            rating = ""
            for line in lines:
                if re.match(r"^\d\.\d$", line):
                    rating = line
                    break

            seen_names.add(name)
            results.append({
                "nombre":       name,
                "telefono":     phone_raw,
                "telefono_crm": phone_crm,
                "ciudad":       city,
                "direccion":    address,
                "rating":       rating,
                "maps_url":     maps_url,
                "query":        query,
            })

        except Exception:
            continue

    print(f"    ✅  {len(results)} resultados ({sum(1 for r in results if r['telefono'])} con teléfono)")
    return results


# ─── Main ─────────────────────────────────────────────────────────────────────

async def main(queries: list[str], max_results: int, output: str):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = output or f"leads_ailin_{timestamp}.csv"

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

        for query in queries:
            rows = await scrape_query(page, query, max_results)
            for row in rows:
                key = row["telefono_crm"] or (row["nombre"] + row["ciudad"])
                if key in seen_phones:
                    continue
                seen_phones.add(key)
                all_results.append(row)

        await browser.close()

    if not all_results:
        print("\n❌  No se encontraron resultados.")
        return

    fieldnames = ["nombre", "telefono", "telefono_crm", "ciudad", "direccion", "rating", "maps_url", "query"]
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_results)

    with_phone = sum(1 for r in all_results if r["telefono"])
    print(f"\n{'─'*50}")
    print(f"✅  {len(all_results)} lugares únicos → '{output_file}'")
    print(f"   📞  Con teléfono: {with_phone}  |  Sin teléfono: {len(all_results) - with_phone}")


def parse_args():
    parser = argparse.ArgumentParser(description="Google Maps Scraper para Ailin")
    parser.add_argument("--queries", nargs="+",
                        help='Búsquedas. Ej: "ropa niños Rosario" "ropa infantil Mendoza"')
    parser.add_argument("--max", type=int, default=MAX_PER_QUERY,
                        help=f"Máximo de resultados por búsqueda (default: {MAX_PER_QUERY})")
    parser.add_argument("--output", type=str, default="",
                        help="Nombre del archivo CSV (default: leads_ailin_FECHA.csv)")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(main(args.queries or DEFAULT_QUERIES, args.max, args.output))
