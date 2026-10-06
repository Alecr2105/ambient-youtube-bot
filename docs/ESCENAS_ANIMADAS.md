# Escenas animadas con Kling

En vez de una imagen quieta, cada video puede mostrar la ilustración con movimiento sutil: lluvia que cae, vapor del café, una lámpara que titila. Es lo que hace Lofi Girl.

Hacés **un clip de 10 s por escena**, una sola vez. El bot lo repite durante las 3–4 h del video y también en el directo 24/7.

## Paso a paso (por cada escena)

1. Entrá a [klingai.com](https://klingai.com) → **Image to Video**.
2. **Start frame** y **End frame**: subí **la misma ilustración** en los dos. Así el clip termina donde empezó.
3. **Duración:** 10 s. **Modo:** el de mejor calidad que permitan tus créditos.
4. **Prompt de movimiento** (en inglés, siempre con la cámara quieta):

   ```
   Static camera, no camera movement. Subtle ambient motion only: [MOVIMIENTO].
   Keep the character and the room exactly as in the image. No new objects, no text.
   ```

   | Escena | [MOVIMIENTO] |
   |---|---|
   | Cabaña con lluvia | rain streaks running down the window, soft flicker of the desk lamp, steam rising from the cup |
   | Techo de zinc | rain dripping from the roof edge, light mist |
   | Río / catarata | water flowing, gentle mist, leaves swaying slightly |
   | Chimenea | fire flames flickering, warm light pulsing softly |
   | Selva / grillos de noche | fireflies blinking, leaves swaying, faint moonlight shimmer |
   | Balcón frente al mar | waves rolling in the distance, curtains moving in the breeze |
   | Cafetal al amanecer | morning mist drifting, birds crossing far away, leaves swaying |
   | Jardín con lluvia suave | light rain falling, drops on leaves |

   Si el personaje está en la escena, agregá: `the character breathes slowly and turns a page`.

5. Descargá el MP4 y guardalo en `C:\ambient_youtube_bot\assets\visuals\`.
   - Con nombre descriptivo **en inglés** (`rain_window_cabin.mp4` → etiquetas `rain`, `window`, `cabin`), o
   - Con cualquier nombre y etiquetas en inglés en `visuals.yaml`:

   ```yaml
   files:
     cabana_lluvia_kling.mp4:
       tags: [rain, window, storm]
   ```

6. Avisame, o corré:

   ```
   python main.py index-visuals
   python main.py visuals
   ```

   `visuals` debe mostrar la receta con "animated scene(s)".

## Qué hace el bot con el clip

- **Costura:** aunque inicio y fin sean la misma imagen, el movimiento salta al reiniciar. El bot funde los primeros 0,75 s sobre el final, así que al repetirse no se nota el corte.
- **Formato:** lo normaliza a 1920×1080 y 30 fps. La copia queda en `cache/loops/`; el original no se toca.
- **Prioridad:** si una escena tiene clip animado, se usa en vez de la foto. Un video no mezcla fotos quietas con escenas animadas.
- **Bitrate:** 2,5 Mbps (`ANIMATED_VIDEO_BITRATE`) en vez de 0,8 Mbps para foto quieta. Un video de 3,5 h pesa ~4 GB y tarda ~1,5 h en subir con tu conexión.
- **Directo:** usa el mismo clip como escena.

## Cuándo regenerar un clip

- El personaje se deforma, aparecen manos o caras raras, o surge texto.
- La cámara se mueve (zoom o paneo): al repetirse cada 10 s marea.
- El movimiento es brusco. Para estudiar, cuanto más sutil, mejor.

## Requisitos

- **Resolución:** 1080p si Kling lo permite. Un clip de 720p se agranda y se ve más blando; `python main.py visuals` lo avisa.
- **Contenido:** nada de texto, logos ni marcas en la ilustración.
