locals {
  additional_cidrs = var.enable_additional_cidrs ? var.additional_cidrs : []

  public_subnet_cidrs      = var.enable_public_subnets ? var.public_subnet_cidrs : []
  private_subnet_cidrs     = var.enable_private_subnets ? var.private_subnet_cidrs : []
  nonroutable_subnet_cidrs = var.enable_nonroutable_subnets ? var.nonroutable_subnet_cidrs : []

  public_subnet_ids      = [for m in values(module.public_subnets) : m.id]
  private_subnet_ids     = [for m in values(module.private_subnets) : m.id]
  nonroutable_subnet_ids = [for m in values(module.nonroutable_subnets) : m.id]

  enable_gateways = var.enable_gateways && (var.enable_internet_gateway || var.enable_public_nat_gateways || var.enable_private_nat_gateways)

  # Public NAT gateways require public subnets. If public subnets are disabled, treat public NATs as disabled too.
  enable_public_nat_gateways_effective = local.enable_gateways && var.enable_public_nat_gateways && var.enable_public_subnets && length(var.public_subnet_cidrs) > 0

  enable_public_route_table       = var.enable_public_route_table && length(local.public_subnet_ids) > 0
  enable_private_route_tables     = var.enable_private_route_tables && length(local.private_subnet_ids) > 0
  enable_nonroutable_route_tables = var.enable_nonroutable_route_tables && length(local.nonroutable_subnet_ids) > 0

  s3_endpoint_route_table_ids = concat(
    [for m in values(module.private_route_tables) : m.route_table_id],
    [for m in values(module.nonroutable_route_tables) : m.route_table_id]
  )

  interface_endpoint_subnet_ids = distinct(compact([for az in var.azs :
    lookup({ for k, s in module.private_subnets : s.availability_zone => s.id }, az, null) != null ? lookup({ for k, s in module.private_subnets : s.availability_zone => s.id }, az, null) : lookup({ for k, s in module.nonroutable_subnets : s.availability_zone => s.id }, az, null)
  ]))

  interface_endpoint_sg_ids = length(var.vpc_endpoints_security_group_ids) > 0 ? var.vpc_endpoints_security_group_ids : (var.enable_vpc_endpoints_sg ? [module.vpc_endpoints_sg[0].security_group_id] : [])
}

# NACLs for public, private, and nonroutable subnets
module "nacls" {
  source              = "./modules/nacls"
  enabled             = var.enable_nacls
  enable_public_nacl  = var.enable_public_nacl
  enable_private_nacl = var.enable_private_nonroutable_nacl

  vpc_id                 = module.vpc.id
  public_subnet_ids      = local.public_subnet_ids
  private_subnet_ids     = local.private_subnet_ids
  nonroutable_subnet_ids = local.nonroutable_subnet_ids

  public_ingress_rules  = var.nacl_rules.public_ingress
  public_egress_rules   = var.nacl_rules.public_egress
  private_ingress_rules = var.nacl_rules.private_ingress
  private_egress_rules  = var.nacl_rules.private_egress

  base_tag = var.base_tag
  vpc_name = var.vpc_name
}
# VPC from child module
module "vpc" {
  source = "./modules/vpc"

  region               = var.region
  cidr_block           = var.vpc_cidr
  additional_cidrs     = local.additional_cidrs
  instance_tenancy     = "default"
  enable_dns_support   = true
  enable_dns_hostnames = true
  base_tag             = var.base_tag
  tags = {
    Name        = var.vpc_name
    Environment = var.vpc_name
  }
}

# Public subnets
module "public_subnets" {
  source     = "./modules/subnets"
  for_each   = toset(local.public_subnet_cidrs)
  depends_on = [module.vpc]

  vpc_id                  = module.vpc.id
  cidr_block              = each.value
  availability_zone       = element(var.azs, index(local.public_subnet_cidrs, each.value))
  map_public_ip_on_launch = true
  region                  = var.region
  name                    = "${var.vpc_name}-pub-subnet-${element(var.azs, index(local.public_subnet_cidrs, each.value))}"
  application             = "ntw"
  created_by              = "Cloud Network Team"
  creation_date           = timestamp()
  tags = {
    Tier = "public"
  }
}

