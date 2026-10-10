// Synthetic LLM-06 fixture: unsupported language. Never executed.
const replies = await Promise.all(questions.map((q) => client.messages.create({ model: "claude-sonnet-4-5", system, messages: q })));
