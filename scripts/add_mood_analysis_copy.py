#!/usr/bin/env python3
"""KG-28 三层次分析的降级文案落进 15 个后端文案库（一次性脚本，跑完即删）。

插入点：每个 locale 的 `mood` 段末尾（`trend` 之后）加一个 `analysis` 子对象。
键序固定，15 个文件同序——`test_copy_library.py` 比的是键集与占位符集合，
键序稳定才能让 14 份 diff 长得一样，review 时看得出「真的只有新增」。

为什么这三句必须在文案库里而不是写在代码里：降级出口是唯一一句
**在没有 AI 产出时也要说给用户听**的话，15 种界面语言里漏一种中文，
就等于在非中文界面上演一次「服务器自己讲普通话」。

人称口径跟随各语言 mood 段既有文案（fr/de/es/it 用非正式第二人称，
pt 用 você，id/ms/vi/km 按既有写法），不跟 errors 段的正式口径。

跑一次即停：任一文件已有 `mood.analysis` 就中止，不给「第二遍插一半」留机会。
既有键一个都不改。
"""
import json
import re
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "backend" / "data" / "copy"
_PLACEHOLDER = re.compile(r"\{(\w+)\}")

LOCALES = ("zh", "en", "es", "ko", "ja", "ru", "id", "ms", "th", "de", "fr", "it", "pt", "vi", "km")

ORDER = ["facts", "degraded_summary", "crisis_next_step"]

