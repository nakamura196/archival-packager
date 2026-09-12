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
    "SIP 作成（素材フォルダ／ZIP から受入パッケージ）":
        "Create a SIP (submission package from a source folder or ZIP)",
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
    "原本": "originals",
    "その他 {count} 種": "{count} other formats",
    "保存用に変換": "Normalized",
    "ウイルス検査済": "Virus-scanned",
    "未識別": "Unidentified",
    "拡張子が不一致": "Extension mismatch",

    # 処理の記録タブ（PREMIS）
    "処理の記録がありません": "No preservation events were recorded",
    "AIP には PREMIS の記録が入ります。SIP の段階では作られません。":
        "PREMIS event records are written into the AIP. They are not created at "
        "the SIP stage.",
    "{count} 件の記録。担当者が別に作業記録を書く必要はありません。":
        "{count} events recorded. There is no need to keep a separate log by hand.",
    "パッケージ全体": "the whole package",
    "日時": "Date and time",
    "処理": "Event",
    "結果": "Outcome",
    "実行したもの": "Agent",
    "対象": "Target",
    "詳細": "Details",

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
}
