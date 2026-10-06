# ambient_youtube_bot

Bot que produce y publica **todos los días, sin intervención**, videos de 3–4 h de música lofi y ambiente para estudiar en el canal de YouTube **Costa Rica Ambience**. También alimenta un **directo 24/7** desde un servidor en la nube.

## Resumen del proyecto

**Qué hace, de punta a punta:**
1. **Planifica:** elige receta, duración y hora de publicación.
2. **Investiga keywords en inglés** con las sugerencias de búsqueda de YouTube.
3. **Audio:** arma lluvia real a partir de grabaciones CC0 de Freesound con licencia verificada.
4. **Música:** genera **temas lofi nuevos para cada video** con un modelo de IA local (ACE-Step, Apache-2.0) y los mezcla sobre la lluvia.
5. **Video:** renderiza la escena ilustrada **animada en loop** (Wan 2.2) con la GPU.
6. **Publicación:** crea miniatura y metadata, pasa un **quality gate** automático y sube y programa el video con la YouTube Data API. La API pasó la auditoría de cumplimiento de Google.
7. **Directo:** manda cada video a una VM de Oracle Cloud que transmite 24/7 a YouTube Live.

**Ingeniería destacada:**
- **Pipeline como máquina de estados reanudable**, con estados en SQLite.
  - Cada etapa deja un marcador; tras un corte de luz o un reinicio, retoma desde la última etapa válida.
  - Errores de infraestructura: reintentos con backoff.
  - Errores de contenido: se descartan sin reintentar.
- **Quality gate de audio y video**, todo medido, nada a ojo:
  - Audio: loudness BS.1770 (−18 LUFS, −1 dBTP), silencios, detección de loops por autocorrelación espectral, clics en empalmes, y "calma para estudiar" (picos de loudness de corto plazo).
  - Video: cuadros negros y saltos visibles en empalmes.
  - Duplicados de concepto.
- **Procedencia y licencias:** cada sonido queda registrado con licencia, autor y URL.
  - Se descartan grabaciones con voces, tráfico o texturas irregulares, con detectores propios.
  - Cada tema generado guarda modelo, licencia, semilla y prompt.
- **Video eficiente:**
  - Cada variante se renderiza una sola vez con NVENC y el resto se ensambla por *stream copy*.
  - Loops animados sin costura: fundido del inicio sobre el final, verificado con métricas por cuadro.
  - H.265 para la mitad de peso.
- **Directo 24/7 casi sin CPU:** el servidor gratuito solo reenvía paquetes ya codificados (FFmpeg `-c copy`, ~1 % de CPU) bajo `systemd`, con reinicio automático.
- **IA local y gratuita:** ComfyUI se controla por su API HTTP y el bot lo arranca y apaga para liberar memoria antes del render.

**Stack:**
- Python 3.14, SQLAlchemy y Alembic (SQLite), APScheduler, FastAPI (panel de control) y pytest.
- FFmpeg (NVENC), NumPy y SoundFile.
- YouTube Data API v3 (OAuth2) y Freesound API v2 (OAuth2).
- ComfyUI, ACE-Step y Wan 2.2.
- Oracle Cloud (Ubuntu, systemd).
- Windows Task Scheduler.

**Resultados medidos:**
- **Producción:** un video de 3,5 h sale en ~2 h en una laptop con RTX 4060.
- **Música:** ~90 s de GPU por tema de 3 min.
- **Archivo:** 3,1 GB en H.265.
- **Tests:** 242 automatizados.

---

- Especificación original: `CLAUDE.md` · Análisis aprobado: `docs/ANALYSIS.md` · Formato lofi: `docs/LOFI.md` · Directo 24/7: `deploy/stream/README.md` · Auditoría de la API: `docs/YOUTUBE_AUDIT.md`
- Todo lo que ve YouTube está en inglés.
- El ambiente son grabaciones reales con licencia verificada (CC0 de Freesound o propias).
- La música la genera el canal con un modelo de licencia Apache-2.0.

---

## 1. Instalación (Windows)

```powershell
winget install Gyan.FFmpeg            # FFmpeg con NVENC
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env           # revisá cada variable; están documentadas
.\.venv\Scripts\python.exe main.py db-upgrade
.\.venv\Scripts\python.exe main.py doctor
.\.venv\Scripts\python.exe -m pytest
```

