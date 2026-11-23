# 🎮 Vigilante de stock de PS5 (España) → Discord

Bot que se ejecuta solo en **GitHub Actions** varias veces al día, revisa tiendas españolas y te manda un **mensaje a Discord** cuando hay una **PS5 nueva** (no seminueva ni reacondicionada) **disponible para comprar por 600 € o menos**.

| Tienda | Cómo se comprueba |
|---|---|
| El Corte Inglés | Fichas de consolas PS5 → datos `schema.org` (InStock / SoldOut) |
| MediaMarkt | Búsqueda "consola ps5" → fichas (`schema.org` + botón de compra) |
| GAME | Búsqueda → fichas (descarta las **seminuevas**) |
| Xtralife | Sección de consolas PS5 → fichas |
| PcComponentes | Categoría Videoconsolas PS5 → fichas |
| Amazon.es | Fichas principales de PS5 + búsqueda (botón "Añadir a la cesta") |
| Carrefour | Tarjetas del buscador (vendedor, precio y "Agotado temporalmente") |
| AliExpress | Búsqueda con **envío desde España** |
| Chollometro | Chollos de PS5 **activos** (cubre también la tienda de PlayStation, ofertas con cupón, etc.) |

Fnac está desactivada: usa un captcha anti-bots que bloquea a GitHub Actions.

**Horario (11 revisiones al día, hora peninsular de invierno):** cada 2 horas de 7:07 a 21:07, y además a las 23:04, 00:04 y 00:25, porque muchas ofertas empiezan a medianoche. En verano todo va una hora más tarde. GitHub puede retrasar unos minutos las ejecuciones programadas.

**Mensajes:** solo escribe cuando hay algo que contar, nunca "no hay stock".
- **Un mensaje por tienda** con cada modelo disponible por ≤ 600 €: modelo, precio y enlace "Comprar".
- Se envía cuando aparece un modelo nuevo disponible en esa tienda o uno baja de precio (≥ 5 €); el mensaje incluye todos los modelos disponibles en ese momento.
- Si sigue disponible, te lo recuerda cada 12 h (`realert_hours`).
- Si una tienda no se puede leer durante 8 ejecuciones seguidas (por ejemplo, porque bloquea a GitHub), te llega **un** aviso ⚠️ al mismo canal.

---

## Puesta en marcha (10 minutos)

### 1. Crea el webhook de Discord
1. En tu servidor de Discord, crea (o elige) un canal, por ejemplo `#stock-ps5`.
2. **Editar canal (⚙️) → Integraciones → Webhooks → Nuevo webhook**. Ponle nombre (p. ej. `Vigilante PS5`).
3. Pulsa **Copiar URL del webhook** (empieza por `https://discord.com/api/webhooks/...`). Trátala como una contraseña: quien la tenga puede escribir en ese canal.
4. Para que te **suene en el móvil**: en ese canal, *Ajustes de notificación → Todos los mensajes*. O usa el secreto opcional `DISCORD_MENTION` (paso 3) para que cada aviso te mencione.

### 2. Crea el repositorio en GitHub
1. En GitHub: **New repository** (por ejemplo `ps5-stock-bot`).
   - **Público** → minutos de Actions gratis e ilimitados (recomendado; no contiene datos personales, los secretos no se ven).
   - **Privado** → 2.000 min/mes gratis; este bot gasta unos 4–6 min por ejecución (~1.200–1.800 min/mes). Si te quedas corto, reduce el horario en `.github/workflows/ps5-stock.yml`.
2. Sube **todos** los archivos de esta carpeta (incluida la carpeta oculta `.github`).
   - Desde la web: *Add file → Upload files* y arrastra el contenido de la carpeta.
   - O con git: `git init && git add . && git commit -m "Bot PS5" && git branch -M main && git remote add origin <url> && git push -u origin main`.

### 3. Añade los secretos
En el repo: **Settings → Secrets and variables → Actions → New repository secret**
- `DISCORD_WEBHOOK_URL` → la URL del webhook del paso 1
- `DISCORD_MENTION` *(opcional)* → `@everyone`, o `<@TU_ID_DE_USUARIO>` para mencionarte solo a ti
  (tu ID: Ajustes de usuario → Avanzado → activa *Modo desarrollador*; luego clic derecho en tu nombre → *Copiar ID de usuario*)

### 4. Permisos y prueba
1. **Settings → Actions → General → Workflow permissions** → marca **Read and write permissions** (el bot guarda `state.json` para no repetir avisos).
2. Pestaña **Actions** → *Vigilar stock PS5* → **Run workflow** → marca **"Enviar solo un aviso de prueba a Discord"** → debe aparecer un mensaje ✅ en el canal.
3. Vuelve a lanzarlo **sin** marcar la casilla: hará una revisión completa. Abre la ejecución y mira el **Summary**: verás una tabla con cada tienda, cada consola encontrada, su precio y si hay stock.

A partir de ahí funciona solo.

---

## Ajustes (`config.yaml`)

- `max_price: 600` → cambia el límite de precio (también puedes poner `max_price` en una tienda concreta).
- `realert_hours: 12` → cada cuánto te lo recuerda si sigue disponible (0 = nunca).
- `enabled: false` en una tienda → la desactiva.
- Para vigilar una ficha concreta, añádela en `products:` de su tienda.

Horario: edita las líneas `cron:` de `.github/workflows/ps5-stock.yml` (están en hora UTC).

## Probarlo en tu ordenador (opcional)

```bash
pip install -r requirements.txt
python -m playwright install chromium
python check_stock.py --dry-run            # no envía nada, imprime el informe
python check_stock.py --dry-run --only eci,game
python tests/test_rules.py                 # pruebas de las reglas de filtrado
```

## Limitaciones a tener en cuenta

- Las tiendas pueden **bloquear** las IPs de GitHub o cambiar su web. El bot lo detecta y te avisa en vez de fallar en silencio; el *Summary* de cada ejecución muestra qué tienda falló.
- Las reposiciones de PS5 a veces duran **minutos**. Con ejecuciones cada 2 h puede escaparse alguna; Chollometro ayuda a cubrir esos huecos.
- En AliExpress y Chollometro el precio puede depender de **cupones**; abre el enlace y comprueba vendedor y condiciones.
- GitHub desactiva los workflows programados de repos **sin actividad durante 60 días**; el bot escribe un "latido" diario en `state.json` para evitarlo.
