# Análisis previo — ambient_youtube_bot

> **APROBADO 2026-09-13.** Decisiones del dueño del canal:
> - Todos los ajustes de §0 y §1 aceptados (colchón de 2 días, sin Pixabay ni AudioCraft automáticos, coffee shop desactivado).
> - **Prioridad: monetización completa.** `ALLOWED_LICENSES=CC0,PROCEDURAL,OWN` por defecto. CC-BY queda soportado pero apagado, porque la atribución no afecta ingresos pero sí amplía el catálogo de riesgo de procedencia.
> - **Gasto cero:** `DAILY_BUDGET=0`, `MONTHLY_BUDGET=0`, `AI_AUDIO_PROVIDER=none`, `LLM_PROVIDER=none` (metadata con plantillas).
> - Velocidad de subida: **medida el 2026-09-20** — 823 MB en ~20 min (≈0,76 MB/s, ~6 Mbps de subida). Un video de 4 h a 5 Mbps pesa ~6,6 GB, o sea **~2,5 h de subida**; con ~55 min de producción son ~3,5 h de máquina por video diario.
> - Notificaciones: ninguna por ahora (solo dashboard).

> Entregable de la sección 8 del brief (`CLAUDE.md`). Fecha: 2026-09-13.
> Hasta que lo apruebes no se escribe código de implementación.
> Los datos externos verificados llevan la fuente al lado. Lo que **no** pude verificar está marcado como **[SIN VERIFICAR]** y queda como tarea antes de la fase que lo necesite.

---

## 0. Resumen ejecutivo — lo que cambia respecto al brief

Hay cinco hallazgos que obligan a ajustar el plan. Ninguno reabre el modelo de negocio.

