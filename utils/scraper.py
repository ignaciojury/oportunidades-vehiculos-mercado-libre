# utils/scraper.py
# Scraper y generador de URLs para Mercado Libre Autos (AR)
# -------------------------------------------------------
# Provee:
#   - build_base_url(...): arma una URL de búsqueda (semánticamente estable) en ML Autos
#   - scrape_list(...): descarga listados paginados y devuelve registros estandarizados
#
# NOTAS IMPORTANTES
# - Mercado Libre cambia el HTML con frecuencia y puede aplicar verificaciones anti-bot.
# - Este scraper es best-effort: usa selectores comunes y fallbacks por regex.
# - Para uso en producción, considerá agregar rotating proxies, headers variados y backoff.

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Iterator, List, Optional, Tuple
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

# ─────────────────────────────────────────
# Config
# ─────────────────────────────────────────
BASE_DOMAIN = "https://autos.mercadolibre.com.ar"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# ─────────────────────────────────────────
# Helpers internos
# ─────────────────────────────────────────

def _safe_float(x):
    try:
        return float(str(x).replace(".", "").replace(",", "."))
    except Exception:
        return None


def _norm_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _extract_price(block) -> Tuple[Optional[float], Optional[str]]:
    # Precios típicos en ML: andes-money-amount__fraction + andes-money-amount__currency-symbol
    frac = block.select_one(".andes-money-amount__fraction")
    cur = block.select_one(".andes-money-amount__currency-symbol")
    if frac:
        price = _safe_float(frac.get_text("", strip=True))
        curr = (cur.get_text("", strip=True) if cur else None)
        # Normalización de currency
        if curr:
            curr = "USD" if "U$S" in curr or "US$" in curr or "USD" in curr.upper() else "ARS"
        return price, curr
    # Fallback por regex
    m = re.search(r"(U?\$\s?)([\d\.,]+)", block.get_text(" "))
    if m:
        price = _safe_float(m.group(2))
        curr = "USD" if "U$" in m.group(1) else "ARS"
        return price, curr
    return None, None


def _extract_km(text: str) -> Optional[int]:
    m = re.search(r"(\d{1,3}(?:[\.\,]\d{3})*)(?:\s*km|\s*kms?)", text, re.I)
    if m:
        val = _safe_float(m.group(1))
        return int(val) if val is not None else None
    return None


def _extract_year(text: str) -> Optional[int]:
    m = re.search(r"\b(19\d{2}|20\d{2})\b", text)
    if m:
        y = int(m.group(1))
        if 1960 <= y <= 2035:
            return y
    return None


# ─────────────────────────────────────────
# URL builder
# ─────────────────────────────────────────

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
    """Construye una URL de búsqueda en Mercado Libre Autos.

    Dado que ML usa rutas con filtros (y cambian), construimos una URL base estable
    que prioriza *dueno-directo* y luego pasamos hint de filtros por querystring
    (los ignora si no corresponden, pero mantiene reproducibilidad y logging).
    """
    path_parts = []
    if dueno_directo:
        path_parts.append("dueno-directo")

    # Transmisión: sólo si hay exactamente una opción, agregamos una pista en el path
    tx_map = {"automática": "automatica", "manual": "manual", "cvt": "cvt"}
    if transmissions and len(transmissions) == 1:
        tx = transmissions[0].lower()
        if tx in tx_map:
            path_parts.append(tx_map[tx])

    path = "/".join([p for p in path_parts if p])
    base = f"{BASE_DOMAIN}/{path}" if path else BASE_DOMAIN

    # Hints en query (para trazabilidad / no rompen si ML los ignora)
    q = {}
    if year_min is not None:
        q["year_min"] = year_min
    if year_max is not None:
        q["year_max"] = year_max
    if price_min_ars is not None:
        q["price_min_ars"] = price_min_ars
    if price_max_ars is not None:
        q["price_max_ars"] = price_max_ars
    if km_min is not None:
        q["km_min"] = km_min
    if km_max is not None:
        q["km_max"] = km_max

    if q:
        return f"{base}?{urlencode(q)}"
    return base


# ─────────────────────────────────────────
# Scraper principal (paginado)
# ─────────────────────────────────────────

@dataclass
class PageLog:
    page: int
    url: str
    items_found: int
    next_url: Optional[str]
    verification: bool = False  # posible bloqueo / captcha
    error: Optional[str] = None


