#!/usr/bin/env python3
"""Turn a video or recording into text: tidy captions, cut audio for speech-to-text, join the pieces, and pick and
extract the frames worth reading. The /video skill's SKILL.md says when to use each step.

Run it with the video tools' Python, so it can find the ffmpeg that comes with imageio-ffmpeg:

    ~/.venvs/yt-dlp/bin/python -I <workspace>/.claude/skills/video/video.py <command> --help

Commands:
    captions          a .vtt caption file -> a readable transcript, one timestamped paragraph about every 30 s
    split-audio       audio or video -> 20-minute mono mp3 pieces small enough for speech-to-text
    merge-transcript  the pieces' speech-to-text output -> one transcript with full-video times
    pick-frames       a video -> the times worth looking at (skips near-duplicates; optionally dark frames)
    extract-frames    those times -> full-size .jpg frames named t<H>-<MM>-<SS>.jpg
    merge-index       the frame readers' batch files -> one on-screen index
    ffmpeg            print the path of the ffmpeg this script uses
"""
import argparse
import glob
import html
import os
import re
import shutil
import subprocess
import sys

# Whisper writes these over silence; a run of them is collapsed into one line.
FILLER = {"you", "you.", "thank you.", "thank you", "thank you so much.", "thanks.", "bye.", "."}
SILENCE = '(silence or inaudible; the model wrote filler like "you" / "Thank you.")'
STT_LINE = re.compile(r'^\[(?:(\d+):)?(\d+):(\d\d)-(?:(\d+):)?(\d+):(\d\d)\] ?(.*)$')
# Lines that aren't transcript: ask_model.py's note, and the background task's exit line.
NOT_TEXT = re.compile(r'^\[(\w+ \S+: speech-to-text of .*|exited with code \d+)\]$')


def clock(s):
    s = int(s)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        raise SystemExit("No ffmpeg found. Run this with ~/.venvs/yt-dlp/bin/python (which has imageio-ffmpeg), "
                         "or install ffmpeg; see the skill's setup section.")


def run_ffmpeg(args, **kw):
    return subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", *args], check=True, **kw)


# ---------------------------------------------------------------- captions

def vtt_cues(path):
    """(start seconds, [text lines]) per cue, with inline timing and styling tags removed."""
    cues, start, lines = [], None, []
    timing = re.compile(r'^(\d+:)?(\d\d):(\d\d)\.\d+ --> ')
    for raw in open(path, encoding="utf-8"):
        raw = raw.rstrip("\n")
        m = timing.match(raw)
        if m:
            if start is not None:
                cues.append((start, lines))
            start = int((m[1] or "0:")[:-1]) * 3600 + int(m[2]) * 60 + int(m[3])
            lines = []
        elif start is not None and raw.strip():
            text = html.unescape(re.sub(r'<[^>]+>', '', raw)).strip()
            if text:
                lines.append(text)
    if start is not None:
        cues.append((start, lines))
    return cues


def cmd_captions(a):
    """YouTube's automatic captions roll: each cue repeats the line before it. Keep each line once."""
    words, recent = [], []
    for start, lines in vtt_cues(a.vtt):
        for line in lines:
            if line in recent:
                continue
            recent = (recent + [line])[-3:]
            words.append((start, line))
    if not words:
        raise SystemExit(f"no caption text in {a.vtt}")
    paras, cur, cur_start = [], [], None
    for start, line in words:
        # A new paragraph after `every` seconds, at a sentence end if there is one soon.
        if cur and (start - cur_start >= a.every * 2 or
                    start - cur_start >= a.every and cur[-1].endswith((".", "?", "!", "]"))):
            paras.append(f"[{clock(cur_start)}] " + " ".join(cur))
            cur = []
        if not cur:
            cur_start = start
        cur.append(line)
    paras.append(f"[{clock(cur_start)}] " + " ".join(cur))
    kind = "uploaded captions" if a.uploaded else "YouTube's automatic captions"
    head = f"""# Transcript: "{a.title}"

Source: {a.source}
From {kind}, tidied by the /video skill. Times are where each paragraph starts.
{"" if a.uploaded else "Automatic captions have no punctuation to speak of and often mishear names. Check the audio before quoting anyone."}
"""
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(head + "\n" + "\n\n".join(paras) + "\n")
    print(f"{len(words)} caption lines -> {len(paras)} paragraphs -> {a.out}")


# ---------------------------------------------------------------- audio

def cmd_split_audio(a):
    os.makedirs(a.out_dir, exist_ok=True)
    run_ffmpeg(["-i", a.source, "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k", "-f", "segment",
                "-segment_time", str(a.minutes * 60), "-reset_timestamps", "1",
                os.path.join(a.out_dir, "part-%02d.mp3")])
    parts = sorted(glob.glob(os.path.join(a.out_dir, "part-*.mp3")))
    mb = max(os.path.getsize(p) for p in parts) / 1e6
    print(f"{len(parts)} pieces of up to {a.minutes} min (largest {mb:.1f} MB) in {a.out_dir}")


