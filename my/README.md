# my/: your own files

Everything here belongs to you. Updating the workspace (`python3 scripts/update.py`, or downloading a new version) never changes
this folder, so put your own choices here rather than editing `CLAUDE.md` or the ethics files directly.

| File | What it is | Created by |
|---|---|---|
| `profile.md` | Your cause area, who you are, your budget and creativity preferences, anything Claude should always know. `CLAUDE.md` reads it every session. | `setup.py`, or Claude on your first session |
| `ETHICS_SHORT.md` (optional) | Your own ethical framework. If it exists, it replaces the default `ETHICS_SHORT.md` everywhere. | You, or the `/my-ethics` skill |
| `ETHICS_FULL.md` (optional) | The long version, if you want one. Replaces the default `ETHICS_FULL.md`. | You |
| `techwatch.md` | Your limits for tech watch: money, platforms, privacy, what never to propose. | `setup.py` |
| `imports/` | Notes and memory you brought in from other AI tools, waiting for `/import-context` (then moved to `imports/done/`). | `setup.py`, or you |
| `notify.json` | Phone notifications (ntfy): on or off, your private topic name, and how many days apart follow-up briefs come. | `setup.py`, or `scripts/notify.py --setup` |

Claude may edit files here only after you approve it at a permission prompt. Nothing in `my/` is uploaded
anywhere unless you put it in a public repository yourself (it's gitignored).
