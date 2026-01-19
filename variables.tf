# Toggle for DHCP Option Set
variable "enable_dhcp_option_set" {
  description = "If false, do not create or associate a DHCP option set."
  type        = bool
  default     = true
}
# DHCP Options variables
variable "domain_name" {
  description = "DHCP option: domain_name"
  type        = string
  default     = "ec2.internal"
}
variable "domain_name_servers" {
  description = "DHCP option: domain_name_servers"
  type        = list(string)
  default     = ["AmazonProvidedDNS"]
}
variable "ntp_servers" {
  description = "DHCP option: ntp_servers"
  type        = list(string)
  default     = ["0.0.0.0"]
}
variable "netbios_name_servers" {
  description = "DHCP option: netbios_name_servers"
  type        = list(string)
  default     = ["192.168.1.1"]
}
variable "netbios_node_type" {
  description = "DHCP option: netbios_node_type"
  type        = number
  default     = 2
}
variable "environment" {
  description = "Environment name (e.g., dev, prod)"
  type        = string
  default     = "dev"
}
variable "base_tag" {
  description = "Common tags for all resources (Region, application, vpc_name, created by)"
  type        = map(string)
}


variable "vpc_name" {
  description = "Name to assign to the VPC (e.g., ntw-dev-vpc1)"
  type        = string
  default     = "ntw-dev-vpc1"
}


variable "region" {
  description = "AWS region to deploy resources"
  type        = string
}

variable "vpc_cidr" {
  description = "Primary CIDR block for the VPC"
  type        = string
}

variable "additional_cidrs" {
  description = "Additional CIDR blocks to associate with the VPC"
  type        = list(string)
}

variable "enable_additional_cidrs" {
  description = "If false, do not associate any additional CIDRs with the VPC (ignores additional_cidrs)."
  type        = bool
  default     = true
}

variable "public_subnet_cidrs" {
  description = "List of CIDR blocks for public subnets"
  type        = list(string)
}

variable "enable_public_subnets" {
  description = "If false, do not create public subnets (ignores public_subnet_cidrs)."
  type        = bool
  default     = true
}

variable "private_subnet_cidrs" {
  description = "List of CIDR blocks for private subnets"
  type        = list(string)
}

variable "enable_private_subnets" {
  description = "If false, do not create private subnets (ignores private_subnet_cidrs)."
  type        = bool
  default     = true
}

variable "nonroutable_subnet_cidrs" {
  description = "List of CIDR blocks for non-routable subnets"
  type        = list(string)
}

variable "enable_nonroutable_subnets" {
  description = "If false, do not create non-routable subnets (ignores nonroutable_subnet_cidrs)."
  type        = bool
  default     = true
}

variable "enable_gateways" {
  description = "Master toggle for gateways module (IGW + NATs)."
  type        = bool
  default     = true
}

variable "enable_internet_gateway" {
  description = "If false, do not create an Internet Gateway (IGW)."
  type        = bool
  default     = true
}

variable "enable_public_nat_gateways" {
  description = "If false, do not create public NAT gateways used by private subnets."
  type        = bool
  default     = true
}

variable "enable_private_nat_gateways" {
  description = "If false, do not create private NAT gateways used by nonroutable subnets."
  type        = bool
  default     = true
}

variable "enable_public_route_table" {
  description = "If false, do not create/associate the public route table."
  type        = bool
  default     = true
}

variable "enable_private_route_tables" {
  description = "If false, do not create private route tables."
  type        = bool
  default     = true
}

variable "enable_nonroutable_route_tables" {
  description = "If false, do not create nonroutable route tables."
  type        = bool
  default     = true
}

variable "enable_nacls" {
  description = "Master toggle for creating NACLs and their rules."
  type        = bool
  default     = true
}

variable "enable_public_nacl" {
  description = "If false, do not create the public NACL."
  type        = bool
  default     = true
}

variable "enable_private_nonroutable_nacl" {
  description = "If false, do not create the combined private/nonroutable NACL."
  type        = bool
  default     = true
}

