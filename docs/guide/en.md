---
title: Getting started
---

<!--
The beginner's guide (English). The Japanese version is index.md.
Readers are archivists and librarians who handle incoming records and are not
computer specialists. Keep jargon to a minimum and explain it when used.
The videos and screenshots are produced by scripts/docs/record_guide.py. The
text between the narration markers is generated from docs/guide/narration.json;
edit it there, not here.
-->

[日本語](./) · [Back to the top page](../)

# Getting started

Archival Packager takes the files you have received and **packs them into a
form that can be kept for the long term**. The result is called a "package".

This page walks you through installing the application, creating packages and
checking what is inside them. The videos have spoken explanations.
**You never need to type a command.**

- [1. Install](#1-install)
- [2. Create a SIP (submission package)](#2-create-a-sip-submission-package)
- [3. Create an AIP (preservation package) and check it](#3-create-an-aip-preservation-package-and-check-it)
- [If something goes wrong](#if-something-goes-wrong)

## Before you start

**Your original files are never changed.** The application only reads them.
Packages are written to a separate folder that you choose.

There are two kinds of package.

- **SIP (Submission Information Package)** — the records as received, packed
  together with a record of what was received, when, and how many files
- **AIP (Archival Information Package)** — built from a SIP and prepared for
  long-term preservation. For example, images also get a copy in a format
  suited to preservation (TIFF)

Usually you create a SIP first, check it, and then create the AIP.

---

## 1. Install

### Windows

1. Open the [Microsoft Store page](https://apps.microsoft.com/detail/9N6XJD7THHPZ)
2. Press "Get" (or "Install")
3. When "Archival Packager" appears in the Start menu, you are done

Updates arrive automatically, like any other Store app.

### Mac

1. Open the [download page](https://github.com/nakamura196/archival-packager/releases/latest)
2. Under "Assets", click the file whose name ends in **`.dmg`** to download it
3. Open the downloaded file. A window appears; drag the application icon in it
   into the "Applications" folder
4. Open Archival Packager from the "Applications" folder

macOS 12 or later is required.

### Opening it for the first time

**The first start can take a few minutes.** The window may look as if nothing
is happening; the application is preparing itself. From the second time on it
opens straight away.

If your Mac says the application "cannot be opened because the developer
cannot be verified", right-click the icon (or click it while holding the
control key) and choose "Open".

### The first screen

![The window just after starting](media/en/home.png)

To switch the language, use the menu at the **top right** (it shows the
current language). Your choice is remembered next time.

---

## 2. Create a SIP (submission package)

<video controls playsinline preload="metadata" poster="media/en/sip.png" src="media/en/sip.mp4" style="width:100%;border:1px solid #ddd"></video>

*With sound. The red dot shows where the mouse clicks. The text below is what the video says.*

<!-- narration:sip -->
First, let's create a Submission Information Package, or SIP.

Under "What to create", choose "Create a SIP".

Under "Input", press "Choose a folder" and pick the folder of records you received. A folder picker opens; it is skipped in this video.

Next, choose a destination for the package. Use a different place from the records themselves.

The identifier is a name for this transfer. It becomes the name of the package folder.

The title is required. It is the name of the group of records.

Open "Options" to choose extras such as a virus scan.

Here, we turn on the virus scan.

Press "Run". Progress appears on the right.

When it finishes, it says the SIP was created. Any files that need a closer look are listed here.

Press "Look inside" to see what went into the package.

The Files tab shows each file's format and its virus scan result. The original files have not been changed.
<!-- /narration:sip -->

### More details

- If you received the records as a ZIP file, use "Choose a ZIP" under "Input"
- If "Run" cannot be pressed, the line under the button tells you what is
  still missing
- Files that need a closer look are those in which a virus was found, those
  that seem to contain personal information, those whose format could not be
  identified, and those whose extension does not match their content.
  **The application never deletes any of them.** What to do with them is your
  decision

### Options

Nothing is selected at first. The defaults work as they are.

| Option | What it does |
| --- | --- |
| Run a virus scan | Checks the files for viruses. **Needs a one-time download first** (see below) |
| Scan for personally identifiable information (PII) | Looks for things that look like e-mail addresses or phone numbers and lists them. Nothing is removed |
| Sanitize file names | Fixes characters that cannot be used and names that are too long. The original names are kept in the record |
| Wrap the output as a BagIt bag | Uses a layout that libraries and archives commonly use for transfers |
| Serialize the output as a ZIP | Turns the finished package into one ZIP file, handy for handing it over |

**Before using the virus scan**, press **"Download / update definitions"**
under "Virus definition database" once. It downloads the data used to
recognise viruses (several hundred MB). This section appears when the virus
scan is selected. If you run without the data, no scan is done and it is
recorded as "skipped" — never as "nothing found".

---

## 3. Create an AIP (preservation package) and check it

<video controls playsinline preload="metadata" poster="media/en/aip.png" src="media/en/aip.mp4" style="width:100%;border:1px solid #ddd"></video>

*With sound. The text below is what the video says.*

<!-- narration:aip -->
Next, let's turn the SIP into an Archival Information Package, or AIP.

Under "What to create", choose "Create an AIP".

For the input, choose the SIP folder we just created.

Choose a destination. The descriptive metadata comes from the SIP, so there is nothing to type again.

Press "Run". The application checks that no file has changed since it was received, and copies images into a format suited to long-term preservation.

When it finishes, press "Look inside".

The Workflow tab lays out what was done, stage by stage, from left to right. A green tick means no problems were found.

Press a stage to see the tools used and how many files it covered.

In "Normalization", two images were copied into the TIFF format. The original images are kept as well.

"Fixity check" records that each file was compared with the record made when the SIP was created, to confirm nothing had changed.

The Preservation events tab lists the same records one by one. The application writes them automatically, so there is no need to keep a separate log by hand.
<!-- /narration:aip -->

### More details

The "Look inside" screen has five tabs.

| Tab | What it shows |
| --- | --- |
| Overview | Which kinds of file, how many, and when the package was made |
| Preservation events | When, what, with which tool, and with what result — one line per event |
| Workflow | The same records grouped into stages |
| Files | Each file's format, size and virus scan result |
| Raw data | The files inside the package themselves |

Besides the green tick, a stage in the Workflow tab may show:

- **"… to check"** — some files in that stage need a closer look. Press the stage to see which
- **No records** — nothing was recorded for that stage, for example when the virus scan was not selected

You can also **create the SIP and the AIP in one go**: under "What to create",
choose "From source material through to an AIP". If you want to check
something at the SIP stage, though, it is safer to do them separately.

---

## If something goes wrong

- **"Run" cannot be pressed** — the line under the button says what is missing
- **The first start is slow** — only the first time; it takes a few minutes
- **The virus scan says "skipped"** — press "Download / update definitions" once
- **Files inside the package will not open** — the application only lists
  them. Use "Show in folder" and open them with your usual applications

For more detail, see the [full usage guide](../usage.md). What the application
cannot do is listed under [Known limits](https://github.com/nakamura196/archival-packager#known-limits)
in the README.

Questions: nakamura@hi.u-tokyo.ac.jp
