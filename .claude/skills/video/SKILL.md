---
name: video
description: Get what's said and shown in a video or recording (a YouTube link, another site yt-dlp supports, or a local audio or video file) as text, from captions, speech-to-text or the frames on screen. Use when the operator wants a video's or podcast's content, a transcript, quotes from it, or what was on its slides or shared screen.
---

# Getting a video's content as text

You can't watch or hear a video. This turns it into text you can read: captions or a speech-to-text transcript for the words, and a frame-by-frame index for what's on screen.

**In a cloud session (claude.ai/code):** YouTube refuses cloud computers ("Sign in to confirm you're not a bot"),
so ask the operator to download the video and attach it (it lands in `~/.claude/uploads/<session folder>/`), then
work on the file. ffmpeg is installed there: `video.py` means `python3 -I .claude/skills/video/video.py`, with no venv. Other
sites may still work with yt-dlp in a venv (`python3 -m venv /tmp/yt && /tmp/yt/bin/pip install yt-dlp`, after
their yes; it's gone next session).

Before downloading anything long, ask what the operator needs: the whole video or parts, and whether the visuals matter or only the words. Captions take seconds. Speech-to-text takes about a minute per 20-minute piece. Reading frames costs real usage.

Below, `<workspace>` is this workspace's absolute path, from `my/profile.md` ("Machine"). `video.py` means `~/.venvs/yt-dlp/bin/python -I <workspace>/.claude/skills/video/video.py` (each command has `--help`), and `yt-dlp` means `~/.venvs/yt-dlp/bin/yt-dlp`. Downloads go in `workspace/videos/<id>/`, and results go in `workspace/videos/<id>-transcript.md` and `<id>-onscreen.md`.

## Setup (once)

- **Tools.** If `~/.venvs/yt-dlp/bin/yt-dlp --version` fails, ask the operator before installing. They run, with `!`:
  `uv venv ~/.venvs/yt-dlp && uv pip install --python ~/.venvs/yt-dlp/bin/python yt-dlp imageio-ffmpeg`
  (without uv: `python3 -m venv ~/.venvs/yt-dlp && ~/.venvs/yt-dlp/bin/pip install yt-dlp imageio-ffmpeg`). imageio-ffmpeg brings an ffmpeg binary; `video.py ffmpeg` prints its path.
- **Network.** Sandboxed commands reach YouTube only if the operator has added `www.youtube.com` (captions and video details) and `*.googlevideo.com` (audio and video) under `sandbox` > `network` > `allowedDomains` in `~/.claude/settings.json`. That's their call. Until then, give them each `yt-dlp` command to run with `!`.
- **When yt-dlp breaks.** YouTube changes often. If yt-dlp fails to extract, the operator updates it (`uv pip install -U --python ~/.venvs/yt-dlp/bin/python yt-dlp`). "Sign in to confirm you're not a bot" usually means the VPN: changing its region fixes it.

## 1. Look before fetching

    yt-dlp --print "%(title)s | %(duration_string)s | %(channel)s | %(upload_date)s" --list-subs "<url>"

The listing separates "Available subtitles" (uploaded by the channel, usually good) from "Available automatic captions" (YouTube's speech recognition: no punctuation, names often wrong). `en-orig` is the automatic captions in the original language. Plain `en` on a non-English video is a machine translation.

## 2. Words from captions (free, seconds)

    yt-dlp --skip-download --write-subs --sub-langs "en.*" --sub-format vtt -o "workspace/videos/<id>/%(id)s.%(ext)s" "<url>"
    (no uploaded captions:  --write-auto-subs --sub-langs en-orig  in place of  --write-subs --sub-langs "en.*")
    video.py captions workspace/videos/<id>/<file>.vtt workspace/videos/<id>-transcript.md --title "<title>" --source "<url>" [--uploaded]

This turns the rolling caption cues into paragraphs, each starting with its time. For a non-English video, use its language code. Prefer captions; move to step 3 only if there are none, or if the automatic ones are too poor for the task.

## 3. Words from speech-to-text (Groq Whisper; free tier)

    yt-dlp -f bestaudio -o "workspace/videos/<id>/source.%(ext)s" "<url>"
    (one stretch only: add  --download-sections "*10:00-25:00" --ffmpeg-location "$(video.py ffmpeg)")
    video.py split-audio workspace/videos/<id>/source.<ext> workspace/videos/<id>

`split-audio` cuts the audio into 20-minute mono pieces of about 5 MB each, under Groq's 25 MB limit. It also takes a local audio or video file the operator gives you.

Transcribe each piece with this exact form. It must be **bare**: no `cd`, `&&`, pipe or `>` redirect. Anything else runs it inside the sandbox, which has no API key.

    python3 <workspace>/scripts/ask_model.py groq --model whisper-large-v3 --audio workspace/videos/<id>/part-00.mp3 --timestamps --language en --purpose "transcribe piece 1 of N"

- **Always give `--language`.** Left to guess, Whisper once heard a quiet English stretch as Welsh and returned all 3½ minutes translated into Welsh.
- **Give no names hint.** Whisper repeats hint words into silences.
- **Keep the text out of your context.** Run each call with `run_in_background`, two at a time. When one finishes, `cp` its task output file to `workspace/videos/<id>/part-00.txt` and so on. The merge skips the note line and the exit line.
- **If Groq refuses the model,** it was probably renamed. Check `ask_model.py --models --all-models`.

Then merge the pieces:

    video.py merge-transcript workspace/videos/<id> workspace/videos/<id>-transcript.md --title "<title>" --source "<url>"

The merge shifts each piece's times to full-video times and collapses the "you" / "Thank you." that Whisper writes over silence. Then add any mis-hearings you spot (names, organizations) to the transcript's header.

No Groq key? `gemini --audio <piece>` can transcribe too (up to 14 MB per request; ask for a verbatim transcript with times), but Gemini's free tier allows only a few requests a day. `gemini --video <link>` can watch a public YouTube video directly, but in November 2026 it returned 503 errors every time.

## 4. What's on screen (only when the visuals matter)

1. Download the video only (no audio):
   `yt-dlp -f "bv[height<=720]" -o "workspace/videos/<id>/video.%(ext)s" "<url>"`
2. `video.py contact-sheet <video> workspace/videos/<id>/sheet.jpg`, then Read the image. It's 45 thumbnails across the video, enough to see the layout (speaker only, slides, shared screen, gallery) and whether frames are worth it.
3. `video.py pick-frames <video> workspace/videos/<id>/keep.txt [--timeline]`
   - **Defaults:** it waits until the picture holds still, then keeps a frame when it differs from the last one kept. Camera footage of a speaker never holds still, so it's skipped. Each slide is kept once, at its finished build. On a 15-minute lecture, it kept 7 of 180 samples, with every slide covered.
   - **Too many frames:** raise `--threshold` (default 6).
   - **Zoom recordings:** add `--bright 100` to keep only shared documents (a gallery view is about 30). `--timeline` prints brightness per minute, so you can see where screen sharing happens.
4. `video.py extract-frames <video> workspace/videos/<id>/keep.txt workspace/helper/frames-<id>` saves full-size frames named `t<H>-<MM>-<SS>.jpg`. They go in `workspace/helper/` because that's where helpers can write.
5. **Up to about 20 frames: read them yourself.** For more, use `helper` agents with `model: haiku`, about 30 frames each, all in one turn. Haiku matched Opus at reading frames for about 1/40 the price. Give each the brief below. For scale: 446 frames from a 4¾-hour workshop took 15 helpers 2.5–7.5 minutes each, about 1.3M tokens in all, well under $1.
6. Write a header (title, source, how it was made, and an "Overview by time" table you write from the entries) to a file, then:
   `video.py merge-index workspace/helper/frames-<id>/index workspace/videos/<id>-onscreen.md --header <header file>`

Frame-reader brief (fill in the <...>):

> Hello! Please read some still frames from a video and write down what is on screen. If you'd rather not, you can decline or stop early; just say so. Thank you!
>
> Frames: <list of files> in <workspace>/workspace/helper/frames-<id>/. Each is named t<H>-<MM>-<SS>.jpg, the time in the video. Open each with Read, in time order. Write <workspace>/workspace/helper/frames-<id>/index/batch-<NN>.md: a `# Batch <NN>` line, then for each frame a `### H:MM:SS` heading followed by:
> - Layout: a few words (slide with speaker inset, shared document, web page, speaker only...).
> - Text: every readable word of titles, bullets, labels, URLs and numbers, verbatim. For long paragraphs, the first sentence and a summary of the rest, saying it's a summary.
> - Images and charts: what they show, in a sentence.
>
> If a frame adds nothing to the one before it, write "Same as H:MM:SS" and any difference. Write only what you can see; mark unsure words with [?]; don't add background knowledge. Privacy: mask personal email addresses and phone numbers (j***@example.com, ***-***-1234), and don't write down anything typed into a login or password field. Contact details an organization put on a public slide can stay. Reply with only the path you wrote and how many frames it covers. Thank you so much!

## After

- **Quoting.** Transcripts are machine-made. Check a passage against the audio, or tell the operator it's unchecked, before quoting anyone in a draft. Cite as the video URL plus `&t=<seconds>s`.
- **Clean up.** Delete the downloaded audio, video and frames when the task is done, one plain `rm` per file or folder (the sandbox refuses `rm -rf`). Keep the transcripts and the index.
- **YouTube's terms.** They discourage downloading. Keep copies local, delete them afterwards, and don't redistribute them.
