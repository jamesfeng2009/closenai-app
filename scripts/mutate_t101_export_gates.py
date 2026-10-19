#!/usr/bin/env python3
"""T1-01（通用数据导出）的变异自证（2026-10-18）。

这批交付的是「一个人把全库拷一遍」这条链，闸判的三类东西各有靶：

后端（`backend/tests/test_data_export.py`）——
- B1 撤掉 `_require_own` 的 user_id 比较 → 别人的 job_id 变成可读，§14.1 跨用户隔离失效；
- B2 撤掉 24h 限频的存在性判据 → 一个人可以反复把全库拷一遍；
- B3 撤掉下载侧的过期校验 → 到期清理只是摆设，产物永久留在盘上；
- B4 让表级排除失效 → `refresh_tokens`/`receipts` 进包（§14.1 明文禁止的那一种）；
- B5 逐表读取把申请人取反 → 包里的行全是别人的（§14.1 产物面隔离）；
- B6 撤掉列级排除 → 512 维向量进包，zip 体积翻数倍且把派生物当原始数据带走；
- B7 撤掉 ready-缺文件的自愈 → 用户对着下载 500 等到过期；
- B8 把 cron 的 5 分钟改成 30 分钟 → 与工作单「每 5 分钟」的交付节奏不符。

前端（`frontend/__tests__/dataExportWiring.test.ts`）——
- F1 界面把保存周期写死 7 天（§14.2 的第二真源）；
- F2 五态里 `expired` 被改名（状态词汇与后端分家）；
- F3 业务码 40905 抄成 40995（错误码第二真源）；
- F4 下载改成 `Linking.openURL(download_path)`（带不上 Bearer，必然 401，且把「导出」变成可转发的 bearer 链接）；
- F5 下载封装丢掉 `binary: true`（字节流被当信封解包）；
- F6 types 撤掉 `download_path` 声明（响应形状与 `job_to_dict` 分家）。

实库（`backend/tests/test_data_export_pg.py`，一次性空库）——sqlite 结构上测不到的三条：
- P1 列级排除失效：`memories.embedding` 那 512 维在真 PG 上是 pgvector HALFVEC，sqlite 侧
  只是兼容用的 JSON，「排除列根本没进 SELECT」这句只有真库才算测过；
- P2 CAS 谓词摘掉：认领从「rowcount=1 才算拿到」退化成无条件 UPDATE，两个 worker 都以为自己
  领到了（sqlite 是单写者，永远排不成「同时」）；
- P3 注销不再先收产物：行删了包还在盘上，`file_path` 随行一起消失，文件变成库上无记录的孤儿。

`pytest-pg` 这一类缺 `TEST_DATABASE_URL` 时记「未跑」，既不算红也不算绿
（[[feedback-mutation-anchor-must-reach-target]]）；跑全量前先备好一次性库：
    docker exec closenai-pg-dev psql -U postgres -c 'CREATE DATABASE t101_e2e'
    DATABASE_URL=postgresql+psycopg://… .venv/bin/python -m alembic -c backend/alembic.ini upgrade head
    TEST_DATABASE_URL=postgresql://… CLOSEN_REQUIRE_PG=1 ./.venv/bin/python scripts/mutate_t101_export_gates.py

GR1/GR2/GR3 是「不该打红」的证人：闸判行为与名单，不判源码行数与注释位置。

纪律：串行执行、copy2 备份＋filecmp 逐字节写回、不用 git checkout
（[[feedback-mutation-restore-no-git-checkout]]、[[feedback-mutation-gates-no-concurrency]]）；
每条红闸先跑基线，基线不绿记「未跑」，不把环境的红算成闸的红
（[[feedback-mutation-anchor-must-reach-target]]）。

用法：
    ./.venv/bin/python scripts/mutate_t101_export_gates.py --check
    ./.venv/bin/python scripts/mutate_t101_export_gates.py
    ./.venv/bin/python scripts/mutate_t101_export_gates.py --only B1,F4
"""
from __future__ import annotations

import argparse
import filecmp
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VENV_PY = REPO / ".venv" / "bin" / "python"
FRONT = REPO / "frontend"

