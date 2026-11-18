# AutoClipFactory

A free GitHub Actions starter pipeline that checks a YouTube channel for a finished VOD, downloads it, selects candidate moments using audio energy, creates vertical subtitled clips with faster-whisper + FFmpeg, and uploads them to YouTube Shorts.

## What is automatic
- Runs hourly in GitHub Actions, even when your laptop is off.
- Checks the configured YouTube channel for a finished upload/VOD.
- Avoids reprocessing a VOD after successful processing.
- Creates up to five 20-55 second vertical clips with burned-in captions.
- Uploads clips as public YouTube videos when the YouTube OAuth secret is configured.

## Important limitations
- The current selector is a loud-audio heuristic. It does **not** understand jokes, character identity, or guarantee that an animated character stays in frame.
- The 9:16 crop is centered, not face/character-tracked. Review clips before relying on them for quality.
- GitHub Actions free minutes and job limits apply. Very long streams may exceed the free runner budget.
- This starter currently uploads to YouTube only. Instagram Reels publishing needs an eligible Instagram Professional account, Meta app setup, and additional credentials.
- YouTube API uploads require a valid OAuth refresh token with the `youtube.upload` scope. Never commit credentials or tokens to the repository.

## One-time setup required
1. Open **Settings → Secrets and variables → Actions** in this repository.
2. Add a repository secret named `YOUTUBE_TOKEN_JSON`.
3. Its value must be the complete OAuth authorized-user JSON containing `client_id`, `client_secret`, `refresh_token`, and `token_uri`. Generate it for the YouTube channel you want to publish to, with the `youtube.upload` scope.
4. Check `autoclip/config.json` and correct `channel_url` to the exact source channel URL.
5. Open **Actions → AutoClipFactory → Run workflow** to test once. Scheduled runs are hourly after GitHub enables Actions for the repository.

## Where clips appear
Rendered MP4 and SRT files are created under `output/` during a run. GitHub-hosted runners are temporary, so these files are not retained after the run unless an artifact-upload step is added. Published videos remain on your YouTube channel.

## Safety
Uploads are public by default. Set `privacy_status` to `unlisted` or `private` in `autoclip/config.json` until you have reviewed the output quality.
