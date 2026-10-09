function parseOrNull(text) {
  try {
    if (!text.startsWith('{')) throw new Error('not json');
    return JSON.parse(text);
  } catch (e) {
    return null;
  }
}

const inputs = ['{"a":1}', 'plain text', '{"b":2}', 'nope'];
const end = Date.now() + 2500;
let sink = 0;
while (Date.now() < end) {
  for (let i = 0; i < 4000; i++) sink += parseOrNull(inputs[i % 4]) ? 1 : 0;
}
console.log('done', sink);