SVC = REPO / "backend" / "services" / "export_service.py"
USER_SVC = REPO / "backend" / "services" / "user_service.py"
EXPORT_REPO = REPO / "backend" / "repositories" / "export_repository.py"
CRONTAB = REPO / "deploy" / "schedule" / "crontab"
SCREEN = FRONT / "src" / "screens" / "PrivacySecurityScreen.tsx"
CONFIG_TS = FRONT / "src" / "config" / "exportArchive.ts"
API_TS = FRONT / "src" / "services" / "api.ts"
TYPES_TS = FRONT / "src" / "types" / "index.ts"

GATE = "backend/tests/test_data_export.py"
PG_GATE = "backend/tests/test_data_export_pg.py"
PG_ENV = "TEST_DATABASE_URL"
WIRING = "__tests__/dataExportWiring.test.ts"

CRON_LINE = (
    '*/5 * * * * . /etc/closenai/cron.env && curl -fsS -H "Authorization: Bearer $CRON_SECRET" '
    "http://127.0.0.1:8000/api/v1/cron/export-jobs >/dev/null"
)

MUTATIONS: list[tuple] = [
    (
        "B1",
        "撤掉 _require_own 的 user_id 比较：别人的 job_id 与自己的同权可读（§14.1）",
        [(SVC, "        if job is None or job.user_id != user_id:", "        if job is None:")],
        "pytest", f"{GATE}::test_export_cross_user_isolation", "test_export_cross_user_isolation",
    ),
    (
        "B2",
        "撤掉 24h 限频的存在性判据：同一个人可以反复把全库拷一遍",
        [(SVC, "        existing = await export_repository.latest_blocking(db, user_id, since)",
          "        existing = None")],
        "pytest", f"{GATE}::test_request_rate_limited_per_database",
        "test_request_rate_limited_per_database",
    ),
    (
        "B3",
        "撤掉下载侧的过期校验：到期清理只剩 cron 一条腿，产物在盘上永久可读",
        [(SVC,
          "        if job.expires_at is not None and _aware(job.expires_at) <= now:\n"
          "            raise AppException(\n"
          "                ErrorCode.EXPORT_NOT_READY,\n",
          "        if False:\n"
          "            raise AppException(\n"
          "                ErrorCode.EXPORT_NOT_READY,\n")],
        "pytest", f"{GATE}::test_downloading_an_expired_job_is_conflict",
        "test_downloading_an_expired_job_is_conflict",
    ),
    (
        "B4",
        "表级排除失效：refresh_tokens／receipts 被打进可转发的 zip（§14.1 明文禁止）",
        [(SVC, "        if key not in EXPORT_EXCLUDE_REASONS", "        if key not in ()")],
        "pytest", f"{GATE}::test_export_excludes_secrets", "test_export_excludes_secrets",
    ),
    (
        "B5",
        "逐表读取把申请人取反：包里的行全是别人的（产物面的跨用户泄露）",
        [(SVC, "                            .where(target == job.user_id)",
          "                            .where(target != job.user_id)")],
        "pytest", f"{GATE}::test_other_users_rows_are_not_in_the_package",
        "test_other_users_rows_are_not_in_the_package",
    ),
    (
        "B6",
        "列级排除失效：memories.embedding 进包，派生向量被当作用户带走的数据",
        [(SVC, "            if c.name not in EXCLUDED_COLUMNS.get(model.__tablename__, {})",
          "            if True")],
        "pytest", f"{GATE}::test_export_excludes_secrets", "test_export_excludes_secrets",
    ),
    (
        "B7",
        "撤掉 ready-缺文件的自愈：卷没挂对时用户对着下载 500 等到过期",
        [(SVC, '        for job_id in await export_repository.ids_by_status(db, ("ready",)):',
          '        for job_id in []:')],
        "pytest", f"{GATE}::test_missing_artifact_is_rebuilt_not_left_dangling",
        "test_missing_artifact_is_rebuilt_not_left_dangling",
    ),
    (
        "B8",
        "cron 从每 5 分钟改成每 30 分钟：申请后「马上能拿到」的承诺变成半小时",
        [(CRONTAB, CRON_LINE, CRON_LINE.replace("*/5 * * * *", "*/30 * * * *"))],
        "pytest", f"{GATE}::test_export_queue_is_scheduled_every_five_minutes",
        "test_export_queue_is_scheduled_every_five_minutes",
    ),
    (
        "F1",
        "界面把保存周期写死 7 天：§14.2 要的那句「留多久」变成前端第二真源",
        [(SCREEN, "t('export.desc', { days: retentionDays })", "t('export.desc', { days: 7 })")],
        "jest", WIRING, "列表封装回传 retention_days，屏用它插值而不是自己写天数",
    ),
    (
        "F2",
        "五态里 expired 改名 done（类型面与映射表一起改，保住编译）：状态词汇与后端 EXPORT_STATUSES 分家，界面念一个后端不认识的态",
        [(TYPES_TS,
          "export type ExportJobStatus = 'queued' | 'running' | 'ready' | 'failed' | 'expired';",
          "export type ExportJobStatus = 'queued' | 'running' | 'ready' | 'failed' | 'done';"),
         (CONFIG_TS, "  expired: 'export.statusExpired',", "  done: 'export.statusExpired',")],
        "jest", WIRING, "EXPORT_STATUS_KEYS 的键与后端 EXPORT_STATUSES 同集同序",
    ),
    (
        "F3",
        "业务码 40905 抄成 40995：错误码的第二真源，用户看到通用失败而不是「还没好」",
        [(CONFIG_TS, "  40905: 'export.notReady',", "  40995: 'export.notReady',")],
        "jest", WIRING, "三个业务码与 backend/core/errors.py 逐值相等",
    ),
    (
        "F4",
        "下载退回 Linking.openURL(download_path)：带不上 Bearer 必然 401，还造出一个可转发的 bearer 链接",
        [(SCREEN,
          "      const bytes = await getUserExportDownload(fresh.job_id);\n"
          "      await saveArchive(bytes, fresh.file_name ?? 'closenai-export.zip');",
          "      await Linking.openURL(fresh.download_path ?? '');")],
        "jest", WIRING, "屏里的 Linking 只用于法律条款两个外链，没有拿去开过任何导出线",
    ),
    (
        "F5",
        "下载封装丢掉 binary: true：zip 字节流被当 JSON 信封解包，拿到的是乱码或异常",
        [(API_TS,
          "  return request<ArrayBuffer>(`/user/export/${jobId}/download`, { binary: true });",
          "  return request<ArrayBuffer>(`/user/export/${jobId}/download`);")],
        "jest", WIRING, "下载那条吃二进制出口，其余三条仍走 JSON 信封解包",
    ),
    (
        "F6",
        "types 撤掉 download_path：响应形状与后端 job_to_dict 分家（薄掉一个契约字段）",
        [(TYPES_TS, "  download_path?: string;", "")],
        "jest", WIRING, "types 的 ExportJob 字段与后端 job_to_dict 同集",
    ),
    (
        "P1",
        "列级排除失效（真库面）：pgvector 的 512 维向量被读进档案包——sqlite 上那列只是兼容 JSON，这句只有真库测得到",
        [(SVC, "            if c.name not in EXCLUDED_COLUMNS.get(model.__tablename__, {})",
          "            if True")],
        "pytest-pg", f"{PG_GATE}::test_real_pg_package_is_readable_and_excludes_vector",
        "test_real_pg_package_is_readable_and_excludes_vector",
    ),
    (
        "P2",
        "CAS 认领摘掉 status='queued' 谓词（真库面）：两个 worker 同时认领同一个 job，都以为自己拿到了",
        [(EXPORT_REPO,
          '            .where(DataExportJob.id == job_id, DataExportJob.status == "queued")',
          '            .where(DataExportJob.id == job_id)')],
        "pytest-pg", f"{PG_GATE}::test_real_pg_two_workers_claim_the_same_job_once",
        "test_real_pg_two_workers_claim_the_same_job_once",
    ),
    (
        "P3",
        "注销不再先收产物（真库面）：行删了包还在盘上，file_path 随行消失，文件变成库上无记录的孤儿",
        [(USER_SVC,
          "        artifacts = await export_service.delete_user_artifacts(db, user_id)",
          "        artifacts = 0")],
        "pytest-pg", f"{PG_GATE}::test_real_pg_purge_clears_rows_and_artifact",
        "test_real_pg_purge_clears_rows_and_artifact",
    ),
]

