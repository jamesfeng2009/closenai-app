#!/usr/bin/env python3
"""KG-28 的第 30 个键 `quotaDone`（一次性脚本，跑完即删）。

为什么单开一个键而不是借 `reflect.quotaMessage`：那句话说的是「今天的免费**消息**用完了」，
而情绪分析走的是**第三个独立池**（`quota_service._mood_analysis_limit`，免费档 1 次/天）。
借来的文案会把用户指向错误的那一格额度——他会以为少了一条主动消息，而真正被扣的是分析次数。

插入点：`moodAnalysis.remaining` 那一行之后（同一段、相邻两格，读数与耗尽是同一件事的两面）。
锚点在每个文件里恰好出现一次（脚本先数一遍），既有键一个都不改；已有 `quotaDone` 就中止。
"""
from pathlib import Path

LOCALES = Path(__file__).resolve().parent.parent / "frontend" / "src" / "i18n" / "locales"

VALUES = {
    "en": "No analyses left for today — come back tomorrow.",
    "zh": "今天的分析次数用完了，明天再来。",
    "de": "Heute sind keine Analysen mehr übrig — komm morgen wieder.",
    "es": "Ya no te quedan análisis por hoy — vuelve mañana.",
    "fr": "Plus d'analyse possible aujourd'hui — revenez demain.",
    "id": "Jatah analisis hari ini sudah habis — kembali lagi besok.",
    "it": "Le analisi di oggi sono terminate — riprova domani.",
    "ja": "今日使える分析の回数をすべて使いました。明日またお越しください。",
    "km": "ចំនួនការវិភាគសម្រាប់ថ្ងៃនេះអស់ហើយ — សូមត្រឡប់មកវិញស្អែក។",
    "ko": "오늘 사용할 수 있는 분석 횟수를 모두 썼습니다. 내일 다시 오세요.",
    "ms": "Baki analisis hari ini telah habis — datang semula esok.",
    "pt": "As análises de hoje acabaram — volte amanhã.",
    "ru": "Разборы на сегодня закончились — возвращайся завтра.",
    "th": "โควตาวิเคราะห์วันนี้หมดแล้ว กลับมาใหม่อีกทีพรุ่งนี้",
    "vi": "Số lần phân tích hôm nay đã hết — hãy quay lại ngày mai.",
}


def main() -> None:
    on_disk = {p.stem for p in LOCALES.glob("*.ts") if p.stem != "index"}
    assert on_disk == set(VALUES), f"语言清单对不上：{on_disk ^ set(VALUES)}"
    for lang, value in VALUES.items():
        assert '"' not in value and "\\" not in value, f"{lang} 的值会破 TS 字符串"
        assert lang == "en" or value != VALUES["en"], f"{lang} 逐字等于英文"
        path = LOCALES / f"{lang}.ts"
        text = path.read_text(encoding="utf-8")
        assert '"quotaDone":' not in text, f"{lang} 已有 quotaDone，脚本只许跑一次"
        anchor = '    "remaining": '
        hits = [i for i, line in enumerate(text.split("\n")) if line.startswith(anchor)]
        assert len(hits) == 1, f"{lang} 的 remaining 锚点出现 {len(hits)} 次"
        lines = text.split("\n")
        lines[hits[0] + 1 : hits[0] + 1] = [f'    "quotaDone": "{value}",']
        path.write_text("\n".join(lines), encoding="utf-8")
        print(f"{lang}: moodAnalysis.quotaDone +1 键")


if __name__ == "__main__":
    main()
