# utils/scraper.py
from __future__ import annotations
import re
import time
import math
import random
from dataclasses import dataclass, asdict
from typing import List, Tuple, Dict, Optional
from urllib.parse import urlencode, urlunsplit

import requests
from bs4 import BeautifulSoup


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
)

@dataclass
class PageLog:
    page: int
    url: str
    status: int
    items_found: int
    verification: bool = False
    note: str = ""


def _proxies(proxy_url: Optional[str]) -> Optional[dict]:
    if not proxy_url:
        return None
    return {"http": proxy_url, "https": proxy_url}


def build_base_url(
    dueno_directo: bool,
    year_min: int, year_max: int,
    price_min_ars: int, price_max_ars: int,
    km_min: int, km_max: int,
    transmissions: Optional[List[str]] = None,
) -> str:
    """
    Construye URL de Mercado Libre Autos:
      - Segmentos: /dueno-directo/(automatico|manual|cvt)
      - Query: ?year=a-b&price=x-y&km=i-j
    """
    base_host = "autos.mercadolibre.com.ar"

    segments: List[str] = []
    if dueno_directo:
        segments.append("dueno-directo")

    # transmisión sólo si hay EXACTAMENTE una
    if transmissions:
        tx_map = {"Automática": "automatico", "Manual": "manual", "CVT": "cvt"}
        picks = [tx_map.get(t.strip()) for t in transmissions if t.strip() in tx_map]
        picks = [p for p in picks if p]
        if len(picks) == 1:
            segments.append(picks[0])

    path = "/".join(segments)
    if path and not path.endswith("/"):
        path += "/"

    q = {
        "year": f"{int(year_min)}-{int(year_max)}",
        "price": f"{int(price_min_ars)}-{int(price_max_ars)}",
        "km": f"{int(km_min)}-{int(km_max)}",
    }
    query = urlencode(q)
    return urlunsplit(("https", base_host, path, query, ""))


def canonicalize_ml_url(u: str, proxy_url: Optional[str] = None, timeout: int = 20) -> Tuple[str, Dict]:
    """
    Hace GET con redirects y devuelve:
      - URL canónica (sin '/_Desde_###')
      - meta: {'verification': bool}
    """
    try:
        r = requests.get(
            u,
            headers={"User-Agent": USER_AGENT},
            proxies=_proxies(proxy_url),
            timeout=timeout,
            allow_redirects=True,
        )
        r.raise_for_status()
        url = re.sub(r"/_Desde_\d+/?$", "", r.url, flags=re.IGNORECASE)
        return url, {"verification": ("account-verification" in r.url)}
    except Exception:
        # ante error, devolvemos lo que tengamos
        return u, {"verification": False}


