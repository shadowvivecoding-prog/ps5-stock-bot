"""Pruebas de las reglas de filtrado con títulos reales vistos en las tiendas (nov. 2026)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from check_stock import decide_product, is_new_console, looks_like_console_listing, normalize_url, parse_price  # noqa: E402

CONSOLES = [
    "Consola PlayStation 5 Digital",
    "Consola PlayStation 5, chasis E, 1TB",
    "PlayStation 5 digital + 2 mandos DualSense",
    "PACK PlayStation 5 + Marvel Lobezno PS5",
    "Consola - Sony PS5 Slim Digital Edition, 825 GB SSD, 4K, 1 mando, Chasis E, Blanco + Pack Fortnite Flowering Chaos (código de descarga)",
    "Consola PS5 - PLAYSTATION PlayStation 5 (Chasis E) 1TB, 1 TB, White",
    "Consola PlayStation 5 Digital Chassis E AON",
    "Consola PS5 Chassis E Digital + Auriculares Atlas 200 Blancos + Base de Carga PS5 Deluxe",
    "Sony PlayStation 5 Slim Digital Chasis E + Samsung 990 PRO 1 TB SSD PCIe 4.0 NVMe Disipador Térmico",
    "Sony PlayStation 5 Pro",
    "Playstation Consola 5 Estandar Chassis E",
    "PlayStation Consola de 5 discos (nuevo modelo delgado 2023) - Versión de los EAU, 1 año de garantía del fabricante",
    "PS5 CON DOS MANDOS",
    "Sony PS5 Console 825GB Digital",
    "CONSOLA PS5 SLIM WHITE EDITION (1TB) – CHASIS E – VERSIÓN CON LECTOR – 1 MANDO",
    "Consola PlayStation 5 Digital 825GB - Blanco",
]
NOT_CONSOLES = [
    "Seminuevo Consola Playstation 5 Mod. Slim",
    "Unidad De Disco Externo Consola Digital PS5",
    "Playstation 5 - Mando inalámbrico DualSense",
    "Playstation 5 Portal Remote Player|Reproductor a distancia",
    "Soporte vertical para Consolas PS5® y consolas PS5® Edición Digital (Modelo Slim)",
    "PlayStation VR2",
    "consola mini ps5 m5",
    "CATAN - Console Edition PS4 & PS5",
    "Consola Sony PlayStation 2 TWO Negra Seminueva PS2 Retro",
    "Lexar EQ790 PS5 SSD 4TB, SSD Interno NVMe M.2 con Disipador de Calor",
    "Mcbazel PS5 Pro/PS5 Slim/ PS5 RGB LED Tira de Luces con Control Remoto IR",
    "CONSOLA NINTENDO SWITCH 2 V1 + MARIO KART WORLD",
    "Philips QLED 75PUS7810 75\" Smart TV Titan OS Sony PlayStation 5 Slim Digital",
    "Playstation Estación de recarga DualSense - PlayStation 5",
    "PlayStation 5-MARVEL: Lobezno - Edición Estándar",
]


def test_consoles():
    for t in CONSOLES:
        assert is_new_console(t), t


def test_not_consoles():
    for t in NOT_CONSOLES:
        assert not is_new_console(t), t


def test_listing_filter():
    assert looks_like_console_listing("Agotado Consola PlayStation 5 Digital 599,90 €")
    assert looks_like_console_listing("-7% Consola PlayStation 5 Digital 825GB Fortnite")
    assert not looks_like_console_listing("Incluye Regalo GAME HH-ST RGB Blanco PC - PS5 - PS4 - Auriculares")
    assert not looks_like_console_listing("Seminuevo Consola Playstation 5 Mod. Slim 564,99€")


def test_prices():
    assert parse_price("1.108,13 €") == 1108.13
    assert parse_price("599,99€") == 599.99
    assert parse_price("499,– €") == 499.0
    assert parse_price("599.90") == 599.9
    assert parse_price("1108,\n13\n €") == 1108.13


def test_urls():
    assert normalize_url("https://www.amazon.es/Playstation-Consola/dp/B0FP2S1MNB/ref=sr_1_3?x=1") == "https://www.amazon.es/dp/B0FP2S1MNB"
    assert normalize_url("https://www.game.es/consola-playstation-5-mod-slim/227565?utm=1") == "https://www.game.es/consola-playstation-5-mod-slim/227565"


def test_decide():
    # El Corte Inglés agotado (JSON-LD SoldOut)
    assert decide_product({"ld": {"price": 599.9, "availability": "SoldOut"}, "mainPrice": None}, 600, 250)[0] is False
    # MediaMarkt con stock (JSON-LD InStock) a precio de reventa
    ok, price, *_ = decide_product({"ld": {"price": 1108.13, "availability": "InStock", "seller": "Office-Partner ES"}, "mainPrice": 1108.13}, 600, 250)
    assert ok and price == 1108.13
    # PcComponentes: JSON-LD con PVPR más alto que el precio visible -> nos quedamos con el menor
    ok, price, *_ = decide_product({"ld": {"price": 1017.65, "availability": "InStock"}, "mainPrice": 949.99}, 600, 250)
    assert ok and price == 949.99
    # Sin JSON-LD (MediaMarkt agotado): sin botón -> no disponible
    assert decide_product({"ld": None, "buyButton": False, "negatives": ["Este artículo no está disponible actualmente."]}, 600, 250)[0] is False
    # Amazon con botón de compra
    ok, price, _, seller = decide_product({"amazon": {"price": 599.99, "addToCart": True, "availability": "En stock", "seller": "Amazon"}}, 600, 250)
    assert ok and price == 599.99 and seller == "Amazon"
    # Amazon sin botón
    assert decide_product({"amazon": {"price": None, "addToCart": False, "availability": "No disponible."}}, 600, 250)[0] is False


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