# Private subnets
module "private_subnets" {
  source     = "./modules/subnets"
  for_each   = toset(local.private_subnet_cidrs)
  depends_on = [module.vpc]

  vpc_id            = module.vpc.id
  cidr_block        = each.value
  availability_zone = element(var.azs, index(local.private_subnet_cidrs, each.value))
  region            = var.region
  name              = "${var.vpc_name}-pvt-subnet-${element(var.azs, index(local.private_subnet_cidrs, each.value))}"
  application       = "ntw"
  created_by        = "Cloud Network Team"
  creation_date     = timestamp()
  tags = {
    Tier = "private"
  }
}

# Non‑routable subnets
module "nonroutable_subnets" {
  source     = "./modules/subnets"
  for_each   = toset(local.nonroutable_subnet_cidrs)
  depends_on = [module.vpc]

  vpc_id            = module.vpc.id
  cidr_block        = each.value
  availability_zone = element(var.azs, index(local.nonroutable_subnet_cidrs, each.value))
  region            = var.region
  name              = "${var.vpc_name}-nr-subnet-${element(var.azs, index(local.nonroutable_subnet_cidrs, each.value))}"
  application       = "ntw"
  created_by        = "Cloud Network Team"
  creation_date     = timestamp()
  tags = {
    Tier     = "nonroutable"
    PointsTo = "nat-private-${index(local.nonroutable_subnet_cidrs, each.value) + 1}-${each.value}"
  }
}


# Public route table (single) — associates all public subnets with one RT.
module "public_route_table" {
  source = "./modules/routing-tables"
  count  = local.enable_public_route_table ? 1 : 0
  vpc_id = module.vpc.id
  # default route for internet-bound traffic
  cidr_block = "0.0.0.0/0"
  gateway_id = module.gateways.igw_id
  subnet_ids = local.public_subnet_ids
  depends_on = [module.gateways]

  tags = {
    Name        = "${var.vpc_name}-public-rt"
    Environment = var.vpc_name
    Tier        = "public"
  }
}

# Private route tables — one per private subnet 

module "private_route_tables" {
  source   = "./modules/routing-tables"
  for_each = local.enable_private_route_tables ? toset(local.private_subnet_cidrs) : toset([])

  vpc_id = module.vpc.id

  # default route for private subnets -> their public NAT gateway

  cidr_block = "0.0.0.0/0"
  # During import, NATs are brought in one-by-one; avoid failing refresh when some keys don't exist yet.
  nat_gateway_id = try(module.gateways.public_nat_ids[each.value], null)
  subnet_ids     = [module.private_subnets[each.value].id]
  depends_on     = [module.gateways]

  tags = {
    Name        = "${var.vpc_name}-private-${index(local.private_subnet_cidrs, each.value) + 1}"
    Environment = var.vpc_name
    Tier        = "private"
  }
}

# Nonroutable route tables — one per nonroutable subnet
module "nonroutable_route_tables" {
  source   = "./modules/routing-tables"
  for_each = local.enable_nonroutable_route_tables ? toset(local.nonroutable_subnet_cidrs) : toset([])

  vpc_id = module.vpc.id

  # default route for nonroutable subnets -> private NAT gateway
  cidr_block = "0.0.0.0/0"
  # During import, NATs are brought in one-by-one; avoid failing refresh when some keys don't exist yet.
  nat_gateway_id = try(module.gateways.private_nat_ids[each.value], null)
  subnet_ids     = [module.nonroutable_subnets[each.value].id]
  depends_on     = [module.gateways]

  tags = {
    Name        = "${var.vpc_name}-nonroutable-${index(local.nonroutable_subnet_cidrs, each.value) + 1}"
    Environment = var.vpc_name
    Tier        = "nonroutable"
  }
}

# Gateways: create IGW + NATs
module "gateways" {
  source = "./modules/gateways"

