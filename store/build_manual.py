# -*- coding: utf-8 -*-
"""Microsoft ストア提出の手順書を docx で作る。

貼り付ける文面も全部入れて、この 1 冊だけ見れば済む形にする。
PDF 化は scripts/../../paper/to_pdf.applescript と同じ手で行う。
"""
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

BASE = "https://partner.microsoft.com/en-us/dashboard/products/9N6XJD7THHPZ/submissions/1152921505701841218"

doc = Document()

# 既定のフォント（日本語）
style = doc.styles["Normal"]
style.font.name = "Hiragino Sans"
style.font.size = Pt(10.5)
style.element.rPr.rFonts.set(qn("w:eastAsia"), "Hiragino Sans")
for s in doc.sections:
    s.top_margin = s.bottom_margin = Cm(2.0)
    s.left_margin = s.right_margin = Cm(2.0)


def h(text, level=1):
    p = doc.add_heading(text, level=level)
    for r in p.runs:
        r.font.name = "Hiragino Sans"
        r._element.rPr.rFonts.set(qn("w:eastAsia"), "Hiragino Sans")
        r.font.color.rgb = RGBColor(0x1F, 0x29, 0x37)
    return p


def para(text, bold=False, indent=0.0):
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(indent)
    p.paragraph_format.space_after = Pt(4)
    r = p.add_run(text)
    r.bold = bold
    return p


def mono(text, indent=0.4):
    """貼り付ける値。等幅にして、行を折り返しても読めるようにする。"""
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(indent)
    p.paragraph_format.space_after = Pt(4)
    r = p.add_run(text)
    r.font.name = "Menlo"
    r.font.size = Pt(9)
    r._element.rPr.rFonts.set(qn("w:eastAsia"), "Menlo")
    return p


def bullet(text, indent=0.5):
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.left_indent = Cm(indent)
    p.paragraph_format.space_after = Pt(2)
    r = p.add_run(text)
    r.font.name = "Hiragino Sans"
    r._element.rPr.rFonts.set(qn("w:eastAsia"), "Hiragino Sans")
    return p


# ---------------------------------------------------------------- 表紙
t = doc.add_heading("Microsoft ストア提出 手順書", level=0)
for r in t.runs:
    r.font.name = "Hiragino Sans"
    r._element.rPr.rFonts.set(qn("w:eastAsia"), "Hiragino Sans")
para("Archival Packager / 2026年9月9日")
para("")
para("この手順書だけで提出が完了します。貼り付ける文面はすべて本文に入れてあります。", bold=True)
para("")
para("いま提出の下書き（Submission 1）は作成済みで、5つの必須項目がすべて「Not started」の"
     "状態です。すべてが「Complete」になると、画面上部の Submit for certification ボタンが"
     "押せるようになります。いま灰色なのはそのためです。")

h("はじめに確認すること", 1)
bullet("Partner Center に、個人アカウントでサインインしていること")
bullet("Apps and games → Archival Packager を開いていること")
bullet("画面に「Product submission: In draft (Submission 1)」と出ていること")
para("")
para("以下、各項目のリンクは Partner Center の直リンクです。ブラウザのアドレス欄に貼れば"
     "その画面に直接行けます。")

# ---------------------------------------------------------------- 0
h("0. 先に用意するもの", 1)

para("提出に使うファイルは2つです。作業を始める前に手元に置いてください。")
para("")
para("(1) アプリ本体（MSIX ファイル・110MB）", bold=True)
para("次のページを開き、ArchivalPackager-0.1.0-windows-x64.msix をダウンロードします。", indent=0.4)
mono("https://github.com/nakamura196/archival-packager/releases/tag/v0.1.0")
para("※ このファイルは 9月9日に差し替えています。それ以前にダウンロードしたものがあれば、"
     "取り直してください。対応言語の宣言などを直しています。", indent=0.4)
para("")
para("(2) スクリーンショット（PNG・1枚）", bold=True)
mono("~/git/kim/archival-packager/store/screenshots/01-sip.png")
para("1486 × 973 ピクセル。ストアの要件（1366 × 768 以上）を満たしています。", indent=0.4)

