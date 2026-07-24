# Flet 実行可能性検証（使い捨て）

`aip`（Swift / macOS 専用）を Windows にも出すため、1 コードベース化の移植先として
**Flet（Python + Flutter）** が成立するかを事実で確かめる。ここが通れば本実装へ進み、
このディレクトリは削除する。通らなければ Electron + TypeScript に切り替える。

## なぜ Flet を第一候補にしたか

移植対象 4,100 行の中核は保存パッケージ（SIP/AIP）の生成であり、この領域の
参照実装はほぼ Python に集中している。

| 用途 | Python | TypeScript |
|---|---|---|
| BagIt | `bagit`（米国議会図書館の公式実装） | 相当品なし |
| METS/PREMIS の XSD 検証 | `lxml` | 相当品なし |
| PDF テキスト抽出（PII 走査用） | `pypdf` / `pdfminer.six` | 選択肢はあるが弱い |

BagIt を自前で書き直さずに済むことが最大の利点。保存パッケージの正しさが
このアプリの存在理由なので、ここは実装リスクを持ち込みたくない。

同じ理由で Archivematica も元の CCA-Public/sipcreator も Python であり、
今後の機能追加でも参照実装が手に入る。

## 唯一の懸念＝この検証で潰すもの

Flet の配布パイプラインは Electron ほど踏み固められていない。特に
**外部バイナリ（sf / clamscan / gs / magick）の同梱**と**署名・公証**は前例が少ない。
推測で採否を決めず、以下 3 点だけを実測する。

| # | 確認すること | 合格条件 | 結果 |
|---|---|---|---|
| 1 | 同梱バイナリの起動 | `flet build macos` した .app から、同梱した `sf -version` が動く | ✅ 合格 |
| 2 | 署名と公証 | その .app に Developer ID 署名（hardened runtime）と公証が通り、Gatekeeper を抜ける | ✅ 合格 |
| 3 | Windows ビルド | 同一ソースから `flet build windows` が通り、同梱 `sf.exe` が動く | 実行中 |

### 検証 2 の最終結果

```
notarytool: status: Accepted
stapler:    The staple and validate action worked!
spctl:      accepted / source=Notarized Developer ID
```

公証済みアプリでの実行結果（`~/flet-spike-report.txt`）:

```
HIT  [bundle:Contents/Resources/bin] .../Flet Spike.app/Contents/Resources/bin/sf
default.sig: 同梱を使用
siegfried 1.11.4
.../Flet Spike.app/Contents/Resources/bin/default.sig
結果: OK 同梱バイナリの起動に成功
```

既知の場所で一発ヒットし、総当たり探索へのフォールバックは発生していない。
`default.sig` も同梱したものを参照しており、開発機の Homebrew 版を拾っていない。

### 1 について

パッケージ後は Python が bundle 内へ展開されるため、開発時と実行時でバイナリの
在処が変わる。`main.py` は在処を推測せず候補を総当たりし、**実際に見つかった場所**を
画面に出す。実行ビットが落ちる可能性も想定し、`chmod` での復旧可否まで確認する。

### 2 について

証明書は確認済み（`Developer ID Application: Satoru Nakamura (Q6S8JS6GWV)`、
現行 aip の `project.yml` の Team と一致）。焦点は、Flutter が生成した .app 構造に対して
`--macos-entitlements` 経由で hardened runtime を適用しつつ、同梱バイナリの起動が
ブロックされないかどうか。

### 3 について

Flutter は macOS から Windows デスクトップをクロスビルドできない。GitHub Actions の
`windows-latest` で確認する。つまりこの項目の合格には CI の用意が要る。

## 検証中に判明した落とし穴

### `flet build` が「CocoaPods not installed」で失敗する（macOS・解決済み）

**症状**: `flutter build macos` を直接叩けば `pod install` が 6.9 秒で通るのに、
`flet build macos` 経由だと 4 秒で `CocoaPods not installed or not in valid state.` で落ちる。
`flutter doctor` は（`uv run` 経由でも）`[✓] Xcode` を返すため、原因が見えにくい。

**原因**: `flet_cli/commands/build.py:744` が PATH を組み直す際、
`flet.utils.cleanup_path(path, "flutter")` を通す。この関数は
**flutter 実行ファイルを含むディレクトリを PATH から丸ごと除去する**
（Flet は自前の Flutter 3.29.2 を使うため、システム Flutter を締め出す意図）。

Homebrew で Flutter を入れていると `/opt/homebrew/bin` が除去対象になり、
**同じディレクトリにいる `pod` も巻き添えで消える**。

```
除去された PATH 項目: ['/opt/homebrew/bin']
flet の PATH での pod: NOT_FOUND
```