def cmd_merge_transcript(a):
    files = sorted(glob.glob(os.path.join(a.pieces_dir, "part-*.txt")))
    if not files:
        raise SystemExit(f"no part-*.txt files in {a.pieces_dir}")
    lines, speech, filler = [], 0, 0
    for n, path in enumerate(files):
        off = n * a.piece_seconds
        if n:
            lines.append(f"\n--- piece {n + 1} starts at {clock(off)} (a word or two may be cut at this join) ---\n")
        silent, started = None, False
        for raw in open(path, encoding="utf-8"):
            raw = raw.rstrip("\n")
            if not raw.strip() or NOT_TEXT.match(raw.strip()):
                continue
            m = STT_LINE.match(raw)
            if not m:  # a wrapped continuation line (anything before the first timestamp is a header)
                if started and not lines[-1].startswith("\n"):
                    lines[-1] += " " + raw.strip()
                continue
            started = True
            start = off + int(m[1] or 0) * 3600 + int(m[2]) * 60 + int(m[3])
            end = off + int(m[4] or 0) * 3600 + int(m[5]) * 60 + int(m[6])
            text = m[7].strip()
            if text.lower() in FILLER:
                filler += 1
                if silent is not None:
                    lines[-1] = f"[{clock(silent)}-{clock(end)}] {SILENCE}"
                else:
                    silent = start
                    lines.append(f"[{clock(start)}-{clock(end)}] {SILENCE}")
                continue
            silent = None
            speech += 1
            lines.append(f"[{clock(start)}-{clock(end)}] {text}")
    head = f"""# Transcript: "{a.title}"

Source: {a.source}
Machine transcript: {a.model}, run on {len(files)} pieces of {a.piece_seconds // 60} minutes. The times below are times in the full recording. Nobody has checked it against the audio.

How to read it:
- Pieces were cut without overlap, so a word or two may be missing or garbled at each `--- piece N starts ---` line.
- When nobody is speaking, Whisper tends to write "you" or "Thank you.". Those lines are collapsed into "(silence or inaudible ...)". Short real "thank you"s inside speech are collapsed too.
- Names of people and organizations are often misspelled. Check the audio before quoting anyone.
"""
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(head + "\n" + "\n".join(lines) + "\n")
    print(f"{len(files)} pieces, {speech} speech lines, {filler} filler lines collapsed -> {a.out}")


# ---------------------------------------------------------------- frames

def cmd_pick_frames(a):
    w, h = 96, 60
    raw = run_ffmpeg(["-i", a.video, "-an", "-vf", f"fps=1/{a.every},scale={w}:{h},format=gray",
                      "-f", "rawvideo", "-"], stdout=subprocess.PIPE).stdout
    size = w * h
    n = len(raw) // size

    def diff(f, g):
        return sum(abs(x - y) for x, y in zip(f, g)) / size

    kept, last, prev, bright, light, moving = [], None, None, [], 0, 0
    for i in range(n):
        frame = raw[i * size:(i + 1) * size]
        mean = sum(frame) / size
        bright.append(mean)
        settled = prev is not None and diff(frame, prev) < a.still
        prev = frame
        if mean < a.bright:
            last = None  # a dark stretch ends the run, so the next bright frame is always kept
            continue
        light += 1
        if a.still and not settled:  # camera footage or a transition: wait until the picture holds still
            moving += 1
            continue
        if last is None or diff(frame, last) > a.threshold:
            kept.append(i * a.every)
            last = frame
    with open(a.out, "w") as f:
        f.write("\n".join(str(s) for s in kept) + "\n")
    if a.timeline:
        per = max(1, 60 // a.every)
        print("Mean brightness per minute (0-255; a Zoom gallery is about 30, a shared document over 100):")
        for m in range(0, n, per):
            chunk = bright[m:m + per]
            print(f"  {clock(m * a.every)}  {sum(chunk) / len(chunk):5.0f}  {'#' * int(sum(chunk) / len(chunk) / 8)}")
    print(f"kept {len(kept)} of {n} sampled frames (one every {a.every} s); skipped {n - light} too dark and "
          f"{moving} still moving -> {a.out}")


def duration(path):
    err = subprocess.run([ffmpeg(), "-hide_banner", "-i", path], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d\d):(\d\d)", err)
    if not m:
        raise SystemExit(f"can't read the length of {path}")
    return int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])


