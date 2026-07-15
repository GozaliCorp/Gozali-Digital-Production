#!/usr/bin/env python3
"""
Auto-post berita teknologi — Gozali Corp Technology (gozalicorp.my.id)

Alur:
  Google News RSS (24 jam terakhir)  ->  Gemini API (tulis ulang orisinal)  ->  news.json

Dijalankan otomatis oleh GitHub Actions. Bisa juga dijalankan manual:
  python scripts/fetch_news.py            # mode normal (butuh GEMINI_API_KEY)
  python scripts/fetch_news.py --dry-run  # uji pipeline TANPA memanggil AI (gratis)

Catatan penting:
  - Skrip ini TIDAK menyalin isi artikel. Hanya judul + cuplikan yang dipakai
    sebagai bahan, lalu AI menulis ringkasan orisinal berbahasa Indonesia.
  - Setiap berita SELALU menyimpan nama media + tautan ke sumber aslinya.
"""

import os
import re
import sys
import json
import html
import time
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

# ---------------------------------------------------------------- konfigurasi
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEWS_FILE = os.path.join(ROOT, "news.json")

API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Model Gemini tier gratis. Dicoba berurutan — kalau satu model sudah tidak ada
# (Google kadang menghentikan model lama), otomatis pindah ke berikutnya.
MODELS = [m for m in [
    os.environ.get("GEMINI_MODEL", ""),
    "gemini-2.5-flash",
    "gemini-3.5-flash",
    "gemini-2.5-flash-lite",
] if m]

MAX_TOTAL = 24          # jumlah berita yang disimpan di news.json
MAX_NEW_PER_RUN = 8     # maksimal berita baru tiap run
DRY_RUN = "--dry-run" in sys.argv

# Kata kunci pencarian. "when:1d" = hanya 24 jam terakhir.
QUERIES = [
    "teknologi",
    "kecerdasan buatan OR AI",
    "startup teknologi Indonesia",
    "gadget OR smartphone",
    "keamanan siber",
]

KATEGORI_VALID = ["AI", "Gadget", "Startup", "Keamanan", "Software", "Internet"]

UA = "Mozilla/5.0 (compatible; GozaliCorpNewsBot/1.0; +https://gozalicorp.my.id)"


# ---------------------------------------------------------------- util umum
def log(msg):
    print(f"[news] {msg}", flush=True)


def bersihkan(teks):
    """Buang tag HTML dan rapikan spasi."""
    teks = re.sub(r"<[^>]+>", " ", teks or "")
    teks = html.unescape(teks)
    return re.sub(r"\s+", " ", teks).strip()


def kunci(judul):
    """Kunci dedup: judul dinormalisasi (tanpa nama media di belakang)."""
    j = re.sub(r"\s+-\s+[^-]+$", "", judul or "")
    return re.sub(r"[^a-z0-9]+", "", j.lower())[:70]


# ---------------------------------------------------------------- ambil RSS
def url_feed(q):
    qq = urllib.parse.quote(f"{q} when:1d")
    return f"https://news.google.com/rss/search?q={qq}&hl=id&gl=ID&ceid=ID:id"


