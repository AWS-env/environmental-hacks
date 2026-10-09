// Synthetic LLM-16 fixture: unsupported language. Never executed.
app.post("/chat", async (req, res) => res.json(await client.chat.completions.create({ model: "gpt-5.5", messages: req.body })));
