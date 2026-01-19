// Internet Gateway
resource "aws_internet_gateway" "igw" {
  count  = var.create_igw ? 1 : 0
  vpc_id = var.vpc_id
  tags   = var.tags
}

// Create EIPs for public NATs when allocation_id not provided
resource "aws_eip" "nat_eip" {
  for_each = { for k, cfg in var.public_nat_configs : k => cfg if lookup(cfg, "allocation_id", null) == null && (lookup(cfg, "connectivity_type", "public") == "public") }

  domain = "vpc"
  tags   = merge(var.tags, { Name = "nat-eip-${each.key}" })
}

// Public NAT gateways (zonal NATs expected).
resource "aws_nat_gateway" "public" {
  for_each = var.public_nat_configs

  allocation_id                      = lookup(each.value, "allocation_id", null) != null ? lookup(each.value, "allocation_id") : (contains(keys(aws_eip.nat_eip), each.key) ? aws_eip.nat_eip[each.key].allocation_id : null)
  subnet_id                          = each.value.public_subnet_id
  private_ip                         = lookup(each.value, "private_ip", null)
  connectivity_type                  = lookup(each.value, "connectivity_type", "public")
  availability_mode                  = lookup(each.value, "availability_mode", null)
  secondary_allocation_ids           = lookup(each.value, "secondary_allocation_ids", null)
  secondary_private_ip_address_count = lookup(each.value, "secondary_private_ip_address_count", null)
  secondary_private_ip_addresses     = lookup(each.value, "secondary_private_ip_addresses", null)
  tags                               = merge(var.tags, { Name = lookup(each.value, "nat_name", "nat-public-${each.key}") })

  # Removed ignore_changes for regional_nat_gateway_address (provider-managed, warning suppressed)
}

// Private NAT gateways (no public EIP)
resource "aws_nat_gateway" "private" {
  for_each = var.private_nat_configs

  subnet_id                          = each.value.subnet_id
  private_ip                         = lookup(each.value, "private_ip", null)
  connectivity_type                  = "private"
  availability_mode                  = lookup(each.value, "availability_mode", null)
  secondary_private_ip_address_count = lookup(each.value, "secondary_private_ip_address_count", null)
  secondary_private_ip_addresses     = lookup(each.value, "secondary_private_ip_addresses", null)
  tags                               = merge(var.tags, { Name = lookup(each.value, "nat_name", "nat-private-${each.key}") })

  # Removed ignore_changes for regional_nat_gateway_address (provider-managed, warning suppressed)
}
