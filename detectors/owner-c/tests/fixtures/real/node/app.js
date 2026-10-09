const fs = require('node:fs');

function lookup(items, wanted) {
  let hits = 0;
  for (const p of wanted) {
    if (items.includes(p)) hits += 1;
  }
  return hits;
}

function cloneRows(rows) {
  return rows.map((r) => JSON.parse(JSON.stringify(r)));
}

function readSelf() {
  let total = 0;
  for (let i = 0; i < 3000; i++) total += fs.readFileSync(__filename, 'utf8').length;
  return total;
}

function names(rows) {
  return rows.filter((r) => r.id % 2 === 0).map((r) => r.name.toUpperCase()).map((s) => s + '!');
}

function ids(rows) {
  return rows.reduce((acc, r) => [...acc, r.id], []);
}

function format(n) {
  return new Intl.NumberFormat('en-US').format(n);
}

function parseOrNull(text) {
  try {
    if (!text.startsWith('{')) throw new Error('not json');
    return JSON.parse(text);
  } catch (e) {
    return null;
  }
}

const items = Array.from({ length: 20000 }, (_, i) => i);
const wanted = Array.from({ length: 10000 }, (_, i) => i * 2);
const rows = Array.from({ length: 8000 }, (_, i) => ({ id: i, name: 'row' + i, tags: ['a', 'b', 'c'] }));
const inputs = ['{"a":1}', 'plain text', '{"b":2}', 'nope'];

const end = Date.now() + 3000;
let sink = 0;
while (Date.now() < end) {
  sink += lookup(items, wanted);
  sink += cloneRows(rows).length + cloneRows(rows).length;
  sink += readSelf();
  for (let k = 0; k < 24; k++) sink += names(rows).length;
  sink += ids(rows.slice(0, 2500)).length;
  for (let i = 0; i < 2000; i++) sink += format(i).length;
  for (let i = 0; i < 60000; i++) sink += parseOrNull(inputs[i % 4]) ? 1 : 0;
}
console.log('done', sink);
