import json
import os
import time
import requests

try:
    from curl_cffi import requests as cureq
    HAS_CURL = True
except ImportError:
    cureq = None
    HAS_CURL = False

# 🌐 Sunucu Adı
SERVER_NAME = "Charon"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()
API_URL = os.environ.get("API_URL", "").strip()

DB_FILE = "bildirilenler.json"
LISTE_FILE = "takip_listesi.json"
STATE_FILE = "bot_state.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://metin2alerts.com/",
    "Origin": "https://metin2alerts.com"
}

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
        print(f"Kayıt Hatası ({dosya}): {e}")

def send_telegram(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("HATA: TELEGRAM_TOKEN veya CHAT_ID Secrets içinde tanımlı değil!")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            print(f"Telegram Mesaj Gönderme Başarısız ({r.status_code}): {r.text}")
        else:
            print("Telegram mesajı başarıyla iletildi.")
    except Exception as e:
        print(f"Telegram İstek Hatası: {e}")

def komutlari_isle(takip_listesi):
    if not TELEGRAM_TOKEN:
        print("HATA: TELEGRAM_TOKEN bulunamadı!")
        return takip_listesi

    state = load_json(STATE_FILE, {"last_update_id": 0})
    last_id = state.get("last_update_id", 0)
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={last_id + 1}"
    try:
        r = requests.get(url, timeout=10)
        res = r.json()

        if not res.get("ok"):
            print(f"Telegram getUpdates Hatası: {res}")
            return takip_listesi

        updates = res.get("result", [])
        print(f"Gelen bekleyen mesaj sayısı: {len(updates)}")
        degisiklik_var = False

        for update in updates:
            up_id = update["update_id"]
            if up_id > last_id:
                last_id = up_id
            
            msg = update.get("message", {})
            text = msg.get("text", "").strip()
            sender_id = str(msg.get("chat", {}).get("id", "")).strip()

            print(f"Okunan komut: '{text}' | Gönderen Chat ID: '{sender_id}'")

            if sender_id != CHAT_ID:
                print(f"ID Uyuşmazlığı! Secret CHAT_ID: '{CHAT_ID}', Gelen ID: '{sender_id}'")
                continue

            if text.startswith("/ekle"):
                parcalar = text.replace("/ekle", "").strip().rsplit(" ", 1)
                if len(parcalar) == 2 and parcalar[1].replace(".", "", 1).isdigit():
                    isim = parcalar[0].strip()
                    fiyat = float(parcalar[1])
                    takip_listesi = [item for item in takip_listesi if item["isim"].lower() != isim.lower()]
                    takip_listesi.append({"isim": isim, "max_won": fiyat})
                    degisiklik_var = True
                    send_telegram(f"✅ <b>Listeye Eklendi:</b> {isim} (Tavan: {fiyat} Won)")
                else:
                    send_telegram("⚠️ Hatalı format! Örnek: <code>/ekle Dolunay Kılıcı 15</code>")

            elif text.startswith("/sil"):
                isim = text.replace("/sil", "").strip()
                yeni_liste = [item for item in takip_listesi if item["isim"].lower() != isim.lower()]
                if len(yeni_liste) != len(takip_listesi):
                    takip_listesi = yeni_liste
                    degisiklik_var = True
                    send_telegram(f"🗑️ <b>Listeden Silindi:</b> {isim}")
                else:
                    send_telegram(f"⚠️ <b>{isim}</b> takip listesinde bulunamadı.")

            elif text == "/liste" or text == "/start":
                if not takip_listesi:
                    send_telegram("📋 Takip listeniz şu an boş.")
                else:
                    metin = "📋 <b>Aktif Takip Listesi (Charon):</b>\n\n"
                    for itm in takip_listesi:
                        metin += f"• {itm['isim']} ➔ Maks: {itm['max_won']} Won\n"
                    metin += "\n<i>Yeni eşya eklemek için: /ekle İsim Fiyat</i>"
                    send_telegram(metin)

        state["last_update_id"] = last_id
        save_json(STATE_FILE, state)
        if degisiklik_var:
            save_json(LISTE_FILE, takip_listesi)

    except Exception as e:
        print(f"Telegram okuma hatası: {e}")

    return takip_listesi

def fetch_data(urun_adi):
    params = {"server": SERVER_NAME, "query": urun_adi}
    if HAS_CURL:
        try:
            r = cureq.get(API_URL, params=params, headers=HEADERS, impersonate="chrome120", timeout=15)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
            
    try:
        r = requests.get(API_URL, params=params, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"İstek Hatası: {e}")
    return None

def main():
    varsayilan_liste = [
        {"isim": "Zehir Kılıcı", "max_won": 50},
        {"isim": "Kin Kılıcı", "max_won": 30}
    ]
    takip_listesi = load_json(LISTE_FILE, varsayilan_liste)
    takip_listesi = komutlari_isle(takip_listesi)

    seen_ids = set(load_json(DB_FILE, []))
    yeni_bildirim_sayisi = 0

    print(f"[{SERVER_NAME}] Tarama başlatıldı...")

    for hedef in takip_listesi:
        aranan = hedef["isim"]
        limit_won = hedef["max_won"]

        data = fetch_data(aranan)
        if not data:
            time.sleep(2)
            continue

        items = data if isinstance(data, list) else (data.get("items") or data.get("data") or data.get("results") or [])

        for item in items:
            item_id = str(item.get("id") or item.get("_id") or item.get("hash") or f"{item.get('name')}_{item.get('price_won')}")
            name = item.get("name", item.get("item_name", ""))
            price = float(item.get("price_won", item.get("price", 9999)))
            bonuses = item.get("bonuses", item.get("efsunlar", []))
            seller = item.get("seller", item.get("player_name", "Bilinmiyor"))

            if aranan.lower() in name.lower() and price <= limit_won:
                if item_id in seen_ids:
                    continue

                efsun_yazisi = "\n".join([f"• {b}" for b in bonuses]) if bonuses else "Standart / Belirtilmemiş"

                mesaj = (
                    f"🚨 <b>METIN2 FIRSAT İLANI!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"🗡️ <b>Eşya:</b> {name}\n"
                    f"💰 <b>Fiyat:</b> {price} Won (Hedef: ≤{limit_won} Won)\n"
                    f"👤 <b>Satıcı:</b> {seller}\n"
                    f"🌐 <b>Sunucu:</b> {SERVER_NAME}\n\n"
                    f"✨ <b>Efsunlar / Taşlar:</b>\n{efsun_yazisi}\n"
                    f"━━━━━━━━━━━━━━━━━━"
                )

                send_telegram(mesaj)
                seen_ids.add(item_id)
                yeni_bildirim_sayisi += 1
                time.sleep(1)

        time.sleep(2)

    save_json(DB_FILE, list(seen_ids))
    print(f"Tarama bitti. {yeni_bildirim_sayisi} yeni bildirim gönderildi.")

if __name__ == "__main__":
    main()
