#!/usr/bin/env python3
"""KG-30 金句卡文案落进 15 个后端文案库（一次性脚本，跑完即删）。

插入点：每个 locale 的 `share` 段里 `memory` 之后（金句卡与记忆卡是同族物料，读的人
能在同一屏里对到上一张卡）。键序固定，15 个文件同序——`test_copy_library.py` 比的是
键集，键序稳定才能让 14 份 diff 长得一样，review 时才看得出「真的只有新增」。

跑一次即停：任一文件已有 `share.quote` 就中止，不给「第二遍插一半」留机会。
既有键一个都不改。

`{date}` 只出现在 `source_memory` / `source_reflection` 两支：占位符集合由
`test_placeholders_match_en_across_all_locales` 逐语言比对，漏写就是运行时 KeyError，
多写就是键集对不上——所以脚本自己先按这个判据校一遍再落盘。
"""
import json
import re
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "backend" / "data" / "copy"
_PLACEHOLDER = re.compile(r"\{(\w+)\}")

LOCALES = ("zh", "en", "es", "ko", "ja", "ru", "id", "ms", "th", "de", "fr", "it", "pt", "vi", "km")

ORDER = ["title", "intro", "source_memory", "source_reflection", "own_words", "draft_note"]

COPY = {
    "en": {
        "title": "A Line You Wrote",
        "intro": "Something you wrote yourself:",
        "source_memory": "From a memory you saved · {date}",
        "source_reflection": "From a reflection you saved · {date}",
        "own_words": "You typed this sentence yourself — no AI wrote it for you.",
        "draft_note": "Saved from a reflection the AI drafted — the wording may not be entirely yours.",
    },
    "zh": {
        "title": "你自己写下的一句",
        "intro": "下面这句是你自己写的：",
        "source_memory": "来自你存下的一条记忆 · {date}",
        "source_reflection": "来自你存下的一条反思 · {date}",
        "own_words": "这句话是你自己敲下的，不是 AI 替你写的。",
        "draft_note": "这句出自 AI 起草、你按下保存的反思，措辞未必全是你自己的。",
    },
    "es": {
        "title": "Una frase tuya",
        "intro": "Algo que escribiste tú:",
        "source_memory": "De un recuerdo que guardaste · {date}",
        "source_reflection": "De una reflexión que guardaste · {date}",
        "own_words": "Tú escribiste esta frase; ninguna IA la escribió por ti.",
        "draft_note": "Guardada de una reflexión que redactó la IA; puede que no sea del todo tu redacción.",
    },
    "ko": {
        "title": "당신이 쓴 한 문장",
        "intro": "당신이 직접 쓴 문장입니다:",
        "source_memory": "당신이 저장한 기억에서 · {date}",
        "source_reflection": "당신이 저장한 회고에서 · {date}",
        "own_words": "이 문장은 당신이 직접 입력했습니다. AI가 대신 쓴 문장이 아닙니다.",
        "draft_note": "AI가 초안을 쓴 회고를 저장한 것입니다 — 표현이 전적으로 당신 것은 아닐 수 있습니다.",
    },
    "ja": {
        "title": "あなたが書いた一文",
        "intro": "あなた自身が書き残した一文：",
        "source_memory": "あなたが保存した記憶から · {date}",
        "source_reflection": "あなたが保存した振り返りから · {date}",
        "own_words": "この一文はあなた自身が打ち込んだもので、AI が代筆したものではありません。",
        "draft_note": "AI が下書きした振り返りを保存したもので、表現はあなた自身とは限りません。",
    },
    "ru": {
        "title": "Фраза, написанная тобой",
        "intro": "То, что ты написал сам:",
        "source_memory": "Из воспоминания, которое ты сохранил · {date}",
        "source_reflection": "Из рефлексии, которую ты сохранил · {date}",
        "own_words": "Эту фразу ты набрал сам — не ИИ писал её за тебя.",
        "draft_note": "Сохранено из рефлексии, которую набросал ИИ — формулировка может быть не совсем твоей.",
    },
    "id": {
        "title": "Satu Kalimat dari Anda",
        "intro": "Sesuatu yang kamu tulis sendiri:",
        "source_memory": "Dari kenangan yang kamu simpan · {date}",
        "source_reflection": "Dari refleksi yang kamu simpan · {date}",
        "own_words": "Kalimat ini kamu ketik sendiri — bukan AI yang menuliskannya untukmu.",
        "draft_note": "Disimpan dari refleksi yang disusun AI — pilihan katanya mungkin tidak seluruhnya dari kamu.",
    },
    "ms": {
        "title": "Satu Ayat daripada Anda",
        "intro": "Sesuatu yang anda tulis sendiri:",
        "source_memory": "Daripada kenangan yang anda simpan · {date}",
        "source_reflection": "Daripada refleksi yang anda simpan · {date}",
        "own_words": "Ayat ini anda taip sendiri — bukan AI yang menulisnya untuk anda.",
        "draft_note": "Disimpan daripada refleksi yang dirangka AI — susunan katanya mungkin bukan sepenuhnya milik anda.",
    },
    "th": {
        "title": "หนึ่งประโยคที่คุณเขียน",
        "intro": "สิ่งที่คุณเขียนขึ้นเอง:",
        "source_memory": "จากความทรงจำที่คุณบันทึกไว้ · {date}",
        "source_reflection": "จากการสะท้อนคิดที่คุณบันทึกไว้ · {date}",
        "own_words": "ประโยคนี้คุณพิมพ์เอง — ไม่ได้ให้ AI เขียนแทนคุณ",
        "draft_note": "บันทึกจากการสะท้อนคิดที่ AI ร่างให้ ถ้อยคำอาจไม่ใช่ของคุณทั้งหมด",
    },
    "de": {
        "title": "Ein Satz von dir",
        "intro": "Etwas, das du selbst geschrieben hast:",
        "source_memory": "Aus einer Erinnerung, die du gespeichert hast · {date}",
        "source_reflection": "Aus einer Reflexion, die du gespeichert hast · {date}",
        "own_words": "Diesen Satz hast du selbst getippt — keine KI hat ihn für dich geschrieben.",
        "draft_note": "Gespeichert aus einer von der KI entworfenen Reflexion — der Wortlaut stammt vielleicht nicht ganz von dir.",
    },
    "fr": {
        "title": "Une phrase de vous",
        "intro": "Quelque chose que vous avez écrit vous-même :",
        "source_memory": "Tirée d'un souvenir que vous avez enregistré · {date}",
        "source_reflection": "Tirée d'une réflexion que vous avez enregistrée · {date}",
        "own_words": "Vous avez tapé cette phrase vous-même — aucune IA ne l'a écrite à votre place.",
        "draft_note": "Enregistrée depuis une réflexion rédigée par l'IA — la formulation ne vient peut-être pas de vous.",
    },
    "it": {
        "title": "Una frase tua",
        "intro": "Qualcosa che hai scritto tu:",
        "source_memory": "Da un ricordo che hai salvato · {date}",
        "source_reflection": "Da una riflessione che hai salvato · {date}",
        "own_words": "Questa frase l'hai scritta tu — nessuna IA l'ha scritta al posto tuo.",
        "draft_note": "Salvata da una riflessione abbozzata dall'IA; la formulazione potrebbe non essere tutta tua.",
    },
    "pt": {
        "title": "Uma frase sua",
        "intro": "Algo que você mesmo escreveu:",
        "source_memory": "De uma lembrança que você salvou · {date}",
        "source_reflection": "De uma reflexão que você salvou · {date}",
        "own_words": "Você digitou esta frase — nenhuma IA a escreveu por você.",
        "draft_note": "Salva de uma reflexão que a IA rascunhou; a redação pode não ser totalmente sua.",
    },
    "vi": {
        "title": "Một câu bạn tự viết",
        "intro": "Một điều chính bạn đã viết:",
        "source_memory": "Từ một ký ức bạn đã lưu · {date}",
        "source_reflection": "Từ một bản suy ngẫm bạn đã lưu · {date}",
        "own_words": "Câu này do chính bạn gõ — không phải AI viết thay bạn.",
        "draft_note": "Được lưu từ bản suy ngẫm do AI phác thảo — câu chữ có thể không hoàn toàn là của bạn.",
    },
    "km": {
        "title": "ប្រយោកមួយដែលអ្នកបានសរសេរ",
        "intro": "អ្វីមួយដែលអ្នកបានសរសេរដោយខ្លួនឯង៖",
        "source_memory": "ពីការចងចាំដែលអ្នកបានរក្សាទុក · {date}",
        "source_reflection": "ពីការឆ្លុះបញ្ចាំងដែលអ្នកបានរក្សាទុក · {date}",
        "own_words": "ប្រយោកនេះអ្នកវាយដោយខ្លួនឯង — មិនមែន AI សរសេរជំនួសអ្នកទេ។",
        "draft_note": "បានរក្សាទុកពីការឆ្លុះបញ្ចាំងដែល AI រាងឡើង — ពាក្យសំដីអាចមិនមែនជារបស់អ្នកទាំងស្រុងទេ។",
    },
}


