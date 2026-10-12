// Tests for the transcript line-up (ui/js/studio2.js). Run: node tools/tests/test_studio2.mjs
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const code = fs.readFileSync(new URL('../../ui/js/studio2.js', import.meta.url), 'utf8');
const ctx = {console, atob};vm.createContext(ctx);vm.runInContext(code + '\n;globalThis.alignTranscript=alignTranscript;globalThis.quietest=quietest;globalThis.anaSet=anaSet;globalThis.anaPauseCuts=anaPauseCuts;globalThis.anaMissedSounds=anaMissedSounds;globalThis.ytTime=ytTime;', ctx);
const {alignTranscript} = ctx;

// a long "talk": owner's exact text, and what the recogniser heard (with typical mistakes)
let seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
const vocab = 'the heart is the field of action for the divine light and transmission flows from the master into the abhyasi who meditates with love devotion and surrender every morning evening constant remembrance purifies the mind samskaras dissolve slowly Babuji said that real life begins when we turn inward towards the ultimate reality'.split(' ');
const owner = []; for (let i = 0; i < 9000; i++) owner.push(vocab[Math.floor(rnd() * vocab.length)] + (rnd() < 0.08 ? '.' : rnd() < 0.05 ? ',' : ''));
const asr = []; let t = 0.5; const truth = [];
owner.forEach((w, i) => {
  if (rnd() < 0.01) { asr.push({w: 'um', s: t, e: t + 0.3}); t += 0.4; }           // filler not in owner's text
  if (rnd() < 0.005) { t += 0.3; truth.push({s: t - 0.3, e: t}); return; }          // recogniser missed the word
  const d = 0.15 + rnd() * 0.3; let heard = w.replace(/[.,]/g, '');
  if (rnd() < 0.02) heard = heard.slice(0, -1) + 'x';                                // mis-heard (near)
  if (rnd() < 0.01) heard = 'banana';                                                // mis-heard (different)
  if (i % 3 === 0 && rnd() < 0.05) heard = heard.charAt(0).toUpperCase() + heard.slice(1);
  asr.push({w: heard, s: t, e: t + d}); truth.push({s: t, e: t + d}); t += d + 0.05 + (rnd() < 0.1 ? 0.6 : 0);
});
const t0 = Date.now();
const r = alignTranscript(owner.join(' '), asr);
const ms = Date.now() - t0;
const ownerOut = r.words.filter(w => !w.x);
assert.equal(ownerOut.length, owner.length, 'every owner word is kept, in order');
assert.equal(JSON.stringify(ownerOut.map(w => w.w)), JSON.stringify(owner), 'exact owner wording');
const pct = 100 * r.matched / r.total;
assert.ok(pct >= 97, `matched ${pct.toFixed(2)}%`);
let bad = 0; ownerOut.forEach((w, i) => { if (Math.abs(w.s - truth[i].s) > 0.25) bad++; });
assert.ok(bad / owner.length < 0.003, `${bad} words with wrong time`);
assert.ok(r.extra >= 80, 'fillers found as extra words');
assert.ok(r.words.filter(w => w.x).every(w => w.w === 'um' || w.w === 'banana'), 'only real extras flagged');
for (let i = 1; i < r.words.length; i++) assert.ok(r.words[i].s >= r.words[i - 1].s - 1e-9, 'times in order');
assert.ok(ms < 5000, `took ${ms} ms`);
console.log(`line-up: ${owner.length} words in ${ms} ms, matched ${pct.toFixed(2)}%, unsure ${r.unsure}, extra ${r.extra}, wrong-time ${bad}`);

// sound map: quietest point, pauses by what you hear, keep breaths, [sound] items
const env = new Uint8Array(1000).fill(90);               // 10 s of "speech" at -10 dB
for (let i = 300; i < 600; i++) env[i] = 20;              // 3.0-6.0 s silence (-80 dB)
env[450] = 5;                                             // quietest moment at 4.5 s
for (let i = 520; i < 560; i++) env[i] = 45;              // a breath 5.2-5.6 s
const b64 = Buffer.from(env).toString('base64');
ctx.anaSet({env_b64: b64, speech: [[0, 3], [6, 10]], breaths: [[5.2, 5.6]], noise_floor_db: -80});
assert.ok(Math.abs(ctx.quietest(3.5, 5) - 4.505) < 0.011, 'quietest point');
let cuts = ctx.anaPauseCuts(1.0, false);
assert.equal(cuts.length, 1); assert.ok(cuts[0][0] > 3.3 && cuts[0][0] < 3.65 && cuts[0][1] > 5.35 && cuts[0][1] < 5.7, JSON.stringify(cuts));
cuts = ctx.anaPauseCuts(1.0, true);
assert.ok(cuts[0][1] <= 5.2, 'breath kept: ' + JSON.stringify(cuts));
ctx.anaSet({env_b64: b64, speech: [[0, 1], [1.4, 1.9], [2.2, 3]], breaths: [], noise_floor_db: -80});
const snd = ctx.anaMissedSounds([{w: 'a', s: 0.1, e: 0.9}, {w: 'b', s: 2.3, e: 2.9}]);
assert.equal(snd.length, 1); assert.ok(Math.abs(snd[0].s - 1.4) < 1e-9 && snd[0].snd === 1);
assert.equal(ctx.ytTime(3725.9), '1:02:05'); assert.equal(ctx.ytTime(65), '1:05');
console.log('sound map + chapters time format: OK');
