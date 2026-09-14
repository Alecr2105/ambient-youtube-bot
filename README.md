# ambient_youtube_bot

Bot que produce videos diarios de ambiente sonoro (3–4 h) para YouTube. Especificación completa en `CLAUDE.md`; análisis aprobado en `docs/ANALYSIS.md`.

Estado: **fase 2 (proveedores y licencias)**. El README completo llega en la fase 7.

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
| `recipes` — lista las recetas de ambiente | 1 |
| `produce-audio --recipe X [--minutes N] [--seed S]` — renderiza audio masterizado y corre el quality check | 1 |
| `check-audio ARCHIVO [--minutes N]` — quality check de audio sobre cualquier archivo | 1 |
| `produce`, `plan`, `run-scheduler` | 6 |
| `dashboard` | 7 |

## Audio

- Recetas editables en `app/audio/recipes/*.yaml`. Los valores `[min, max]` se sortean por video según la semilla, así que dos videos de la misma receta nunca son iguales.
- Cada render sale en `output/audio_runs/<receta>_<fecha>_<semilla>/` con `audio.flac` (48 kHz / 24 bit), `recipe_instance.json`, `events.json` y `quality_report.json`.
- Master: −18 LUFS integrados, true peak ≤ −1 dBTP (configurable con `TARGET_LUFS` y `TARGET_TRUE_PEAK`).

## Fuentes de audio (híbrido)

Cada capa o evento de una receta es `procedural` (generado por código, licencia `PROCEDURAL`) o `library` (grabaciones reales). Para `library` el orden es:

1. Catálogo local (`python main.py sounds`).
2. Grabaciones propias en `assets/audio_own/<categoría>/`.
3. Freesound, **solo CC0**, con filtros anti-reclamo: original sin pérdida, descripción de grabación, historial de descargas y valoraciones, sin palabras de riesgo (música, voz, película…) y sin modulación tipo voz.
4. Si no hay nada utilizable: el generador procedural de respaldo de la receta. Si la capa o el evento es `required` y no tiene respaldo, la receta no se produce.

Las grabaciones se convierten en horas sin loops mediante resíntesis granular (fragmentos aleatorios, crossfades ≥ 4 s, micro-pitch y nivel). Cada render deja `licenses.json` con todas las fuentes usadas.

### Conectar Freesound (gratis)

1. Creá una cuenta en freesound.org y pedí credenciales de API en https://freesound.org/apiv2/apply/ (sin callback URL).
2. En `.env`: `FREESOUND_CLIENT_ID=<client id>`, `FREESOUND_API_KEY=<client secret / api key>` y `FREESOUND_TOKEN_PATH` fuera del repo.
3. `python main.py freesound-auth` y pegá el código que muestra Freesound (bajar originales requiere OAuth2).

Otros comandos: `import-sound` (registrar un sonido descargado a mano, con su licencia y URL), `sounds` (catálogo), `blacklist-sound ID --reason "..."` (nunca volver a usarlo, p. ej. tras un reclamo).
