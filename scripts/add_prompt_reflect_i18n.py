#!/usr/bin/env python3
"""KG-26 反思提示库文案落进 15 个 locale（一次性脚本，跑完即删）。

插入点：每个 locale 的 `"reflect"` 段里那唯一一行 `"saveDraft": ...` 之后。
脚本先定位 reflect 段的边界，再只在段内找锚点，因此不会误插到别的段。
既有键一个都不改，只追加 13 个新键；同一段里出现第二次 `saveDraft` 就中止。

语气按所在块走：de / fr 的 errors 块是敬称，reflect 块是非正式的 du / 您→tu 混用
按各自 reflect 段既有行文（de 用 du），所以这里用非正式。
"""
from pathlib import Path

LOCALES = Path(__file__).resolve().parent.parent / "frontend" / "src" / "i18n" / "locales"

COPY: dict[str, dict[str, str]] = {
    "en": {
        "promptEntry": "Need a prompt?",
        "promptSheetTitle": "Reflection prompts",
        "promptSheetSubtitle": "Pick one, rewrite it in your own words, then save it as a reflection.",
        "promptClusterAll": "All",
        "promptUse": "Use this",
        "promptSwap": "Another one",
        "promptSave": "Save as reflection",
        "promptSaved": "Saved — this entry keeps a reference to the prompt you used.",
        "promptStale": "The prompt library has been updated. Please pick one again.",
        "promptBlank": "Write something first — a blank entry would just add an empty row to your timeline.",
        "promptEmpty": "No prompts available right now.",
        "promptLoadFailed": "Couldn't load the prompts.",
        "promptUntranslated": "Shown in English — this library currently has English and Chinese versions only.",
    },
    "zh": {
        "promptEntry": "需要提示？",
        "promptSheetTitle": "反思提示",
        "promptSheetSubtitle": "挑一条，用你自己的话改写，再存成一条反思。",
        "promptClusterAll": "全部",
        "promptUse": "用这条",
        "promptSwap": "换一条",
        "promptSave": "存为反思",
        "promptSaved": "已存下——这条记录会带上你用的那条提示。",
        "promptStale": "提示库已更新，请重新选一条。",
        "promptBlank": "先写点什么——空内容存进去，时间线上只会多一行空白。",
        "promptEmpty": "暂时没有可用的提示。",
        "promptLoadFailed": "提示没能加载。",
        "promptUntranslated": "当前显示英文：这个库暂时只有中英两版。",
    },
    "de": {
        "promptEntry": "Braucht du eine Vorlage?",
        "promptSheetTitle": "Reflexionsvorlagen",
        "promptSheetSubtitle": "Wähl eine, schreib sie in deinen Worten um und speicher sie als Reflexion.",
        "promptClusterAll": "Alle",
        "promptUse": "Diese nehmen",
        "promptSwap": "Nächste",
        "promptSave": "Als Reflexion speichern",
        "promptSaved": "Gespeichert — der Eintrag behält den Verweis auf die verwendete Vorlage.",
        "promptStale": "Die Sammlung der Vorlagen wurde aktualisiert. Bitte wähle eine neue.",
        "promptBlank": "Schreib erst etwas — ein leerer Eintrag wäre nur eine Zeile ohne Inhalt in deinem Verlauf.",
        "promptEmpty": "Derzeit sind keine Vorlagen verfügbar.",
        "promptLoadFailed": "Vorlagen konnten nicht geladen werden.",
        "promptUntranslated": "Auf Englisch angezeigt — diese Sammlung gibt es bisher nur auf Englisch und Chinesisch.",
    },
    "es": {
        "promptEntry": "¿Necesitas una guía?",
        "promptSheetTitle": "Guías de reflexión",
        "promptSheetSubtitle": "Elige una, reescríbela con tus palabras y guárdala como reflexión.",
        "promptClusterAll": "Todas",
        "promptUse": "Usar esta",
        "promptSwap": "Otra",
        "promptSave": "Guardar como reflexión",
        "promptSaved": "Guardada — el registro conserva la referencia a la guía que usaste.",
        "promptStale": "La biblioteca de guías se actualizó. Elige otra vez.",
        "promptBlank": "Escribe algo primero — un registro vacío solo añadiría una línea sin contenido a tu historial.",
        "promptEmpty": "Ahora mismo no hay guías disponibles.",
        "promptLoadFailed": "No se pudieron cargar las guías.",
        "promptUntranslated": "Se muestra en inglés — esta biblioteca solo tiene versiones en inglés y chino.",
    },
    "fr": {
        "promptEntry": "Besoin d'un repère ?",
        "promptSheetTitle": "Repères de réflexion",
        "promptSheetSubtitle": "Choisissez-en un, reformulez-le avec vos mots, puis enregistrez-le comme réflexion.",
        "promptClusterAll": "Tous",
        "promptUse": "Utiliser",
        "promptSwap": "Un autre",
        "promptSave": "Enregistrer comme réflexion",
        "promptSaved": "Enregistré — l'entrée garde la référence du repère utilisé.",
        "promptStale": "La bibliothèque de repères a été mise à jour. Veuillez en choisir un autre.",
        "promptBlank": "Écrivez d'abord quelque chose — une entrée vide ajouterait une ligne sans contenu à votre historique.",
        "promptEmpty": "Aucun repère disponible pour l'instant.",
        "promptLoadFailed": "Impossible de charger les repères.",
        "promptUntranslated": "Affiché en anglais — cette bibliothèque n'existe qu'en anglais et en chinois.",
    },
    "id": {
        "promptEntry": "Butuh pancingan?",
        "promptSheetTitle": "Pancingan refleksi",
        "promptSheetSubtitle": "Pilih satu, tulis ulang dengan kata-katamu sendiri, lalu simpan sebagai refleksi.",
        "promptClusterAll": "Semua",
        "promptUse": "Pakai ini",
        "promptSwap": "Lainnya",
        "promptSave": "Simpan sebagai refleksi",
        "promptSaved": "Tersimpan — catatan ini menyimpan rujukan pancingan yang kamu pakai.",
        "promptStale": "Kumpulan pancingan sudah diperbarui. Silakan pilih lagi.",
        "promptBlank": "Tulis sesuatu dulu — entri kosong hanya menambah baris tanpa isi di linimasamu.",
        "promptEmpty": "Belum ada pancingan yang tersedia.",
        "promptLoadFailed": "Pancingan gagal dimuat.",
        "promptUntranslated": "Ditampilkan dalam bahasa Inggris — kumpulan ini baru ada versi Inggris dan Mandarin.",
    },
    "it": {
        "promptEntry": "Ti serve uno spunto?",
        "promptSheetTitle": "Spunti di riflessione",
        "promptSheetSubtitle": "Scegline uno, riscrivilo con parole tue e salvalo come riflessione.",
        "promptClusterAll": "Tutti",
        "promptUse": "Usa questo",
        "promptSwap": "Un altro",
        "promptSave": "Salva come riflessione",
        "promptSaved": "Salvato — la voce conserva il riferimento allo spunto usato.",
        "promptStale": "La raccolta di spunti è stata aggiornata. Scegline di nuovo uno.",
        "promptBlank": "Scrivi prima qualcosa — una voce vuota aggiungerebbe solo una riga senza contenuto alla cronologia.",
        "promptEmpty": "Nessuno spunto disponibile in questo momento.",
        "promptLoadFailed": "Impossibile caricare gli spunti.",
        "promptUntranslated": "In inglese — questa raccolta esiste solo in inglese e cinese.",
    },
    "ja": {
        "promptEntry": "ヒントが必要ですか？",
        "promptSheetTitle": "振り返りのヒント",
        "promptSheetSubtitle": "ひとつ選んで、自分の言葉に書き直してから、振り返りとして保存してください。",
        "promptClusterAll": "すべて",
        "promptUse": "これを使う",
        "promptSwap": "次のひとつ",
        "promptSave": "振り返りとして保存",
        "promptSaved": "保存しました——使ったヒントへの参照も一緒に残ります。",
        "promptStale": "ヒント集が更新されました。もう一度選び直してください。",
        "promptBlank": "先に何かを書いてください——空のままでは、タイムラインに中身のない行が増えるだけです。",
        "promptEmpty": "現在使えるヒントはありません。",
        "promptLoadFailed": "ヒントを読み込めませんでした。",
        "promptUntranslated": "英語で表示しています——このヒント集は現在、英語と中国語のみです。",
    },
    "km": {
        "promptEntry": "ត្រូវការការណែនាំទេ?",
        "promptSheetTitle": "ការណែនាំសម្រាប់ការឆ្លុះបញ្ចាំង",
        "promptSheetSubtitle": "ជ្រើសរើសមួយ សរសេរវាជាពាក្យរបស់អ្នកខ្លួនឯង រួចរក្សាទុកវាជាការឆ្លុះបញ្ចាំង។",
        "promptClusterAll": "ទាំងអស់",
        "promptUse": "ប្រើមួយនេះ",
        "promptSwap": "មួយទៀត",
        "promptSave": "រក្សាទុកជាការឆ្លុះបញ្ចាំង",
        "promptSaved": "បានរក្សាទុក — កំណត់ត្រានេះរក្សាការយោងទៅការណែនាំដែលអ្នកបានប្រើ។",
        "promptStale": "សំណុំការណែនាំត្រូវបានធ្វើបច្ចុប្បន្នភាព។ សូមជ្រើសរើសម្តងទៀត។",
        "promptBlank": "សរសេរអ្វីមួយជាមុនសិន — កំណត់ត្រាទទេគ្រាន់តែបន្ថែមបន្ទាត់ទទេក្នុងខ្សែពេលវេលារបស់អ្នក។",
        "promptEmpty": "បច្ចុប្បន្នមិនទាន់មានការណែនាំដែលអាចប្រើបានទេ។",
        "promptLoadFailed": "មិនអាចផ្ទុកការណែនាំទេ។",
        "promptUntranslated": "បង្ហាញជាភាសាអង់គ្លេស — សំណុំការណែនាំនេះមានតែភាសាអង់គ្លេស និងចិនប៉ុណ្ណោះ។",
    },
    "ko": {
        "promptEntry": "도움이 필요하신가요?",
        "promptSheetTitle": "회고 문구",
        "promptSheetSubtitle": "하나를 고르고 자기 말로 다시 쓴 뒤 회고로 저장하세요.",
        "promptClusterAll": "전체",
        "promptUse": "이것 사용",
        "promptSwap": "다음 것",
        "promptSave": "회고로 저장",
        "promptSaved": "저장했습니다 — 사용한 문구에 대한 참조도 함께 남습니다.",
        "promptStale": "문구 모음이 업데이트되었습니다. 다시 선택해 주세요.",
        "promptBlank": "먼저 내용을 입력하세요 — 비어 두면 타임라인에 빈 줄만 추가됩니다.",
        "promptEmpty": "현재 사용할 수 있는 문구가 없습니다.",
        "promptLoadFailed": "문구를 불러오지 못했습니다.",
        "promptUntranslated": "영어로 표시합니다 — 이 모음은 현재 영어와 중국어만 제공합니다.",
    },
    "ms": {
        "promptEntry": "Perlukan panduan?",
        "promptSheetTitle": "Panduan refleksi",
        "promptSheetSubtitle": "Pilih satu, tulis semula dengan kata anda sendiri, kemudian simpan sebagai refleksi.",
        "promptClusterAll": "Semua",
        "promptUse": "Guna ini",
        "promptSwap": "Satu lagi",
        "promptSave": "Simpan sebagai refleksi",
        "promptSaved": "Disimpan — rekod ini menyimpan rujukan panduan yang anda guna.",
        "promptStale": "Kumpulan panduan telah dikemas kini. Sila pilih semula.",
        "promptBlank": "Tulis sesuatu dahulu — rekod kosong hanya menambah baris tanpa isi dalam garis masa anda.",
        "promptEmpty": "Tiada panduan tersedia pada masa ini.",
        "promptLoadFailed": "Panduan gagal dimuatkan.",
        "promptUntranslated": "Dipaparkan dalam bahasa Inggeris — kumpulan ini baru ada versi Inggeris dan Cina.",
    },
    "pt": {
        "promptEntry": "Precisa de um estímulo?",
        "promptSheetTitle": "Estímulos de reflexão",
        "promptSheetSubtitle": "Escolha um, reescreva com suas palavras e salve como reflexão.",
        "promptClusterAll": "Todos",
        "promptUse": "Usar este",
        "promptSwap": "Outro",
        "promptSave": "Salvar como reflexão",
        "promptSaved": "Salvo — o registro guarda a referência ao estímulo usado.",
        "promptStale": "A biblioteca de estímulos foi atualizada. Escolha outro.",
        "promptBlank": "Escreva algo primeiro — um registro vazio só adicionaria uma linha sem conteúdo ao seu histórico.",
        "promptEmpty": "Nenhum estímulo disponível no momento.",
        "promptLoadFailed": "Não foi possível carregar os estímulos.",
        "promptUntranslated": "Exibido em inglês — esta biblioteca só tem versões em inglês e chinês.",
    },
    "ru": {
        "promptEntry": "Нужна подсказка?",
        "promptSheetTitle": "Подсказки для рефлексии",
        "promptSheetSubtitle": "Выберите одну, перепишите своими словами и сохраните как рефлексию.",
        "promptClusterAll": "Все",
        "promptUse": "Взять эту",
        "promptSwap": "Другую",
        "promptSave": "Сохранить как рефлексию",
        "promptSaved": "Сохранено — запись хранит ссылку на использованную подсказку.",
        "promptStale": "Набор подсказок обновился. Выберите заново.",
        "promptBlank": "Сначала напишите что-нибудь — пустая запись добавит в хронологию лишь строку без содержания.",
        "promptEmpty": "Сейчас подсказки недоступны.",
        "promptLoadFailed": "Не удалось загрузить подсказки.",
        "promptUntranslated": "Показано на английском — этот набор пока есть только на английском и китайском.",
    },
    "th": {
        "promptEntry": "ต้องการคำถามนำไหม?",
        "promptSheetTitle": "คำถามนำสำหรับการสะท้อน",
        "promptSheetSubtitle": "เลือกหนึ่งข้อ เขียนด้วยคำของคุณเอง แล้วบันทึกเป็นการสะท้อน",
        "promptClusterAll": "ทั้งหมด",
        "promptUse": "ใช้ข้อนี้",
        "promptSwap": "ข้อต่อไป",
        "promptSave": "บันทึกเป็นการสะท้อน",
        "promptSaved": "บันทึกแล้ว — รายการนี้เก็บอ้างอิงคำถามนำที่คุณใช้",
        "promptStale": "ชุดคำถามนำได้รับการอัปเดตแล้ว กรุณาเลือกใหม่",
        "promptBlank": "เขียนอะไรสักอย่างก่อน — รายการว่างจะเพิ่มเพียงบรรทัดว่างในไทม์ไลน์ของคุณ",
        "promptEmpty": "ยังไม่มีคำถามนำที่ใช้งานได้",
        "promptLoadFailed": "โหลดคำถามนำไม่สำเร็จ",
        "promptUntranslated": "แสดงเป็นภาษาอังกฤษ — ชุดนี้มีเฉพาะเวอร์ชันภาษาอังกฤษและจีน",
    },
    "vi": {
        "promptEntry": "Cần một gợi ý?",
        "promptSheetTitle": "Gợi ý suy ngẫm",
        "promptSheetSubtitle": "Chọn một gợi ý, viết lại theo lời của bạn, rồi lưu thành bản suy ngẫm.",
        "promptClusterAll": "Tất cả",
        "promptUse": "Dùng gợi ý này",
        "promptSwap": "Gợi ý khác",
        "promptSave": "Lưu thành bản suy ngẫm",
        "promptSaved": "Đã lưu — bản ghi giữ tham chiếu đến gợi ý bạn đã dùng.",
        "promptStale": "Thư viện gợi ý đã được cập nhật. Hãy chọn lại.",
        "promptBlank": "Hãy viết gì đó trước — bản ghi trống chỉ thêm một dòng không nội dung vào dòng thời gian của bạn.",
        "promptEmpty": "Hiện chưa có gợi ý nào.",
        "promptLoadFailed": "Không tải được gợi ý.",
        "promptUntranslated": "Đang hiển thị bằng tiếng Anh — thư viện này hiện chỉ có bản tiếng Anh và tiếng Trung.",
    },
}

