const reply = await client.chat.completions.create({ model: "gpt-4o-mini", messages: [{ role: "user", content: "Hi" }] });