def main() -> None:
    assert set(COPY) == set(LOCALES), f"语言清单对不上：{set(COPY) ^ set(LOCALES)}"
    en = COPY["en"]
    for loc in LOCALES:
        block = COPY[loc]
        assert list(block) == ORDER, f"{loc} 键序/键集与 ORDER 不一致"
        for key in ORDER:
            value = block[key]
            assert value, f"{loc}.{key} 是空串"
            ph = set(_PLACEHOLDER.findall(value))
            en_ph = set(_PLACEHOLDER.findall(en[key]))
            assert ph == en_ph, f"{loc}.{key} 占位符 {ph} ≠ en {en_ph}"
            if loc != "en":
                assert value != en[key], f"{loc}.{key} 逐字等于英文"

    for loc in LOCALES:
        path = DATA_DIR / f"{loc}.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        share = doc["share"]
        assert "quote" not in share, f"{loc} 已有 share.quote，脚本只许跑一次"
        assert "memory" in share, f"{loc} 的 share 段没有 memory 锚点"
        # 保序插入：memory 之后放 quote，其余键原样
        merged: dict = {}
        for k, v in share.items():
            merged[k] = v
            if k == "memory":
                merged["quote"] = dict(COPY[loc])
        doc["share"] = merged
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{loc}: share.quote +{len(ORDER)} 键")


if __name__ == "__main__":
    main()
