#!/usr/bin/env python3
"""T4-04 行动闭环文案落进 15 个 locale（一次性脚本）。

插入点：每个 locale 文件里 `"auth": {` 之前——15 个文件实测该锚点各出现一次
（脚本对每个文件 assert 命中一次，撞不上就整体不写）。只追加一个新段，不动任何既有键。

**可重跑**：段已存在时走 `patch_missing`，只把 ORDER 里缺的键补进段内对应位置。
这样「加一个文案」仍然是改上游表 + 重跑，而不是手改 15 个文件——手改的漏一个键
只有跑到那门语言才看得见，而 i18n 闸要等到有人读那份文件才会报。

为什么值里一个 ASCII 双引号都不许有：这些行直接拼进 TS 的双引号字符串，
脚本不做转义（`render` 就是 `f'"{value}"'`），漏一个就把整个 locale 文件打成语法错误——
而那只在 jest 运行时才红，比漏翻译难查得多。引用文案一律用弯引号。

`{{date}}` 那一格必须每门语言都留着：`i18nLocales.test.ts` 逐键比较占位符数量，
少一个占位符就是整条闸红，而不是「这句没翻全」。
"""
from __future__ import annotations

import re
from pathlib import Path

LOCALES = Path(__file__).resolve().parent.parent / "frontend" / "src" / "i18n" / "locales"

ORDER = [
    "title", "sectionActive", "sectionCompleted", "sectionSkipped", "empty", "truncated",
    "due", "markDone", "skip", "skipSaved", "cta", "modalTitle", "actionLabel", "goalLabel",
    "newGoal", "goalTitleLabel", "dateLabel", "dateInvalid", "create", "saving",
    "savedTitle", "savedMessage", "failed", "resultTitle", "resultHint",
    "notePlaceholder", "resultSubmit", "resultSaved", "resultSameDay",
]

RESULT_ORDER = ["better", "same", "worse", "unknown"]

