# Ambient YouTube Bot — Brief para Claude Code

> **Cómo usar este archivo:** guardalo como `CLAUDE.md` en la raíz del repo (para que Claude Code lo lea siempre) **o** pegalo completo como primer mensaje. Antes de escribir código, Claude Code debe completar el **Análisis previo obligatorio** (sección 8) y esperar mi aprobación.

---

## 1. Contexto — lo que ya está decidido

Este proyecto nace de una decisión estratégica ya tomada. No la reabrás ni propongas alternativas al modelo de negocio; enfocate en ejecutarlo bien.

**El canal:** un canal de YouTube de sonidos ambientales y relajantes, nuevo, independiente de cualquier otro canal mío. Publica videos largos (3–4 horas), ~1 por día, sin voz ni narración.

**El público:** Estados Unidos primero, luego Reino Unido, Canadá, Australia y resto del mundo anglófono. Esto NO es una preferencia estética: el RPM por vista en EE.UU. es entre 5 y 17 veces mayor que en Latinoamérica, y el canal existe para monetizar a través del Programa de Socios de YouTube (YPP). Toda decisión de producto se subordina a esto.

**El diferenciador:** yo vivo en Costa Rica y grabo personalmente todos los recursos visuales (selva, lluvia tropical, ríos, cataratas, bosque nuboso, cafetales, techos de zinc bajo aguacero, volcán). "Costa Rica" es un término de búsqueda aspiracional con demanda real en inglés (rainforest, jungle, tropical rain, pura vida). Es un activo que canales genéricos no pueden usar con honestidad. El sistema debe explotarlo en títulos, descripciones y tags cuando el visual lo justifique.

**El riesgo principal a evitar:** YouTube rechaza o desmonetiza canales por **contenido no auténtico / producido en masa** (imagen estática + audio en bucle, repetido decenas de veces). Todo el diseño de audio y video de este sistema existe para que cada video sea genuinamente distinto y se sienta hecho a mano. Esto es un criterio de aceptación, no una sugerencia.

**El segundo riesgo:** derechos de autor. El sistema no puede usar ni un solo segundo de audio o video cuyo permiso comercial no esté verificado y registrado. Un reclamo de Content ID redirige el 100% de los ingresos al reclamante; una retirada formal es un strike. Tres strikes cierran el canal.

---

## 2. Objetivo del sistema

Construir `ambient_youtube_bot`: un sistema en Python que **cada día, sin intervención manual**:

1. Decide qué ambiente sonoro producir (con variedad, historial y potencial de búsqueda).
2. Investiga keywords en inglés para ese ambiente.
3. **Genera o adquiere el audio por sí mismo** (yo no proporciono sonidos) y construye una mezcla de 3–4 horas continua, natural y sin loops evidentes.
4. Toma **mis** recursos visuales de `/assets/visuals/` y construye el video largo con variación (loops con crossfade, zoom lento, movimiento sutil, ajustes de luz).
5. Renderiza con FFmpeg.
6. Genera miniatura a partir de mis frames, con texto en inglés.
7. Genera título, descripción y tags en inglés, orientados a búsqueda real en EE.UU.
8. Pasa controles de calidad automáticos.
9. Sube y programa por YouTube Data API v3.
10. Registra todo en base de datos: estados, costos, licencias, errores, URLs.

Con un **panel de control** para supervisar, pausar, configurar y ejecutar manualmente.

---

## 3. Restricciones no negociables

### 3.1 Idioma
- **Todo lo que YouTube lee está en inglés, sin excepción:** título, descripción, tags, texto de miniatura, nombres de playlists, `defaultLanguage` y `defaultAudioLanguage` del video.
- **Nada en español en la señal principal.** Ni títulos mixtos, ni marcas de agua, ni texto en pantalla. Un título bilingüe deja el video en tierra de nadie para el algoritmo.
- Si se quiere alcance hispano, se hace **únicamente** vía `localizations` de la API (título/descripción en `es` como pista alterna), nunca en el campo principal. Esto es opcional y debe estar apagado por defecto (`ENABLE_ES_LOCALIZATION=false`).
- Los logs, comentarios de código y este README pueden estar en español o inglés; el output del bot hacia YouTube, solo inglés.

