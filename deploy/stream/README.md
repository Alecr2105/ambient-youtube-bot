# Directo 24/7 en Oracle Cloud

El directo corre en una VM **Always Free** de Oracle Cloud, no en la laptop.

## Cómo funciona

- **La laptop:** después de subir cada video, `main.py stream-sync` (también lo corre el ciclo diario) arma un *programa* y lo copia al servidor por SSH. Un programa tiene:
  - `scene.mp4`: loop de 10 s a 1080p30, bitrate constante de 4,5 Mbps y keyframe cada 2 s, que es lo que pide YouTube Live.
  - `audio.m4a`: el audio del video en AAC a 128 kbps.
- **El servidor:** `stream.sh` repite el loop bajo el audio y lo reenvía a YouTube **sin recodificar** (1 % de CPU, ~60 MB de RAM). Reproduce los programas en orden de fecha y vuelve a empezar. El bot deja los últimos `STREAM_KEEP_PROGRAMS` (7).
- **Servicio:** `ambient-stream.service` (systemd) arranca con la VM y se reinicia solo si FFmpeg se cae.

Entre un programa y el siguiente hay un corte de unos segundos. Con una clave de transmisión **persistente**, YouTube mantiene el mismo directo.

## Servidor

| | |
|---|---|
| VM | `ambient-stream`, VM.Standard.E2.1.Micro (Always Free), Ubuntu 24.04, US East (Ashburn) |
| IP pública | `<IP_DE_LA_VM>` (efímera: se mantiene mientras la VM exista) |
| Clave SSH | `C:\Users\<usuario>\.ambient_bot\oracle_stream`, solo en la laptop, nunca en el repo |
| Tráfico | 4,6 Mbps ≈ 1,5 TB/mes de salida; Always Free incluye 10 TB/mes |

Entrar al servidor:

```
ssh -i C:\Users\<usuario>\.ambient_bot\oracle_stream ubuntu@<IP_DE_LA_VM>
```

## Activar el directo (una sola vez)

1. **YouTube Studio → Crear → Emitir en directo.** La primera vez YouTube tarda hasta 24 h en habilitarlo.
2. En **Transmisión**, crear una clave **persistente** (no "de un solo uso") y copiarla. En la configuración del directo:
   - Desactivar "Finalizar automáticamente" (*auto-stop*).
   - Poner título, descripción y miniatura del canal.
3. Guardar la clave **solo en el servidor**. No va en el repo ni en `.env`:

   ```
   ssh -i C:\Users\<usuario>\.ambient_bot\oracle_stream ubuntu@<IP_DE_LA_VM>
   echo 'YOUTUBE_STREAM_KEY=xxxx-xxxx-xxxx-xxxx-xxxx' | sudo tee /etc/ambient-stream.env >/dev/null
   sudo chmod 600 /etc/ambient-stream.env
   sudo systemctl start ambient-stream
   ```

## Operación

| Para | Comando (en el servidor) |
|---|---|
| Ver si transmite | `systemctl status ambient-stream` |
| Ver el log en vivo | `journalctl -u ambient-stream -f` |
| Detener | `sudo systemctl stop ambient-stream` |
| Ver programas | `ls ~/stream/programs` |

Para reinstalar los archivos de esta carpeta, copiá `stream.sh` a `~/stream/` y `ambient-stream.service` a `/etc/systemd/system/`. Después corré `sudo systemctl daemon-reload && sudo systemctl enable --now ambient-stream`.
