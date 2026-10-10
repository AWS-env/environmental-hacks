# Retrieval service (synthetic LLM-18 fixture)
provider "aws" {
  region = "eu-central-1"
}

provider "aws" {
  alias  = "edge"
  region = "us-east-1"
}

resource "azurerm_cognitive_account" "openai" {
  name                = "rag-openai"
  location            = "eastus"
  resource_group_name = "rag"
  kind                = "OpenAI"
  sku_name            = "S0"
}

resource "aws_ssm_parameter" "model_endpoint" {
  name  = "/rag/model-endpoint"
  type  = "String"
  value = "https://bedrock-runtime.eu-central-1.amazonaws.com"
}
