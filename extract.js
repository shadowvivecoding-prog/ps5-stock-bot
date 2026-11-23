// Extractor que se ejecuta DENTRO de la página (Playwright page.evaluate).
// Devuelve datos crudos; las decisiones (¿hay stock? ¿es consola? ¿precio OK?)
// se toman en Python (check_stock.py).
(args) => {
  args = args || {};
  const mode = args.mode || 'product';
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const PRICE_RE = /(\d{1,3}(?:[.\s]\d{3})+|\d+)(?:[.,](\d{1,2}))?\s*(?:€|EUR)/i;

  const parsePrice = (txt) => {
    if (txt == null) return null;
    if (typeof txt === 'number') return txt;
    const s = String(txt).replace(/\s+/g, '').replace(/,[–-]+/, ',00');
    // "1.108,13€" | "599,99€" | "599.90" | "1108,\n13€"
    let m = s.match(/(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{1,2}))?€?/);
    if (s.includes(',') || s.includes('€')) {
      if (!m) return null;
      return parseFloat(m[1].replace(/\./g, '') + '.' + (m[2] || '0'));
    }
    const f = parseFloat(s);
    return isNaN(f) ? null : f;
  };

  const visible = (e) => {
    if (!e || !e.getBoundingClientRect) return false;
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const cs = getComputedStyle(e);
    return cs.visibility !== 'hidden' && cs.display !== 'none' && parseFloat(cs.opacity || '1') > 0.05;
  };
  const absTop = (e) => e.getBoundingClientRect().top + window.scrollY;

  const bodyText = clean(document.body ? document.body.innerText : '').slice(0, 4000);
  const blocked = /captcha|no eres un robot|not a robot|are you a human|access denied|acceso denegado|attention required|just a moment|introduce los caracteres|request unsuccessful|verifica que eres|slide right to secure|unusual traffic|pardon our interruption|checking your browser|cf-browser-verification/i
    .test(document.title + ' ' + bodyText.slice(0, 1500)) ||
    /un momento|verificando|security check|comprobación de seguridad/i.test(document.title);

  // ------------------------------------------------------------------ CARDS
  if (mode === 'cards') {
    const sel = args.cardSelector;
    // Algunas webs (Carrefour) pintan los resultados dentro de un shadow DOM
    const roots = [document];
    const addRoots = (r) => r.querySelectorAll('*').forEach((e) => { if (e.shadowRoot) { roots.push(e.shadowRoot); addRoots(e.shadowRoot); } });
    addRoots(document);
    const qsa = (q) => roots.flatMap((r) => [...r.querySelectorAll(q)]);
    let cards = [];
    if (sel) {
      cards = qsa(sel);
    } else {
      // Tarjetas genéricas: el elemento más pequeño que contiene un precio y "PS5/PlayStation 5"
      const cand = qsa('article, li, div').filter((d) => {
        const t = d.innerText || '';
        return t.length > 25 && t.length < 700 && /€/.test(t) && /(ps5|playstation\s*5)/i.test(t);
      });
      const minimal = cand.filter((d) => !cand.some((o) => o !== d && d.contains(o)));
      // Ampliamos cada tarjeta hasta su contenedor más grande que no incluya otra tarjeta
      // (así entran los botones "Añadir" / "Agotado" que a veces están al lado)
      cards = minimal.map((c) => {
        let p = c;
        while (p.parentElement && (p.parentElement.innerText || '').length < 1200 &&
               !minimal.some((o) => o !== c && p.parentElement.contains(o))) p = p.parentElement;
        return p;
      });
    }
    const out = cards.slice(0, 120).map((c) => {
      const text = clean(c.innerText);
      const titleEl = args.titleSelector ? c.querySelector(args.titleSelector) : null;
      let title = clean(titleEl ? titleEl.innerText : '');
      if (!title) {
        const lines = (c.innerText || '').split('\n').map(clean).filter(Boolean);
        title = lines.find((l) => /(ps5|playstation\s*5)/i.test(l) && l.length > 8) || lines[0] || '';
      }
      const priceM = text.match(PRICE_RE);
      let href = '';
      if (c.tagName === 'A') href = c.href;
      else {
        const a = (args.linkSelector && c.querySelector(args.linkSelector)) || c.querySelector('a[href]');
        href = a ? a.href : '';
      }
      const seller = (text.match(/Vendido (?:y enviado )?por:?\s*([^|·€]{2,40}?)(?=\s(?:Oferta|Más ofertas|Añadir|Agotad|Envío|Precio|\d)|$)/i) || [])[1] || '';
      const store = (text.match(/Disponible en\s+(.+?)\s+(?:Compartido|Publicado|Editado|$)/i) || [])[1] || '';
      return {
        title: title.slice(0, 200),
        priceText: priceM ? priceM[0] : '',
        price: priceM ? parsePrice(priceM[0]) : null,
        href,
        seller: clean(seller),
        store: clean(store),
        text: text.slice(0, 600),
      };
    });
    return { mode, url: location.href, pageTitle: document.title, blocked, count: out.length, cards: out };
  }

  // ---------------------------------------------------------------- PRODUCT
  // 1) JSON-LD (schema.org Product / Offer)
  const products = [];
  const seen = new Set();
  const walk = (d, depth) => {
    if (!d || typeof d !== 'object' || depth > 10 || seen.has(d)) return;
    seen.add(d);
    if (Array.isArray(d)) { d.forEach((x) => walk(x, depth + 1)); return; }
    const t = [].concat(d['@type'] || []).join(',');
    if (/(^|,)Product(,|$)/i.test(t)) {
      const offers = [].concat(d.offers || []).flatMap((o) => (o && o.offers ? [].concat(o.offers) : [o])).filter(Boolean);
      products.push({
        name: d.name || '',
        url: d.url || '',
        offers: offers.map((o) => ({
          price: parsePrice(o.price != null ? String(o.price) : (o.lowPrice != null ? String(o.lowPrice) : null)),
          availability: String(o.availability || '').split('/').pop(),
          seller: (o.seller && (o.seller.name || (typeof o.seller === 'string' ? o.seller : ''))) || '',
          condition: String(o.itemCondition || d.itemCondition || '').split('/').pop(),
          url: o.url || '',
        })),
      });
    }
    Object.values(d).forEach((v) => walk(v, depth + 1));
  };
  document.querySelectorAll('script[type="application/ld+json"]').forEach((s) => {
    try { walk(JSON.parse(s.textContent), 0); } catch (e) { /* ignore */ }
  });

  const here = location.href.split(/[?#]/)[0].replace(/\/$/, '');
  const sameUrl = (u) => u && u.split(/[?#]/)[0].replace(/\/$/, '') === here;
  // Elegimos la oferta de ESTA página (ProductGroup con variantes → la variante cuya URL coincide)
  let ld = null;
  for (const p of products) {
    for (const o of p.offers) {
      if (sameUrl(o.url) || sameUrl(p.url)) { ld = { name: p.name, ...o }; break; }
    }
    if (ld) break;
  }
  if (!ld) {
    const p = products.find((x) => x.offers.length);
    if (p) ld = { name: p.name, ...p.offers[0] };
  }

  // 2) Título
  const h1 = document.querySelector('#productTitle, h1');
  const title = clean(h1 ? h1.innerText : '') || (ld && ld.name) || document.title;
  const h1Top = h1 && visible(h1) ? absTop(h1) : 0;
  const zoneBottom = Math.max(h1Top, 0) + 1100; // "zona de compra" = primera pantalla y media

  // 3) Precio principal = el precio visible con la letra más grande en la zona de compra
  let mainPrice = null;
  let bestSize = 0;
  document.querySelectorAll('span, div, p, strong, b, ins, bdi').forEach((e) => {
    const raw = (e.innerText || '');
    if (raw.length > 24 || !/\d/.test(raw)) return;
    const txt = raw.replace(/\s+/g, '').replace(/,[–-]+/, ',00');
    if (!/^\D{0,4}\d{1,4}(?:\.\d{3})*(?:,\d{0,2})?€?$/.test(txt) && !/^\d+,?€?$/.test(txt)) return;
    if (!/€/.test(txt) && !/€/.test((e.parentElement && e.parentElement.innerText) || '')) return;
    if (!visible(e)) return;
    const top = absTop(e);
    if (top > zoneBottom) return;
    const OLD = 's, del, strike, [class*="old"], [class*="before"], [class*="strike"], [class*="pvp"], [class*="crossed"], [class*="Strike"], [class*="Old"]';
    if (e.closest(OLD) || e.querySelector(OLD)) return;
    if (/line-through/.test(getComputedStyle(e).textDecorationLine || '')) return;
    let size = parseFloat(getComputedStyle(e).fontSize) || 0;
    e.querySelectorAll('*').forEach((c) => { size = Math.max(size, parseFloat(getComputedStyle(c).fontSize) || 0); });
    const val = parsePrice(txt.includes('€') ? txt : txt + '€');
    // a igual tamaño de letra, preferimos el texto más completo ("579,99€" mejor que "579,")
    const score = size * 100 + Math.min(txt.length, 20);
    if (val && score > bestSize) { bestSize = score; mainPrice = val; }
  });

  // 4) Botones de compra y avisos de "agotado" en la zona de compra
  const BUY_RE = /^(añadir( al carrito| a la cesta| a cesta)?|comprar( ya| ahora)?|add to (cart|basket)|buy now|reservar( ahora)?)$/i;
  const NEG_RE = /(agotad|no disponible|avísame|sin stock|fuera de stock|próximamente|no está disponible|currently unavailable|no hay stock)/i;
  let buyButton = false;
  const negatives = [];
  document.querySelectorAll('button, a, input[type="submit"], [role="button"], span, div, p').forEach((e) => {
    const isBtn = /^(BUTTON|A|INPUT)$/.test(e.tagName) || e.getAttribute('role') === 'button';
    const t = clean(e.innerText || e.value || '');
    if (!t || t.length > 60) return;
    if (!visible(e)) return;
    const top = absTop(e);
    if (top > zoneBottom) return;
    if (e.closest('nav, header, footer, [class*="carousel"], [class*="slider"], [class*="recommend"], [class*="related"]')) return;
    if (isBtn && BUY_RE.test(t) && !e.disabled && e.getAttribute('aria-disabled') !== 'true') buyButton = true;
    if (NEG_RE.test(t) && e.children.length <= 3) negatives.push(t);
  });

  // 5) ¿Es de segunda mano? (insignias tipo "Seminuevo" fuera de menús)
  let usedBadge = false;
  document.querySelectorAll('span, div, p, strong, li, small').forEach((e) => {
    if (e.children.length > 1) return;
    const t = clean(e.innerText);
    if (!/^(seminuevo|seminueva|reacondicionad[oa]|segunda mano|usad[oa]|como nuevo|renewed|reacondicionado certificado)$/i.test(t)) return;
    if (!visible(e) || absTop(e) > zoneBottom) return;
    if (e.closest('nav, header, footer, a, [class*="menu"], [class*="filter"]')) return;
    usedBadge = true;
  });

  // 6) Amazon: tiene su propia estructura
  let amazon = null;
  if (/(^|\.)amazon\./.test(location.hostname)) {
    const q = (s) => { const el = document.querySelector(s); return el ? clean(el.innerText || el.textContent) : ''; };
    amazon = {
      price: parsePrice(q('#corePrice_feature_div .a-offscreen') || q('#corePriceDisplay_desktop_feature_div .a-offscreen') || q('#apex_desktop .a-price .a-offscreen') || q('#price_inside_buybox')),
      addToCart: !!document.querySelector('#add-to-cart-button, #buy-now-button'),
      availability: (q('#availability span') || q('#availability')).replace(/P\.when[\s\S]*/, '').trim(),
      seller: q('#merchantInfoFeature_feature_div .offer-display-feature-text-message') || q('#sellerProfileTriggerId') || q('#merchant-info'),
      shipsFrom: q('#fulfillerInfoFeature_feature_div .offer-display-feature-text-message'),
      outOfStock: !!document.querySelector('#outOfStock, #availability .a-color-price'),
    };
  }

  // 7) Enlaces de la página (para descubrir productos desde páginas de búsqueda)
  let links = [];
  if (args.collectLinks) {
    const re = new RegExp(args.collectLinks, 'i');
    const map = new Map();
    document.querySelectorAll('a[href]').forEach((a) => {
      const href = a.href.split('#')[0];
      if (!re.test(href)) return;
      let text = clean(a.innerText || a.getAttribute('title') || a.getAttribute('aria-label') || '');
      if (text.length < 8) {
        let p = a.parentElement;
        for (let i = 0; i < 4 && p && clean(p.innerText).length < 8; i++) p = p.parentElement;
        if (p) text = clean(p.innerText).slice(0, 200);
      }
      const prev = map.get(href) || '';
      if (text.length > prev.length) map.set(href, text.slice(0, 200));
    });
    links = [...map.entries()].map(([href, text]) => ({ href, text }));
  }

  return {
    mode, url: location.href, pageTitle: document.title, blocked,
    title: title.slice(0, 250),
    ld, ldCount: products.length,
    mainPrice, buyButton, negatives: [...new Set(negatives)].slice(0, 6), usedBadge,
    amazon, links,
  };
}
