---
layout: guide
lang: en
home_url: /archival-packager/en.html
title: Archival Packager
eyebrow: macOS · Windows
lead: Builds OAIS information packages (SIP / AIP) from your files, without a command line.
alternate: { title: 日本語, url: ./, lang: ja }
nav:
  - { title: Getting started, url: guide/en.html }
  - { title: Using the window, url: manual/en.html }
  - { title: Full manual, url: usage-en.html }
  - { title: GitHub, url: "https://github.com/nakamura196/archival-packager" }
quick_links:
  - { title: Getting started, text: Install and first use, with videos, url: guide/en.html, mark: "▶" }
  - { title: Using the window, text: Every part of the window, with screenshots, url: manual/en.html, mark: "▢" }
footer: "Contact: nakamura@hi.u-tokyo.ac.jp"
---

A desktop application that builds OAIS information packages — a Submission
Information Package (SIP) and an Archival Information Package (AIP) — from
born-digital and digitised files. macOS and Windows.

**New to it?** [Getting started](guide/en.md) walks you through installing and
using the application, with narrated videos.

### Watch it

The video on creating a SIP (submission package) plays first, followed by the one on creating an AIP (preservation package).

<iframe src="https://www.youtube-nocookie.com/embed/2seRY7gT1Qg?playlist=mIwb2glsDyU&rel=0" title="Archival Packager guide (create a SIP, then an AIP)" style="width:100%;aspect-ratio:16/9;border:0" allow="encrypted-media; picture-in-picture; fullscreen" allowfullscreen loading="lazy"></iframe>

*With sound. The same content is also available as text: [Getting started](guide/en.md)*

### Download

| Platform | Where |
| --- | --- |
| Windows | [Microsoft Store](https://apps.microsoft.com/detail/9N6XJD7THHPZ) |
| macOS | [Releases](https://github.com/nakamura196/archival-packager/releases/latest) (signed and notarised `.dmg`) |

Nothing else to install. The tools used for identification and scanning are
bundled with the application.

### What it does

Format identification with Siegfried against the PRONOM registry, virus
scanning with ClamAV, checksums (SHA-256), technical metadata in DFXML, and
preservation events recorded in METS with embedded PREMIS — all without using a
command line.

Originals are never modified. Files are read only, and preservation copies are
written separately.

### Links

- [Getting started](guide/en.md) — installing and first use, with videos
- [Using the window](manual/en.md) — every part of the window, with screenshots
- [How to use it](usage-en.md) — the interface and the command line, in full
- [Source code](https://github.com/nakamura196/archival-packager) (MIT)
- [Privacy policy](privacy-policy.md)

### Credits

Satoru Nakamura (The University of Tokyo) and Boyoung Kim (National Institutes
for the Humanities).
Contact: nakamura@hi.u-tokyo.ac.jp
