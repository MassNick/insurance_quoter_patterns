data "azurerm_client_config" "current" {}

locals {
  loc    = lookup({ canadacentral = "cc", canadaeast = "ce" }, var.location, substr(var.location, 0, 4))
  suffix = "${var.workload}-${var.environment}-${local.loc}"
  flat   = replace(local.suffix, "-", "")
  uniq   = substr(sha256("${var.subscription_id}/${local.suffix}"), 0, 4) # keeps globally unique names stable per subscription
  prod   = var.environment == "prod"
  tags   = merge({ workload = var.workload, environment = var.environment, managed_by = "terraform" }, var.tags)
  ops    = length(var.operator_ips) > 0 # dev: the deployer's IPs may reach the registry, Foundry, Search and kb-docs

  # Models come from settings.yaml (one file for models, thinking level and structured outputs); tfvars may override.
  settings = yamldecode(file("${path.module}/../../settings.yaml"))
  model_deployments = var.model_deployments != null ? var.model_deployments : {
    for k, d in local.settings.deployments : k => {
      model_name = d.model, model_version = d.version, model_format = "OpenAI", sku_name = d.sku, capacity = d.capacity
    }
  }
  model_roles = var.model_roles != null ? var.model_roles : {
    main      = local.settings.agents.main.deployment
    memory    = local.settings.agents.memory.deployment
    document  = local.settings.agents.document.deployment
    embedding = local.settings.knowledge.embedding_deployment
  }

  name = {
    foundry = "aif-${local.suffix}-${local.uniq}"
    kv      = "kv-${local.suffix}-${local.uniq}"
    search  = "srch-${local.suffix}-${local.uniq}"
    acr     = "cr${local.flat}${local.uniq}"
    cosmos  = { app = "cosmos-${local.suffix}-${local.uniq}", foundry = "cosmos-fdy-${local.suffix}-${local.uniq}" }
    storage = { kb = "st${local.flat}${local.uniq}kb", uploads = "st${local.flat}${local.uniq}up", foundry = "st${local.flat}${local.uniq}fdy" }
  }

  # Built-in role definition GUIDs (architecture section 11). GUIDs, not names: the Foundry roles were renamed in 2026.
  role_guids = {
    acr_pull                      = "7f951dda-4ed3-4680-a7ca-43fe172d538d"
    acr_push                      = "8311e382-0749-4cb8-b61a-304f252e45ec"
    container_apps_contributor    = "358470bc-b998-42bd-ab17-a7e34c199c0f"
    cosmos_db_operator            = "230815da-be43-4aae-9cb4-875f7bd000aa"
    cognitive_services_user       = "a97b65f3-24c7-4388-baec-2e87135dc908"
    cognitive_services_openai     = "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd"
    foundry_agent_consumer        = "eed3b665-ab3a-47b6-8f48-c9382fb1dad6"
    foundry_user                  = "53ca6127-db72-4b80-b1b0-d745d6d5456d"
    foundry_project_manager       = "eadc314b-1a2d-4efa-be10-5d325db5065e"
    key_vault_secrets_user        = "4633458b-17de-408a-b874-0445c86b69e6"
    log_analytics_reader          = "73c42c96-874c-492b-b04d-ab87d138a893"
    search_index_data_reader      = "1407120a-92aa-4202-b7e9-c0e197c71c8f"
    search_index_data_contributor = "8ebe5a00-799e-43f5-93ac-243d3dce84a7"
    search_service_contributor    = "7ca78c08-252a-4471-8644-bb5ff32d4ba0"
    storage_account_contributor   = "17d1049b-9a84-46fb-8f53-869881c3d3ab"
    storage_blob_data_contributor = "ba92f5b4-2d11-453d-a403-e96b0029c9fe"
    storage_blob_data_owner       = "b7e6dc6d-f1e8-4753-8033-0f276bb0955b"
    storage_blob_data_reader      = "2a2b9908-6ea1-4ae2-8e65-a410df84e7d1"
  }
  role_ids = { for k, g in local.role_guids : k => "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/${g}" }

  # ---- Container apps: one definition per app, rendered by a single resource (apps.tf) ----
  mcp_audience = { for k in ["mcp-quoting", "mcp-a2ui"] : k => "api://${data.azurerm_client_config.current.tenant_id}/${var.workload}-${var.environment}-${k}" } # app registration id URIs (entra/ stack)
  app_keys     = concat(["bff", "mcp-quoting", "mcp-a2ui"], var.deploy_web ? ["web"] : [])
  app_name     = { for k in local.app_keys : k => "ca-${k}-${local.suffix}" }
  fqdn         = var.deploy_apps ? { for k, n in local.app_name : k => "${n}.${azurerm_container_app_environment.this.default_domain}" } : {}

  # Key Vault secrets (random, written in core.tf). Container Apps reference them by URL; the apps' identities read them.
  kv_secrets = toset(["session-ref-key", "session-cookie-key"])
  secret_url = { for k in local.kv_secrets : k => "${azurerm_key_vault.this.vault_uri}secrets/${k}" } # versionless: the latest version

  # The tokens for the MCP servers are requested with the MCP app registration's id URI.
  mcp_scope = { for k, a in local.mcp_audience : k => "${a}/.default" }
  # What each MCP server accepts as aud: the id URI and, once the entra stack is applied (var.mcp_client_ids), the client id (v1 tokens carry it).
  mcp_aud = { for k, a in local.mcp_audience : k => join(",", compact([a, lookup(var.mcp_client_ids, k, "")])) }
  mcp_env = {
    ENTRA_TENANT_ID             = data.azurerm_client_config.current.tenant_id
    ENTRA_ALLOWED_PRINCIPAL_IDS = join(",", [azurerm_user_assigned_identity.app["bff"].principal_id, module.foundry.project_principal_id])
  }

  # mcp-quoting adapters: each is a stub until a real base URL is given (solution/mcp/quoting/README.md, "Environment variables").
  quoting_adapters = {
    rating  = { switch = "RATING_ENGINE", prefix = "RATING" }
    catalog = { switch = "VEHICLE_CATALOG", prefix = "CATALOG" }
    address = { switch = "ADDRESS_SERVICE", prefix = "ADDRESS" }
    vin     = { switch = "VIN_DECODER", prefix = "VIN" }
  }
  quoting_env = merge(
    { for k, a in local.quoting_adapters : a.switch => contains(keys(var.quoting.backends), k) ? "http" : "stub" },
    { for k, b in var.quoting.backends : "${local.quoting_adapters[k].prefix}_BASE_URL" => b.base_url },
    { for k, b in var.quoting.backends : "${local.quoting_adapters[k].prefix}_SCOPE" => b.scope if b.scope != null },
    {
      HTTP_TIMEOUT_SECONDS = tostring(var.quoting.http_timeout_seconds)
      HTTP_RETRIES         = tostring(var.quoting.http_retries)
      STATE_READER         = "cosmos" # committed state from Cosmos, read-only
      COSMOS_ENDPOINT      = azurerm_cosmosdb_account.this["app"].endpoint
      COSMOS_DATABASE      = azurerm_cosmosdb_sql_database.app.name
      COSMOS_CONTAINER     = "sessions"
    },
  )

  app_defs = {
    bff = {
      port = 8000, health = "/api/health", cpu = 1, memory = "2Gi", affinity = false, concurrency = 100 # no sticky sessions: ACA allows them only with a single revision, and the canary needs Multiple; session state is in Cosmos
      min  = local.prod ? 2 : 1, max = local.prod ? 20 : 3
      env = {
        FOUNDRY_PROJECT_ENDPOINT = module.foundry.project_endpoint
        HOSTED_AGENT_NAME        = "quote-agent"
        DOCUMENT_AGENT_NAME      = "document-agent"
        A2UI_MCP_URL             = try("https://${local.fqdn["mcp-a2ui"]}/mcp", "")
        A2UI_MCP_SCOPE           = local.mcp_scope["mcp-a2ui"]
        QUOTING_MCP_URL          = try("https://${local.fqdn["mcp-quoting"]}/mcp", "")
        QUOTING_MCP_SCOPE        = local.mcp_scope["mcp-quoting"]
        COSMOS_ENDPOINT          = azurerm_cosmosdb_account.this["app"].endpoint
        COSMOS_DATABASE          = azurerm_cosmosdb_sql_database.app.name
        BLOB_ACCOUNT_URL         = azurerm_storage_account.this["uploads"].primary_blob_endpoint
        BLOB_CONTAINER           = "uploads"
        CORS_ORIGINS             = "https://same-origin.invalid" # Front Door serves the web app and /api from one host: CORS stays closed
        OTEL_ENABLED             = "true"
      }
      secrets = { SESSION_REF_KEY = local.secret_url["session-ref-key"], SESSION_COOKIE_KEY = local.secret_url["session-cookie-key"] }
    }
    mcp-quoting = {
      # One replica: its quote store is in memory, so a second replica would not know the first one's quotes (README, QuoteStore).
      port    = 8101, health = "/healthz", cpu = 0.5, memory = "1Gi", affinity = false, concurrency = 50
      min     = 1, max = 1
      env     = merge(local.mcp_env, local.quoting_env, { ENTRA_AUDIENCE = local.mcp_aud["mcp-quoting"] })
      secrets = { SESSION_REF_KEY = local.secret_url["session-ref-key"] } # the same secret as the BFF's
    }
    mcp-a2ui = {
      port    = 8102, health = "/healthz", cpu = 0.5, memory = "1Gi", affinity = false, concurrency = 50
      min     = 1, max = local.prod ? 10 : 2
      env     = merge(local.mcp_env, { ENTRA_AUDIENCE = local.mcp_aud["mcp-a2ui"] }) # no data access: the catalog is baked into the image
      secrets = {}
    }
    web = {
      port    = 8080, health = "/", cpu = 0.25, memory = "0.5Gi", affinity = false, concurrency = 100
      min     = 1, max = local.prod ? 5 : 2
      env     = {}
      secrets = {}
    }
  }
  apps = var.deploy_apps ? { for k in local.app_keys : k => local.app_defs[k] } : {}

  # Front Door: the API goes through the BFF origin (no cache, SSE), the static site through web (cached).
  fd_routes = {
    bff = { patterns = ["/api/*"], cache = false }
    web = { patterns = ["/*"], cache = true }
  }

  # Hosted-agent egress allow-list (architecture section 12): Foundry (models, toolbox), the knowledge base, Cosmos, Application Insights.
  egress_hosts = [
    "${local.name.foundry}.services.ai.azure.com",
    "${local.name.search}.search.windows.net",
    "${local.name.cosmos["app"]}.documents.azure.com",
    "${local.name.cosmos["app"]}-${var.location}.documents.azure.com",
    "*.applicationinsights.azure.com",
  ]
}