COPY: dict[str, dict[str, object]] = {
    "en": {
        "title": "My actions",
        "sectionActive": "In progress",
        "sectionCompleted": "Done",
        "sectionSkipped": "Skipped",
        "empty": "Nothing here yet. Open an insight and tap Try a small action.",
        "due": "Due {{date}}",
        "markDone": "Mark done",
        "skip": "Skip",
        "skipSaved": "Skipped. It no longer waits for you.",
        "cta": "Try a small action",
        "modalTitle": "Add a small action",
        "actionLabel": "What will you actually do?",
        "goalLabel": "Which goal does this sit under?",
        "newGoal": "New goal",
        "goalTitleLabel": "Name this goal",
        "dateLabel": "Due date (optional)",
        "dateInvalid": "Write the date like 2026-10-31",
        "create": "Add to my actions",
        "saving": "Adding",
        "savedTitle": "Added",
        "savedMessage": "It is in your actions now.",
        "failed": "Could not save it, so nothing was added. Try again.",
        "resultTitle": "How did it go?",
        "resultHint": "Only what you actually felt. Two records on the same day count as one.",
        "notePlaceholder": "Add a line if you want",
        "resultSubmit": "Record it",
        "resultSaved": "Recorded",
        "resultSameDay": "You already recorded one today. That one stays.",
        "result": {
            "better": "Better",
            "same": "No change",
            "worse": "Worse",
            "unknown": "Hard to say",
        },
    },
    "zh": {
        "title": "我的行动",
        "sectionActive": "进行中",
        "sectionCompleted": "已完成",
        "sectionSkipped": "已跳过",
        "empty": "这里还没有行动。打开一条洞察，点「试一个小行动」就能加进来。",
        "due": "截止 {{date}}",
        "markDone": "标记完成",
        "skip": "跳过",
        "skipSaved": "已跳过，这条不再等你。",
        "cta": "试一个小行动",
        "modalTitle": "加一个小行动",
        "actionLabel": "你实际会做哪一件？",
        "goalLabel": "挂在哪个目标下？",
        "newGoal": "新建目标",
        "goalTitleLabel": "给这个目标起个名",
        "dateLabel": "截止日期（可留空）",
        "dateInvalid": "日期请写成 2026-10-31 这样",
        "create": "加进行动",
        "saving": "正在添加",
        "savedTitle": "已加入",
        "savedMessage": "它已经在你的行动里。",
        "failed": "没能保存，所以什么都没加。请再试一次。",
        "resultTitle": "结果如何？",
        "resultHint": "只写你真实的感觉。同一天记两次只算一次。",
        "notePlaceholder": "想多说一句就说一句",
        "resultSubmit": "记下",
        "resultSaved": "已记下",
        "resultSameDay": "今天已经记过一条，保留那一条。",
        "result": {
            "better": "变好",
            "same": "没变化",
            "worse": "变差",
            "unknown": "说不清",
        },
    },
    "ja": {
        "title": "私のアクション",
        "sectionActive": "進行中",
        "sectionCompleted": "完了",
        "sectionSkipped": "スキップ済み",
        "empty": "まだアクションはありません。インサイトを開いて「小さく試す」をタップすると追加できます。",
        "due": "期限 {{date}}",
        "markDone": "完了にする",
        "skip": "スキップ",
        "skipSaved": "スキップしました。もう待たなくて大丈夫です。",
        "cta": "小さく試す",
        "modalTitle": "小さなアクションを追加",
        "actionLabel": "実際に何をする？",
        "goalLabel": "どの目標に入れる？",
        "newGoal": "新しい目標",
        "goalTitleLabel": "目標に名前をつける",
        "dateLabel": "期限（任意）",
        "dateInvalid": "日付は 2026-10-31 の形で入力",
        "create": "アクションに追加",
        "saving": "追加中",
        "savedTitle": "追加しました",
        "savedMessage": "アクションの一覧に入りました。",
        "failed": "保存できませんでした。何も追加されていません。もう一度お試しください。",
        "resultTitle": "結果はどうだった？",
        "resultHint": "実際に感じたことだけ。同じ日に2回記録しても1回として数えます。",
        "notePlaceholder": "よかったら一言",
        "resultSubmit": "記録する",
        "resultSaved": "記録しました",
        "resultSameDay": "今日はすでに記録があります。あの1件を残します。",
        "result": {
            "better": "よくなった",
            "same": "変わっていない",
            "worse": "悪くなった",
            "unknown": "何とも言いにくい",
        },
    },
    "ko": {
        "title": "내 행동",
        "sectionActive": "진행 중",
        "sectionCompleted": "완료",
        "sectionSkipped": "건너뜀",
        "empty": "아직 행동이 없습니다. 인사이트를 열고 작은 행동 시도하기를 누르면 추가됩니다.",
        "due": "마감 {{date}}",
        "markDone": "완료로 표시",
        "skip": "건너뛰기",
        "skipSaved": "건너뛰었습니다. 이제 기다리지 않아도 돼요.",
        "cta": "작은 행동 시도하기",
        "modalTitle": "작은 행동 추가",
        "actionLabel": "실제로 무엇을 할까요?",
        "goalLabel": "어떤 목표에 넣을까요?",
        "newGoal": "새 목표",
        "goalTitleLabel": "이 목표에 이름 붙이기",
        "dateLabel": "마감일 (선택)",
        "dateInvalid": "날짜는 2026-10-31 형식으로 입력해 주세요",
        "create": "행동에 추가",
        "saving": "추가하는 중",
        "savedTitle": "추가했습니다",
        "savedMessage": "이제 당신의 행동 목록에 있습니다.",
        "failed": "저장하지 못했고 아무것도 추가되지 않았습니다. 다시 시도해 주세요.",
        "resultTitle": "결과는 어땠나요?",
        "resultHint": "실제로 느낀 것만 적어 주세요. 같은 날 두 번 적어도 한 번으로 칩니다.",
        "notePlaceholder": "원하면 한 줄 더",
        "resultSubmit": "기록하기",
        "resultSaved": "기록했습니다",
        "resultSameDay": "오늘 이미 기록했습니다. 앞의 기록을 그대로 둡니다.",
        "result": {
            "better": "더 나음",
            "same": "변화 없음",
            "worse": "더 나빠짐",
            "unknown": "뭐라 말하기 애매",
        },
    },
    "vi": {
        "title": "Hành động của tôi",
        "sectionActive": "Đang thực hiện",
        "sectionCompleted": "Đã xong",
        "sectionSkipped": "Đã bỏ qua",
        "empty": "Chưa có hành động nào. Mở một insight và chạm Thử một hành động nhỏ.",
        "due": "Hạn {{date}}",
        "markDone": "Đánh dấu đã xong",
        "skip": "Bỏ qua",
        "skipSaved": "Đã bỏ qua. Việc này không còn chờ bạn nữa.",
        "cta": "Thử một hành động nhỏ",
        "modalTitle": "Thêm một hành động nhỏ",
        "actionLabel": "Bạn sẽ thực sự làm gì?",
        "goalLabel": "Đặt dưới mục tiêu nào?",
        "newGoal": "Mục tiêu mới",
        "goalTitleLabel": "Đặt tên mục tiêu này",
        "dateLabel": "Hạn chót (không bắt buộc)",
        "dateInvalid": "Viết ngày theo dạng 2026-10-31",
        "create": "Thêm vào hành động",
        "saving": "Đang thêm",
        "savedTitle": "Đã thêm",
        "savedMessage": "Nó đã nằm trong danh sách hành động của bạn.",
        "failed": "Không lưu được, nên chưa thêm gì. Hãy thử lại.",
        "resultTitle": "Kết quả thế nào?",
        "resultHint": "Chỉ ghi điều bạn thực sự cảm nhận. Ghi hai lần trong cùng một ngày chỉ tính một.",
        "notePlaceholder": "Muốn thì ghi thêm một dòng",
        "resultSubmit": "Ghi lại",
        "resultSaved": "Đã ghi",
        "resultSameDay": "Hôm nay bạn đã ghi một lần. Lần đó được giữ nguyên.",
        "result": {
            "better": "Tốt hơn",
            "same": "Không thay đổi",
            "worse": "Tệ hơn",
            "unknown": "Khó nói",
        },
    },
    "th": {
        "title": "การลงมือทำของฉัน",
        "sectionActive": "กำลังทำ",
        "sectionCompleted": "ทำเสร็จ",
        "sectionSkipped": "ข้ามแล้ว",
        "empty": "ยังไม่มีรายการลงมือทำ เปิดข้อสังเกตหนึ่งข้อแล้วแตะ ลองทำเล็ก ๆ ดู",
        "due": "ครบกำหนด {{date}}",
        "markDone": "ทำเสร็จแล้ว",
        "skip": "ข้าม",
        "skipSaved": "ข้ามแล้ว รายการนี้ไม่ต้องรอคุณอีก",
        "cta": "ลองทำเล็ก ๆ ดู",
        "modalTitle": "เพิ่มการลงมือทำเล็ก ๆ",
        "actionLabel": "คุณจะลงมือทำอะไรจริง ๆ?",
        "goalLabel": "ให้อยู่ใต้เป้าหมายไหน?",
        "newGoal": "เป้าหมายใหม่",
        "goalTitleLabel": "ตั้งชื่อเป้าหมายนี้",
        "dateLabel": "วันครบกำหนด (ไม่บังคับ)",
        "dateInvalid": "เขียนวันที่แบบ 2026-10-31",
        "create": "เพิ่มเข้าไปในการลงมือทำ",
        "saving": "กำลังเพิ่ม",
        "savedTitle": "เพิ่มแล้ว",
        "savedMessage": "รายการนี้อยู่ในกลุ่มการลงมือทำของคุณแล้ว",
        "failed": "บันทึกไม่สำเร็จ ยังไม่ได้เพิ่มอะไร ลองอีกครั้ง",
        "resultTitle": "เป็นอย่างไรบ้าง?",
        "resultHint": "เขียนเฉพาะที่คุณรู้สึกจริง จดสองครั้งในวันเดียวกันนับครั้งเดียว",
        "notePlaceholder": "อยากเพิ่มอีกบรรทัดก็ใส่ได้",
        "resultSubmit": "จดไว้",
        "resultSaved": "จดแล้ว",
        "resultSameDay": "วันนี้คุณจดไว้แล้วหนึ่งครั้ง เก็บข้อนั้นไว้",
        "result": {
            "better": "ดีขึ้น",
            "same": "ไม่เปลี่ยน",
            "worse": "แย่ลง",
            "unknown": "บอกไม่ถูก",
        },
    },
    "id": {
        "title": "Aksiku",
        "sectionActive": "Sedang berjalan",
        "sectionCompleted": "Selesai",
        "sectionSkipped": "Dilewati",
        "empty": "Belum ada aksi. Buka sebuah wawasan, lalu ketuk Coba aksi kecil.",
        "due": "Tenggat {{date}}",
        "markDone": "Tandai selesai",
        "skip": "Lewati",
        "skipSaved": "Dilewati. Aksi ini tidak lagi menunggumu.",
        "cta": "Coba aksi kecil",
        "modalTitle": "Tambah aksi kecil",
        "actionLabel": "Apa yang benar-benar akan kamu lakukan?",
        "goalLabel": "Di bawah target yang mana?",
        "newGoal": "Target baru",
        "goalTitleLabel": "Beri nama target ini",
        "dateLabel": "Tenggat (opsional)",
        "dateInvalid": "Tulis tanggal seperti 2026-10-31",
        "create": "Tambahkan ke aksiku",
        "saving": "Menambahkan",
        "savedTitle": "Ditambahkan",
        "savedMessage": "Sekarang ada di daftar aksimu.",
        "failed": "Gagal menyimpan, jadi tidak ada yang ditambahkan. Coba lagi.",
        "resultTitle": "Bagaimana hasilnya?",
        "resultHint": "Tulis yang benar-benar kamu rasakan. Dua catatan di hari yang sama dihitung satu.",
        "notePlaceholder": "Mau tambah satu baris?",
        "resultSubmit": "Simpan",
        "resultSaved": "Tersimpan",
        "resultSameDay": "Hari ini kamu sudah mencatat satu. Yang itu dipertahankan.",
        "result": {
            "better": "Membaik",
            "same": "Tidak berubah",
            "worse": "Memburuk",
            "unknown": "Susah dijelaskan",
        },
    },
    "ms": {
        "title": "Tindakan saya",
        "sectionActive": "Sedang berjalan",
        "sectionCompleted": "Selesai",
        "sectionSkipped": "Dilangkau",
        "empty": "Belum ada tindakan. Buka satu wawasan dan ketik Cuba tindakan kecil.",
        "due": "Tarikh akhir {{date}}",
        "markDone": "Tandai selesai",
        "skip": "Langkau",
        "skipSaved": "Sudah dilangkau. Ia tidak menunggu anda lagi.",
        "cta": "Cuba tindakan kecil",
        "modalTitle": "Tambah tindakan kecil",
        "actionLabel": "Apa yang benar-benar akan anda buat?",
        "goalLabel": "Di bawah sasaran yang mana?",
        "newGoal": "Sasaran baharu",
        "goalTitleLabel": "Namakan sasaran ini",
        "dateLabel": "Tarikh akhir (pilihan)",
        "dateInvalid": "Tulis tarikh seperti 2026-10-31",
        "create": "Tambah ke tindakan saya",
        "saving": "Menambah",
        "savedTitle": "Ditambah",
        "savedMessage": "Ia kini ada dalam senarai tindakan anda.",
        "failed": "Gagal menyimpan, jadi tiada apa ditambah. Cuba lagi.",
        "resultTitle": "Bagaimana hasilnya?",
        "resultHint": "Tulis apa yang anda benar-benar rasa. Dua rekod pada hari yang sama dikira sekali.",
        "notePlaceholder": "Mahu tambah satu baris?",
        "resultSubmit": "Rekodkan",
        "resultSaved": "Direkodkan",
        "resultSameDay": "Anda sudah merekod hari ini. Yang itu kekal.",
        "result": {
            "better": "Lebih baik",
            "same": "Tidak berubah",
            "worse": "Lebih teruk",
            "unknown": "Susah nak cakap",
        },
    },
    "de": {
        "title": "Meine Schritte",
        "sectionActive": "Läuft",
        "sectionCompleted": "Erledigt",
        "sectionSkipped": "Übersprungen",
        "empty": "Noch keine Schritte. Öffnen Sie einen Insight und tippen Sie auf Kleinen Schritt versuchen.",
        "due": "Frist {{date}}",
        "markDone": "Erledigt markieren",
        "skip": "Überspringen",
        "skipSaved": "Übersprungen. Der Schritt wartet nicht mehr.",
        "cta": "Kleinen Schritt versuchen",
        "modalTitle": "Kleinen Schritt hinzufügen",
        "actionLabel": "Was tun Sie konkret?",
        "goalLabel": "Unter welches Ziel?",
        "newGoal": "Neues Ziel",
        "goalTitleLabel": "Dieses Ziel benennen",
        "dateLabel": "Frist (optional)",
        "dateInvalid": "Datum wie 2026-10-31 schreiben",
        "create": "Zu meinen Schritten",
        "saving": "Wird hinzugefügt",
        "savedTitle": "Hinzugefügt",
        "savedMessage": "Er ist jetzt in Ihren Schritten.",
        "failed": "Speichern fehlgeschlagen, nichts wurde hinzugefügt. Bitte erneut versuchen.",
        "resultTitle": "Wie lief es?",
        "resultHint": "Nur was Sie wirklich gefühlt haben. Zwei Einträge am selben Tag zählen einmal.",
        "notePlaceholder": "Gern eine Zeile mehr",
        "resultSubmit": "Notieren",
        "resultSaved": "Notiert",
        "resultSameDay": "Heute haben Sie schon notiert. Der erste Eintrag bleibt.",
        "result": {
            "better": "Besser",
            "same": "Kein Wechsel",
            "worse": "Schlechter",
            "unknown": "Schwer zu sagen",
        },
    },
    "fr": {
        "title": "Mes actions",
        "sectionActive": "En cours",
        "sectionCompleted": "Terminées",
        "sectionSkipped": "Ignorées",
        "empty": "Aucune action pour l'instant. Ouvrez un insight et touchez Essayer une petite action.",
        "due": "Échéance {{date}}",
        "markDone": "Marquer terminé",
        "skip": "Ignorer",
        "skipSaved": "Ignorée. Elle ne vous attend plus.",
        "cta": "Essayer une petite action",
        "modalTitle": "Ajouter une petite action",
        "actionLabel": "Que ferez-vous concrètement ?",
        "goalLabel": "Sous quel objectif ?",
        "newGoal": "Nouvel objectif",
        "goalTitleLabel": "Nommez cet objectif",
        "dateLabel": "Échéance (facultatif)",
        "dateInvalid": "Écrivez la date comme 2026-10-31",
        "create": "Ajouter à mes actions",
        "saving": "Ajout en cours",
        "savedTitle": "Ajoutée",
        "savedMessage": "Elle est dans vos actions maintenant.",
        "failed": "Enregistrement impossible, rien n'a été ajouté. Réessayez.",
        "resultTitle": "Comment ça s'est passé ?",
        "resultHint": "Écrivez seulement ce que vous avez ressenti. Deux mentions le même jour comptent pour une.",
        "notePlaceholder": "Une ligne de plus si vous voulez",
        "resultSubmit": "Noter",
        "resultSaved": "Noté",
        "resultSameDay": "Vous avez déjà noté aujourd'hui. On garde cette mention.",
        "result": {
            "better": "Mieux",
            "same": "Pas de changement",
            "worse": "Pire",
            "unknown": "Difficile à dire",
        },
    },
    "es": {
        "title": "Mis acciones",
        "sectionActive": "En curso",
        "sectionCompleted": "Hechas",
        "sectionSkipped": "Saltadas",
        "empty": "Aún no hay acciones. Abre una idea y toca Probar una acción pequeña.",
        "due": "Fecha límite {{date}}",
        "markDone": "Marcar hecha",
        "skip": "Saltar",
        "skipSaved": "Saltada. Ya no te espera.",
        "cta": "Probar una acción pequeña",
        "modalTitle": "Añadir una acción pequeña",
        "actionLabel": "¿Qué harás exactamente?",
        "goalLabel": "¿Bajo qué meta?",
        "newGoal": "Meta nueva",
        "goalTitleLabel": "Nombra esta meta",
        "dateLabel": "Fecha límite (opcional)",
        "dateInvalid": "Escribe la fecha como 2026-10-31",
        "create": "Añadir a mis acciones",
        "saving": "Añadiendo",
        "savedTitle": "Añadida",
        "savedMessage": "Ya está en tus acciones.",
        "failed": "No se guardó; no añadimos nada. Inténtalo otra vez.",
        "resultTitle": "¿Cómo fue?",
        "resultHint": "Solo lo que realmente sentiste. Dos registros el mismo día cuentan como uno.",
        "notePlaceholder": "Añade una línea si quieres",
        "resultSubmit": "Registrar",
        "resultSaved": "Registrado",
        "resultSameDay": "Hoy ya registraste uno. Ese se queda.",
        "result": {
            "better": "Mejor",
            "same": "Sin cambios",
            "worse": "Peor",
            "unknown": "Difícil de decir",
        },
    },
    "it": {
        "title": "Le mie azioni",
        "sectionActive": "In corso",
        "sectionCompleted": "Fatte",
        "sectionSkipped": "Saltate",
        "empty": "Nessuna azione per ora. Apri un insight e tocca Prova una piccola azione.",
        "due": "Entro {{date}}",
        "markDone": "Segna come fatta",
        "skip": "Salta",
        "skipSaved": "Saltata. Non ti aspetta più.",
        "cta": "Prova una piccola azione",
        "modalTitle": "Aggiungi una piccola azione",
        "actionLabel": "Cosa farai di preciso?",
        "goalLabel": "Sotto quale obiettivo?",
        "newGoal": "Nuovo obiettivo",
        "goalTitleLabel": "Dai un nome a questo obiettivo",
        "dateLabel": "Data limite (facoltativa)",
        "dateInvalid": "Scrivi la data come 2026-10-31",
        "create": "Aggiungi alle mie azioni",
        "saving": "Aggiunta in corso",
        "savedTitle": "Aggiunta",
        "savedMessage": "Ora è nelle tue azioni.",
        "failed": "Salvataggio fallito, non abbiamo aggiunto nulla. Riprova.",
        "resultTitle": "Com'è andata?",
        "resultHint": "Solo quello che hai davvero sentito. Due note nello stesso giorno contano una.",
        "notePlaceholder": "Aggiungi una riga se vuoi",
        "resultSubmit": "Registra",
        "resultSaved": "Registrata",
        "resultSameDay": "Oggi hai già registrato. Quella nota resta.",
        "result": {
            "better": "Meglio",
            "same": "Nessun cambiamento",
            "worse": "Peggio",
            "unknown": "Difficile a dirsi",
        },
    },
    "pt": {
        "title": "Minhas ações",
        "sectionActive": "Em andamento",
        "sectionCompleted": "Concluídas",
        "sectionSkipped": "Puladas",
        "empty": "Nenhuma ação ainda. Abra uma percepção e toque em Experimentar uma ação pequena.",
        "due": "Prazo {{date}}",
        "markDone": "Marcar como feita",
        "skip": "Pular",
        "skipSaved": "Pulada. Ela não espera mais por você.",
        "cta": "Experimentar uma ação pequena",
        "modalTitle": "Adicionar uma ação pequena",
        "actionLabel": "O que você vai fazer de fato?",
        "goalLabel": "Sob qual meta?",
        "newGoal": "Nova meta",
        "goalTitleLabel": "Dê um nome a esta meta",
        "dateLabel": "Prazo (opcional)",
        "dateInvalid": "Escreva a data como 2026-10-31",
        "create": "Adicionar às minhas ações",
        "saving": "Adicionando",
        "savedTitle": "Adicionada",
        "savedMessage": "Ela já está nas suas ações.",
        "failed": "Não foi possível salvar; nada foi adicionado. Tente de novo.",
        "resultTitle": "Como foi?",
        "resultHint": "Só o que você realmente sentiu. Dois registros no mesmo dia contam como um.",
        "notePlaceholder": "Se quiser, acrescente uma linha",
        "resultSubmit": "Registrar",
        "resultSaved": "Registrado",
        "resultSameDay": "Você já registrou hoje. Aquele fica.",
        "result": {
            "better": "Melhor",
            "same": "Sem mudança",
            "worse": "Pior",
            "unknown": "Difícil dizer",
        },
    },
    "ru": {
        "title": "Мои шаги",
        "sectionActive": "В работе",
        "sectionCompleted": "Сделано",
        "sectionSkipped": "Пропущено",
        "empty": "Шагов пока нет. Откройте вывод и нажмите Попробовать маленький шаг.",
        "due": "Срок {{date}}",
        "markDone": "Отметить сделанным",
        "skip": "Пропустить",
        "skipSaved": "Пропущено. Он больше не ждёт.",
        "cta": "Попробовать маленький шаг",
        "modalTitle": "Добавить маленький шаг",
        "actionLabel": "Что именно вы сделаете?",
        "goalLabel": "Под какой целью?",
        "newGoal": "Новая цель",
        "goalTitleLabel": "Название цели",
        "dateLabel": "Срок (необязательно)",
        "dateInvalid": "Пишите дату как 2026-10-31",
        "create": "Добавить в шаги",
        "saving": "Добавляем",
        "savedTitle": "Добавлено",
        "savedMessage": "Теперь это в ваших шагах.",
        "failed": "Не сохранилось, ничего не добавлено. Попробуйте снова.",
        "resultTitle": "Как прошло?",
        "resultHint": "Только то, что вы действительно почувствовали. Две записи в один день считаются одной.",
        "notePlaceholder": "Добавьте строку, если хотите",
        "resultSubmit": "Записать",
        "resultSaved": "Записано",
        "resultSameDay": "Сегодня вы уже записывали. Та запись остаётся.",
        "result": {
            "better": "Стало лучше",
            "same": "Без изменений",
            "worse": "Стало хуже",
            "unknown": "Трудно сказать",
        },
    },
    "km": {
        "title": "សកម្មភាពរបស់ខ្ញុំ",
        "sectionActive": "កំពុងធ្វើ",
        "sectionCompleted": "បានបញ្ចប់",
        "sectionSkipped": "បានរំលង",
        "empty": "មិនទាន់មានសកម្មភាពទេ។ សូមបើកការសង្កេតមួយ រួចចុះលើ សាកល្បងសកម្មភាពតិចតួច។",
        "due": "ផុតកំណត់ {{date}}",
        "markDone": "គណ្ថាថាបញ្ចប់",
        "skip": "រំលង",
        "skipSaved": "បានរំលងហើយ។ វាមិនរង់ចាំអ្នកទៀតទេ។",
        "cta": "សាកល្បងសកម្មភាពតិចតួច",
        "modalTitle": "បន្ថែមសកម្មភាពតិចតួច",
        "actionLabel": "តើអ្នកនឹងធ្វើអ្វីជាក់ស្តែង?",
        "goalLabel": "ដាក់ក្រោមគោលដៅណា?",
        "newGoal": "គោលដៅថ្មី",
        "goalTitleLabel": "ដាក់ឈ្មោះគោលដៅនេះ",
        "dateLabel": "ថ្ងៃផុតកំណត់ (មិនចាំបាច់)",
        "dateInvalid": "សរសេរថ្ងៃដូចជា 2026-10-31",
        "create": "បន្ថែមទៅសកម្មភាពរបស់ខ្ញុំ",
        "saving": "កំពុងបន្ថែម",
        "savedTitle": "បានបន្ថែម",
        "savedMessage": "ឥឡូវនេះវាស្ថិតក្នុងសកម្មភាពរបស់អ្នកហើយ។",
        "failed": "រក្សាទុកមិនបាន ដូច្នេះមិនមានអ្វីត្រូវបានបន្ថែមទេ។ សូមព្យាយាមម្តងទៀត។",
        "resultTitle": "តើលទ្ធផលយ៉ាងដូចម្តេច?",
        "resultHint": "សរសេរតែអ្វីដែលអ្នកពិតជាមានអារម្មណ៍។ ការកត់ពីរនៅថ្ងៃតែមួយរាប់តែម្តង។",
        "notePlaceholder": "បន្ថែមមួយបន្ទាត់បើអ្នកចង់",
        "resultSubmit": "កត់ទុក",
        "resultSaved": "បានកត់",
        "resultSameDay": "ថ្ងៃនេះអ្នកបានកត់ហើយ។ ការកត់មុននៅដដែល។",
        "result": {
            "better": "ប្រសើរឡើង",
            "same": "មិនប្តូរ",
            "worse": "អាក្រក់ឡើង",
            "unknown": "ពិបាកនិយាយ",
        },
    },
}


