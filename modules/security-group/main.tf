locals {
  # Separate rules with CIDR blocks from those with security group references
  ingress_cidr_rules = [
    for idx, rule in var.ingress_rules : merge(rule, { index = idx })
    if lookup(rule, "cidr_blocks", null) != null && lookup(rule, "source_security_group_id", null) == null
  ]
  
  ingress_sg_rules = [
    for idx, rule in var.ingress_rules : merge(rule, { index = idx })
    if lookup(rule, "source_security_group_id", null) != null
  ]
  
  egress_cidr_rules = [
    for idx, rule in var.egress_rules : merge(rule, { index = idx })
    if lookup(rule, "cidr_blocks", null) != null && lookup(rule, "source_security_group_id", null) == null
  ]
  
  egress_sg_rules = [
    for idx, rule in var.egress_rules : merge(rule, { index = idx })
    if lookup(rule, "source_security_group_id", null) != null
  ]
}

resource "aws_security_group" "this" {
  name        = var.name
  description = var.description
  vpc_id      = var.vpc_id

  # Only use inline blocks for CIDR-based rules
  dynamic "ingress" {
    for_each = local.ingress_cidr_rules
    content {
      from_port   = ingress.value.from_port
      to_port     = ingress.value.to_port
      protocol    = ingress.value.protocol
      cidr_blocks = ingress.value.cidr_blocks
      description = lookup(ingress.value, "description", null)
    }
  }

  dynamic "egress" {
    for_each = local.egress_cidr_rules
    content {
      from_port   = egress.value.from_port
      to_port     = egress.value.to_port
      protocol    = egress.value.protocol
      cidr_blocks = egress.value.cidr_blocks
      description = lookup(egress.value, "description", null)
    }
  }

  tags = merge(var.base_tag, var.tags)
  
  # Prevent destroy when rules exist
  lifecycle {
    create_before_destroy = true
  }
}

# Separate rule resources for security group references
resource "aws_security_group_rule" "ingress_sg" {
  for_each = { for rule in local.ingress_sg_rules : rule.index => rule }

  type                     = "ingress"
  security_group_id        = aws_security_group.this.id
  from_port                = each.value.from_port
  to_port                  = each.value.to_port
  protocol                 = each.value.protocol
  source_security_group_id = each.value.source_security_group_id
  description              = lookup(each.value, "description", null)
}

resource "aws_security_group_rule" "egress_sg" {
  for_each = { for rule in local.egress_sg_rules : rule.index => rule }

  type                     = "egress"
  security_group_id        = aws_security_group.this.id
  from_port                = each.value.from_port
  to_port                  = each.value.to_port
  protocol                 = each.value.protocol
  source_security_group_id = each.value.source_security_group_id
  description              = lookup(each.value, "description", null)
}

output "security_group_id" {
  value = aws_security_group.this.id
}
