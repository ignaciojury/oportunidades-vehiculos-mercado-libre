# utils/scraper.py
# Standalone helpers for Mercado Libre autos/camionetas list scraping.
# IMPORTANT: This module does NOT import itself and can be safely imported by Streamlit apps.
#
# Exposes:
#   - build_base_url(...): str
#   - canonicalize_ml_url(url: str, proxy: str|None) -> tuple[str, dict]
#   - scrape_list(base_url: str, max_items: int = 240, max_pages: int = 5, proxy_url: str|None = None, delay_s: float = 0.8)
#       -> tuple[list[dict], list[dict]]
#
# Notes:
# - This is a best-effort HTML scraper. Mercado Libre cambia a menudo el marcado.
# - El parser intenta varios selectores comunes y degrada con gracia.
# - No requiere Streamlit, para permitir uso desde scripts o tests.
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit, urlencode, parse_qsl, urljoin
import re
import time
import math
import logging

import requests
from bs4 import BeautifulSoup

__all__ = ["build_base_url", "canonicalize_ml_url", "scrape_list"]

_LOG = logging.getLogger(__name__)
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

@dataclass
class PageLog:
    url: str
    status: int
    items_found: int
    verification: bool = False
    error: Optional[str] = None

def _clean_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def _canonical(u: str) -> str:
    if not u:
        return ""
    p = urlsplit(u)
    # drop query + fragment to avoid duplicates by tracking params
    return urlunsplit((p.scheme, p.netloc, p.path, "", ""))

def build_base_url(
    dueno_directo: bool = True,
    year_min: Optional[int] = None,
    year_max: Optional[int] = None,
    price_min_ars: Optional[int] = None,
    price_max_ars: Optional[int] = None,
    km_min: Optional[int] = None,
    km_max: Optional[int] = None,
    transmissions: Optional[List[str]] = None,
) -> str:
    """
    Construye una URL base de búsqueda para MercadoLibre Autos AR.
    La estructura exacta de filtros de ML cambia con el tiempo; esto arma una URL razonable.
    """
    base = "https://autos.mercadolibre.com.ar/"
    path_bits = []

    if dueno_directo:
        path_bits.append("dueno-directo")
    # Filtros de transmisión pueden ir en ruta (si solo hay 1)
    tx_map = {"Automática": "automatica", "Manual": "manual", "CVT": "cvt"}
    if transmissions:
        tx = [t for t in transmissions if t in tx_map]
        if len(tx) == 1:
            path_bits.append(tx_map[tx[0]])

    # Rango de años (cuando ML usa formato de ruta)
    # Ejemplos: anio-2016-2016, anio-2018-2020, etc.
    if year_min or year_max:
        a = year_min or year_max
        b = year_max or year_min
        if a and b:
            path_bits.append(f"anio-{a}-{b}")
        elif a:
            path_bits.append(f"anio-{a}-{a}")

    path = "/".join(path_bits)
    if path:
        base = urljoin(base, path + "/")

    # Parámetros (precio, km)
    q: Dict[str, Any] = {}
    if price_min_ars is not None:
        q["price_from"] = str(int(price_min_ars))
    if price_max_ars is not None:
        q["price_to"] = str(int(price_max_ars))
    if km_min is not None:
        q["km_from"] = str(int(km_min))
    if km_max is not None:
        q["km_to"] = str(int(km_max))

    query = f"?{urlencode(q)}" if q else ""
    return base + query