# `truncated` 是第二批补进去的键：Go 的两个列表都是 `ORDER BY created_at DESC LIMIT n`
# （db/queries/canonical.sql 的 ListGoals / ListCommitments），默认 50 条、上限 200，
# 超出只回 `truncated=true`（domain/service_goal.go 的 clampListLimit）。
# 屏上不消费这一格就是「行动变少」这种最难查的假象，所以它必须有文案。
TRUNCATED: dict[str, str] = {
    "en": "Only the most recent actions are listed here. Older ones are not shown.",
    "zh": "这里只列出最近的行动，更早的没有显示。",
    "es": "Aquí solo se muestran las acciones más recientes. Las anteriores no aparecen.",
    "ko": "여기에는 최근 행동만 표시됩니다. 그보다 이전 행동은 나타나지 않습니다.",
    "ja": "ここには直近の行動だけが表示され、それより前の行動は表示されません。",
    "ru": "Здесь показаны только последние действия. Более ранние не отображаются.",
    "id": "Yang tampil di sini hanya tindakan terbaru. Yang lebih lama tidak ditampilkan.",
    "ms": "Yang disenaraikan di sini hanya tindakan terkini. Yang lebih lama tidak dipaparkan.",
    "th": "ที่นี่แสดงเฉพาะการกระทำล่าสุด ส่วนที่เก่ากว่านั้นไม่แสดง",
    "de": "Hier werden nur die neuesten Aktionen angezeigt. Ältere werden nicht gezeigt.",
    "fr": "Seules les actions récentes sont affichées ici. Les plus anciennes sont masquées.",
    "it": "Qui vengono mostrate solo le azioni recenti. Le precedenti non sono visualizzate.",
    "pt": "Aqui só aparecem as ações recentes. As anteriores não são mostradas.",
    "vi": "Ở đây chỉ liệt kê hành động gần nhất. Những hành động sớm hơn không hiển thị.",
    "km": "ទីនេះបង្ហាញតែសកម្មភាពថ្មីៗប៉ុណ្ណោះ ចំណែកសកម្មភាពចាស់ជាងនេះមិនបង្ហាញទេ។",
}

