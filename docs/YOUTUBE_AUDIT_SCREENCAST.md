# Ambient Bot — API usage walkthrough (script for the YouTube API Services audit)

Requested by the YouTube API Services team on 2026-09-17: *"a script / screencast (English Translated Version) demonstrating how the API services are used to upload videos to the YouTube channel."*

- **API client:** Ambient Bot (private command-line tool, no hosted service, no other users)
- **Google Cloud project number:** <número-de-proyecto> (project `<id-del-proyecto>`)
- **Channel:** Costa Rica Ambience — https://www.youtube.com/channel/UCLdMsGZaoBAXUPvDcGXPTHQ
- **Scopes:** `https://www.googleapis.com/auth/youtube.upload`, `https://www.googleapis.com/auth/youtube`
- **Endpoints used:** `videos.insert`, `videos.list`, `videos.update`, `thumbnails.set`, `playlistItems.insert`, `channels.list`, `search.list`

## 1. What the tool does

Ambient Bot runs only on the channel owner's own computer. Once a day it produces a long-form nature ambience video (3–4 hours) for the owner's own channel and uploads it through the YouTube Data API v3:

1. It generates the audio itself (procedural synthesis: rain, wind, water, fire, insects) or uses recordings whose commercial license it has verified and logged.
2. It builds the video from footage the owner filmed in Costa Rica. No stock, no third-party media.
3. It writes the English title, description and tags, and renders a thumbnail from frames of that same video.
4. It runs a quality gate (audio loudness and loop detection, video duration and cuts, metadata language, licensing, duplicates).
5. Only if every check passes does it call the API to upload, set the thumbnail, schedule publication and add the video to a playlist.

Every upload is one video of the owner's own original content to the owner's own channel. The tool does not read, collect or store data about any other channel, video, viewer or comment.

## 2. How the API is used, step by step

| Step | API call | Code | Notes |
|---|---|---|---|
| Authorize once | OAuth 2.0 installed-app flow, loopback redirect | `app/youtube/auth.py` | The owner signs in and picks the Costa Rica Ambience channel. The refresh token is stored in a file outside the repository, on the owner's machine only. |
| Identify the channel | `channels.list` (`mine=true`) | `app/youtube/uploader.py:my_channel` | Confirms the tool is attached to the intended channel; 1 quota unit. |
| Upload | `videos.insert` (resumable, `part=snippet,status`) | `app/youtube/uploader.py:upload_video` | Chunked resumable upload with exponential-backoff retries. |
| Upload body | `status.privacyStatus=private`, `status.selfDeclaredMadeForKids=false`, optional `status.publishAt`, `snippet.defaultLanguage=en`, `snippet.defaultAudioLanguage=en`, `snippet.categoryId=10` | `app/youtube/uploader.py:build_video_body` | Validated before the call: title ≤ 100 characters, tags ≤ 500 characters, `publishAt` must be in the future and RFC 3339 UTC. |
| Thumbnail | `thumbnails.set` | `app/youtube/uploader.py:set_thumbnail` | Image generated from frames of the same video. |
| Playlist | `playlistItems.insert` | `app/scheduler/service.py:upload_ready` | Optional, into the owner's own playlist. |
| Verify | `videos.list` (`part=status,statistics`) | `app/youtube/sync.py:sync_status` | Confirms the video is live and reads its public view count. |
| Keyword research | `search.list` | `app/research/suggest.py` | Occasional, for the owner's own titles; fewer than 20 calls a day. |
| Quota control | — | `app/youtube/quota.py` | Every call is counted per bucket (uploads / search / general) against the documented cost, and the tool aborts cleanly instead of exceeding the daily limit. |

## 3. Screencast scene list

Recorded on the owner's Windows machine. On-screen captions are in English; the terminal output is in English. Total runtime about 4 minutes.

| Scene | On screen | English caption |
|---|---|---|
| 0 | Title card | "Ambient Bot — how the YouTube Data API is used. API client: Ambient Bot. Project number <número-de-proyecto>. Channel: Costa Rica Ambience." |
| 1 | `python main.py doctor` | "The tool runs locally on the channel owner's computer. MODE=test means nothing is published automatically." |
| 2 | The public site: home, privacy policy, terms | "Public privacy policy and terms of service, linked from the OAuth consent screen." |
| 3 | `python main.py youtube-auth`, browser consent | "OAuth 2.0: the owner selects their own channel and grants youtube.upload and youtube. The token is stored locally, outside the code." |
| 4 | Terminal: connected channel | "channels.list confirms the tool is attached to Costa Rica Ambience." |
| 5 | `python main.py youtube-test-upload --confirm` | "videos.insert: a resumable upload of a video created by the tool. Upload progress is shown; the wait is sped up." |
| 6 | Terminal result | "The API returns the video ID. privacyStatus=private, selfDeclaredMadeForKids=false, and the quota used today is recorded per bucket." |
| 7 | YouTube Studio (English UI) | "The same video in YouTube Studio: visibility Private, 'Made for kids: No'." |
| 8 | `licenses.json`, `quality_report.json` | "Every video keeps a license log for all audio used and a quality report; footage is filmed by the channel owner." |
| 9 | Google Account → connected apps | "Access can be revoked at any time from the owner's Google Account, as stated in the privacy policy." |
| 10 | Closing card | "One upload per day of the owner's own content, to the owner's own channel. Default quota is sufficient; no increase is needed." |

## 4. Narration text (also used in the email reply)

Ambient Bot is a private command-line tool that I run on my own computer to publish my own videos on my own channel, Costa Rica Ambience. There is no hosted service and no other users, which is why there is no demo account.

The tool first produces the video: it generates the ambience audio itself or uses audio whose commercial license it has verified and logged, and it builds the picture from footage I filmed in Costa Rica. It then writes the English title, description and tags and renders a thumbnail from frames of that same video, and runs a quality gate.

To publish, it authorizes once with OAuth 2.0. I sign in, select the Costa Rica Ambience channel and grant `youtube.upload` and `youtube`; the refresh token is stored in a file on my computer, outside the application's code, and I can revoke access at any time from my Google Account. The tool then calls `channels.list` to confirm the channel, and `videos.insert` as a resumable upload with the title, description, tags, `categoryId=10`, `defaultLanguage=en`, `privacyStatus=private`, `selfDeclaredMadeForKids=false` and, when scheduling, `publishAt` in RFC 3339 UTC. After the upload it calls `thumbnails.set` for the thumbnail generated from the video, optionally `playlistItems.insert` to add the video to my own playlist, and later `videos.list` to confirm the video is live and read its public view count. `search.list` is used occasionally for keyword research on my own titles, fewer than 20 calls a day.

Every API call is counted per quota bucket against the documented cost, and the tool aborts cleanly rather than exceeding the daily limit. With one upload per day it uses about 110 of the 10,000 general units, one of the 100 daily uploads and a handful of search calls, so the default quota is sufficient and I am not asking for an increase. The purpose of the audit request is to have uploads made through the API no longer restricted to private.
