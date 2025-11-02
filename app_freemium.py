# app_freemium.py — Oportunidades ML (Scraping) con plan Freemium + Cookies + Excel mejorado
# -*- coding: utf-8 -*-

import os
import re
import json
import time
import uuid
from datetime import datetime
from dataclasses import asdict, is_dataclass
from urllib.parse import urlsplit, urlunsplit

import pandas as pd
import numpy as np
import requests
import streamlit as st
from streamlit_cookies_manager import EncryptedCookieManager

from utils.scraper import build_base_url, scrape_list

# ─────────────────────────────────────────
# Helpers de configuración / secretos
# ─────────────────────────────────────────

def _get_secret_or_env(key: str, default: str = "") -> str:
    """Lee primero de st.secrets si existe, luego de variables de entorno."""
    try:
        if hasattr(st, "secrets") and key in st.secrets:
            val = st.secrets.get(key)
            return str(val) if val is not None else default
    except Exception:
        pass
    return os.getenv(key, default)

# Límites y parámetros de plan (por defecto listo para DEMO)
FREE_LIMIT_SEARCHES     = int(_get_secret_or_env("FREE_LIMIT_SEARCHES",   "2"))  # ← SOLO 1 BÚSQUEDA FREE
FREE_PAGES_PER_YEAR     = int(_get_secret_or_env("FREE_PAGES_PER_YEAR",   "8"))
FREE_ITEMS_PER_PAGE     = int(_get_secret_or_env("FREE_ITEMS_PER_PAGE",   "36"))
PREMIUM_PAGES_PER_YEAR  = int(_get_secret_or_env("PREMIUM_PAGES_PER_YEAR","30"))
PREMIUM_ITEMS_PER_PAGE  = int(_get_secret_or_env("PREMIUM_ITEMS_PER_PAGE","48"))

# Códigos premium (separados por coma)
PREMIUM_CODES = {c.strip() for c in _get_secret_or_env("PREMIUM_CODES", "").split(",") if c.strip()}

# Cookie cifrada (persistencia por navegador)
COOKIE_PASSWORD = _get_secret_or_env("COOKIE_PASSWORD", "cambia_esto_en_secrets")
cookies = EncryptedCookieManager(prefix="ml_autos_", password=COOKIE_PASSWORD)
if not cookies.ready():
    st.stop()  # Streamlit necesita un render para inicializar cookies

# UID por navegador
if not cookies.get("uid"):
    cookies["uid"] = str(uuid.uuid4())
    cookies.save()

# Estado de cuota en cookie (JSON: {count, ts})
_quota_raw = cookies.get("quota") or json.dumps({"count": 0, "ts": int(time.time())})
quota = json.loads(_quota_raw)

# ── reemplazar estas funciones ─────────────────────────
def _persist_quota(q: dict):
    # guarda la cuota en la cookie cifrada
    cookies["quota"] = json.dumps(q)
    cookies.save()

def inc_search_count():
    quota["count"] = int(quota.get("count", 0)) + 1
    quota["ts"] = int(time.time())
    _persist_quota(quota)



# ─────────────────────────────────────────
# UI base
# ─────────────────────────────────────────
st.set_page_config(page_title="Oportunidades ML (Scraping)", page_icon="🚗", layout="wide")
st.title("🚗 Oportunidades en Autos & Camionetas")
st.caption(
    "Tenés 1 búsqueda gratis cada 30 días. Ingresa un código Premium para desbloquear límites."
)

# ─────────────────────────────────────────
# Funciones auxiliares de datos
# ─────────────────────────────────────────

def fmt_money(x):
    try:
        return f"{float(x):,.0f}".replace(",", ".")
    except Exception:
        return x


def normalize_price_ars(price, currency, usd_ars, misprice_ars_threshold=200_000):
    """
    - Si currency=ARS y price<threshold -> asumimos USD mal tipeado => convertimos a ARS (assumed_currency='USD*')
    - Si currency=USD -> convertimos a ARS, devolvemos price_usd
    - Otras -> convertimos a ARS tomando usd_ars como referencia (heurística)
    Devuelve: (price_ars, price_usd, assumed_currency)
    """
    if price is None:
        return None, None, None

    cur = (currency or "ARS").upper()
    p = float(price)

    if cur == "USD":
        return p * usd_ars, p, "USD"

    if cur == "ARS":
        if p < misprice_ars_threshold:
            return p * usd_ars, p, "USD*"
        return p, p / usd_ars, "ARS"

    return p, p / usd_ars, cur


