// Synthetic LLM-13 fixture: unsupported language. Never executed.
await redis.set(prompt, (await client.chat.completions.create({ model: "gpt-5.5", messages })).choices[0].message.content);