`doctor` verifica Python, FFmpeg y encoder de GPU, base de datos, credenciales fuera del repo, visuales y disco. En los ejemplos siguientes, `python` es `.\.venv\Scripts\python.exe`.

## 2. Credenciales de Google / YouTube

1. En Google Cloud: crear proyecto → habilitar **YouTube Data API v3** → Google Auth Platform con público **Externo**, scopes `youtube.upload` y `youtube`, y estado **En producción**. En "Prueba" el token caduca cada 7 días. Para uso personal no hace falta verificación de Google.
2. Crear un cliente OAuth **App de escritorio** y guardar el JSON **fuera del repo**, por ejemplo `C:\Users\<vos>\.ambient_bot\client_secret.json`.
3. En `.env`: `YOUTUBE_CLIENT_SECRETS_PATH` y `YOUTUBE_TOKEN_PATH`, ambos fuera del repo.
4. `python main.py youtube-auth` → elegí el canal del bot, **no** tu canal personal.
5. Opcional: `python main.py youtube-test-upload --confirm` sube un video **privado** de 30 s.

La **auditoría de la API de YouTube** está aprobada (2026-09-20): las subidas por API ya pueden ser públicas o programadas con `publishAt`. Ver `docs/YOUTUBE_AUDIT.md`.

> **El canal tiene que estar verificado por teléfono** (https://www.youtube.com/verify). Sin eso YouTube corta las subidas en **15 minutos** — un video más largo se sube completo y después aparece como *"Processing abandoned: video is too long"* — y `thumbnails.set` responde 403. Comprobado el 2026-09-20 con un video de 30 min.

## 3. Tu footage

1. Copiá clips (MP4/MOV, ≥ 30 s) y fotos (JPG/PNG/WEBP) a `assets/visuals/`. El bot nunca los modifica.
2. Etiquetalos con nombres descriptivos (`rain_window_01.mp4` → `rain`, `window`) o con `assets/visuals/visuals.yaml` (ver `visuals.example.yaml`). Las etiquetas se cruzan con `visual_tags` de cada receta.
3. `python main.py index-visuals`.
4. `python main.py visuals` dice qué recetas ya se pueden producir con lo que hay y qué etiquetas conviene filmar después.

Guía de qué grabar y con qué requisitos técnicos: `docs/GRABACION_VISUALES.md`.

## 4. Primer video en modo TEST

Con `MODE=test` (el valor por defecto) el pipeline completo llega a `READY` y **no sube nada**.

```powershell
python main.py plan                                # qué se produciría y cuándo
python main.py produce --recipe heavy_rain_window  # o sin --recipe: lo elige el selector
python main.py videos                              # estados
```

Para una prueba rápida, poné `VIDEO_DURATION=5` en `.env` (5 minutos).

El paquete queda en `output/<video_id>/`:

| Archivo | Contenido |
|---|---|
| `video.mp4` | video final (H.264 + AAC) |
| `audio.flac` | audio masterizado, −18 LUFS / −1 dBTP |
| `thumb_selected.jpg`, `thumbs/` | miniatura elegida y variantes |
| `metadata.json` | título, descripción, tags, idioma y candidatos puntuados |
| `licenses.json` | cada fuente de audio con su licencia |
| `quality_report.json` | resultado del gate: audio, video, metadata, licencias y duplicados |

## 5. Operación diaria

```powershell
python main.py run-scheduler     # worker: ciclo diario a CREATE_HOUR + órdenes del panel cada minuto
python main.py dashboard         # panel en http://127.0.0.1:8000
```

El ciclo diario:
1. Mantiene `BUFFER_DAYS` de videos listos por adelantado.
2. Retoma los videos a medias.
3. Produce los que faltan (si una receta falla por contenido, prueba otra).
4. En `MODE=production`, sube y programa los `READY` para `PUBLISH_HOUR` (hora del Este).
5. Si `ORACLE_HOST` está configurado, manda los videos subidos al directo 24/7 (`python main.py stream-sync` lo hace a mano). Ver `deploy/stream/README.md`.

**Si algo falla:**
- Cada etapa deja un marcador y el pipeline retoma desde la última etapa válida.
- Los errores de infraestructura se reintentan con backoff (`MAX_RETRIES`, `BACKOFF_BASE`).
- Los errores de contenido (sin footage, sin fuente licenciada, gate rechazado) pasan a `FAILED` sin reintentar.
- Todo queda en la tabla `errors` y en el panel.

**Pasar a producción:** `MODE=production` en `.env` y reiniciar el worker. Antes, conviene producir y revisar un par de videos en TEST.

### Dejarlo corriendo en esta laptop

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_windows_task.ps1
Start-ScheduledTask -TaskName "AmbientBot Worker"
```

La tarea arranca al iniciar sesión y, si el worker no está corriendo (por ejemplo, al despertar de una suspensión), lo vuelve a lanzar en menos de 15 minutos. También puede despertar el equipo. Dejá la laptop enchufada y con los temporizadores de reactivación permitidos. Para quitarla: `-Uninstall`.

### Linux / Docker

Ver `Dockerfile`. Sin GPU el render usa libx264 y tarda bastante más.

## 6. Referencia de comandos

| Comando | Qué hace |
|---|---|
| `doctor` · `db-upgrade` | diagnóstico · migraciones |
| `recipes` | lista de recetas (`app/audio/recipes/*.yaml`, editables) |
| `plan` · `produce [--recipe X] [--video-id ID] [--date AAAA-MM-DD]` · `videos` | planificar, producir o retomar, listar |
| `upload --video-id ID` | subir y programar un video READY (solo production) |
| `run-scheduler` · `dashboard` · `pause` · `resume` | operación |
| `produce-audio --recipe X --minutes N` · `check-audio ARCHIVO` | audio suelto y su quality check |
| `index-visuals` · `produce-video --recipe X --audio audio.flac` | visuales y video suelto |
| `metadata-preview --recipe X` | título, descripción y tags con investigación en vivo |
| `sounds` · `import-sound` · `blacklist-sound ID --reason` · `freesound-auth` | catálogo de audio con licencias |
| `youtube-auth` · `youtube-test-upload --confirm` | conexión con YouTube |

## 7. Cómo funciona

**Audio.** Solo grabaciones reales: **CC0 de Freesound** o tus propias grabaciones en `assets/audio_own/<categoría>/`. Nada sintético (decisión del 2026-10-04):
- Cada receta mezcla dos capas de textura con búsquedas distintas (por ejemplo lluvia en la ventana y lluvia afuera), con 3–4 grabaciones cada una, más eventos sueltos donde corresponde (truenos, crujidos, pájaros).
- Las grabaciones pasan filtros anti-reclamo y de ruido artificial (tráfico, sirenas, motores) y se convierten en horas con resíntesis granular, sin loops.
- Si una capa obligatoria no tiene grabación usable, esa receta no se produce ese día y el selector elige otra. El colchón de 2 días cubre una caída de Freesound.
- Quality check: loudness, true peak, clipping, silencios, loops (autocorrelación) y clics en uniones.

**Video.**
- Hasta 6 segmentos base de ~10 min con fragmentos aleatorios de tus clips, fundidos, zoom y paneo lento, color sutil, espejado opcional y viñeta. En recetas de sleep, oscurecimiento progresivo.
- Se encadenan con fundidos de 4 s y se concatenan sin recodificar.
- Quality check: duración, resolución, fps, cuadros negros y saltos en uniones.

**Metadata.**
- Títulos con la fórmula *qué + dónde + duración + para qué*, puntuados, con "Costa Rica" solo cuando el ambiente lo justifica.
- Keywords del autocompletado de YouTube, con lista blanca contra términos engañosos.
- Tags ≤ 500 caracteres, descripción sin enlaces externos, control de español en campos principales.

**Duplicados.** No se repite la combinación exacta receta + sonidos + visuales + título, ni un título casi igual (120 días), ni la misma receta con el mismo footage (14 días).

**Costos.** `DAILY_BUDGET` y `MONTHLY_BUDGET` en 0: ninguna llamada con costo se ejecuta y todo funciona con recursos gratuitos.