### 3.2 Recursos visuales
- Se usan **exclusivamente** los archivos que yo coloque en `/assets/visuals/` (MP4, MOV, JPG, PNG, WEBP). Son grabados por mí y propiedad del canal.
- **Prohibido** descargar, buscar o incorporar automáticamente video o imágenes de stock, de bancos, de IA o de cualquier fuente externa como fondo principal.
- El sistema debe poder tomar un solo clip de 20 minutos y construir 4 horas sin que la repetición sea evidente.

### 3.3 Recursos de audio
- **Yo no proporciono sonidos.** El sistema debe obtenerlos, generarlos o sintetizarlos.
- **Prohibido** descargar audio de YouTube, Spotify, SoundCloud o cualquier plataforma de streaming para reutilizarlo.
- **Prohibido** usar cualquier recurso cuya licencia comercial no sea verificable. Si hay duda, se descarta.
- Cada recurso externo registra: URL/fuente, proveedor, tipo de licencia, fecha de adquisición, identificador, restricciones, atribución requerida.
- Dejar un slot de proveedor opcional `own_recordings/` por si en el futuro agrego grabaciones de campo propias (lluvia real de la zona, chicharras, pájaros). Está vacío al inicio; el sistema no debe depender de él.

### 3.4 Sin voz, sin guion
- No narración, no voz sintética, no persona hablando, no subtítulos hablados. Solo **ambiente sonoro + visual**.

### 3.5 Seguridad
- Ninguna credencial en el código. Todo en `.env`, con `.env.example` documentado y `.env` en `.gitignore`.
- El token de OAuth de YouTube se guarda fuera del repo.

### 3.6 Cumplimiento YouTube
- `selfDeclaredMadeForKids: false` en cada subida (obligatorio, es contenido para adultos que trabajan/duermen).
- Evaluar si aplica la declaración de contenido alterado/sintético de YouTube para audio generado por IA; si la API lo permite, exponerlo como configuración. No asumir el nombre del campo: verificarlo en la documentación actual de la API.
- Respetar la cuota de la Data API (10.000 unidades/día por defecto; una subida cuesta ~1.600). Con 1 video/día sobra, pero el sistema debe medir y registrar el consumo y abortar limpiamente si se agota.

---

## 4. Reglas de contenido y SEO (decididas en conversación)

### 4.1 Fórmula de título
`[Qué es] + [Dónde, si aplica] + [Duración] + [Para qué sirve]`

Descriptivo y literal; en este nicho la gente busca exactamente lo que quiere. Nada de títulos creativos.

Ejemplos válidos:
- `Heavy Rain on Window for Deep Sleep — 4 Hours`
- `Costa Rica Rainforest Rain Sounds | 3 Hours for Sleep, Study & Relaxation`
- `Gentle Rain on Tropical Leaves 🌿 Deep Sleep Sounds | Costa Rica Jungle Ambience`
- `Thunderstorm & Rain Sounds for Sleeping — 4 Hours`
- `Cozy Fireplace Ambience for Relaxing & Studying — 3 Hours`
- `Morning Birdsong in the Cloud Forest | 2 Hour Nature Ambience for Focus`

El sistema genera varias alternativas, las puntúa (claridad, coincidencia con keywords investigadas, naturalidad, longitud ≤ 100 caracteres, ausencia de keyword stuffing) y elige la mejor.

### 4.2 Keywords semilla en inglés
`rain sounds for sleeping` · `heavy rain sounds` · `rain on window` · `rain on roof` · `thunderstorm sounds` · `thunder and rain` · `relaxing rain` · `rainforest sounds` · `jungle sounds` · `tropical rain` · `rain on leaves` · `forest sounds` · `forest at night` · `river sounds` · `waterfall sounds` · `ocean waves for sleep` · `beach sounds` · `wind sounds` · `fireplace ambience` · `cozy cabin` · `birds chirping` · `night ambience` · `coffee shop ambience` · `white noise` · `brown noise for sleep` · `pink noise` · `nature sounds for sleeping` · `sleep ambience` · `study ambience` · `focus sounds` · `ambient nature` · `costa rica`

