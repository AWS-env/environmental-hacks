// Synthetic fixture for LLM-18: a TypeScript agent with a Bedrock client in another Region.
import { BedrockRuntimeClient, ConverseCommand } from "@aws-sdk/client-bedrock-runtime";
import { S3Client } from "@aws-sdk/client-s3";

const assets = new S3Client({ region: "eu-central-1" });

export const bedrock = new BedrockRuntimeClient({
  maxAttempts: 3,
  region: "eu-central-1",
});

export async function ask(prompt: string) {
  return bedrock.send(new ConverseCommand({ modelId: "amazon.nova-lite-v1:0", messages: [] }));
}
