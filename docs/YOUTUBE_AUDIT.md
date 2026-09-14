# YouTube API Services — borrador del formulario de auditoría

Formulario: https://support.google.com/youtube/contact/yt_api_form
**Lo envía el dueño del canal desde su cuenta de Google.** Los campos marcados ✍️ son datos personales o legales que completás vos. Todo lo demás está listo para copiar y pegar, en inglés, como pide el formulario.

## Por qué hace falta
Los proyectos de API no verificados solo pueden subir videos **privados**. La cuota por defecto alcanza de sobra (1 subida/día sobre un cupo de 100), así que el objetivo de la auditoría **no es pedir más cuota** sino verificar el cumplimiento y quitar esa restricción. Fuente: https://developers.google.com/youtube/v3/docs/videos/insert

## Datos ya disponibles

| Dato | Valor |
|---|---|
| Google Cloud project ID | `<id-del-proyecto>` |
| Google Cloud project number | `<número-de-proyecto>` |
| Nombre de la app (API client) | Ambient Bot |
| Sitio (HTTPS) | https://alecr2105.github.io/ambient-bot-site/ |
| Privacy Policy | https://alecr2105.github.io/ambient-bot-site/privacy.html |
| Terms of Service | https://alecr2105.github.io/ambient-bot-site/terms.html |
| Canal | Costa Rica Ambience — https://www.youtube.com/channel/UCLdMsGZaoBAXUPvDcGXPTHQ |
| Scopes OAuth | `https://www.googleapis.com/auth/youtube.upload`, `https://www.googleapis.com/auth/youtube` |
| Subida de prueba | `hE7R9_g7HCE` (privado, 2026-09-13) |

## Sección 1 — Tipo de solicitud
- **Request type:** el formulario ofrece "compliance audit for additional quota" o "re-audit to maintain current quota", y ninguna describe exactamente "quitar la restricción de privado". Recomendación: elegir la **auditoría de cumplimiento (additional quota)** y dejar claro en los textos que la cuota por defecto alcanza y que el objetivo es verificar el cumplimiento. Si hubiera una opción más específica en el momento de enviar, usar esa.

## Sección 2 — Contacto
- **Application type:** Individual user.
- ✍️ Full legal name, dirección, ciudad, provincia, código postal.
- **Country:** Costa Rica.
- **Organization's legal name:** ✍️ tu nombre legal (persona física) o "N/A – individual".
- **Primary website:** https://alecr2105.github.io/ambient-bot-site/
- **Category:** Media.
- **Organization size:** Individual / sole proprietor (la opción más pequeña).
- **Primary contact email:** <correo-del-dueño>

## Sección 3 — Modelo de negocio
**Work description** (copiar):
> I run "Costa Rica Ambience", a YouTube channel of long-form nature ambience videos (rain, rainforest, rivers, waterfalls, ocean) for sleep, study and relaxation. I personally film all footage in Costa Rica. Ambient Bot is an internal tool that I alone use to upload my own videos to my own channel, set their titles, descriptions, tags and thumbnails, schedule their publication and add them to my playlists. It does not serve other users, does not access any other channel or viewer data, and stores OAuth tokens only on my own computer.

- **Target audience:** Individual creators (yo mismo).
- **Monetization model:** Other → "The tool is not monetized; the channel may earn revenue through the YouTube Partner Program."
- **Ads sold on or within YouTube content:** No.
- **Prior YouTube approval / Google representative:** None.
- **How did you learn about the API:** YouTube Data API documentation.
- **Content Owner IDs / Google Ads IDs:** dejar vacío (no aplica).

## Sección 4 — API client
- **API client name:** Ambient Bot. El nombre no incluye "YouTube".
- **Primary access URL:** https://alecr2105.github.io/ambient-bot-site/
- **Privacy Policy URL / Terms URL:** los de la tabla de arriba.
- **Publicly accessible:** No. Explicarlo en *Access instructions*:
  > Ambient Bot is a private command-line tool that runs only on the channel owner's computer; there is no hosted app or login for reviewers. The public site above documents the tool, its privacy policy and terms. The attached screenshots show the OAuth consent screen, the upload command and the resulting private video in YouTube Studio. I am happy to provide a screen recording or answer any question.
- **Demo account:** no hay una app web con login. Si el formulario obliga a completarlo, escribir "N/A – command-line tool, see access instructions" y marcar el acknowledgement.

## Sección 5 — Casos de uso y cuota (1 proyecto)
- **Google Cloud project number:** `<número-de-proyecto>`
- **Use case:** Video Uploading (+ Internal Company Tool, si deja elegir varias).
- **OAuth:** Yes.
- **Derived metrics acknowledgement:** marcar. La herramienta no calcula métricas derivadas.
- **Expected API usage volume:** la opción más baja (< 1,000 requests/day).
- **Endpoints:** `videos.insert`, `videos.list`, `videos.update`, `thumbnails.set`, `playlistItems.insert`, `channels.list`, `search.list`.
- **Total quota:** Default (10,000 units/day).
- **search.list quota:** Default (100/day). Justificación: "Occasional keyword research for my own video titles; well under 20 calls/day."
- **videos.insert quota:** Default (100/day). Justificación: "About one upload per day of my own original videos."

## Evidencia (capturas)
Guardarlas en `docs/audit_screenshots/` (ignorada por git) y adjuntar como imagen o PDF:

1. **Privacy Policy**: la página completa, mostrando la sección de YouTube API Services, el enlace a la Google Privacy Policy, la retención y borrado, y la revocación.
2. **Homepage**: https://alecr2105.github.io/ambient-bot-site/ con el enlace a Privacy Policy visible.
3. **Terms of Service**: la página completa.
4. **OAuth flow**: pantalla de consentimiento de Google con "Ambient Bot" y los 2 permisos de YouTube, más la página de revocación https://myaccount.google.com/connections con Ambient Bot listado.
5. **Upload interface**: salida de `python main.py youtube-test-upload --confirm` (terminal), más el video `hE7R9_g7HCE` en YouTube Studio mostrando "Privado".

## Después de enviar
- El equipo de YouTube responde por email, típicamente en semanas. El bot sigue funcionando mientras tanto: sube en privado y programa la metadata.
- Si rechazan: formulario de apelación https://support.google.com/youtube/contact/yt_api_appeals. Mientras tanto, se publica con 1 clic diario en Studio.
- El video de prueba `hE7R9_g7HCE` se puede borrar de Studio **después** de que termine la auditoría, porque quizás lo pidan como evidencia.