HEADERS = {"User-Agent": UA, "Accept-Language": "es-AR,es;q=0.9,en;q=0.8"}


def _parse_listing_cards(soup: BeautifulSoup) -> List[dict]:
    rows = []
    cards = soup.select("li.ui-search-layout__item, .ui-search-result__wrapper, .ui-search-layout__item .ui-search-result")
    if not cards:
        # Fallback a cualquier link con clase ui-search-link
        cards = soup.select("a.ui-search-link")

    for c in cards:
        try:
            # Link + título
            a = c.select_one("a.ui-search-link") or c.find("a", href=True)
            url = a["href"].split("#")[0] if a else None
            title = _norm_spaces((a.get_text("", strip=True) if a else ""))

            # Precio
            price_block = c.select_one(".ui-search-price, .ui-search-price__second-line, .andes-money-amount") or c
            price, curr = _extract_price(price_block)

            # Ubicación / ciudad / estado
            loc = c.select_one(".ui-search-item__location, .ui-search-result__content-location")
            location_text = _norm_spaces(loc.get_text(" ", strip=True)) if loc else ""
            city, state = None, None
            if "," in location_text:
                parts = [p.strip() for p in location_text.split(",", 1)]
                city = parts[0]
                state = parts[1] if len(parts) > 1 else None
            else:
                state = location_text or None

            # Atributos adicionales (km, año, caja) a partir de badges/listas
            attr_text = " ".join(
                _norm_spaces(x.get_text(" ", strip=True))
                for x in c.select(".ui-search-card-attributes__attribute, .ui-search-item__group__element")
            )
            year = _extract_year(title) or _extract_year(attr_text)
            km = _extract_km(attr_text)

            # Caja (muy variable)
            gearbox = None
            m = re.search(r"\b(Automatic[ao]|Manual|CVT)\b", attr_text, re.I)
            if m:
                val = m.group(1).lower()
                if "cvt" in val:
                    gearbox = "CVT"
                elif "man" in val:
                    gearbox = "Manual"
                else:
                    gearbox = "Automática"

            rows.append(
                {
                    "title": title or None,
                    "permalink": url,
                    "price": price,
                    "currency": curr,
                    "state": state,
                    "city": city,
                    "km": km,
                    "gearbox": gearbox,
                    "year": year,
                }
            )
        except Exception:
            continue
    return rows


def _find_next_url(soup: BeautifulSoup) -> Optional[str]:
    # Botón siguiente habitual
    nxt = soup.select_one("li.andes-pagination__button--next a, a[rel=next]")
    if nxt and nxt.has_attr("href"):
        return nxt["href"]
    # Fallback: buscar _Desde_ en links
    for a in soup.find_all("a", href=True):
        if "_Desde_" in a["href"]:
            return a["href"]
    return None


def scrape_list(
    base_url: str,
    max_items: int = 1200,
    max_pages: int = 30,
    proxy_url: Optional[str] = None,
    delay_s: float = 0.8,
) -> Tuple[List[dict], List[PageLog]]:
    """Descarga listados desde una URL semilla y pagina hasta límites indicados.

    Devuelve: (rows, logs) donde rows es lista de dicts estandarizados y logs es PageLog.
    """
    session = requests.Session()
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    rows: List[dict] = []
    logs: List[PageLog] = []

    url = base_url
    for page_idx in range(1, max_pages + 1):
        try:
            r = session.get(url, headers=HEADERS, proxies=proxies, timeout=30)
            html = r.text
            soup = BeautifulSoup(html, "lxml")

            # Heurística simple de verificación/captcha
            verification = bool(re.search(r"verificaci\u00f3n|captcha|robot|verifica que eres", html, re.I))

            batch = _parse_listing_cards(soup)
            rows.extend(batch)

            next_url = _find_next_url(soup)
            logs.append(PageLog(page=page_idx, url=url, items_found=len(batch), next_url=next_url, verification=verification))

            if len(rows) >= max_items or not next_url:
                break

            url = next_url
            if delay_s and delay_s > 0:
                time.sleep(delay_s)
        except Exception as e:
            logs.append(PageLog(page=page_idx, url=url, items_found=0, next_url=None, error=str(e)))
            break

    # Trim si excedimos
    if len(rows) > max_items:
        rows = rows[:max_items]

    return rows, logs
