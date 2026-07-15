#!/usr/bin/env node
/**
 * ClosenAI 技能框架验证脚本
 *
 * 检查 .claude/ 目录结构、技能文件、spec 文件、workflow.md 一致性
 * 用法：node .trellis/scripts/verify-skills.js
 */

const fs = require('fs');
const path = require('path');

const BASE = path.resolve(__dirname, '../..');

let passed = 0;
let failed = 0;
const errors = [];

function check(condition, label) {
  if (condition) {
    console.log(`  ✓ ${label}`);
    passed++;
  } else {
    console.log(`  ✗ ${label}`);
    failed++;
    errors.push(label);
  }
}

function exists(relPath) {
  return fs.existsSync(path.join(BASE, relPath));
}

function read(relPath) {
  return fs.readFileSync(path.join(BASE, relPath), 'utf-8');
}

console.log('\n🔍 ClosenAI 技能框架验证\n');

// 1. 检查 .claude/ 目录结构
console.log('── .claude/ 目录结构 ──');
check(exists('.claude/settings.json'), '.claude/settings.json 存在');
check(exists('.claude/skills/superpowers/'), '.claude/skills/superpowers/ 目录存在');
check(exists('.claude/skills/matt-pocock/'), '.claude/skills/matt-pocock/ 目录存在');

// 2. 检查 Superpowers 技能文件（6 个）
console.log('\n── Superpowers 技能文件（执行层，6 个）──');
const superpowersSkills = [
  'brainstorming.md',
  'requesting-code-review.md',
  'test-driven-development.md',
  'systematic-debugging.md',
  'verification-before-completion.md',
  'subagent-driven-development.md',
];
for (const skill of superpowersSkills) {
  const relPath = `.claude/skills/superpowers/${skill}`;
  check(exists(relPath), relPath);
}

// 3. 检查 Matt Pocock 技能文件（4 个）
console.log('\n── Matt Pocock 技能文件（诊断层，4 个）──');
const mattPocockSkills = [
  'grill-with-docs.md',
  'diagnosing-bugs.md',
  'improve-codebase-architecture.md',
  'domain-modeling.md',
];
for (const skill of mattPocockSkills) {
  const relPath = `.claude/skills/matt-pocock/${skill}`;
  check(exists(relPath), relPath);
}

// 4. 检查 settings.json 中的技能配置
console.log('\n── settings.json 配置 ──');
if (exists('.claude/settings.json')) {
  try {
    const settings = JSON.parse(read('.claude/settings.json'));
    check(settings.skill_framework === 'three-layer', 'skill_framework = three-layer');
    check(settings.skills && settings.skills.length === 10, `skills 数量 = 10（实际 ${settings.skills ? settings.skills.length : 0}）`);

    // 检查每个 skill 的 file 路径指向实际存在的文件
    if (settings.skills) {
      for (const skill of settings.skills) {
        check(exists(skill.file), `${skill.name} → ${skill.file} 存在`);
      }
    }
  } catch (e) {
    check(false, 'settings.json 解析成功（错误：' + e.message + '）');
  }
} else {
  check(false, 'settings.json 存在');
}

// 5. 检查 .trellis/ 核心文件
console.log('\n── .trellis/ 核心文件 ──');
check(exists('.trellis/workflow.md'), '.trellis/workflow.md 存在');
check(exists('.trellis/scripts/setup-matt-pocock-skills.md'), '.trellis/scripts/setup-matt-pocock-skills.md 存在');

const specFiles = [
  'architecture.md',
  'code-architecture.md',
  'cost-optimization.md',
  'memory-layer.md',
  'ui-design-system.md',
  'app-store-compliance.md',
  'api-conventions.md',
  'app-startup-timing.md',
  'frontend-sqlite-schema.md',
  'domain-configuration.md',
];
console.log('\n── .trellis/spec/ 规范文件 ──');
for (const spec of specFiles) {
  check(exists(`.trellis/spec/${spec}`), spec);
}