Usar solo los que correspondan al contenido real del video. Cero stuffing.

### 4.3 Subnichos y prioridad
Tres públicos distintos con CPM distinto:
- **Sleep** — máximo tiempo de reproducción, CPM más bajo (la gente duerme).
- **Study / Focus / Work** — buen equilibrio, muy buscado.
- **Meditation / Relaxation / Wellness / Spa** — mejor CPM (anunciantes de bienestar pagan más).

El selector diario debe poder ponderar subnichos por configuración (`SUBNICHE_WEIGHTS`), con valor por defecto que favorezca ligeramente relaxation/meditation sin abandonar sleep y study.

### 4.4 Uso de "Costa Rica"
Incluir "Costa Rica" / "rainforest" / "jungle" / "tropical" / "cloud forest" en título o descripción **solo cuando el visual y el ambiente lo justifiquen** (lluvia tropical, selva, río, catarata, bosque nuboso). No usarlo en un video de chimenea o cafetería.

### 4.5 Descripción
Inglés. Estructura: qué es el ambiente, duración, usos recomendados (sleep, study, focus, meditation, etc.), breve nota del canal (visuales grabados en Costa Rica cuando aplique), timestamps opcionales si hay secciones, y sin enlaces a nada que no sea del canal. Sin keyword stuffing.

### 4.6 Publicación
- Horario orientado a EE.UU.: por defecto programar para las **20:00–22:00 hora del Este (America/New_York)**, configurable.
- Categoría por defecto: Music (`categoryId=10`), configurable.
- `privacyStatus: private` + `publishAt` para programar.

---

## 5. Especificación funcional detallada

### 5.1 Tipos de ambiente
Rain Sounds · Heavy Rain · Rain on Window · Rain on Roof · Thunderstorm · Thunder and Rain · Forest Sounds · Forest at Night · River Sounds · Waterfall · Ocean Waves · Beach Sounds · Wind · Fireplace · Cozy Cabin · Birds · Night Ambience · Coffee Shop Ambience · White Noise · Brown Noise · Pink Noise · Nature Sounds · Sleep Ambience · Study Ambience · Focus Ambience · Relaxing Ambience

Definir cada ambiente como una **receta** (YAML o JSON en `app/audio/recipes/`): capas requeridas, capas opcionales, rangos de nivel por capa, densidad de eventos (truenos, ráfagas, crujidos), método de generación preferido por capa, visuales compatibles (por tag), subnicho, keywords asociadas. Las recetas son datos, no código, para que yo pueda editarlas.

### 5.2 Audio — fuentes (arquitectura modular)
Interfaz común `AudioProvider` con métodos `search(query, constraints)`, `fetch(id)`, `license_info(id)`, `cost_estimate(...)`. Implementaciones independientes y sustituibles:

1. **`ProceduralGenerator`** (local, costo cero, prioridad máxima cuando la calidad lo permite): ruido blanco/rosa/marrón; lluvia sintética (bandas de ruido filtrado + impulsos estocásticos de gota con densidad variable); lluvia en ventana (capa de golpes de frecuencia media con resonancia); viento (ruido rosa por bandpass modulado lentamente); oleaje (ruido rosa/marrón con modulación de amplitud de periodo 8–14 s, irregular); río (ruido de banda ancha con modulación rápida y leve); trueno (ráfaga de ruido con lowpass y decaimiento largo, imagen estéreo ancha); fuego (impulsos de crujido esparcidos + rumor grave); texturas continuas de fondo. Implementar con `numpy`/`scipy`; renderizar por bloques para no cargar 4 horas en RAM.
2. **`LicensedLibraryProvider`** (fuentes con licencia verificable vía API): Freesound (filtrar por licencia CC0 preferentemente; si CC-BY, registrar y generar atribución en la descripción), Pixabay audio, y cualquier otra con API y licencia clara. Guardar siempre el registro de licencia antes de usar. Ideal para lo que es difícil de sintetizar bien: pájaros, grillos, chicharras, cafetería, crujido de madera.
3. **`AIAudioProvider`** (usar solo donde aporte valor real): encapsular proveedores como ElevenLabs Sound Effects, Stability Audio, o modelos locales (AudioCraft/AudioGen) detrás de la misma interfaz. Registrar costo por llamada. **Verificar límites actuales** (duración máxima por clip, precio) en la documentación de cada uno antes de asumir nada.
4. **`OwnRecordingsProvider`** (opcional, vacío al inicio): grabaciones de campo propias en `/assets/audio_own/`, licencia = propietario.