| # | Hallazgo | Impacto | Ajuste propuesto |
|---|----------|---------|------------------|
| 1 | **Los proyectos de API sin verificar suben videos bloqueados en privado.** "All videos uploaded via the `videos.insert` endpoint from unverified API projects created after 28 July 2020 will be restricted to private viewing mode" y hace falta pasar una auditoría ([videos.insert](https://developers.google.com/youtube/v3/docs/videos/insert)). | Sin la auditoría, `publishAt` nunca hace público el video: la fase 6 no sirve en producción. | Pedir la auditoría (API Compliance Audit) **en la fase 0**, en paralelo al desarrollo, porque tarda semanas y no depende del código. Mientras tanto, el modo PRODUCTION sube en privado y el dashboard avisa que falta publicarlo a mano. |
| 2 | **La cuota de YouTube cambió.** `videos.insert` y `search.list` ahora tienen **cupos propios de 100 llamadas/día, 1 unidad por llamada**; el resto comparte 10.000 unidades/día. `thumbnails.set` = 50, `playlistItems.insert` = 50, `videos.update` = 50, `videos.list` = 1 ([quota cost](https://developers.google.com/youtube/v3/determine_quota_cost)). | El dato de "~1.600 unidades por subida" del brief quedó viejo. | Contabilizar la cuota **por cupo** (uploads / search / general), no como un solo total. Los costos quedan en configuración, no en el código. |
| 3 | **Pixabay no tiene API de audio.** Su API solo documenta imágenes y videos ([Pixabay API](https://pixabay.com/api/docs/)). | No existe un `PixabayProvider` automático legal: sacar audio de ahí requeriría scraping. | Quitar Pixabay como proveedor automático. Dejarlo solo como **importación manual** (tú descargas, el bot registra la licencia). |
| 4 | **AudioCraft/AudioGen no se puede usar comercialmente.** "The models weights in this repository are released under the CC-BY-NC 4.0 license" ([AudioCraft README](https://github.com/facebookresearch/audiocraft/blob/main/README.md)). | Queda descartado por la regla 3.3. | Quitarlo de la lista de proveedores de IA. |
| 5 | **pytrends está muerto.** Se archivó el 17-04-2025 y ya no funciona de forma confiable; la API oficial de Google Trends sigue en alfa cerrada ([issue #636](https://github.com/GeneralMills/pytrends/issues/636), [DEV](https://dev.to/esteban_ortega/pytrends-is-dead-heres-how-to-get-google-trends-data-in-2026-1a18)). | La investigación de keywords no puede depender de Trends. | Research basado en el autocompletado de YouTube, `search.list` y `videos.list`. Trends queda como fuente opcional si se consigue acceso a la API oficial. |

Además, recomiendo tres cambios operativos (detallados en §1 y §9):

- **Un colchón de videos listos (buffer):** producir con 2 días de adelanto. Si algo falla a las 3 a.m., se publica un video que ya está `READY` y el fallo se reintenta con calma.
- **Separar producir de subir.** Un video de 4 h pesa ~9 GB (§9). La subida puede tardar horas según tu ancho de banda, así que debe ser un trabajo aparte, reanudable.
- **Riesgo de "contenido no auténtico" a nivel de canal.** La política (renombrada el 15-07-2025) dice: "channels where content feels interchangeable from video to video are not allowed to monetize" ([YPP policies](https://support.google.com/youtube/answer/1311392)). La revisión la hacen personas y juzgan **el canal completo**, no cada archivo. Que el audio pase la autocorrelación es necesario, pero no alcanza: cada video tiene que tener un **concepto distinto y reconocible** (lugar, clima, hora del día, grabación concreta). Por eso el selector diario (§5.6) va a puntuar la distancia de concepto contra los videos recientes, no solo evitar repetir categoría.

---

## 1. Arquitectura: evaluación y ajustes

La estructura de carpetas del brief es buena y la mantengo. Estos son los ajustes:

### 1.1 Proceso y ejecución
- **Pipeline idempotente como comando.** `main.py produce --video-id X` avanza un video desde su último estado válido. Todo lo demás (scheduler, dashboard, reintentos) lo llama a él. Así se prueba solo, sin scheduler.
- **Dos procesos:** un *worker* (scheduler + pipeline) y el *dashboard* (FastAPI). Se comunican **solo por la base de datos** (el dashboard escribe órdenes como "pausar" o "producir ahora" en una tabla `commands` y el worker las lee). Si se cae el dashboard, la producción sigue.
- **Scheduler:** mantengo `run-scheduler` con APScheduler como pide el brief. Como todavía no sabés dónde va a correr, el despliegue en esta laptop usa además el **Programador de tareas de Windows** con "reactivar el equipo para ejecutar", que sobrevive a reinicios y suspensiones. En Linux sería systemd timer o Docker. Ambos llaman al mismo comando.
- **Colchón de producción:** `BUFFER_DAYS=2`. El scheduler mantiene siempre N videos en `READY` o `SCHEDULED` por delante.

### 1.2 Stack
- **Python 3.12 en un venv (con `uv`).** La máquina tiene 3.14. Algunas librerías de audio (`pyloudnorm`, `soundfile`, `scipy`) pueden no tener todavía wheels probadas en 3.14, y en Windows eso significa compilar. Lo verifico en la fase 0; si todo instala limpio en 3.14, uso 3.14.
- **FFmpeg:** no está instalado. Hay que instalar una build con NVENC (tu RTX 4060 lo soporta). El sistema detecta encoders con `ffmpeg -encoders` y cae a `libx264` si no hay GPU.
- **Base de datos:** SQLAlchemy 2 + Alembic (migraciones reales, listas para pasar a Postgres).
- **Dashboard:** FastAPI + Jinja2 + HTMX. Es más estable que Streamlit para un proceso que corre semanas y permite botones de acción sin recargar todo.
- **Audio:** `numpy`, `scipy.signal`, `soundfile` (lectura/escritura por bloques), `pyloudnorm` para medir LUFS. El paso final de loudness/true peak se valida con `ffmpeg ebur128`.

### 1.3 Cambios en el modelo de datos
- Tabla `commands` (dashboard → worker) y `quota_usage` con columna `bucket` (`uploads`, `search`, `general`).
- `concept_fingerprint` en `videos`: vector de atributos (ambiente, lugar, hora del día, clima, capas, visuales) para medir distancia entre conceptos, además del hash exacto de duplicados de §5.15.
- `sounds.provenance_notes`: por qué se considera segura la licencia (ver riesgo en §6.2).

### 1.4 Desarrollo sin visuales todavía
Todavía no tenés grabaciones. Para las fases 3–5, los tests usan **clips sintéticos generados con FFmpeg** (`testsrc2`, gradientes con ruido) guardados solo en `tests/fixtures/`. El pipeline **rechaza** producir en `output/` si `/assets/visuals/` está vacío. Así se cumple la regla 3.2 y se puede desarrollar igual. Para validar de verdad la fase 3 (4 h sin uniones visibles) hará falta al menos un clip real tuyo de ~20 min.

---

## 2. Cómo se consigue cada sonido sin que vos aportes audio

Hay cuatro técnicas, que se combinan dentro de cada receta:

1. **Síntesis procedural pura**: ruido filtrado, modulado y con eventos estocásticos. Cuesta cero, es infinita y no tiene loops.
2. **Resíntesis granular de grabaciones licenciadas**: se toma una grabación CC0 de 1–5 min y se fabrican 4 h reordenando fragmentos de 5–30 s con puntos de corte aleatorios, crossfades de potencia constante ≥ 4 s, micro-pitch ±1–2 %, nivel ±1–2 dB e inversión ocasional (solo en texturas donde no se nota, como lluvia o río; nunca en pájaros ni truenos). **Es la técnica central** para que un recurso corto no genere un loop detectable.
3. **Eventos puntuales licenciados**: truenos, cantos de pájaro, crujidos y ráfagas de 1–20 s, colocados con un proceso de Poisson de densidad variable. Con un banco de 20–50 eventos distintos por tipo, más variación de pitch, filtro y paneo, la repetición no se percibe.
4. **IA de efectos de sonido**: solo para eventos o texturas que falten en el catálogo y dentro del presupuesto.

Cada capa de una receta declara su método preferido y sus alternativas. El selector aplica el orden del brief: procedural → licenciado → IA → capa no disponible.

---

## 3. Comparativa por categoría: procedural vs. licenciado vs. IA

Escala de calidad: ★ pobre · ★★ aceptable · ★★★ buena · ★★★★ indistinguible de una grabación.
Las calificaciones procedurales son **mi estimación técnica**; se confirman con escucha real al terminar la fase 1.

| Categoría | Procedural | Licenciado (Freesound CC0/CC-BY) | IA (ElevenLabs SFX) | Viabilidad para 3–4 h |
|---|---|---|---|---|
| White / Pink / Brown noise | ★★★★ — es matemática exacta | innecesario | innecesario | Trivial |
| Rain (general), Heavy Rain | ★★★ — banda de ruido + gotas estocásticas | ★★★★ como textura granular | ★★★ en clips de 30 s | Híbrido: cama procedural + textura granular |
| Rain on Window | ★★ — la resonancia del vidrio cuesta | ★★★★ | ★★★ | Granular licenciado + cama procedural |
| Rain on Roof (zinc) | ★★ — el zinc tiene un timbre metálico muy particular | ★★★ (hay pocas grabaciones de zinc) | ★★★ | Granular licenciado; **ideal a futuro con grabación propia** |
| Thunderstorm / Thunder and Rain | Retumbo lejano ★★★; truenos cercanos ★ (suenan falsos) | ★★★★ como eventos | ★★★ como eventos | Cama de lluvia + banco de eventos de trueno licenciados |
| Wind | ★★★★ | ★★★ | ★★★ | Procedural |
| Ocean Waves / Beach | ★★★ — modulación irregular de 8–14 s | ★★★★ | ★★★ | Procedural + textura granular de orilla |
| River Sounds | ★★ — le falta el detalle de burbujeo | ★★★★ | ★★★ | Granular licenciado |
| Waterfall | ★★★ — ruido denso de banda ancha | ★★★★ | ★★★ | Procedural + textura |
| Fireplace / Cozy Cabin | ★★★ — crujidos + rumor | ★★★★ | ★★★ | Cama procedural + eventos de crujido licenciados |
| Birds | ★ — los trinos sintéticos se notan | ★★★★ como eventos | ★★★ | Eventos licenciados; IA si faltan |
| Night Ambience / Forest at Night (grillos, chicharras, ranas) | Grillos ★★★ (tren de pulsos modulados); chicharras ★★; ranas ★ | ★★★★ | ★★★ | Granular licenciado + grillos procedurales |
| Forest Sounds / Nature Sounds | Compuesto | Compuesto | Compuesto | Receta de capas: viento + hojas + pájaros + agua |
| Coffee Shop Ambience | ✗ — no se puede sintetizar murmullo | ★★★ | ★★ | **Riesgo alto** (ver §6.4). Desactivado por defecto |
| Sleep / Study / Focus / Relaxing Ambience | Son **presets de subnicho**, no sonidos | — | — | Recetas que reusan las capas anteriores con otra dinámica: sleep más oscuro y estable, focus sin eventos bruscos |

### Costo, dependencia y riesgo por fuente

| | Procedural | Freesound | ElevenLabs SFX | Stable Audio |
|---|---|---|---|---|
| Costo | 0 (CPU) | 0 | "$0.12 per minute", aunque la misma página dice "billed per generation" ([pricing](https://elevenlabs.io/pricing/api)) — **ambiguo, confirmar antes de la fase 2** | **[SIN VERIFICAR]**: la página oficial no mostró precios; un tercero lo lista a $0.19/generación ([Venice](https://venice.ai/models/stable-audio-25)) |
| Dependencia externa | Ninguna | API + cuenta | API + plan pago | API |
| Riesgo de licencia | Ninguno (`PROCEDURAL`) | Bajo con CC0; medio con CC-BY (atribución obligatoria); ver riesgo de procedencia en §6.2 | Depende de los términos del plan: **[SIN VERIFICAR]** si el uso comercial requiere plan pago | **[SIN VERIFICAR]** |
| Viabilidad 3–4 h | Infinita | Con resíntesis granular | Solo eventos o texturas cortas (máx. 30 s) | Clips de hasta ~3 min según la prensa de Stability ([anuncio](https://stability.ai/news-updates/stability-ai-introduces-stable-audio-25-the-first-audio-model-built-for-enterprise-sound-production-at-scale)) |

---

## 4. Opción más económica por categoría

- **Costo cero total:** todas las categorías excepto coffee shop se pueden hacer con **procedural + Freesound**, que son gratis.
- La IA **nunca** es la opción más barata en ninguna categoría. Su única función es cubrir huecos del catálogo.
- En la práctica, el costo externo del sistema en régimen normal es **$0/día**, más el LLM opcional de metadata (céntimos por video si se activa).

## 5. Opción de mejor calidad por categoría

- **Ruidos (white, pink, brown) y viento:** procedural.
- **Todo lo que tenga "carácter"** (lluvia en ventana o zinc, río, pájaros, insectos nocturnos, fuego, truenos cercanos): **grabación real** (Freesound hoy; grabaciones propias mañana) usada con resíntesis granular.
- **La mejor calidad global es híbrida:** una cama procedural sin loops aporta continuidad, encima van texturas granulares licenciadas que aportan realismo, y encima eventos licenciados que dan vida.
- Un apunte de negocio que no cambia el plan: **tus grabaciones de campo en Costa Rica** (zinc, chicharras, pájaros del bosque nuboso) serían la mejor fuente en calidad y en autenticidad frente a YouTube. El slot `OwnRecordingsProvider` queda listo para cuando las tengas.

---

## 6. Limitaciones concretas de cada opción

### 6.1 Procedural
- Los sonidos biológicos (pájaros, ranas, voces) suenan artificiales.
- Riesgo sutil: un generador mal diseñado puede repetir su propio patrón (por ejemplo, LFOs periódicos). Mitigación: modulación con ruido filtrado en lugar de senos puros y semillas distintas por video; el gate de autocorrelación lo verifica.
- Costo de CPU: ver §9.

### 6.2 Freesound
- **Límites:** "60 requests per minute and 2000 requests per day" ([API overview](https://freesound.org/docs/api/overview.html)). Con catálogo en caché esto sobra.
- **Calidad:** sin OAuth2 solo se obtienen previews con pérdida: `preview-hq-mp3` ~128 kbps y `preview-hq-ogg` ~192 kbps ([resources](https://freesound.org/docs/api/resources_apiv2.html)). Descargar el **original** requiere OAuth2. **Recomendación:** hacer el login OAuth2 una sola vez, como con YouTube, y bajar originales. Recodificar un MP3 de 128 kbps dentro de un AAC final degrada la calidad de forma audible en texturas como la lluvia.
- **Licencias:** Freesound usa CC0, CC BY, CC BY-NC y Sampling+ ([FAQ](https://freesound.org/help/faq/)). CC BY-NC queda **prohibido** ("you can't earn any money with the piece of work you create"). Sampling+ también se rechaza por ser una licencia retirada y ambigua. **Lista blanca: `CC0`, `CC-BY-3.0`, `CC-BY-4.0`.**
- **Riesgo de procedencia (el más importante):** en Freesound cualquiera sube archivos. Una persona puede subir como CC0 un audio que no es suyo, y ahí la licencia registrada no te protege de un reclamo de Content ID. Mitigación:
  - Preferir usuarios con historial, muchas descargas y buenas valoraciones, y archivos con descripción de grabación (equipo, lugar).
  - Rechazar descripciones con palabras de riesgo ("ripped", "from movie", "youtube", nombres de apps de sonidos).
  - Rechazar cualquier grabación con música o voz inteligible, detectado con análisis espectral simple más revisión en el dashboard.
  - Registrar esas señales en `provenance_notes`.
  - Si un video recibe un reclamo, el recurso pasa a una lista negra y se marcan todos los videos que lo usaron.

### 6.3 IA
- **ElevenLabs:** duración "at least 0.5 and at most 30" segundos. Tiene parámetro `loop` en `eleven_text_to_sound_v2` ([API ref](https://elevenlabs.io/docs/api-reference/text-to-sound-effects/convert)). Sirve para eventos, no para camas largas.
- **AudioCraft/AudioGen:** descartado por la licencia NC (§0).
- **Stable Audio:** precio y licencia **[SIN VERIFICAR]**. Se evalúa en la fase 2 antes de integrarlo.
- **YouTube y contenido sintético:** el campo existe y es `status.containsSyntheticMedia`. Sirve para declarar contenido realista alterado o sintético: personas diciendo cosas que no dijeron, eventos reales alterados o "realistic scenes that didn't occur" ([videos resource](https://developers.google.com/youtube/v3/docs/videos)). Un ambiente sonoro sintético sobre imágenes reales tuyas **no parece** estar en esa definición, pero la decisión es tuya. Queda como `DECLARE_SYNTHETIC_MEDIA` (por defecto `false`) y se registra en cada subida.

### 6.4 Coffee shop
- No se puede sintetizar.
- Las grabaciones reales de cafeterías suelen tener **música de fondo con copyright** (riesgo directo de Content ID) y **voces inteligibles** (choca con la regla 3.4).
- Además, en un canal "Costa Rica nature" diluye el concepto.
- **Recomendación:** receta incluida pero `enabled: false`. Solo se activa con recursos revisados a mano en el dashboard.

### 6.5 Research
- El autocompletado de YouTube (`suggestqueries.google.com`) **no es una API oficial documentada**. Puede cambiar o bloquearse. Se usa con caché, límite de tasa y degradación limpia: si falla, se usan las keywords semilla de la receta.
- `search.list` tiene su propio cupo de 100/día (§0). Se usan ~5–10 por video y luego `videos.list` (1 unidad) para ver estadísticas de los resultados top.

---

## 7. Capa de proveedores

```python
class AudioProvider(Protocol):
    name: str
    def search(self, query: SoundQuery, constraints: SoundConstraints) -> list[SoundCandidate]: ...
    def fetch(self, candidate_id: str) -> FetchedSound: ...        # descarga a cache/ y devuelve path + checksum
    def license_info(self, candidate_id: str) -> LicenseRecord: ...  # obligatorio ANTES de fetch
    def cost_estimate(self, query: SoundQuery) -> Money: ...        # 0 para procedural/freesound/own

class ProceduralGenerator:        # capas que se generan en lugar de buscarse
    def render(self, spec: LayerSpec, duration_s: float, seed: int, sink: BlockSink) -> LicenseRecord: ...
```

- **Registro por nombre:** `providers.yaml` o `.env` activa y ordena los proveedores (`AUDIO_PROVIDERS=own,freesound,elevenlabs`). Agregar uno nuevo es crear una clase y registrarla; el mixer no cambia.
- **El mixer nunca habla con un proveedor.** Recibe `ResolvedLayer` (path o generador + `LicenseRecord` ya validado) desde el `SourceSelector`.
- **`SourceSelector`** aplica la cadena procedural → catálogo local → proveedores licenciados → IA. Antes de cualquier llamada con costo pregunta al `BudgetGuard`.
- **Primero el catálogo:** antes de llamar a una API se busca en la tabla `sounds` por tags y features. Con el tiempo el catálogo crece y las llamadas externas tienden a cero.
- **Tests:** cada proveedor tiene un doble falso (`FakeProvider`); los tests nunca llaman a APIs reales.

---

## 8. Sistema de licencias

### 8.1 Modelo
```
licenses
  id, sound_id → sounds.id
  license_type        # enum de la lista blanca: CC0 | CC-BY-3.0 | CC-BY-4.0 | OWN | PROCEDURAL | AI_COMMERCIAL
  license_url         # URL canónica de la licencia
  source_url          # página del recurso (p. ej. https://freesound.org/s/12345/)
  provider, provider_asset_id, author
  attribution_required: bool
  attribution_text    # generado desde plantilla por tipo de licencia
  restrictions        # texto/JSON (p. ej. "no standalone redistribution")
  acquired_at, terms_snapshot_path   # copia del texto de la licencia/ToS al momento de adquirir
  provenance_notes, verified_by (auto|manual), blacklisted: bool, blacklist_reason

video_sounds  (video_id, sound_id, layer, seconds_used)   # uso real por video
```

### 8.2 Validación
- `LicenseValidator.assert_usable(sound)` se llama **en tres puntos:** al entrar al catálogo, al resolver una capa y en el quality gate final.
- Bloquea si: el tipo no está en `ALLOWED_LICENSES`, falta algún campo obligatorio, `blacklisted=true`, o la licencia requiere atribución y no hay texto.
- El gate final recorre `video_sounds` completo: si un solo recurso falla, el video queda `FAILED` y no avanza.

### 8.3 Atribución y reporte
- `attribution.py` arma el bloque de créditos en inglés con el formato recomendado por Freesound: `"<title>" by <author> (<url>) licensed under CC BY 4.0`. Se agrega al final de la descripción **solo si algún recurso lo exige**.
- Si el bloque no entra en el límite de la descripción, se pone un resumen y la lista completa se deja en el comentario fijado. Esto último **[SIN VERIFICAR]**: hay que confirmar en los términos de CC BY que es atribución suficiente; si no lo es, esos recursos no se usan.
- `output/<video_id>/licenses.json` incluye todos los recursos, con licencia, URLs, segundos usados y hash, además del texto de atribución publicado.

---

## 9. Operación diaria: tiempos, disco, caché y fallos

### 9.1 Estimaciones en tu máquina (Ryzen 7 8845HS, 16 hilos · RTX 4060 · 31 GB RAM)

> **Medido 2026-09-13 (fase 1)**: `heavy_rain_window`, 240 min, 2 capas procedurales, un solo hilo → mezcla 16,1 min · master 5,9 min (un intento, −18,00 LUFS) · quality check de audio 2,5 min · **total 24,5 min**. `audio.flac` = 2,8 GB. Mejor que la estimación de abajo; el cuello de botella real será el video.
>
> **Medido 2026-09-13 (fase 3)**: video de 240 min desde un clip de 20 min (1080p30, NVENC, 6 variantes × 10 min) → segmentos 19,6 min · transiciones 9 s · ensamblado + AAC 4,1 min · **render 24 min** · quality check de video 5,5 min. `video.mp4` = 8,9 GB. **Video completo de 4 h (audio + video + controles) ≈ 55 min.**
Son **estimaciones de orden de magnitud** que se miden de verdad al cerrar las fases 1 y 3, y se registran en DB por etapa.

| Etapa (video de 4 h, audio 48 kHz estéreo) | CPU | Con GPU (NVENC) |
|---|---|---|
| Síntesis procedural (3–5 capas, por bloques, en paralelo) | 10–25 min | igual (es CPU) |
| Resíntesis granular + eventos + automatización | 5–15 min | igual |
| Masterizado (EQ, compresión, limitador) + medición LUFS | 5–10 min | igual |
| Codificación de audio final (AAC u Opus) | 1–3 min | igual |
| **Segmentos visuales base** (~4 variantes × 15 min con crossfades, Ken Burns y color) | 60–150 min con x264 | **15–40 min** con NVENC |
| Transiciones entre segmentos + concatenación `-c copy` + mux | 5–10 min | 5–10 min |
| Quality check (autocorrelación, frames negros, uniones) | 10–20 min | 10–20 min |
| Miniaturas + metadata | 1–2 min | 1–2 min |
| **Total** | **~2–4 h** | **~1–2 h** |

Notas sobre video:
- `zoompan` (Ken Burns) en FFmpeg es lento y suele temblar por redondeo de píxeles. En la fase 3 comparo `zoompan` contra un escalado previo más `crop` con coordenadas subpíxel, y elijo el más suave.
- Concatenar con `-c copy` exige que todos los segmentos tengan **exactamente** los mismos parámetros de codificación y GOP cerrado. Las uniones no se hacen cortando: se renderiza un **clip de transición** corto (crossfade de ~4 s entre el final de A y el inicio de B), y la concatenación queda A → T(A,B) → B.

### 9.2 Disco por video
| Archivo | Tamaño aprox. |
|---|---|
| Intermedios de audio (float32, se borran al terminar) | ~5,5 GB por capa × 4 h; se mezcla en streaming para no guardar todas las capas a la vez. Pico ~6–12 GB |
| `audio.flac` final (entregable) | ~1,5–2,5 GB |
| Segmentos visuales (se reutilizan dentro del mismo video) | ~2–4 GB |
| `video.mp4` final de 4 h a 1080p (H.264 ~5 Mbps; HEVC ~3,5 Mbps) | **~9 GB** H.264 / ~6,5 GB HEVC |
| **Pico durante producción** | **~20–30 GB** |

Con 400 GB libres alcanza, pero el disco se llena en un mes si nadie limpia. Política propuesta (`RETENTION_DAYS`):
- Intermedios: se borran al llegar a `READY`.
- `video.mp4`: se borra N días después de `PUBLISHED`.
- Metadata, `licenses.json`, reportes y miniaturas: se conservan siempre.
- Si el espacio libre baja de `MIN_FREE_DISK_GB`, no se empieza a producir y aparece una alerta en el dashboard.

### 9.3 Subida
- Límite de archivo: 256 GB ([videos.insert](https://developers.google.com/youtube/v3/docs/videos/insert)). No hay problema.
- **El cuello de botella es tu ancho de banda de subida:** 9 GB a 20 Mbps de subida son ~1 h; a 5 Mbps, ~4 h. La subida es resumable, con reanudación si se corta, y corre como trabajo aparte de la producción. **Necesito saber tu velocidad de subida** para elegir entre H.264 y HEVC y el bitrate.

### 9.4 Caché
- `cache/sounds/<provider>/<id>.<ext>` con checksum: se descarga una vez y se usa siempre.
- `cache/research/`: resultados de autocompletado y búsquedas con TTL de 7 días.
- `cache/visual_segments/`: segmentos base por `(visual_id, variante, parámetros)`. Pueden reutilizarse entre videos solo si el control de duplicados lo permite.

### 9.5 ¿Qué pasa si una etapa falla a las 3 a.m.?
1. La etapa lanza una excepción. El pipeline la registra en `errors` (con traceback, etapa, `video_id` e intento) y en `states_log`.
2. **Reintento con backoff exponencial** (`MAX_RETRIES`, `BACKOFF_BASE`). Cada etapa escribe en un directorio temporal `work/<video_id>/<stage>.tmp` y solo al terminar lo **renombra** al definitivo. Por eso un reintento nunca ve archivos a medio escribir, y reanudar = saltar las etapas cuyo artefacto final ya existe y tiene checksum válido.
3. **Sin procesos huérfanos:** cada FFmpeg se lanza en un grupo de procesos (Job Object en Windows) con timeout. Al fallar o cancelar se mata el grupo entero y se borra el `.tmp`.
4. Si se agotan los reintentos: estado `FAILED` con `failed_from_state`. El scheduler prueba **otra receta** para ese día, siempre que quede tiempo antes de la hora de publicación.
5. **Gracias al colchón de 2 días**, la publicación de mañana no se afecta: ya hay un video `READY`. El dashboard muestra la alerta y, si se configura, se envía una notificación (Telegram, email o push; se decide después).
6. Si la **cuota** o el **presupuesto** se agotan: aborto limpio de esa operación, estado de espera y reanudación automática cuando se renueva el cupo (medianoche hora del Pacífico para YouTube, según [quota cost](https://developers.google.com/youtube/v3/determine_quota_cost)).
7. Si la **laptop estaba apagada o suspendida:** el Programador de tareas la despierta. Si igual se perdió la ventana, al arrancar el worker detecta trabajos atrasados y los ejecuta.

---

## 10. Plan por fases con entregables

Cada fase termina con tests en verde y una demo que podés correr vos.

| Fase | Entregables | Cómo lo verificás |
|---|---|---|
| **0 — Esqueleto** | venv, `requirements.txt` fijado, config con `pydantic-settings`, `.env.example` documentado, logging con rotación, SQLAlchemy + Alembic con todas las tablas, CLI (`plan`, `produce`, `run-scheduler`, `dashboard`), detección de FFmpeg/NVENC. **En paralelo, trámite tuyo:** crear el proyecto de Google Cloud y pedir la auditoría de la API. | `python main.py doctor` muestra FFmpeg, GPU, DB y configuración. `pytest` en verde. |
| **1 — Audio procedural** | Generadores (white, pink, brown, rain, window, wind, ocean, river, waterfall, thunder, fire, crickets), recetas YAML con esquema validado, mixer por bloques (capas, eventos Poisson, automatización lenta, crossfades), processor (EQ, compresión, limitador, loudness −18 LUFS / −1 dBTP), autocorrelación básica. | `produce-audio --recipe heavy_rain_window --minutes 240` genera `audio.flac` de 4 h y pasa el gate de audio. Te paso fragmentos para escuchar. |
| **2 — Proveedores y licencias** | Interfaz `AudioProvider`, Freesound (OAuth2, originales, filtro de licencia, heurísticas de procedencia), catálogo en DB, resíntesis granular, `LicenseValidator`, `licenses.json`, atribuciones, `OwnRecordingsProvider`, importador manual (para Pixabay u otros), ElevenLabs detrás de flag + `BudgetGuard`. Verificación previa de precios y términos de ElevenLabs y Stable Audio. | Una receta con pájaros o río usa Freesound, genera atribución si corresponde y bloquea un recurso con licencia NC en un test. |
| **3 — Visuales y video** | Indexador de `/assets/visuals/` + `visuals.yaml`, emparejamiento por tags, compositor (loops con crossfade, ping-pong, Ken Burns, oscurecimiento sleep, color por sección, viñeta), generador de filtergraphs, NVENC con respaldo x264, segmentos + transiciones + concat. | Con un clip tuyo de 20 min → video de 4 h; detector de uniones sin alertas; revisión visual tuya de las transiciones. |
| **4 — Metadata, research y miniaturas** | Research (autocompletado + `search.list` + `videos.list` con presupuesto de cuota), ranking de keywords, títulos por plantilla + LLM opcional con puntuación, descripción, tags ≤ 500 caracteres, miniaturas desde frames (nitidez, exposición), texto en inglés con fuente OFL incluida, 3–5 variantes, `localizations` opcional. | `metadata.json` y `thumbs/` generados; test que falla si aparece español en campos principales. |
| **5 — Quality gate y duplicados** | Todos los checks de §5.16, reporte `quality_report.json`, hash de combinación, similitud de títulos, distancia de concepto. | Tests con audio con loop artificial, silencio, clipping y un título casi duplicado: todos deben ser rechazados. |
| **6 — YouTube y scheduler** | OAuth2 (token fuera del repo), subida resumable, miniatura, `publishAt`, playlist, `selfDeclaredMadeForKids=false`, `containsSyntheticMedia` configurable, cuota por cupo, trabajo de subida separado, APScheduler + pipeline de estados + reintentos + reanudación + colchón. | En `MODE=test`, el pipeline completo con `VIDEO_DURATION=5` termina en `READY`. Con tu OK, una subida real **privada** de prueba. |
| **7 — Dashboard y operación** | FastAPI + HTMX (videos, estados, errores, costos, cuota, licencias por video, visuales, configuración, pausar, producir ahora), corte automático por presupuesto, retención de disco, Programador de tareas de Windows + Dockerfile/systemd para migrar, README con credenciales de Google y primer video en TEST. | Seguís el README desde cero y producís un video en TEST sin ayuda. |

---

## 11. Lo que necesito que decidas o me confirmes

1. **¿Aprobás el análisis y los ajustes de §0 y §1?** Sobre todo: colchón de 2 días, quitar Pixabay y AudioCraft como proveedores automáticos, y coffee shop desactivado por defecto.
2. **Licencias CC-BY:** ¿aceptás atribuciones en la descripción, o preferís usar **solo CC0** (menos catálogo, descripciones más limpias)?
3. **Velocidad de subida de tu internet** (medila en speedtest). Define el códec y el bitrate.
4. **Presupuesto:** valores iniciales para `DAILY_BUDGET` y `MONTHLY_BUDGET`. Mi sugerencia: $1/día y $15/mes, sin IA de audio al principio.
5. **LLM para metadata:** ¿plantillas solas al inicio, o activar un LLM (por ejemplo, la API de Claude) desde la fase 4?
6. **Auditoría de YouTube:** ¿podés crear el proyecto en Google Cloud y pedir la auditoría durante la fase 0? Te preparo la guía paso a paso.
7. **Notificaciones de fallos:** ¿por qué canal querés enterarte (Telegram, email, ninguno por ahora)?
