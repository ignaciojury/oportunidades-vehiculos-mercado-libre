# scraper.py — Single-file Streamlit app (based on app.py)
# Usage: streamlit run scraper.py
import os
import re
from datetime import datetime
from dataclasses import asdict, is_dataclass
from urllib.parse import urlsplit, urlunsplit

import pandas as pd
import streamlit as st

#from utils.scraper import build_base_url, scrape_list, canonicalize_ml_url  # reuses your utils module
import numpy as np


# ─────────────────────────────────────────
# Config de página
# ─────────────────────────────────────────
st.set_page_config(page_title="Oportunidades ML (Scraping)", page_icon="🚗", layout="wide")
st.title("🚗 Oportunidades en Autos & Camionetas — Scraping (sin API)")
st.caption(
    "La app consulta año por año dentro del rango elegido (p.ej. 2016, 2017, 2018), "
    "consolida, agrupa por TÍTULO y AÑO, calcula promedio ARS por grupo y detecta oportunidades."
)

# ─────────────────────────────────────────
# Helpers
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

    g = (tmp.groupby(kcols, dropna=False)["price_ars"]
             .agg(group_mean_ars="mean", group_n="count")
             .reset_index())

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


# ─────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────
with st.sidebar:
    st.header("Filtros de scraping")
    only_private = st.checkbox(
        "Sólo dueño directo",
        value=True,
        help="Usa /dueno-directo en la URL. Desactivalo para ampliar la muestra."
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
        help="Si elegís EXACTAMENTE una, se agrega en la ruta (automatica/manual/cvt)."
    )

    st.subheader("Marca / Modelo manual (no rompe nada)")
    brand_text = st.text_input("Marca contiene… (opcional)", value="", help="Ej: toyota, peugeot, ford")
    model_text = st.text_input("Modelo contiene… (opcional)", value="", help="Ej: corolla, 208, fiesta")
    match_all_words = st.checkbox("Coincidir todas las palabras", value=True,
                                  help="Si está activo, requiere que todas las palabras ingresadas estén en el título.")

    st.subheader("Agrupación por título")
    aggressive = st.checkbox("Normalización agresiva del título", value=False,
                              help="Quita tildes/símbolos para juntar variantes similares.")
    use_title_core = st.checkbox("Usar 'núcleo' del título (quita adjetivos)", value=False,
                                 help="Amplía grupos removiendo palabras como 'impecable', 'gnc', etc.")
    min_group_size = st.slider("Mínimo publicaciones por grupo", 2, 30, 3, step=1)
    pct_threshold = st.slider("% por debajo del promedio del grupo", 5, 60, 15, step=1)

    # Ampliar muestra (cada año) – fijo
    PAGES_PER_YEAR = 30
    ITEMS_PER_PAGE = 48
    per_year_max_items = PAGES_PER_YEAR * ITEMS_PER_PAGE
    st.subheader("Ampliar muestra (cada año)")
    st.caption(f"Fijo: {PAGES_PER_YEAR} páginas/año × {ITEMS_PER_PAGE} avisos/página ≈ {per_year_max_items} avisos/año.")

    delay = st.number_input("Delay entre páginas (s)", min_value=0.1, value=0.8, step=0.1)
    proxy = st.text_input("Proxy (http(s)://user:pass@host:puerto)", value=os.getenv("HTTP_PROXY", ""))

run = st.button("🔎 Buscar")