def cmd_contact_sheet(a):
    secs = duration(a.video)
    step = max(1, secs // (a.cols * a.rows))
    run_ffmpeg(["-y", "-i", a.video, "-an", "-vf", f"fps=1/{step},scale=240:-1,tile={a.cols}x{a.rows}",
                "-frames:v", "1", a.out])
    print(f"{a.cols}x{a.rows} thumbnails, one every {step} s (left to right, top to bottom) of {clock(secs)} -> {a.out}")


def cmd_extract_frames(a):
    os.makedirs(a.out_dir, exist_ok=True)
    times = [int(t) for t in open(a.times).read().split()]
    for s in times:
        name = os.path.join(a.out_dir, f"t{s // 3600}-{s % 3600 // 60:02d}-{s % 60:02d}.jpg")
        run_ffmpeg(["-y", "-ss", str(s), "-i", a.video, "-frames:v", "1", "-q:v", "3", name])
    print(f"{len(times)} frames -> {a.out_dir}")


def cmd_merge_index(a):
    files = sorted(glob.glob(os.path.join(a.batch_dir, "batch-*.md")))
    if not files:
        raise SystemExit(f"no batch-*.md files in {a.batch_dir}")
    parts = [re.sub(r"^# .*\n", "", open(p, encoding="utf-8").read(), count=1).strip() for p in files]
    body = "\n\n".join(parts)
    entries = len(re.findall(r"^### ", body, re.M))
    if a.header:
        header = open(a.header, encoding="utf-8").read().rstrip() + "\n"
    else:
        header = "# On-screen index\n\nMachine readings of video frames, not checked frame by frame.\n"
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(header + "\n## All entries\n\n" + body + "\n")
    print(f"{len(files)} batch files, {entries} entries -> {a.out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("captions", help="tidy a .vtt caption file into a transcript")
    p.add_argument("vtt")
    p.add_argument("out")
    p.add_argument("--title", required=True)
    p.add_argument("--source", required=True, help="the video's URL")
    p.add_argument("--uploaded", action="store_true", help="the captions were uploaded by the channel, not automatic")
    p.add_argument("--every", type=int, default=30, help="start a new paragraph after about this many seconds")
    p.set_defaults(fn=cmd_captions)

    p = sub.add_parser("split-audio", help="cut audio into mono mp3 pieces for speech-to-text")
    p.add_argument("source", help="an audio or video file")
    p.add_argument("out_dir")
    p.add_argument("--minutes", type=int, default=20, help="piece length (default 20, about 4.8 MB each)")
    p.set_defaults(fn=cmd_split_audio)

    p = sub.add_parser("merge-transcript", help="join part-NN.txt speech-to-text pieces")
    p.add_argument("pieces_dir")
    p.add_argument("out")
    p.add_argument("--title", required=True)
    p.add_argument("--source", required=True, help="the recording's URL or file name")
    p.add_argument("--piece-seconds", type=int, default=1200, help="length of each piece (default 1200 = 20 min)")
    p.add_argument("--model", default="Groq whisper-large-v3")
    p.set_defaults(fn=cmd_merge_transcript)

    p = sub.add_parser("pick-frames", help="choose which times to look at")
    p.add_argument("video")
    p.add_argument("out", help="a text file: one time in seconds per line")
    p.add_argument("--every", type=int, default=5, help="seconds between samples (default 5)")
    p.add_argument("--threshold", type=float, default=6.0,
                   help="mean pixel difference (0-255) that counts as a new screen (default 6). Raise it for fewer "
                        "frames")
    p.add_argument("--bright", type=float, default=0.0,
                   help="skip frames darker than this. 100 keeps only shared documents in a Zoom recording "
                        "(default 0: keep all)")
    p.add_argument("--still", type=float, default=3.0,
                   help="keep a frame only once the picture has settled: it differs from the sample before by less "
                        "than this (default 3). Camera footage of a speaker never settles; slides and shared "
                        "screens do. 0 turns this off")
    p.add_argument("--timeline", action="store_true", help="also print mean brightness per minute")
    p.set_defaults(fn=cmd_pick_frames)

    p = sub.add_parser("contact-sheet", help="one image of evenly spaced thumbnails, to see the video's layout")
    p.add_argument("video")
    p.add_argument("out", help="a .jpg path")
    p.add_argument("--cols", type=int, default=9)
    p.add_argument("--rows", type=int, default=5)
    p.set_defaults(fn=cmd_contact_sheet)

    p = sub.add_parser("extract-frames", help="save full-size frames at the picked times")
    p.add_argument("video")
    p.add_argument("times", help="the file pick-frames wrote")
    p.add_argument("out_dir")
    p.set_defaults(fn=cmd_extract_frames)

    p = sub.add_parser("merge-index", help="join the frame readers' batch-NN.md files")
    p.add_argument("batch_dir")
    p.add_argument("out")
    p.add_argument("--header", help="a Markdown file to put above the entries")
    p.set_defaults(fn=cmd_merge_index)

    p = sub.add_parser("ffmpeg", help="print the ffmpeg path")
    p.set_defaults(fn=lambda a: print(ffmpeg()))

    a = ap.parse_args()
    try:
        a.fn(a)
    except subprocess.CalledProcessError as e:
        sys.exit(f"ffmpeg failed (exit {e.returncode}); check the input file")


if __name__ == "__main__":
    main()