for _lang, _text in TRUNCATED.items():
    assert _lang in COPY, f"TRUNCATED 里有未登记的语言：{_lang}"
    assert "truncated" not in COPY[_lang], f"{_lang} 已自带 truncated 键"
    COPY[_lang]["truncated"] = _text

assert set(TRUNCATED) == set(COPY), "TRUNCATED 必须覆盖全部 15 个语言"


def render(lang: str, copy: dict[str, object]) -> str:
    lines = ['  "actions": {']
    for key in ORDER:
        lines.append(f'    "{key}": "{copy[key]}",')
    result = copy["result"]
    assert isinstance(result, dict) and list(result) == RESULT_ORDER, \
        f"{lang} 的 result 子表键序必须与 RESULT_ORDER 一致"
    keys = list(result)
    lines.append('    "result": {')
    for key in keys:
        tail = "," if key != keys[-1] else ""
        lines.append(f'      "{key}": "{result[key]}"{tail}')
    lines.append("    }")
    lines.append("  },")
    return "\n".join(lines) + "\n"


BLOCK_HEAD = '  "actions": {\n'


def block_span(text: str) -> tuple[int, int]:
    """actions 段在文件文本里的 [起, 止) 偏移（止 = 该段收尾的 `  },` 之后）。"""
    start = text.index(BLOCK_HEAD)
    end = text.index("\n  },\n", start) + len("\n  },\n")
    return start, end