El **selector de fuente** por capa decide con esta lógica: si procedural alcanza calidad aceptable → procedural; si no → librería licenciada; si no hay resultado licenciado adecuado → IA (si el presupuesto lo permite); si nada → marcar la capa como no disponible y elegir otra receta.

### 5.3 Audio Engine (`app/audio/mixer/`, `app/audio/processor/`)
Capacidades obligatorias:
- Combinar pistas, cortar segmentos, mezclar capas con niveles independientes.
- Loops **inteligentes**: nunca repetir un archivo tal cual; usar puntos de corte aleatorios, crossfades largos (≥ 4 s), inversión ocasional, micro-variaciones de pitch (±1–2%) y de nivel (±1–2 dB) por pasada.
- Eventos estocásticos (trueno, ráfaga, gota fuerte, crujido) colocados con distribución aleatoria controlada por la receta; nunca a intervalos regulares.
- Automatización lenta de volumen por capa (LFO muy lento + ruido aleatorio) para que el ambiente "respire".
- Crossfades entre secciones; fade in (~10 s) y fade out (~20 s) del máster.
- EQ por capa, compresión ligera de máster, limitador, normalización a un objetivo de loudness configurable (por defecto **−18 LUFS integrado, −1 dBTP**; YouTube normaliza a −14, ir por debajo evita bombeo).
- Detección y eliminación de silencios accidentales y de clics en las uniones.
- Render por bloques a disco (WAV/FLAC intermedio, AAC o Opus final) para 3–4 horas sin agotar memoria.

Salida: una pista de 3–4 horas que se sienta como un ambiente continuo y natural.

### 5.4 Biblioteca de audio (catálogo en DB)
Cada sonido: `id`, `name`, `category`, `type` (layer/event/bed), `source`, `provider`, `license_type`, `license_url`, `attribution_text`, `duration`, `sample_rate`, `channels`, `bpm` (si aplica), `features` (JSON: loudness, spectral centroid, tags), `restrictions`, `acquired_at`, `cost`, `local_path`, `checksum`.
El sistema busca en el catálogo antes de generar o descargar; cachea resultados en `/cache/`.

### 5.5 Control de licencias (`app/licensing/`)
- Validador que **bloquea** el uso de cualquier recurso sin `license_type` en la lista blanca configurable (`ALLOWED_LICENSES=CC0,PIXABAY,OWN,PROCEDURAL,AI_COMMERCIAL,...`).
- Genera automáticamente el bloque de atribuciones para la descripción cuando la licencia lo exija.
- Exporta un reporte por video (`output/<video_id>/licenses.json`) con todos los recursos usados.

### 5.6 Selección automática del ambiente (`app/scheduler/` + `app/research/`)
Cada día elige receta + variante considerando: historial del canal, publicaciones recientes (no repetir categoría en N días, configurable), variedad de subnicho, visuales disponibles y no usados recientemente, potencial de búsqueda (de la investigación), rendimiento histórico (vía YouTube Analytics API si está disponible; si no, por vistas de la Data API), tendencias estacionales opcionales (p. ej., más "cozy fireplace" en invierno del hemisferio norte).

Ejemplo de rotación semanal esperada:
Día 1 Heavy Rain + Window · Día 2 Thunderstorm + Forest · Día 3 Ocean Waves + Sunset · Día 4 Cozy Fireplace + Cabin · Día 5 Night Forest + Crickets · Día 6 Rain + Distant Thunder · Día 7 Brown Noise + Dark Ambient Visual