**回避**: `pod` を flutter がいないディレクトリへ symlink する。Flutter 側は触らない。

```
ln -sf /opt/homebrew/bin/pod ~/.local/bin/pod
```

**CI での注意**: 同じ理由で、Flutter と CocoaPods が同一ディレクトリに入る構成の
ランナーでは再発する。`flet build` の前に `command -v pod` を
**cleanup_path 適用後の PATH で**確認すること。Flutter を別途インストールする
action（subosito/flutter-action 等）は、この問題を誘発するので使わない
（Flet が自前で取得するため、そもそも不要）。

## 実測で分かった本実装への要件

### 同梱バイナリは app.zip 内に入り、実行時に展開される

`flet build` は Python アプリ一式（`assets/` 含む）を
`.app/Contents/Frameworks/App.framework/Versions/A/Resources/flutter_assets/app/app.zip`
に固める。実行時は次の場所へ展開される。

```
~/Library/Application Support/<bundle-id>/flet/app/
```

`__file__` はこの展開先を指すので、**`Path(__file__).parent / "assets" / "bin" / "sf"`
で解決できる**。総当たり探索は不要（検証用に入れているだけ）。

### 展開時に実行ビットが落ちる ← 必ず対処が要る

zip 展開でパーミッションが失われ、初回起動は `PermissionError` になる。
検証アプリは `chmod(0o755)` して再試行し、成功している（レポートの
`(chmod 755 後に成功)`）。**本実装でも同梱バイナリ起動前に実行ビットを
確認・付与する処理が必須。**

### ad-hoc 署名は zip 往復で保持される（Apple Silicon で重要）

Apple Silicon は全実行ファイルに署名を要求する。同梱 sf は Homebrew 由来の
ad-hoc(linker-signed) 署名を持ち、zip 往復後も CodeDirectory が同一であることを確認した。
したがって展開後もそのまま起動できる。

### hardened runtime 署名は同梱バイナリの起動を妨げない

Developer ID + hardened runtime（entitlements は空 dict、サンドボックス無効）で
署名した .app から、展開された sf が問題なく起動した。
`disable-library-validation` は現時点で不要。

### 同梱バイナリは app.zip に入れてはいけない（公証が弾く）← 実測で確定

最初の構成（Python アプリ側の `assets/bin/sf`）は **起動はできるが公証で Invalid になる**。
Apple の審査は app.zip の中まで降りて検査する。

```
path: .../flutter_assets/app/app.zip/assets/bin/sf
  The binary is not signed with a valid Developer ID certificate.
  The signature does not include a secure timestamp.
  The executable does not have the hardened runtime enabled.
```

zip 内のデータは署名できないため、この構成は原理的に公証を通せない。
**バイナリは `.app/Contents/Resources/bin/` に置き、個別に Developer ID で署名する。**
現行 aip（Swift 版）が `Resources/bin/` に置いているのと同じ構成に揃うことになる。

配置後の解決は `Path(sys.executable).parent.parent / "Resources" / "bin"` で足りる
（`sys.executable` は `<App>.app/Contents/MacOS/<exe>`）。

### 署名対象を実行ビットで絞ってはいけない ← 実測で確定

`find -perm +111` で Mach-O を絞ると **dylib を取りこぼす**。実際に
`Python.framework/Versions/3.12/lib/` の `libssl.3.dylib` と `libcrypto.3.dylib` は
mode `rw-r--r--`（実行ビット無し）で、これが未署名のまま残って公証が Invalid になった。

判定は必ず `file(1)` の Mach-O 判定で行い、パーミッションで絞らないこと。
署名後は「未署名の Mach-O が残っていないか」を全件 `codesign -v` で確認する
（`sign.zsh` の [4/4] がこれを行う）。

### siegfried の署名ファイルを同梱していない ← 対処済み

検証中、sf は `default.sig` を
`/opt/homebrew/Cellar/siegfried/1.11.4/share/siegfried/default.sig` から読んでいた。
これは**この開発機に Homebrew 版 siegfried が入っているから**であって、
配布先の環境には存在しない。現行 aip は `Resources/bin/default.sig` を同梱している。

**本実装では `default.sig` を同梱し、`sf -sig <同梱パス>` と明示的に渡すこと。**
これを怠ると開発機では動くのに配布先で識別が失敗する（気づきにくい）。

## 実行方法

```
uv run flet run spike                                      # 開発モード
uv run flet build macos spike --product "Flet Spike" --bundle-id com.nakamura.fletspike
```

同梱バイナリは `spike/assets/bin/sf`（`.gitignore` 済み。`aip/app/Resources/bin/sf` から複製）。
