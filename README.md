# ambient_youtube_bot

Bot que produce videos diarios de ambiente sonoro (3–4 h) para YouTube. Especificación completa en `CLAUDE.md`; análisis aprobado en `docs/ANALYSIS.md`.

Estado: **fase 0 (esqueleto)**. El README completo llega en la fase 7.

## Puesta en marcha

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env        # y ajustá valores
.\.venv\Scripts\python.exe main.py db-upgrade
.\.venv\Scripts\python.exe main.py doctor
.\.venv\Scripts\python.exe -m pytest
```

Requiere FFmpeg (`winget install Gyan.FFmpeg`). `doctor` lo encuentra aunque la terminal no tenga el PATH actualizado.

## Comandos

| Comando | Fase |
|---|---|
| `doctor` — entorno, FFmpeg/GPU, DB, config | 0 |
| `db-upgrade` — migraciones | 0 |
| `produce --recipe X [--minutes N]` | 1 |
| `plan`, `run-scheduler` | 6 |
| `dashboard` | 7 |