### 5.7 Investigación (`app/research/`)
Antes de cada video, investigar keywords en inglés relevantes al ambiente elegido. Fuentes aceptables: autocompletar de YouTube (endpoint público de sugerencias), Google Trends (vía `pytrends` o equivalente), y la Data API (`search.list` con moderación por cuota). Producir un ranking de términos que se use para título, descripción y tags. Sin stuffing.

### 5.8 Visuales (`app/visuals/`)
- Indexar `/assets/visuals/` en DB: `id`, `path`, `type`, `duration`, `resolution`, `fps`, `tags` (asignados por mí en un `visuals.yaml` o inferidos por nombre de archivo), `usage_count`, `last_used_at`, `checksum`.
- Emparejar ambiente ↔ visual por tags (p. ej., `rain,window` con `Rain on Window`).
- Construcción de composiciones largas: loops con crossfade, combinación de varios clips, orden y puntos de entrada aleatorios, inversión (ping-pong) cuando el movimiento lo permita, **Ken Burns lento** en imágenes fijas, oscurecimiento gradual para subnicho sleep, ajuste sutil de brillo/contraste/temperatura por sección, viñeta suave.
- Evitar repetición evidente: registrar el patrón usado y no repetirlo con el mismo clip.

### 5.9 Video / render (`app/video/`)
- FFmpeg como motor principal. Generar la cadena de filtros programáticamente y ejecutar con `subprocess`; sin librerías pesadas de edición.
- Soporte: render local, aceleración por GPU si está disponible (NVENC / VAAPI / VideoToolbox; detectar y caer a x264 si no), H.264 o H.265 configurable, resolución (`1920x1080` por defecto), FPS (`30` por defecto; permitir `24`), bitrate configurable.
- Duración: `VIDEO_DURATION_MIN=180`, `VIDEO_DURATION_MAX=240` (minutos); si `VIDEO_DURATION` está definido, forzar ese valor exacto.
- Estrategia para eficiencia: renderizar un segmento visual base (p. ej., 10–20 min ya con crossfades y variaciones) y concatenar con `-c copy` variando el orden/versión de segmentos, en lugar de re-encodear 4 horas continuas. Comprobar que las uniones no muestren saltos.

### 5.10 Miniaturas (`app/thumbnails/`)
- Solo desde mis frames. Seleccionar candidatos por calidad (nitidez, exposición, contraste, ausencia de fotogramas de transición), aplicar corrección de imagen, añadir texto en inglés grande y legible (2–4 palabras, fuente con licencia libre incluida en el repo), generar 3–5 variantes, puntuar y elegir. Guardar todas en `output/<video_id>/thumbs/`.

### 5.11 Metadata (`app/metadata/`)
Generación de título (varias alternativas, puntuación, elección), descripción, tags (≤ 500 caracteres totales), categoría, idioma, localizaciones opcionales. Puede usarse un LLM vía API para redactar, encapsulado como proveedor sustituible y con costo registrado; también debe funcionar sin LLM con plantillas.

### 5.12 YouTube (`app/youtube/`)
Exclusivamente YouTube Data API v3 oficial con OAuth2 (refresh token persistido fuera del repo). Debe: subir video (resumable), subir miniatura, establecer título/descripción/tags/categoría/idioma, `madeForKids=false`, programar (`private` + `publishAt`), añadir a playlist opcional, aplicar `localizations` si está activado, recuperar URL, registrar resultado y consumo de cuota.

### 5.13 Scheduler
APScheduler (o equivalente). Configurable: `VIDEOS_PER_DAY=1`, hora de creación, hora de publicación, zona horaria (`TIMEZONE=America/Costa_Rica` para el proceso; `PUBLISH_TIMEZONE=America/New_York` para el público), días de la semana activos.
Pipeline con verificación de estado entre etapas; ante fallo: registrar, reintentar con backoff exponencial (`MAX_RETRIES`, `BACKOFF_BASE`), y reanudar desde el último estado válido (idempotencia por `video_id`).

### 5.14 Base de datos
SQLite inicialmente (con SQLAlchemy o `sqlite3` + migraciones simples), diseñada para migrar a Postgres. Tablas: `videos`, `ambients`, `recipes`, `sounds`, `licenses`, `visuals`, `titles`, `descriptions`, `thumbnails`, `states_log`, `youtube_results`, `errors`, `api_costs`, `publish_history`, `research_results`.