# ---------------------------------------------------------------- 1
doc.add_page_break()
h("1. Packages（アプリ本体を上げる）", 1)
para("アップロードに数分かかるので、最初にやります。")
mono(f"{BASE}/packages")
para("")
para("手順", bold=True)
bullet("ドロップ領域に MSIX ファイルをドラッグする（またはファイルを選ぶ）")
bullet("アップロードが終わると検証が走り、「Validated」と表示される")
bullet("Device family availability は既定のままでよい")
para("")
para("注意", bold=True)
para("パッケージが「Validated」になっても、画面の項目が「Incomplete」のままになることが"
     "あります。パッケージ以外の設定が残っているときに起きます。他の項目を先に埋めてから"
     "戻ってきてください。", indent=0.4)

# ---------------------------------------------------------------- 2
h("2. Pricing and availability（価格と提供範囲）", 1)
mono(f"{BASE}/availability")
para("")
para("設定するのは1か所だけです。", bold=True)
bullet("Base price を Free（無料）にする")
para("")
para("次の3つは既定のままで結構です。", bold=True)
bullet("Markets … すべての国・地域（画面は日本語だけですが、日本の資料を扱う海外の機関も"
       "あるため制限しません）")
bullet("Audience … Public audience")
bullet("Schedule … Release as soon as possible")

# ---------------------------------------------------------------- 3
h("3. Properties（製品の性質）", 1)
mono(f"{BASE}/properties")
para("")
para("Category（カテゴリ）", bold=True)
mono("Productivity")
para("サブカテゴリは指定しません。", indent=0.4)
para("")
para("Privacy policy URL（プライバシーポリシー）", bold=True)
mono("https://nakamura196.github.io/archival-packager/privacy-policy.html")
para("必須です。デスクトップアプリは、規約 10.5.1 で常に求められます。", indent=0.4)
para("")
para("Support contact info（サポート連絡先）", bold=True)
mono("nakamura@hi.u-tokyo.ac.jp")
para("")
para("Website、System requirements、Product declarations は空欄で構いません。")

# ---------------------------------------------------------------- 4
doc.add_page_break()
h("4. Age ratings（年齢レーティング）", 1)
mono(f"{BASE}/ageratings")
para("")
para("IARC という第三者機関の質問票に答えます。ここは自動化できず、手作業が必要です。")
para("")
para("画面の上に「IARC has recently updated the age ratings questionnaire」という警告が"
     "出ています。質問が追加されているので、未回答の欄が残らないよう最後まで進めてください。",
     bold=True)
para("")
para("実際の質問と答え（2026年9月9日時点の画面）", bold=True)
bullet("App Type … All Other App Types を選ぶ")
bullet("Downloaded App（性的表現・暴力・言葉づかいが同梱されているか） … No")
bullet("User Content Sharing（利用者どうしのやりとり） … No")
bullet("Online Content（アプリから見られる、同梱外のコンテンツ） … No")
bullet("Promotion or Sale of Age-Restricted Products（たばこ・酒・銃・賭博） … No")
bullet("現在地を他の利用者と共有するか … No")
bullet("デジタル商品を購入できるか … No")
bullet("現金報酬・ギフトカード・暗号資産・NFT … No")
bullet("Web ブラウザまたは検索エンジンか … No")
bullet("主にニュースまたは教育の製品か … No")
bullet("審査機関から直接レーティングを取得するか／物理メディアで配布するか … No")
para("")
para("App Type 以外はすべて No です。答えたあと Preview ratings を押して確定します。",
     bold=True)
para("")
para("迷いやすい2つ", bold=True)
para("Online Content は「利用者が見るコンテンツ」を指します。例として挙がっているのが "
     "Netflix の映画、Amazon の商品一覧、Spotify の曲、ニュース記事です。ウイルス定義"
     "データベースは利用者が見るものではなく、アプリが動くためのデータなので No です。",
     indent=0.4)
para("")
para("「主にニュースまたは教育の製品か」も No です。研究の成果物ではありますが、この質問は"
     "「学習用のコンテンツを提供する製品」を指しています。本アプリは業務用の道具です。",
     indent=0.4)
para("")