// 6. 检查 workflow.md 与 settings.json 的一致性
console.log('\n── workflow.md 一致性 ──');
if (exists('.trellis/workflow.md')) {
  const wf = read('.trellis/workflow.md');
  check(wf.includes('brainstorming'), 'workflow.md 引用 brainstorming');
  check(wf.includes('requesting-code-review'), 'workflow.md 引用 requesting-code-review');
  check(wf.includes('systematic-debugging'), 'workflow.md 引用 systematic-debugging');
  check(wf.includes('verification-before-completion'), 'workflow.md 引用 verification-before-completion');
  check(wf.includes('subagent-driven-development'), 'workflow.md 引用 subagent-driven-development');
  check(wf.includes('grill-with-docs'), 'workflow.md 引用 grill-with-docs');
  check(wf.includes('diagnosing-bugs'), 'workflow.md 引用 diagnosing-bugs');
  check(wf.includes('improve-codebase-architecture'), 'workflow.md 引用 improve-codebase-architecture');
  check(wf.includes('domain-modeling'), 'workflow.md 引用 domain-modeling');
  check(!wf.includes('/trellis:check'), 'workflow.md 不再使用 /trellis:check（已替换为 requesting-code-review）');
}

// 7. 检查 CLAUDE.md 不再包含完整框架定义
console.log('\n── CLAUDE.md 去重检查 ──');
if (exists('CLAUDE.md')) {
  const claude = read('CLAUDE.md');
  check(claude.includes('参见') && claude.includes('workflow.md'), 'CLAUDE.md 引用 workflow.md');
  check(!claude.includes('### 架构总览'), 'CLAUDE.md 不再包含架构总览（已移至 workflow.md）');
  check(!claude.includes('### 各层职责'), 'CLAUDE.md 不再包含各层职责表（已移至 workflow.md）');
}

// 8. 检查 spec 文件无内容重叠
console.log('\n── spec 文件边界检查 ──');
if (exists('.trellis/spec/cost-optimization.md') && exists('.trellis/spec/memory-layer.md')) {
  const cost = read('.trellis/spec/cost-optimization.md');
  const mem = read('.trellis/spec/memory-layer.md');

  check(!cost.includes('| `values`（价值观） | 365 天 | 核心信念'), 'cost-optimization.md §3.2 不再包含完整 TTL 表（已引用 memory-layer.md）');
  check(cost.includes('memory-layer.md'), 'cost-optimization.md 引用 memory-layer.md');
  check(mem.includes('唯一权威'), 'memory-layer.md §8 标注为唯一权威来源');
}

if (exists('.trellis/spec/architecture.md') && exists('.trellis/spec/domain-configuration.md')) {
  const arch = read('.trellis/spec/architecture.md');
  check(arch.includes('domain-configuration.md'), 'architecture.md §2.4 引用 domain-configuration.md');
  check(!arch.includes('**架构决策**'), 'architecture.md §2.4 不再包含详细架构决策（已移至 domain-configuration.md）');
}

// 9. 检查任务状态
console.log('\n── 任务状态 ──');
const taskDirs = [
  'P0-F01-ai-daily-message', 'P0-F02-memory-layer', 'P0-F03-mood-checkin',
  'P0-F04-reflection-card', 'P0-F05-growth-dashboard', 'P0-F06-personality-profile',
  'P0-F07-growth-record',
  'P1-F01-push-notification', 'P1-F02-pro-subscription', 'P1-F03-advanced-insights',
  'P1-F04-social-export', 'P1-F05-onboarding',
];
let pendingCount = 0;
let otherCount = 0;
for (const dir of taskDirs) {
  const ctxPath = path.join(BASE, `.trellis/tasks/${dir}/context.json`);
  if (fs.existsSync(ctxPath)) {
    try {
      const ctx = JSON.parse(fs.readFileSync(ctxPath, 'utf-8'));
      if (ctx.status === 'pending') pendingCount++;
      else otherCount++;
    } catch (e) {}
  }
}
check(pendingCount + otherCount === 12, `任务文件数量 = 12（实际 ${pendingCount + otherCount}）`);
check(pendingCount === 12, `所有任务状态为 pending（${pendingCount} pending, ${otherCount} 其他）— P0 阶段尚未开始，状态正确`);

// 10. 检查协作配置
console.log('\n── 双人协作配置 ──');
check(exists('.github/CODEOWNERS'), '.github/CODEOWNERS 存在');
check(exists('CONTRIBUTING.md'), 'CONTRIBUTING.md 存在');

// 结果
console.log('\n' + '═'.repeat(50));
console.log(`结果：${passed} 通过，${failed} 失败`);
if (failed > 0) {
  console.log('\n❌ 失败项：');
  for (const err of errors) {
    console.log(`   - ${err}`);
  }
  process.exit(1);
} else {
  console.log('\n✅ 全部通过！三层技能框架配置完整。');
}
