#!/usr/bin/env python3
"""KG-30 金句卡前端文案落进 15 个 locale（一次性脚本，跑完即删）。

插入点：每个 locale 的 `share` 段里那行 `"memory": "…"` 变体标签之后（金句卡与回忆卡
是同族物料）。既有键一个都不改，只追加 6 个；`share` 段里出现第二次 `"memory":` 锚点
就中止。

跑一次即停：任一文件已有 `quote:` 键就中止。
"""
from pathlib import Path

LOCALES = Path(__file__).resolve().parent.parent / "frontend" / "src" / "i18n" / "locales"

ORDER = [
    "quote", "quoteEntry", "quoteEmpty", "quoteIntro",
    "quoteManifestQuote", "quoteNote",
]

COPY: dict[str, dict[str, str]] = {
    "en": {
        "quote": "Quote",
        "quoteEntry": "Share these words",
        "quoteEmpty": "There is nothing on this card — the line you came from is empty, or you opened it without picking a record.",
        "quoteIntro": "A line you saved yourself",
        "quoteManifestQuote": "The line exactly as you wrote it",
        "quoteNote": "You choose when this leaves the app: nothing is shared until you tap export. The card carries your referral link, and a card already saved on your device can't be taken back.",
    },
    "zh": {
        "quote": "金句",
        "quoteEntry": "分享这句话",
        "quoteEmpty": "这张卡上没有句子——你点进来的那条是空的，或者你没选具体记录就打开了它。",
        "quoteIntro": "你自己存下的一句",
        "quoteManifestQuote": "原样的那句你写过的话",
        "quoteNote": "在你按下导出之前，什么都不会被分享。卡上会带上你的推荐链接；已经存进设备的卡收不回来。",
    },
    "de": {
        "quote": "Zitat",
        "quoteEntry": "Diesen Satz teilen",
        "quoteEmpty": "Auf dieser Karte ist kein Satz — der Eintrag, aus dem du kommst, ist leer, oder du hast die Karte ohne Eintrag geöffnet.",
        "quoteIntro": "Ein Satz, den du selbst gespeichert hast",
        "quoteManifestQuote": "Der Satz genau so, wie du ihn geschrieben hast",
        "quoteNote": "Geteilt wird erst, wenn du exportierst — vorher verlässt nichts die App. Die Karte enthält deinen Empfehlungslink, und eine Karte, die schon auf deinem Gerät liegt, kannst du nicht zurückholen.",
    },
    "es": {
        "quote": "Frase",
        "quoteEntry": "Compartir esta frase",
        "quoteEmpty": "Esta tarjeta no tiene frase — el registro del que vienes está vacío, o la abriste sin elegir un registro.",
        "quoteIntro": "Una frase que guardaste tú",
        "quoteManifestQuote": "La frase tal y como la escribiste",
        "quoteNote": "Nada se comparte hasta que pulsas exportar. La tarjeta lleva tu enlace de recomendación, y una tarjeta ya guardada en tu dispositivo no se puede retirar.",
    },
    "fr": {
        "quote": "Citation",
        "quoteEntry": "Partager cette phrase",
        "quoteEmpty": "Aucune phrase sur cette carte — l'enregistrement d'origine est vide, ou vous l'avez ouverte sans choisir d'enregistrement.",
        "quoteIntro": "Une phrase que vous avez enregistrée vous-même",
        "quoteManifestQuote": "La phrase exactement telle que vous l'avez écrite",
        "quoteNote": "Rien n'est partagé tant que vous n'appuyez pas sur exporter. La carte porte votre lien de parrainage, et une carte déjà enregistrée sur votre appareil ne peut pas être reprise.",
    },
    "id": {
        "quote": "Kutipan",
        "quoteEntry": "Bagikan kalimat ini",
        "quoteEmpty": "Tidak ada kalimat di kartu ini — catatan asalmu kosong, atau kamu membukanya tanpa memilih catatan.",
        "quoteIntro": "Satu kalimat yang kamu simpan sendiri",
        "quoteManifestQuote": "Kalimatnya persis seperti yang kamu tulis",
        "quoteNote": "Belum ada yang dibagikan sampai kamu menekan ekspor. Kartu ini membawa tautan referral kamu, dan kartu yang sudah tersimpan di perangkatmu tidak bisa ditarik kembali.",
    },
    "ms": {
        "quote": "Petikan",
        "quoteEntry": "Kongsi ayat ini",
        "quoteEmpty": "Tiada ayat pada kad ini — rekod asal anda kosong, atau anda membukanya tanpa memilih rekod.",
        "quoteIntro": "Satu ayat yang anda simpan sendiri",
        "quoteManifestQuote": "Ayatnya tepat seperti yang anda tulis",
        "quoteNote": "Tiada apa-apa dikongsi sehingga anda menekan eksport. Kad ini membawa pautan rujukan anda, dan kad yang sudah disimpan pada peranti anda tidak boleh ditarik balik.",
    },
    "it": {
        "quote": "Citazione",
        "quoteEntry": "Condividi questa frase",
        "quoteEmpty": "Su questa carta non c'è nessuna frase — la voce di origine è vuota, oppure l'hai aperta senza scegliere una voce.",
        "quoteIntro": "Una frase che hai salvato tu",
        "quoteManifestQuote": "La frase esattamente come l'hai scritta",
        "quoteNote": "Nulla viene condiviso finché non premi esporta. La carta porta il tuo link di invito e una carta già salvata sul dispositivo non si può ritirare.",
    },
    "ja": {
        "quote": "引用",
        "quoteEntry": "この一言を共有",
        "quoteEmpty": "このカードには文がありません。元になった記録が空か、記録を選ばずに開きました。",
        "quoteIntro": "あなた自身が保存した一文",
        "quoteManifestQuote": "あなたが書いたままの一文",
        "quoteNote": "書き出しを押すまで、何も共有されません。カードにはあなたの紹介リンクが入り、端末に保存済みのカードは取り消せません。",
    },
    "ko": {
        "quote": "인용",
        "quoteEntry": "이 한마디 공유",
        "quoteEmpty": "이 카드에는 문장이 없습니다 — 나온 기록이 비어 있거나, 기록을 고르지 않고 열었습니다.",
        "quoteIntro": "당신이 직접 저장한 한마디",
        "quoteManifestQuote": "당신이 쓴 그대로의 문장",
        "quoteNote": "내보내기를 누르기 전에는 아무것도 공유되지 않습니다. 카드에는 당신의 추천 링크가 들어가며, 기기에 이미 저장된 카드는 회수할 수 없습니다.",
    },
    "ru": {
        "quote": "Цитата",
        "quoteEntry": "Поделиться этой фразой",
        "quoteEmpty": "На этой карте нет фразы — исходная запись пуста, или ты открыл карту, не выбрав запись.",
        "quoteIntro": "Фраза, которую ты сохранил сам",
        "quoteManifestQuote": "Фраза ровно в том виде, как ты её написал",
        "quoteNote": "Пока не нажмёшь «экспортировать», ничего не публикуется. На карте будет твоя реферальная ссылка, и карту, уже сохранённую на устройстве, не вернуть.",
    },
    "th": {
        "quote": "คำคม",
        "quoteEntry": "แบ่งปันประโยคนี้",
        "quoteEmpty": "บนการ์ดใบนี้ไม่มีประโยค — รายการที่คุณเปิดมาว่าเปล่า หรือคุณเปิดการ์ดโดยไม่เลือกรายการ",
        "quoteIntro": "ประโยคที่คุณบันทึกไว้เอง",
        "quoteManifestQuote": "ประโยคเดิมตามที่คุณเขียน",
        "quoteNote": "จะยังไม่มีการแบ่งปันจนกว่าคุณกดส่งออก การ์ดใบนี้มีลิงก์แนะนำของคุณแนบไปด้วย และการ์ดที่บันทึกไว้ในเครื่องแล้วไม่สามารถดึงคืนได้",
    },
    "vi": {
        "quote": "Trích dẫn",
        "quoteEntry": "Chia sẻ câu này",
        "quoteEmpty": "Thẻ này không có câu nào — bản ghi bạn mở tới đang trống, hoặc bạn mở thẻ mà chưa chọn bản ghi.",
        "quoteIntro": "Một câu chính bạn đã lưu",
        "quoteManifestQuote": "Câu chữ đúng như bạn đã viết",
        "quoteNote": "Chưa gì được chia sẻ cho tới khi bạn bấm xuất. Thẻ kèm theo liên kết giới thiệu của bạn, và thẻ đã lưu trên máy thì không thu hồi lại được.",
    },
    "pt": {
        "quote": "Citação",
        "quoteEntry": "Compartilhar esta frase",
        "quoteEmpty": "Não há frase neste cartão — o registro de origem está vazio, ou você abriu o cartão sem escolher um registro.",
        "quoteIntro": "Uma frase que você mesmo salvou",
        "quoteManifestQuote": "A frase exatamente como você a escreveu",
        "quoteNote": "Nada é compartilhado até você tocar em exportar. O cartão traz o seu link de convite, e um cartão já salvo no seu dispositivo não pode ser retirado de volta.",
    },
    "km": {
        "quote": "ការដកស្រង់",
        "quoteEntry": "ចែករំលែកប្រយោកនេះ",
        "quoteEmpty": "គ្មានប្រយោកលើកាតនេះ ទាំងកត់ត្រាដើមទទេ ឬអ្នកបានបើកកាតដោយមិនជ្រើសរើសកត់ត្រា។",
        "quoteIntro": "ប្រយោកមួយដែលអ្នកបានរក្សាទុកដោយខ្លួនឯង",
        "quoteManifestQuote": "ប្រយោកតាមបែបដែលអ្នកបានសរសេរ",
        "quoteNote": "មិនមានអ្វីត្រូវបានចែករំលែកទេ លុះត្រាតែអ្នកចុចដើម្បីបញ្ចេញ។ កាតនេះបញ្ចូលទាំងតំណអញ្ជើញរបស់អ្នក ហើយកាតដែលបានរក្សាទុកលើឧបករណ៍ហើយ មិនអាចទាញត្រឡប់វិញបានទេ។",
    },
}


