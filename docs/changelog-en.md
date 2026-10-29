---
lang: en
home_url: /en.html
image: /assets/ogp/ogp-en.png
title: What's new
description: What changed in each version of Archival Packager.
eyebrow: Archival Packager
alternate: { title: 日本語, url: changelog.html, lang: ja }
footer: "Contact: nakamura.satoru@mail.u-tokyo.ac.jp"
nav:
  - { title: Home, url: en.html }
  - { title: Getting started, url: guide/en.html }
  - { title: Using the window, url: manual/en.html }
  - { title: Full manual, url: usage-en.html }
  - { title: GitHub, url: "https://github.com/nakamura196/archival-packager" }
---

<!--
Add each new version at the top. Keep this in step with changelog.md (Japanese).
Readers use the application: say what changes on screen and in the packages, not in the code.
-->

Get the latest version from the Microsoft Store (Windows) or from the [GitHub releases](https://github.com/nakamura196/archival-packager/releases/latest) (Mac).
See [Getting started](guide/en.html) for how to install it.

## 0.1.10 (2026-10-29)

### Fixes

- **Long folder names in the "Package contents" tree no longer run over the pane next to it.**
  A name that does not fit is cut short with "…". Hover over it to see the full name.

## 0.1.9 (2026-10-28)

### Highlights

- **TIFF and PDF files are now checked with JHOVE.**
  JHOVE checks that a file is well formed according to its format. It is bundled with the application, together with Java, which makes the application about 50 MB larger.
  - If a preservation copy made by conversion fails the check, that copy is not used and the original is preserved instead.
  - If an original fails the check, it is still kept. A warning is shown and the result is recorded.
  - If JHOVE could not run, this is recorded as "not checked" with a warning, never as "no problems".
- **What happens when a SIP is created is now carried into the AIP.**
  The virus scan, format identification and other steps are recorded as PREMIS events (a standard format for processing records) when the SIP is created.
  When you build the AIP, those events are carried over with their original dates, and you can see them in the AIP viewer.
- **The SIP now has its own METS document** (a standard inventory of the package), `sip-mets.xml`, which is carried into the AIP.
  The AIP's METS structure follows the same layout as Archivematica.
- **The Japanese screen now uses plainer words**, for people who are not preservation specialists.

### On screen

- In "Description of the materials", the required title now comes first and is marked in red.
- The viewer tabs are now called "Items" and "Package contents".
  "Items" shows each original on the same row as its preservation copy.
- "Package contents" shows CSV files as tables and images inline.
- After "Build an AIP from this SIP", the window points you to the left pane.
- Each option now has a short explanation.
- On the English screen, the virus-definition status and the details of the processing records were still in Japanese. They are now translated.

### In the packages

- The processing records now include the versions of ClamAV (virus scan) and Siegfried (format identification).
- `accession.csv` now also shows the path after a file was renamed.
- Item identifiers in `atom-import.csv` are now zero-padded serial numbers (`0001` and so on).

### Requirements and other changes

- The Mac version runs on macOS 12 or later.
- The bundled components (Pillow, pypdf and others) were updated to versions with known problems fixed.
  The build now uses fixed component versions, so every build contains the same ones.
- This site moved to [https://ap.ldas.jp](https://ap.ldas.jp).
  The links in the application's About section point there too.
- The contact address is now nakamura.satoru@mail.u-tokyo.ac.jp.

## 0.1.8 (2026-09-23)

- Added a "Workflow" tab that shows the processing as a diagram, stage by stage.
- Fixed a bug where choosing a destination inside the source folder wrote the SIP into the original folder (this is now refused and shown in red).
- After a SIP is created, a "Build an AIP from this SIP" button appears.
- Choosing a folder that is not a SIP as the AIP input is now shown in red.
- On the English screen, progress, warnings and categories were still in Japanese. They are now translated.
- Published [Getting started](guide/en.html), with videos.

For earlier changes, see the [history on GitHub](https://github.com/nakamura196/archival-packager/commits/main).