# 不该打红的东西：证明闸判的是行为与名单，不是源码行数或注释位置。
GREEN: list[tuple] = [
    (
        "GR1",
        "在跨用户隔离那句 raise 前插 15 行注释：这条闸判行为，不该因为文件变长而红",
        [(SVC,
          '            raise AppException(ErrorCode.RESOURCE_NOT_FOUND, "导出任务不存在 / export job not found")',
          "".join("            # 变异证人 %d\n" % i for i in range(15))
          + '            raise AppException(ErrorCode.RESOURCE_NOT_FOUND, "导出任务不存在 / export job not found")')],
        "pytest", f"{GATE}::test_export_cross_user_isolation", "",
    ),
    (
        "GR2",
        "往 exportArchive.ts 的映射表上面插 15 行注释：键集与码值闸判内容，不判行号",
        [(CONFIG_TS, "/** 后端业务码 → i18n 键。未登记码由界面退回服务端文案，不假装成功。 */",
          "".join(f"// 变异证人 {i}\n" for i in range(15))
          + "/** 后端业务码 → i18n 键。未登记码由界面退回服务端文案，不假装成功。 */")],
        "jest", WIRING, "",
    ),
    (
        "GR3",
        "往 export_repository.py 的状态名单上面插 15 行注释：实库闸判的是认领与包的内容，不该因为文件变长而红",
        [(EXPORT_REPO,
          'BLOCKING_STATUSES = ("queued", "running", "ready")',
          "".join(f"# 变异证人 {i}\n" for i in range(15))
          + 'BLOCKING_STATUSES = ("queued", "running", "ready")')],
        "pytest-pg", PG_GATE, "",
    ),
]


