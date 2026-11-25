#!/usr/bin/env python3
"""Vigila el stock de PS5 nuevas en tiendas españolas y avisa por Discord.

Uso:
    python check_stock.py                 # ejecución normal
    python check_stock.py --dry-run       # no envía nada a Telegram, solo informa
    python check_stock.py --only eci,game # solo algunas tiendas
    python check_stock.py --test          # envía un aviso de prueba y sale

Variables de entorno:
    DISCORD_WEBHOOK_URL                    webhook del canal de Discord donde avisar
    DISCORD_MENTION                        (opcional) p. ej. "@everyone" o "<@TU_ID>" para que suene
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   (opcional) también avisar por Telegram
    PLAYWRIGHT_CHANNEL                     (opcional) p. ej. "chrome" para usar el Chrome instalado
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import html
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests
import yaml

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"
STATE_PATH = Path(os.environ.get("PS5_STATE_PATH", ROOT / "state.json"))
EXTRACT_JS = (ROOT / "extract.js").read_text(encoding="utf-8")

TZ = dt.timezone(dt.timedelta(hours=1))  # solo para mostrar horas; el estado usa epoch
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# --------------------------------------------------------------------------- reglas

USED_RE = re.compile(
    r"seminuev|reacondicionad|segunda mano|\busad[oa]s?\b|renewed|como nuevo|outlet|caja abierta|open box",
    re.I,
)
NOT_CONSOLE_RE = re.compile(
    r"\bmini\b|retro|ps4|ps3|ps2|playstation\s*[234]\b|portal|remote player|\bvr2?\b|switch|xbox",
    re.I,
)
# Ofertas ligadas a contratos (p. ej. operadoras con permanencia): no son una compra normal
CONTRACT_RE = re.compile(r"permanencia|€\s*/\s*mes|/mes\b|clientes de|solo para clientes", re.I)
CONSOLE_START_RE = re.compile(
    r"^\W*(?:nuev[oa]\s+)?(?:pack\s+(?:de\s+)?)?(?:consola|console|videoconsola|edici[oó]n digital (?:de )?playstation\s*5|"
    r"(?:sony\s+)?playstation\s*5|(?:sony\s+)?ps5|playstation\s+consola)\b",
    re.I,
)
ACCESSORY_AFTER_RE = re.compile(
    r"^\W*(?:sony\s+)?(?:ps5|playstation\s*5)\s*[-–:|]?\s*(?:mando|dualsense|juego|soporte|funda|"
    r"cargador|estaci[oó]n|base|cascos|auriculares|ssd|disco|unidad|volante|cable|skin|placa)",
    re.I,
)
HARDWARE_HINT_RE = re.compile(
    r"consola|console|konsole|slim|digital|\bpro\b|chas+is|\d+\s?(?:gb|tb)\b|mandos?\b|dualsense|lector|disco|discos",
    re.I,
)
GAME_EDITION_RE = re.compile(
    r"edici[oó]n\s+(?:est[aá]ndar|deluxe|gold|ultimate|day one|coleccionista)|standard edition|day one",
    re.I,
)
MENTIONS_PS5_RE =re.compile(r"ps5|playstation\s*5|playstation consola", re.I)

AVAILABLE_LD = {"instock", "limitedavailability", "onlineonly", "instoreonly", "preorder", "presale", "backorder"}
UNAVAILABLE_LD = {"outofstock", "soldout", "discontinued"}


def _norm(t: str) -> str:
    return " ".join((t or "").replace("®", "").replace("™", "").split())


def is_new_console(title: str) -> bool:
    """¿El título describe una consola PS5 (sola o en pack) nueva?"""
    t = _norm(title)
    if not t or not MENTIONS_PS5_RE.search(t):
        return False
    if USED_RE.search(t) or NOT_CONSOLE_RE.search(t):
        return False
    if not CONSOLE_START_RE.search(t):
        return False
    if ACCESSORY_AFTER_RE.search(t):
        return False
    if not re.match(r"^\W*(?:nuev[oa]\s+)?(?:pack\b|consola|console|videoconsola|edici[oó]n digital)", t, re.I):
        # Empieza por "PS5"/"PlayStation 5": puede ser un juego ("PlayStation 5-MARVEL: Lobezno")
        if GAME_EDITION_RE.search(t) or not HARDWARE_HINT_RE.search(t):
            return False
    return True


LISTING_HINT_RE = re.compile(
    r"consola|console|konsole|playstation\s*5\s*(?:slim|digital|pro|est[aá]ndar|standard|\d|\+|con\b|edici)|"
    r"ps5\s*(?:slim|digital|pro|est[aá]ndar|standard|\+|con\b|edici)|chas+is",
    re.I,
)


def looks_like_console_listing(text: str) -> bool:
    """Filtro suave para enlaces de listados (el texto puede empezar por 'Agotado', '-7%', etc.)."""
    t = _norm(text)[:200]
    if not MENTIONS_PS5_RE.search(t) or USED_RE.search(t) or NOT_CONSOLE_RE.search(t):
        return False
    return bool(LISTING_HINT_RE.search(t))


def parse_price(text) -> float | None:
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    s = re.sub(r"\s+", "", str(text))
    s = re.sub(r",[–-]+", ",00", s)
    if "," not in s and "€" not in s:  # formato máquina: "599.90"
        try:
            return float(s)
        except ValueError:
            pass
    s = s.replace("€", "")
    m = re.search(r"(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{1,2}))?", s)
    if not m:
        return None
    return float(m.group(1).replace(".", "") + "." + (m.group(2) or "0"))


def normalize_url(url: str) -> str:
    if not url:
        return url
    m = re.search(r"amazon\.(es|fr|de|it)/(?:.*?/)?dp/([A-Z0-9]{10})", url)
    if m:
        return f"https://www.amazon.{m.group(1)}/dp/{m.group(2)}"
    m = re.search(r"aliexpress\.com/item/(\d+)\.html", url)
    if m:
        return f"https://es.aliexpress.com/item/{m.group(1)}.html"
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


# --------------------------------------------------------------------------- modelo


@dataclass
class Finding:
    store_id: str
    store: str
    title: str
    url: str
    price: float | None
    available: bool
    reason: str
    seller: str = ""
    note: str = ""
    uid: str = ""  # identificador alternativo cuando la tarjeta no tiene enlace propio

    @property
    def key(self) -> str:
        return f"{self.store_id}|{self.uid or normalize_url(self.url) or self.title}"


@dataclass
class StoreResult:
    store_id: str
    store: str
    ok: bool = True
    error: str = ""
    findings: list[Finding] = field(default_factory=list)
    checked: int = 0


def decide_product(data: dict, max_price: float, min_price: float, ignore_ld: bool = False) -> tuple[bool, float | None, str, str]:
    """Devuelve (disponible, precio, motivo, vendedor) a partir de lo extraído de una ficha.
    ignore_ld: la tienda publica una disponibilidad falsa en schema.org (p. ej. Sony) -> se usa lo visible."""
    ld = dict(data.get("ld") or {})
    if ignore_ld and ld.get("price"):
        data = {**data, "mainPrice": None}  # en Sony los precios de otros modelos confunden
    if ignore_ld:
        ld["availability"] = ""
    amazon = data.get("amazon")
    seller = ""

    prices = []
    if amazon:
        if amazon.get("price"):
            prices.append(float(amazon["price"]))
        seller = amazon.get("seller") or ""
    if ld.get("price"):
        prices.append(float(ld["price"]))
    if data.get("mainPrice"):
        prices.append(float(data["mainPrice"]))
    prices = [p for p in prices if p >= min_price]
    price = min(prices) if prices else None
    seller = seller or ld.get("seller") or ""

    availability = (ld.get("availability") or "").lower()
    negatives = data.get("negatives") or []

    if amazon:
        avail_txt = (amazon.get("availability") or "").lower()
        if amazon.get("addToCart") and not amazon.get("outOfStock") and "no disponible" not in avail_txt:
            return True, price, f"Amazon: botón de compra activo ({amazon.get('availability') or 'sin texto'})", seller
        return False, price, f"Amazon: {amazon.get('availability') or 'sin botón de compra'}", seller

    if availability in AVAILABLE_LD:
        return True, price, f"schema.org: {ld.get('availability')}", seller
    if availability in UNAVAILABLE_LD:
        return False, price, f"schema.org: {ld.get('availability')}", seller

    # Sin datos estructurados: botón de compra visible y ningún "agotado" en la zona de compra
    if data.get("buyButton") and not negatives:
        return True, price, "botón de compra visible", seller
    if negatives:
        return False, price, "página: " + "; ".join(negatives[:2]), seller
    return False, price, "sin botón de compra", seller


# --------------------------------------------------------------------------- navegador


async def new_page(context):
    page = await context.new_page()

    async def _route(route):
        if route.request.resource_type in {"image", "media", "font"}:
            await route.abort()
        else:
            await route.continue_()

    await page.route("**/*", _route)
    return page


async def goto(page, url: str, timeout_s: int, wait_s: float = 3.0):
    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_s * 1000)
    try:
        await page.wait_for_load_state("networkidle", timeout=min(12, timeout_s) * 1000)
    except Exception:
        pass
    await page.wait_for_timeout(int(wait_s * 1000))


async def extract(page, **args) -> dict:
    return await page.evaluate(EXTRACT_JS, args)


async def check_product_store(context, store: dict, cfg: dict, sem: asyncio.Semaphore) -> StoreResult:
    res = StoreResult(store["id"], store["name"])
    max_price = float(store.get("max_price", cfg["max_price"]))
    min_price = float(cfg.get("min_price", 250))
    timeout_s = int(cfg.get("page_timeout_s", 45))
    pattern = store.get("link_pattern", "")

    # 1) descubrir fichas
    urls: dict[str, str] = {normalize_url(u): "" for u in store.get("products", [])}
    discover_errors = []
    for durl in store.get("discover", []):
        async with sem:
            page = await new_page(context)
            try:
                await goto(page, durl, timeout_s, store.get("wait_s", 3))
                # baja un poco para que carguen resultados perezosos
                for _ in range(3):
                    await page.mouse.wheel(0, 2500)
                    await page.wait_for_timeout(600)
                data = await extract(page, mode="product", collectLinks=pattern)
                if data.get("blocked"):
                    discover_errors.append(f"bloqueado en {durl}")
                for link in data.get("links", []):
                    href = normalize_url(link["href"])
                    text = link.get("text", "")
                    # en los listados el texto del enlace suele incluir el título del producto
                    if text and not looks_like_console_listing(text):
                        continue
                    urls.setdefault(href, text)
            except Exception as e:  # noqa: BLE001
                discover_errors.append(f"{durl}: {type(e).__name__}: {str(e)[:120]}")
            finally:
                await page.close()

    if not urls:
        res.ok = False
        res.error = "; ".join(discover_errors) or "no se encontró ninguna ficha"
        return res

    # 2) revisar cada ficha
    limit = int(cfg.get("max_products_per_store", 20))
    page_errors = []

    async def check_one(url: str):
        async with sem:
            page = await new_page(context)
            try:
                await goto(page, url, timeout_s, store.get("wait_s", 3))
                data = await extract(page, mode="product")
            except Exception as e:  # noqa: BLE001
                page_errors.append(f"{url}: {type(e).__name__}")
                return
            finally:
                await page.close()
        if data.get("blocked"):
            page_errors.append(f"bloqueado: {url}")
            return
        title = data.get("title") or urls.get(url) or url
        res.checked += 1
        if not is_new_console(title):
            return
        if data.get("usedBadge"):
            res.findings.append(Finding(res.store_id, res.store, title, url, None, False, "es seminueva/reacondicionada"))
            return
        available, price, reason, seller = decide_product(data, max_price, min_price, bool(store.get("ignore_ld")))
        res.findings.append(Finding(res.store_id, res.store, title, url, price, available, reason, seller, store.get("note", "")))

    await asyncio.gather(*(check_one(u) for u in list(urls)[:limit]))

    if res.checked == 0:
        res.ok = False
        res.error = "; ".join((discover_errors + page_errors)[:3]) or "no se pudo leer ninguna ficha"
    elif page_errors or discover_errors:
        res.error = f"{len(page_errors) + len(discover_errors)} páginas con error"
    return res


async def check_cards_store(context, store: dict, cfg: dict, sem: asyncio.Semaphore) -> StoreResult:
    res = StoreResult(store["id"], store["name"])
    max_price = float(store.get("max_price", cfg["max_price"]))
    min_price = float(cfg.get("min_price", 250))
    is_chollo = store.get("mode") == "chollometro"
    urls = store.get("urls") or [store["url"]]
    seen = set()
    errors = []
    for url in urls:
        async with sem:
            page = await new_page(context)
            try:
                await goto(page, url, int(cfg.get("page_timeout_s", 45)), store.get("wait_s", 4))
                for _ in range(4):
                    await page.mouse.wheel(0, 2500)
                    await page.wait_for_timeout(700)
                data = await extract(
                    page,
                    mode="cards",
                    cardSelector=store.get("card_selector") or ("article" if is_chollo else None),
                    titleSelector=store.get("title_selector") or ("[class*=title]" if is_chollo else None),
                    linkSelector='a[href*="/ofertas/"]' if is_chollo else None,
                )
            except Exception as e:  # noqa: BLE001
                errors.append(f"{url}: {type(e).__name__}: {str(e)[:120]}")
                continue
            finally:
                await page.close()
        if data.get("blocked"):
            errors.append(f"bloqueado: {url}")
            continue
        if not data.get("cards"):
            if store.get("empty_ok"):
                res.checked += 1  # la página se leyó; simplemente no hay ninguna PS5 a la venta
            else:
                errors.append(f"sin resultados en {url}")
            continue
        res.checked += len(data["cards"])
        for c in data["cards"]:
            title = c.get("title") or ""
            text = c.get("text") or ""
            if not is_new_console(title):
                continue
            key = (normalize_url(c.get("href") or "") or title)
            if key in seen:
                continue
            seen.add(key)
            price = c.get("price")
            if price is not None and price < min_price:
                continue
            link = c.get("href") or url
            if is_chollo:
                # solo chollos reales (enlace /ofertas/), no las fichas comparadoras "X ofertas desde..."
                if "/ofertas/" not in (c.get("href") or ""):
                    continue
                expired = re.search(r"Termin[oó]|expir[oó]|caducad|Expirado", text, re.I)
                if CONTRACT_RE.search(text):
                    continue
                available = not expired
                reason = "chollo activo" if available else "chollo caducado"
                seller = c.get("store") or ""
            else:
                out = re.search(r"agotad|no disponible|sin stock|próximamente|avísame", text, re.I)
                available = not out
                reason = "aparece en el listado" if available else "listado: agotado"
                seller = c.get("seller") or ""
            uid = "" if c.get("href") else re.sub(r"\W+", " ", title.lower()).strip()[:120]
            res.findings.append(Finding(res.store_id, res.store, title, link, price, available, reason, seller,
                                        store.get("note", ""), uid))

    if res.checked == 0:
        res.ok = False
        res.error = "; ".join(errors[:3]) or "sin datos"
    elif errors:
        res.error = "; ".join(errors[:2])
    return res


async def run_checks(cfg: dict, only: set[str] | None) -> list[StoreResult]:
    from playwright.async_api import async_playwright

    group = os.environ.get("PS5_GROUP", "").strip()
    stores = [s for s in cfg["stores"] if s.get("enabled", True) and (not only or s["id"] in only)
              and (only or not group or s.get("group", "cloud") == group)]  # con --only se ignora el grupo (pruebas)
    sem = asyncio.Semaphore(int(cfg.get("concurrency", 4)))
    async with async_playwright() as pw:
        launch_args = {
            "headless": True,
            "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        }
        if os.environ.get("PLAYWRIGHT_CHANNEL"):
            launch_args["channel"] = os.environ["PLAYWRIGHT_CHANNEL"]
        browser = await pw.chromium.launch(**launch_args)
        context = await browser.new_context(
            user_agent=USER_AGENT,
            locale="es-ES",
            timezone_id="Europe/Madrid",
            viewport={"width": 1400, "height": 900},
            extra_http_headers={"Accept-Language": "es-ES,es;q=0.9"},
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            "Object.defineProperty(navigator, 'languages', {get: () => ['es-ES','es']});"
        )

        async def run_store(store):
            started = time.time()
            try:
                if store.get("mode", "product") == "product":
                    r = await check_product_store(context, store, cfg, sem)
                else:
                    r = await check_cards_store(context, store, cfg, sem)
            except Exception as e:  # noqa: BLE001
                r = StoreResult(store["id"], store["name"], ok=False, error=f"{type(e).__name__}: {str(e)[:150]}")
            print(f"[{store['name']}] {'OK' if r.ok else 'FALLO'} · {r.checked} revisados · "
                  f"{len(r.findings)} consolas · {time.time() - started:.0f}s {r.error}", flush=True)
            return r

        results = await asyncio.gather(*(run_store(s) for s in stores))
        await context.close()
        await browser.close()
    return list(results)


# --------------------------------------------------------------------------- avisos y estado


def fmt_price(p: float | None) -> str:
    if p is None:
        return "¿?"
    return f"{p:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


# Un aviso = dict con: title, description, url (opcional), fields [(nombre, valor)], footer, color
COLOR_OK, COLOR_WARN, COLOR_INFO = 0x2ECC71, 0xF39C12, 0x3498DB


def _discord_base(webhook: str) -> str:
    return webhook.split("?")[0].rstrip("/")


def _discord_call(method: str, url: str, **kw):
    for _ in range(4):
        r = requests.request(method, url, timeout=20, **kw)
        if r.status_code == 429:  # límite de Discord: esperar lo que pide y reintentar
            try:
                wait = float(r.json().get("retry_after", 2))
            except ValueError:
                wait = 2
            time.sleep(min(wait, 30))
            continue
        return r
    return r


def _send_discord(webhook: str, n: dict, edit_id: str | None = None) -> str:
    """Publica (o edita, si edit_id) un mensaje. Devuelve el id del mensaje o "" si falla."""
    embed = {
        "title": n["title"][:256],
        "description": n.get("description", "")[:4000],
        "color": n.get("color", COLOR_INFO),
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    if n.get("url"):
        embed["url"] = n["url"]
    if n.get("fields"):
        embed["fields"] = [{"name": k[:256], "value": (v or "—")[:1024], "inline": True} for k, v in n["fields"]]
    if n.get("footer"):
        embed["footer"] = {"text": n["footer"][:2048]}
    payload = {"embeds": [embed]}
    if not edit_id:
        payload["username"] = "Vigilante PS5"
        mention = os.environ.get("DISCORD_MENTION", "").strip()
        if mention and n.get("ping", True):
            payload["content"] = mention
            payload["allowed_mentions"] = {"parse": ["everyone", "users", "roles"]}
    base = _discord_base(webhook)
    if edit_id:
        r = _discord_call("PATCH", f"{base}/messages/{edit_id}", json=payload)
    else:
        r = _discord_call("POST", f"{base}?wait=true", json=payload)
    if not r.ok:
        if r.status_code != 404:
            print(f"Error de Discord {r.status_code}: {r.text[:300]}", file=sys.stderr)
        return ""
    try:
        return str(r.json().get("id", "")) or "ok"
    except ValueError:
        return "ok"


def delete_discord_message(msg_id: str, dry_run: bool = False) -> bool:
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if dry_run or not webhook or not msg_id or msg_id == "ok":
        if dry_run:
            print(f"---- (se borraría el mensaje {msg_id}) ----")
        return False
    r = _discord_call("DELETE", f"{_discord_base(webhook)}/messages/{msg_id}")
    return r.ok or r.status_code == 404


def upsert_discord_message(msg_id: str | None, n: dict, dry_run: bool = False) -> str:
    """Edita el mensaje si existe; si no (o lo borraste), crea uno nuevo. Devuelve su id."""
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if dry_run or not webhook:
        print(f"---- (mensaje de estado {'editado' if msg_id else 'nuevo'}) ----\n{n['title']}\n{n.get('description','')}")
        return msg_id or ""
    if msg_id and msg_id != "ok":
        got = _send_discord(webhook, n, edit_id=msg_id)
        if got:
            return msg_id
    return _send_discord(webhook, n)


def _send_telegram(token: str, chat_id: str, n: dict) -> bool:
    lines = [f"<b>{html.escape(n['title'])}</b>"]
    if n.get("items"):
        for title, price, url in n["items"]:
            lines.append(f"• {html.escape(title[:110])}\n  <b>{html.escape(price)}</b> · {html.escape(url)}")
    elif n.get("description"):
        lines.append(html.escape(n["description"]))
    lines += [f"{html.escape(k)}: <b>{html.escape(v or '—')}</b>" for k, v in n.get("fields", [])]
    if n.get("url"):
        lines.append(html.escape(n["url"]))
    if n.get("footer"):
        lines.append(f"<i>{html.escape(n['footer'])}</i>")
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": "\n".join(lines), "parse_mode": "HTML"},
        timeout=20,
    )
    if not r.ok:
        print(f"Error de Telegram {r.status_code}: {r.text[:300]}", file=sys.stderr)
    return r.ok


def send_notice(n: dict, dry_run: bool = False):
    """Devuelve el id del mensaje de Discord (o True si se envió solo por Telegram); falso si falla."""
    """Envía el aviso a Discord (y a Telegram si también está configurado)."""
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if dry_run or not (webhook or (tg_token and tg_chat)):
        print("---- (aviso no enviado) ----")
        print(n["title"])
        for title, price, url in n.get("items", []):
            print(f"  · {title[:80]} | {price} | {url}")
        for k, v in n.get("fields", []):
            print(f"  {k}: {v}")
        if n.get("url") and not n.get("items"):
            print("  " + n["url"])
        print("----------------------------")
        return dry_run
    ok = False
    if tg_token and tg_chat:
        ok = _send_telegram(tg_token, tg_chat, n) or ok
    if webhook:
        ok = _send_discord(webhook, n) or ok
    return ok


def store_notice(store: str, hits: list[Finding], max_price: float = 600) -> dict:
    """Un único mensaje por tienda con todos los modelos disponibles: modelo, precio y enlace."""
    hits = sorted(hits, key=lambda f: f.price or 9e9)[:15]
    lines = []
    for f in hits:
        line = f"**{f.title[:110]}**\n💶 **{fmt_price(f.price)}**"
        if f.seller and f.seller.lower() not in store.lower():
            line += f" · vendido por {f.seller[:40]}"
        if f.price is not None and f.price > max_price:
            line += " ⚠️ por encima de tu límite"
        line += f" · [Comprar]({f.url})"
        lines.append(line)
    notes = {f.note for f in hits if f.note}
    desc = "\n\n".join(lines)
    if notes:
        desc += "\n\nℹ️ " + " ".join(notes)
    n = "1 modelo" if len(hits) == 1 else f"{len(hits)} modelos"
    return {
        "title": f"🎮 PS5 disponible en {store} ({n})",
        "description": desc,
        "url": hits[0].url,
        "items": [(f.title, fmt_price(f.price), f.url) for f in hits],
        "color": COLOR_OK,
    }


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def save_state(state: dict):
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def process(results: list[StoreResult], cfg: dict, state: dict, dry_run: bool) -> tuple[list[Finding], list[str]]:
    now = time.time()
    items = state.setdefault("items", {})
    health = state.setdefault("health", {})
    realert_s = float(cfg.get("realert_hours", 12)) * 3600
    sent: list[Finding] = []
    notes: list[str] = []

    for r in results:
        # salud de cada tienda
        h = health.setdefault(r.store_id, {"fails": 0, "warned": False})
        if r.ok:
            if h.get("warned"):
                send_notice({"title": f"✅ Vuelvo a poder leer {r.store}", "color": COLOR_INFO, "ping": False}, dry_run)
            health[r.store_id] = {"fails": 0, "warned": False}
        else:
            h["fails"] = h.get("fails", 0) + 1
            h["last_error"] = r.error[:200]
            if h["fails"] >= int(cfg.get("health_alert_after", 8)) and not h.get("warned"):
                send_notice({
                    "title": f"⚠️ No consigo leer {r.store}",
                    "description": f"Llevo {h['fails']} comprobaciones seguidas fallando "
                                   "(probablemente bloquea a GitHub Actions).",
                    "footer": r.error[:200],
                    "color": COLOR_WARN,
                    "ping": False,
                }, dry_run)
                h["warned"] = True

        hits: list[Finding] = []
        trigger = False
        store_cfg = next((s for s in cfg["stores"] if s["id"] == r.store_id), {})
        watch_all = bool(store_cfg.get("watch_all"))
        for f in r.findings:
            max_price = float(store_cfg.get("max_price", cfg["max_price"]))
            ok_price = f.price is not None and (f.price <= max_price or watch_all)
            hit = f.available and ok_price
            prev = items.get(f.key, {})
            items[f.key] = {
                "title": f.title[:150], "price": f.price, "available": f.available,
                "hit": hit, "last_seen": now, "last_alert": prev.get("last_alert", 0),
            }
            if hit and f.key not in {h.key for h in hits}:
                hits.append(f)
                was_hit = prev.get("hit", False)
                if watch_all:  # tiendas vigiladas a cualquier precio: avisa si cambia el precio, sin recordatorios
                    cheaper = prev.get("price") is not None and f.price is not None and abs(f.price - prev["price"]) >= 1
                    stale = False
                else:
                    cheaper = prev.get("price") and f.price is not None and f.price < prev["price"] - 4.99
                    stale = realert_s > 0 and now - prev.get("last_alert", 0) >= realert_s
                trigger = trigger or not was_hit or bool(cheaper) or stale
        # Un solo mensaje por tienda, solo si hay algo nuevo, más barato o toca recordatorio
        alert_msgs = state.setdefault("alert_msgs", {})
        if hits and trigger:
            # borra el aviso anterior de esta tienda y publica uno nuevo (para que vuelva a sonar)
            if alert_msgs.get(r.store_id):
                delete_discord_message(alert_msgs.pop(r.store_id), dry_run)
            res_id = send_notice(store_notice(r.store, hits, float(store_cfg.get("max_price", cfg["max_price"]))), dry_run)
            if res_id:
                if isinstance(res_id, str):
                    alert_msgs[r.store_id] = res_id
                for f in hits:
                    items[f.key]["last_alert"] = now
                sent.extend(hits)
        elif not hits and r.ok and alert_msgs.get(r.store_id):
            # ya no queda stock en esa tienda: borramos su aviso para que el canal no engañe
            delete_discord_message(alert_msgs.pop(r.store_id), dry_run)

    # olvida productos que no vemos desde hace 14 días
    for k in [k for k, v in items.items() if now - v.get("last_seen", 0) > 14 * 86400]:
        del items[k]
    # latido diario: mantiene el repo "activo" para que GitHub no desactive el cron
    state["heartbeat"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    return sent, notes


def status_notice(results: list[StoreResult], sent: list[Finding]) -> dict:
    """Mensaje fijo que se edita en cada revisión: última comprobación y estado de cada tienda."""
    group = os.environ.get("PS5_GROUP", "")
    where = {"cloud": "GitHub", "home": "mi PC"}.get(group, "bot")
    now = madrid_now()
    lines = [f"🕒 **Última comprobación:** {now:%d/%m/%Y %H:%M}", ""]
    for r in results:
        hits = [f for f in r.findings if f.available and f.price is not None and f.price <= 600]
        if not r.ok:
            lines.append(f"❌ {r.store}: no se pudo leer")
        elif hits:
            lines.append(f"🟢 {r.store}: ¡{len(hits)} con stock!")
        else:
            lines.append(f"✅ {r.store}: {r.checked} revisadas, sin stock")
    all_bad = all(not r.ok for r in results) if results else True
    return {
        "title": f"{'🔴' if all_bad else '🟢'} Vigilante PS5 ({where}) funcionando",
        "description": "\n".join(lines),
        "color": COLOR_WARN if all_bad else COLOR_INFO,
        "ping": False,
    }


def madrid_now() -> dt.datetime:
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("Europe/Madrid"))
    except Exception:  # noqa: BLE001  (Windows sin tzdata)
        return dt.datetime.now(TZ)


def write_summary(results: list[StoreResult], sent: list[Finding], cfg: dict):
    lines = [f"## Stock PS5 · {dt.datetime.now(TZ).strftime('%d/%m/%Y %H:%M')} (hora peninsular aprox.)", ""]
    if sent:
        lines.append(f"### 🔔 Avisos enviados: {len(sent)}")
        for f in sent:
            lines.append(f"- **{f.store}** · [{f.title[:90]}]({f.url}) · {fmt_price(f.price)}")
        lines.append("")
    lines += ["| Tienda | Estado | Revisados |", "|---|---|---|"]
    for r in results:
        lines.append(f"| {r.store} | {'✅' if r.ok else '❌ ' + r.error[:120]} | {r.checked} |")
    lines += ["", "### Consolas encontradas", "", "| Tienda | Producto | Precio | ¿Disponible? | Motivo |", "|---|---|---|---|---|"]
    for r in results:
        for f in sorted(r.findings, key=lambda x: (not x.available, x.price or 9e9)):
            mark = "🟢 sí" if f.available else "🔴 no"
            title = f.title[:80].replace("|", "/")
            lines.append(f"| {f.store} | [{title}]({f.url}) | {fmt_price(f.price)} | {mark} | {f.reason[:70].replace('|', '/')} |")
    text = "\n".join(lines) + "\n"
    out = os.environ.get("GITHUB_STEP_SUMMARY")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(text)
    else:
        print(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="no envía avisos, solo informa")
    ap.add_argument("--only", default="", help="ids de tiendas separados por comas")
    ap.add_argument("--test", "--test-telegram", dest="test", action="store_true", help="envía un aviso de prueba y sale")
    ap.add_argument("--config", default=str(CONFIG_PATH))
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))

    if args.test or os.environ.get("TEST_NOTICE", "").lower() == "true":
        ok = send_notice({
            "title": "✅ Vigilante de PS5 configurado",
            "description": "Te avisaré aquí cuando haya una PS5 nueva disponible por "
                           f"{fmt_price(float(cfg['max_price']))} o menos.",
            "color": COLOR_INFO,
        })
        print("Mensaje de prueba enviado" if ok else "No se pudo enviar el mensaje de prueba")
        sys.exit(0 if ok else 1)

    only = {s.strip() for s in args.only.split(",") if s.strip()} or None
    results = asyncio.run(run_checks(cfg, only))
    state = load_state()
    sent, _ = process(results, cfg, state, args.dry_run)
    if not only:  # las pruebas con --only no tocan el mensaje de estado
      state["status_msg"] = upsert_discord_message(state.get("status_msg"), status_notice(results, sent), args.dry_run) or state.get("status_msg")
    save_state(state)
    write_summary(results, sent, cfg)
    print(f"Avisos enviados: {len(sent)}")


if __name__ == "__main__":
    main()