COPY = {
    "en": {
        "facts": "Today you checked in as {label}. Lines in your own words: {count}.",
        "degraded_summary": "{facts} I don't have enough of your own words to name a pattern yet.",
        "crisis_next_step": "Please talk to someone you trust now, or call the number above.",
    },
    "zh": {
        "facts": "今天你的签到是「{label}」。你自己写下的句子：{count} 条。",
        "degraded_summary": "{facts}我还没有足够多你自己的原话，能指认出一个模式。",
        "crisis_next_step": "请现在找你信任的人聊聊，或拨打上面的号码。",
    },
    "es": {
        "facts": "Hoy te registraste como {label}. Frases con tus propias palabras: {count}.",
        "degraded_summary": "{facts} Aún no tengo suficientes frases tuyas para señalar un patrón.",
        "crisis_next_step": "Habla ahora con alguien de tu confianza o llama al número de arriba.",
    },
    "ko": {
        "facts": "오늘의 체크인: {label}. 직접 쓴 문장: {count}개.",
        "degraded_summary": "{facts}당신 자신의 말로 된 문장이 아직 충분해서, 패턴을 짚지 못하겠습니다.",
        "crisis_next_step": "지금 신뢰하는 사람과 이야기하거나 위의 번호로 전화해 주세요.",
    },
    "ja": {
        "facts": "今日のチェックイン: {label}。ご自身が書いた文: {count} 件。",
        "degraded_summary": "{facts}あなた自身の言葉がまだ足りなくて、パターンをあげることはできません。",
        "crisis_next_step": "今すぐ信頼できる人に相談するか、上の番号に電話してください。",
    },
    "ru": {
        "facts": "Сегодняшняя отметка настроения: {label}. Фраз, написанных тобой: {count}.",
        "degraded_summary": "{facts} Мне не хватает твоих собственных слов, чтобы указать на закономерность.",
        "crisis_next_step": "Поговори сейчас с тем, кому ты доверяешь, или позвони по номеру выше.",
    },
    "id": {
        "facts": "Hari ini kamu mencatat suasana sebagai {label}. Kalimat tulisanmu sendiri: {count}.",
        "degraded_summary": "{facts} Aku belum punya cukup kalimat dari kata-katamu sendiri untuk menyebut sebuah pola.",
        "crisis_next_step": "Bicaralah sekarang dengan orang yang kamu percaya, atau hubungi nomor di atas.",
    },
    "ms": {
        "facts": "Hari ini anda merekod mood sebagai {label}. Ayat tulisan anda sendiri: {count}.",
        "degraded_summary": "{facts} Belum cukup ayat daripada kata-kata anda sendiri untuk menunjukkan satu corak.",
        "crisis_next_step": "Berbicaralah sekarang dengan orang yang anda percaya, atau hubungi nombor di atas.",
    },
    "th": {
        "facts": "วันนี้คุณบันทึกอารมณ์เป็น{label} ประโยคที่คุณเขียนเอง: {count} ประโยค",
        "degraded_summary": "{facts}ยังไม่มีคำพูดของคุณเองมากพอที่จะชี้ว่าเป็นรูปแบบใด",
        "crisis_next_step": "ขอใหคุยกับคนที่คุณไว้วางใจเดี๋ยวนี้ หรือโทรตามหมายเลขข้างบน",
    },
    "de": {
        "facts": "Heute hast du {label} eingetragen. Sätze in deinen eigenen Worten: {count}.",
        "degraded_summary": "{facts} Mir fehlen noch zu viele eigene Worte von dir, um ein Muster zu benennen.",
        "crisis_next_step": "Sprich jetzt mit jemandem, dem du vertraust, oder ruf die Nummer oben an.",
    },
    "fr": {
        "facts": "Aujourd'hui, ton humeur notée : {label}. Phrases écrites avec tes mots : {count}.",
        "degraded_summary": "{facts} Il me manque encore tes propres mots pour désigner un schéma.",
        "crisis_next_step": "Parle dès maintenant à quelqu'un en qui tu as confiance, ou appelle le numéro ci-dessus.",
    },
    "it": {
        "facts": "Oggi hai segnato {label}. Frasi con parole tue: {count}.",
        "degraded_summary": "{facts} Mi mancano ancora parole tue per indicare uno schema.",
        "crisis_next_step": "Parla adesso con qualcuno di cui ti fidi, o chiama il numero qui sopra.",
    },
    "pt": {
        "facts": "Hoje você registrou {label}. Frases com as suas palavras: {count}.",
        "degraded_summary": "{facts} Ainda faltam palavras suas para eu apontar um padrão.",
        "crisis_next_step": "Fale agora com alguém de sua confiança ou ligue para o número acima.",
    },
    "vi": {
        "facts": "Hôm nay bạn ghi nhận là {label}. Câu do chính bạn viết: {count}.",
        "degraded_summary": "{facts} Mình chưa có đủ câu chữ của chính bạn để chỉ ra một khuôn mẫu.",
        "crisis_next_step": "Hãy nói chuyện với người bạn tin tưởng ngay bây giờ, hoặc gọi số ở trên.",
    },
    "km": {
        "facts": "ថ្ងៃនេះអ្នកបានបញ្ជាក់អារម្មណ៍ជា {label}។ ប្រយោកដែលអ្នកសរសេរដោយខ្លួនឯង៖ {count}។",
        "degraded_summary": "{facts}ខ្ញុំមិនទាន់មានប្រយោកជាពាក្យរបស់អ្នកគ្រប់គ្រាន់ ដើម្បីច្បាស់ជាទម្លាប់ទេ។",
        "crisis_next_step": "សូមនិយាយជាមួយអ្នកដែលអ្នកទុកចិត្តឥឡូវនេះ ឬទូរស័ព្ទទៅលេខខាងលើ។",
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
        mood = doc["mood"]
        assert "analysis" not in mood, f"{loc} 已有 mood.analysis，脚本只许跑一次"
        assert "trend" in mood, f"{loc} 的 mood 段没有 trend 锚点"
        merged: dict = {}
        for k, v in mood.items():
            merged[k] = v
            if k == "trend":
                merged["analysis"] = dict(COPY[loc])
        doc["mood"] = merged
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{loc}: mood.analysis +{len(ORDER)} 键")


if __name__ == "__main__":
    main()