  vpc_id     = module.vpc.id
  create_igw = local.enable_gateways && var.enable_internet_gateway
  tags = {
    Environment = var.vpc_name
    Name        = "${var.vpc_name}-gateways"
  }

  // create one public NAT per private subnet placed in the corresponding public subnet (same index)
  public_nat_configs = local.enable_public_nat_gateways_effective ? {
    for idx, cidr in local.private_subnet_cidrs : cidr => {
      private_subnet_id = module.private_subnets[cidr].id
      public_subnet_id  = module.public_subnets[element(local.public_subnet_cidrs, idx)].id

      availability_mode = "zonal"
      connectivity_type = "public"

      nat_name     = "nat-public-${idx + 1}-${cidr}"
      display_name = "${var.vpc_name}-public-${idx + 1}"
    } if idx < length(local.public_subnet_cidrs)
  } : {}

  // create one private NAT per nonroutable subnet (no public EIP)

  private_nat_configs = local.enable_gateways && var.enable_private_nat_gateways ? {
    for idx, cidr in local.nonroutable_subnet_cidrs : cidr => {
      subnet_id         = module.nonroutable_subnets[cidr].id
      availability_mode = "zonal"
      # user-friendly display name for the nonroutable route table -> NAT mapping
      display_name = "${var.vpc_name}-nonroutable-${idx + 1}"
      # nat resource name: include an index and the CIDR for easy lookup
      nat_name = "nat-private-${idx + 1}-${cidr}"
    }
  } : {}
}

# Explicit aws_route resources for each route table
# Public route table: default route to IGW
resource "aws_route" "public_default" {
  for_each               = (local.enable_public_route_table && local.enable_gateways && var.enable_internet_gateway) ? { "default" = true } : {}
  route_table_id         = module.public_route_table[0].route_table_id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = module.gateways.igw_id
}

# Private route tables: default route to public NAT gateway
resource "aws_route" "private_default" {
  # Use plan-known keys (CIDR lists) so imports/plans don't depend on apply-time module outputs.
  for_each = (local.enable_private_route_tables && local.enable_public_nat_gateways_effective) ? {
    for idx, cidr in local.private_subnet_cidrs : cidr => true
    if idx < length(local.public_subnet_cidrs)
  } : {}
  route_table_id         = module.private_route_tables[each.key].route_table_id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = module.gateways.public_nat_ids[each.key]
}

# Optional: additional route for private route tables (enabled per-VPC via tfvars)
resource "aws_route" "private_onprem" {
  for_each               = (var.enable_private_onprem_route && local.enable_private_route_tables) ? { for cidr in local.private_subnet_cidrs : cidr => true } : {}
  route_table_id         = module.private_route_tables[each.key].route_table_id
  destination_cidr_block = var.private_onprem_destination_cidr
  nat_gateway_id         = try(module.gateways.public_nat_ids[each.key], null)
}

# Nonroutable route tables: default route to private NAT gateway

resource "aws_route" "nonroutable_default" {
  for_each               = (local.enable_nonroutable_route_tables && local.enable_gateways && var.enable_private_nat_gateways) ? { for cidr in local.nonroutable_subnet_cidrs : cidr => true } : {}
  route_table_id         = module.nonroutable_route_tables[each.key].route_table_id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = module.gateways.private_nat_ids[each.key]
}

locals {
  allowed_route_target_types = toset([
    "gateway_id",
    "nat_gateway_id",
    "transit_gateway_id",
    "vpc_peering_connection_id",
    "vpc_endpoint_id",
    "network_interface_id",
  ])

  public_extra_routes = {
    for idx, r in var.public_extra_routes : "${r.destination_cidr_block}-${r.target_type}-${r.target_id}-${idx}" => r
    if contains(local.allowed_route_target_types, r.target_type)
  }

  private_extra_routes = {
    for idx, r in var.private_extra_routes : "${r.destination_cidr_block}-${r.target_type}-${r.target_id}-${idx}" => r
    if contains(local.allowed_route_target_types, r.target_type)
  }

  nonroutable_extra_routes = {
    for idx, r in var.nonroutable_extra_routes : "${r.destination_cidr_block}-${r.target_type}-${r.target_id}-${idx}" => r
    if contains(local.allowed_route_target_types, r.target_type)
  }
}

