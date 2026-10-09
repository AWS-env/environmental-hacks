import Anthropic from "@anthropic-ai/sdk";

const client = new Anthropic();
const message = await client.messages.create({ model: "claude-haiku-4-5", max_tokens: 64, messages: [] });
console.log(message.content[0].text);