# ---------------------------------------------------------------- 5
doc.add_page_break()
h("5. Store listings（掲載情報）", 1)
para("一番手数の多い項目です。2つのやり方があります。")
para("")
h("方法A：CSV で一括取り込み（おすすめ）", 2)
para("説明文の入力とスクリーンショットのアップロードを、フォルダ1つの取り込みで済ませます。")
para("")
bullet("提出の一覧画面に戻り、「Store listings」の行を見る")
bullet("「Store listings」の文字のすぐ下に、小さな青いリンクが3つ横に並んでいる"
       "（Add/remove languages ／ Export listings ／ Import listings）")
bullet("右端の矢印ではなく、この小さいリンクの「Export listings」を押す")
bullet("CSV がダウンロードされる。そのファイルの場所を中村（AI）に伝える")
bullet("埋めた CSV とスクリーンショットの入ったフォルダを受け取る")
bullet("「Import listings」→「Upload folder」でそのフォルダを選ぶ")
para("")
para("CSV の Field / ID / Type の列は書き換え禁止です。この値は先生の環境から出した"
     "ものでないと合わないため、雛形のダウンロードだけはお願いする必要があります。", indent=0.4)
para("")
h("方法B：画面で直接入力する", 2)
mono(f"{BASE}/managelanguages?producttype=app")
para("")
bullet("まず言語に「日本語（ja-JP）」を追加する")
bullet("Description（説明）に、この手順書の付録Aの文章を貼る")
bullet("Screenshots に 01-sip.png を上げる（最低1枚。必須）")
bullet("Search terms に、付録Aの検索キーワード7つを入れる")

# ---------------------------------------------------------------- 6
h("6. Submission options（提出時の申告）", 1)
mono(f"{BASE}/options")
para("")
para("いまは「Recommended」と表示されていますが、MSIX を上げると必須に変わる可能性が"
     "高い項目です。", bold=True)
para("")
para("Restricted capabilities（制限付き機能の申告）", bold=True)
para("このアプリは runFullTrust という制限付き機能を宣言しています。同梱した"
     "clamscan と sf を子プロセスとして起動するために必要なものです。申告欄が出たら、"
     "付録Bの文章を使ってください。", indent=0.4)
para("")
para("Notes for certification（審査担当者へのメモ）", bold=True)
para("付録Bの文章を貼ってください。省略しないでください。ウイルス対策エンジンである"
     "ClamAV を同梱しているため、説明がないと問い合わせで審査が止まる可能性があります。",
     indent=0.4)

# ---------------------------------------------------------------- 7
h("7. 提出する", 1)
para("6つすべてが「Complete」になると、画面上部の Submit for certification ボタンが"
     "押せるようになります。")
para("")
bullet("Application overview に戻る")
bullet("Submit for certification を押す")
bullet("状態が「In certification」に変わる")
para("")
para("審査は通常2〜3営業日です。結果はメールで届きます。")
para("")
para("公開されると、次の URL が生きます。それまでは 410（存在しない）を返します。")
mono("https://apps.microsoft.com/detail/9N6XJD7THHPZ")

h("うまくいかないとき", 1)
para("Submit ボタンが押せない", bold=True)
para("どれかの項目が「Complete」になっていません。一覧に戻って、灰色の"
     "「Not started」や「Incomplete」が残っていないか見てください。", indent=0.4)
para("")
para("Packages が Incomplete のまま", bold=True)
para("パッケージ自体が Validated でも、他の必須設定が残っていると消えません。"
     "他の項目を埋めてから戻ってください。", indent=0.4)
para("")
para("Age ratings が Incomplete のまま", bold=True)
para("IARC の質問票が更新され、質問が追加されています。追加分に未回答が"
     "残っている可能性があります。", indent=0.4)
para("")
para("審査で差し戻された", bold=True)
para("理由がメールと画面に出ます。ClamAV 関連を指摘された場合は、付録Bの説明が"
     "入っているか確認してください。", indent=0.4)

# ---------------------------------------------------------------- 付録A
doc.add_page_break()
h("付録A　掲載情報（貼り付け用）", 1)

para("製品名", bold=True)
mono("Archival Packager")
para("")
para("簡単な説明（Short description）", bold=True)
mono("デジタル資料から、国際標準 OAIS の情報パッケージ（SIP・AIP）を作成するアプリです。"
     "フォーマット識別、ウイルス検査、チェックサムの算出、保存処理記録の作成を、"
     "コマンドライン操作なしで行えます。文書館・史料館・資料館での資料の受入と"
     "長期保存を想定しています。")