def canonicalize_ml_url(u: str, proxy_url: str | None = None, timeout: int = 20) -> tuple[str, dict]:
    """
    Devuelve (url_canonica, meta).
    Si ML devuelve verificación/captcha, NO usamos esa URL y marcamos meta['verification']=True
    """
    meta = {"verification": False, "status": None, "final_url": None}
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
            "Referer": "https://autos.mercadolibre.com.ar/",
        }
        proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
        r = requests.get(u, headers=headers, proxies=proxies, timeout=timeout, allow_redirects=True)
        meta["status"] = r.status_code
        meta["final_url"] = r.url

        # Si nos mandan a verificación, no usamos esa URL “canónica”
        if "account-verification" in r.url or "/gz/" in r.url and "verification" in r.url:
            meta["verification"] = True
            # devolvemos la URL original para que scrape_list la intente igual (o lo registremos)
            return u, meta

        canon = r.url
        canon = re.sub(r"/_Desde_\d+/?$", "", canon, flags=re.IGNORECASE)
        return canon, meta
    except Exception as e:
        meta["status"] = f"error: {e}"
        return u, meta


def _canonical_link(u: str) -> str:
    """Normaliza un permalink: sin query ni fragment (evita duplicados por #polycard… o ?tracking_id=…)."""
    if not u:
        return ""
    p = urlsplit(u)
    return urlunsplit((p.scheme, p.netloc, p.path, "", ""))


def title_norm_exact(t: str) -> str:
    return (t or "").strip()


