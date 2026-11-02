#!/usr/bin/env python3
"""KG-30 金句卡的第 4 个失败码文案（一次性脚本，跑完即删）。

插入点：每个 locale 的 `pdf.failed` 段里那行 `"memoryGone": …` 之后——新码走的是
「同一段落里的第四个分支」，不是另起一个 namespace：`pdfFailureKey` 的返回键全在
`pdf.failed.*` 下面，另起一处等于再造第二个真源。

既有键一个都不改；任一文件已有 `notOriginal` 就中止。
"""
from pathlib import Path

LOCALES = Path(__file__).resolve().parent.parent / "frontend" / "src" / "i18n" / "locales"

VALUES = {
    "en": "This line was put together by AI from your chats, so it doesn't go on a card that says you wrote it.",
    "zh": "这句是 AI 从对话里整理出来的，不是你自己写下的原话，所以不做成金句卡。",
    "de": "Diesen Satz hat die KI aus deinen Chats zusammengestellt — er kommt nicht auf eine Karte, die von dir stammt.",
    "es": "Esta frase la armó la IA a partir de tus chats, así que no va en una tarjeta que diga que la escribiste tú.",
    "fr": "Cette phrase a été rassemblée par l'IA depuis vos échanges ; elle ne va pas sur une carte qui dirait que vous l'avez écrite.",
    "id": "Kalimat ini disusun AI dari obrolanmu, jadi tidak dijadikan kartu yang menyebut kamu yang penulisannya.",
    "ms": "Ayat ini disusun oleh AI daripada perbualan anda, jadi ia tidak dijadikan kad yang mengatakan anda yang menulisnya.",
    "it": "Questa frase l'ha messa insieme l'IA dalle tue chat, quindi non finisce su una carta che dice che l'hai scritta tu.",
    "ja": "この文は AI があなたの会話からまとめ出したものなので、「あなたが書いた」とするカードにはしません。",
    "ko": "이 문장은 AI 가 당신의 대화에서 모아 만든 것이어서, ‘당신이 쓴 말’이라고 적힌 카드에는 실리지 않습니다.",
    "ru": "Эту фразу ИИ собрал из твоих разговоров, поэтому на карту, где сказано, что она твоя, она не пойдёт.",
    "th": "ประโยคนี้ AI จัดขึ้นจากบทสนทนาของคุณ จึงไม่ทำเป็นการ์ดที่บอกว่าเขียนขึ้นเอง",
    "vi": "Câu này do AI ghép lại từ các đoạn trò chuyện của bạn, nên không đưa vào thẻ nói rằng chính bạn đã viết.",
    "pt": "Esta frase foi montada pela IA a partir das suas conversas, por isso não vai para um cartão que diga que foi você quem a escreveu.",
    "km": "ប្រយោកនេះ AI បានរៀបចំពីការសន្ទនារបស់អ្នក ដូច្នេះវាមិនត្រូវបានដាក់លើកាតដែលចែងថាអ្នកជាអ្នកសរសេរឡើយ។",
}


def main() -> None:
    on_disk = {p.stem for p in LOCALES.glob("*.ts") if p.stem != "index"}
    assert on_disk == set(VALUES), f"语言清单对不上：{on_disk ^ set(VALUES)}"
    for lang, value in VALUES.items():
        assert '"' not in value, f"{lang} 的值里有裸双引号，会破 TS 字符串"
        assert value != VALUES["en"] or lang == "en", f"{lang} 逐字等于英文"
        path = LOCALES / f"{lang}.ts"
        text = path.read_text(encoding="utf-8")
        assert '"notOriginal":' not in text, f"{lang} 已有 notOriginal，脚本只许跑一次"
        anchor = '      "memoryGone": '
        hits = [i for i, ln in enumerate(text.split("\n")) if ln.startswith(anchor)]
        assert len(hits) == 1, f"{lang} 的 memoryGone 锚点出现 {len(hits)} 次"
        lines = text.split("\n")
        lines[hits[0] + 1:hits[0] + 1] = [f'      "notOriginal": "{value}",']
        path.write_text("\n".join(lines), encoding="utf-8")
        print(f"{lang}: pdf.failed.notOriginal +1 键")


if __name__ == "__main__":
    main()