ORDER = [
    "promptEntry", "promptSheetTitle", "promptSheetSubtitle", "promptClusterAll",
    "promptUse", "promptSwap", "promptSave", "promptSaved", "promptStale",
    "promptBlank", "promptEmpty", "promptLoadFailed", "promptUntranslated",
]


def reflect_span(text: str) -> tuple[int, int]:
    """返回 reflect 段（含首行）在全文中的 [start, end) 区间。"""
    start = text.index('  "reflect": {\n')
    end = text.index('\n  },', start)
    return start, end


def main() -> None:
    for lang, copy in COPY.items():
        assert set(copy) == set(ORDER), f"{lang} 键集与 ORDER 不一致：{set(copy) ^ set(ORDER)}"
        path = LOCALES / f"{lang}.ts"
        text = path.read_text(encoding="utf-8")

        for key in ORDER:
            assert f'    "{key}":' not in text, f"{lang} 已有 {key}，脚本只许跑一次"

        for value in copy.values():
            assert '"' not in value, f"{lang} 的值里有裸双引号，会破 TS 字符串：{value[:40]}"

        start, end = reflect_span(text)
        block = text[start:end]
        lines = block.split("\n")
        hits = [i for i, ln in enumerate(lines) if ln.startswith('    "saveDraft": ')]
        assert len(hits) == 1, f"{lang} reflect 段里 saveDraft 锚点出现 {len(hits)} 次"

        inserted = [f'    "{key}": {chr(34)}{copy[key]}{chr(34)},' for key in ORDER]
        lines[hits[0] + 1:hits[0] + 1] = inserted
        path.write_text(text[:start] + "\n".join(lines) + text[end:], encoding="utf-8")
        print(f"{lang}: reflect 段 +{len(ORDER)} 键")


if __name__ == "__main__":
    main()
