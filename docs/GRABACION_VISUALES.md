# Qué grabar para el canal (y cómo entregarlo al bot)

> **2026-10-04 — cambio de concepto:** el canal ahora usa **escenas lofi ilustradas de Costa Rica** en vez de footage propio (ver `docs/ANALYSIS.md`). Para ilustraciones valen los mismos requisitos de abajo: **16:9, 1920×1080 o más, sin texto en español, sin logos ni marcas**. Escenas que hoy faltan y desbloquean recetas: **cabaña con chimenea** (fuego), **estudiar junto a un río** (río), **catarata**, **lluvia sobre techo de zinc**, y la **selva de noche sin el logo de Apple** en la laptop.

El código de las fases 3–7 ya está hecho: el bot arma el video, la metadata, la miniatura, pasa el quality gate y sube. Lo que limita el canal es **tu material**. Al 2026-09-20 hay 5 fotos indexadas (ninguna llega a 1920x1080) y ningún clip de video, así que la mitad de las recetas no se pueden producir y las que sí salen con poca variedad.

Para ver el estado en cualquier momento:

```
python main.py visuals
```

Ese comando dice, receta por receta, si ya se puede producir (`READY`), si se puede pero con poca variedad (`THIN`) o si falta material (`NO FOOTAGE`), y al final lista **las etiquetas que desbloquean más recetas**.

## 1. Requisitos técnicos (los que aplica el indexador)

| Qué | Requisito |
|---|---|
| Formato | `.mp4` o `.mov` (video), `.jpg` / `.png` / `.webp` (imágenes fijas, se usan poco) |
| Duración mínima por clip | **30 s** (más corto se descarta) |
| Duración recomendada | **5–20 min por clip**; el bot corta trozos de 45–150 s y **no repite** un trozo dentro del mismo video |
| Resolución | 1080p mínimo; 4K mejor (el render sale a 1920x1080, así que 4K deja margen para el zoom lento) |
| FPS | 24, 30 o 60; el render final va a 30 |
| Cámara | **trípode o superficie fija**. Nada de cámara en mano: el bot aplica un zoom/paneo lentísimo y cualquier temblor se nota multiplicado |
| Exposición | fija (manual). El autoexposure "respirando" arruina un video de 4 h |
| Enfoque | manual y fijo; que la cámara no busque foco a mitad del clip |
| Audio de la cámara | da igual, el bot lo ignora: **todo el audio lo genera él** |

Lo que **no** debe aparecer en cuadro:
- personas reconocibles, placas de carros, marcas, logos, carteles con texto;
- relojes, celulares con pantalla encendida, nada que delate la hora;
- luces que parpadeen (LED, fluorescentes) ni fuegos artificiales;
- cualquier material que no hayas filmado vos.

Consejo: si el clip no tiene texto ni nada asimétrico reconocible (un volcán, por ejemplo), marcalo `allow_mirror: true` y el bot lo puede espejar para duplicar la variedad.

## 2. Qué filmar primero (ordenado por lo que más desbloquea)

Con la lista de recetas activas hoy, este es el orden que rinde más:

| Prioridad | Etiquetas | Qué es | Desbloquea |
|---|---|---|---|
| 1 | `forest`, `jungle` | selva/bosque: hojas moviéndose con viento, luz filtrándose, bambú, helechos | 7 recetas cada una (lluvia suave, río, catarata, tormenta, grillos, pájaros, viento) |
| 2 | `rain`, `window` | lluvia cayendo sobre hojas, charcos, y **lluvia vista desde adentro por una ventana** | 5 y 2 |
| 3 | `night` | selva/jardín de noche (con luz de luna o una luz cálida indirecta) | 5 |
| 4 | `river`, `stream`, `waterfall` | río corriendo, quebrada entre piedras, catarata de lejos y de cerca | 3 |
| 5 | `ocean`, `beach`, `waves`, `sunset` | oleaje, orilla, atardecer en la playa | 1 pero es un nicho grande |
| 6 | `fire`, `fireplace`, `cabin` | fogata, chimenea, interior de cabaña de noche | 1 |
| 7 | `wind`, `mountains`, `clouds`, `storm` | copas de árboles con viento, nubes moviéndose, cielo de tormenta | 3 |
| 8 | `dark`, `abstract` | texturas muy oscuras y lentas (gotas en vidrio desenfocadas, agua negra) para las recetas de ruido | 3 |

**Meta mínima para arrancar:** 3 clips de 10–15 min con etiquetas `rain`, `forest`/`jungle` y `night`. Con eso ya hay varias recetas `READY` y el bot puede producir un video distinto cada día durante más de una semana.

**Meta cómoda:** 2 o más clips y 10+ minutos por ambiente (eso es lo que el comando `visuals` marca como suficiente). En un video se usan hasta 3 clips.

## 3. Cómo entregarlo

1. Copiá los archivos a `C:\ambient_youtube_bot\assets\visuals\` (podés usar subcarpetas). No los edites ni los recortes: el bot solo los lee.
2. Etiquetalos. Dos opciones:
   - **Nombre del archivo**: `rain_window_cabin_01.mp4` → etiquetas `rain`, `window`, `cabin`. Palabras de 3+ letras, en inglés, separadas por `_`.
   - **`assets/visuals/visuals.yaml`** (recomendado): copiá `visuals.example.yaml` y poné etiquetas explícitas y `allow_mirror` por archivo. Ahí también podés excluir descartes con `exclude: true`.
3. Indexalo:

```
python main.py index-visuals
python main.py visuals
```

4. Cuando alguna receta aparezca `READY`:

```
python main.py plan                 # qué se produciría y cuándo se publicaría
python main.py produce              # produce un video completo hasta READY (~55 min para 4 h)
```

En `MODE=test` nada se sube; el video queda en `output/<video_id>/` con su `licenses.json` y su `quality_report.json`.

## 4. Lo que sigue bloqueado por fuera del material

- **Publicación automática**: desbloqueada — Google aprobó la auditoría el 2026-09-20. Para que el scheduler publique solo falta poner `MODE=production` en `.env`.
- **Capas de audio con grabaciones reales** (pájaros, burbujeo del río, fuego con carácter): faltan las credenciales de Freesound. Sin ellas el bot usa solo síntesis procedural, que ya pasa el quality gate pero suena más plano en esas capas. Cuando quieras: crear cuenta en freesound.org → *Settings → API credentials → New credentials* y correr `python main.py freesound-auth`.