def _parse_cards(html: str) -> List[dict]:
    """
    Parser defensivo de tarjetas. Intenta:
      1) JSON-LD
      2) Selectores de tarjetas con BeautifulSoup
    Devuelve una lista de dicts con claves: title, price, currency, year, km, gearbox, state, city, permalink
    (lo que no encuentre, lo deja en None).
    """
    out: List[dict] = []

    soup = BeautifulSoup(html, "html.parser")

    # 1) Intento JSON-LD (a veces MercadoLibre expone product list)
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            import json
            data = json.loads(script.text.strip())
            # puede ser dict o lista
            items = []
            if isinstance(data, dict):
                if "@type" in data and data.get("@type") in ("ItemList", "CollectionPage"):
                    items = data.get("itemListElement", []) or []
            elif isinstance(data, list):
                for d in data:
                    if isinstance(d, dict) and d.get("@type") in ("ItemList", "CollectionPage"):
                        items += d.get("itemListElement", []) or []

            for el in items:
                # elementos suelen tener {"@type": "ListItem", "item": {...}}
                it = el.get("item") if isinstance(el, dict) else None
                if not isinstance(it, dict):
                    continue
                url = it.get("url")
                name = it.get("name")
                offers = it.get("offers") or {}
                price = offers.get("price")
                currency = offers.get("priceCurrency")
                if url or name:
                    out.append({
                        "title": name,
                        "price": price,
                        "currency": currency,
                        "year": None,
                        "km": None,
                        "gearbox": None,
                        "state": None,
                        "city": None,
                        "permalink": url,
                    })
        except Exception:
            pass

    # 2) Parser de tarjetas visibles (fallback)
    # Tarjetas clásicas con <a class="ui-search-link"> y bloques de precio
    cards = soup.select("li.ui-search-layout__item, .ui-search-layout__item")
    for li in cards:
        try:
            a = li.select_one("a.ui-search-link")
            url = a.get("href") if a else None
            title_el = li.select_one("h2.ui-search-item__title, .poly-card__title, .ui-search-item__title")
            title = title_el.get_text(strip=True) if title_el else None

            price_el = li.select_one(".andes-money-amount__fraction")
            price = None
            if price_el:
                raw = price_el.get_text(strip=True).replace(".", "").replace(",", "")
                price = int(re.sub(r"\D", "", raw)) if re.search(r"\d", raw) else None

            currency = "ARS"  # mayoría en ARS; si hay “U$S” podríamos detectar, pero no siempre está marcado

            # datos opcionales
            city = None
            state = None
            loc_el = li.select_one(".ui-search-item__group__element.ui-search-item__location")
            if loc_el:
                loc = loc_el.get_text(" ", strip=True)
                # ej: "Capital Federal" o "Córdoba"
                city = None
                state = loc

            # año / km / caja suelen estar en “attributes”
            year = None
            km = None
            gearbox = None
            attr_els = li.select(".ui-search-card-attributes__attribute, .ui-search-item__group__attribute")
            for at in attr_els:
                txt = at.get_text(" ", strip=True).lower()
                if re.search(r"\b(19|20)\d{2}\b", txt):
                    m = re.search(r"\b((19|20)\d{2})\b", txt)
                    if m:
                        year = int(m.group(1))
                if "km" in txt:
                    n = re.sub(r"[^\d]", "", txt)
                    if n:
                        km = int(n)
                if any(x in txt for x in ["automática", "automatico", "automatica", "manual", "cvt"]):
                    if "manual" in txt:
                        gearbox = "Manual"
                    elif "cvt" in txt:
                        gearbox = "CVT"
                    else:
                        gearbox = "Automática"

            out.append({
                "title": title,
                "price": price,
                "currency": currency,
                "year": year,
                "km": km,
                "gearbox": gearbox,
                "state": state,
                "city": city,
                "permalink": url,
            })
        except Exception:
            continue

    return out


def _page_url(u: str, page_index: int) -> str:
    """
    MercadoLibre pagina con sufijo '/_Desde_###'
    page_index base 0. Cada página tiene ~48 resultados. El offset es 1-indexado.
    """
    if page_index <= 0:
        return u
    # offset ML: 1 + 48*page_index (pero aceptan '+/-')
    offset = 1 + 48 * page_index
    if u.endswith("/"):
        return f"{u}_Desde_{offset}"
    return f"{u}/_Desde_{offset}"


def scrape_list(
    base_url: str,
    max_items: int = 48 * 30,
    max_pages: int = 30,
    proxy_url: Optional[str] = None,
    delay_s: float = 0.8,
) -> Tuple[List[dict], List[dict]]:
    """
    Itera páginas desde base_url, agregando '/_Desde_###'.
    Detiene al llegar a max_items, max_pages o si detecta verificación.
    Devuelve (rows, logs) donde logs es lista de dicts de PageLog.
    """
    rows: List[dict] = []
    logs: List[dict] = []

    for pi in range(max_pages):
        url = _page_url(base_url, pi)
        try:
            r = requests.get(
                url,
                headers={"User-Agent": USER_AGENT},
                proxies=_proxies(proxy_url),
                timeout=25,
                allow_redirects=True,
            )
            status = r.status_code
            verif = ("account-verification" in r.url)
            if status != 200 or verif:
                logs.append(asdict(PageLog(pi + 1, url, status, 0, verification=verif, note="blocked")))
                break

            items = _parse_cards(r.text)
            rows.extend(items)
            logs.append(asdict(PageLog(pi + 1, url, status, len(items), verification=False)))

            if len(rows) >= max_items:
                rows = rows[:max_items]
                break

            # pequeño delay con jitter
            sleep_s = max(0.0, delay_s + random.uniform(-0.25, 0.25))
            time.sleep(sleep_s)

        except Exception as e:
            logs.append(asdict(PageLog(pi + 1, url, 0, 0, verification=False, note=f"error: {e}")))
            break

    return rows, logs