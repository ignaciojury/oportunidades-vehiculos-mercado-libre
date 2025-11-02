# Oportunidades ML (Scraping) — Freemium

Buscador de oportunidades en Mercado Libre Autos con agrupación por **título + año**, normalización **ARS/USD**, y exportación a **Excel** con **links compactos** y **gráfica**. Incluye modelo **Freemium** con 1 búsqueda gratis por navegador cada 30 días (persistencia por **cookie cifrada**) y desbloqueo **Premium** mediante **código**.

---

## 📦 Estructura recomendada

```
.
├─ app_freemium.py
├─ utils/
│  └─ scraper.py
├─ requirements.txt
├─ README.md
└─ .env              # opcional (o usar st.secrets)
```

> **Nota**: `scraper.py` debe exponer `build_base_url(...)` y `scrape_list(...)`.

---

## ✨ Funcionalidades

* **Freemium con cookies**: 1 búsqueda gratis cada 30 días por navegador.
* **Código Premium** (sidebar) para desbloquear límites de paginado/muestra.
* **Normalización de precios** (detecta USD mal tipeado en ARS bajo umbral).
* **Agrupación** por *título normalizado* + *año* con detección de infravalorados.
* **Export a Excel** con:

  * Autoajuste de columnas.
  * Columna **Link** compacta (hipervínculo "Abrir").
  * Hoja **Gráfico** (precio oportunidad vs promedio del grupo).
* **Filtros**: rango de años, precio, km, transmisión, marca/modelo.

---

## ⚙️ Configuración (ENV o `st.secrets`)

La app lee primero de `st.secrets` y luego de variables de entorno. Puedes usar **uno u otro**.

### Opción A — `st.secrets` (Streamlit Cloud / local)

Crea un archivo `.streamlit/secrets.toml` (local) o usa el editor de **Secrets** en Streamlit Cloud con este contenido de ejemplo:

```toml
# Límites Free/Premium
FREE_LIMIT_SEARCHES = "1"            # ← 1 búsqueda FREE por navegador / 30 días
FREE_PAGES_PER_YEAR = "8"
FREE_ITEMS_PER_PAGE = "36"
PREMIUM_PAGES_PER_YEAR = "30"
PREMIUM_ITEMS_PER_PAGE = "48"

# Códigos Premium (separados por coma)
PREMIUM_CODES = "ABC123,XYZ999,VIP-2025"

# Clave para cifrar cookie (cámbiala!)
COOKIE_PASSWORD = "pon-una-clave-segura-larga"
```

### Opción B — `.env` (local, uvicorn/docker/etc.)

Crea `.env` en la raíz (o exporta variables en tu shell):

```env
FREE_LIMIT_SEARCHES=1
FREE_PAGES_PER_YEAR=8
FREE_ITEMS_PER_PAGE=36
PREMIUM_PAGES_PER_YEAR=30
PREMIUM_ITEMS_PER_PAGE=48

PREMIUM_CODES=ABC123,XYZ999,VIP-2025
COOKIE_PASSWORD=pon-una-clave-segura-larga
```

> La app prioriza `st.secrets` sobre ENV. En producción, evita subir `.env` al repo.

---

## 🧩 Dependencias

Archivo `requirements.txt` sugerido:

```
streamlit>=1.36
pandas>=2.1
numpy>=1.26
requests>=2.31
XlsxWriter>=3.1
streamlit-cookies-manager>=0.2
```

Instala con:

```bash
pip install -r requirements.txt
```

---

## ▶️ Ejecución local

```bash
# 1) Clonar
git clone https://github.com/tu-usuario/ml-autos-freemium.git
cd ml-autos-freemium

# 2) (Opcional) crear .venv
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 3) Dependencias
pip install -r requirements.txt

# 4) Configurar secrets o .env (ver arriba)

# 5) Ejecutar
streamlit run app_freemium.py
```

La app quedará disponible en `http://localhost:8501`.

---

## ☁️ Despliegue en Streamlit Community Cloud

