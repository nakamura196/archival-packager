---
title: "変換規則を自分で足す"
eyebrow: Archival Packager 技術資料
---

Archival Packager は、AIP を作るときに「この形式は保存用にこう変換する」という
規則にしたがって派生物を作ります。この規則は表（1 枚のテキストファイル）で
持っていて、**利用者が書き足せます**。

何も設定しなければ、最初から入っている 2 つの規則だけが動きます。この文書は、
それ以外の変換を足したいときのためのものです。

## どこに置くか

`rules.toml` という名前で、アプリが記録（`errors.log`）を置いているのと同じ
場所に置きます。

| OS | 置き場所 |
| --- | --- |
| Windows | `%LOCALAPPDATA%\ArchivalPackager\rules.toml` |
| macOS / Linux | `~/.local/share/ArchivalPackager/rules.toml`（`XDG_DATA_HOME` を設定している場合はその下） |

ファイルが無ければ、最初から入っている規則だけで動きます。
**書き換えたあとはアプリを開き直してください。** 1 回の変換の途中で表が
変わると、同じパッケージの中に説明の付かない差が生まれるため、表は起動時に
1 回だけ読みます。

## 書き方

書式は TOML です。規則 1 つを `[[rule]]` で始めます。

```toml
[[rule]]
id              = "wav-to-flac"
puid_in         = ["fmt/141", "fmt/142"]
purpose         = "preservation"
executor        = "command"
tool            = "flac"
args            = ["--best", "-o", "{out}", "{in}"]
puid_out        = "fmt/279"
format_name_out = "Free Lossless Audio Codec"
out_extension   = "flac"
```

| 項目 | 要否 | 意味 |
| --- | --- | --- |
| `id` | 必須 | 規則の名前。保存処理の記録（PREMIS）にそのまま書かれます |
| `puid_in` | 必須 | 変換したい形式の PRONOM 識別子。複数書けます |
| `purpose` | 任意 | 今は `"preservation"`（保存用）のみ。省略すると保存用 |
| `executor` | 必須 | `"command"` と書きます（外部コマンドを呼ぶ、の意味） |
| `tool` | 必須 | 実行するコマンド名 |
| `args` | 必須 | コマンドに渡す引数。**文字列のリスト**で書きます |
| `puid_out` | 任意 | 変換後の形式の PRONOM 識別子 |
| `format_name_out` | 任意 | 変換後の形式の名前。省略すると画面上「unknown」と出ます |
| `out_extension` | 必須 | 変換後のファイルの拡張子（英数字のみ） |

`id` は**一度使ったら変えないでください**。過去に作った AIP の記録にこの文字列が
書き込まれています。名前を変えると、その AIP を後から読んだ人が、今の表と
突き合わせられなくなります。

### `{in}` と `{out}`

`args` の中で使える差し込みは、次の 2 つ**だけ**です。

- `{in}` … 変換元（原本）のファイルのパス
- `{out}` … 変換先のファイルのパス

両方とも必ず 1 回は書いてください。`{out}` が無いとどこにも書き出されず、
`{in}` が無いと原本を読みません。どちらも「実行はできるが何も起きない」ので、
表を読む時点で無効にします。

`{tmp}` のような、上の 2 つ以外の差し込みを書いた規則は無効になります。

### PRONOM 識別子の調べ方

