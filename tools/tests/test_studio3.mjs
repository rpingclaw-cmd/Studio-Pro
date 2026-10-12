// Tests for word-by-word Reel captions (ui/js/studio3.js). Run: node tools/tests/test_studio3.mjs
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const post = fs.readFileSync(new URL('../../ui/js/studio.js', import.meta.url), 'utf8');
const pick = re => { const m = post.match(re); assert.ok(m, 'helper not found: ' + re); return m[0]; };
const helpers = [pick(/function mapper\(segs\)\{[^\n]*\n[^\n]*\}\}\}/), pick(/const assT=t=>\{[^\n]*\n?/)].join('\n');
const code = fs.readFileSync(new URL('../../ui/js/studio3.js', import.meta.url), 'utf8');
const ctx = {console, STU: {words: [], removed: {}, settings: {}}};
vm.createContext(ctx);
vm.runInContext(helpers + '\n' + code + '\n;globalThis.makeAssWords=makeAssWords;globalThis.parseTime=parseTime;', ctx);
const words = 'The heart is the field of action for the divine light. Constant remembrance purifies the mind.'.split(' ');
ctx.STU.words = words.map((w, i) => ({w, s: 10 + i * 0.4, e: 10 + i * 0.4 + 0.3}));
ctx.STU.removed = {3: 'user'};          // a cut word must not appear
const ass = ctx.makeAssWords([[9.5, 20]], {style: 'gold'});
const ev = ass.split('\n').filter(l => l.startsWith('Dialogue:'));
assert.equal(ev.length, words.length - 1, 'one event per spoken (kept) word');
assert.ok(!ev.some(l => /\bthe field\b/.test(l.replace(/\{[^}]*\}/g, '')) && /\bis the field\b/.test(l)), 'cut word dropped');
for (const l of ev) {
  assert.equal((l.match(/\\c&H0048AEE0/g) || []).length, 1, 'exactly one highlighted word: ' + l);
  const plain = l.split(',,0,0,0,,')[1].replace(/\{[^}]*\}/g, '');
  assert.ok(plain.split(' ').length <= 3 && plain.length <= 30, 'short lines: ' + plain);
}
const t0 = ev.map(l => l.split(',')[1]);
for (let i = 1; i < t0.length; i++) assert.ok(t0[i] >= t0[i - 1], 'in time order');
assert.ok(/MarginV|,576,/.test(ass), 'safe zone margin');
assert.equal(ctx.parseTime('1:00'), 60); assert.equal(ctx.parseTime('1:02:03'), 3723); assert.ok(isNaN(ctx.parseTime('abc')));
console.log(`captions: ${ev.length} word events OK; time parsing OK`);