def patch_missing(text: str, lang: str, copy: dict[str, object]) -> tuple[str, int]:
    """已有 actions 段时只补该段里缺的键：补一个键不许把整段重写一遍。

    插入点取「ORDER 里排在它之前、且块内确实存在的那个键」的下一行，所以补进去的键
    与首跑生成的键序待在同一处。块内一个可对齐的键都没有就直接报错，
    而不是悄悄插到别的位置去。
    """
    start, stop = block_span(text)
    block = text[start:stop]
    added = 0
    for key in ORDER:
        if re.search(rf'^    "{key}": ', block, re.M):
            continue
        value = copy[key]
        assert isinstance(value, str)
        line = f'    "{key}": "{value}",'
        prev = None
        for candidate in reversed(ORDER[: ORDER.index(key)]):
            found = re.search(rf'^    "{candidate}": .*$', block, re.M)
            if found:
                prev = found
                break
        assert prev is not None, f"{lang} 的 actions 段里找不到可对齐的既有键"
        block = block[: prev.end()] + "\n" + line + block[prev.end() :]
        added += 1
    return text[:start] + block + text[stop:], added


def main() -> None:
    for lang, copy in COPY.items():
        assert set(copy) == set(ORDER) | {"result"}, \
            f"{lang} 键集与 ORDER 不一致：{set(copy) ^ (set(ORDER) | {'result'})}"
        path = LOCALES / f"{lang}.ts"
        text = path.read_text(encoding="utf-8")
        anchor = '  "auth": {\n'
        assert text.count(anchor) == 1, f"{lang} 锚点出现 {text.count(anchor)} 次"
        for value in list(copy.values()):
            if isinstance(value, dict):
                for inner in value.values():
                    assert '"' not in inner and "{{" not in inner, \
                        f"{lang} result 值里有裸双引号或占位符：{inner[:40]}"
            else:
                assert '"' not in value, f"{lang} 值里有裸双引号，会破 TS 字符串：{value[:40]}"
        for key in ORDER:
            want = str(copy[key]).count("{{date}}")
            assert want in (0, 1), f"{lang} {key} 的 {{{{date}}}} 出现 {want} 次"
            assert (key == "due") == (want == 1), \
                f"{lang} {key} 的占位符归属变了（只有 due 该带 {{{{date}}}}）"
        if BLOCK_HEAD not in text:
            path.write_text(text.replace(anchor, render(lang, copy) + anchor), encoding="utf-8")
            print(f"{lang}: +{len(ORDER) + len(RESULT_ORDER)} 键（整段新建）")
            continue
        patched, added = patch_missing(text, lang, copy)
        assert added > 0, f"{lang} 的 actions 段已含全部键：ORDER 变了请核对，不是无事可做"
        path.write_text(patched, encoding="utf-8")
        print(f"{lang}: +{added} 键（补进已有段）")


if __name__ == "__main__":
    main()
