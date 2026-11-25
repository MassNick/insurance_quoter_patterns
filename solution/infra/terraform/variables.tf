variable "subscription_id" {
  description = "Target subscription (one per environment)."
  type        = string
}

variable "environment" {
  description = "dev, test or prod. Drives names and the prod-only settings (zone redundancy, replicas, purge protection)."
  type        = string
  validation {
    condition     = contains(["dev", "test", "prod"], var.environment)
    error_message = "environment must be dev, test or prod."
  }
}

variable "workload" {
  description = "Short workload code used in every resource name."
  type        = string
  default     = "aiq"
}

variable "location" {
  description = "Primary region. The Foundry account, its agent subnet and the VNet must share it. Canada Central primary, Canada East for disaster recovery (D11)."
  type        = string
  default     = "canadacentral"
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "vnet_cidr" {
  description = "VNet range. Subnets are carved from it: aca-infra /23, private-endpoints /24, agent-egress /24 (RFC 1918, away from 192.168.0.0/24 and 172.30-31.x)."
  type        = string
  default     = "10.40.0.0/16"
}

variable "enable_nat_gateway" {
  description = "Fixed outbound IP for the Container Apps subnet (rating engine and address provider allow-lists)."
  type        = bool
  default     = false
}

# ---- Models: read from settings.yaml at the solution root (deployments, agents.*.deployment). Set these only to override it. ----

variable "model_deployments" {
  description = <<-EOT
    Overrides settings.yaml `deployments`: Foundry model deployments, keyed by deployment name. Region is the account
    region (var.location); processing location follows sku_name (Standard = regional, DataZoneStandard / GlobalStandard = wider).
  EOT
  default     = null
  type = map(object({
    model_name    = string
    model_version = string
    model_format  = optional(string, "OpenAI")
    sku_name      = string
    capacity      = number
  }))
}

variable "model_roles" {
  description = "Overrides settings.yaml: which deployment (a key of the deployments) plays each role. Returned by the model_deployments output (role to deployment and model name); deploy/post_deploy.sh maps it to MAIN_MODEL_DEPLOYMENT and the other model variables."
  default     = null
  type = object({
    main      = string
    memory    = string
    document  = string
    embedding = string
  })
  validation {
    condition     = var.model_roles == null || var.model_deployments == null || alltrue([for d in values(var.model_roles) : contains(keys(var.model_deployments), d)])
    error_message = "Every model_roles value must be a key of model_deployments."
  }
}

variable "agent_egress_mode" {
  description = "Hosted-agent network egress policy: Audit first, then Enforced once the audit log shows no legitimate denials."
  type        = string
  default     = "Audit"
  validation {
    condition     = contains(["Audit", "Enforced"], var.agent_egress_mode)
    error_message = "agent_egress_mode must be Audit or Enforced."
  }
}

# ---- Data ----

variable "cosmos_failover_location" {
  description = "Read region for the app Cosmos account (service-managed failover). null = single region."
  type        = string
  default     = null
}

variable "cosmos_containers" {
  description = "App containers in database quoting (all partitioned on /session_id). TTLs are proposals pending compliance review (architecture 6.3)."
  type = map(object({
    default_ttl    = number
    max_throughput = number
  }))
  default = {
    sessions      = { default_ttl = 7776000, max_throughput = 4000 }   # 90 days
    events        = { default_ttl = 31536000, max_throughput = 10000 } # 365 days
    state_changes = { default_ttl = 31536000, max_throughput = 4000 }  # 365 days
    quotes        = { default_ttl = 94608000, max_throughput = 1000 }  # 3 years
    agent_history = { default_ttl = 2592000, max_throughput = 4000 }   # 30 days
    confirmations = { default_ttl = 94608000, max_throughput = 1000 }  # 3 years
    handoffs      = { default_ttl = 94608000, max_throughput = 1000 }  # 3 years
  }
}

variable "search_sku" {
  description = "standard2 or higher is required for private ingestion of the knowledge source (Foundry IQ docs, 2026-11). The design said standard; see README."
  type        = string
  default     = "standard2"
}

variable "search_replicas" {
  type    = number
  default = 1
}

# ---- Container Apps ----

variable "deploy_apps" {
  description = "false on the first apply (the registry is empty), true once the images are pushed."
  type        = bool
  default     = false
}

variable "deploy_web" {
  description = "Also run the web front end as a container app (the design uses Static Web Apps; this keeps everything behind one Front Door origin type)."
  type        = bool
  default     = false
}

variable "app_images" {
  description = "Image per app (bff, mcp-quoting, mcp-a2ui, web) as <registry>/<repo>:<git-sha>. Only used when an app is first created; CD rolls revisions after that."
  type        = map(string)
  default     = {}
  validation {
    condition     = !var.deploy_apps || alltrue([for k in concat(["bff", "mcp-quoting", "mcp-a2ui"], var.deploy_web ? ["web"] : []) : contains(keys(var.app_images), k)])
    error_message = "deploy_apps = true needs an image for each deployed app."
  }
}

variable "mcp_client_ids" {
  description = "Client id of each MCP app registration, by app (mcp-quoting, mcp-a2ui): the output client_ids of the entra stack, known only after it is applied. When set, the app's ENTRA_AUDIENCE is \"<api:// URI>,<client id>\", because v1 tokens carry the client id as aud. Empty: the URI alone."
  type        = map(string)
  default     = {}
  validation {
    condition     = alltrue([for k, v in var.mcp_client_ids : contains(["mcp-quoting", "mcp-a2ui"], k) && can(regex("^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$", lower(v)))])
    error_message = "mcp_client_ids: the keys must be mcp-quoting or mcp-a2ui and the values GUIDs."
  }
}

variable "quoting" {
  description = <<-EOT
    mcp-quoting settings (solution/mcp/quoting/README.md). backends: the real quoting system behind each adapter, by key
    rating, catalog, address or vin; an adapter left out runs on the built-in stub. scope is the Entra scope of that API.
    http_timeout_seconds and http_retries apply to every HTTP adapter (NFR-PERF-5: create_quote under 3 s at p95).
  EOT
  type = object({
    backends             = optional(map(object({ base_url = string, scope = optional(string) })), {})
    http_timeout_seconds = optional(number, 5)
    http_retries         = optional(number, 2)
  })
  default = {}
  validation {
    condition     = alltrue([for k in keys(var.quoting.backends) : contains(["rating", "catalog", "address", "vin"], k)])
    error_message = "quoting.backends keys must be rating, catalog, address or vin."
  }
}

# ---- Principals (all optional) ----

variable "cicd_principal_id" {
  description = "Object id of the GitHub OIDC service principal that pushes images, rolls revisions and runs the Foundry data-plane scripts."
  type        = string
  default     = null
}

variable "operators_group_id" {
  description = "Entra group that reads logs and traces (Log Analytics Reader)."
  type        = string
  default     = null
}

variable "quote_agent_principal_id" {
  description = "Principal id of the quote-agent's Entra agent identity (instance_identity). Only exists after the first agent deploy, so apply a second time with it set. A pass that leaves it null removes the agent's role assignments: the CD terraform action passes the previous state's output quote_agent_principal_id unless the tfvars set it."
  type        = string
  default     = null
}

variable "operator_ips" {
  description = <<-EOT
    Dev only: public IPs or CIDRs (for example "203.0.113.7/32") of the people who deploy from their own machine instead of a
    runner in the VNet. When set, the registry, the Foundry account, Search and the kb-docs storage accept those addresses
    (firewall rules, default deny); the apps stay private behind Front Door. Leave empty in prod.
  EOT
  type        = list(string)
  default     = []
  validation {
    condition     = length(var.operator_ips) == 0 || var.environment != "prod"
    error_message = "operator_ips is for dev only; prod deploys from a runner inside the VNet."
  }
}

variable "cosmos_location" {
  description = "Region of the two Cosmos accounts when it must differ from location (for example canadaeast when Canada Central has no Cosmos capacity). Null = location."
  type        = string
  default     = null
}

variable "cicd_principal_type" {
  description = "Type of cicd_principal_id: ServicePrincipal (the GitHub OIDC identity) or User (dev: a person deploying from a workstation gets the same deploy roles)."
  type        = string
  default     = "ServicePrincipal"
  validation {
    condition     = contains(["ServicePrincipal", "User", "Group"], var.cicd_principal_type)
    error_message = "cicd_principal_type is ServicePrincipal, User or Group."
  }
}