# ─────────────────────────────────────────
# Acción
# ─────────────────────────────────────────
if run:
    years_to_query = list(range(year_min, year_max + 1))
    st.info(f"Estrategia: búsqueda por año individual → {years_to_query}")

    rows_all: list[dict] = []
    logs_all: list[dict] = []
    total_by_year = []
    seen_links_all = set()  # <- DEDUPE GLOBAL POR AVISO

    # 1) Scraping por cada año del rango
    for y in years_to_query:
        base_url_y = build_base_url(
            dueno_directo=only_private,
            year_min=y, year_max=y,  # consulta "por año"
            price_min_ars=price_min, price_max_ars=price_max,
            km_min=km_min, km_max=km_max,
            transmissions=transmissions,
        )

        # Canonicalizamos ANTES de paginar, usando la versión de utils (devuelve (url, meta))
        seed_url, meta = canonicalize_ml_url(base_url_y, proxy.strip() or None)
        st.markdown(f"• Año {y}: <{seed_url}>")

        if meta.get("verification"):
            st.warning("⚠️ Mercado Libre pidió verificación/captcha. Probá con proxy residencial o menor frecuencia.")
        
        with st.spinner(f"Scrapeando año {y}…"):
            rows_y, logs_y = scrape_list(
                base_url=seed_url,
                max_items=per_year_max_items,
                max_pages=PAGES_PER_YEAR,
                proxy_url=proxy.strip() or None,
                delay_s=delay,
            )

        # Logs de este año (en forma dict) + metadata
        for lg in logs_y:
            d = _log_to_dict(lg)
            d["year_query"] = y
            d["base_url_seed"] = seed_url
            d["base_url_orig"] = base_url_y
            logs_all.append(d)

        # Filtrar duplicados GLOBALMENTE + imputar año si falta
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

        # respiro entre años
        if delay and delay > 0:
            pass

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
        st.warning("No se encontraron publicaciones en el rango. Si ves 'verification=True' en logs, probá con un proxy residencial o menor frecuencia.")
        st.stop()

    df = pd.DataFrame(rows_all)

    # De-dupe defensivo por si quedara algo
    if "_permalink_key" not in df.columns:
        df["_permalink_key"] = df["permalink"].fillna("").map(_canonical_link)
    df = df.dropna(subset=["_permalink_key"]).drop_duplicates(subset=["_permalink_key"], keep="first").reset_index(drop=True)

    # 3) Normalización ARS/USD
    extra = df.apply(lambda r: pd.Series(
        normalize_price_ars(r.get("price"), r.get("currency"), usd_ars, misprice_th)
    ), axis=1)
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
        "title","year","km","gearbox",
        "price","currency","assumed_currency","price_usd","price_ars",
        "state","city","permalink"
    ]
    base_cols = [c for c in base_cols if c in df_filtered.columns]
    shown = df_filtered.sort_values(by=["year", "price_ars"], ascending=[True, True]).reset_index(drop=True)[base_cols].copy()
    for c in ["price","price_usd","price_ars"]:
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
        comp_base,
        key_cols=["title_group", "year"],
        min_group_size=min_group_size
    )

    with st.expander("Top (título, año) repetidos"):
        top_pairs = (df_filtered
                    .dropna(subset=["title_group", "year"])
                    .groupby(["title_group", "year"])
                    .size()
                    .reset_index(name="n")
                    .sort_values("n", ascending=False)
                    .head(50))
        st.dataframe(top_pairs, use_container_width=True)

    with st.expander("Detalle de agrupación"):
        st.json(stats)

    if comp_best.empty:
        st.info(
            "Todavía no hay comparables con la configuración actual. "
            "Subí el tamaño de muestra, desactivá 'Sólo dueño directo', "
            "probá 'Normalización agresiva' y/o 'Núcleo del título', o bajá el 'Mínimo por grupo'."
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

    # 12) Export a Excel con hoja Oportunidades formateada + gráfico por grupo
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"oportunidades_scraping_por_anio_{ts}.xlsx"

    with pd.ExcelWriter(fname, engine="xlsxwriter") as w:
        if 'shown' in locals() and not shown.empty:
            shown.to_excel(w, index=False, sheet_name="Resultados")
        if 'comp_best' in locals() and not comp_best.empty:
            comp_best.to_excel(w, index=False, sheet_name="Comparables")

        wb = w.book

        # ---- Oportunidades (encabezados, orden y formato) ----
        if not opp.empty:
            wb = w.book  # asegurar referencia al workbook

            opp_xls = opp.copy()
            # Asegurar numéricos
            for c in ["price_ars", "group_mean_ars", "diff_ars", "undervalue_pct", "group_n"]:
                if c in opp_xls.columns:
                    opp_xls[c] = pd.to_numeric(opp_xls[c], errors="coerce")

            # % como fracción para usar formato "0%"
            if "undervalue_pct" in opp_xls.columns:
                opp_xls["undervalue_pct"] = opp_xls["undervalue_pct"] / 100.0

            # Ordenar por Diferencia desc (si existe)
            if "diff_ars" in opp_xls.columns:
                opp_xls = opp_xls.sort_values("diff_ars", ascending=False)

            # Columnas en el orden deseado
            cols_opp = [
                "title", "year", "permalink",
                "price_ars", "group_mean_ars", "group_n", "diff_ars", "undervalue_pct"
            ]
            cols_opp = [c for c in cols_opp if c in opp_xls.columns]
            opp_xls = opp_xls[cols_opp]

            # Renombrar encabezados
            rename_map = {
                "title": "Vehículo",
                "year": "Año",
                "permalink": "Link",
                "price_ars": "Precio ($ ARS)",
                "group_mean_ars": "Precio De mercado promedio ($ ARS)",
                "group_n": "Tamaño del grupo analizado",
                "diff_ars": "Diferencia ($ ARS)",
                "undervalue_pct": "Porcentaje de diferencia",
            }
            opp_xls.rename(columns=rename_map, inplace=True)

            # Escribir hoja
            sheet_name = "Oportunidades"
            opp_xls.to_excel(w, index=False, sheet_name=sheet_name)
            ws_opp = w.sheets[sheet_name]

            # Formatos: todo centrado (horizontal y vertical)
            base_center = {"align": "center", "valign": "vcenter"}
            fmt_header = wb.add_format({**base_center, "bold": True})
            fmt_text   = wb.add_format({**base_center})
            fmt_money  = wb.add_format({**base_center, "num_format": "#,##0"})
            fmt_int    = wb.add_format({**base_center, "num_format": "0"})
            fmt_pct    = wb.add_format({**base_center, "num_format": "0%"})

            # Anchos + formatos por columna
            # A: Vehículo, B: Año, C: Link, D: Precio, E: Promedio, F: Tamaño grupo, G: Diferencia, H: %
            ws_opp.set_column("A:A", 55, fmt_text)
            ws_opp.set_column("B:B", 10, fmt_int)
            ws_opp.set_column("C:C", 70, fmt_text)
            ws_opp.set_column("D:D", 18, fmt_money)
            ws_opp.set_column("E:E", 24, fmt_money)
            ws_opp.set_column("F:F", 16, fmt_int)
            ws_opp.set_column("G:G", 18, fmt_money)
            ws_opp.set_column("H:H", 16, fmt_pct)

            # Encabezado centrado y en negrita + congelar
            ws_opp.set_row(0, None, fmt_header)
            ws_opp.freeze_panes(1, 0)

            # ---- Datos para el gráfico (mínimo por grupo vs promedio)
            label_base = opp["title_group"] if "title_group" in opp.columns else opp["title"]
            chart_df = pd.DataFrame({
                "label": label_base.astype(str) + " (" + opp["year"].astype("Int64").astype(str) + ")",
                "precio_min_ars": opp["price_ars"],
                "promedio_grupo_ars": opp["group_mean_ars"],
            })
            chart_df = (
                chart_df.groupby("label", as_index=False)
                        .agg(precio_min_ars=("precio_min_ars", "min"),
                             promedio_grupo_ars=("promedio_grupo_ars", "first"))
            )

            chart_df.to_excel(w, index=False, sheet_name="ChartData")
            ws_cd = w.sheets["ChartData"]
            ws_cd.set_column("A:A", 55, fmt_text)
            ws_cd.set_column("B:C", 18, fmt_money)

            chart = wb.add_chart({"type": "column"})
            last_row = len(chart_df) + 1  # +1 por header
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

        meta_rows = []
        if "years_to_query" in locals():
            meta_rows.append(("years_queried", ", ".join(map(str, years_to_query))))
        meta_rows.append(("opportunities_count", 0 if opp.empty else len(opp)))
        meta_df = pd.DataFrame(meta_rows, columns=["key", "value"])
        meta_df.to_excel(w, index=False, sheet_name="Resumen")

    with open(fname, "rb") as f:
        st.download_button(
            "⬇️ Descargar Excel",
            data=f.read(),
            file_name=fname,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