Estados (enum): `PLANNED → RESEARCHING → AUDIO_SOURCE_SELECTION → AUDIO_GENERATION → AUDIO_MIXING → VISUAL_SELECTION → VIDEO_PROCESSING → RENDERING → QUALITY_CHECK → READY → UPLOADING → SCHEDULED → PUBLISHED` y `FAILED` desde cualquier punto (con `failed_from_state`).

### 5.15 Control de duplicados
Impedir la repetición exacta de la combinación `(ambiente, conjunto de sonidos, conjunto de visuales, título)`; hash de combinación en DB; rotación con historial; umbral de similitud de título (evitar títulos casi idénticos).

### 5.16 Quality check (`app/quality/`)
Gate obligatorio antes de `READY`. Debe verificar al menos:
- Audio: duración correcta; loudness dentro del objetivo; sin clipping; sin silencios > 2 s; **detección de loops evidentes** (autocorrelación sobre ventanas largas: si hay repetición periódica fuerte, rechazar); sin clics en uniones.
- Video: duración igual al audio (±1 s); resolución/fps correctos; sin fotogramas negros no intencionales; sin saltos en las uniones (comparar frames adyacentes en puntos de concatenación).
- Metadata: título ≤ 100 chars, inglés, sin caracteres raros; descripción presente; tags válidos; `madeForKids=false`; sin texto en español en campos principales.
- Licencias: todos los recursos con licencia permitida y registro completo.
- Duplicados: combinación no usada.
Si algo falla → `FAILED` con detalle, y el scheduler decide reintento o receta alternativa.

### 5.17 Costos
Prioridad: procesamiento local > FFmpeg > procedural > recursos propios > APIs económicas > IA solo con valor real. Registrar el costo de cada llamada externa. `DAILY_BUDGET` y `MONTHLY_BUDGET` en `.env`; al alcanzarse, detener automáticamente toda operación con costo y seguir con lo que sea gratuito (procedural + catálogo ya adquirido).

### 5.18 Panel de control
Interfaz web ligera (FastAPI + HTML/HTMX o Streamlit; elegir lo más simple y estable). Debe permitir: ver videos y estados, próximos videos, errores, costos, sonidos y licencias usadas por video, recursos visuales indexados, añadir/reindexar visuales, configurar duración y horario, activar/desactivar automatización, ejecutar producción manual, pausar publicación, ver consumo de cuota de YouTube, ver reporte de quality check.

### 5.19 Modos
- **TEST** (`MODE=test`, por defecto): ejecuta todo el pipeline hasta `READY`, incluye miniatura y metadata, **no sube nada**. Deja el paquete completo en `output/<video_id>/`.
- **PRODUCTION** (`MODE=production`): crea, valida, sube, programa.

---

## 6. Arquitectura y estructura

Python 3.11+ · FFmpeg · SQLite · APScheduler · `numpy`/`scipy` para síntesis · `pydantic` para configuración/recetas · logging estructurado a `logs/` con rotación.

```
ambient_youtube_bot/
├── app/
│   ├── audio/
│   │   ├── generators/      # procedural (noise, rain, wind, ocean, fire, thunder…)
│   │   ├── providers/       # freesound, pixabay, ai_*, own_recordings (interfaz común)
│   │   ├── recipes/         # YAML/JSON de ambientes
│   │   ├── mixer/           # composición de capas, eventos, automatización
│   │   └── processor/       # eq, compresión, limitador, loudness, fades
│   ├── video/               # construcción de filtergraphs y render FFmpeg
│   ├── visuals/             # indexado, tagging, emparejamiento, composición
│   ├── youtube/             # OAuth, upload, thumbnails, scheduling, quota
│   ├── metadata/            # títulos, descripciones, tags, localizations
│   ├── research/            # keywords, trends, autocompletar
│   ├── scheduler/           # APScheduler, pipeline, reintentos, selección diaria
│   ├── database/            # modelos, migraciones, repositorios
│   ├── quality/             # gates de audio, video, metadata, licencias, duplicados
│   ├── thumbnails/          # selección de frames, texto, variantes
│   ├── licensing/           # validación, registro, atribuciones, reportes
│   ├── dashboard/           # panel de control
│   └── utils/               # ffmpeg wrapper, logging, config, costos
├── assets/
│   ├── visuals/             # MIS archivos (nunca tocados por el bot)
│   ├── audio_own/           # opcional, vacío al inicio
│   └── fonts/               # fuentes con licencia libre para miniaturas
├── output/
├── cache/
├── logs/
├── tests/
├── .env
├── .env.example
├── .gitignore
├── requirements.txt
├── main.py
└── README.md
```

