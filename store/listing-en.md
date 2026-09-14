# Microsoft ストア 掲載情報（英語）

`listing-ja.md` の英語版。**節の見出しは日本語のまま**にしてある。
`scripts/store_submit.py` が両方の版を同じ書式で読むためで、
送られるのは各節の中身だけ。

**日本語版の翻訳ではない。** 同じことを英語の読者に向けて書き直したもの。
片方を直したら、もう片方も見ること。事実（同梱物の版、検出する種別、
できないこと）がずれると、どちらかが嘘になる。

---

## 製品名

Archival Packager

## 簡単な説明（Short description・最大 1000 文字）

Builds OAIS information packages (SIP and AIP) from digital materials.
Format identification, virus scanning, checksums and preservation metadata,
without a command line. For archives and other institutions taking in and
preserving born-digital and digitised records.

## 説明（Description）

Archival Packager builds the information packages used to keep digital
material readable over the long term.

OAIS (Open Archival Information System) is the international reference model
for digital preservation, but the systems that implement it demand real
technical capacity to install and run, which puts them out of reach of small
institutions. This application does the first step of that work without
requiring specialist knowledge.

What it does

- Identifies formats with Siegfried, recording PRONOM identifiers
- Scans for viruses with ClamAV
- Calculates checksums (SHA-256)
- Records technical metadata in DFXML, including pixel dimensions, colour
  space, bit depth and resolution for images
- Writes a description spreadsheet for AtoM / ISAD(G), a technical inventory
  and an accession record
- Finds candidate personal information: Japanese individual numbers, credit
  card numbers, phone numbers, email addresses and Japanese postal codes.
  Results are masked. Names, addresses and dates of birth are not detected
- Builds a Submission Information Package (SIP), optionally as a BagIt bag
- Builds an Archival Information Package (AIP): a METS document with PREMIS
  events embedded, recording what was done and when

Why you might use it

- Nothing else to install. The tools it uses for identification and scanning
  are bundled
- Originals are never modified. Files are read only, and preservation copies
  are written separately
- The output is in standard formats. It is built to be read by Archivematica
  and AtoM, so an institution can move to those systems once it has the
  capacity for them

Who it is for

Anyone responsible for taking in and preserving digital records at an
archive, a records office, a museum or a corporate archive. It does not
assume you are an information systems specialist.

Please note

- The interface is Japanese and English. The contents of the packages
  (spreadsheet headings, the report, the preservation event records) are
  written in Japanese whatever the interface language is, so that a package
  can be read back regardless of the language it was made in
- The virus definition database is not bundled. Download it from within the
  application
- Converting PostScript / EPS needs Ghostscript, which is not bundled
- Format validation (the equivalent of JHOVE or veraPDF) is not included.
  Files produced by conversion are opened again to confirm they can be read,
  which is not the same as confirming they conform to a specification

Developed by Satoru Nakamura (The University of Tokyo) and Boyoung Kim
(National Institutes for the Humanities).

## 検索キーワード（最大 7 つ）

digital preservation
OAIS
archives
BagIt
PREMIS
METS
digital archive

## カテゴリ

日本語版と同じ（Productivity）。カテゴリは言語ごとには変えられない。

## スクリーンショット

`store/screenshots/en/01-sip.png` （1486 × 973）。

CI の Windows ビルドが撮ったもの（`scripts/screenshot-windows.ps1` の成果物
`archival-packager-windows-screenshots`）。初回起動が OS の言語に従うように
なったので、runner が英語環境である以上、**何もしなくても英語の画面が撮れる。**

## 分かったこと（2026-09-13）

- **掲載情報には言語ごとに 1 枚以上の画像が要る。** 画像なしで送ったら、
  74 MB を送り終えたあとの確定の段階で弾かれた:
  `InvalidParameterValue Validation error: NoScreenshotsOfAnyType`
- `en-us` の掲載情報を足すには、パッケージが `en-US` を宣言している必要がある。
  0.1.7 の MSIX から宣言している
- 説明文・簡単な説明・キーワードは、言語ごとに別々に持てる
