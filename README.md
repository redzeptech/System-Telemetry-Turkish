# System-Telemetry-Turkish 🖥️🟢

Gamer / Matrix temalı gelişmiş donanım izleme ve teşhis sistemi. Türkçe arayüz ve mesajlarla telemetri toplayan bu proje; CPU, GPU, bellek, disk, fan ve anakart verilerini birleştirir; eşik analizi, uyarılar, 0–100 sağlık skoru ve PDF rapor üretir.

## Özellikler

- **Canlı dashboard:** Flask tabanlı web paneli; Cyberpunk / Matrix teması, Chart.js ile sıcaklık grafikleri, donanım özeti, günlük PDF indirme. CORS açık; API ile entegrasyon kolay.
- **Akıllı analiz:** Sıcaklık trendi, bileşen korelasyonu ve termal analiz (CPU / GPU / fan ilişkisi); yapılandırılabilir eşikler (`config/thresholds.yaml`).
- **PDF raporlama:** Donanım envanteri, sağlık özeti ve olay günlüğü içeren günlük rapor (`REPORT_YYYY_MM_DD.pdf`); FPDF2 ve ReportLab tabanlı boru hatları.
- **Skor sistemi:** Ağırlıklı bileşen puanları ve 100 üzerinden dinamik genel sağlık skoru; `config/scoring_rules.yaml` ile özelleştirilebilir.
- **Kalıcılık:** SQLite telemetri, anlık görüntüler ve isteğe bağlı saatlik özet (rollup) ile veri yönetimi.

## Kurulum

Python 3.10+ önerilir. Windows’ta WMI ve `pywin32` otomatik seçilir; Linux’ta ilgili satırlar atlanır.

```bash
git clone <repo-url>
cd System-Telemetry-Turkish
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux / macOS
# source .venv/bin/activate

pip install -r requirements.txt
```

## Çalıştırma

**Sürekli telemetri + web paneli (önerilen tam paket):**

```bash
python main.py --web
```

Varsayılan olarak panel `0.0.0.0:5000` üzerinden dinlenir; tarayıcıdan genelde [http://127.0.0.1:5000](http://127.0.0.1:5000) açın. Sadece konsol döngüsü için `python main.py` (web olmadan).

**Yalnızca Flask paneli (isteğe bağlı olarak arka planda toplayıcı ile):**

```bash
python ui/web/app.py
# veya toplayıcıyı aynı süreçte açmak için:
python ui/web/app.py --with-collector
```

**Tek seferlik JSON raporu:**

```bash
python main.py --once --out reports
```

**Tam boru hatlı PDF (ölçüm + analiz + DB + PDF):**

```bash
python main.py --full-report --out reports
```

## Yapılandırma

- `config/settings.yaml` — örnekleme aralığı, SQLite yolu, LHM JSON URL, rapor dizini, uyarı soğuma süresi.
- `config/thresholds.yaml` — sıcaklık ve kullanım eşikleri.
- `config/scoring_rules.yaml` — sağlık skoru ağırlıkları ve cezalar.

## API (özet)

| Uç nokta | Açıklama |
|----------|----------|
| `GET /api/telemetry` / `GET /api/live-data` | Son telemetri ve grafik serisi |
| `GET /api/health` | Sağlık özeti (snapshot yokken “beklemede” durumu) |
| `GET /api/hardware` | CPU, GPU, RAM, OS özeti |
| `GET /api/integration-status` | LibreHardwareMonitor JSON URL yapılandırması |
| `GET /api/reports/daily-pdf` | Günlük PDF üretir ve indirir |

## Proje yapısı

```
config/          # Ayarlar ve eşikler
core/            # Toplayıcılar, analizörler, uyarılar, skorlama, raporlama
integrations/    # LHM, WMI, smartctl vb.
storage/         # SQLite ve depo
ui/              # CLI dashboard, Flask web
tests/           # pytest
main.py          # Giriş noktası
```

## Harici araçlar

- **LibreHardwareMonitor (LHM):** Ayrı kurulan uygulama; `Options → Remote Web Server → Run` ile JSON (ör. `http://localhost:8085/data.json`) sağlanır. `settings.yaml` içindeki `json_url` ile eşleşmeli.
- **smartctl:** Smartmontools ile birlikte gelir; disk SMART için `config/settings.yaml` altında `integrations.smartctl` ayarlanır.

## Testler

```bash
pytest
```

## Lisans

Depoda belirtilen lisans dosyasına bakın (veya proje sahibine danışın).
