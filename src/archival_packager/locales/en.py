"""English wording for the screen.

The keys are the Japanese source strings — see ``archival_packager.i18n`` for why.
A missing key is not an error: the Japanese text is shown instead.

Terminology follows the vocabulary used in the archives and digital preservation
community (OAIS, ISAD(G), PREMIS, Archivematica), not a literal rendering of the
Japanese. 移管 is a *transfer*, 受入 an *accession*, 来歴 *provenance*,
正規化 *normalization*, 原本 the *original*, 保存用 *preservation*,
目録記述 *description*, 完全性 *fixity*, 情報パッケージ an *information package*.
Spelling is US English, to match the PREMIS and Archivematica documentation an
archivist reading this screen is most likely to have beside them.

Note that 正規化 appears in two unrelated senses in this app. Converting a file to
a preservation format is *normalization*; rewriting an unsafe file name is
*sanitizing*. Using one word for both would suggest the file itself was altered.
"""

from __future__ import annotations

TEXTS: dict[str, str] = {
    # ------------------------------------------------------------------
    # ヘッダー・共通
    # ------------------------------------------------------------------
    "表示言語": "Display language",
    "使い方・ライセンス・連絡先": "How to use, licenses, contact",
    "閉じる": "Close",
    "開く": "Open",
    "場所を開く": "Show in folder",
    "フォルダを開く": "Open the folder",
    "実行": "Run",
    "進捗": "Progress",
    # 区切り文字も訳す。日本語の読点・中黒をそのまま英文に置くと読めない。
    "、": ", ",
    "・": ", ",
    "バイト": "bytes",
    "{count} 件": "{count} files",
    "（他 {count} 件）": "({count} more)",
    "不明": "Unknown",
    "（未記入）": "(not recorded)",

    # ------------------------------------------------------------------
    # 何を作るか（モード）
    # ------------------------------------------------------------------
    "何を作るか": "What to create",
    # ラジオのラベルは列の幅で切られる。実測で、この長さが英語での上限。
    # 何をするかは下の 1 行（mode_note）が説明するので、括弧の中は入力の種類だけにする。
    "SIP 作成（素材フォルダ／ZIP から受入パッケージ）":
        "Create a SIP (from a source folder or ZIP)",
    "AIP 作成（SIP から長期保存パッケージ）":
        "Create an AIP (preservation package from a SIP)",
    "素材から AIP まで一気通貫": "From source material through to an AIP",
    "素材フォルダ（または ZIP）から受入パッケージを作ります。原本は変更しません。":
        "Builds a submission package from a folder (or ZIP) of source material. "
        "The originals are left unchanged.",
    "既にある SIP から長期保存パッケージを作ります。記述は SIP から引き継ぎます。":
        "Builds a preservation package from an existing SIP. The description is "
        "carried over from the SIP.",
    "素材から受入パッケージを作り、続けて長期保存パッケージまで作ります。":
        "Builds a submission package from the source material, then goes on to "
        "build the preservation package.",

    # ------------------------------------------------------------------
    # 入力・出力先
    # ------------------------------------------------------------------
    "入力": "Input",
    "出力先": "Destination",
    "未選択": "Not selected",
    "未選択（任意）": "Not selected (optional)",
    "フォルダを選ぶ": "Choose a folder",
    "ZIP を選ぶ": "Choose a ZIP",
    "SIP のフォルダを選ぶ": "Choose the SIP folder",
    "素材フォルダ / SIP を選ぶ": "Choose the source folder or the SIP",
    "受入 ZIP を選ぶ": "Choose the ZIP to accession",
    "出力先フォルダを選ぶ": "Choose the destination folder",
    "前回の accession.csv を選ぶ": "Choose the previous accession.csv",
    "accession.csv を選ぶ": "Choose accession.csv",
    "前回の受入記録（配列前後の突合）":
        "Previous accession record (to match against the arrangement)",
    "素材フォルダ": "Source folder",
    "SIP フォルダ": "SIP folder",
    "タイトル": "Title",
    "あと {items} を指定すると押せます": "Set {items} to enable this button",

    # ------------------------------------------------------------------
    # 記述メタデータ（ISAD(G) の項目名に合わせる）
    # ------------------------------------------------------------------
    "記述メタデータ": "Descriptive metadata",
    "識別子": "Identifier",
    "例: 2026-移管-総務課": "e.g. 2026-transfer-general-affairs",
    "タイトル（必須）": "Title (required)",
    "例: 総務課 一般文書": "e.g. General Affairs Division, general records",
    "内容・範囲": "Scope and content",
    "年代": "Dates",
    "例: 2024–2025": "e.g. 2024–2025",
    "担当者名": "Archivist",
    "PREMIS に保存処理の実施者として記録されます":
        "Recorded in PREMIS as the agent that performed the preservation actions",

    # ------------------------------------------------------------------
    # オプション
    # ------------------------------------------------------------------
    "オプション": "Options",
    "既定のまま": "Defaults",
    "BagIt bag として梱包する": "Wrap the output as a BagIt bag",
    "個人情報(PII)を走査する": "Scan for personally identifiable information (PII)",
    "ウイルス検査を行う（定義 DB が必要）":
        "Run a virus scan (needs the definition database)",
    "ファイル名を安全化する（元名は accession.csv に残ります）":
        "Sanitize file names (the original names are kept in accession.csv)",
    "成果物を ZIP（無圧縮）に固める": "Serialize the output as a ZIP (stored, uncompressed)",
    "保存用フォーマットへ変換する（AIP）": "Normalize to preservation formats (AIP)",

    # ------------------------------------------------------------------
    # ウイルス定義データベース
    # ------------------------------------------------------------------
    "ウイルス定義データベース": "Virus definition database",
    "定義を取得 / 更新": "Download / update definitions",
    "ウイルス定義: ClamAV が同梱されていないため検査できません":
        "Virus definitions: ClamAV is not bundled, so no scan can be run",
    "ウイルス定義を取得しています（数百 MB あります）…":
        "Downloading the virus definitions (several hundred MB)…",
    "ウイルス定義の更新が完了しました。": "The virus definitions have been updated.",

    # ------------------------------------------------------------------
    # 実行結果
    # ------------------------------------------------------------------
    "――― AIP 作成 ―――": "――― Building the AIP ―――",
    "SIP を作成しました（{count} 件 / {size} バイト）":
        "Created the SIP ({count} files / {size} bytes)",
    "AIP を作成しました（原本 {originals} 件 / 派生物 {derivatives} 件）":
        "Created the AIP ({originals} originals / {derivatives} derivatives)",
    "中身を見る": "Look inside",
    "記述スプレッドシート": "Description spreadsheet",
    "レポート": "Report",
    "PII レポート": "PII report",
    "配列前後の対応表": "Arrangement map",
    "目視確認が必要な点: {count} 件": "Points to check by eye: {count}",
    "・{warning}": "• {warning}",
    "目視確認が必要な点はありません。": "There is nothing that needs checking by eye.",

    # ------------------------------------------------------------------
    # エラー
    # ------------------------------------------------------------------
    "処理を完了できませんでした": "The job could not be completed",
    "想定外のエラーが発生しました。": "An unexpected error occurred.",
    "記録: {path}": "Logged to: {path}",
    "内容をコピー": "Copy the details",
    "エラーの内容をコピーしました。報告に貼り付けてください。":
        "The details have been copied. Paste them into your report.",
    "コピーした内容を nakamura@hi.u-tokyo.ac.jp までお送りいただけると助かります。":
        "It would help us if you sent what was copied to nakamura@hi.u-tokyo.ac.jp.",

    # ------------------------------------------------------------------
    # ビューア（生成結果）
    # ------------------------------------------------------------------
    "生成結果: {name}": "Result: {name}",
    "概要": "Overview",
    "処理の記録": "Preservation events",
    "ファイル": "Files",
    "生データ": "Raw data",
    "保存用情報パッケージ（AIP）": "Archival Information Package (AIP)",
    "提出用情報パッケージ（SIP）": "Submission Information Package (SIP)",
    "作成日時": "Created",
    "ファイル数": "Files",
    "原本 {count} 件": "{count} originals",
    "（ほかに {count} 件）": " (plus {count} more)",
    "合計サイズ": "Total size",
    "置き場所": "Location",
    "内訳": "Breakdown",
    "原本": "Originals",
    "その他 {count} 種": "{count} other formats",
    "保存用に変換": "Normalized",
    "ウイルス検査済": "Virus-scanned",
    "未識別": "Unidentified",
    "拡張子が不一致": "Extension mismatch",

    # 処理の記録タブ（PREMIS）
    "処理の記録がありません": "No preservation events were recorded",
    "SIP の段階では、処理の記録はまだありません。この SIP から AIP を作ると、行った処理がここに並びます。":
        "A SIP does not have preservation events yet. Once you build an AIP from "
        "this SIP, what was done appears here.",
    "{count} 件の記録。担当者が別に作業記録を書く必要はありません。":
        "{count} events recorded. There is no need to keep a separate log by hand.",
    "パッケージ全体": "the whole package",
    "日時": "Date and time",
    "処理": "Event",
    "結果": "Outcome",
    "実行したもの": "Agent",
    "対象": "Target",
    "詳細": "Details",

    # ワークフロータブ（PREMIS を段階ごとに束ねた流れ図）
    "ワークフロー": "Workflow",
    "取り込み": "Ingestion",
    "フォーマットの識別": "Format identification",
    "保存用形式への変換": "Normalization",
    "変換結果の検証": "Validation",
    "チェックサムの算出": "Checksum calculation",
    "完全性の確認": "Fixity check",
    "SIP の原本を AIP に取り込んだ記録です。":
        "Records that the originals in the SIP were taken into the AIP.",
    "ClamAV で検査した記録です。検査しなかったときは記録を書きません。":
        "Records of scanning with ClamAV. Nothing is recorded when no scan was run.",
    "Siegfried で PRONOM の形式を特定した記録です。":
        "Records of identifying the PRONOM format with Siegfried.",
    "長期保存に向く形式へ変換した記録です。対象の形式だけが変換されます。":
        "Records of converting files to formats suited to long-term preservation. "
        "Only formats with a conversion rule are converted.",
    "変換で作ったファイルを開き直せたかの確認です。原本の形式適合性の検査（JHOVE など）ではありません。":
        "Checks that each converted file could be opened again. This is not a "
        "format conformance check of the originals (such as JHOVE).",
    "SHA-256 は各ファイルの記録（PREMIS object）に入っています。"
    "算出そのものは処理の記録としては書いていません。":
        "Each file's SHA-256 is stored in its PREMIS object. The calculation itself "
        "is not written as a preservation event.",
    "SIP の BagIt マニフェストとチェックサムを照合した記録です。":
        "Records of comparing checksums against the SIP's BagIt manifest.",
    "SHA-256 が記録されたファイル: {count} 件": "Files with a SHA-256 recorded: {count}",
    "この段階の記録はありません。行わなかったか、記録されていません。":
        "There are no records for this stage. It was either not performed or not "
        "recorded.",
    "記録": "Events",
    "対象ファイル": "Files covered",
    "問題のあったファイル（{count} 件）": "Files with problems ({count})",
    "問題のあったファイルはありません。": "No file had a problem.",
    "要確認 {count} 件": "{count} to check",
    "記録なし": "No records",
    "段階を押すと、使ったツール・件数・問題のあったファイルが出ます。":
        "Select a stage to see the tools used, the counts and any files with problems.",

    # ファイルタブ
    "ファイルの一覧を読めませんでした": "The file list could not be read",
    "{count} 件。中身を見るときは、右端のボタンでファイルの場所を開きます。":
        "{count} files. To look at one, use the button on the right to open its "
        "location.",
    "区分": "Use",
    "相対パス": "Relative path",
    "フォーマット": "Format",
    "サイズ": "Size",
    "ウイルス検査": "Virus scan",
    "警告": "Warning",

    # 生データタブ
    "左のファイルを選ぶと内容を表示します": "Select a file on the left to see its contents",
    "読み取れませんでした: {error}": "Could not be read: {error}",
    "テキストとして表示できない形式です": "This format cannot be shown as text",
    "サイズ: {size}": "Size: {size}",
    "先頭バイト: {head}": "First bytes: {head}",
    "※ 先頭 {size} のみ表示しています。全体はファイルを直接お開きください。":
        "Note: only the first {size} is shown. Open the file itself to read all "
        "of it.",

    # ------------------------------------------------------------------
    # 情報画面
    # ------------------------------------------------------------------
    "情報": "About",
    "使い方": "How to use",
    "このアプリについて": "About this app",
    "ライセンス": "Licenses",
    "1. 何を作るかを選ぶ": "1. Choose what to create",
    "受入パッケージ（SIP）だけを作るか、長期保存パッケージ（AIP）まで作るかを選びます。":
        "Choose whether to create only a submission package (SIP), or to carry on "
        "to a preservation package (AIP).",
    "2. 入力と出力先を選ぶ": "2. Choose the input and the destination",
    "素材のフォルダ（または ZIP）と、成果物を置くフォルダを指定します。原本は読み取るだけで、変更しません。":
        "Give the folder (or ZIP) holding the source material, and the folder the "
        "results should go in. The originals are only read, never modified.",
    "3. 記述メタデータを入れる": "3. Enter the descriptive metadata",
    "タイトルなどを入力します。ここで入れた内容が、受入記録としてパッケージに残ります。":
        "Enter the title and the other fields. What you enter here stays in the "
        "package as the accession record.",
    "4. 実行する": "4. Run it",
    "フォーマットの識別、チェックサムの算出、必要なら検査を行い、情報パッケージを作ります。":
        "Formats are identified, checksums are calculated, scans are run if you "
        "asked for them, and the information package is built.",
    "5. 中身を確かめる": "5. Check what was made",
    "できあがったら「中身を見る」で、生成された構造とファイルの内容をそのまま読めます。":
        "Once it is finished, \"Look inside\" shows the structure that was built "
        "and lets you read the files themselves.",
    "デジタル資料から、国際標準 OAIS の情報パッケージを作成します。":
        "Builds information packages that follow the OAIS international standard "
        "from digital materials.",
    "開発: 中村 覚（東京大学）・金 甫榮（人間文化研究機構）":
        "Developed by Satoru Nakamura (The University of Tokyo) and "
        "Boyoung Kim (National Institutes for the Humanities)",
    "連絡先": "Contact",
    "不具合に出会われたら、エラー画面の「内容をコピー」から貼り付けてお送りください。":
        "If you hit a problem, please use \"Copy the details\" on the error panel "
        "and send us what it copies.",
    "プライバシーポリシー": "Privacy policy",
    "Microsoft ストア": "Microsoft Store",
    "記録の保存先: {path}": "Log file: {path}",
    "本アプリは MIT ライセンスです。同梱している第三者のコンポーネントには、"
    "それぞれ元のライセンスが適用されます。とくに ClamAV は GPL-2.0 であり、"
    "ソースコードの入手方法を下記に示しています。":
        "This application is under the MIT license. The third-party components "
        "bundled with it stay under their own licenses. ClamAV in particular is "
        "under GPL-2.0, and how to obtain its source code is set out below.",
    "（{name} が見つかりませんでした）": "({name} was not found)",
    "（{name} を読めませんでした: {error}）": "({name} could not be read: {error})",
    # ------------------------------------------------------------------
    # core から来る文（進捗・目視確認・ファイル一覧の値）。ui/messages.py
    # 画面に出すときだけ訳す。パッケージに書かれる文は日本語のまま。
    # ------------------------------------------------------------------
    "入力フォルダを走査しています…": "Scanning the input folder…",
    "Archivematica transfer 構造を検出しました（objects/ をそのまま尊重）":
        "Found an Archivematica transfer layout (objects/ is kept as it is)",
    "入力 ZIP を展開しています…": "Extracting the input ZIP…",
    "対象ファイル: {count} 件": "Files to process: {count}",
    "ファイル名を安全化: {count} 件の名前を変更しました（元の名前は accession.csv に残ります）":
        "Sanitized file names: {count} renamed (the original names are kept in accession.csv)",
    "siegfried が同梱されていないため、フォーマット識別をスキップします。":
        "Siegfried is not bundled, so format identification is skipped.",
    "フォーマットを識別しています（siegfried）…": "Identifying formats (Siegfried)…",
    "フォーマット識別に失敗したためスキップします: {detail}":
        "Format identification failed and was skipped: {detail}",
    "画像の技術的特性を読み取っています…（{count} 件）":
        "Reading the technical characteristics of images… ({count})",
    "チェックサム(SHA-256)を計算しています…": "Calculating checksums (SHA-256)…",
    "ClamAV が同梱されていないため、ウイルスチェックをスキップします。":
        "ClamAV is not bundled, so the virus scan is skipped.",
    "ウイルス定義 DB が未取得のため、ウイルスチェックをスキップします。":
        "The virus definitions have not been downloaded, so the virus scan is skipped.",
    "ウイルスチェック中（ClamAV）…": "Scanning for viruses (ClamAV)…",
    "ウイルスチェックに失敗しましたが、SIP 作成は続行します: {detail}":
        "The virus scan failed; building the SIP continues: {detail}",
    "ウイルスは検出されませんでした。": "No viruses were found.",
    "ウイルス検出: {count} 件。report.txt を確認してください。":
        "Viruses found: {count}. Check report.txt.",
    "個人情報(PII)をスキャンしています…": "Scanning for personal information (PII)…",
    "PII 候補は見つかりませんでした。": "No possible personal information was found.",
    "PII 候補: {count} 件（{files} ファイル）。pii-report.csv を確認してください。":
        "Possible personal information: {count} ({files} files). Check pii-report.csv.",
    " ただし {count} ファイルは中身を読めず、走査できていません。":
        " However, {count} files could not be read and were not scanned.",
    "スプレッドシートを生成しています…": "Writing the spreadsheets…",
    "入力の metadata/metadata.csv を継承します":
        "Carrying over metadata/metadata.csv from the input",
    "前回の accession.csv を読めませんでした: {name}。対応表をスキップします。":
        "Could not read the previous accession.csv: {name}. The arrangement map is skipped.",
    "配列前後を突合: {matched}/{total} 行が一致（arrangement-map.csv）":
        "Matched against the previous arrangement: {matched}/{total} rows "
        "(arrangement-map.csv)",
    "BagIt bag を組み立てています…": "Assembling the BagIt bag…",
    "SIP を組み立てています…": "Assembling the SIP…",
    "ZIP（無圧縮）に固めています…": "Packing into a ZIP (uncompressed)…",
    "ZIP を作成しました: {name}": "Created the ZIP: {name}",
    "完了しました。": "Done.",
    "入力を読み取っています（BagIt bag）…": "Reading the input (BagIt bag)…",
    "入力を読み取っています（SIP ディレクトリ）…": "Reading the input (SIP folder)…",
    "原本 {count} 件（ハッシュ継承 {inherited} 件 / 再計算 {recomputed} 件）":
        "{count} originals ({inherited} checksums carried over / {recomputed} recalculated)",
    "完全性を確認しています（マニフェスト照合）…":
        "Checking fixity (against the manifest)…",
    "完全性確認: {count} 件すべて一致": "Fixity check: all {count} match",
    "完全性確認: {count} 件の不一致。要確認": "Fixity check: {count} do not match. Needs checking",
    "完全性確認をスキップ: {reason}": "Fixity check skipped: {reason}",
    "フォーマット変換は行いません（オプション OFF）":
        "No format conversion (the option is off)",
    "正規化の対象はありません（既に保存に適した形式、または未知の形式）":
        "Nothing to normalize (already in a preservation format, or an unknown format)",
    "フォーマット変換: {count} 件": "Format conversions: {count}",
    "METS を生成しています…": "Writing the METS…",
    "AIP（BagIt bag）を組み立てています…": "Assembling the AIP (BagIt bag)…",

    # 目視確認が必要な点（種類: 対象）
    "ウイルス検出": "Virus found",
    "個人情報の候補": "Possible personal information",
    "形式を特定できない": "Format not identified",
    "拡張子と中身が食い違う": "Extension does not match the content",
    "画像を読めません": "Image cannot be read",
    "パスが長すぎます（Windows で開けない可能性）":
        "Path too long (may not open on Windows)",
    "ファイル名が NFC 正規化されていません（Windows/Linux で別名と判定される可能性）":
        "File name is not NFC-normalized (Windows/Linux may treat it as a different name)",
    "変換ツールが無いため原本のまま保存":
        "Kept as the original because the conversion tool is missing",
    "変換に失敗（原本のまま保存）": "Conversion failed (kept as the original)",
    "変換結果を読み戻せないため原本のまま保存":
        "Kept as the original because the converted file could not be read back",
    "完全性確認をスキップ": "Fixity check skipped",
    "変換規則表": "Conversion rules",
    # 種類ごとの、次にすること
    "ウイルス検出: 該当ファイルを開かず、担当の部署に相談してください。":
        "Virus found: do not open the file; consult the staff responsible.",
    "個人情報の候補: 公開の可否を判断するため、pii-report.csv で箇所を確かめてください。":
        "Possible personal information: check the passages in pii-report.csv before "
        "deciding whether the records can be made public.",
    "形式を特定できない: 壊れていないか、ふだんのソフトで開けるかを確かめてください。"
    "開ければ、そのまま保存して差し支えありません。":
        "Format not identified: check that the file opens in the software you normally "
        "use. If it opens, it is fine to keep it as it is.",
    "拡張子と中身が食い違う: 名前の末尾（.pdf など）と中身の形式が違います。"
    "開けるかどうかを確かめてください。":
        "Extension does not match the content: the end of the name (.pdf and so on) "
        "does not match what the file contains. Check that it opens.",
    # ファイル一覧の警告（siegfried の原文の言い換え）
    "形式を特定できませんでした": "Format could not be identified",
    "拡張子と中身が食い違っています": "The extension does not match the content",
    "拡張子だけで判定しました（中身では確かめていません）":
        "Identified by extension only (the content was not checked)",
    "ファイル名だけで判定しました（中身では確かめていません）":
        "Identified by file name only (the content was not checked)",
    # ファイル一覧の値
    "保存用": "Preservation copy",
    "提出書類": "Submission documentation",
    "未実施": "Not scanned",
    "検出なし": "Clean",

    # ------------------------------------------------------------------
    # 入力の取り違え・次の手順
    # ------------------------------------------------------------------
    "入力と出力先の赤字の説明を確かめてください":
        "See the note in red under Input or Destination",
    "出力先が資料のフォルダの中にあります。原本のフォルダに書き込まないよう、別の場所を選んでください。":
        "The destination is inside the folder of records. Choose another place so "
        "that nothing is written into the originals.",
    "出力先が SIP のフォルダの中にあります。別の場所を選んでください。":
        "The destination is inside the SIP folder. Choose another place.",
    "出力先が SIP のフォルダの中にあります。SIP に書き込まないよう、別の場所を選んでください。":
        "The destination is inside the SIP folder. Choose another place so that "
        "nothing is written into the SIP.",
    "選んだフォルダは SIP ではありません。この中の「{name}」が SIP です。そちらを選んでください。":
        "The folder you chose is not a SIP. The SIP is \"{name}\" inside it; choose "
        "that one.",
    "選んだフォルダは SIP ではありません。素材のフォルダから作るときは、"
    "「何を作るか」で「SIP 作成」か「素材から AIP まで一気通貫」を選んでください。":
        "The folder you chose is not a SIP. To start from a folder of records, choose "
        "\"Create a SIP\" or \"From source material through to an AIP\" under "
        "\"What to create\".",
    "選んだフォルダは SIP ではありません（objects フォルダがありません）。":
        "The folder you chose is not a SIP (it has no objects folder).",
    "入力フォルダに対象ファイルがありません。": "There are no files in the input folder.",
    "この SIP から AIP を作る": "Build an AIP from this SIP",
    "中身を確かめてから進んでください。日を改めるときは「AIP 作成」でこのフォルダを選びます。":
        "Look inside before going on. To do it another day, choose \"Create an AIP\" "
        "and pick this folder.",
    "AIP 作成に切り替え、作った SIP を入力にしました。出力先を確かめて「実行」を押してください。":
        "Switched to \"Create an AIP\" with the new SIP as the input. Check the "
        "destination, then press \"Run\".",
}
