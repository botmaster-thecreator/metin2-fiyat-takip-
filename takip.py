import json
import os
import time
import threading
import requests
from flask import Flask

try:
    from curl_cffi import requests as cureq
    HAS_CURL = True
except ImportError:
    cureq = None
    HAS_CURL = False

# 🌐 Sunucu ve Ayarlar
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

lock = threading.Lock()

# -------------------------------------------------------------
# 🌐 FLASK WEB SUNUCUSU (Render'ı Canlı Tutmak İçin)
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
        print(f"Telegram Gönderim Hatası: {e}")

# -------------------------------------------------------------
# 🧠 GEMINI DOĞAL DİL ANALİZİ
# -------------------------------------------------------------
def gemini_ile_cozumle(kullanici_metni):
    if not GEMINI_API_KEY:
        return None

    prompt = f"""
    Sen bir Metin2 pazar asistanısın. Kullanıcının Türkçe yazdığı mesajı analiz et ve niyetini JSON olarak çıkar.

    Niyetler (aksiyon):
    1. "son_sil": Kullanıcı en son eklenen eşyaları geri almak / silmek istiyorsa (Örn: "en son eklediklerini sil", "az öncekileri çıkar", "son işlemi iptal et").
    2. "sil": Kullanıcı belirli bir eşyayı veya kategoriyi silmek istiyorsa (Örn: "kaskları sil", "kara büyüyü kaldır"). "silinecekler" listesine aranacak kelimeleri ekle.
    3. "temizle": Kullanıcı tüm listeyi sıfırlamak istiyorsa (Örn: "bütün listeyi sil", "her şeyi temizle").
    4. "liste": Kullanıcı mevcut takip listesini görmek istiyorsa (Örn: "listeyi göster", "neler var", "listede ne ekli", "list").
    5. "ekle": Kullanıcı pazarda aranacak yeni eşyalar tanımlıyorsa.

    Ekleme Kuralları:
    - Sınıf odaklı isteklerde (örn: "sura için 15 ateş zırh") popüler eşyaların tam isimlerini türet ("Kara Büyü Zırh +9" gibi).
    - Artı belirtilmemişse varsayılan "+9" yap. Fiyat yoksa max_won: 9999 ver.
    - Efsunları sayısal değer ve sade haliyle diziye ekle ("15 ateş", "2000 hp").

    Çıktı SADECE geçerli bir JSON nesnesi olmalıdır:
    {{
      "aksiyon": "ekle" | "son_sil" | "sil" | "temizle" | "liste",
      "silinecekler": ["kelime1", "kelime2"],
      "esyalar": [
        {{"isim": "Kara Büyü Zırh +9", "max_won": 50.0, "efsunlar": ["15 ateş"]}}
      ]
    }}

    Kullanıcı İsteği: "{kullanici_metni}"
    """

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"response_mime_type": "application/json"}
    }

    try:
        res = requests.post(url, json=payload, timeout=20)
        data = res.json()
        raw_json = data["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(raw_json)
    except Exception as e:
        print(f"Gemini Çözümleme Hatası: {e}")
        return None

def liste_mesaji_gonder(takip_listesi):
    if not takip_listesi:
        send_telegram("📋 Takip listeniz şu an boş.\n\nİstediğin eşyayı günlük dille yazabilirsin (Örn: <i>Sura 15 ateş kask ve zırh 40 won</i>).")
    else:
        metin = "📋 <b>Aktif Takip Listesi (Charon):</b>\n\n"
        for itm in takip_listesi:
            efs = itm.get("efsunlar", [])
            efs_metin = f" <i>(Efsun: {', '.join(efs)})</i>" if efs else ""
            metin += f"• <b>{itm['isim']}</b> ➔ Maks: {itm['max_won']} Won{efs_metin}\n"
        send_telegram(metin)

# -------------------------------------------------------------
# ⚡ ANLIK TELEGRAM DİNLEYİCİSİ (LONG-POLLING)
# -------------------------------------------------------------
def telegram_dinleyici_dongusu():
    print("Telegram anlık dinleme başlatıldı...")
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
                    takip_listesi = load_json(LISTE_FILE, [
                        {"isim": "Zehir Kılıcı +9", "max_won": 50, "efsunlar": []},
                        {"isim": "Kin Kılıcı +9", "max_won": 30, "efsunlar": []}
                    ])
                    son_eklenenler = state.get("son_eklenenler", [])
                    degisiklik_var = False

                    if text.lower() in ["/liste", "/list", "/start", "liste", "list"]:
                        liste_mesaji_gonder(takip_listesi)

                    elif text.startswith("/sil"):
                        hedef = text.replace("/sil", "").strip().lower()
                        yeni_liste = [item for item in takip_listesi if hedef not in item["isim"].lower()]
                        if len(yeni_liste) != len(takip_listesi):
                            takip_listesi = yeni_liste
                            degisiklik_var = True
                            send_telegram(f"🗑️ '{hedef}' içeren eşyalar listeden temizlendi.")
                        else:
                            send_telegram(f"⚠️ <b>{hedef}</b> takip listesinde bulunamadı.")

                    else:
                        send_telegram("🧠 İsteğin analiz ediliyor...")
                        analiz = gemini_ile_cozumle(text)

                        if not analiz:
                            send_telegram("⚠️ İsteğin anlaşılamadı. Lütfen tekrar dene.")
                        else:
                            aksiyon = analiz.get("aksiyon")

                            if aksiyon == "son_sil":
                                if not son_eklenenler:
                                    send_telegram("⚠️ Hafızada geri alınabilecek son eklenmiş bir eşya kaydı bulunamadı.")
                                else:
                                    silinen_isimler = [ad.lower() for ad in son_eklenenler]
                                    eski_boyut = len(takip_listesi)
                                    takip_listesi = [itm for itm in takip_listesi if itm["isim"].lower() not in silinen_isimler]
                                    if len(takip_listesi) < eski_boyut:
                                        send_telegram(f"🗑️ <b>Son eklenenler kaldırıldı:</b>\n• " + "\n• ".join(son_eklenenler))
                                        son_eklenenler = []
                                        degisiklik_var = True
                                    else:
                                        send_telegram("⚠️ Son eklenen eşyalar zaten listede bulunamadı.")

                            elif aksiyon == "sil":
                                silinecekler = [s.lower() for s in analiz.get("silinecekler", [])]
                                if not silinecekler:
                                    send_telegram("⚠️ Hangi eşyayı silmek istediğin tam anlaşılamadı.")
                                else:
                                    eski_boyut = len(takip_listesi)
                                    takip_listesi = [
                                        itm for itm in takip_listesi 
                                        if not any(h in itm["isim"].lower() for h in silinecekler)
                                    ]
                                    if len(takip_listesi) < eski_boyut:
                                        send_telegram(f"🗑️ <b>Listeden temizlendi:</b> {', '.join(silinecekler)}")
                                        degisiklik_var = True
                                    else:
                                        send_telegram(f"⚠️ '{', '.join(silinecekler)}' ile eşleşen bir eşya bulunamadı.")

                            elif aksiyon == "temizle":
                                takip_listesi = []
                                son_eklenenler = []
                                degisiklik_var = True
                                send_telegram("🧹 <b>Takip listen tamamen temizlendi!</b>")

                            elif aksiyon == "liste":
                                liste_mesaji_gonder(takip_listesi)

                            elif aksiyon == "ekle":
                                yeni_esyalar = analiz.get("esyalar", [])
                                if yeni_esyalar:
                                    eklenen_isimler = []
                                    yeni_eklenen_adlar = []
                                    for y_item in yeni_esyalar:
                                        takip_listesi = [item for item in takip_listesi if item["isim"].lower() != y_item["isim"].lower()]
                                        takip_listesi.append(y_item)
                                        yeni_eklenen_adlar.append(y_item["isim"])
                                        efs = f" [{', '.join(y_item.get('efsunlar', []))}]" if y_item.get('efsunlar') else ""
                                        eklenen_isimler.append(f"• <b>{y_item['isim']}</b> (Tavan: {y_item['max_won']} Won){efs}")
                                    
                                    son_eklenenler = yeni_eklenen_adlar
                                    degisiklik_var = True
                                    send_telegram("✅ <b>Listeye eklendi:</b>\n\n" + "\n".join(eklenen_isimler))
                                else:
                                    send_telegram("⚠️ Eklenecek eşya tespit edilemedi.")

                    state["last_update_id"] = last_id
                    state["son_eklenenler"] = son_eklenenler
                    save_json(STATE_FILE, state)
                    if degisiklik_var:
                        save_json(LISTE_FILE, takip_listesi)

        except Exception as e:
            print(f"Telegram Dinleme Hatası: {e}")
            time.sleep(3)

# -------------------------------------------------------------
# 🔍 METIN2 PAZAR TARAYICISI (5 DAKİKADA BİR)
# -------------------------------------------------------------
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

def pazar_tarama_dongusu():
    print(f"[{SERVER_NAME}] Pazar tarama servisi başlatıldı...")
    while True:
        try:
            with lock:
                takip_listesi = load_json(LISTE_FILE, [])
                seen_ids = set(load_json(DB_FILE, []))

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
                    time.sleep(1)

                time.sleep(2)

            with lock:
                save_json(DB_FILE, list(seen_ids))

        except Exception as e:
            print(f"Pazar Tarama Hatası: {e}")

        time.sleep(300)

# -------------------------------------------------------------
# 🚀 BAŞLATICI
# -------------------------------------------------------------
if __name__ == "__main__":
    t_tele = threading.Thread(target=telegram_dinleyici_dongusu, daemon=True)
    t_tele.start()

    t_pazar = threading.Thread(target=pazar_tarama_dongusu, daemon=True)
    t_pazar.start()

    run_flask()