variable "nacl_rules" {
  description = "Configurable NACL rules. Empty lists mean no rules (except defaults if you keep them in tfvars)."
  type = object({
    public_ingress  = list(object({ rule_number = number, protocol = string, rule_action = string, cidr_block = optional(string), ipv6_cidr_block = optional(string), from_port = number, to_port = number }))
    public_egress   = list(object({ rule_number = number, protocol = string, rule_action = string, cidr_block = optional(string), ipv6_cidr_block = optional(string), from_port = number, to_port = number }))
    private_ingress = list(object({ rule_number = number, protocol = string, rule_action = string, cidr_block = optional(string), ipv6_cidr_block = optional(string), from_port = number, to_port = number }))
    private_egress  = list(object({ rule_number = number, protocol = string, rule_action = string, cidr_block = optional(string), ipv6_cidr_block = optional(string), from_port = number, to_port = number }))
  })
  default = {
    public_ingress = []
    public_egress  = []
    # Preserve existing behavior: allow all inbound from 10.0.0.0/8 on the private/nonroutable NACL.
    private_ingress = [{ rule_number = 100, protocol = "-1", rule_action = "allow", cidr_block = "10.0.0.0/8", from_port = 0, to_port = 0 }]
    private_egress  = []
  }
}

variable "enable_s3_gateway_endpoint" {
  description = "If true, create the S3 Gateway VPC endpoint (requires private/nonroutable route tables)."
  type        = bool
  default     = true
}

variable "enable_interface_endpoints" {
  description = "If true, create interface endpoints (EC2 + SSM) in private/nonroutable subnets."
  type        = bool
  default     = true
}

variable "enable_vpc_endpoints_sg" {
  description = "If true, create a default security group for interface endpoints."
  type        = bool
  default     = true
}

variable "vpc_endpoints_security_group_ids" {
  description = "If provided, use these security group IDs for interface endpoints (used when you don't want Terraform to create one)."
  type        = list(string)
  default     = []
}

variable "extra_security_groups" {
  description = "Additional security groups to create in this VPC (keyed map) and manage via tfvars."
  type = map(object({
    name        = string
    description = string
    ingress_rules = optional(list(object({
      from_port                = number
      to_port                  = number
      protocol                 = string
      cidr_blocks              = optional(list(string), [])
      source_security_group_id = optional(string)
      description              = optional(string)
    })), [])
    egress_rules = optional(list(object({
      from_port                = number
      to_port                  = number
      protocol                 = string
      cidr_blocks              = optional(list(string), [])
      source_security_group_id = optional(string)
      description              = optional(string)
    })), [])
    tags = optional(map(string), {})
  }))
  default = {}
}

variable "public_extra_routes" {
  description = "Additional routes to add to the public route table."
  type = list(object({
    destination_cidr_block = string
    target_type            = string # gateway_id | nat_gateway_id | transit_gateway_id | vpc_peering_connection_id | vpc_endpoint_id | network_interface_id
    target_id              = string
  }))
  default = []
}

variable "private_extra_routes" {
  description = "Additional routes to add to ALL private route tables."
  type = list(object({
    destination_cidr_block = string
    target_type            = string
    target_id              = string
  }))
  default = []
}

variable "nonroutable_extra_routes" {
  description = "Additional routes to add to ALL nonroutable route tables."
  type = list(object({
    destination_cidr_block = string
    target_type            = string
    target_id              = string
  }))
  default = []
}

variable "azs" {
  description = "List of Availability Zones to spread subnets across"
  type        = list(string)
}

variable "enable_private_onprem_route" {
  description = "If true, add an additional route to all private route tables (VPC-specific via tfvars)."
  type        = bool
  default     = false
}

variable "private_onprem_destination_cidr" {
  description = "Destination CIDR for the optional private on-prem route."
  type        = string
  default     = "10.0.0.0/8"
}

variable "interface_vpc_endpoints" {
  description = "Map of interface VPC endpoints to create. Key = service name (e.g., 'ec2', 'ssm', 'ssmmessages')"
  type = map(object({
    private_dns_enabled = optional(bool, true)
    tags                = optional(map(string), {})
  }))
  default = {}
}
# end#