def ambil(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def parse_rss(xml_bytes):
    """Ambil item dari RSS Google News."""
    hasil = []
    root = ET.fromstring(xml_bytes)
    for item in root.iter("item"):
        judul = bersihkan(item.findtext("title", ""))
        link = (item.findtext("link", "") or "").strip()
        if not judul or not link:
            continue

        # Google News menaruh nama media di elemen <source>, juga di akhir judul
        src_el = item.find("source")
        sumber = bersihkan(src_el.text) if src_el is not None and src_el.text else ""
        if not sumber:
            m = re.search(r"\s-\s([^-]+)$", judul)
            sumber = m.group(1).strip() if m else "Sumber"

        judul_bersih = re.sub(r"\s+-\s+[^-]+$", "", judul).strip() or judul
        cuplikan = bersihkan(item.findtext("description", ""))[:400]

        # tanggal terbit
        tgl = datetime.now(timezone.utc)
        pub = item.findtext("pubDate")
        if pub:
            for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
                try:
                    tgl = datetime.strptime(pub.strip(), fmt)
                    if tgl.tzinfo is None:
                        tgl = tgl.replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    continue

        hasil.append({
            "judul_asli": judul_bersih,
            "cuplikan": cuplikan,
            "link": link,
            "sumber": sumber,
            "waktu": tgl,
        })
    return hasil


def kumpulkan_kandidat():
    """Ambil semua feed, gabung, buang duplikat, urutkan terbaru dulu."""
    semua, terlihat = [], set()
    for q in QUERIES:
        try:
            data = ambil(url_feed(q))
            items = parse_rss(data)
            log(f"'{q}' -> {len(items)} berita")
        except Exception as e:
            log(f"GAGAL ambil '{q}': {e}")
            continue

        for it in items:
            k = kunci(it["judul_asli"])
            if k and k not in terlihat:
                terlihat.add(k)
                semua.append(it)
        time.sleep(1)  # sopan terhadap server

    # buang yang lebih tua dari 24 jam (jaga-jaga)
    batas = datetime.now(timezone.utc) - timedelta(hours=24)
    semua = [x for x in semua if x["waktu"] >= batas]
    semua.sort(key=lambda x: x["waktu"], reverse=True)
    return semua


# ---------------------------------------------------------------- AI rewrite
PROMPT = """Kamu editor berita teknologi untuk Gozali Corp Technology (Indonesia).

Di bawah ini ada daftar judul berita teknologi 24 jam terakhir beserta cuplikannya.
Untuk SETIAP berita, tulis ulang menjadi entri berita berbahasa Indonesia yang ORISINAL.

Aturan wajib:
- Tulis dengan kata-katamu sendiri. JANGAN menyalin frasa dari judul/cuplikan asli.
- "judul": maksimal 12 kata, jelas dan menarik, bukan clickbait berlebihan.
- "ringkasan": 1-2 kalimat (maksimal 30 kata) menjelaskan inti beritanya.
- "kategori": pilih TEPAT SATU dari: {kategori}
- "baca": perkiraan menit baca (angka bulat 2-6).
- Bahasa Indonesia yang natural dan profesional. Jangan berlebihan.
- Jika sebuah berita tidak berkaitan dengan teknologi, beri "skip": true.

Balas HANYA dengan JSON array, tanpa penjelasan, tanpa markdown, tanpa ```.
Format tiap elemen:
{{"i": <nomor berita>, "judul": "...", "ringkasan": "...", "kategori": "...", "baca": 3}}

Daftar berita:
{daftar}
"""


def _minta_gemini(model, prompt):
    """Satu panggilan ke Gemini. Balikkan teks jawaban."""
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent")
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.4,
            "maxOutputTokens": 2048,
            "responseMimeType": "application/json",  # paksa balasan JSON valid
        },
    }).encode()

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": API_KEY,
        },
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        resp = json.loads(r.read())

    kandidat = resp.get("candidates") or []
    if not kandidat:
        blok = resp.get("promptFeedback", {}).get("blockReason")
        raise RuntimeError(f"Gemini tidak mengembalikan jawaban (blockReason={blok})")

    alasan = kandidat[0].get("finishReason")
    if alasan not in (None, "STOP", "MAX_TOKENS"):
        raise RuntimeError(f"Gemini berhenti: finishReason={alasan}")

    bagian = kandidat[0].get("content", {}).get("parts", []) or []
    return "".join(b.get("text", "") for b in bagian).strip()


