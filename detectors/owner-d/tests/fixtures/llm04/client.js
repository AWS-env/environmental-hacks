// Synthetic LLM-04 fixture: unsupported language. Never executed.
const first = await client.chat.completions.create({ model: "gpt-4.1", messages });
const second = await client.chat.completions.create({ model: "gpt-4.1", messages: [...messages, first] });
