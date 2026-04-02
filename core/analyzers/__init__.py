"""
Telemetri analizörleri — katmanlı mimari + veri bağlamı:

**Veri bağlamı:** :class:`analysis_context.AnalysisDataContext` — anlık satırlar ve
``storage/repository`` üzerinden son N kayıt (bileşen/metrik başına, trend için);
üretim: :func:`context_builder.build_analysis_context`.

**Katman A — Threshold (eşik):** Anlık değerleri ``config/thresholds.yaml`` limitleriyle
karşılaştırır; uygulama: :class:`TelemetryRowAnalyzer`.

**Katman A2 — Termal korelasyon:** ``load`` / ``temperature`` / ``fan_speed`` ve depo
geçmişi; düşük yükte sıcaklık artışı → soğutma uyarısı. Uygulama:
:class:`thermal_analyzer.ThermalCorrelationAnalyzer`.

**Katman B — Trend (pencereleme):** Bellekte rolling window; uygulama:
:class:`performance_analyzer.PerformanceAnalyzer`.

**Katman C — Korelasyon:** Çapraz sensör kuralları; uygulama:
:class:`correlation_analyzer.CorrelationAnalyzer`.
"""