def panggil_ai(kandidat):
    """Kirim batch judul ke Gemini, terima JSON array hasil tulis ulang."""
    daftar = "\n".join(
        f'{i+1}. Judul: {k["judul_asli"]}\n   Cuplikan: {k["cuplikan"][:220]}'
        for i, k in enumerate(kandidat)
    )
    prompt = PROMPT.format(kategori=", ".join(KATEGORI_VALID), daftar=daftar)

    for model in MODELS:
        for percobaan in range(3):
            try:
                teks = _minta_gemini(model, prompt)
                teks = re.sub(r"^```(?:json)?|```$", "", teks, flags=re.M).strip()
                data = json.loads(teks)
                # responseMimeType JSON kadang membungkus array dalam objek
                if isinstance(data, dict):
                    for v in data.values():
                        if isinstance(v, list):
                            data = v
                            break
                if isinstance(data, list):
                    log(f"AI OK memakai model: {model}")
                    return data
                raise ValueError("balasan bukan JSON array")

            except urllib.error.HTTPError as e:
                pesan = e.read().decode()[:200]
                log(f"[{model}] HTTP {e.code}: {pesan}")
                if e.code in (404, 400):
                    break                      # model tidak ada -> coba model lain
                if e.code in (429, 500, 503) and percobaan < 2:
                    tunggu = 10 * (percobaan + 1)   # backoff untuk limit tier gratis
                    log(f"kena limit/error sementara, tunggu {tunggu} detik...")
                    time.sleep(tunggu)
                    continue
                break
            except Exception as e:
                log(f"[{model}] gagal: {e}")
                if percobaan < 2:
                    time.sleep(5)
                    continue
                break

    raise RuntimeError("Semua model Gemini gagal dipanggil.")


# ---------------------------------------------------------------- main
def muat_lama():
    try:
        with open(NEWS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def main():
    lama = muat_lama()
    sudah_ada = {kunci(n.get("judul", "")) for n in lama}
    sudah_link = {n.get("link", "") for n in lama}
    log(f"news.json saat ini: {len(lama)} berita")

    kandidat = kumpulkan_kandidat()
    log(f"kandidat 24 jam terakhir: {len(kandidat)}")

    # saring yang sudah pernah diposting
    baru = [
        k for k in kandidat
        if kunci(k["judul_asli"]) not in sudah_ada and k["link"] not in sudah_link
    ][:MAX_NEW_PER_RUN]

    if not baru:
        log("Tidak ada berita baru. Selesai.")
        return 0

    log(f"akan diproses: {len(baru)} berita baru")

    # --- tulis ulang ---
    if DRY_RUN:
        log("MODE DRY-RUN: melewati AI, memakai judul asli apa adanya.")
        hasil = [{
            "i": i + 1,
            "judul": k["judul_asli"][:90],
            "ringkasan": k["cuplikan"][:150] or "(dry-run)",
            "kategori": "AI",
            "baca": 3,
        } for i, k in enumerate(baru)]
    else:
        if not API_KEY:
            log("ERROR: GEMINI_API_KEY belum diset.")
            log("Ambil gratis di https://aistudio.google.com/apikey")
            return 1
        hasil = panggil_ai(baru)

    # --- gabungkan ---
    tambahan = []
    for h in hasil:
        if not isinstance(h, dict) or h.get("skip"):
            continue
        idx = h.get("i", 0) - 1
        if not (0 <= idx < len(baru)):
            continue
        asal = baru[idx]

        kat = h.get("kategori", "AI")
        if kat not in KATEGORI_VALID:
            kat = "AI"

        judul = (h.get("judul") or "").strip()
        ringkasan = (h.get("ringkasan") or "").strip()
        if not judul or not ringkasan:
            continue

        tambahan.append({
            "judul": judul,
            "kategori": kat,
            "ringkasan": ringkasan,
            "tanggal": asal["waktu"].astimezone(timezone.utc).strftime("%Y-%m-%d"),
            "baca": int(h.get("baca", 3) or 3),
            "img": "",
            "link": asal["link"],
            "sumber": asal["sumber"],
        })

    if not tambahan:
        log("Tidak ada berita lolos filter. Selesai.")
        return 0

    gabung = (tambahan + lama)[:MAX_TOTAL]
    with open(NEWS_FILE, "w", encoding="utf-8") as f:
        json.dump(gabung, f, ensure_ascii=False, indent=2)

    log(f"DITAMBAHKAN {len(tambahan)} berita -> news.json kini {len(gabung)} berita")
    for t in tambahan:
        log(f"  + [{t['kategori']}] {t['judul']}  ({t['sumber']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