Variables mínimas en `.env.example` (documentadas):
`MODE`, `VIDEOS_PER_DAY`, `VIDEO_DURATION_MIN`, `VIDEO_DURATION_MAX`, `VIDEO_DURATION`, `TIMEZONE`, `PUBLISH_TIMEZONE`, `PUBLISH_HOUR`, `CREATE_HOUR`, `ACTIVE_WEEKDAYS`, `RESOLUTION`, `FPS`, `VIDEO_CODEC`, `VIDEO_BITRATE`, `AUDIO_CODEC`, `AUDIO_BITRATE`, `TARGET_LUFS`, `TARGET_TRUE_PEAK`, `USE_GPU`, `DAILY_BUDGET`, `MONTHLY_BUDGET`, `ALLOWED_LICENSES`, `SUBNICHE_WEIGHTS`, `ENABLE_ES_LOCALIZATION`, `YOUTUBE_CATEGORY_ID`, `YOUTUBE_CLIENT_SECRETS_PATH`, `YOUTUBE_TOKEN_PATH`, `YOUTUBE_PLAYLIST_ID`, `FREESOUND_API_KEY`, `PIXABAY_API_KEY`, `AI_AUDIO_PROVIDER`, `AI_AUDIO_API_KEY`, `LLM_PROVIDER`, `LLM_API_KEY`, `MAX_RETRIES`, `BACKOFF_BASE`, `LOG_LEVEL`.

---

## 7. Prioridades (en orden)

1. Automatización total.
2. Bajo costo.
3. Audio de alta calidad, continuo y natural.
4. Contenido original y no repetitivo (defensa contra "contenido no auténtico").
5. Uso exclusivo de mis recursos visuales.
6. Variación real entre videos.
7. Estabilidad y reanudación ante fallos.
8. SEO en inglés para EE.UU.
9. Operación diaria sin intervención.
10. Cumplimiento estricto de licencias y políticas de YouTube.

---

## 8. Análisis previo obligatorio (antes de escribir código)

Entregá este análisis en un archivo `docs/ANALYSIS.md` y **esperá mi OK** antes de implementar:

1. Análisis de la arquitectura propuesta y ajustes que recomendás, con justificación.
2. Cómo va a conseguir/generar el sistema cada categoría de sonido sin que yo aporte audio.
3. Comparativa **procedural vs. APIs de IA vs. fuentes licenciadas**, por categoría de ambiente: calidad esperada, costo, dependencia externa, riesgo de licencia, viabilidad para 3–4 horas.
4. Cuál es la opción más económica por categoría.
5. Cuál ofrece mejor calidad por categoría.
6. Limitaciones concretas de cada opción (p. ej., duración máxima por clip en APIs de IA, límites de cuota de Freesound, dificultad de sintetizar pájaros o grillos proceduralmente).
7. Diseño de la capa de proveedores para cambiar de fuente sin tocar el resto.
8. Diseño del sistema de licencias (modelo de datos, validación, reporte, atribuciones).
9. Explicación operativa de cómo se producirán videos diarios de 3–4 h sin intervención: tiempos estimados de síntesis, mezcla y render en CPU y GPU, uso de disco, estrategia de cache, y qué pasa si una etapa falla a las 3 a.m.
10. Plan de implementación por fases (sección 9) con estimación de lo que se entrega en cada una.

Donde necesités datos actuales (precios, límites de API, campos de la Data API), **verificalos en la documentación oficial** y citá la fuente en el análisis. No asumas valores de memoria.

---

## 9. Plan de implementación por fases

