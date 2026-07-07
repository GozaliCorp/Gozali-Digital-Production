# Folder Gambar — Gozali Corp Technology

Simpan gambar berita, edukasi, dan produk di folder ini, lalu rujuk lewat path
di file JSON. Ini membuat file JSON tetap ringan (tidak menyimpan base64 besar).

## Cara pakai
1. Upload file gambar ke folder `img/` di repo GitHub (mis. `img/berita-ai.jpg`).
2. Di `news.json` / `edukasi.json` / `products.json`, isi kolom `"img"` dengan
   path-nya, contoh:

   "img": "img/berita-ai.jpg"

3. Commit. Gambar otomatis tampil di situs.

## Tips
- Gunakan nama file huruf kecil tanpa spasi: `img/chip-ai-2026.jpg` (bukan `Chip AI.jpg`).
- Ukuran ideal berita/edukasi: rasio 16:9 (mis. 1200x675 px), produk: 4:3.
- Format .jpg untuk foto, .png untuk gambar dengan teks/logo.
- Kompres dulu (mis. tinypng.com) agar situs cepat.

Kalau memakai fitur "Unggah gambar" di Panel Admin, gambar disimpan sebagai
base64 langsung di JSON (praktis, tapi file jadi lebih besar). Untuk katalog
banyak gambar, cara path folder ini lebih hemat.
