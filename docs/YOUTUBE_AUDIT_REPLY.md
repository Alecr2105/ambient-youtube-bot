# Respuesta al equipo de YouTube API Services (pedido del 2026-09-17)

Google respondió al formulario enviado el 2026-09-14 pidiendo, en 7 días hábiles:

> "Kindly provide us with a script / screencast (English Translated Version) demonstrating how the API services are used to upload videos to the YouTube channel."

**Lo manda el dueño del canal** desde <correo-del-dueño>, **respondiendo en el mismo hilo** (así conservan el número de caso). Abajo está el correo listo para copiar y pegar; el guion completo está en `docs/YOUTUBE_AUDIT_SCREENCAST.md`.

## Qué se adjunta / enlaza

| Elemento | Valor |
|---|---|
| Screencast (2:15, inglés, subtítulos quemados) | `docs/screencast/ambient_bot_api_walkthrough.mp4` → subido **sin listar** al canal: `PENDIENTE_LINK` |
| Subida privada hecha durante la grabación | `n6Nbu2OgnmY` (2026-09-17, privada) |
| Subida privada anterior, ya enviada como evidencia | `hE7R9_g7HCE` (2026-09-13, privada) |
| Google Cloud project number | <número-de-proyecto> (`<id-del-proyecto>`) |
| Canal | Costa Rica Ambience — UCLdMsGZaoBAXUPvDcGXPTHQ |

## Correo (en inglés, para copiar y pegar)

> Subject: Re: YouTube API Services — Ambient Bot (project <número-de-proyecto>)
>
> Hello,
>
> Thank you for the review. As requested, here is a script and an English screencast showing how Ambient Bot uses YouTube API Services to upload videos to my channel.
>
> **Screencast (2 minutes, English, unlisted on the channel it uploads to):** PENDIENTE_LINK
>
> The recording shows, in this order: the tool running locally on my own computer; the public page with its privacy policy and terms; the OAuth 2.0 consent flow where I sign in, select the brand account of my own channel (Costa Rica Ambience) and grant `youtube.upload` and `youtube`; `channels.list` confirming which channel the tool is attached to; a real upload through `videos.insert` (video ID `n6Nbu2OgnmY`, created during the recording); the API response with `privacyStatus: private` and `selfDeclaredMadeForKids: false` plus the quota consumed per bucket; the same video in YouTube Studio; the license log and quality report the tool keeps for every video; and the Google Account page where I can remove the tool's access at any time.
>
> **Script**
>
> Ambient Bot is a private command-line tool that I run on my own computer to publish my own videos on my own channel, Costa Rica Ambience. There is no hosted service and no other users, which is why there is no demo account and no public login.
>
> The tool first produces the video: it generates the ambience audio itself, or uses audio whose commercial license it has verified and recorded in a license log, and it builds the picture from footage I filmed in Costa Rica. It then writes the English title, description and tags, renders a thumbnail from frames of that same video, and runs a quality gate (loudness, loop detection, duration, metadata language, licensing, duplicates).
>
> To publish, it authorizes once with OAuth 2.0 using the installed-application flow with a loopback redirect (`app/youtube/auth.py`). I sign in, select the Costa Rica Ambience channel and grant `youtube.upload` and `youtube`. The refresh token is stored in a file on my computer, outside the application's code, and I can revoke access at any time from my Google Account.
>
> It then calls `channels.list` (`mine=true`) to confirm the channel, and `videos.insert` as a resumable upload with the title, description, tags, `categoryId=10`, `snippet.defaultLanguage=en`, `snippet.defaultAudioLanguage=en`, `status.privacyStatus=private`, `status.selfDeclaredMadeForKids=false` and, when scheduling, `status.publishAt` in RFC 3339 UTC. After the upload it calls `thumbnails.set` with the thumbnail generated from the same video, optionally `playlistItems.insert` to add the video to one of my own playlists, and later `videos.list` (`part=status,statistics`) to confirm the video is live and read its public view count. `search.list` is used occasionally for keyword research on my own titles, fewer than 20 calls a day.
>
> Every call is counted per quota bucket against the documented cost, and the tool stops cleanly instead of exceeding the daily limit. With one upload per day it uses roughly 110 of the 10,000 general units, one of the 100 daily uploads and a handful of search calls, so the default quota is sufficient and I am not requesting an increase. The purpose of my request is to have uploads made through the API no longer restricted to private visibility.
>
> The tool does not read, collect or store data about any other channel, video, viewer or comment, and it is not offered to anyone else.
>
> Please let me know if you would like a longer recording, additional screenshots, or a walkthrough of any part of the code.
>
> Best regards,
> Alejandro Corrales — Costa Rica Ambience
> Google Cloud project <número-de-proyecto> (`<id-del-proyecto>`)

## Checklist antes de enviar

- [ ] Revisar el screencast completo (ya se revisaron los cuadros cada 3 s: no aparece nada del escritorio ni de ventanas personales; sí aparece el correo del dueño en la pantalla de consentimiento de Google, que es la cuenta que presentó la solicitud).
- [ ] Subirlo sin listar con `python main.py youtube-upload-file --file docs/screencast/ambient_bot_api_walkthrough.mp4 --title "Ambient Bot - YouTube Data API usage walkthrough" --privacy unlisted --confirm` y pegar el enlace en `PENDIENTE_LINK` (dos lugares).
- [ ] Responder en el mismo hilo del correo, sin cambiar el asunto.
- [ ] Volver a poner YouTube/Studio en español después de grabar.
