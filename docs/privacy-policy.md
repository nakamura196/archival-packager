# プライバシーポリシー / Privacy Policy

**Archival Packager**

最終更新: 2026年9月8日 / Last updated: 8 September 2026

---

## 日本語

### 結論

**このアプリは、利用者の個人情報を収集しません。外部に送信もしません。**

すべての処理は利用者の端末の中で完結します。開発者は、利用者が何を処理したか、
どのようなファイルを扱ったかを知る手段を持ちません。

### 扱う情報

**利用者が選んだファイル。** アプリは、利用者が指定したフォルダやファイルを読み取り、
フォーマットの識別、チェックサムの算出、技術メタデータの抽出を行います。
**原本は読み取るだけで、変更しません。** 生成した情報パッケージは、利用者が指定した
出力先にのみ書き出します。

**利用者が入力した記述メタデータ。** タイトルや受入番号など、画面から入力された値は、
生成する情報パッケージの中にのみ記録されます。

**個人情報の候補の検出結果。** アプリには、扱う資料の中に個人情報が含まれていないかを
確認する機能があります。マイナンバー、クレジットカード番号、メールアドレス、電話番号、
郵便番号の候補を検出します。**検出結果は必ずマスクして記録します**
（例: `123-****-**89`）。レポート自体が漏洩源にならないようにするためです。
この結果も、利用者が指定した出力先に書き出されるだけで、外部には送信されません。

### 通信

アプリが外部と通信するのは、**利用者が設定画面で「ウイルス定義の更新」を実行したときだけ**です。
このとき、同梱している freshclam が `database.clamav.net` からウイルス定義データベースを
取得します。送信されるのは定義の取得要求のみで、利用者のファイルや入力内容は一切送信されません。

これ以外に、アプリが自ら通信を行うことはありません。利用状況の送信（テレメトリ）、
クラッシュレポートの送信、更新確認のいずれも行いません。

### 保存先

- ウイルス定義データベース: Windows は `%LOCALAPPDATA%`、macOS は `~/.local/share` 配下
- 生成した情報パッケージ: 利用者が指定した出力先のみ
- 一時ファイル: OS の一時フォルダ（処理の終了後に削除されます）

### アンインストール

アプリを削除すると、アプリ本体と同梱ツールが削除されます。ダウンロードしたウイルス定義
データベースは上記の場所に残るため、不要であれば手動で削除してください。
生成した情報パッケージは、利用者の資料そのものであるため削除されません。

### 第三者への提供

ありません。開発者は利用者のデータを取得しないため、提供する対象がありません。

### 同梱している第三者のソフトウェア

ClamAV、Siegfried を同梱しています。ライセンスと入手方法は配布物に含まれる `NOTICE` に
記載しています。ClamAV は定義の取得時にのみ通信します。

### お問い合わせ

中村 覚（東京大学史料編纂所） nakamura@hi.u-tokyo.ac.jp

---

## English

### Summary

**This application does not collect any personal information, and does not transmit
anything to the developer or any third party.**

All processing happens locally on the user's device. The developer has no means of
knowing what files a user processes.

### Information handled

**Files the user selects.** The application reads the folders and files the user
specifies, in order to identify formats, compute checksums, and extract technical
metadata. **Originals are read only and are never modified.** Generated information
packages are written only to the output location the user chooses.

**Descriptive metadata the user enters.** Values entered through the interface, such as
title and accession number, are recorded only inside the generated package.

**Results of personally identifiable information (PII) detection.** The application can
flag candidate PII in the material being processed: Japanese individual numbers, credit
card numbers, email addresses, telephone numbers, and postal codes. **Results are always
masked** (for example `123-****-**89`) so that the report itself cannot become a source
of disclosure. These results are written only to the user's chosen output location.

### Network access

The application contacts the network **only when the user explicitly runs "update virus
definitions" from the settings screen.** The bundled `freshclam` then downloads the
ClamAV signature database from `database.clamav.net`. Only the request for the database
is sent; no user file or user input is transmitted.

The application performs no other network activity. There is no telemetry, no crash
reporting, and no update check.

### Storage locations

- Virus definition database: under `%LOCALAPPDATA%` on Windows, `~/.local/share` on macOS
- Generated information packages: only the output location chosen by the user
- Temporary files: the operating system's temporary directory (removed after processing)

### Uninstallation

Removing the application removes the application and its bundled tools. Any downloaded
virus definition database remains in the location above and can be deleted manually.
Generated information packages are the user's own material and are not removed.

### Disclosure to third parties

None. The developer receives no user data, so there is nothing to disclose.

### Bundled third-party software

ClamAV and Siegfried are bundled. Their licenses and how to obtain their sources are
stated in the `NOTICE` file included with the distribution. ClamAV accesses the network
only when definitions are updated.

### Contact

Satoru Nakamura, Historiographical Institute, The University of Tokyo — nakamura@hi.u-tokyo.ac.jp