def _apply(edits: list) -> None:
    for path, old, new in edits:
        src = path.read_text(encoding="utf-8")
        n = src.count(old)
        assert n == 1, f"{path.name}：锚点命中 {n} 次（要求恰好 1 次）\n{old[:90]!r}"
        path.write_text(src.replace(old, new, 1), encoding="utf-8")


def _verify_anchor(edits: list) -> str | None:
    for path, old, _new in edits:
        if not path.exists():
            return "文件不存在"
        n = path.read_text(encoding="utf-8").count(old)
        if n != 1:
            return f"命中 {n} 次（要 1）"
    return None


def _sanity(edits: list) -> str | None:
    """Python 靶被改成语法不通时，红的是解释器而不是判据——先当场拦下。"""
    import ast
    for path, _old, _new in edits:
        if path.suffix != ".py":
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            return f"{path.name}：变异体不是合法 Python（{exc}）"
    return None


def run_pytest(node: str) -> tuple[int, str]:
    proc = subprocess.run(
        [str(VENV_PY), "-m", "pytest", node, "-q", "--no-header"],
        cwd=REPO, capture_output=True, text=True, timeout=900,
    )
    return proc.returncode, proc.stdout + proc.stderr


def run_jest(test_file: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["npx", "jest", "--silent", test_file],
        cwd=FRONT, capture_output=True, text=True, timeout=900,
    )
    return proc.returncode, proc.stdout + proc.stderr


def run(kind: str, target: str) -> tuple[int, str]:
    if kind == "jest":
        return run_jest(target)
    if kind == "pytest-pg" and not os.environ.get(PG_ENV):
        # 缺库时那条用例是 skip，pytest 退出码仍是 0——不拦下来就会把「没跑」念成「基线绿」。
        return -2, f"{PG_ENV} 未设置：{target} 没跑，实库用例不算绿"
    return run_pytest(target)


