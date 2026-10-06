# Formato lofi: música generada + lluvia + escena animada

El dueño lo decidió el 2026-10-05. Le gustaron los seis estilos de las muestras y pidió la lluvia debajo.

## Cómo se arma un video

1. **Lluvia:** la receta (`lofi_rain_window`, `lofi_gentle_rain` o `lofi_rain_roof`) renderiza el ambiente de lluvia de Freesound igual que antes. Pasa sus propios controles: loops, clics, calma.
2. **Música:** el bot arranca ComfyUI y genera **temas nuevos para ese video** con ACE-Step.
   - 3,5 h son ~72 temas de 3 min, unos 90 s de GPU cada uno: **~1,8 h**.
   - Los estilos rotan sin repetir el mismo dos veces seguidas: guitarra acústica, piano rhodes, guitarra de nylon, jazz suave, bossa lofi y piano nocturno.
3. **Mezcla:**
   - Cada tema se nivela a −18 LUFS y se une al siguiente con un crossfade de 4 s.
   - La lluvia va 15 LU por debajo.
   - El limitador solo frena picos.
4. **Control de calidad del mix:**
   - Duración, volumen, pico, silencio y clics en los crossfades.
   - "Calma" con margen de 8 LU, porque los beats suben un poco.
   - Sin detector de loops: un tema lofi repite compases a propósito.
5. **Video:** la escena animada (loop de Wan 2.2) y la metadata lofi.
   - La descripción dice que la música la compone el canal con un modelo de IA, sobre lluvia real.
6. **Licencias:**
   - `licenses.json` lista cada tema con modelo, licencia, semilla y prompt.
   - `music.json` guarda el detalle.

## Herramientas (gratis, en la laptop)

| Qué | Dónde | Licencia |
|---|---|---|
| ComfyUI portable v0.38.0 | `C:\ai_tools\ComfyUI_windows_portable` | GPL-3.0 (herramienta) |
| ACE-Step v1 3.5B (música) | `ComfyUI\models\checkpoints` | Apache-2.0, uso comercial |
| Wan 2.2 TI2V-5B (animación) | `ComfyUI\models\diffusion_models` | Apache-2.0, uso comercial |

- **Servidor:** el bot arranca ComfyUI solo cuando lo necesita y lo cierra al terminar la música, para liberar RAM para el render. Si ya hay un ComfyUI abierto, lo usa y no lo cierra.
  - El bot lo abre en modo normal, porque ACE-Step entra en los 8 GB de la GPU: ~90 s por tema.
  - Un ComfyUI abierto a mano con `--lowvram` (el que necesita Wan) hace la música ~50 % más lenta.
- **Animación:** las escenas se animan una sola vez con `C:\ai_tools\scripts\animate_sample.py`.
  - Formato: 960×544, dos tramos de 5 s encadenados = 10 s, ~25 min por escena.
  - El clip va a `assets/visuals/` con `loop: true`.

## Activarlo

En `.env`:

```
MUSIC_MODE=true
ANIMATED_VIDEO_CODEC=h265
```

Después reiniciá el worker:

```
Stop-ScheduledTask -TaskName "AmbientBot Worker"; Start-ScheduledTask -TaskName "AmbientBot Worker"
```

Con `MUSIC_MODE=false` el bot vuelve a las recetas de solo ambiente.

## Variedad

Las tres recetas lofi buscan escenas con etiqueta `rain`. Hoy hay **una sola escena con lluvia** (la cabaña), así que todos los videos la usan, igual que Lofi Girl usa siempre a su personaje.

Para que haya variedad:
1. Generar versiones **con lluvia** de las otras ilustraciones (ChatGPT: "same scene, but raining, no text").
2. Animarlas con Wan.
3. Etiquetarlas en `visuals.yaml`.