`puid_in` に書く `fmt/141` のような文字列は、英国国立公文書館の
[PRONOM](https://www.nationalarchives.gov.uk/PRONOM/) が付けている形式の
識別子です。SIP を作ったときの `formats.csv`（提出書類の中）に、資料ごとの
識別結果が入っているので、そこから写すのが確実です。

## できないこと

### コードは読み込みません

規則表に書けるのは**データだけ**です。Python のコードやスクリプトを
読み込ませることはできません。

このアプリは Apple の公証と Microsoft ストアの審査を通して配布しています。
実行時に外部のコードを読み込めるようにすると、審査が見たものと実際に動くものが
別になります。資料を預かる道具なので、ここは保守的にしています。

### アプリ内蔵の処理は呼べません

`executor = "builtin"` は、最初から入っている画像変換（Pillow）のための
書き方で、**利用者の表からは指定できません**。指定した規則は無効になります。
外部コマンドを呼ぶ `"command"` を使ってください。

### `tool` にパスは書けません

`tool` にはコマンド名だけを書きます。`/usr/local/bin/flac` や `../flac` の
ような書き方はできません。相対パスは「どこから見た相対か」がアプリの動き方で
変わり、`..` はその外側を指せてしまうためです。コマンドは `PATH` の通った
場所に置いてください。

### シェルは通しません

`args` は必ず文字列のリストで書きます。`args = "{in} > {out}"` のように
1 本の文字列で書くことはできません。リダイレクトやパイプ、ワイルドカードは
使えません。

### 利用用（access）の派生物はまだ作れません

`purpose = "access"` を書いた規則は、いまのところ無効になります。
受け付けてしまうと、保存用として記録された利用用のファイルが AIP に
入ってしまいます。記録が実態と違うほうが、機能が無いことより困ります。

## 書き間違えたとき

**表が壊れていてもアプリは起動します。** 読めなかった規則は使われず、
理由が変換結果の警告に出ます。たとえばこう出ます。

```
変換規則表: 規則 wav-to-flac: args に使えない印があります: {tmp}。使えるのは {in}（原本）と {out}（変換先）だけです。
```

書いたはずの規則が効いていないと感じたら、まずこの警告を見てください。

すでに入っている規則と同じ `id` を使った場合、あとから書いたほうが無効に
なります（記録の中で「どちらの規則で作られたか」を決められなくなるため）。

## 規則を足すと何が起きるか

**同じ資料を入れても、作られる AIP が変わります。**

これは意図した動作ですが、同時に厄介な性質でもあります。あなたの組織で
作った AIP と、別の組織が同じ資料から作った AIP の中身が違い、その理由が
パッケージの外（それぞれの `rules.toml`）にあることになるからです。

そこで Archival Packager は、AIP を作るたびに

- どの規則で変換したかを、保存処理の記録（PREMIS）に書き
  （`rule="wav-to-flac"; ruleSource="user"; program="flac"; version="flac 1.4.3"; ...`）
- **そのとき効いていた規則の表そのもの**を、
  `objects/submissionDocumentation/normalization-rules.toml` として
  パッケージの中に入れます

2 つめが要点です。Archivematica は規則の識別子だけを記録し、規則の中身は中央の
登録簿（FPR）にあります。後年その登録簿が引けなくなると、識別子が残っていても
何をしたのか分かりません。表そのものを一緒に入れておけば、**そのパッケージ
だけで説明が付きます**。

`ruleSource` は、その規則が最初から入っていたもの（`builtin`）か、利用者が
足したもの（`user`）かを表します。パッケージの違いが「表を足したから」なのか
どうかを、後から見た人が判断できるようにするためのものです。

同梱される表には、読めなかった規則の理由もコメントとして残ります。
あるはずの変換が無い理由が、そのファイルだけで分かるようにするためです。

## 最初から入っている規則

参考までに、何も設定しないときに動く 2 つの規則です。利用者が書くのと
まったく同じ書式で書いてあります（同じ書式・同じ読み込み経路を使うことで、
利用者向けの仕組みが使われないまま壊れることを防いでいます）。

```toml
[[rule]]
id = "image-to-tiff"
purpose = "preservation"
executor = "builtin"          # アプリ内蔵（Pillow）。利用者の表からは指定できません
tool = "pillow"
args = []
puid_in = ["fmt/11", "fmt/12", "fmt/13", "fmt/935", "fmt/41", "..."]  # PNG / JPEG / GIF / BMP
puid_out = "fmt/353"
format_name_out = "Tagged Image File Format"
out_extension = "tiff"

[[rule]]
id = "postscript-to-pdf"
purpose = "preservation"
executor = "command"
tool = "gs"                   # Ghostscript。同梱していないので、入っていれば使います
args = ["-dNOPAUSE", "-dBATCH", "-dSAFER", "-sDEVICE=pdfwrite", "-sOutputFile={out}", "{in}"]
puid_in = ["fmt/124", "fmt/501", "fmt/122", "fmt/123", "..."]         # PostScript / EPS
puid_out = "fmt/276"
format_name_out = "Acrobat PDF 1.7 - Portable Document Format"
out_extension = "pdf"
```

同じ形式に対して自分の規則を書くと、そちらが優先されます。

## 例: Ghostscript で PDF/A-2b を作る

最初から入っている `postscript-to-pdf` が作るのは、ふつうの PDF です。
PostScript / EPS から PDF/A-2b を作りたいときは、次の規則を足します。
アプリの機能として組み込んでいない理由は
[既知の限界](limits.md#pdfa-は作らない) にあります。

**Ghostscript（`gs`）が入っている機械でだけ動きます。** アプリには同梱していません。

### 1. 準備するファイル

PDF/A には、色の基準になる ICC プロファイル（出力インテント）を埋め込む必要があります。
Ghostscript に付属の 2 つのファイルを、1 つのフォルダに写します。
ここでは `rules.toml` と同じ場所の `pdfa` というフォルダにします
（例は macOS で、利用者名を `you` としています）。

| ファイル | 付属している場所（Homebrew の例） |
| --- | --- |
| `PDFA_def.ps` | `/opt/homebrew/share/ghostscript/<版>/lib/PDFA_def.ps` |
| `srgb.icc` | `/opt/homebrew/share/ghostscript/<版>/iccprofiles/srgb.icc` |

写した `PDFA_def.ps` の 1 か所を、ICC プロファイルの**絶対パス**に書き換えます。

```postscript
/ICCProfile (/Users/you/.local/share/ArchivalPackager/pdfa/srgb.icc) % Customise
```

元の `(srgb.icc)` のままだと、Ghostscript は**アプリを起動したフォルダ**から探します。
見つからなくても変換は成功扱いで終わり、PDF/A ではない PDF ができます（下の「注意」）。

### 2. 規則

```toml
[[rule]]
id              = "postscript-to-pdfa2b"
puid_in         = ["fmt/124", "fmt/501", "x-fmt/91", "x-fmt/406", "x-fmt/407", "x-fmt/408", "fmt/122", "fmt/123"]
executor        = "command"
tool            = "gs"
args            = [
  "-dNOPAUSE", "-dBATCH", "-dSAFER",
  "-dPDFA=2", "-dPDFACompatibilityPolicy=2", "-sColorConversionStrategy=RGB",
  "-sDEVICE=pdfwrite",
  "--permit-file-read=/Users/you/.local/share/ArchivalPackager/pdfa/",
  "-sOutputFile={out}",
  "/Users/you/.local/share/ArchivalPackager/pdfa/PDFA_def.ps",
  "{in}",
]
puid_out        = "fmt/476"
format_name_out = "Acrobat PDF/A - Portable Document Format 2b"
out_extension   = "pdf"
```

`puid_in` が最初から入っている規則と同じなので、この規則が優先されます。
引数の意味は次のとおりです。

- `-dPDFA=2` … PDF/A-2 として書き出す
- `-sColorConversionStrategy=RGB` … 色を RGB にそろえる（埋め込む ICC が RGB のため）
- `-dPDFACompatibilityPolicy=2` … PDF/A にできない箇所があれば、PDF/A にするのを**やめる**。
  `1` にすると、その箇所を黙って落として続けます。保存用には `2` を勧めます
- `--permit-file-read=...` … `-dSAFER` は既定でファイルの読み込みを止めるので、
  ICC プロファイルのあるフォルダだけを読めるようにする
- `PDFA_def.ps` は `{in}` より**前に**置く

Windows では、パスを `C:/Users/you/AppData/Local/ArchivalPackager/pdfa/` のように
`/` で書きます（`PDFA_def.ps` の中も同じ）。

### 3. 注意: できた PDF が PDF/A とは限らない

**アプリは、できた PDF が PDF/A に適合するかを確かめていません。**
確かめているのは、開き直せるかと、JHOVE で PDF として正しいかだけです。

困るのは、準備が不十分でも**変換が成功扱いで終わる**ことです。
2026年11月6日、Ghostscript 10.07.1 で、ICC プロファイルが見つからない状態で試しました。
Ghostscript は「PDF/A の処理を中止した」と表示しながら終了コード 0 で終わりました。
できたファイルには PDF/A-2b を名乗る印が入っていて、出力インテントはありませんでした。
この規則のままでは、その PDF が PREMIS に「PDF/A-2b（fmt/476）」と記録されます。

規則を足したら、最初の何件かを [veraPDF](https://verapdf.org/) で検査してください。

```
verapdf -f 2b <できた PDF>
```

フォントを埋め込めない PostScript や、透明・特殊な色を使う PostScript は、
PDF/A にできないことがあります。`-dPDFACompatibilityPolicy=2` のときは、
その旨を表示して、PDF/A ではない PDF を書き出すことがあります。これも veraPDF で分かります。