def canonicalize_ml_url(url: str, proxy: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
    """
    Devuelve (url_canon, meta). No hace request; solo normaliza y marca verification=False por defecto.
    """
    return _canonical(url), {"verification": False}

def _requests_session(proxy_url: Optional[str]) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": _UA, "Accept-Language": "es-AR,es;q=0.9,en;q=0.8"})
    if proxy_url:
        s.proxies.update({"http": proxy_url, "https": proxy_url})
    s.timeout = 20
    return s

def _extract_price(text: str) -> Tuple[Optional[float], Optional[str]]:
    if not text:
        return None, None
    txt = text.replace(".", "").replace("\xa0", " ").replace("\u202f", " ")
    m_usd = re.search(r"U\$D\s*([\d\s]+)", txt, flags=re.I)
    if m_usd:
        val = re.sub(r"\s+", "", m_usd.group(1))
        try:
            return float(val), "USD"
        except ValueError:
            return None, "USD"
    m_ars = re.search(r"\$\s*([\d\s]+)", txt)
    if m_ars:
        val = re.sub(r"\s+", "", m_ars.group(1))
        try:
            return float(val), "ARS"
        except ValueError:
            return None, "ARS"
    # Fallback: números sueltos
    m = re.search(r"([\d\.]{5,})", txt)
    if m:
        val = m.group(1).replace(".", "")
        try:
            return float(val), None
        except ValueError:
            return None, None
    return None, None

def _parse_card(card) -> Optional[Dict[str, Any]]:
    # Busca enlace principal
    a = card.select_one("a.ui-search-result__content, a.ui-search-link, a")
    href = a.get("href") if a else None
    if not href:
        return None
    title_el = card.select_one("h2.ui-search-item__title, h1.ui-search-item__title, .ui-search-item__title")
    title = _clean_spaces(title_el.get_text()) if title_el else None

    # Precio
    price_el = card.select_one(".ui-search-price__second-line, .ui-search-price, .ui-search-item__group__element--price")
    price_txt = _clean_spaces(price_el.get_text()) if price_el else ""
    price, currency = _extract_price(price_txt)

    # Ubicación (provincia/ciudad)
    loc_el = card.select_one(".ui-search-item__group__element.ui-search-item__location, .ui-search-item__group__element--location")
    location = _clean_spaces(loc_el.get_text()) if loc_el else ""
    state, city = None, None
    if "," in location:
        city, state = [s.strip() for s in location.split(",", 1)]
    else:
        state = location or None

    # Specs (km, caja)
    spec_txt = " ".join([_clean_spaces(e.get_text()) for e in card.select(".ui-search-card-attributes__attribute")])
    km = None
    m_km = re.search(r"(\d[\d\.]{3,})\s*km", spec_txt, flags=re.I)
    if m_km:
        try:
            km = int(m_km.group(1).replace(".", ""))
        except Exception:
            km = None
    gearbox = None
    for g in ("Automática", "Manual", "CVT"):
        if re.search(g, spec_txt, flags=re.I):
            gearbox = g
            break

    # Año (si aparece)
    year = None
    m_year = re.search(r"\b(20\d{2}|19\d{2})\b", spec_txt)
    if m_year:
        year = int(m_year.group(1))

    return {
        "title": title,
        "permalink": href,
        "price": price,
        "currency": currency,
        "state": state,
        "city": city,
        "km": km,
        "gearbox": gearbox,
        "year": year,
    }

def _detect_verification(html: str) -> bool:
    if not html:
        return False
    # Patrones típicos de ML para human check / Robot verification
    return ("captcha" in html.lower()) or ("verificaci" in html.lower() and "robot" in html.lower())

def _page_urls(base_url: str, page_index: int, items_per_page: int = 48) -> List[str]:
    """
    Devuelve variantes de URL para la misma página (ML usa diferentes formatos). Intentamos varias.
    """
    urls = []
    # Variante "?page=N"
    if page_index > 1:
        urls.append(f"{base_url}&page={page_index}" if "?" in base_url else f"{base_url}?page={page_index}")
    else:
        urls.append(base_url)

    # Variante "_Desde_{offset}" (49, 97, ...)
    if page_index > 1:
        offset = (page_index - 1) * items_per_page + 1
        # insertamos antes del query si lo hay
        split = urlsplit(base_url)
        path = split.path.rstrip("/") + f"_Desde_{offset}"
        urls.append(urlunsplit((split.scheme, split.netloc, path, split.query, split.fragment)))
    return urls

def scrape_list(
    base_url: str,
    max_items: int = 240,
    max_pages: int = 5,
    proxy_url: Optional[str] = None,
    delay_s: float = 0.8,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Scrapea listados desde base_url, paginando hasta max_pages o max_items.
    Devuelve (rows, logs). Cada row tiene: title, permalink, price, currency, state, city, km, gearbox, year.
    """
    session = _requests_session(proxy_url)
    rows: List[Dict[str, Any]] = []
    logs: List[Dict[str, Any]] = []
    seen_links = set()

    items_per_page_guess = 48  # ML suele mostrar 48 por página, pero puede variar.
    pages = int(max_pages)

    for i in range(1, pages + 1):
        tried_urls = _page_urls(base_url, i, items_per_page_guess)
        page_ok = False
        last_err = None

        for url in tried_urls:
            try:
                resp = session.get(url, allow_redirects=True, timeout=25)
                html = resp.text or ""
                verif = _detect_verification(html)
                if verif:
                    logs.append(asdict(PageLog(url=url, status=resp.status_code, items_found=0, verification=True)))
                    page_ok = True  # no seguimos intentando variantes; requiere intervención (proxy, menor ritmo)
                    break

                if resp.status_code != 200:
                    last_err = f"HTTP {resp.status_code}"
                    continue

                soup = BeautifulSoup(html, "lxml")
                # Cards posibles
                cards = soup.select("li.ui-search-layout__item, li.ui-search-result, .ui-search-layout__item")
                if not cards:
                    # fallback más laxo
                    cards = soup.select("li")
                count_added = 0
                for c in cards:
                    row = _parse_card(c)
                    if not row:
                        continue
                    key = _canonical(row.get("permalink", ""))
                    if not key or key in seen_links:
                        continue
                    seen_links.add(key)
                    rows.append(row)
                    count_added += 1
                    if len(rows) >= max_items:
                        break
                logs.append(asdict(PageLog(url=url, status=resp.status_code, items_found=count_added)))
                page_ok = True
                break
            except Exception as e:
                last_err = str(e)
                continue

        if not page_ok:
            logs.append(asdict(PageLog(url=tried_urls[-1], status=0, items_found=0, error=last_err or "fetch_failed")))

        if len(rows) >= max_items or (logs and logs[-1].get("verification")):
            break
        if delay_s and i < pages:
            time.sleep(delay_s)

    return rows, logs