para("")
para("説明（Description）", bold=True)
for line in [
    "Archival Packager は、デジタル資料を長期保存するための「情報パッケージ」を作成する"
    "デスクトップアプリケーションです。",
    "",
    "デジタル資料の長期保存には OAIS（Open Archival Information System）という国際標準が"
    "ありますが、これを実装したシステムは導入と運用に高い技術力を必要とし、小規模な組織では"
    "手が出ないのが実情です。本アプリは、その最初の一歩にあたる作業を、専門的な知識なしに"
    "行えるようにしたものです。",
    "",
    "【できること】",
    "・フォーマットの識別（Siegfried / PRONOM の識別子を付与）",
    "・ウイルス検査（ClamAV）",
    "・チェックサムの算出（SHA-256）",
    "・技術メタデータの記録（DFXML 形式）",
    "・記述シート（AtoM / ISAD(G) 向け）、技術インベントリ、受入記録の作成",
    "・個人情報の候補の検出（マイナンバー、クレジットカード番号など。結果はマスク表示）",
    "・提出用情報パッケージ（SIP）の作成。BagIt 形式にも対応",
    "・保存用情報パッケージ（AIP）の作成。METS に PREMIS を埋め込み、いつ何を行ったかの"
    "記録を残します",
    "",
    "【特長】",
    "・インストール作業が要りません。識別と検査に使う外部ツールは同梱しています",
    "・原本を変更しません。読み取るだけで、保存用に変換したファイルは別に作ります",
    "・出力は標準的な形式です。Archivematica や AtoM が読み取れる形に揃えているため、"
    "組織の体制が整った段階で、標準的なシステムへ移行できます",
    "",
    "【想定している利用者】",
    "文書館・史料館・資料館・企業アーカイブズなどで、デジタル資料の受入と保存を担当する方。"
    "情報システムの専門家であることは前提としていません。",
    "",
    "【ご注意】",
    "・画面は日本語のみです",
    "・ウイルス定義データベースは同梱していません。アプリの設定画面から取得してください",
    "・PostScript / EPS の変換には、別途 Ghostscript が必要です",
    "",
    "開発: 中村 覚（東京大学）・金 甫榮（人間文化研究機構）",
]:
    mono(line if line else " ")
para("")
para("検索キーワード（7つまで）", bold=True)
mono("デジタルアーカイブ / 長期保存 / OAIS / アーカイブズ / メタデータ / BagIt / PREMIS")

# ---------------------------------------------------------------- 付録B
doc.add_page_break()
h("付録B　審査担当者へのメモ（貼り付け用）", 1)
para("Submission options の Notes for certification に、次の文章をそのまま貼ってください。")
para("")
para("この欄は上限が500文字です。超えた分は黙って切り捨てられます。以下は446文字なので"
     "そのまま収まります。書き足すときは必ず数えてください。", bold=True)
para("")
for line in [
    'デジタル資料の長期保存（OAIS）用の情報パッケージを作成する業務用ツールです。学術研究の成果物で、無料で提供します。',
    " ",
    'ClamAV 1.5.3 (GPL-2.0) を同梱していますが、ウイルス対策ソフトではありません。常駐監視やシステム変更は行わず、利用者が指定したフォルダを操作に応じて一度だけ検査します。定義DBは同梱せず、利用者が明示的に取得したときのみ database.clamav.net へ接続します。',
    " ",
    'Siegfried 1.11.6 (Apache-2.0) はファイル形式の判定用で、通信しません。',
    " ",
    'いずれも未改変の公式ビルドで、ライセンスは同梱の NOTICE に記載しています。',
    " ",
    'runFullTrust は、これら同梱実行ファイルを子プロセスとして起動するために宣言しています。',
    " ",
    '個人情報の収集・送信はありません。',
    'https://nakamura196.github.io/archival-packager/privacy-policy.html',
]:
    mono(line if line.strip() else " ")
para("")
para("残したのは、審査で引っかかりうる3点です。ClamAV がウイルス対策ソフトではないこと、"
     "runFullTrust を宣言している理由、個人情報を扱わないこと。番号付きの体裁や"
     "括弧内の例示は、判断に要らないので落としてあります。")

doc.save("/Users/nakamura/git/kim/archival-packager/store/ストア提出手順.docx")
print("書き出し: store/ストア提出手順.docx")
