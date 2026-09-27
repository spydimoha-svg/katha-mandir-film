# katha-mandir-film

A Claude Code skill that turns a numbered Katha Mandir shooting script plus a
folder of Veo/Flow clips into a finished film, vertical reels, caption files,
thumbnails and a publish sheet.

`SKILL.md` is what Claude reads. This file is for people.

## Needs

- Windows 10 or 11. Tamil text is rendered through PowerShell.
- Claude Code: terminal, desktop app, or the VS Code extension. The claude.ai
  website and phone app cannot run it.
- [Git](https://git-scm.com/download/win)
- [Python 3](https://www.python.org/downloads/), with "Add python.exe to PATH"
  ticked during install
- No ffmpeg install needed. It comes with the `imageio-ffmpeg` package.

## Install

In PowerShell:

```powershell
git clone https://github.com/spydimoha-svg/katha-mandir-film "$env:USERPROFILE\.claude\skills\katha-mandir-film"
python -m pip install -r "$env:USERPROFILE\.claude\skills\katha-mandir-film\requirements.txt"
```

The first clone opens a browser to sign in to GitHub. Then restart Claude
Code and type `/katha-mandir-film` to confirm it is listed.

## Update

```powershell
git -C "$env:USERPROFILE\.claude\skills\katha-mandir-film" pull
```

Restart Claude Code after pulling. Do not edit the files here yourself: local
changes will conflict with the next pull. Send changes to the repo owner.

## Use

Open Claude Code in a project folder that holds the script (`.docx`) and the
clips, and ask for the film, for example "combine these clips in script
order". It stops twice for your sign off: after mapping clips to scenes, and
after quality checks.

## Watermark

The round watermark is made from `assets/logo.jpeg`, the Katha Mandir logo.
For another channel, replace that file with your own logo under the same name.
