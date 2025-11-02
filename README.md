# Oportunidades en Mercado Libre (Autos & Camionetas)

Buscador de **oportunidades** basado en scraping por año. Agrupa por **título + año**, calcula el **promedio del grupo** y detecta publicaciones **10–30%** por debajo del mercado. Incluye **exportación a Excel** con gráficos y **modo Freemium**.

## Demo / App

* Frontend: **Streamlit** (`app_freemium.py`)
* Scraper: `utils/scraper.py`

> **Nota legal:** Respetá los Términos de Uso del sitio objetivo. Este proyecto es educativo. El HTML de Mercado Libre puede cambiar sin aviso y el scraping puede verse limitado por verificaciones anti-bot.

---

## Características

* Búsqueda por **rango de años** (consulta año por año).
* Filtros: **dueño directo**, precio (ARS), kilómetros, transmisión.
* Agrupación por **título normalizado** y opción de **núcleo del título** (quita adjetivos comunes).
* Detección de **oportunidades** con umbral configurable (% bajo el promedio del grupo).
* **Excel**: auto-ajuste de columnas, hipervínculos compactos ("Abrir"), tablas y **gráfico** comparativo.
* **Freemium/Premium** con límites por plan y **código premium**.

---

## Requisitos

`requirements.txt` sugerido:

```
streamlit
pandas
requests
numpy
xlsxwriter
beautifulsoup4
lxml
```

> Si usás un entorno virtual: `python -m venv .venv && source .venv/bin/activate` (Linux/Mac) o `./.venv/Scripts/activate` (Windows).

---

## Estructura

```
.
├── app_freemium.py        # App Streamlit (freemium)
├── utils/
│   └── scraper.py        # URL builder + scraper paginado
├── requirements.txt
├── README.md
└── .gitignore
```

---

## Variables de entorno / Secrets

Configuralas en **Streamlit Cloud** (Secrets) o como variables de entorno locales.

* `FREE_LIMIT_SEARCHES` (int) – búsquedas por sesión en modo Free (por defecto `10`).
* `FREE_PAGES_PER_YEAR` (int) – páginas por año en Free (por defecto `8`).
* `FREE_ITEMS_PER_PAGE` (int) – avisos por página en Free (por defecto `36`).
* `PREMIUM_PAGES_PER_YEAR` (int) – páginas por año en Premium (por defecto `30`).
* `PREMIUM_ITEMS_PER_PAGE` (int) – avisos por página en Premium (por defecto `48`).
* `PREMIUM_CODES` (str) – lista separada por comas con códigos válidos, p.ej.: `"code1,code2"`.

> Localmente podés usar `.streamlit/secrets.toml` (pero **NO lo subas** al repo):
>
> ```toml
> PREMIUM_CODES = "code1,code2"
> FREE_LIMIT_SEARCHES = 10
> FREE_PAGES_PER_YEAR = 8
> FREE_ITEMS_PER_PAGE = 36
> PREMIUM_PAGES_PER_YEAR = 30
> PREMIUM_ITEMS_PER_PAGE = 48
> ```

---

## Ejecutar localmente

```bash
pip install -r requirements.txt
streamlit run app_freemium.py
```

> Si necesitás proxy residencial, pasalo por la UI o exportá `HTTP_PROXY` / `HTTPS_PROXY`.

---

## Deploy en Streamlit Cloud

1. Subí el repo a GitHub (privado recomendado).
2. En **Streamlit Cloud**, crea una app y seleccioná `app_freemium.py`.
3. En **Secrets**, pegá las variables del bloque anterior.
4. **Deploy**.

### Custom domain

* Configurá un dominio en las opciones del proyecto (CNAME en tu DNS → ver panel de Streamlit Cloud).

---

## Uso

1. Elegí filtros en la barra lateral (años, precio, kms, transmisión, *dueño directo*).
2. Optativo: marca *Normalización agresiva* y *Núcleo del título* para agrupar variantes.
3. Elegí el **umbral %** por debajo del promedio del grupo.
4. Clic en **Buscar**.
5. Exportá el Excel desde el botón **Descargar Excel**.

---

## Limitaciones y recomendaciones

* ML puede mostrar **verificación/captcha**; si ocurre seguido, **bajá la frecuencia** o usá **proxy residencial**.
* El HTML cambia: si dejan de aparecer tarjetas, actualizá selectores en `scraper.py`.
* El agrupamiento por título es heurístico; para mayor precisión, considerar **modelo ML** por modelo/versión.

---

## Roadmap (ideas)

* Alertas por email/Telegram cuando se detecten nuevas oportunidades.
* Histórico de precios por modelo.
* Integración oficial con **API de Mercado Libre** cuando sea conveniente.
* Pago con **Mercado Pago / Stripe** para códigos premium.
* Panel admin simple para gestionar códigos y ver métricas.

---

## Licencia

Proyecto privado por ahora. Si se abre, sugerida **MIT** o **Apache-2.0**.