def share_span(text: str) -> tuple[int, int]:
    """返回 share 段（含首行）在全文中的 [start, end) 区间。"""
    start = text.index('  "share": {\n')
    end = text.index('\n  },', start)
    return start, end


def main() -> None:
    # 证人：15 个 locale 文件必须全在 COPY 里。上一版漏了 pt，脚本照跑不误、14 个语言绿
    # 铃铃落地，缺的那个语言只在 i18nLocales 键集闸才红——先在脚本里点名，别把漏交给下游。
    on_disk = {p.stem for p in LOCALES.glob("*.ts") if p.stem != "index"}
    assert on_disk == set(COPY), f"语言清单对不上：仅磁盘={sorted(on_disk - set(COPY))} 仅脚本={sorted(set(COPY) - on_disk)}"
    for lang, copy in COPY.items():
        assert set(copy) == set(ORDER), f"{lang} 键集与 ORDER 不一致：{set(copy) ^ set(ORDER)}"
        path = LOCALES / f"{lang}.ts"
        text = path.read_text(encoding="utf-8")

        for key in ORDER:
            assert f'    "{key}":' not in text, f"{lang} 已有 {key}，脚本只许跑一次"

        for value in copy.values():
            assert '"' not in value, f"{lang} 的值里有裸双引号，会破 TS 字符串：{value[:40]}"

        start, end = share_span(text)
        lines = text[start:end].split("\n")
        hits = [i for i, ln in enumerate(lines) if ln.startswith('    "memory": ')]
        assert len(hits) == 1, f"{lang} share 段里 memory 锚点出现 {len(hits)} 次"

        inserted = [f'    "{key}": "{copy[key]}",' for key in ORDER]
        lines[hits[0] + 1:hits[0] + 1] = inserted
        path.write_text(text[:start] + "\n".join(lines) + text[end:], encoding="utf-8")
        print(f"{lang}: share +{len(ORDER)} 键")


if __name__ == "__main__":
    main()