resource "aws_route" "public_extra" {
  for_each               = local.enable_public_route_table ? local.public_extra_routes : {}
  route_table_id         = module.public_route_table[0].route_table_id
  destination_cidr_block = each.value.destination_cidr_block

  gateway_id                = each.value.target_type == "gateway_id" ? (each.value.target_id == "igw" ? module.gateways.igw_id : each.value.target_id) : null
  nat_gateway_id            = each.value.target_type == "nat_gateway_id" ? each.value.target_id : null
  transit_gateway_id        = each.value.target_type == "transit_gateway_id" ? each.value.target_id : null
  vpc_peering_connection_id = each.value.target_type == "vpc_peering_connection_id" ? each.value.target_id : null
  vpc_endpoint_id           = each.value.target_type == "vpc_endpoint_id" ? each.value.target_id : null
  network_interface_id      = each.value.target_type == "network_interface_id" ? each.value.target_id : null
}

resource "aws_route" "private_extra" {
  for_each = {
    for item in flatten([
      for rt_key, rt in module.private_route_tables : [
        for r_key, r in local.private_extra_routes : {
          key            = "${rt_key}-${r_key}"
          route_table_id = rt.route_table_id
          route          = r
        }
      ]
    ]) : item.key => item
  }
  route_table_id         = each.value.route_table_id
  destination_cidr_block = each.value.route.destination_cidr_block

  gateway_id                = each.value.route.target_type == "gateway_id" ? (each.value.route.target_id == "igw" ? module.gateways.igw_id : each.value.route.target_id) : null
  nat_gateway_id            = each.value.route.target_type == "nat_gateway_id" ? each.value.route.target_id : null
  transit_gateway_id        = each.value.route.target_type == "transit_gateway_id" ? each.value.route.target_id : null
  vpc_peering_connection_id = each.value.route.target_type == "vpc_peering_connection_id" ? each.value.route.target_id : null
  vpc_endpoint_id           = each.value.route.target_type == "vpc_endpoint_id" ? each.value.route.target_id : null
  network_interface_id      = each.value.route.target_type == "network_interface_id" ? each.value.route.target_id : null
}

resource "aws_route" "nonroutable_extra" {
  for_each = {
    for item in flatten([
      for rt_key, rt in module.nonroutable_route_tables : [
        for r_key, r in local.nonroutable_extra_routes : {
          key            = "${rt_key}-${r_key}"
          route_table_id = rt.route_table_id
          route          = r
        }
      ]
    ]) : item.key => item
  }
  route_table_id         = each.value.route_table_id
  destination_cidr_block = each.value.route.destination_cidr_block

  gateway_id                = each.value.route.target_type == "gateway_id" ? (each.value.route.target_id == "igw" ? module.gateways.igw_id : each.value.route.target_id) : null
  nat_gateway_id            = each.value.route.target_type == "nat_gateway_id" ? each.value.route.target_id : null
  transit_gateway_id        = each.value.route.target_type == "transit_gateway_id" ? each.value.route.target_id : null
  vpc_peering_connection_id = each.value.route.target_type == "vpc_peering_connection_id" ? each.value.route.target_id : null
  vpc_endpoint_id           = each.value.route.target_type == "vpc_endpoint_id" ? each.value.route.target_id : null
  network_interface_id      = each.value.route.target_type == "network_interface_id" ? each.value.route.target_id : null
}

# DHCP Options Set
module "dhcp_options" {
  source               = "./modules/dhcp-options"
  vpc_id               = module.vpc.id
  domain_name          = "ec2.internal"
  domain_name_servers  = ["AmazonProvidedDNS"]
  ntp_servers          = []
  netbios_name_servers = []
  netbios_node_type    = null
  tags = {
    Name        = "${var.vpc_name}-dhcp-options"
    Environment = var.vpc_name
  }
}