def is_red(code: int, out: str, kind: str, target: str, marker: str) -> bool:
    if code == 0 or not marker:
        return False
    if not re.search(r"\d+ (failed|error)", out):
        return False
    if kind.startswith("pytest"):
        return f"FAILED {target}" in out
    return marker in out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只验锚点命中数，不改任何文件")
    ap.add_argument("--only", default="", help="逗号分隔的用例 id")
    args = ap.parse_args()
    wanted = {x.strip().upper() for x in args.only.split(",") if x.strip()}

    cases = [*MUTATIONS, *GREEN]
    if args.check:
        bad = 0
        for case in cases:
            problem = _verify_anchor(case[2])
            if problem:
                print(f"坏锚点 {case[0]}：{problem}")
                bad += 1
        print(f"锚点校验：{'全部命中一次' if bad == 0 else f'{bad} 条有问题'}（共 {len(cases)} 条）")
        return 1 if bad else 0

    paths = sorted({e[0] for case in cases for e in case[2]})
    with tempfile.TemporaryDirectory(prefix="mutate-t101-") as tmp:
        originals: dict[Path, Path] = {}
        for idx, path in enumerate(paths):
            assert path.is_file(), f"靶文件不存在：{path}"
            rel = path.relative_to(REPO).as_posix().replace("/", "+")
            bak = Path(tmp) / f"{idx:02d}-{rel}"
            assert bak not in originals.values(), f"备份名碰撞：{bak}"
            shutil.copy2(path, bak)
            originals[path] = bak

        def restore() -> None:
            for path, bak in originals.items():
                shutil.copy2(bak, path)
                assert filecmp.cmp(bak, path, shallow=False), f"{path} 恢复后字节不一致"

        red_ok = red_fail = green_ok = green_fail = 0
        unrun: list[str] = []
        failures: list[str] = []
        try:
            for cid, desc, edits, kind, target, marker in MUTATIONS:
                if wanted and cid.upper() not in wanted:
                    continue
                base_code, base_out = run(kind, target)
                if base_code == -2:
                    unrun.append(cid)
                    print(f"[{cid}] 未跑（{base_out}）：缺库不是绿，也不把它的红记在闸头上")
                    continue
                if base_code != 0:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 未跑（基线本来就不绿，exit={base_code}）：不能把环境的红记成闸的红\n  "
                          + "\n  ".join(base_out.splitlines()[-8:]))
                    continue
                _apply(edits)
                try:
                    broken = _sanity(edits)
                    code, out = (-1, broken) if broken else run(kind, target)
                finally:
                    restore()
                if code == -1:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（变异体自身不合法）：{out}")
                    continue
                if is_red(code, out, kind, target, marker):
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓（{kind}）  {desc}")
                else:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（{kind} exit={code}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-18:]))

            for cid, desc, edits, kind, target, _marker in GREEN:
                if wanted and cid.upper() not in wanted:
                    continue
                _apply(edits)
                try:
                    code, out = run(kind, target)
                finally:
                    restore()
                if code == -2:
                    unrun.append(cid)
                    print(f"[{cid}] 未跑（{out}）：缺库时这条证人没有证人")
                    continue
                if code == 0:
                    green_ok += 1
                    print(f"[{cid}] 保持绿 ✓（{kind}）  {desc}")
                else:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 保持绿 ✗（{kind} exit={code}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-18:]))
        except Exception as exc:  # noqa: BLE001
            restore()
            print(f"异常中止，已恢复：{exc!r}")
            return 2

        code, out = run_pytest(GATE)
        residue = 0 if code == 0 else 1
        print(("复核 导出闸：绿" if code == 0
               else "残留（红！）：\n  " + "\n  ".join(out.splitlines()[-15:])))
        if os.environ.get(PG_ENV):
            pg_code, pg_out = run_pytest(PG_GATE)
            residue = residue or (0 if pg_code == 0 else 1)
            print(("复核 实库闸：绿" if pg_code == 0
                   else "残留（红！）：\n  " + "\n  ".join(pg_out.splitlines()[-15:])))
        else:
            print(f"复核 实库闸：未跑（缺 {PG_ENV}）")
        print(f"\nT1-01 变异自证：打红 {red_ok}/{len(MUTATIONS)}，保持绿 {green_ok}/{len(GREEN)}，"
              f"失败 {red_fail + green_fail}，未跑 {len(unrun)}，残留 {residue}")
        if unrun:
            print(f"未跑清单（缺 {PG_ENV}，不算绿）：{', '.join(unrun)}")
        if failures:
            print(f"失败清单：{failures}")
        return 1 if (red_fail or green_fail or residue or unrun) else 0


if __name__ == "__main__":
    sys.exit(main())
