import json
import os
import re
import time
import threading
import urllib.parse
import requests
from flask import Flask

SERVER_NAME = "Charon"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()
SCRAPER_API_KEY = os.environ.get("SCRAPER_API_KEY", "").strip()

DB_FILE = "bildirilenler.json"
LISTE_FILE = "takip_listesi.json"
STATE_FILE = "bot_state.json"

lock = threading.Lock()

# -------------------------------------------------------------
# 🌐 FLASK WEB SUNUCUSU
# -------------------------------------------------------------
app = Flask(__name__)

@app.route("/")
def home():
    return "Metin2 Botu 7/24 Aktif Calisiyor!", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# -------------------------------------------------------------
# 💾 DOSYA YÖNETİMİ
# -------------------------------------------------------------
def load_json(dosya, varsayilan):
    if os.path.exists(dosya):
        try:
            with open(dosya, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return varsayilan
    return varsayilan

def save_json(dosya, veri):
    try:
        with open(dosya, "w", encoding="utf-8") as f:
            json.dump(veri, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Kayıt Hatası ({dosya}): {e}", flush=True)

def send_telegram(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram Gönderim Hatası: {e}", flush=True)

# -------------------------------------------------------------
# ⚡ HIZLI KOMUT AYRIŞTIRICI
# -------------------------------------------------------------
def hizli_ayristir(metin):
    kalip = r"^(?P<isim>.+?)\s+(?P<won>\d+(?:[\.,]\d+)?)\s*won(?:\s+(?P<efsun>.*))?$"
    eslesme = re.match(kalip, metin.strip(), re.IGNORECASE)
    if not eslesme:
        return None

    ad = eslesme.group("isim").strip()
    won_str = eslesme.group("won").replace(",", ".")
    fiyat = float(won_str)
    efsun_ham = eslesme.group("efsun")
    efsunlar = [e.strip().lower() for e in efsun_ham.split(",")] if efsun_ham else []

    arti_eklenmeyenler = ["kutsama kağıdı", "ruh taşı", "ayışığı", "büyülü metal", "inci", "zen fasulyesi"]
    if not any(k in ad.lower() for k in arti_eklenmeyenler):
        if not re.search(r"\+\d+$", ad):
            ad += " +9"

    return {
        "aksiyon": "ekle",
        "esyalar": [{"isim": ad.title() if "+" not in ad else ad, "max_won": fiyat, "efsunlar": efsunlar}]
    }

def liste_mesaji_gonder(takip_listesi):
    if not takip_listesi:
        send_telegram("📋 Takip listeniz şu an boş.\n\nÖrnek: <code>Kutsama Kağıdı 999 won</code> veya <code>Dolunay Kılıcı +9 50 won</code>")
    else:
        metin = "📋 <b>Aktif Takip Listesi (Charon):</b>\n\n"
        for itm in takip_listesi:
            efs = itm.get("efsunlar", [])
            efs_metin = f" <i>(Efsun: {', '.join(efs)})</i>" if efs else ""
            metin += f"• <b>{itm['isim']}</b> ➔ Maks: {itm['max_won']} Won{efs_metin}\n"
        send_telegram(metin)

# -------------------------------------------------------------
# ⚡ ANLIK TELEGRAM DİNLEYİCİSİ
# -------------------------------------------------------------
def telegram_dinleyici_dongusu():
    print("Telegram dinleme aktif...", flush=True)
    state = load_json(STATE_FILE, {"last_update_id": 0, "son_eklenenler": []})
    last_id = state.get("last_update_id", 0)

    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={last_id + 1}&timeout=20"
            r = requests.get(url, timeout=25)
            res = r.json()

            if not res.get("ok"):
                time.sleep(2)
                continue

            updates = res.get("result", [])
            for update in updates:
                up_id = update["update_id"]
                if up_id > last_id:
                    last_id = up_id
                
                msg = update.get("message", {})
                text = msg.get("text", "").strip()
                sender_id = str(msg.get("chat", {}).get("id", "")).strip()

                if sender_id != CHAT_ID or not text:
                    continue

                with lock:
                    takip_listesi = load_json(LISTE_FILE, [])
                    degisiklik_var = False
                    temiz_komut = text.lower()

                    if temiz_komut in ["/liste", "/list", "/start", "liste", "list"]:
                        liste_mesaji_gonder(takip_listesi)

                    elif temiz_komut in ["/temizle", "temizle", "sıfırla"]:
                        takip_listesi = []
                        degisiklik_var = True
                        send_telegram("🧹 <b>Takip listeniz temizlendi!</b>")

                    elif text.startswith("/sil"):
                        hedef = text.replace("/sil", "").strip().lower()
                        yeni_liste = [item for item in takip_listesi if hedef not in item["isim"].lower()]
                        if len(yeni_liste) != len(takip_listesi):
                            takip_listesi = yeni_liste
                            degisiklik_var = True
                            send_telegram(f"🗑️ '{hedef}' listeden kaldırıldı.")
                        else:
                            send_telegram(f"⚠️ <b>{hedef}</b> listede bulunamadı.")

                    else:
                        analiz = hizli_ayristir(text)
                        if analiz and analiz.get("aksiyon") == "ekle":
                            yeni_esyalar = analiz.get("esyalar", [])
                            eklenen_isimler = []
                            for y_item in yeni_esyalar:
                                takip_listesi = [item for item in takip_listesi if item["isim"].lower() != y_item["isim"].lower()]
                                takip_listesi.append(y_item)
                                eklenen_isimler.append(f"• <b>{y_item['isim']}</b> (Tavan: {y_item['max_won']} Won)")
                            degisiklik_var = True
                            send_telegram("✅ <b>Listeye eklendi:</b>\n\n" + "\n".join(eklenen_isimler))
                        else:
                            send_telegram("⚠️ Komut formatı: <code>Eşya Adı Fiyat won</code>\nÖrn: <code>Kutsama Kağıdı 999 won</code>")

                    state["last_update_id"] = last_id
                    save_json(STATE_FILE, state)
                    if degisiklik_var:
                        save_json(LISTE_FILE, takip_listesi)

        except Exception as e:
            print(f"Telegram Hatası: {e}", flush=True)
            time.sleep(3)

# -------------------------------------------------------------
# 🔍 METIN2 PAZAR VERİ ÇEKİCİ (JS RENDER & DEEP EXTRACTOR)
# -------------------------------------------------------------
def fetch_pazar_verisi(urun_adi):
    arama_kelimesi = urun_adi.split("+")[0].strip() if "+" in urun_adi else urun_adi
    if "(" in arama_kelimesi:
        arama_kelimesi = arama_kelimesi.split("(")[0].strip()

    encoded_query = urllib.parse.quote(arama_kelimesi)
    target_url = f"https://metin2alerts.com/?server={SERVER_NAME}&search={encoded_query}"

    if not SCRAPER_API_KEY:
        print("⚠️ SCRAPER_API_KEY bulunamadı.", flush=True)
        return []

    scraper_url = "http://api.scraperapi.com"
    params = {
        "api_key": SCRAPER_API_KEY,
        "url": target_url,
        "render": "true"  # Tarayıcı motorunu çalıştırır, JS ile ilanların dolmasını sağlar
    }

    try:
        r = requests.get(scraper_url, params=params, timeout=60)
        if r.status_code != 200:
            print(f"⚠️ [{urun_adi}] ScraperAPI HTTP {r.status_code}", flush=True)
            return []

        html = r.text

        # 1. Next.js Veri Ayrıştırma
        next_data_match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
        if next_data_match:
            try:
                data = json.loads(next_data_match.group(1))
                page_props = data.get("props", {}).get("pageProps", {})
                for key in ["items", "marketItems", "data", "listings", "results"]:
                    val = page_props.get(key)
                    if isinstance(val, list) and len(val) > 0:
                        # Schema.org nesnelerini filtrele
                        gercek_ilanlar = [x for x in val if isinstance(x, dict) and "@context" not in x]
                        if gercek_ilanlar:
                            print(f"📦 [{urun_adi}] __NEXT_DATA__ üzerinden {len(gercek_ilanlar)} ilan alındı.", flush=True)
                            return gercek_ilanlar
            except Exception:
                pass

        # 2. Render edilmiş HTML Tablosu / Kart Ayrıştırma (DOM Regex Fallback)
        bulunan_ilanlar = []
        satirlar = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL | re.IGNORECASE)
        for satir in satirlar:
            if "schema.org" in satir.lower():
                continue
            metin = re.sub(r'<[^>]+>', ' ', satir).strip()
            if arama_kelimesi.lower() in metin.lower():
                won_bul = re.search(r'(\d+(?:[.,]\d+)?)\s*won', metin, re.IGNORECASE)
                if won_bul:
                    fiyat = float(won_bul.group(1).replace(",", "."))
                    bulunan_ilanlar.append({
                        "name": urun_adi,
                        "price_won": fiyat,
                        "seller": "Pazar İlanı",
                        "bonuses": []
                    })

        if bulunan_ilanlar:
            print(f"📦 [{urun_adi}] DOM üzerinden {len(bulunan_ilanlar)} ilan ayrıştırıldı.", flush=True)
            return bulunan_ilanlar

        print(f"ℹ️ [{urun_adi}] Render tamamlandı, eşleşen ilan bulunamadı.", flush=True)
        return []

    except Exception as e:
        print(f"⚠️ [{urun_adi}] İstek Hatası: {e}", flush=True)
        return []

def pazar_tarama_dongusu():
    print(f"[{SERVER_NAME}] Pazar tarama aktif.", flush=True)
    while True:
        try:
            with lock:
                takip_listesi = load_json(LISTE_FILE, [])
                seen_ids = set(load_json(DB_FILE, []))

            zaman_str = time.strftime('%H:%M:%S')
            print(f"\n--- [{zaman_str}] Pazar Taraması ({len(takip_listesi)} Eşya) ---", flush=True)
            yeni_bildirim_sayisi = 0

            for hedef in takip_listesi:
                aranan_tam_ad = hedef["isim"].lower()
                limit_won = hedef["max_won"]

                items = fetch_pazar_verisi(hedef["isim"])

                for item in items:
                    name = item.get("name") or item.get("item_name") or ""
                    if not name:
                        continue

                    price_val = item.get("price_won") or item.get("won") or item.get("price") or 0
                    try:
                        price = float(price_val)
                        if price > 5000:
                            price = price / 100000000.0
                    except Exception:
                        price = 9999

                    item_id = str(item.get("id") or item.get("_id") or f"{name}_{price}_{item.get('seller', '')}")

                    if aranan_tam_ad not in name.lower() and name.lower() not in aranan_tam_ad:
                        continue
                    if price > limit_won:
                        continue
                    if item_id in seen_ids:
                        continue

                    seller = item.get("seller") or item.get("player_name") or "Bilinmiyor"
                    bonuses = item.get("bonuses") or item.get("efsunlar") or []
                    efsun_yazisi = "\n".join([f"• {b}" for b in bonuses]) if bonuses else "Standart"

                    mesaj = (
                        f"🚨 <b>METIN2 FIRSAT İLANI!</b>\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"🗡️ <b>Eşya:</b> {name}\n"
                        f"💰 <b>Fiyat:</b> {price} Won (Hedef: ≤{limit_won} Won)\n"
                        f"👤 <b>Satıcı:</b> {seller}\n"
                        f"🌐 <b>Sunucu:</b> {SERVER_NAME}\n\n"
                        f"✨ <b>Efsunlar:</b>\n{efsun_yazisi}\n"
                        f"━━━━━━━━━━━━━━━━━━"
                    )
                    send_telegram(mesaj)
                    seen_ids.add(item_id)
                    yeni_bildirim_sayisi += 1
                    time.sleep(1)

                time.sleep(2)

            with lock:
                save_json(DB_FILE, list(seen_ids))

            print(f"✅ Tarama bitti. {yeni_bildirim_sayisi} yeni bildirim atıldı.\n", flush=True)

        except Exception as e:
            print(f"Pazar Tarama Hatası: {e}", flush=True)

        time.sleep(300)

if __name__ == "__main__":
    threading.Thread(target=telegram_dinleyici_dongusu, daemon=True).start()
    threading.Thread(target=pazar_tarama_dongusu, daemon=True).start()
    run_flask()
