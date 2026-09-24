---
layout: guide
lang: ja
title: Archival Packager
eyebrow: macOS · Windows
lead: デジタル資料から、OAIS の情報パッケージ（SIP / AIP）を作るアプリです。コマンドを打つ必要はありません。
alternate: { title: English, url: en.html, lang: en }
nav:
  - { title: はじめての方へ, url: guide/ }
  - { title: 画面の使い方, url: manual/ }
  - { title: 詳しい使い方, url: usage.html }
  - { title: GitHub, url: "https://github.com/nakamura196/archival-packager" }
quick_links:
  - { title: はじめての方へ, text: インストールと使い方（動画つき）, url: guide/, mark: "▶" }
  - { title: 画面の使い方, text: 画面の各部を、写真つきで, url: manual/, mark: "▢" }
footer: "連絡先: nakamura@hi.u-tokyo.ac.jp"
---

デジタル資料から、OAIS 参照モデルの情報パッケージ（SIP / AIP）を作成する
デスクトップアプリケーションです。macOS と Windows に対応します。

**はじめての方は** [はじめての方へ](guide/) をご覧ください。インストールから
使い方までを、音声つきの動画で説明しています。

### 動画で見る

受入パッケージ（SIP）を作る動画のあと、長期保存パッケージ（AIP）を作る動画が続けて流れます。

<iframe src="https://www.youtube-nocookie.com/embed/fgraxOi60EQ?playlist=fgraxOi60EQ,_cHN7XBQzbo&rel=0" title="Archival Packager の使い方（SIP を作る → AIP を作る）" style="width:100%;aspect-ratio:16/9;border:0" allow="encrypted-media; picture-in-picture; fullscreen" allowfullscreen loading="lazy"></iframe>

*音声が出ます。同じ内容を文章でも読めます: [はじめての方へ](guide/)*

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
