---
layout: guide
lang: en
title: Archival Packager
eyebrow: macOS · Windows
lead: "Builds OAIS information packages (SIP / AIP) from your files, without a command line. デジタル資料から、OAIS の情報パッケージ（SIP / AIP）を作るアプリです。"
alternate: { title: 日本語, url: "#日本語", lang: ja }
nav:
  - { title: Getting started, url: guide/en.html }
  - { title: はじめての方へ, url: guide/ }
  - { title: GitHub, url: "https://github.com/nakamura196/archival-packager" }
quick_links:
  - { title: Getting started, text: Install and first use, with videos, url: guide/en.html, mark: "▶" }
  - { title: Using the window, text: Every part of the window, with screenshots, url: manual/en.html, mark: "▢" }
  - { title: はじめての方へ, text: インストールと使い方（動画つき）, url: guide/, mark: "▶" }
  - { title: 画面の使い方, text: 画面の各部を、写真つきで, url: manual/, mark: "▢" }
footer: "Contact / 連絡先: nakamura@hi.u-tokyo.ac.jp"
---

## English

A desktop application that builds OAIS information packages — a Submission
Information Package (SIP) and an Archival Information Package (AIP) — from
born-digital and digitised files. macOS and Windows.

**New to it?** [Getting started](guide/en.md) walks you through installing and
using the application, with narrated videos.

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
- [How to use it](usage.md) — the interface and the command line, in full
- [Source code](https://github.com/nakamura196/archival-packager) (MIT)
- [Privacy policy](privacy-policy.md)

### Credits

Satoru Nakamura (The University of Tokyo) and Boyoung Kim (National Institutes
for the Humanities).
Contact: nakamura@hi.u-tokyo.ac.jp

---

## 日本語

デジタル資料から、OAIS 参照モデルの情報パッケージ（SIP / AIP）を作成する
デスクトップアプリケーションです。macOS と Windows に対応します。

**はじめての方は** [はじめての方へ](guide/) をご覧ください。インストールから
使い方までを、音声つきの動画で説明しています。

### ダウンロード

| | |
| --- | --- |
| Windows | [Microsoft Store](https://apps.microsoft.com/detail/9N6XJD7THHPZ) |
| macOS | [Releases](https://github.com/nakamura196/archival-packager/releases/latest)（署名・公証済みの `.dmg`） |

導入作業は要りません。識別と検査に使う外部ツールは同梱しています。

### できること

フォーマット識別（Siegfried / PRONOM）、ウイルス検査（ClamAV）、チェックサム
（SHA-256）、技術メタデータ（DFXML）、保存処理記録（METS / PREMIS）の生成を、
コマンドライン操作なしで行えます。

原本は変更しません。読み取るだけで、保存用に変換したファイルは別に作ります。

### リンク

- [はじめての方へ](guide/) — インストールと使い方（動画つき）
- [画面の使い方](manual/) — 画面の各部を、写真つきで
- [使い方（詳細版）](usage.md) — 画面とコマンドライン
- [ソースコード](https://github.com/nakamura196/archival-packager)（MIT）
- [プライバシーポリシー](privacy-policy.md)

### 開発

中村 覚（東京大学）・金 甫榮（人間文化研究機構）
連絡先: nakamura@hi.u-tokyo.ac.jp
