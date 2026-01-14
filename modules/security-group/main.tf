resource "aws_security_group" "this" {
  name        = var.name
  description = var.description
  vpc_id      = var.vpc_id

  dynamic "ingress" {
    for_each = var.ingress_rules
    content {
      from_port                = ingress.value.from_port
      to_port                  = ingress.value.to_port
      protocol                 = ingress.value.protocol
      cidr_blocks              = lookup(ingress.value, "cidr_blocks", null)
      source_security_group_id = lookup(ingress.value, "source_security_group_id", null)
      description              = lookup(ingress.value, "description", null)
    }
  }

  dynamic "egress" {
    for_each = var.egress_rules
    content {
      from_port                = egress.value.from_port
      to_port                  = egress.value.to_port
      protocol                 = egress.value.protocol
      cidr_blocks              = lookup(egress.value, "cidr_blocks", null)
      source_security_group_id = lookup(egress.value, "source_security_group_id", null)
      description              = lookup(egress.value, "description", null)
    }
  }

  tags = merge(var.base_tag, var.tags)
}