def title_norm_aggressive(t: str) -> str:
    if not isinstance(t, str):
        return ""
    s = t.lower()
    s = re.sub(r"[áàä]", "a", s)
    s = re.sub(r"[éèë]", "e", s)
    s = re.sub(r"[íìï]", "i", s)
    s = re.sub(r"[óòö]", "o", s)
    s = re.sub(r"[úùü]", "u", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

_STOPWORDS_DESC = {
    "impecable","excelente","único","unico","dueño","dueno","dueño unico","dueno unico",
    "permuto","permuta","urgente","oportunidad","full","pack","cuero",
    "gnc","nafta","diesel","tdi","turbo","16v","v6","v8",
    "financio","financiacion","u$s","usd","dolares","dólares",
    "km","kms","km reales","poco uso","segunda mano","primer dueño","primer dueno"
}

def title_core(s: str) -> str:
    s = title_norm_aggressive(s)
    if not s:
        return s
    tokens = s.split()
    keep = [t for t in tokens if t not in _STOPWORDS_DESC]
    return " ".join(keep).strip()


def build_groups_by_keys(df: pd.DataFrame, key_cols: list[str], min_group_size: int):
    """Agrupa por key_cols y devuelve df mergeado con group_mean_ars/group_n + stats."""
    missing = [c for c in key_cols + ["price_ars"] if c not in df.columns]
    if missing:
        return pd.DataFrame(), {"ok": False, "reason": f"faltan columnas: {missing}"}

    tmp = df.copy()
    kcols = []
    for c in key_cols:
        kc = f"__k_{c}"
        tmp[kc] = tmp[c].astype(object).where(pd.notna(tmp[c]), "__NA__").astype(str)
        kcols.append(kc)

    g = (
        tmp.groupby(kcols, dropna=False)["price_ars"]
           .agg(group_mean_ars="mean", group_n="count")
           .reset_index()
    )

    g_valid = g[g["group_n"] >= min_group_size].copy()
    merged = tmp.merge(g_valid, on=kcols, how="inner").drop(columns=kcols)

    stats = {
        "ok": True,
        "keys": key_cols,
        "groups": int(g_valid.shape[0]),
        "rows": int(merged.shape[0]),
        "min_group_size": int(min_group_size),
    }
    return merged, stats


def contains_all(haystack: str, needles: list[str]) -> bool:
    hs = (haystack or "").lower()
    return all(n.lower() in hs for n in needles if n.strip())


def _log_to_dict(x):
    if is_dataclass(x):
        return asdict(x)
    if isinstance(x, dict):
        return x
    if hasattr(x, "__dict__"):
        try:
            return dict(x.__dict__)
        except Exception:
            pass
    return {"value": str(x)}


def is_premium_code(code: str | None) -> bool:
    if not code:
        return False
    if not PREMIUM_CODES:
        # Si no configuraste códigos aún, cualquier código sirve (modo pruebas)
        return True
    return code in PREMIUM_CODES

# ─────────────────────────────────────────
# Sidebar: Plan + Filtros
# ─────────────────────────────────────────
with st.sidebar:
    # --- Plan / Premium ---
    st.subheader("Plan")
    st.caption("Ingresá tu código Premium para desbloquear límites.")
    premium_code = st.text_input("Código Premium", type="password")
    premium = is_premium_code(premium_code)

    if premium:
        st.success("✅ Premium activado")
    else:
        st.info(
            f"Plan Free: hasta {FREE_LIMIT_SEARCHES} búsqueda cada 30d, "
            f"{FREE_PAGES_PER_YEAR} páginas/año, {FREE_ITEMS_PER_PAGE} avisos/página."
        )

    # --- Pago Premium (Mercado Pago) ---
    st.divider()
    st.subheader("¿Querés Premium?")
    mp_url = st.secrets.get("MP_CHECKOUT_URL", os.getenv("MP_CHECKOUT_URL", ""))
    if mp_url:
        try:
            st.link_button("🛒 Comprar Premium (Mercado Pago)", mp_url)
        except Exception:
            st.markdown(f"[🛒 Comprar Premium (Mercado Pago)]({mp_url})")
        st.caption("Link de pago (URL completa):")
        st.code(mp_url, language="text")
    else:
        st.info("Configurá MP_CHECKOUT_URL en st.secrets o variables de entorno para mostrar el botón de pago.")

    # --- Filtros de scraping ---
    st.header("Filtros de scraping")
    only_private = st.checkbox(
        "Sólo dueño directo",
        value=True,
        help="Usa /dueno-directo en la URL. Desactivalo para ampliar la muestra.",
    )
    usd_ars = st.number_input("USD → ARS", min_value=1, value=1350, step=1)
    misprice_th = st.number_input("Asumir USD si ARS < X", min_value=10_000, value=200_000, step=10_000)

    year_min, year_max = st.slider("Rango de años (consulta año por año)", 1980, 2035, (2016, 2023), step=1)

    price_min, price_max = st.slider("Precio (ARS)", 0, 120_000_000, (0, 40_000_000), step=100_000)
    st.caption(f"Rango actual: ${price_min:,.0f} - ${price_max:,.0f}".replace(",", "."))

    km_min, km_max = st.slider("Kilómetros", 0, 450_000, (0, 220_000), step=5_000)
    st.caption(f"Rango actual: ${km_min:,.0f} - ${km_max:,.0f}".replace(",", "."))

    tx_opts = ["Automática", "Manual", "CVT"]
    transmissions = st.multiselect(
        "Tipo de caja (ruta)",
        tx_opts,
        default=["Automática"],
        help="Si elegís EXACTAMENTE una, se agrega en la ruta (automatica/manual/cvt).",
    )

    st.subheader("Marca / Modelo manual (opcional)")
    brand_text = st.text_input("Marca contiene…", value="", help="Ej: toyota, peugeot, ford")
    model_text = st.text_input("Modelo contiene…", value="", help="Ej: corolla, 208, fiesta")
    match_all_words = st.checkbox(
        "Coincidir todas las palabras",
        value=True,
        help="Si está activo, requiere que todas las palabras ingresadas estén en el título.",
    )

    st.subheader("Agrupación por título")
    aggressive = st.checkbox(
        "Normalización agresiva del título", value=False, help="Quita tildes/símbolos para juntar variantes similares."
    )
    use_title_core = st.checkbox(
        "Usar 'núcleo' del título (quita adjetivos)", value=False,
        help="Amplía grupos removiendo palabras como 'impecable', 'gnc', etc."
    )
    min_group_size = st.slider("Mínimo publicaciones por grupo", 2, 30, 3, step=1)
    pct_threshold = st.slider("% por debajo del promedio del grupo", 5, 60, 15, step=1)

    # --- Límites por plan (SILENCIOSOS, sin UI) ---
    PAGES_PER_YEAR = PREMIUM_PAGES_PER_YEAR if premium else FREE_PAGES_PER_YEAR
    ITEMS_PER_PAGE = PREMIUM_ITEMS_PER_PAGE if premium else FREE_ITEMS_PER_PAGE
    per_year_max_items = PAGES_PER_YEAR * ITEMS_PER_PAGE
    # (si algún día querés verlos, activá este debug:)
    # if False: st.caption(f"{PAGES_PER_YEAR} páginas/año × {ITEMS_PER_PAGE} avisos/página ≈ {per_year_max_items} avisos/año.")

    # --- Conectividad / rate limiting ---
    delay = st.number_input("Delay entre páginas (s)", min_value=0.1, value=0.8, step=0.1)
    proxy = st.text_input("Proxy (http(s)://user:pass@host:puerto)", value=os.getenv("HTTP_PROXY", ""))

# botón fuera del sidebar
run = st.button("🔎 Buscar")
# ─────────────────────────────────────────
# Utilidades para Excel: autoajuste y Link compacto
# ─────────────────────────────────────────

def _autofit_worksheet(ws, df: pd.DataFrame, max_width: int = 60, min_width: int = 8):
    """Ajusta ancho de columnas en base a longitud de encabezados y celdas."""
    if df is None or df.empty:
        return
    for idx, col in enumerate(df.columns):
        max_len = len(str(col))
        col_values = df[col].astype(str).fillna("")
        max_len = max(max_len, col_values.map(len).max())
        width = min(max_width, max(min_width, max_len + 2))
        ws.set_column(idx, idx, width)


def _write_df_with_links(writer: pd.ExcelWriter, df: pd.DataFrame, sheet_name: str,
                         link_col: str = "permalink", link_title: str = "Link"):
    """Escribe df en Excel, reemplazando la columna de URLs por un HYPERLINK compacto."""
    df2 = df.copy()
    link_present = link_col in df2.columns

    if link_present:
        insert_at = list(df2.columns).index(link_col)
        df2.insert(insert_at, link_title, "Ver")
        df2.drop(columns=[link_col], inplace=True)

    df2.to_excel(writer, index=False, sheet_name=sheet_name)
    ws = writer.sheets[sheet_name]

    wb = writer.book
    fmt_header = wb.add_format({"align": "center", "valign": "vcenter", "bold": True})
    ws.set_row(0, None, fmt_header)
    ws.freeze_panes(1, 0)

    if link_present:
        link_col_idx = list(df2.columns).index(link_title)
        fmt_link = wb.add_format({"font_color": "blue", "underline": 1, "align": "center"})
        for r, url in enumerate(df[link_col].fillna(""), start=1):
            if isinstance(url, str) and url.strip():
                ws.write_url(r, link_col_idx, url, fmt_link, string="Abrir")
            else:
                ws.write(r, link_col_idx, "-")

    _autofit_worksheet(ws, df2)


# ─────────────────────────────────────────
# Acción principal
# ─────────────────────────────────────────
if run:
    # --- Límite Freemium por cookie (1 búsqueda por 30 días, configurable) ---
    if not premium:
        already = int(quota.get("count", 0))
        if already >= FREE_LIMIT_SEARCHES:
            st.error(
                f"Límite de {FREE_LIMIT_SEARCHES} búsqueda(s) alcanzado en este navegador. "
                "Ingresá un código premium para continuar."
            )
            st.stop()
        # Cuenta esta búsqueda (persistente en cookie cifrada)
        inc_search_count()

    # Años a consultar
    years_to_query = list(range(year_min, year_max + 1))
    st.info(f"Estrategia: búsqueda por año individual → {years_to_query}")

    rows_all: list[dict] = []
    logs_all: list[dict] = []
    total_by_year: list[dict] = []
    seen_links_all: set[str] = set()  # DEDUPE GLOBAL POR AVISO

    # 1) Scraping por cada año del rango
    for y in years_to_query:
        base_url_y = build_base_url(
            dueno_directo=only_private,
            year_min=y,
            year_max=y,  # consulta "por año"
            price_min_ars=price_min,
            price_max_ars=price_max,
            km_min=km_min,
            km_max=km_max,
            transmissions=transmissions,
        )

        # Canonicalizamos y detectamos verificación/captcha
        seed_url, seed_meta = canonicalize_ml_url(base_url_y, proxy.strip() or None)
        st.markdown(f"• Año {y}: <{seed_url}>")
        if seed_meta.get("verification"):
            st.warning("⚠️ Mercado Libre solicitó verificación/captcha para esta búsqueda. "
                       "Probá con un proxy residencial o bajá el ritmo (delay).")

        # Scrape con defensas
        with st.spinner(f"Scrapeando año {y}…"):
            try:
                rows_y, logs_y = scrape_list(
                    base_url=seed_url,
                    max_items=per_year_max_items,
                    max_pages=PAGES_PER_YEAR,
                    proxy_url=proxy.strip() or None,
                    delay_s=delay,
                )
            except Exception as e:
                st.error(f"Error al scrapear {y}: {e}")
                rows_y, logs_y = [], []

        # Logs (incluye meta del seed)
        for lg in (logs_y or []):
            d = _log_to_dict(lg)
            d["year_query"] = y
            d["base_url_seed"] = seed_url
            d["base_url_orig"] = base_url_y
            d["seed_status"] = seed_meta.get("status")
            d["seed_final_url"] = seed_meta.get("final_url")
            d["seed_verification"] = seed_meta.get("verification")
            logs_all.append(d)

        # De-dupe global por permalink + imputar año faltante
        added = 0
        for r in (rows_y or []):
            r = dict(r)
            k = _canonical_link(r.get("permalink", ""))
            if not k or k in seen_links_all:
                continue
            seen_links_all.add(k)
            r["_permalink_key"] = k
            if r.get("year") in [None, "", 0]:
                r["year"] = y
            rows_all.append(r)
            added += 1

        total_by_year.append({"year": y, "items": added})

    # 2) Resumen por año y logs
    df_years = pd.DataFrame(total_by_year)
    st.subheader("Resumen por año")
    if not df_years.empty:
        st.dataframe(df_years, use_container_width=True)
        st.caption(f"Total consolidado (sin duplicados): **{int(df_years['items'].sum())}** publicaciones")
    else:
        st.write("Sin datos por año.")

    with st.expander("🧪 Logs por página (todas las consultas)"):
        df_logs = pd.DataFrame(logs_all)
        st.dataframe(df_logs, use_container_width=True) if not df_logs.empty else st.write("Sin logs.")

    if not rows_all:
        st.warning(
            "No se encontraron publicaciones en el rango. Si ves 'verification=True' en logs, probá con un proxy residencial o menor frecuencia."
        )
        st.stop()

    df = pd.DataFrame(rows_all)

    # De-dupe defensivo
    if "_permalink_key" not in df.columns:
        df["_permalink_key"] = df["permalink"].fillna("").map(_canonical_link)
    df = df.dropna(subset=["_permalink_key"]).drop_duplicates(subset=["_permalink_key"], keep="first").reset_index(drop=True)

    # 3) Normalización ARS/USD
    extra = df.apply(
        lambda r: pd.Series(normalize_price_ars(r.get("price"), r.get("currency"), usd_ars, misprice_th)), axis=1
    )
    extra.columns = ["price_ars", "price_usd", "assumed_currency"]
    df = pd.concat([df, extra], axis=1)
    df["price_ars"] = pd.to_numeric(df["price_ars"], errors="coerce")
    df["year"] = pd.to_numeric(df.get("year"), errors="coerce").astype("Int64")

    # 4) Claves de agrupación: título_norm y (opcional) núcleo
    if aggressive:
        df["title_norm"] = df["title"].map(title_norm_aggressive)
    else:
        df["title_norm"] = df["title"].map(title_norm_exact)

    if use_title_core:
        df["title_group"] = df["title_norm"].map(title_core)
    else:
        df["title_group"] = df["title_norm"]

    # 5) Filtros manuales de Marca / Modelo
    df_filtered = df.copy()
    tokens_brand = [t.strip() for t in brand_text.split()] if brand_text.strip() else []
    tokens_model = [t.strip() for t in model_text.split()] if model_text.strip() else []

    if tokens_brand:
        if match_all_words:
            df_filtered = df_filtered[df_filtered["title_norm"].map(lambda s: contains_all(s, tokens_brand))]
        else:
            df_filtered = df_filtered[df_filtered["title_norm"].str.contains("|".join(tokens_brand), case=False, na=False)]
    if tokens_model:
        if match_all_words:
            df_filtered = df_filtered[df_filtered["title_norm"].map(lambda s: contains_all(s, tokens_model))]
        else:
            df_filtered = df_filtered[df_filtered["title_norm"].str.contains("|".join(tokens_model), case=False, na=False)]

    st.caption(f"Publicaciones tras filtros manuales: **{len(df)} → {len(df_filtered)}**")

    # 6) Resultados crudos consolidados
    st.subheader("Resultados (consolidados de todos los años)")
    base_cols = [
        "title", "year", "km", "gearbox",
        "price", "currency", "assumed_currency", "price_usd", "price_ars",
        "state", "city", "permalink"
    ]
    base_cols = [c for c in base_cols if c in df_filtered.columns]
    shown = df_filtered.sort_values(by=["year", "price_ars"], ascending=[True, True]).reset_index(drop=True)[base_cols].copy()
    for c in ["price", "price_usd", "price_ars"]:
        if c in shown.columns:
            shown[c] = shown[c].apply(fmt_money)

    try:
        colcfg = {"permalink": st.column_config.LinkColumn("link")}
    except Exception:
        colcfg = {}

    st.dataframe(shown, use_container_width=True, column_config=colcfg)

    # 7) Top claves repetidas (debug)
    with st.expander("Top claves de agrupación repetidas (título o núcleo)"):
        vc = df_filtered["title_group"].value_counts().reset_index()
        vc.columns = ["title_group", "n"]
        st.dataframe(vc.head(50), use_container_width=True)

    # 8) Comparables por TÍTULO y AÑO
    df_filtered["year"] = pd.to_numeric(df_filtered.get("year"), errors="coerce").astype("Int64")
    comp_base = df_filtered.dropna(subset=["title_group", "year", "price_ars"]).copy()

    comp_best, stats = build_groups_by_keys(
        comp_base, key_cols=["title_group", "year"], min_group_size=min_group_size
    )

    with st.expander("Top (título, año) repetidos"):
        top_pairs = (
            df_filtered.dropna(subset=["title_group", "year"]) \
                      .groupby(["title_group", "year"]).size().reset_index(name="n") \
                      .sort_values("n", ascending=False).head(50)
        )
        st.dataframe(top_pairs, use_container_width=True)

    with st.expander("Detalle de agrupación"):
        st.json(stats)

    if comp_best.empty:
        st.info(
            "Todavía no hay comparables con la configuración actual. Subí el tamaño de muestra, desactivá 'Sólo dueño directo', probá 'Normalización agresiva' y/o 'Núcleo del título', o bajá el 'Mínimo por grupo'."
        )
        st.stop()

    # 9) Oportunidades vs promedio del grupo
    comp_best["diff_ars"] = comp_best["group_mean_ars"] - comp_best["price_ars"]
    comp_best["undervalue_pct"] = (comp_best["diff_ars"] / comp_best["group_mean_ars"]) * 100

    # DEDUPE por aviso (por si se coló algo desde los grupos)
    key_col = "_permalink_key" if "_permalink_key" in comp_best.columns else "permalink"
    comp_best = comp_best.drop_duplicates(subset=[key_col], keep="first")

    opp = comp_best[comp_best["undervalue_pct"] >= pct_threshold].copy()
    opp = opp.sort_values(by=["undervalue_pct", "price_ars"], ascending=[False, True]).reset_index(drop=True)

    # 10) Comparables
    st.markdown("### Comparables (por clave de agrupación)")
    comp_cols = [
        "title","year","price","currency","assumed_currency","price_usd","price_ars",
        "group_mean_ars","group_n","diff_ars","undervalue_pct",
        "state","city","permalink"
    ]
    comp_cols = [c for c in comp_cols if c in comp_best.columns]
    comp_show = comp_best[comp_cols].copy()
    for c in ["price","price_usd","price_ars","group_mean_ars","diff_ars"]:
        if c in comp_show.columns:
            comp_show[c] = comp_show[c].apply(fmt_money)
    if "undervalue_pct" in comp_show.columns:
        comp_show["undervalue_pct"] = comp_show["undervalue_pct"].map(lambda x: f"{x:.1f}%")
    st.dataframe(comp_show, use_container_width=True, column_config=colcfg)

    # 11) Oportunidades
    if not opp.empty:
        st.markdown("### 🟢 Oportunidades (por debajo del promedio del grupo)")
        opp_cols = [c for c in comp_cols if c in opp.columns]
        opp_show = opp[opp_cols].copy()
        for c in ["price","price_usd","price_ars","group_mean_ars","diff_ars"]:
            if c in opp_show.columns:
                opp_show[c] = opp_show[c].apply(fmt_money)
        if "undervalue_pct" in opp_show.columns:
            opp_show["undervalue_pct"] = opp_show["undervalue_pct"].map(lambda x: f"{x:.0f}%")
        st.dataframe(opp_show, use_container_width=True, column_config=colcfg)
    else:
        st.info("No hay oportunidades con el umbral actual. Bajá el %, aumentá la muestra o activá 'núcleo del título'.")

    # 12) Export a Excel con autoajuste + Link compacto + gráfico por grupo
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"oportunidades_scraping_por_anio_{ts}.xlsx"

    export_df_results = shown.copy()
    export_df_comp = comp_best.copy()
    export_df_opp = opp.copy()

    with pd.ExcelWriter(fname, engine="xlsxwriter") as w:
        # Resultados
        if not export_df_results.empty:
            _write_df_with_links(w, export_df_results, sheet_name="Resultados", link_col="permalink", link_title="Link")

        # Comparables
        if not export_df_comp.empty:
            _write_df_with_links(w, export_df_comp, sheet_name="Comparables", link_col="permalink", link_title="Link")

        # Oportunidades (encabezados, formatos y gráfico)
        if not export_df_opp.empty:
            wb = w.book

            opp_xls = export_df_opp.copy()
            for c in ["price_ars", "group_mean_ars", "diff_ars", "undervalue_pct", "group_n"]:
                if c in opp_xls.columns:
                    opp_xls[c] = pd.to_numeric(opp_xls[c], errors="coerce")
            if "undervalue_pct" in opp_xls.columns:
                opp_xls["undervalue_pct"] = opp_xls["undervalue_pct"] / 100.0
            if "diff_ars" in opp_xls.columns:
                opp_xls = opp_xls.sort_values("diff_ars", ascending=False)

            cols_opp = [
                "title","year","permalink","price_ars","group_mean_ars","group_n","diff_ars","undervalue_pct"
            ]
            cols_opp = [c for c in cols_opp if c in opp_xls.columns]
            opp_xls = opp_xls[cols_opp]

            rename_map = {
                "title": "Vehículo",
                "year": "Año",
                "permalink": "Link",
                "price_ars": "Precio ($ ARS)",
                "group_mean_ars": "Precio de mercado promedio ($ ARS)",
                "group_n": "Tamaño del grupo analizado",
                "diff_ars": "Diferencia ($ ARS)",
                "undervalue_pct": "Porcentaje de diferencia",
            }
            opp_xls.rename(columns=rename_map, inplace=True)

            # Escribir hoja con links compactos
            orig_permalink = export_df_opp["permalink"] if "permalink" in export_df_opp.columns else pd.Series([])
            opp_xls.to_excel(w, index=False, sheet_name="Oportunidades")
            ws_opp = w.sheets["Oportunidades"]

            base_center = {"align": "center", "valign": "vcenter"}
            fmt_header = wb.add_format({**base_center, "bold": True})
            fmt_text   = wb.add_format({**base_center})
            fmt_money  = wb.add_format({**base_center, "num_format": "#,##0"})
            fmt_int    = wb.add_format({**base_center, "num_format": "0"})
            fmt_pct    = wb.add_format({**base_center, "num_format": "0%"})

            ws_opp.set_row(0, None, fmt_header)
            ws_opp.freeze_panes(1, 0)

            # Compactar columna Link como hipervínculo "Abrir"
            if "Link" in opp_xls.columns and len(orig_permalink) == len(opp_xls):
                col_idx = list(opp_xls.columns).index("Link")
                fmt_link = wb.add_format({"font_color": "blue", "underline": 1, "align": "center"})
                for r, url in enumerate(orig_permalink.fillna(""), start=1):
                    if isinstance(url, str) and url.strip():
                        ws_opp.write_url(r, col_idx, url, fmt_link, string="Abrir")
                    else:
                        ws_opp.write(r, col_idx, "-")

            # Autoajustes
            ws_opp.set_column("A:A", 55, fmt_text)
            ws_opp.set_column("B:B", 10, fmt_int)
            ws_opp.set_column("C:C", 12, fmt_text)  # Link compacto
            ws_opp.set_column("D:D", 18, fmt_money)
            ws_opp.set_column("E:E", 24, fmt_money)
            ws_opp.set_column("F:F", 16, fmt_int)
            ws_opp.set_column("G:G", 18, fmt_money)
            ws_opp.set_column("H:H", 16, fmt_pct)

            # Datos para el gráfico (mínimo por grupo vs promedio)
            label_base = export_df_opp["title_group"] if "title_group" in export_df_opp.columns else export_df_opp["title"]
            chart_df = pd.DataFrame({
                "label": label_base.astype(str) + " (" + export_df_opp["year"].astype("Int64").astype(str) + ")",
                "precio_min_ars": export_df_opp["price_ars"],
                "promedio_grupo_ars": export_df_opp["group_mean_ars"],
            })
            chart_df = chart_df.groupby("label", as_index=False).agg(
                precio_min_ars=("precio_min_ars", "min"),
                promedio_grupo_ars=("promedio_grupo_ars", "first")
            )

            chart_df.to_excel(w, index=False, sheet_name="ChartData")
            ws_cd = w.sheets["ChartData"]
            _autofit_worksheet(ws_cd, chart_df)

            chart = wb.add_chart({"type": "column"})
            last_row = len(chart_df) + 1
            chart.add_series({
                "name": "Precio oportunidad (mín)",
                "categories": ["ChartData", 1, 0, last_row - 1, 0],
                "values":     ["ChartData", 1, 1, last_row - 1, 1],
            })
            chart.add_series({
                "name": "Promedio del grupo",
                "categories": ["ChartData", 1, 0, last_row - 1, 0],
                "values":     ["ChartData", 1, 2, last_row - 1, 2],
            })
            chart.set_title({"name": "Precio vs Promedio por grupo (título + año)"})
            chart.set_x_axis({"name": "Grupo"})
            chart.set_y_axis({"name": "Precio ARS"})
            chart.set_legend({"position": "bottom"})

            ws_g = wb.add_worksheet("Gráfico")
            ws_g.insert_chart("A1", chart, {"x_scale": 2.0, "y_scale": 1.7})

        # Resumen / metadatos
        meta_rows = []
        meta_rows.append(("plan", "premium" if premium else "free"))
        meta_rows.append(("free_searches_limit", str(FREE_LIMIT_SEARCHES)))
        meta_rows.append(("searches_used_cookie", str(quota.get("count", 0))))
        meta_rows.append(("pages_per_year", str(PAGES_PER_YEAR)))
        meta_rows.append(("items_per_page", str(ITEMS_PER_PAGE)))
        if "years_to_query" in locals():
            meta_rows.append(("years_queried", ", ".join(map(str, years_to_query))))
        meta_rows.append(("opportunities_count", 0 if export_df_opp.empty else len(export_df_opp)))
        meta_df = pd.DataFrame(meta_rows, columns=["key", "value"])
        meta_df.to_excel(w, index=False, sheet_name="Resumen")
        _autofit_worksheet(w.sheets["Resumen"], meta_df)

    with open(fname, "rb") as f:
        st.download_button(
            "⬇️ Descargar Excel",
            data=f.read(),
            file_name=fname,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

# ─────────────────────────────────────────
# Footer
# ─────────────────────────────────────────
st.markdown("---\n**Freemium**: 1 búsqueda gratis/30 días por navegador. Para desbloquear todo, ingresá tu código Premium en la barra lateral.")
