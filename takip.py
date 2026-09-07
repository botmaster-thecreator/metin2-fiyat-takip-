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
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

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
        print(f"Telegram İstek Hatası: {e}")

def gemini_ile_cozumle(kullanici_metni):
    if not GEMINI_API_KEY:
        print("GEMINI_API_KEY bulunamadı.")
        return []

    prompt = f"""
    Sen bir Metin2 oyun pazar asistanısın. Kullanıcı serbest Türkçe metinle oyunda aramak istediği eşyaları anlatıyor.
    Metni analiz et ve Metin2 pazarında birebir aratılabilecek tam eşya isimlerini, artı seviyesini (+0'dan +9'a kadar), won bütçesini ve aranan efsunları belirle.

    Kurallar:
    1. Kullanıcı genel veya sınıf odaklı konuştuysa (örn: "sura için 15 ateş direnci kask ve zırh bakıyorum 50 won"), o sınıfa ait popüler ilgili eşyaların tam adlarını türet (örn: "Boynuzlu Kask +9", "Kale Kask +9", "Kara Büyü Zırh +9").
    2. Artı belirtilmemiş ama kask, zırh, silah gibi artı basılan bir şey isteniyorsa varsayılan olarak "+9" kabul et.
    3. Fiyat belirtilmemişse max_won değerine 9999 yaz.
    4. Efsun filtresine efsunun sayısal değeriyle birlikte sade anahtar kelimesini ekle (örn: "15 ateş", "2000 hp", "10 rüzgar").
    5. Çıktı SADECE ve kesinlikle JSON formatında bir liste olmalıdır. Hiçbir açıklama yazma.

    JSON Örnek Formatı:
    [
      {{"isim": "Kara Büyü Zırh +9", "max_won": 50.0, "efsunlar": ["15 ateş"]}},
      {{"isim": "Boynuzlu Kask +9", "max_won": 50.0, "efsunlar": ["15 ateş"]}}
    ]

    Kullanıcı İsteği: "{kullanici_metni}"
    """

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "response_mime_type": "application/json"
        }
    }

    try:
        res = requests.post(url, json=payload, timeout=20)
        data = res.json()
        raw_json = data["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(raw_json)
    except Exception as e:
        print(f"Gemini Çözümleme Hatası: {e}")
        return []

def komutlari_isle(takip_listesi):
    if not TELEGRAM_TOKEN:
        return takip_listesi

    state = load_json(STATE_FILE, {"last_update_id": 0})
    last_id = state.get("last_update_id", 0)
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={last_id + 1}"
    try:
        r = requests.get(url, timeout=10)
        res = r.json()
        if not res.get("ok"):
            return takip_listesi

        updates = res.get("result", [])
        degisiklik_var = False

        for update in updates:
            up_id = update["update_id"]
            if up_id > last_id:
                last_id = up_id
            
            msg = update.get("message", {})
            text = msg.get("text", "").strip()
            sender_id = str(msg.get("chat", {}).get("id", "")).strip()

            if sender_id != CHAT_ID or not text:
                continue

            # 1. Silme Komutu
            if text.startswith("/sil"):
                hedef = text.replace("/sil", "").strip().lower()
                yeni_liste = [item for item in takip_listesi if hedef not in item["isim"].lower()]
                if len(yeni_liste) != len(takip_listesi):
                    takip_listesi = yeni_liste
                    degisiklik_var = True
                    send_telegram(f"🗑️ '{hedef}' içeren eşyalar listeden temizlendi.")
                else:
                    send_telegram(f"⚠️ <b>{hedef}</b> takip listesinde bulunamadı.")

            # 2. Liste Komutu
            elif text in ["/liste", "/start"]:
                if not takip_listesi:
                    send_telegram("📋 Takip listeniz şu an boş.\n\nİstediğin eşyayı günlük dille yazabilirsin (Örn: <i>Sura 15 ateş kask ve zırh 40 won</i>).")
                else:
                    metin = "📋 <b>Aktif Takip Listesi (Charon):</b>\n\n"
                    for itm in takip_listesi:
                        efs = itm.get("efsunlar", [])
                        efs_metin = f" <i>(Efsun: {', '.join(efs)})</i>" if efs else ""
                        metin += f"• <b>{itm['isim']}</b> ➔ Maks: {itm['max_won']} Won{efs_metin}\n"
                    send_telegram(metin)

            # 3. Serbest Metin / Doğal Dil Algılama
            else:
                send_telegram("🧠 İsteğin yapay zekâ ile çözümleniyor...")
                yeni_esyalar = gemini_ile_cozumle(text)

                if yeni_esyalar:
                    eklenen_isimler = []
                    for y_item in yeni_esyalar:
                        # Varsa eskilerini temizle
                        takip_listesi = [item for item in takip_listesi if item["isim"].lower() != y_item["isim"].lower()]
                        takip_listesi.append(y_item)
                        efs = f" [{', '.join(y_item.get('efsunlar', []))}]" if y_item.get('efsunlar') else ""
                        eklenen_isimler.append(f"• <b>{y_item['isim']}</b> (Tavan: {y_item['max_won']} Won){efs}")
                    
                    degisiklik_var = True
                    send_telegram("✅ <b>Aşağıdaki eşyalar listeye eklendi:</b>\n\n" + "\n".join(eklenen_isimler))
                else:
                    send_telegram("⚠️ İsteğin anlaşılamadı veya eşya bulunamadı. Lütfen biraz daha açık yazmayı dene.")

        state["last_update_id"] = last_id
        save_json(STATE_FILE, state)
        if degisiklik_var:
            save_json(LISTE_FILE, takip_listesi)

    except Exception as e:
        print(f"Telegram okuma hatası: {e}")

    return takip_listesi

def fetch_data(urun_adi):
    arama_kelimesi = urun_adi.split("+")[0].strip() if "+" in urun_adi else urun_adi
    if "(" in arama_kelimesi:
        arama_kelimesi = arama_kelimesi.split("(")[0].strip()

    params = {"server": SERVER_NAME, "query": arama_kelimesi}
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

def efsun_uyuyor_mu(istenen_sart, tum_efsunlar):
    aranan_kelimeler = istenen_sart.split()
    for satır in tum_efsunlar:
        satır_lower = str(satır).lower()
        if all(kelime in satır_lower for kelime in aranan_kelimeler):
            return True
    return False

def main():
    varsayilan_liste = [
        {"isim": "Zehir Kılıcı +9", "max_won": 50, "efsunlar": []},
        {"isim": "Kin Kılıcı +9", "max_won": 30, "efsunlar": []}
    ]
    takip_listesi = load_json(LISTE_FILE, varsayilan_liste)
    takip_listesi = komutlari_isle(takip_listesi)

    seen_ids = set(load_json(DB_FILE, []))
    yeni_bildirim_sayisi = 0

    print(f"[{SERVER_NAME}] Tarama başlatıldı...")

    for hedef in takip_listesi:
        aranan_tam_ad = hedef["isim"].lower()
        limit_won = hedef["max_won"]
        istenen_efsunlar = hedef.get("efsunlar", [])

        data = fetch_data(hedef["isim"])
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

            if aranan_tam_ad not in name.lower():
                continue

            if price > limit_won:
                continue

            if istenen_efsunlar:
                uygun = True
                for sart in istenen_efsunlar:
                    if not efsun_uyuyor_mu(sart, bonuses):
                        uygun = False
                        break
                if not uygun:
                    continue

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