1. **Sube** el repo a GitHub (público o privado).
2. Entra a **share.streamlit.io** → **New app** → conecta el repo y selecciona `app_freemium.py` como *Main file*.
3. En **Advanced settings → Secrets**, pega el bloque `secrets.toml` del ejemplo.
4. (Opcional) **Variables de entorno** si no usas Secrets.
5. Deploy ✅

### Nota sobre límites Free y cookies

* La cookie `ml_autos_quota` contiene `{count, ts}` y expira a los **30 días**.
* El límite **FREE** (por defecto 1) bloquea nuevas búsquedas si la cookie indica uso ≥ límite.
* Ingresar un **código Premium** válido en el sidebar desactiva los límites de muestra.

---

## 🔑 Flujo de Premium por código

1. Genera y reparte **códigos** (p.ej. `ABC123`) a tus compradores manualmente o por tienda.
2. Agrega esos códigos a `PREMIUM_CODES` (separados por coma) en `st.secrets` o ENV.
3. El usuario ingresa el código en el **sidebar** → la app valida y **activa Premium**.

> En el futuro puedes migrar a un checkout (Mercado Pago / Stripe) que emita y valide **tokens** de acceso.

---

## 📤 Exportación a Excel

* Hojas: **Resultados**, **Comparables**, **Oportunidades**, **ChartData**, **Gráfico**, **Resumen**.
* Autoajuste de columnas y header centrado.
* Columna **Link** compacta con hipervínculo de texto **"Abrir"** en lugar de URL larga.
* Gráfico: *Precio oportunidad (mín)* vs *Promedio del grupo* por clave (título + año).

---

## 🧪 Variables que puedes tunear

* `misprice_ars_threshold` (detección USD mal tipeado): por defecto **200.000**.
* `PAGES_PER_YEAR`, `ITEMS_PER_PAGE` según plan.
* `delay` y `proxy` (sidebar) para *rate-limit/antibot*.

---

## 🛡️ Notas legales

Este proyecto es una herramienta de análisis. Respeta términos de uso de los sitios de destino. El autor no asume responsabilidad por el uso que hagas de los resultados.

---

## 🧾 Licencia

Recomendado: **MIT** o **Apache-2.0** para facilitar adopción comercial. Crea un archivo `LICENSE` con una de estas plantillas:

* MIT → [https://choosealicense.com/licenses/mit/](https://choosealicense.com/licenses/mit/)
* Apache-2.0 → [https://choosealicense.com/licenses/apache-2.0/](https://choosealicense.com/licenses/apache-2.0/)

---

## 🤝 Contribuciones

PRs bienvenidos. Abre issues con:

* Descripción
* Pasos para reproducir
* Logs (si aplica)

---

## 📄 `.env.example`

Copia/renombra a `.env` y ajusta valores:

```env
# Freemium (1 búsqueda free por 30 días)
FREE_LIMIT_SEARCHES=1
FREE_PAGES_PER_YEAR=8
FREE_ITEMS_PER_PAGE=36

# Premium
PREMIUM_PAGES_PER_YEAR=30
PREMIUM_ITEMS_PER_PAGE=48
PREMIUM_CODES=ABC123,XYZ999,VIP-2025

# Cookie
COOKIE_PASSWORD=pon-una-clave-segura-larga
```

---

## 🆘 FAQ

**¿Necesito hacer el repo público para publicar?**
No necesariamente. Streamlit Cloud soporta repos **privados** si conectas tu GitHub.

**¿Cómo cambio el límite Free a 1 búsqueda?**
Ya viene configurado (`FREE_LIMIT_SEARCHES=1`). Ajusta en `secrets` o ENV si quieres otro valor.

**¿Dónde agrego los códigos Premium?**
En `PREMIUM_CODES` separados por coma en `st.secrets` o `.env`.

**¿Cómo cobro?**
Inicialmente distribuye **códigos** manuales luego de cobrar por Mercado Pago/Stripe. Próximamente se puede integrar un webhook que genere códigos y los inserte en `PREMIUM_CODES`.