Implementar en este orden; cada fase debe correr y tener tests antes de pasar a la siguiente.

**Fase 0 — Esqueleto:** estructura, configuración `pydantic`, `.env.example`, logging, DB y migraciones, modelos, `main.py` con CLI (`plan`, `produce --recipe X`, `run-scheduler`, `dashboard`).

**Fase 1 — Audio procedural:** generadores de ruido (blanco/rosa/marrón), lluvia, viento, oleaje, río, trueno, fuego. Mixer con capas, eventos estocásticos, automatización lenta, crossfades. Processor con EQ, compresión, limitador, loudness. Render por bloques. Test: producir 4 horas de "Heavy Rain on Window" solo con procedural y pasar el quality check de audio.

**Fase 2 — Proveedores externos y licencias:** interfaz `AudioProvider`, Freesound y Pixabay con filtrado por licencia, catálogo en DB, validador de licencias, reporte por video, atribuciones. Slot `OwnRecordingsProvider`. Slot `AIAudioProvider` con un proveedor real detrás de flag y presupuesto.

**Fase 3 — Visuales y video:** indexado de `/assets/visuals/`, emparejamiento por tags, composición larga con variación, filtergraphs FFmpeg, render con detección de GPU, concatenación eficiente. Test: de un clip de 20 min producir 4 h sin uniones visibles.

**Fase 4 — Metadata, research y miniaturas:** keywords, generación y puntuación de títulos, descripción, tags, miniaturas desde frames, localizations opcional.

**Fase 5 — Quality gate y duplicados:** todos los checks de 5.16; detección de loops por autocorrelación; control de duplicados.

**Fase 6 — YouTube y scheduler:** OAuth, subida resumable, miniatura, programación, cuota, `madeForKids`, playlist. APScheduler con pipeline de estados, reintentos, backoff, reanudación. Modo TEST por defecto.

**Fase 7 — Panel de control y operación:** dashboard, presupuesto con corte automático, informes de costos, documentación de despliegue (systemd o Docker), README completo con instrucciones para obtener credenciales de Google y correr el primer video en modo TEST.

---

## 10. Criterios de aceptación

- `MODE=test` produce, sin intervención, un paquete completo en `output/<video_id>/` con: `video.mp4` (3–4 h), `audio.flac`, `thumb_selected.jpg` y variantes, `metadata.json` (título, descripción, tags, categoría, idioma), `licenses.json`, `quality_report.json`.
- El audio no contiene loops detectables por autocorrelación ni silencios ni clipping, y está dentro del objetivo de loudness.
- El video usa únicamente archivos de `/assets/visuals/` y no muestra uniones evidentes.
- Ninguna cadena en español en título, descripción, tags o miniatura.
- `madeForKids=false` en la petición de subida.
- Todo recurso de audio externo tiene licencia en la lista blanca y registro completo.
- Dos ejecuciones consecutivas con la misma receta producen resultados distintos (audio, visual y título).
- Si una etapa falla, el sistema reintenta con backoff y, tras agotar reintentos, marca `FAILED` con detalle y no deja procesos huérfanos ni archivos temporales gigantes.
- Al alcanzar `DAILY_BUDGET`, ninguna llamada con costo se ejecuta y el pipeline sigue con recursos gratuitos.
- Tests unitarios para generadores, mixer, licensing, quality y selección; test de integración del pipeline completo en modo TEST con duración reducida (`VIDEO_DURATION=5`).

---

## 11. Qué NO hacer

- No usar audio ni video de YouTube, streaming, bancos sin licencia verificada, ni "encontrado en internet".
- No usar stock visual como fondo principal, aunque sea gratuito.
- No generar voz, narración ni subtítulos hablados.
- No mezclar español en la señal principal de YouTube.
- No usar imagen estática + loop de audio plano: cada video debe pasar el gate anti-repetición.
- No hardcodear credenciales, precios de API ni límites de cuota: todo en configuración y verificado en documentación.
- No entregar pseudocódigo ni explicaciones superficiales: la entrega es una implementación funcional, modular y testeada.
- No reabrir decisiones de negocio ya tomadas (público, idioma, modelo de canal).