# S3 Gateway Endpoint (private and nonroutable subnets only)

module "s3_vpc_endpoint" {
  source = "./modules/vpc-endpoint"
  count = (var.enable_s3_gateway_endpoint && (
    (var.enable_private_route_tables && var.enable_private_subnets && length(var.private_subnet_cidrs) > 0) ||
    (var.enable_nonroutable_route_tables && var.enable_nonroutable_subnets && length(var.nonroutable_subnet_cidrs) > 0)
  )) ? 1 : 0
  vpc_id            = module.vpc.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = local.s3_endpoint_route_table_ids
  tags = {
    Name        = "${var.vpc_name}-s3-endpoint"
    Environment = var.vpc_name
  }
}


# Security group for VPC endpoints 
module "vpc_endpoints_sg" {
  source      = "./modules/security-group"
  count       = (var.enable_interface_endpoints && var.enable_vpc_endpoints_sg && length(var.vpc_endpoints_security_group_ids) == 0) ? 1 : 0
  name        = "${var.vpc_name}-endpoints-sg"
  description = "Security group for VPC endpoints (EC2, SSM)"
  vpc_id      = module.vpc.id
  ingress_rules = [
    {
      from_port   = 443
      to_port     = 443
      protocol    = "tcp"
      cidr_blocks = [var.vpc_cidr]
      description = "Allow HTTPS from within VPC"
    }
  ]
  egress_rules = [
    {
      from_port   = 0
      to_port     = 0
      protocol    = "-1"
      cidr_blocks = ["0.0.0.0/0"]
      description = "Allow all outbound traffic"
    }
  ]
  base_tag = var.base_tag
  tags = {
    Name        = "${var.vpc_name}-endpoints-sg"
    Environment = var.vpc_name
  }
}

module "extra_security_groups" {
  source   = "./modules/security-group"
  for_each = var.extra_security_groups

  name        = each.value.name
  description = each.value.description
  vpc_id      = module.vpc.id

  ingress_rules = try(each.value.ingress_rules, [])
  egress_rules  = try(each.value.egress_rules, [])

  base_tag = var.base_tag
  tags     = try(each.value.tags, {})
}

# EC2 Interface Endpoint (all subnets except public)

module "ec2_vpc_endpoint" {
  source = "./modules/vpc-endpoint"
  count = (var.enable_interface_endpoints && (
    (var.enable_private_subnets && length(var.private_subnet_cidrs) > 0) ||
    (var.enable_nonroutable_subnets && length(var.nonroutable_subnet_cidrs) > 0)
    ) && (
    var.enable_vpc_endpoints_sg || length(var.vpc_endpoints_security_group_ids) > 0
  )) ? 1 : 0
  vpc_id              = module.vpc.id
  service_name        = "com.amazonaws.${var.region}.ec2"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = local.interface_endpoint_subnet_ids
  security_group_ids  = local.interface_endpoint_sg_ids
  private_dns_enabled = true
  tags = {
    Name        = "${var.vpc_name}-ec2-endpoint"
    Environment = var.vpc_name
  }
}

# SSM Interface Endpoint (all subnets except public)

module "ssm_vpc_endpoint" {
  source = "./modules/vpc-endpoint"
  count = (var.enable_interface_endpoints && (
    (var.enable_private_subnets && length(var.private_subnet_cidrs) > 0) ||
    (var.enable_nonroutable_subnets && length(var.nonroutable_subnet_cidrs) > 0)
    ) && (
    var.enable_vpc_endpoints_sg || length(var.vpc_endpoints_security_group_ids) > 0
  )) ? 1 : 0
  vpc_id              = module.vpc.id
  service_name        = "com.amazonaws.${var.region}.ssm"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = local.interface_endpoint_subnet_ids
  security_group_ids  = local.interface_endpoint_sg_ids
  private_dns_enabled = true
  tags = {
    Name        = "${var.vpc_name}-ssm-endpoint"
    Environment = var.vpc_name
  }
}



